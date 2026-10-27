"""Shared helpers for the SplitMate unittest suite (standard library only)."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from splitmate.config import Config
from splitmate.server import create_server
from splitmate.service import SplitMateService

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


def demo_text() -> str:
    return (SAMPLES / "demo-plan.txt").read_text(encoding="utf-8")


def mock_config(**overrides) -> Config:
    env = {
        "SPLITMATE_MODE": "mock",
        "SPLITMATE_PORT": "8000",
        "SPLITMATE_PUBLIC_BASE_URL": "http://127.0.0.1:8000",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return Config.from_env(env=env)


def real_config(fake, **overrides) -> Config:
    """Config that points both integrations at a local fake server."""
    env = {
        "SPLITMATE_MODE": "real",
        "PAYPAL_BASE_URL": fake.paypal_base_url,
        "PAYPAL_CLIENT_ID": "fake-client",
        "PAYPAL_CLIENT_SECRET": "fake-secret",
        "LLM_API_KEY": "fake-key",
        "LLM_BASE_URL": fake.llm_base_url,
        "LLM_MODEL": "fake-model",
        "SPLITMATE_PUBLIC_BASE_URL": "http://127.0.0.1:8000",
    }
    env.update({key: str(value) for key, value in overrides.items()})
    return Config.from_env(env=env)


class AppServer:
    """Run the real HTTP server on an ephemeral port inside a test."""

    def __init__(self, config: Config, service=None):
        self.server = create_server(config, port=0, quiet=True, service=service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:{}".format(self.server.server_address[1])

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()


def http(method, url, body=None, raw=False, timeout=10):
    """Tiny HTTP client used by the server tests. Returns (status, headers, data)."""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            return (
                response.status,
                dict(response.headers),
                payload if raw else json.loads(payload),
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return (
            exc.code,
            dict(exc.headers),
            payload if raw else json.loads(payload),
        )
