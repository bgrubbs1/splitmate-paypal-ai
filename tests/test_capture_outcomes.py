"""Capture completion, reconciliation and no-blind-retry HTTP regressions.

All provider responses come from the existing local fake; expected cents are
literal independent fixture values, not computed using application helpers.
"""
import concurrent.futures
import json
import unittest
from unittest.mock import patch

from splitmate.fake_services import FakeHandler, FakeServices, STATE
from splitmate.paypal_client import PayPalError
from tests.helpers import AppServer, http, real_config


PLAN = {
    "title": "Synthetic verification", "currency": "USD",
    "participants": ["Alice", "Bob"],
    "expenses": [{"description": "Fixture", "amount_cents": 20000,
                  "paid_by": "Alice", "shared_by": ["Alice", "Bob"]}],
}


class CaptureOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeServices().start()
        cls.app = AppServer(real_config(cls.fake)).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()
        cls.fake.stop()

    def setUp(self):
        self.fake.reset()
        self.app.server.service.paypal._token = None
        self.app.server.service.paypal._token_expires_at = 0

    def event(self, plan=PLAN):
        status, _, body = http("POST", self.app.base_url + "/api/events", {"plan": plan})
        self.assertEqual(status, 201)
        return body["event"]

    def post(self, event, action, name=None):
        path = "/api/events/{}".format(event["id"])
        if name:
            path += "/shares/" + name
        return http("POST", self.app.base_url + path + "/" + action, {})

    def outcome(self, order_id, status, capture_id="FIXTURE-CAPTURE", amount="100.00", currency="USD"):
        with STATE.lock:
            order = STATE.orders[order_id]
            order.update(status="COMPLETED", capture_id=capture_id,
                         capture_status=status, amount_value=amount, currency_code=currency)

    def test_noncompleted_captures_are_not_paid_and_never_recaptured(self):
        for provider_status, expected in (("PENDING", "capture_pending"),
                                         ("DECLINED", "capture_failed"),
                                         ("FAILED", "capture_failed"),
                                         ("FUTURE_STATUS", "capture_unknown"),
                                         (None, "capture_unknown")):
            with self.subTest(provider_status=provider_status):
                event = self.event()
                order = event["orders"][0]
                self.outcome(order["order_id"], provider_status)
                before = self.fake.stats()["capture_requests"]
                status, _, body = self.post(event, "capture", "Bob")
                self.assertEqual(status, 200)
                self.assertEqual(body["share"]["status"], expected)
                self.assertEqual(body["share"]["capture_id"], "FIXTURE-CAPTURE")
                self.assertEqual(body["share"]["capture_status"], provider_status)
                self.assertTrue(body["share"]["reconciliation_required"])
                self.assertEqual(body["event"]["totals"]["captured_cents"], 0)
                self.assertEqual(body["event"]["totals"]["captured_count"], 0)
                self.assertEqual(body["event"]["totals"]["collection_target_cents"], 10000)
                self.assertEqual(body["event"]["totals"]["collected_cents"], 10000)
                self.post(event, "capture", "Bob")
                _, _, bulk = self.post(event, "capture-approved")
                self.assertEqual(bulk["captured"], [])
                self.assertEqual(len(bulk["unresolved"]), 1)
                self.assertEqual(self.fake.stats()["capture_requests"], before + 1)
                self.assertEqual(self.post(event, "retry", "Bob")[0], 409)

    def test_completed_control_and_repeated_concurrent_capture_are_idempotent(self):
        event = self.event()
        self.fake.approve(event["orders"][0]["order_id"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.post(event, "capture", "Bob"), range(2)))
        for status, _, body in results:
            self.assertEqual(status, 200)
            self.assertEqual(body["share"]["status"], "captured")
            self.assertEqual(body["share"]["capture_status"], "COMPLETED")
            self.assertEqual(body["event"]["totals"]["captured_cents"], 10000)
        self.post(event, "capture-approved")
        self.assertEqual(self.fake.stats()["capture_requests"], 1)
        self.assertEqual(results[0][2]["share"]["capture_id"], results[1][2]["share"]["capture_id"])

    def test_bulk_only_reports_completed_names_and_amounts(self):
        plan = dict(PLAN, participants=["Alice", "Bob", "Carol", "Dan"], expenses=[{
            "description": "Fixture", "amount_cents": 40000, "paid_by": "Alice",
            "shared_by": ["Alice", "Bob", "Carol", "Dan"],
        }])
        event = self.event(plan)
        for order, state in zip(event["orders"], ("PENDING", "FAILED", "COMPLETED")):
            self.outcome(order["order_id"], state, "CAPTURE-" + order["name"])
        status, _, body = self.post(event, "capture-approved")
        self.assertEqual(status, 200)
        self.assertEqual(body["captured"], ["Dan"])
        self.assertEqual({row["name"] for row in body["unresolved"]}, {"Bob", "Carol"})
        self.assertEqual(body["event"]["totals"]["captured_cents"], 10000)
        self.assertEqual(body["event"]["totals"]["collection_target_cents"], 30000)
        self.post(event, "capture-approved")
        self.assertEqual(self.fake.stats()["capture_requests"], 3)

    def test_sync_uses_capture_not_order_status_and_reconciles_without_capture(self):
        event = self.event()
        oid = event["orders"][0]["order_id"]
        for state, expected, cents in (("PENDING", "capture_pending", 0),
                                       ("DECLINED", "capture_failed", 0),
                                       ("FAILED", "capture_failed", 0),
                                       ("FUTURE_STATUS", "capture_unknown", 0),
                                       ("COMPLETED", "captured", 10000)):
            self.outcome(oid, state)
            status, _, body = self.post(event, "sync", "Bob")
            self.assertEqual(status, 200)
            self.assertEqual(body["share"]["status"], expected)
            self.assertEqual(body["share"]["capture_status"], state)
            self.assertEqual(body["event"]["totals"]["captured_cents"], cents)
        self.assertEqual(self.fake.stats()["capture_requests"], 0)

    def test_sync_all_reconciles_pending_and_does_not_skip_completed_order(self):
        event = self.event()
        oid = event["orders"][0]["order_id"]
        for state, cents in (("PENDING", 0), ("COMPLETED", 10000), ("FAILED", 0)):
            self.outcome(oid, state)
            _, _, body = self.post(event, "sync")
            self.assertEqual(body["event"]["totals"]["captured_cents"], cents)
        self.assertEqual(self.fake.stats()["capture_requests"], 0)

    def test_completed_order_without_capture_evidence_is_unknown_and_held(self):
        event = self.event()
        self.outcome(event["orders"][0]["order_id"], "COMPLETED", capture_id=None)
        _, _, body = self.post(event, "sync", "Bob")
        self.assertEqual(body["share"]["status"], "capture_unknown")
        self.assertEqual(body["event"]["totals"]["captured_cents"], 0)
        self.post(event, "capture", "Bob")
        self.assertEqual(self.fake.stats()["capture_requests"], 0)

    def test_completed_amount_or_currency_mismatch_is_not_counted(self):
        for amount, currency in (("99.99", "USD"), ("100.00", "EUR"), ("invalid", "USD")):
            with self.subTest(amount=amount, currency=currency):
                event = self.event()
                self.outcome(event["orders"][0]["order_id"], "COMPLETED", amount=amount, currency=currency)
                _, _, body = self.post(event, "capture", "Bob")
                self.assertEqual(body["share"]["status"], "capture_unknown")
                self.assertEqual(body["event"]["totals"]["captured_cents"], 0)
                self.assertEqual(body["share"]["capture_id"], "FIXTURE-CAPTURE")

    def test_missing_sync_evidence_does_not_erase_provider_id_or_unlock_retry(self):
        event = self.event()
        oid = event["orders"][0]["order_id"]
        self.outcome(oid, "PENDING")
        self.post(event, "capture", "Bob")
        self.outcome(oid, None, capture_id=None)
        with STATE.lock:
            STATE.orders[oid]["status"] = "APPROVED"
        _, _, body = self.post(event, "sync", "Bob")
        self.assertEqual(body["share"]["capture_id"], "FIXTURE-CAPTURE")
        self.assertTrue(body["share"]["reconciliation_required"])
        self.assertEqual(body["share"]["status"], "capture_unknown")
        self.assertTrue(body["share"]["capture_history"])
        self.post(event, "capture", "Bob")
        self.assertEqual(self.fake.stats()["capture_requests"], 1)

    def test_lost_capture_response_is_held_until_read_only_reconciliation(self):
        event = self.event()
        oid = event["orders"][0]["order_id"]
        self.fake.approve(oid)
        client = self.app.server.service.paypal
        original = client.capture_order

        def lose_response(order_id):
            original(order_id)  # Provider performed capture; response lost to caller.
            raise PayPalError("Synthetic connection lost after capture")

        with patch.object(client, "capture_order", side_effect=lose_response):
            self.assertEqual(self.post(event, "capture", "Bob")[0], 502)
        _, _, held = self.post(event, "capture", "Bob")
        self.assertEqual(held["share"]["status"], "capture_unknown")
        self.assertEqual(held["event"]["totals"]["captured_cents"], 0)
        self.post(event, "capture-approved")
        self.assertEqual(self.fake.stats()["capture_requests"], 1)
        _, _, reconciled = self.post(event, "sync", "Bob")
        self.assertEqual(reconciled["share"]["status"], "captured")
        self.assertEqual(reconciled["event"]["totals"]["captured_cents"], 10000)
        self.assertEqual(self.fake.stats()["capture_requests"], 1)

    def test_known_not_approved_rejection_allows_later_explicit_capture(self):
        event = self.event()
        self.assertEqual(self.post(event, "capture", "Bob")[0], 409)
        self.fake.approve(event["orders"][0]["order_id"])
        self.post(event, "sync", "Bob")
        _, _, body = self.post(event, "capture", "Bob")
        self.assertEqual(body["share"]["status"], "captured")
        self.assertEqual(self.fake.stats()["capture_requests"], 2)

    def test_bulk_lost_response_immediately_reports_unresolved_and_never_retries(self):
        event = self.event()
        self.fake.approve(event["orders"][0]["order_id"])
        client = self.app.server.service.paypal
        original = client.capture_order

        def lose_response(order_id):
            original(order_id)
            raise PayPalError("Synthetic lost response")

        with patch.object(client, "capture_order", side_effect=lose_response):
            _, _, result = self.post(event, "capture-approved")
        self.assertEqual(result["captured"], [])
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertEqual(result["unresolved"][0]["status"], "capture_unknown")
        self.assertIn("reconcile", result["unresolved"][0]["error"])
        self.assertTrue(result["event"]["shares"][1]["capture_history"])
        self.post(event, "capture-approved")
        self.assertEqual(self.fake.stats()["capture_requests"], 1)

    def test_multiple_capture_records_are_preserved_but_not_assumed_settled(self):
        event = self.event()
        self.outcome(event["orders"][0]["order_id"], "COMPLETED")
        original = FakeHandler._representation

        def multiple(order):
            response = original(order)
            captures = response["purchase_units"][0]["payments"]["captures"]
            captures.append(dict(captures[0], id="SECOND-CAPTURE"))
            return response

        with patch.object(FakeHandler, "_representation", staticmethod(multiple)):
            _, _, result = self.post(event, "capture", "Bob")
        self.assertEqual(result["share"]["status"], "capture_unknown")
        self.assertEqual(result["event"]["totals"]["captured_cents"], 0)
        self.assertEqual([item["capture_id"] for item in result["share"]["capture_evidence"]],
                         ["FIXTURE-CAPTURE", "SECOND-CAPTURE"])

    def test_non_string_capture_id_is_not_verified_completion(self):
        event = self.event()
        self.outcome(event["orders"][0]["order_id"], "COMPLETED", capture_id=123)
        _, _, result = self.post(event, "capture", "Bob")
        self.assertEqual(result["share"]["status"], "capture_unknown")
        self.assertEqual(result["event"]["totals"]["captured_cents"], 0)

    def test_different_provider_order_id_is_preserved_but_not_counted(self):
        event = self.event()
        self.outcome(event["orders"][0]["order_id"], "COMPLETED")
        original = FakeHandler._representation

        def wrong_order(order):
            return dict(original(order), id="DIFFERENT-ORDER")

        with patch.object(FakeHandler, "_representation", staticmethod(wrong_order)):
            _, _, result = self.post(event, "capture", "Bob")
        self.assertEqual(result["share"]["status"], "capture_unknown")
        self.assertEqual(result["event"]["totals"]["captured_cents"], 0)
        self.assertEqual(result["share"]["capture_history"][-1]["provider_order_id"], "DIFFERENT-ORDER")

    def test_unresolved_capture_blocks_payment_reminder_without_llm_call(self):
        event = self.event()
        self.outcome(event["orders"][0]["order_id"], "PENDING")
        self.post(event, "sync", "Bob")
        status, _, _ = http("POST", self.app.base_url + "/api/events/{}/reminders".format(event["id"]), {"name": "Bob"})
        self.assertEqual(status, 409)
        self.assertEqual(self.fake.stats()["llm_requests"], 0)


    def test_missing_response_order_id_is_not_verified(self):
        for action in ('capture', 'sync'):
            with self.subTest(action=action):
                event = self.event()
                self.outcome(event['orders'][0]['order_id'], 'COMPLETED')
                original = FakeHandler._representation
                def missing_id(order):
                    result = original(order)
                    result.pop('id')
                    return result
                with patch.object(FakeHandler, '_representation', staticmethod(missing_id)):
                    status, _, body = self.post(event, action, 'Bob')
                self.assertEqual(status, 200)
                self.assertEqual(body['event']['totals']['captured_cents'], 0,
                                 'missing provider order id was replaced with requested id')

    def test_nonexact_provider_amount_is_not_verified(self):
        for amount in ('100.001', '100.0099', '100.00 EUR', 100.001):
            with self.subTest(amount=amount):
                event = self.event()
                self.outcome(event['orders'][0]['order_id'], 'COMPLETED', amount=amount)
                status, _, body = self.post(event, 'capture', 'Bob')
                self.assertEqual(status, 200)
                self.assertEqual(body['event']['totals']['captured_cents'], 0,
                                 'nonexact/malformed provider amount was counted')

    def test_exact_schema_amount_controls_remain_capturable(self):
        for amount in ('100', '100.0', '100.00'):
            with self.subTest(amount=amount):
                event = self.event()
                self.outcome(event['orders'][0]['order_id'], 'COMPLETED', amount=amount)
                status, _, body = self.post(event, 'capture', 'Bob')
                self.assertEqual(status, 200)
                self.assertEqual(body['event']['totals']['captured_cents'], 10000)

    def test_malformed_capture_on_approved_sync_is_held(self):
        event = self.event()
        oid = event['orders'][0]['order_id']
        self.fake.approve(oid)
        original = FakeHandler._representation
        def malformed(order):
            result = original(order)
            result['purchase_units'][0]['payments'] = {'captures': {'id': 'CAPTURE', 'status': 'PENDING'}}
            return result
        with patch.object(FakeHandler, '_representation', staticmethod(malformed)):
            status, _, body = self.post(event, 'sync', 'Bob')
        self.assertEqual(status, 200)
        self.assertTrue(body['share']['reconciliation_required'])
        self.assertEqual(body['share']['status'], 'capture_unknown')
        before = self.fake.stats()['capture_requests']
        self.post(event, 'capture', 'Bob')
        self.assertEqual(self.fake.stats()['capture_requests'], before)

    def test_malformed_additional_capture_container_is_not_discarded(self):
        event = self.event()
        self.outcome(event['orders'][0]['order_id'], 'COMPLETED')
        original = FakeHandler._representation
        def ambiguous(order):
            result = original(order)
            result['purchase_units'].append({'payments': {'captures': {'id': 'SECOND-CAPTURE', 'status': 'PENDING'}}})
            return result
        with patch.object(FakeHandler, '_representation', staticmethod(ambiguous)):
            status, _, body = self.post(event, 'capture', 'Bob')
        self.assertEqual(status, 200)
        self.assertEqual(body['event']['totals']['captured_cents'], 0,
                         'malformed additional capture container was silently discarded')


    def test_exact_leading_decimal_wire_amount_is_supported(self):
        plan = dict(PLAN, expenses=[dict(PLAN["expenses"][0], amount_cents=100)])
        event = self.event(plan)
        self.outcome(event["orders"][0]["order_id"], "COMPLETED", amount=".50")
        status, _, body = self.post(event, "capture", "Bob")
        self.assertEqual(status, 200)
        self.assertEqual(body["share"]["status"], "captured")
        self.assertEqual(body["event"]["totals"]["captured_cents"], 50)

    def test_malformed_wire_shapes_on_approved_sync_are_held_without_capture(self):
        mutations = [
            lambda r: r.update(purchase_units=None),
            lambda r: r.update(purchase_units={}),
            lambda r: r.update(purchase_units=[]),
            lambda r: r["purchase_units"].append(None),
            lambda r: r["purchase_units"][0].update(payments=None),
            lambda r: r["purchase_units"][0].update(payments=[]),
            lambda r: r["purchase_units"][0].update(payments={"captures": None}),
            lambda r: r["purchase_units"][0].update(payments={"captures": "invalid"}),
            lambda r: r["purchase_units"][0].update(payments={"captures": [None]}),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(shape=index):
                event = self.event()
                self.fake.approve(event["orders"][0]["order_id"])
                original = FakeHandler._representation

                def malformed(order):
                    response = original(order)
                    mutate(response)
                    return response

                with patch.object(FakeHandler, "_representation", staticmethod(malformed)):
                    status, _, body = self.post(event, "sync", "Bob")
                self.assertEqual(status, 200)
                self.assertEqual(body["share"]["status"], "capture_unknown")
                self.assertTrue(body["share"]["reconciliation_required"])
                self.assertTrue(body["share"]["capture_validation_errors"])
                before = self.fake.stats()["capture_requests"]
                self.post(event, "capture", "Bob")
                self.post(event, "capture-approved")
                self.assertEqual(self.fake.stats()["capture_requests"], before)

    def test_missing_identity_on_approved_sync_cannot_unlock_capture(self):
        event = self.event()
        self.fake.approve(event["orders"][0]["order_id"])
        original = FakeHandler._representation

        def missing(order):
            response = original(order)
            response.pop("id")
            return response

        with patch.object(FakeHandler, "_representation", staticmethod(missing)):
            _, _, body = self.post(event, "sync", "Bob")
        self.assertEqual(body["share"]["status"], "capture_unknown")
        self.assertIsNone(body["share"]["capture_history"][-1]["provider_order_id"])
        self.post(event, "capture", "Bob")
        self.assertEqual(self.fake.stats()["capture_requests"], 0)

    def test_wire_status_is_not_case_normalized_to_completed(self):
        event = self.event()
        self.outcome(event["orders"][0]["order_id"], "completed")
        _, _, body = self.post(event, "capture", "Bob")
        self.assertEqual(body["share"]["status"], "capture_unknown")
        self.assertEqual(body["event"]["totals"]["captured_cents"], 0)


    def test_nonfinite_provider_money_remains_strict_json_and_reconcilable(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value):
                event = self.event()
                oid = event['orders'][0]['order_id']
                self.outcome(oid, 'COMPLETED', amount=value)
                base = self.app.base_url + '/api/events/' + event['id']
                status, _, raw = http('POST', base + '/shares/Bob/capture', {}, raw=True)
                self.assertEqual(status, 200)
                def reject(value):
                    raise AssertionError('Browser-invalid JSON constant: ' + value)
                responses = [('capture', raw)]
                body = json.loads(raw)
                self.assertEqual(body['share']['status'], 'capture_unknown')
                self.assertEqual(body['event']['totals']['captured_cents'], 0)
                _, _, raw = http('GET', base, raw=True)
                responses.append(('get-held', raw))
                self.outcome(oid, 'COMPLETED')
                _, _, raw = http('POST', base + '/shares/Bob/sync', {}, raw=True)
                responses.append(('sync-completed', raw))
                self.assertEqual(json.loads(raw)['share']['status'], 'captured')
                for stage, raw in responses:
                    with self.subTest(stage=stage):
                        json.loads(raw, parse_constant=reject)

    def test_nonfinite_identity_and_status_remain_browser_readable(self):
        mutations = [
            ('order-id', lambda r: r.update(id=float('nan'))),
            ('capture-id', lambda r: r['purchase_units'][0]['payments']['captures'][0].update(id=float('nan'))),
            ('capture-status', lambda r: r['purchase_units'][0]['payments']['captures'][0].update(status=float('nan'))),
            ('nested-order-id', lambda r: r.update(id={'bad':float('inf')})),
        ]
        for label, mutate in mutations:
            for action in ('capture','sync'):
                with self.subTest(label=label,action=action):
                    event = self.event()
                    self.outcome(event['orders'][0]['order_id'],'COMPLETED')
                    original=FakeHandler._representation
                    def malformed(order):
                        result=original(order); mutate(result); return result
                    with patch.object(FakeHandler,'_representation',staticmethod(malformed)):
                        status,_,raw=http('POST',self.app.base_url+'/api/events/'+event['id']+'/shares/Bob/'+action,{},raw=True)
                    self.assertEqual(status,200)
                    def reject(value): raise AssertionError('Invalid JSON constant: '+value)
                    body=json.loads(raw,parse_constant=reject)
                    self.assertEqual(body['share']['status'],'capture_unknown')
                    self.assertTrue(body['share']['reconciliation_required'])
                    before=self.fake.stats()['capture_requests']
                    self.post(event,'capture','Bob')
                    self.assertEqual(self.fake.stats()['capture_requests'],before)

    def test_invalid_scalar_evidence_is_json_safe_and_does_not_store_nested_details(self):
        # A provider's object in a scalar field must not expose arbitrary payload
        # content or persist nonfinite floats in current/history API responses.
        nested = {"unexpected_private_data": "SYNTHETIC-DO-NOT-RETAIN",
                  "values": [float("nan"), {"overflow": float("inf")} ]}
        fields = ("order-id", "order-status", "capture-id", "capture-status", "amount", "currency")
        for field in fields:
            for value in (float("nan"), float("inf"), float("-inf"), nested, [nested]):
                for action in ("capture", "sync"):
                    with self.subTest(field=field, value_type=type(value).__name__, action=action):
                        event = self.event()
                        oid = event["orders"][0]["order_id"]
                        self.outcome(oid, "COMPLETED")
                        original = FakeHandler._representation

                        def malformed(order):
                            response = original(order)
                            capture = response["purchase_units"][0]["payments"]["captures"][0]
                            if field == "order-id":
                                response["id"] = value
                            elif field == "order-status":
                                response["status"] = value
                            elif field == "capture-id":
                                capture["id"] = value
                            elif field == "capture-status":
                                capture["status"] = value
                            else:
                                capture["amount"]["value" if field == "amount" else "currency_code"] = value
                            return response

                        with patch.object(FakeHandler, "_representation", staticmethod(malformed)):
                            status, _, body = self.post(event, action, "Bob")
                        self.assertEqual(status, 200)
                        self.assertEqual(body["share"]["status"], "capture_unknown")
                        self.assertEqual(body["event"]["totals"]["captured_cents"], 0)
                        self.assertTrue(body["share"]["reconciliation_required"])
                        before = self.fake.stats()["capture_requests"]
                        self.post(event, "capture", "Bob")
                        self.post(event, "capture-approved")
                        self.assertEqual(self.fake.stats()["capture_requests"], before)
                        # Strict serialization of the full response includes history.
                        held_json = json.dumps(body, allow_nan=False)
                        self.assertNotIn("SYNTHETIC-DO-NOT-RETAIN", held_json)
                        self.assertTrue(body["share"]["capture_history"])
                        _, _, completed = self.post(event, "sync", "Bob")
                        self.assertEqual(completed["share"]["status"], "captured")
                        self.assertEqual(completed["event"]["totals"]["captured_cents"], 10000)
                        complete_json = json.dumps(completed, allow_nan=False)
                        self.assertNotIn("SYNTHETIC-DO-NOT-RETAIN", complete_json)


if __name__ == "__main__":
    unittest.main()
