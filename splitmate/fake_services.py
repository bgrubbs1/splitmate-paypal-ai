"""Local fake PayPal + LLM servers for tests and the one-command smoke test.

This is test tooling, never used by the app itself.  It implements the subset
of the PayPal Orders v2 REST API that SplitMate calls, with the same request /
response shapes documented at developer.paypal.com:

* ``POST /v1/oauth2/token`` -- Basic auth, ``grant_type=client_credentials``
* ``POST /v2/checkout/orders`` -- ``intent: CAPTURE``, ``purchase_units``,
  ``application_context`` / ``payment_source.paypal.experience_context``
* ``GET  /v2/checkout/orders/{id}``
* ``POST /v2/checkout/orders/{id}/capture`` -- 422 ORDER_NOT_APPROVED until the
  buyer approves, 201 + captures once approved, 200 on duplicate capture.

It also fakes an OpenAI-compatible ``POST /v1/chat/completions`` endpoint that
returns a valid SplitMate plan as JSON, plus a few failure modes.

Run standalone for the smoke test::

    python3 -m splitmate.fake_services --port 8899

Test-control endpoints (not part of PayPal):

* ``GET  /__test/stats``        counters + recorded orders/requests
* ``POST /__test/approve``      ``{"order_id": "..."}`` -> simulate buyer approval
* ``POST /__test/reset``        clear all state
* ``GET  /healthz``             liveness for scripts
"""

import argparse
import base64
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

FAKE_LLM_PLAN: Dict[str, Any] = {
    "title": "Cabin weekend",
    "currency": "USD",
    "participants": ["Alex", "Sam", "Priya", "Jordan"],
    "expenses": [
        {
            "description": "Cabin",
            "amount": 620.00,
            "paid_by": "Alex",
            "shared_by": ["Alex", "Sam", "Priya", "Jordan"],
        },
        {
            "description": "Groceries",
            "amount": 180.00,
            "paid_by": "Priya",
            "shared_by": ["Alex", "Priya", "Jordan"],
        },
        {
            "description": "Gas",
            "amount": 95.00,
            "paid_by": "Priya",
            "shared_by": ["Alex", "Sam", "Priya", "Jordan"],
        },
        {
            "description": "Firewood",
            "amount": 40.00,
            "paid_by": "Jordan",
            "shared_by": ["Alex", "Priya", "Jordan"],
        },
        {
            "description": "Wine",
            "amount": 90.00,
            "paid_by": "Alex",
            "shared_by": ["Alex", "Priya"],
        },
    ],
    "notes": [
        "Sam left Saturday morning, so he only shares the cabin and gas.",
        "Jordan skipped the wine.",
    ],
}

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
CAPTURED_STATUSES = ("COMPLETED",)


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.token_requests = 0
        self.orders_created = 0
        self.capture_requests = 0
        self.captures = 0
        self.llm_requests = 0
        self.last_basic_auth = ""
        self.tokens: set = set()
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.order_seq = 0
        self.capture_seq = 0
        self.fail_token = False
        self.fail_orders = False

    def reset(self) -> None:
        with self.lock:
            self.__init__()  # type: ignore[misc]

    def next_order_id(self) -> str:
        self.order_seq += 1
        return "FAKEORDER{:08d}".format(self.order_seq)

    def next_capture_id(self) -> str:
        self.capture_seq += 1
        return "FAKECAPTURE{:07d}".format(self.capture_seq)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "token_requests": self.token_requests,
                "orders_created": self.orders_created,
                "capture_requests": self.capture_requests,
                "captures": self.captures,
                "llm_requests": self.llm_requests,
                "last_basic_auth": self.last_basic_auth,
                "tokens_issued": len(self.tokens),
                "orders": [dict(order) for order in self.orders.values()],
            }


STATE = FakeState()
_VERBOSE = False


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _paypal_error(
    handler: BaseHTTPRequestHandler,
    status: int,
    name: str,
    message: str,
    details: Optional[List[Dict[str, str]]] = None,
) -> None:
    _json_response(
        handler,
        status,
        {
            "name": name,
            "message": message,
            "debug_id": "fake-debug-{}".format(int(time.time() * 1000) % 100000),
            "details": details or [],
            "links": [],
        },
    )


def _bearer_ok(handler: BaseHTTPRequestHandler) -> bool:
    header = handler.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return False
    return header[len("Bearer "):].strip() in STATE.tokens


def _read_body(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b""
    if not raw.strip():
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


class FakeHandler(BaseHTTPRequestHandler):
    server_version = "SplitMateFake/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        if _VERBOSE:
            super().log_message(fmt, *args)

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/healthz":
            _json_response(self, 200, {"ok": True})
            return
        if path == "/__test/stats":
            _json_response(self, 200, STATE.snapshot())
            return
        match = re.match(r"^/v2/checkout/orders/([^/]+)$", path)
        if match:
            self._get_order(match.group(1))
            return
        _json_response(self, 404, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/v1/oauth2/token":
            self._token()
            return
        if path == "/v2/checkout/orders":
            self._create_order()
            return
        if path == "/v1/chat/completions":
            self._chat_completion()
            return
        if path == "/__test/approve":
            body = _read_body(self)
            order_id = (body or {}).get("order_id") if isinstance(body, dict) else None
            with STATE.lock:
                order = STATE.orders.get(str(order_id))
                if order is None:
                    _json_response(self, 404, {"ok": False, "error": "unknown order"})
                    return
                if order["status"] == "CREATED":
                    order["status"] = "APPROVED"
                _json_response(self, 200, {"ok": True, "status": order["status"]})
            return
        if path == "/__test/reset":
            STATE.reset()
            _json_response(self, 200, {"ok": True})
            return
        match = re.match(r"^/v2/checkout/orders/([^/]+)/capture$", path)
        if match:
            self._capture_order(match.group(1))
            return
        _json_response(self, 404, {"error": "not found", "path": path})

    # -- PayPal: auth ------------------------------------------------------
    def _token(self) -> None:
        with STATE.lock:
            STATE.token_requests += 1
            header = self.headers.get("Authorization") or ""
            STATE.last_basic_auth = header
            if STATE.fail_token:
                _json_response(
                    self,
                    500,
                    {"name": "INTERNAL_SERVER_ERROR", "message": "fake token failure"},
                )
                return
            if not header.startswith("Basic "):
                _paypal_error(
                    self,
                    401,
                    "AUTHENTICATION_FAILURE",
                    "Authentication failed due to invalid authentication credentials "
                    "or a missing Authorization header.",
                )
                return
            try:
                decoded = base64.b64decode(header[len("Basic "):]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                decoded = ""
            if ":" not in decoded or not decoded.split(":", 1)[0] or not decoded.split(":", 1)[1]:
                _paypal_error(
                    self, 401, "AUTHENTICATION_FAILURE", "invalid client credentials"
                )
                return
            content_type = self.headers.get("Content-Type") or ""
            length = int(self.headers.get("Content-Length") or 0)
            raw_form = self.rfile.read(length) if length else b""
            form = urllib.parse.parse_qs(raw_form.decode("utf-8", "replace"))
            if (
                "application/x-www-form-urlencoded" not in content_type
                or form.get("grant_type", [""])[0] != "client_credentials"
            ):
                _paypal_error(
                    self,
                    400,
                    "INVALID_REQUEST",
                    "Expected grant_type=client_credentials",
                )
                return
            token = "fake-access-token-{}".format(STATE.token_requests)
            STATE.tokens.add(token)
            _json_response(
                self,
                200,
                {
                    "scope": "https://uri.paypal.com/services/payments/payment",
                    "access_token": token,
                    "token_type": "Bearer",
                    "app_id": "APP-80W284485P519543T",
                    "expires_in": 31668,
                    "nonce": "fake-nonce",
                },
            )

    # -- PayPal: create order ---------------------------------------------
    def _create_order(self) -> None:
        with STATE.lock:
            if not _bearer_ok(self):
                _paypal_error(
                    self, 401, "AUTHENTICATION_FAILURE", "invalid or missing access token"
                )
                return
            if STATE.fail_orders:
                _paypal_error(self, 500, "INTERNAL_SERVER_ERROR", "fake order failure")
                return
            body = _read_body(self)
            if not isinstance(body, dict):
                _paypal_error(self, 400, "INVALID_REQUEST", "body must be JSON")
                return
            if body.get("intent") != "CAPTURE":
                _paypal_error(
                    self,
                    400,
                    "INVALID_REQUEST",
                    "intent must be CAPTURE",
                    [{"issue": "INVALID_PARAMETER_VALUE", "description": "intent"}],
                )
                return
            units = body.get("purchase_units")
            if not isinstance(units, list) or not units:
                _paypal_error(
                    self, 400, "INVALID_REQUEST", "purchase_units is required"
                )
                return
            unit = units[0]
            amount = unit.get("amount") if isinstance(unit, dict) else None
            if not isinstance(amount, dict):
                _paypal_error(self, 400, "INVALID_REQUEST", "amount is required")
                return
            value = amount.get("value")
            currency = amount.get("currency_code")
            if not isinstance(value, str) or not re.match(r"^[0-9]+\.[0-9]{2}$", value):
                _paypal_error(
                    self,
                    400,
                    "INVALID_REQUEST",
                    "amount.value must be a decimal string",
                    [{"issue": "DECIMAL_PRECISION", "description": "value"}],
                )
                return
            if not isinstance(currency, str) or len(currency) != 3:
                _paypal_error(self, 400, "INVALID_REQUEST", "currency_code is required")
                return
            order_id = STATE.next_order_id()
            approve_url = "https://www.sandbox.paypal.com/checkoutnow?token=" + order_id
            app_context = body.get("application_context") if isinstance(body.get("application_context"), dict) else {}
            experience = {}
            payment_source = body.get("payment_source")
            if isinstance(payment_source, dict):
                paypal_source = payment_source.get("paypal")
                if isinstance(paypal_source, dict) and isinstance(paypal_source.get("experience_context"), dict):
                    experience = paypal_source["experience_context"]
            order = {
                "id": order_id,
                "status": "CREATED",
                "amount_value": value,
                "currency_code": currency,
                "description": unit.get("description"),
                "reference_id": unit.get("reference_id"),
                "return_url": app_context.get("return_url") or experience.get("return_url"),
                "cancel_url": app_context.get("cancel_url") or experience.get("cancel_url"),
                "used_application_context": bool(app_context),
                "used_experience_context": bool(experience),
                "capture_id": None,
                "capture_status": None,
            }
            STATE.orders[order_id] = order
            STATE.orders_created += 1
            links = [
                {
                    "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/" + order_id,
                    "rel": "self",
                    "method": "GET",
                },
                {"href": approve_url, "rel": "approve", "method": "GET"},
                {
                    "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/" + order_id + "/capture",
                    "rel": "capture",
                    "method": "POST",
                },
            ]
            _json_response(
                self,
                201,
                {"id": order_id, "status": "CREATED", "links": links},
            )

    # -- PayPal: get order -------------------------------------------------
    def _get_order(self, order_id: str) -> None:
        with STATE.lock:
            if not _bearer_ok(self):
                _paypal_error(self, 401, "AUTHENTICATION_FAILURE", "invalid access token")
                return
            order = STATE.orders.get(urllib.parse.unquote(order_id))
            if order is None:
                _paypal_error(
                    self,
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                )
                return
            _json_response(self, 200, self._representation(order))

    # -- PayPal: capture order --------------------------------------------
    def _capture_order(self, order_id: str) -> None:
        with STATE.lock:
            STATE.capture_requests += 1
            if not _bearer_ok(self):
                _paypal_error(self, 401, "AUTHENTICATION_FAILURE", "invalid access token")
                return
            _read_body(self)
            order = STATE.orders.get(urllib.parse.unquote(order_id))
            if order is None:
                _paypal_error(
                    self, 404, "RESOURCE_NOT_FOUND", "The specified resource does not exist."
                )
                return
            if order["status"] in CAPTURED_STATUSES:
                _json_response(self, 200, self._representation(order))
                return
            if order["status"] != "APPROVED":
                _paypal_error(
                    self,
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The requested action could not be performed, semantically incorrect, "
                    "or failed business validation.",
                    [
                        {
                            "issue": "ORDER_NOT_APPROVED",
                            "description": "Payer has not yet approved the Order for payment. "
                            "Please redirect the payer to the 'payer-action' URI returned as "
                            "part of the HATEOAS links or provide payment source information.",
                        }
                    ],
                )
                return
            order["status"] = "COMPLETED"
            order["capture_id"] = STATE.next_capture_id()
            order["capture_status"] = "COMPLETED"
            STATE.captures += 1
            _json_response(self, 201, self._representation(order))

    @staticmethod
    def _representation(order: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": order["id"],
            "intent": "CAPTURE",
            "status": order["status"],
            "purchase_units": [
                {
                    "reference_id": order.get("reference_id") or "default",
                    "amount": {
                        "currency_code": order["currency_code"],
                        "value": order["amount_value"],
                    },
                }
            ],
            "links": [
                {
                    "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/" + order["id"],
                    "rel": "self",
                    "method": "GET",
                }
            ],
        }
        if order.get("capture_id"):
            payload["purchase_units"][0]["payments"] = {
                "captures": [
                    {
                        "id": order["capture_id"],
                        "status": order["capture_status"],
                        "amount": {
                            "currency_code": order["currency_code"],
                            "value": order["amount_value"],
                        },
                        "final_capture": True,
                    }
                ]
            }
        return payload

    # -- Fake LLM ----------------------------------------------------------
    def _chat_completion(self) -> None:
        with STATE.lock:
            STATE.llm_requests += 1
            auth = self.headers.get("Authorization") or ""
            if not auth.startswith("Bearer ") or not auth[len("Bearer "):].strip():
                _json_response(
                    self,
                    401,
                    {"error": {"message": "invalid api key", "type": "invalid_request_error"}},
                )
                return
            body = _read_body(self)
            if not isinstance(body, dict):
                _json_response(
                    self, 400, {"error": {"message": "invalid JSON", "type": "invalid_request_error"}}
                )
                return
            model = str(body.get("model") or "")
            messages = body.get("messages") or []
            text = " ".join(
                str(message.get("content") or "")
                for message in messages
                if isinstance(message, dict)
            ).lower()
            if "please fail" in text or model == "fail-model":
                _json_response(
                    self,
                    500,
                    {"error": {"message": "fake model failure", "type": "server_error"}},
                )
                return
            if "invalid plan" in text or model == "invalid-plan-model":
                content = json.dumps(
                    {"title": "Nope", "participants": ["Alex"], "expenses": []}
                )
            elif "reminder" in text or "remind" in text or model == "reminder-model":
                content = (
                    "Hi Sam! Quick reminder that your $178.75 share of Cabin weekend "
                    "is still open. Pay in a couple of clicks here: "
                    "https://www.sandbox.paypal.com/checkoutnow?token=FAKEORDER00000001"
                )
            elif "bad json" in text or model == "bad-json-model":
                content = "Sorry, I could not produce JSON for that plan."
            else:
                content = json.dumps(FAKE_LLM_PLAN)
            _json_response(
                self,
                200,
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model or "fake-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                },
            )


class FakeServices:
    """Start/stop the fake PayPal+LLM server from tests."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, verbose: bool = False):
        global _VERBOSE
        _VERBOSE = verbose
        self.server = ThreadingHTTPServer((host, port), FakeHandler)
        self.server.daemon_threads = True
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return "http://{}:{}".format(self.host, self.port)

    @property
    def paypal_base_url(self) -> str:
        return self.base_url

    @property
    def llm_base_url(self) -> str:
        return self.base_url + "/v1"

    def start(self) -> "FakeServices":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    # -- convenience helpers ----------------------------------------------
    def stats(self) -> Dict[str, Any]:
        with urllib.request.urlopen(self.base_url + "/__test/stats", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def approve(self, order_id: str) -> Dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + "/__test/approve",
            data=json.dumps({"order_id": order_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def reset(self) -> None:
        STATE.reset()

    def __enter__(self) -> "FakeServices":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fake PayPal + LLM server for SplitMate tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    global _VERBOSE
    _VERBOSE = args.verbose
    server = ThreadingHTTPServer((args.host, args.port), FakeHandler)
    server.daemon_threads = True
    host, port = server.server_address[0], server.server_address[1]
    print("fake PayPal + LLM server on http://{}:{}".format(host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
