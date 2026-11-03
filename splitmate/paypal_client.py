"""PayPal Orders v2 client -- real sandbox client + deterministic offline mock.

Both clients expose the same tiny interface:

* ``create_order(...)`` -> normalized ``{"order_id", "status", "approve_url"}``
* ``get_order(order_id)`` -> normalized ``{"order_id", "status", "capture_id"}``
* ``capture_order(order_id)`` -> normalized
  ``{"order_id", "status", "capture_id", "capture_status"}``

Real client, verified against the PayPal Orders v2 OpenAPI schema
(``checkout_orders_v2`` v2.32) and the PayPal authentication docs
(2026-10-27):

* ``POST {base}/v1/oauth2/token`` -- HTTP Basic auth, form body
  ``grant_type=client_credentials``, response ``access_token`` / ``expires_in``.
* ``POST {base}/v2/checkout/orders`` -- ``intent: CAPTURE`` + ``purchase_units``
  with ``amount: {currency_code, value: "155.00"}``; the redirect URL comes back
  in the HATEOAS ``links`` array (``rel: approve`` for the multi-step order,
  ``rel: payer-action`` when an experience context is supplied).
* ``GET {base}/v2/checkout/orders/{id}`` -- order status
  (``CREATED`` / ``PAYER_ACTION_REQUIRED`` / ``APPROVED`` / ``COMPLETED`` ...).
* ``POST {base}/v2/checkout/orders/{id}/capture`` -- empty JSON body; response
  is an order whose ``purchase_units[].payments.captures[]`` entries carry the
  capture id/status/amount.

The mock client mirrors the same states offline so the demo works with no
network at all.  The mock's "approve" URL points back at the app's own
simulated checkout page, clearly labelled as a simulation.
"""

import base64
import json
import math
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import Config, SUPPORTED_CURRENCIES
from .plan import cents_to_value

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")

APPROVE_RELS = ("payer-action", "approve")
CAPTURE_RELS = ("capture",)


def _evidence_scalar(value: Any) -> Any:
    """Retain scalar diagnostics without invalid JSON or arbitrary payloads.

    Nonfinite JSON extensions/overflow cannot be serialized for browsers. Keep
    their type and spelling in an object, not a coercion to a valid ID/amount.
    Objects/arrays in scalar fields retain only their type: their contents are
    not part of payment evidence and may contain unrelated private data.
    """
    if isinstance(value, float) and not math.isfinite(value):
        label = "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        return {"invalid_type": "nonfinite_number", "value": label}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"invalid_type": type(value).__name__}


def _wire_amount_cents(value: Any, currency: Any) -> Optional[int]:
    """Parse provider wire money exactly, never user-friendly/coercive input.

    All currencies supported by this app have two minor-unit decimal places.
    PayPal money.value is a decimal string (including '.50'), not a JSON
    number, a currency-suffixed user input, or a value to round/truncate.
    """
    if (not isinstance(value, str) or not 1 <= len(value) <= 32
            or currency not in SUPPORTED_CURRENCIES
            or re.fullmatch(r"(?:[0-9]+(?:\.[0-9]{1,2})?|\.[0-9]{1,2})", value) is None):
        return None
    whole, _, fraction = value.partition(".")
    return int(whole or "0") * 100 + int((fraction + "00")[:2])


class PayPalError(RuntimeError):
    """Base error for PayPal integration failures (safe to show a user)."""

    def __init__(self, message: str):
        self.public_message = message
        super().__init__(message)


class PayPalAPIError(PayPalError):
    """A PayPal REST API error with the response fields we care about."""

    def __init__(
        self,
        message: str,
        http_status: Optional[int] = None,
        name: Optional[str] = None,
        debug_id: Optional[str] = None,
        details: Optional[List[Dict[str, str]]] = None,
    ):
        self.http_status = http_status
        self.name = name
        self.debug_id = debug_id
        self.details = details or []
        super().__init__(message)

    @property
    def issue(self) -> str:
        for detail in self.details:
            issue = detail.get("issue")
            if issue:
                return str(issue)
        return ""

    def is_not_approved(self) -> bool:
        return self.issue in ("ORDER_NOT_APPROVED", "PAYER_ACTION_REQUIRED") or self.name == "UNPROCESSABLE_ENTITY" and "not approved" in str(self).lower()


def validate_order_id(order_id: Any) -> str:
    """Reject anything that is not a safe PayPal order id before it hits a URL path."""
    if not isinstance(order_id, str) or not ORDER_ID_RE.match(order_id):
        raise PayPalError("Invalid PayPal order id.")
    return order_id


def amount_value(amount_cents: int) -> str:
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise PayPalError("Order amount must be a positive integer number of cents.")
    return cents_to_value(amount_cents)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] if len(text) > limit else text


class BasePayPalClient:
    mode = "base"

    def create_order(
        self,
        amount_cents: int,
        currency: str,
        description: str,
        reference_id: Optional[str] = None,
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def get_order(self, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def capture_order(self, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def approve_link(order: Dict[str, Any]) -> Optional[str]:
        for relation in APPROVE_RELS:
            for link in order.get("links") or []:
                if link.get("rel") == relation and link.get("href"):
                    return str(link["href"])
        return None


# ---------------------------------------------------------------------------
# Real sandbox client
# ---------------------------------------------------------------------------


class RealPayPalClient(BasePayPalClient):
    mode = "sandbox"

    def __init__(self, config: Config, timeout: float = 20.0):
        self.config = config
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # -- auth -------------------------------------------------------------
    def _credentials(self) -> str:
        raw = "{}:{}".format(
            self.config.paypal_client_id, self.config.paypal_client_secret
        ).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def access_token(self) -> str:
        """OAuth2 client_credentials token, cached until shortly before expiry."""
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token

        request = urllib.request.Request(
            self.config.base_url + "/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": "Basic " + self._credentials(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "SplitMate/1.0 (+hackathon demo)",
            },
            method="POST",
        )
        data = self._perform(request, "authentication")
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise PayPalAPIError(
                "PayPal authentication response did not include an access token."
            )
        try:
            expires_in = int(data.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        self._token = token
        # Refresh a minute early; if PayPal returns a small/zero TTL, just use it once.
        self._token_expires_at = now + max(0, expires_in - 60)
        return token

    # -- HTTP -------------------------------------------------------------
    def _perform(self, request: urllib.request.Request, action: str) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise self._api_error(exc, action)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise PayPalError(
                "Could not reach PayPal ({}). Check your network connection and "
                "PAYPAL_BASE_URL.".format(exc)
            )
        if not body.strip():
            return {}
        try:
            decoded = json.loads(body)
        except ValueError:
            raise PayPalAPIError(
                "PayPal returned a non-JSON response for {}.".format(action)
            )
        if not isinstance(decoded, dict):
            raise PayPalAPIError("PayPal returned an unexpected response for {}.".format(action))
        return decoded

    def _api_error(self, exc: urllib.error.HTTPError, action: str) -> PayPalAPIError:
        status = exc.code
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except OSError:
            body = ""
        name = None
        message = None
        debug_id = None
        details: List[Dict[str, str]] = []
        try:
            decoded = json.loads(body) if body else {}
            if isinstance(decoded, dict):
                name = decoded.get("name")
                message = decoded.get("message")
                debug_id = decoded.get("debug_id")
                for detail in decoded.get("details") or []:
                    if isinstance(detail, dict):
                        details.append(
                            {
                                "issue": str(detail.get("issue") or ""),
                                "description": str(detail.get("description") or ""),
                            }
                        )
        except ValueError:
            pass

        hints = {
            401: "PayPal authentication failed. Check PAYPAL_CLIENT_ID and "
                 "PAYPAL_CLIENT_SECRET (sandbox credentials).",
            403: "PayPal denied the request. Make sure the sandbox app has the "
                 "Orders API enabled and the credentials are for the sandbox.",
            404: "PayPal could not find that order.",
            422: "PayPal rejected the order request.",
        }
        if action == "capture":
            issue = details[0]["issue"] if details else ""
            if issue == "ORDER_NOT_APPROVED":
                hints[422] = (
                    "The buyer has not approved this order yet. Open the PayPal "
                    "checkout link, complete the sandbox payment, then capture."
                )
            else:
                hints[422] = (
                    "PayPal could not capture the order. It may already be "
                    "captured, cancelled, or not approved yet."
                )
        public = hints.get(status) or "PayPal request failed ({} / HTTP {}).".format(action, status)
        if message and status not in (401,):  # never echo auth internals
            public = "{} {}".format(public, _truncate(message, 180)).strip()
        return PayPalAPIError(public, http_status=status, name=name, debug_id=debug_id, details=details)

    def _authorized_request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        action: str = "request",
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": "Bearer " + self.access_token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
            "User-Agent": "SplitMate/1.0 (+hackathon demo)",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.config.base_url + path, data=data, headers=headers, method=method
        )
        return self._perform(request, action)

    # -- Orders v2 ---------------------------------------------------------
    def build_create_order_payload(
        self,
        amount_cents: int,
        currency: str,
        description: str,
        reference_id: Optional[str] = None,
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Public for tests: the exact JSON body sent to PayPal."""
        purchase_unit: Dict[str, Any] = {
            "description": _truncate(description, 127),
            "amount": {
                "currency_code": (currency or "USD").upper(),
                "value": amount_value(amount_cents),
            },
        }
        if reference_id:
            if not REFERENCE_ID_RE.match(reference_id):
                raise PayPalError("Invalid order reference id.")
            purchase_unit["reference_id"] = reference_id
        payload: Dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [purchase_unit],
        }
        urls = {}
        if return_url:
            urls["return_url"] = return_url
        if cancel_url:
            urls["cancel_url"] = cancel_url
        if urls:
            if self.config.paypal_experience_context:
                # Modern placement per the current Orders v2 schema.
                # UNVERIFIED against a live sandbox: see RULES-NOTES.md.
                payload["payment_source"] = {
                    "paypal": {"experience_context": dict(urls, user_action="PAY_NOW")}
                }
            else:
                # Classic redirect flow: multi-step order, buyer redirected to
                # the rel=approve link, then back to return_url. application_context
                # is marked deprecated in the current schema (see RULES-NOTES.md).
                payload["application_context"] = dict(
                    urls, brand_name="SplitMate", user_action="PAY_NOW"
                )
        return payload

    def create_order(
        self,
        amount_cents: int,
        currency: str,
        description: str,
        reference_id: Optional[str] = None,
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self.build_create_order_payload(
            amount_cents, currency, description, reference_id, return_url, cancel_url
        )
        data = self._authorized_request(
            "POST", "/v2/checkout/orders", payload, action="create order"
        )
        order_id = data.get("id")
        if not order_id:
            raise PayPalAPIError("PayPal did not return an order id.")
        return {
            "order_id": str(order_id),
            "status": str(data.get("status") or "CREATED"),
            "approve_url": self.approve_link(data),
        }

    def get_order(self, order_id: str) -> Dict[str, Any]:
        validated = validate_order_id(order_id)
        data = self._authorized_request(
            "GET", "/v2/checkout/orders/" + validated, action="get order"
        )
        result = {
            # A requested ID is context, not provider identity evidence.
            "order_id": _evidence_scalar(data.get("id")),
            "status": _evidence_scalar(data.get("status")),
        }
        result.update(self._extract_capture(data))
        return result

    def capture_order(self, order_id: str) -> Dict[str, Any]:
        validated = validate_order_id(order_id)
        data = self._authorized_request(
            "POST",
            "/v2/checkout/orders/{}/capture".format(validated),
            {},
            action="capture",
        )
        capture = self._extract_capture(data)
        return {
            "order_id": _evidence_scalar(data.get("id")),
            "status": _evidence_scalar(data.get("status")),
            **capture,
        }

    @staticmethod
    def _extract_capture(order: Dict[str, Any]) -> Dict[str, Any]:
        # Keep payment evidence distinct from the order lifecycle. A completed
        # order does not establish that its capture completed. Preserve missing
        # IDs/statuses and multiple captures for conservative reconciliation.
        evidence: List[Dict[str, Any]] = []
        errors: List[str] = []
        if not isinstance(order.get("status"), str) or not order["status"]:
            errors.append("status is missing or invalid")

        def record(capture: Any, path: str) -> None:
            if not isinstance(capture, dict):
                errors.append(path + " must be an object")
                evidence.append({"invalid_type": type(capture).__name__})
                return
            amount = capture.get("amount")
            amount = amount if isinstance(amount, dict) else {}
            value, currency = amount.get("value"), amount.get("currency_code")
            cents = _wire_amount_cents(value, currency)
            if cents is None:
                errors.append(path + ".amount is not exact supported wire money")
            capture_id = capture.get("id")
            if not isinstance(capture_id, str) or not capture_id.strip():
                errors.append(path + ".id is missing or invalid")
            evidence.append({
                "capture_id": _evidence_scalar(capture_id),
                "capture_status": _evidence_scalar(capture.get("status")),
                "amount_cents": cents,
                "amount_value": _evidence_scalar(value),
                "currency": _evidence_scalar(currency),
            })

        units = order.get("purchase_units")
        if not isinstance(units, list) or not units:
            errors.append("purchase_units must be a nonempty array")
            units = []
        for index, unit in enumerate(units):
            path = "purchase_units[{}]".format(index)
            if not isinstance(unit, dict):
                errors.append(path + " must be an object")
                continue
            if "payments" not in unit:  # Ordinary uncaptured orders have none.
                continue
            payments = unit["payments"]
            if not isinstance(payments, dict):
                errors.append(path + ".payments must be an object")
                continue
            if "captures" not in payments:
                continue
            captures = payments["captures"]
            if not isinstance(captures, list):
                errors.append(path + ".payments.captures must be an array")
                # Preserve recognizable identifiers/statuses, but never treat
                # an invalid container as a valid capture or silently drop it.
                record(captures, path + ".payments.captures")
                continue
            for capture_index, capture in enumerate(captures):
                record(capture, path + ".payments.captures[{}]".format(capture_index))
        return dict(evidence[0] if evidence else {},
                    capture_evidence=evidence, capture_validation_errors=errors)


# ---------------------------------------------------------------------------
# Offline mock client
# ---------------------------------------------------------------------------


class MockPayPalClient(BasePayPalClient):
    """Deterministic offline stand-in for the PayPal sandbox.

    Order ids are stable per process (``MOCK-0001``...), states move through
    CREATED -> APPROVED -> COMPLETED, and the approve URL points at the app's
    own clearly-labelled simulated checkout page.
    """

    mode = "mock"

    def __init__(self, config: Config):
        self.config = config
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._counter = 0
        self._lock = None  # set by the service if needed; single-process demo

    def _next_id(self) -> str:
        self._counter += 1
        return "MOCK-{:04d}".format(self._counter)

    def create_order(
        self,
        amount_cents: int,
        currency: str,
        description: str,
        reference_id: Optional[str] = None,
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        amount_value(amount_cents)
        order_id = self._next_id()
        approve_url = "{}/mock/checkout?order={}".format(
            self.config.public_base, order_id
        )
        self._orders[order_id] = {
            "order_id": order_id,
            "status": "CREATED",
            "amount_cents": int(amount_cents),
            "currency": (currency or "USD").upper(),
            "description": _truncate(description, 127),
            "reference_id": reference_id,
            "approve_url": approve_url,
            "capture_id": None,
            "capture_status": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return {
            "order_id": order_id,
            "status": "CREATED",
            "approve_url": approve_url,
        }

    def mark_approved(self, order_id: str) -> Dict[str, Any]:
        order = self._get_raw(order_id)
        if order["status"] == "CREATED":
            order["status"] = "APPROVED"
        return dict(order)

    def get_order(self, order_id: str) -> Dict[str, Any]:
        order = self._get_raw(order_id)
        result = {
            "order_id": order["order_id"],
            "status": order["status"],
        }
        if order.get("capture_id"):
            result["capture_id"] = order["capture_id"]
            result["capture_status"] = order.get("capture_status")
            result["amount_cents"] = order["amount_cents"]
            result["currency"] = order["currency"]
        return result

    def capture_order(self, order_id: str) -> Dict[str, Any]:
        order = self._get_raw(order_id)
        if order["status"] == "COMPLETED":
            return {
                "order_id": order["order_id"],
                "status": "COMPLETED",
                "capture_id": order.get("capture_id"),
                "capture_status": order.get("capture_status"),
                "amount_cents": order["amount_cents"],
                "currency": order["currency"],
            }
        # Simulated buyer completed the (mock) checkout page.
        order["status"] = "COMPLETED"
        order["capture_id"] = "MOCKCAP-{:04d}".format(self._counter)
        order["capture_status"] = "COMPLETED"
        return {
            "order_id": order["order_id"],
            "status": "COMPLETED",
            "capture_id": order["capture_id"],
            "capture_status": "COMPLETED",
            "amount_cents": order["amount_cents"],
            "currency": order["currency"],
        }

    def _get_raw(self, order_id: str) -> Dict[str, Any]:
        validated = validate_order_id(order_id)
        order = self._orders.get(validated)
        if order is None:
            raise PayPalAPIError("Unknown mock PayPal order.", http_status=404, name="RESOURCE_NOT_FOUND")
        return order

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Read-only copy for tests/debugging."""
        return {key: dict(value) for key, value in self._orders.items()}


def build_paypal_client(config: Config) -> BasePayPalClient:
    """Factory: real sandbox client when configured, mock otherwise."""
    if config.paypal_mode == "sandbox":
        return RealPayPalClient(config)
    return MockPayPalClient(config)
