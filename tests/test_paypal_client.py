"""PayPal client tests: OAuth, order payload shapes, capture, errors.

The real client is pointed at a local fake PayPal server that implements the
documented Orders v2 shapes, so no network or credentials are needed.
"""

import base64
import json
import unittest

from splitmate.config import Config
from splitmate.fake_services import FakeServices
from splitmate.paypal_client import (
    MockPayPalClient,
    PayPalAPIError,
    PayPalError,
    RealPayPalClient,
    amount_value,
    build_paypal_client,
    validate_order_id,
)

from tests.helpers import mock_config, real_config


class RealClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeServices().start()

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def setUp(self):
        self.fake.reset()

    def client(self, **overrides) -> RealPayPalClient:
        return RealPayPalClient(real_config(self.fake, **overrides), timeout=5)

    def create(self, client=None, amount_cents=15500):
        client = client or self.client()
        return client.create_order(
            amount_cents=amount_cents,
            currency="USD",
            description="SplitMate - Cabin weekend - Sam share",
            reference_id="evt_test:sam",
            return_url="https://app.test/return",
            cancel_url="https://app.test/cancel",
        )

    # -- auth --------------------------------------------------------------
    def test_oauth_uses_basic_auth_client_credentials(self):
        self.create()
        expected = "Basic " + base64.b64encode(b"fake-client:fake-secret").decode("ascii")
        self.assertEqual(self.fake.stats()["last_basic_auth"], expected)

    def test_access_token_is_cached_across_orders(self):
        client = self.client()
        self.create(client)
        self.create(client)
        self.create(client)
        self.assertEqual(self.fake.stats()["token_requests"], 1)

    def test_token_refreshes_after_expiry(self):
        client = self.client()
        self.create(client)
        client._token_expires_at = 0  # simulate an expired cached token
        self.create(client)
        self.assertEqual(self.fake.stats()["token_requests"], 2)

    # -- create order payload ---------------------------------------------
    def test_create_order_payload_shape(self):
        order = self.create()
        self.assertTrue(order["order_id"].startswith("FAKEORDER"))
        self.assertEqual(order["status"], "CREATED")
        stats = self.fake.stats()
        recorded = stats["orders"][0]
        self.assertEqual(recorded["amount_value"], "155.00")
        self.assertEqual(recorded["currency_code"], "USD")
        self.assertEqual(recorded["description"], "SplitMate - Cabin weekend - Sam share")
        self.assertEqual(recorded["reference_id"], "evt_test:sam")
        self.assertTrue(recorded["used_application_context"])
        self.assertFalse(recorded["used_experience_context"])
        self.assertEqual(recorded["return_url"], "https://app.test/return")
        self.assertEqual(recorded["cancel_url"], "https://app.test/cancel")

    def test_create_order_returns_sandbox_checkout_link(self):
        order = self.create()
        self.assertIsNotNone(order["approve_url"])
        self.assertIn("sandbox.paypal.com/checkoutnow?token=", order["approve_url"])
        self.assertIn(order["order_id"], order["approve_url"])

    def test_minor_unit_amounts(self):
        client = self.client()
        self.create(client, amount_cents=1)
        self.create(client, amount_cents=123456)
        amounts = [order["amount_value"] for order in self.fake.stats()["orders"]]
        self.assertEqual(amounts, ["0.01", "1234.56"])

    def test_description_truncated_to_paypal_limit(self):
        client = self.client()
        client.create_order(
            amount_cents=100,
            currency="USD",
            description="x" * 300,
            return_url="https://app.test/return",
        )
        self.assertLessEqual(len(self.fake.stats()["orders"][0]["description"]), 127)

    def test_experience_context_flag_changes_payload(self):
        client = self.client(PAYPAL_EXPERIENCE_CONTEXT="1")
        self.create(client)
        recorded = self.fake.stats()["orders"][0]
        self.assertTrue(recorded["used_experience_context"])
        self.assertFalse(recorded["used_application_context"])
        self.assertEqual(recorded["return_url"], "https://app.test/return")

    def test_build_payload_is_capture_intent(self):
        payload = self.client().build_create_order_payload(
            15500, "USD", "share", "evt_1:sam", "https://app.test/return", "https://app.test/cancel"
        )
        self.assertEqual(payload["intent"], "CAPTURE")
        self.assertEqual(payload["purchase_units"][0]["amount"]["value"], "155.00")
        self.assertEqual(payload["purchase_units"][0]["amount"]["currency_code"], "USD")
        self.assertIn("application_context", payload)

    # -- status / capture --------------------------------------------------
    def test_status_lifecycle(self):
        client = self.client()
        order = self.create(client)
        self.assertEqual(client.get_order(order["order_id"])["status"], "CREATED")
        self.fake.approve(order["order_id"])
        self.assertEqual(client.get_order(order["order_id"])["status"], "APPROVED")
        captured = client.capture_order(order["order_id"])
        self.assertEqual(captured["status"], "COMPLETED")
        self.assertEqual(captured["capture_status"], "COMPLETED")
        self.assertTrue(captured["capture_id"].startswith("FAKECAPTURE"))
        fetched = client.get_order(order["order_id"])
        self.assertEqual(fetched["status"], "COMPLETED")
        self.assertEqual(fetched["capture_id"], captured["capture_id"])

    def test_capture_before_approval_raises_helpfully(self):
        client = self.client()
        order = self.create(client)
        with self.assertRaises(PayPalAPIError) as context:
            client.capture_order(order["order_id"])
        error = context.exception
        self.assertEqual(error.http_status, 422)
        self.assertEqual(error.name, "UNPROCESSABLE_ENTITY")
        self.assertEqual(error.issue, "ORDER_NOT_APPROVED")
        self.assertTrue(error.is_not_approved())
        self.assertIn("not approved", str(error).lower())

    def test_capture_is_idempotent(self):
        client = self.client()
        order = self.create(client)
        self.fake.approve(order["order_id"])
        first = client.capture_order(order["order_id"])
        second = client.capture_order(order["order_id"])
        self.assertEqual(second["status"], "COMPLETED")
        self.assertEqual(first["capture_id"], second["capture_id"])

    def test_unknown_order_returns_404_error(self):
        client = self.client()
        with self.assertRaises(PayPalAPIError) as context:
            client.get_order("FAKEORDER99999999")
        self.assertEqual(context.exception.http_status, 404)
        self.assertEqual(context.exception.name, "RESOURCE_NOT_FOUND")

    def test_auth_failure_is_sanitised(self):
        client = self.client()
        order = self.create(client)
        self.fake.reset()
        # Issued tokens were wiped, so the next call presents an invalid token.
        with self.assertRaises(PayPalAPIError) as context:
            client.get_order(order["order_id"])
        message = str(context.exception)
        self.assertEqual(context.exception.http_status, 401)
        self.assertIn("authentication failed", message.lower())
        self.assertNotIn("fake-secret", message)

    def test_network_failure_is_sanitised(self):
        config = Config.from_env(
            env={
                "SPLITMATE_MODE": "real",
                "PAYPAL_CLIENT_ID": "id",
                "PAYPAL_CLIENT_SECRET": "secret",
                "PAYPAL_BASE_URL": "http://127.0.0.1:1",
            }
        )
        client = RealPayPalClient(config, timeout=0.3)
        with self.assertRaises(PayPalError):
            client.get_order("FAKEORDER00000001")

    # -- input validation --------------------------------------------------
    def test_order_ids_validated_before_use_in_urls(self):
        for bad in ("", "../../etc/passwd", "order/../x", "bad id!", "a" * 100, None):
            with self.assertRaises(PayPalError):
                validate_order_id(bad)
        self.assertEqual(validate_order_id("5O190127TN364715T"), "5O190127TN364715T")

    def test_amount_value_validation(self):
        self.assertEqual(amount_value(15500), "155.00")
        with self.assertRaises(PayPalError):
            amount_value(0)
        with self.assertRaises(PayPalError):
            amount_value(-5)

    def test_factory_modes(self):
        self.assertIsInstance(build_paypal_client(real_config(self.fake)), RealPayPalClient)
        self.assertIsInstance(build_paypal_client(mock_config()), MockPayPalClient)


class MockClientTests(unittest.TestCase):
    def setUp(self):
        self.client = MockPayPalClient(mock_config())

    def test_mode_is_mock(self):
        self.assertEqual(self.client.mode, "mock")

    def test_deterministic_order_ids_and_approve_urls(self):
        first = self.client.create_order(17875, "USD", "Sam share", "evt_1:sam")
        second = self.client.create_order(2208, "USD", "Priya share", "evt_1:priya")
        self.assertEqual(first["order_id"], "MOCK-0001")
        self.assertEqual(second["order_id"], "MOCK-0002")
        self.assertEqual(
            first["approve_url"], "http://127.0.0.1:8000/mock/checkout?order=MOCK-0001"
        )

    def test_status_lifecycle(self):
        order = self.client.create_order(100, "USD", "share", "ref")
        self.assertEqual(self.client.get_order(order["order_id"])["status"], "CREATED")
        self.client.mark_approved(order["order_id"])
        self.assertEqual(self.client.get_order(order["order_id"])["status"], "APPROVED")
        captured = self.client.capture_order(order["order_id"])
        self.assertEqual(captured["status"], "COMPLETED")
        self.assertTrue(captured["capture_id"].startswith("MOCKCAP-"))

    def test_capture_twice_returns_same_capture(self):
        order = self.client.create_order(100, "USD", "share", "ref")
        first = self.client.capture_order(order["order_id"])
        second = self.client.capture_order(order["order_id"])
        self.assertEqual(first["capture_id"], second["capture_id"])

    def test_unknown_order_raises(self):
        with self.assertRaises(PayPalAPIError):
            self.client.get_order("MOCK-9999")

    def test_rejects_zero_amount(self):
        with self.assertRaises(PayPalError):
            self.client.create_order(0, "USD", "share", "ref")

    def test_snapshot_is_a_copy(self):
        order = self.client.create_order(100, "USD", "share", "ref")
        snapshot = self.client.snapshot()
        self.assertIn(order["order_id"], snapshot)
        snapshot[order["order_id"]]["status"] = "HACKED"
        self.assertEqual(self.client.get_order(order["order_id"])["status"], "CREATED")


if __name__ == "__main__":
    unittest.main()
