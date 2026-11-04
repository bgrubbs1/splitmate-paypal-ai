"""HTTP endpoint tests: mock-mode end-to-end and real-mode against fake PayPal."""

import json
import unittest

from splitmate.fake_services import FakeServices

from tests.helpers import AppServer, demo_text, http, mock_config, real_config


class MockServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = AppServer(mock_config()).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()

    def url(self, path):
        return self.app.base_url + path

    # -- static UI ---------------------------------------------------------
    def test_index_serves_single_page_app(self):
        status, headers, body = http("GET", self.url("/"), raw=True)
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        text = body.decode("utf-8")
        self.assertIn("SplitMate", text)
        self.assertIn("/static/app.js", text)

    def test_css_and_js_are_served(self):
        status, headers, body = http("GET", self.url("/static/styles.css"), raw=True)
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])
        status, headers, _ = http("GET", self.url("/static/app.js"), raw=True)
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])

    def test_vendored_grid_bundle_is_served_with_its_license(self):
        status, headers, body = http(
            "GET", self.url("/static/vendor/ag-grid-community-36.2.0.min.js"), raw=True
        )
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertGreater(len(body), 1000000)
        status, _, license_text = http("GET", self.url("/static/vendor/AG-GRID-LICENSE.txt"), raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"MIT License", license_text)

    def test_index_loads_the_grid_layer_before_the_app_from_this_server(self):
        _, _, body = http("GET", self.url("/"), raw=True)
        text = body.decode("utf-8")
        self.assertLess(text.index("/static/vendor/ag-grid-community"), text.index("/static/grids.js"))
        self.assertLess(text.index("/static/grids.js"), text.index("/static/app.js"))
        self.assertNotIn("<script src=\"http", text, "no script may come from a CDN")

    def test_return_and_cancel_routes_serve_app(self):
        for path in ("/return", "/cancel"):
            status, headers, body = http("GET", self.url(path), raw=True)
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["Content-Type"])

    def test_static_traversal_blocked(self):
        for path in (
            "/static/../app.py",
            "/static/%2e%2e/app.py",
            "/static/../../README.md",
            "/static/nested/../../app.py",
        ):
            status, _, _ = http("GET", self.url(path), raw=True)
            self.assertEqual(status, 404, path)

    def test_missing_static_returns_404(self):
        status, _, body = http("GET", self.url("/static/nope.js"), raw=True)
        self.assertEqual(status, 404)
        self.assertIn(b"Not found", body)

    def test_unknown_route_returns_404_json(self):
        status, _, body = http("GET", self.url("/does-not-exist"))
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    # -- health / demo -----------------------------------------------------
    def test_health_reports_mock_mode_without_secrets(self):
        status, headers, body = http("GET", self.url("/api/health"))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["paypal_mode"], "mock")
        self.assertEqual(body["ai_mode"], "mock")
        self.assertIsNone(body["paypal_base_url"])
        self.assertNotIn("secret", json.dumps(body).lower())

    def test_security_headers(self):
        _, headers, _ = http("GET", self.url("/api/health"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_demo_endpoint_returns_deterministic_plan_text(self):
        status, _, body = http("GET", self.url("/api/demo"))
        self.assertEqual(status, 200)
        self.assertIn("Cabin weekend", body["plan_text"])
        self.assertIn("Sam left Saturday morning", body["plan_text"])

    # -- parse / preview ---------------------------------------------------
    def test_parse_endpoint_returns_plan_and_split(self):
        status, _, body = http("POST", self.url("/api/parse"), {"text": demo_text()})
        self.assertEqual(status, 200)
        self.assertEqual(body["parser"], "rules")
        self.assertTrue(body["ready"])
        self.assertEqual(len(body["plan"]["participants"]), 4)
        self.assertEqual(len(body["split"]["orders"]), 3)

    def test_parse_rejects_empty_text(self):
        status, _, body = http("POST", self.url("/api/parse"), {"text": "  "})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_preview_recomputes_edited_plan(self):
        parsed = http("POST", self.url("/api/parse"), {"text": demo_text()})[2]
        plan = parsed["plan"]
        plan["expenses"][0]["amount_cents"] = 70000
        status, _, body = http("POST", self.url("/api/preview"), {"plan": plan})
        self.assertEqual(status, 200)
        self.assertEqual(body["split"]["total_cents"], parsed["split"]["total_cents"] + 8000)
        self.assertTrue(body["ready"])

    def test_invalid_plan_returns_actionable_details(self):
        status, _, body = http("POST", self.url("/api/preview"), {"plan": {"participants": ["A"], "expenses": []}})
        self.assertEqual(status, 400)
        details = body["error"]["details"]
        self.assertTrue(any("2 participants" in detail for detail in details))
        self.assertTrue(any("expense" in detail.lower() for detail in details))

    # -- full mock checkout flow ------------------------------------------
    def _create_event(self):
        parsed = http("POST", self.url("/api/parse"), {"text": demo_text()})[2]
        status, _, body = http("POST", self.url("/api/events"), {"plan": parsed["plan"]})
        self.assertEqual(status, 201)
        return body["event"]

    def test_create_event_creates_one_mock_order_per_debtor(self):
        event = self._create_event()
        self.assertEqual(len(event["orders"]), 3)
        self.assertEqual(event["paypal_mode"], "mock")
        names = {order["name"]: order for order in event["orders"]}
        self.assertEqual(set(names), {"Sam", "Priya", "Jordan"})
        self.assertEqual(names["Sam"]["amount_cents"], 17875)
        self.assertEqual(names["Sam"]["status"], "order_created")
        self.assertIn("/mock/checkout?order=MOCK-", names["Sam"]["approve_url"])
        self.assertEqual(event["totals"]["collected_cents"], 41291)
        self.assertEqual(event["totals"]["credits_cents"], 41291)

    def test_mock_checkout_page_and_approval(self):
        event = self._create_event()
        order = event["orders"][0]
        status, headers, body = http(
            "GET", self.url("/mock/checkout?order=" + order["order_id"]), raw=True
        )
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("Simulated PayPal checkout", page)
        self.assertIn(order["order_id"], page)

        status, _, approved = http(
            "POST", self.url("/api/mock/checkout/{}/approve".format(order["order_id"])), {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["share"]["status"], "captured")
        self.assertTrue(approved["share"]["capture_id"].startswith("MOCKCAP-"))

    def test_capture_approved_captures_remaining_orders(self):
        event = self._create_event()
        first = event["orders"][0]
        http("POST", self.url("/api/mock/checkout/{}/approve".format(first["order_id"])), {})
        status, _, body = http(
            "POST", self.url("/api/events/{}/capture-approved".format(event["id"])), {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["captured"]), 2)
        self.assertEqual(body["failed"], [])
        totals = body["event"]["totals"]
        self.assertEqual(totals["captured_count"], 3)
        self.assertEqual(totals["captured_cents"], 41291)

    def test_sync_and_share_capture_endpoints(self):
        event = self._create_event()
        order = event["orders"][0]
        status, _, body = http(
            "POST",
            self.url("/api/events/{}/shares/{}/sync".format(event["id"], order["name"])),
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["share"]["status"], "order_created")
        status, _, body = http(
            "POST",
            self.url("/api/events/{}/shares/{}/capture".format(event["id"], order["name"])),
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["share"]["status"], "captured")

    def test_order_lookup_by_id(self):
        event = self._create_event()
        order = event["orders"][0]
        status, _, body = http("GET", self.url("/api/orders/" + order["order_id"]))
        self.assertEqual(status, 200)
        self.assertEqual(body["event"]["id"], event["id"])
        self.assertEqual(body["share"]["name"], order["name"])

    def test_reminder_endpoint(self):
        event = self._create_event()
        status, _, body = http(
            "POST",
            self.url("/api/events/{}/reminders".format(event["id"])),
            {"name": "Sam"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "mock")
        self.assertIn("Sam", body["message"])
        self.assertIn("$178.75", body["message"])

    def test_keep_alive_stays_in_sync_after_ignored_bodies(self):
        # Regression: POST routes that ignore the body must still consume it,
        # otherwise a keep-alive connection desyncs and the next request 501s.
        import http.client

        event = self._create_event()
        address = self.app.server.server_address
        conn = http.client.HTTPConnection(address[0], address[1], timeout=10)
        try:
            conn.request(
                "POST",
                "/api/events/{}/sync".format(event["id"]),
                body=b"{}",
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            conn.request(
                "POST",
                "/api/events/{}/reminders".format(event["id"]),
                body=json.dumps({"name": "Sam"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertIn("Sam", payload["message"])
        finally:
            conn.close()

    def test_unknown_event_and_share_are_404(self):
        status, _, _ = http("GET", self.url("/api/events/evt_nope"))
        self.assertEqual(status, 404)
        event = self._create_event()
        status, _, _ = http(
            "POST", self.url("/api/events/{}/shares/Nobody/capture".format(event["id"])), {}
        )
        self.assertEqual(status, 404)

    # -- request hardening -------------------------------------------------
    def test_oversized_body_rejected(self):
        try:
            status, _, body = http(
                "POST", self.url("/api/parse"), {"text": "x" * 300000}
            )
        except ConnectionError:
            # The server answers without reading the oversized body and closes.
            # Windows can surface that as an aborted send, which is still a rejection.
            return
        self.assertEqual(status, 413)
        self.assertIn("too large", body["error"]["message"])

    def test_invalid_json_rejected(self):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            self.url("/api/parse"),
            data=b"{not json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(context.exception.code, 400)
        context.exception.close()

    def test_method_not_allowed(self):
        status, _, body = http("PUT", self.url("/api/parse"), {})
        self.assertEqual(status, 405)
        self.assertIn("error", body)

    def test_options_allowed(self):
        import urllib.request

        request = urllib.request.Request(self.url("/api/health"), method="OPTIONS")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 204)
            self.assertIn("GET", response.headers["Allow"])

    def test_mock_checkout_requires_known_order(self):
        status, _, _ = http("GET", self.url("/mock/checkout?order=MOCK-9999"), raw=True)
        self.assertEqual(status, 404)


class RealModeServerTests(unittest.TestCase):
    """Whole stack with real PayPal client and LLM client pointed at fakes."""

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
        # Wiping fake server state invalidates any OAuth token cached by the app.
        client = self.app.server.service.paypal
        client._token = None
        client._token_expires_at = 0.0

    def url(self, path):
        return self.app.base_url + path

    def _parse_and_create(self):
        parsed = http("POST", self.url("/api/parse"), {"text": demo_text()})[2]
        return parsed, http("POST", self.url("/api/events"), {"plan": parsed["plan"]})[2]["event"]

    def test_health_reports_sandbox_and_llm(self):
        status, _, body = http("GET", self.url("/api/health"))
        self.assertEqual(status, 200)
        self.assertEqual(body["paypal_mode"], "sandbox")
        self.assertEqual(body["ai_mode"], "llm")
        self.assertEqual(body["llm_model"], "fake-model")
        self.assertIn("127.0.0.1", body["paypal_base_url"])

    def test_parse_uses_llm(self):
        status, _, body = http("POST", self.url("/api/parse"), {"text": "cabin plan"})
        self.assertEqual(status, 200)
        self.assertEqual(body["parser"], "llm")
        self.assertEqual(self.fake.stats()["llm_requests"], 1)

    def test_orders_use_real_client_against_fake_paypal(self):
        _, event = self._parse_and_create()
        self.assertEqual(event["paypal_mode"], "sandbox")
        self.assertEqual(len(event["orders"]), 3)
        for order in event["orders"]:
            self.assertTrue(order["order_id"].startswith("FAKEORDER"))
            self.assertIn("sandbox.paypal.com/checkoutnow", order["approve_url"])
        stats = self.fake.stats()
        self.assertEqual(stats["orders_created"], 3)
        self.assertEqual(stats["token_requests"], 1)  # token cached across all three

    def test_capture_before_approval_returns_409(self):
        _, event = self._parse_and_create()
        order = event["orders"][0]
        status, _, body = http(
            "POST",
            self.url("/api/events/{}/shares/{}/capture".format(event["id"], order["name"])),
            {},
        )
        self.assertEqual(status, 409)
        self.assertIn("approved", body["error"]["message"].lower())

    def test_full_real_mode_capture_flow(self):
        _, event = self._parse_and_create()
        first = event["orders"][0]
        # Buyer approves on the PayPal sandbox (simulated by the fake server).
        self.fake.approve(first["order_id"])
        status, _, synced = http(
            "POST",
            self.url("/api/events/{}/shares/{}/sync".format(event["id"], first["name"])),
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(synced["share"]["status"], "approved")
        status, _, captured = http(
            "POST",
            self.url("/api/events/{}/shares/{}/capture".format(event["id"], first["name"])),
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(captured["share"]["status"], "captured")
        self.assertTrue(captured["share"]["capture_id"].startswith("FAKECAPTURE"))

        for order in event["orders"][1:]:
            self.fake.approve(order["order_id"])
        status, _, remaining = http(
            "POST", self.url("/api/events/{}/capture-approved".format(event["id"])), {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(remaining["captured"]), 2)
        self.assertEqual(remaining["event"]["totals"]["captured_cents"], 41291)
        self.assertEqual(self.fake.stats()["captures"], 3)

    def test_ai_reminder_in_real_mode(self):
        _, event = self._parse_and_create()
        status, _, body = http(
            "POST",
            self.url("/api/events/{}/reminders".format(event["id"])),
            {"name": "Sam"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "llm")
        self.assertTrue(body["message"])
        self.assertEqual(self.fake.stats()["llm_requests"], 2)

    def test_mock_checkout_unavailable_in_real_mode(self):
        _, event = self._parse_and_create()
        order = event["orders"][0]
        status, _, _ = http(
            "GET", self.url("/mock/checkout?order=" + order["order_id"]), raw=True
        )
        self.assertEqual(status, 404)

    def test_no_secrets_in_api_responses(self):
        parsed = http("POST", self.url("/api/parse"), {"text": demo_text()})[2]
        self.assertNotIn("fake-secret", json.dumps(parsed))
        self.assertNotIn("fake-key", json.dumps(parsed))


if __name__ == "__main__":
    unittest.main()
