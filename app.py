#!/usr/bin/env python3
"""SplitMate -- AI group-expense agent with PayPal Orders v2.

Quickstart (offline mock mode, no accounts or network needed)::

    python3 app.py
    # open http://127.0.0.1:8000

Real PayPal sandbox mode::

    cp .env.example .env        # fill in PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET
    python3 app.py              # auto-detects credentials

See README.md / RUNBOOK.md for details.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from splitmate.config import Config, ConfigError, load_env_file  # noqa: E402
from splitmate.server import create_server, serve  # noqa: E402


def _apply_dotenv(path: Path) -> None:
    """Seed os.environ from .env without overriding real environment variables."""
    values = load_env_file(str(path))
    for key, value in values.items():
        os.environ.setdefault(key, value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="SplitMate: paste a group plan, get fair splits, collect via PayPal."
    )
    parser.add_argument("--host", help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port (default 8000 or SPLITMATE_PORT)")
    parser.add_argument(
        "--mode",
        choices=("auto", "mock", "real"),
        help="Override SPLITMATE_MODE (auto uses real PayPal when credentials exist)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-request logging"
    )
    args = parser.parse_args(argv)

    _apply_dotenv(ROOT / ".env")
    if args.mode:
        os.environ["SPLITMATE_MODE"] = args.mode

    try:
        config = Config.from_env()
    except ConfigError as exc:
        print("Configuration error: {}".format(exc), file=sys.stderr)
        return 2

    overrides = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if not os.environ.get("SPLITMATE_PUBLIC_BASE_URL") and args.port:
        overrides["public_base_url"] = "http://{}:{}".format(
            overrides.get("host", config.host), args.port
        )
    if overrides:
        config = config.__class__(**{**config.__dict__, **overrides})

    try:
        if args.quiet:
            server = create_server(config, quiet=True)
            host, port = server.server_address[0], server.server_address[1]
            print("SplitMate mock/real server on http://{}:{}".format(host, port), flush=True)
            server.serve_forever()
        else:
            serve(config)
    except OSError as exc:
        print(
            "Could not start the server: {}.\nIs port {} already in use? "
            "Try: python3 app.py --port 8001".format(exc, config.port),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
