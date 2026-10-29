"""HTTP server: JSON API + single-page UI + simulated checkout page.

Standard library only (``http.server`` + ``urllib``).  The server binds to
127.0.0.1 by default, serves the static single-page app, and exposes the
small JSON API the UI talks to.

Security posture (also exercised by the test suite):

* request bodies are capped (256 KiB) and must be JSON objects;
* static file serving rejects path traversal, NUL bytes, backslashes,
  unknown extensions and oversized files;
* API responses are JSON with ``X-Content-Type-Options: nosniff``,
  ``Referrer-Policy: no-referrer`` and ``X-Frame-Options: DENY``;
* no secrets are ever logged or returned -- PayPal errors are sanitised in
  ``paypal_client`` before they reach here.
"""

import html
import json
import re
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import Config
from .paypal_client import PayPalError
from .plan import PlanValidationError
from .service import ConflictError, NotFoundError, SplitMateService

MAX_BODY_BYTES = 256 * 1024
MAX_STATIC_BYTES = 5 * 1024 * 1024
STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webp": "image/webp",
    ".txt": "text/plain; charset=utf-8",
}

EVENT_SHARE_RE = re.compile(r"^/api/events/([^/]+)/shares/([^/]+)/?(sync|capture|retry)$")
EVENT_ACTION_RE = re.compile(r"^/api/events/([^/]+)/?(sync|capture-approved|reminders)$")


class HttpError(Exception):
    def __init__(self, status: int, message: str, details: Optional[list] = None):
        self.status = status
        self.message = message
        self.details = details or []
        super().__init__(message)


class SplitMateHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, service: SplitMateService, quiet: bool = False):
        self.service = service
        self.quiet = quiet
        super().__init__(address, handler)


class SplitMateHandler(BaseHTTPRequestHandler):
    server_version = "SplitMate/1.0"
    protocol_version = "HTTP/1.1"

    # -- logging -----------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:
        if getattr(self.server, "quiet", False):
            return
        # Never log query strings: they may contain order ids.
        message = fmt % args
        message = message.split("?", 1)[0]
        sys.stderr.write("[splitmate] {}\n".format(message))

    def log_error(self, fmt: str, *args) -> None:
        if not getattr(self.server, "quiet", False):
            self.log_message(fmt, *args)

    # -- dispatch ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        try:
            self._handle_get()
        except Exception as exc:  # noqa: BLE001 - central error rendering
            self._handle_exception(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._handle_post()
        except Exception as exc:  # noqa: BLE001
            self._handle_exception(exc)

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_headers(204, "text/plain; charset=utf-8", cache=False, extra={"Allow": "GET, POST, OPTIONS"})
        self.end_headers()

    # -- GET routes --------------------------------------------------------
    def _handle_get(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html", "/return", "/cancel"):
            self._send_static("index.html", cache=False)
            return
        if path.startswith("/static/"):
            self._send_static(path[len("/static/"):], cache=True)
            return
        if path == "/favicon.ico":
            self._send_bytes(204, b"", "image/x-icon")
            return
        if path == "/api/health":
            service = self._service()
            payload = service.health()
            payload["ok"] = True
            self._send_json(200, payload)
            return
        if path == "/api/demo":
            self._send_json(200, {"plan_text": self._service().demo_text()})
            return
        if path == "/api/events":
            self._send_json(200, {"events": self._service().list_events()})
            return
        match = re.match(r"^/api/events/([^/]+)/?$", path)
        if match:
            event = self._service().get_event(self._decode(match.group(1)))
            self._send_json(200, {"event": event})
            return
        match = re.match(r"^/api/orders/([^/]+)/?$", path)
        if match:
            order_id = self._decode(match.group(1))
            self._send_json(200, self._service().find_order(order_id))
            return
        if path == "/mock/checkout":
            order_id = (query.get("order") or [""])[0]
            self._send_mock_checkout(order_id)
            return
        raise HttpError(404, "Not found.")

    # -- POST routes -------------------------------------------------------
    def _handle_post(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"

        if path == "/api/parse":
            body = self._read_json()
            text = body.get("text")
            if not isinstance(text, str):
                raise HttpError(400, "Expected {\"text\": \"...\"}.")
            try:
                result = self._service().parse(text)
            except ValueError as exc:
                raise HttpError(400, str(exc))
            self._send_json(200, result)
            return
        if path == "/api/preview":
            body = self._read_json()
            if "plan" not in body:
                raise HttpError(400, "Expected {\"plan\": {...}}.")
            try:
                result = self._service().preview(body["plan"])
            except PlanValidationError as exc:
                raise HttpError(400, "Invalid plan.", exc.errors)
            self._send_json(200, result)
            return
        if path == "/api/events":
            body = self._read_json()
            if "plan" not in body:
                raise HttpError(400, "Expected {\"plan\": {...}}.")
            try:
                event = self._service().create_event(body["plan"])
            except PlanValidationError as exc:
                raise HttpError(400, "The plan needs a fix before orders can be created.", exc.errors)
            self._send_json(201, {"event": event})
            return
        match = EVENT_SHARE_RE.match(path)
        if match:
            self._drain_body()
            event_id = self._decode(match.group(1))
            name = self._decode(match.group(2))
            action = match.group(3)
            service = self._service()
            if action == "sync":
                self._send_json(200, service.sync_share(event_id, name))
            elif action == "capture":
                self._send_json(200, service.capture_share(event_id, name))
            else:
                self._send_json(200, service.retry_share(event_id, name))
            return
        match = EVENT_ACTION_RE.match(path)
        if match:
            event_id = self._decode(match.group(1))
            action = match.group(2)
            service = self._service()
            if action == "sync":
                self._drain_body()
                self._send_json(200, service.sync_all(event_id))
            elif action == "capture-approved":
                self._drain_body()
                self._send_json(200, service.capture_approved(event_id))
            else:
                body = self._read_json()
                name = body.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise HttpError(400, "Expected {\"name\": \"participant\"}.")
                self._send_json(200, service.reminder(event_id, name.strip()))
            return
        match = re.match(r"^/api/mock/checkout/([^/]+)/approve$", path)
        if match:
            order_id = self._decode(match.group(1))
            self._read_json()
            self._send_json(200, self._service().approve_mock_order(order_id))
            return
        raise HttpError(404, "Not found.")

    # -- service accessor --------------------------------------------------
    def _service(self) -> SplitMateService:
        return self.server.service  # type: ignore[attr-defined]

    @staticmethod
    def _decode(value: str) -> str:
        return urllib.parse.unquote(value or "").strip()

    # -- request body ------------------------------------------------------
    def _read_json(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise HttpError(411, "Content-Length header is required.")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise HttpError(400, "Invalid Content-Length header.")
        if length < 0:
            raise HttpError(400, "Invalid Content-Length header.")
        if length > MAX_BODY_BYTES:
            raise HttpError(
                413, "Request body too large ({} byte limit).".format(MAX_BODY_BYTES)
            )
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400, "Request body must be valid UTF-8 JSON.")
        if not isinstance(value, dict):
            raise HttpError(400, "Request body must be a JSON object.")
        return value

    def _drain_body(self) -> None:
        """Consume a request body this route ignores so keep-alive stays in sync."""
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length else 0
        except (TypeError, ValueError):
            length = 0
        if length < 0 or length > MAX_BODY_BYTES:
            raise HttpError(400, "Invalid Content-Length header.")
        if length:
            self.rfile.read(length)

    # -- static files ------------------------------------------------------
    def _send_static(self, relative: str, cache: bool) -> None:
        relative = urllib.parse.unquote(relative or "")
        if (
            not relative
            or len(relative) > 200
            or "\x00" in relative
            or "\\" in relative
            or relative.startswith("/")
        ):
            raise HttpError(404, "Not found.")
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT)
        except ValueError:
            raise HttpError(404, "Not found.")
        if not candidate.is_file():
            raise HttpError(404, "Not found.")
        if candidate.suffix.lower() not in CONTENT_TYPES:
            raise HttpError(404, "Not found.")
        try:
            size = candidate.stat().st_size
        except OSError:
            raise HttpError(404, "Not found.")
        if size > MAX_STATIC_BYTES:
            raise HttpError(404, "Not found.")
        try:
            body = candidate.read_bytes()
        except OSError:
            raise HttpError(404, "Not found.")
        self._send_bytes(200, body, CONTENT_TYPES[candidate.suffix.lower()], cache=cache)

    # -- mock checkout page ------------------------------------------------
    def _send_mock_checkout(self, order_id: str) -> None:
        service = self._service()
        if not order_id:
            raise HttpError(404, "No order specified.")
        # Raises 404 when mock mode is off or the order is unknown.
        details = service.mock_checkout(order_id)
        body = _MOCK_CHECKOUT_HTML.format(
            title=html.escape(details["title"]),
            name=html.escape(details["name"]),
            amount=html.escape(
                "{:.2f}".format(details["amount_cents"] / 100.0)
            ),
            currency=html.escape(details["currency"]),
            order_id=html.escape(details["order_id"]),
            event_id=html.escape(details["event_id"]),
        )
        self._send_bytes(200, body.encode("utf-8"), "text/html; charset=utf-8", cache=False)

    # -- responses ---------------------------------------------------------
    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", cache=False)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        cache: bool = False,
    ) -> None:
        self._send_headers(status, content_type, cache=cache, extra={"Content-Length": str(len(body))})
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_headers(
        self,
        status: int,
        content_type: str,
        cache: bool = False,
        extra: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Cache-Control", "public, max-age=300" if cache else "no-store"
        )
        for key, value in (extra or {}).items():
            self.send_header(key, value)

    def _method_not_allowed(self) -> None:
        self._send_json(
            405,
            {"error": {"message": "Method not allowed.", "details": []}},
        )

    # -- errors ------------------------------------------------------------
    def _handle_exception(self, exc: Exception) -> None:
        try:
            if isinstance(exc, HttpError):
                if exc.status == 413:
                    # The request body was never read; the connection cannot be reused.
                    self.close_connection = True
                self._send_json(
                    exc.status,
                    {"error": {"message": exc.message, "details": exc.details}},
                )
                return
            if isinstance(exc, PlanValidationError):
                self._send_json(
                    400,
                    {
                        "error": {
                            "message": "Invalid plan.",
                            "details": exc.errors,
                        }
                    },
                )
                return
            if isinstance(exc, NotFoundError):
                self._send_json(404, {"error": {"message": str(exc) or "Not found.", "details": []}})
                return
            if isinstance(exc, ConflictError):
                self._send_json(409, {"error": {"message": str(exc), "details": []}})
                return
            if isinstance(exc, PayPalError):
                self._send_json(502, {"error": {"message": str(exc), "details": []}})
                return
            if isinstance(exc, ValueError):
                self._send_json(400, {"error": {"message": str(exc), "details": []}})
                return
            if not getattr(self.server, "quiet", False):
                traceback.print_exc()
            self._send_json(
                500,
                {"error": {"message": "Unexpected server error.", "details": []}},
            )
        except (BrokenPipeError, ConnectionResetError):
            return

    def send_error(self, code, message=None, explain=None):  # noqa: D401
        """Emit JSON for protocol-level errors too (e.g. malformed request line)."""
        try:
            body = json.dumps(
                {"error": {"message": message or "Request error.", "details": []}}
            ).encode("utf-8")
            self._send_headers(int(code), "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


_MOCK_CHECKOUT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>SplitMate | simulated checkout</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
    background: radial-gradient(900px 600px at 20% 0%, #123a37 0%, #0b1220 55%);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color:#e8eef8; padding:24px; }}
  .card {{ width:min(440px,100%); background:#111a2c; border:1px solid #24314b;
    border-radius:18px; padding:28px; box-shadow:0 24px 60px rgba(0,0,0,.45); }}
  .badge {{ display:inline-block; font-size:12px; letter-spacing:.08em; text-transform:uppercase;
    color:#0b1220; background:#00d4a0; border-radius:999px; padding:4px 10px; font-weight:700; }}
  h1 {{ font-size:20px; margin:16px 0 6px; }}
  p {{ color:#9fb0c8; line-height:1.5; margin:8px 0; font-size:14px; }}
  .amount {{ font-size:40px; font-weight:800; margin:18px 0 4px; letter-spacing:-.02em; }}
  .amount small {{ font-size:14px; color:#9fb0c8; font-weight:600; margin-left:6px; }}
  .meta {{ border-top:1px solid #24314b; border-bottom:1px solid #24314b;
    margin:18px 0; padding:14px 0; display:grid; gap:8px; font-size:14px; }}
  .meta div {{ display:flex; justify-content:space-between; gap:12px; }}
  .meta span:first-child {{ color:#7d8ea8; }}
  button {{ font:inherit; cursor:pointer; border-radius:12px; padding:14px 18px;
    font-weight:700; border:0; width:100%; }}
  .primary {{ background:#00d4a0; color:#062019; }}
  .primary:hover {{ background:#17e6b2; }}
  .secondary {{ background:transparent; color:#9fb0c8; border:1px solid #2b3a58; margin-top:10px; }}
  .note {{ font-size:12px; color:#6e809b; line-height:1.5; margin-top:18px; }}
  #result {{ min-height:20px; font-size:13px; color:#ffd479; margin-top:12px; }}
</style>
</head>
<body>
  <main class="card">
    <span class="badge">SplitMate mock mode</span>
    <h1>Simulated PayPal checkout</h1>
    <p>This screen stands in for the PayPal sandbox approval page while you run
      the demo offline. <strong>No PayPal account is contacted and no money moves.</strong>
      In real mode SplitMate redirects to the actual PayPal sandbox checkout.</p>
    <div class="amount">${amount}<small>{currency}</small></div>
    <div class="meta">
      <div><span>Paying</span><span>{name}</span></div>
      <div><span>For</span><span>{title}</span></div>
      <div><span>Order</span><span>{order_id}</span></div>
    </div>
    <button class="primary" onclick="approve()" id="approve-btn">Approve &amp; pay (simulated)</button>
    <button class="secondary" onclick="window.location.href='/?mock_cancelled=1&event={event_id}'">Cancel</button>
    <div id="result"></div>
    <p class="note">Mock mode exists so judges can run SplitMate with zero accounts,
      zero network and zero secrets. Real mode (sandbox credentials in <code>.env</code>)
      creates genuine PayPal Orders v2 orders and redirects to PayPal's sandbox site.</p>
  </main>
<script>
  async function approve() {{
    var box = document.getElementById('result');
    var button = document.getElementById('approve-btn');
    button.disabled = true;
    box.textContent = 'Talking to the mock PayPal client...';
    try {{
      var response = await fetch('/api/mock/checkout/{order_id}/approve', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: '{{}}'
      }});
      var data = await response.json();
      if (!response.ok) {{
        throw new Error((data.error && data.error.message) || 'Mock payment failed');
      }}
      box.textContent = 'Captured in mock mode. Redirecting to the dashboard...';
      window.location.href = '/?mock_paid=1&event={event_id}';
    }} catch (error) {{
      box.textContent = error.message;
      button.disabled = false;
    }}
  }}
</script>
</body>
</html>
"""


def create_server(
    config: Config,
    host: Optional[str] = None,
    port: Optional[int] = None,
    service: Optional[SplitMateService] = None,
    quiet: bool = False,
) -> SplitMateHTTPServer:
    service = service or SplitMateService(config)
    server = SplitMateHTTPServer(
        (host or config.host, config.port if port is None else port),
        SplitMateHandler,
        service,
        quiet=quiet,
    )
    return server


def serve(config: Config, quiet: bool = False) -> None:
    server = create_server(config, quiet=quiet)
    host, port = server.server_address[0], server.server_address[1]
    mode = "MOCK (fully offline demo)" if config.paypal_mode == "mock" else "PAYPAL SANDBOX"
    ai = "mock rule-based planner" if config.ai_mode == "mock" else "LLM: {}".format(config.llm_model)
    print("SplitMate is running at http://{}:{}".format(host, port))
    print("  PayPal: {}".format(mode))
    print("  AI:     {}".format(ai))
    for warning in config.warnings:
        print("  note:   {}".format(warning))
    print("  Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
