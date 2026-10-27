"""Configuration for SplitMate.

Everything is read from environment variables (optionally seeded from a local
``.env`` file).  Standard library only -- no python-dotenv dependency.

Modes
-----
``SPLITMATE_MODE=auto`` (default)
    Use the real PayPal sandbox when ``PAYPAL_CLIENT_ID`` +
    ``PAYPAL_CLIENT_SECRET`` are set, and a real LLM when ``LLM_API_KEY`` is
    set.  Anything not configured falls back to the offline mock.
``SPLITMATE_MODE=mock``
    Force every integration offline: deterministic mock PayPal client and
    deterministic rule-based planner.  No network calls at all.
``SPLITMATE_MODE=real``
    Require PayPal sandbox credentials (and use the LLM when configured).
    Raises :class:`ConfigError` with actionable messages when something is
    missing.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

DEFAULT_PAYPAL_BASE_URL = "https://api-m.sandbox.paypal.com"
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"
MODES = ("auto", "mock", "real")
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP", "CAD", "AUD")

# Values that make it obvious a user has not replaced the example file.
_PLACEHOLDERS = {
    "",
    "your-paypal-client-id",
    "your-paypal-client-secret",
    "your-client-id",
    "your-client-secret",
    "your-llm-api-key",
    "sk-your-key",
    "changeme",
}


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a usable configuration."""


def load_env_file(path: str) -> Dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` .env file.

    Supports ``#`` comments, blank lines, an optional ``export `` prefix and
    single/double quotes around values.  Never raises for a malformed file --
    unparseable lines are ignored (the app should still start in mock mode).
    """
    values: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeDecodeError):
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _clean(value: Optional[str]) -> str:
    value = (value or "").strip()
    if value.lower() in _PLACEHOLDERS:
        return ""
    return value


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: Optional[str], default: int, label: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError("{} must be an integer, got {!r}".format(label, value))


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration (no secrets are ever logged)."""

    mode: str = "auto"
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    public_base_url: str = "http://127.0.0.1:8000"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = DEFAULT_PAYPAL_BASE_URL
    currency: str = "USD"
    paypal_experience_context: bool = False
    llm_api_key: str = ""
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_seconds: float = 30.0
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    # -- derived mode helpers -------------------------------------------------
    @property
    def paypal_mode(self) -> str:
        """``"sandbox"`` when real PayPal credentials are in play, else ``"mock"``."""
        if self.mode == "mock":
            return "mock"
        if self.paypal_client_id and self.paypal_client_secret:
            return "sandbox"
        return "mock"

    @property
    def ai_mode(self) -> str:
        """``"llm"`` when an LLM key is configured, else ``"mock"``."""
        if self.mode == "mock":
            return "mock"
        return "llm" if self.llm_api_key else "mock"

    @property
    def is_real_paypal(self) -> bool:
        return self.paypal_mode == "sandbox"

    @property
    def base_url(self) -> str:
        return self.paypal_base_url.rstrip("/")

    @property
    def llm_base(self) -> str:
        return self.llm_base_url.rstrip("/")

    @property
    def public_base(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def return_url(self) -> str:
        return self.public_base + "/return"

    @property
    def cancel_url(self) -> str:
        return self.public_base + "/cancel"

    def public_description(self) -> Dict[str, object]:
        """Safe-to-expose summary used by ``/api/health`` and the UI."""
        return {
            "app": "splitmate",
            "mode": self.mode,
            "paypal_mode": self.paypal_mode,
            "paypal_base_url": self.base_url if self.is_real_paypal else None,
            "ai_mode": self.ai_mode,
            "llm_model": self.llm_model if self.ai_mode == "llm" else None,
            "currency": self.currency,
            "public_base_url": self.public_base,
            "warnings": list(self.warnings),
        }

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        env: Optional[Dict[str, str]] = None,
        dotenv_path: Optional[str] = None,
    ) -> "Config":
        """Build a config from a mapping (defaults to os.environ + .env)."""
        merged: Dict[str, str] = {}
        if env is None:
            if dotenv_path:
                merged.update(load_env_file(dotenv_path))
            merged.update(os.environ)
        else:
            merged = dict(env)

        def get(name: str, default: str = "") -> Optional[str]:
            return merged.get(name, default)

        warnings = []
        mode = (get("SPLITMATE_MODE", "auto") or "auto").strip().lower()
        if mode not in MODES:
            raise ConfigError(
                "SPLITMATE_MODE must be one of {}, got {!r}".format(", ".join(MODES), mode)
            )

        port = _as_int(get("SPLITMATE_PORT", str(DEFAULT_PORT)), DEFAULT_PORT, "SPLITMATE_PORT")
        if not (1 <= port <= 65535):
            raise ConfigError("SPLITMATE_PORT must be between 1 and 65535")

        paypal_id = _clean(get("PAYPAL_CLIENT_ID"))
        paypal_secret = _clean(get("PAYPAL_CLIENT_SECRET"))
        llm_key = _clean(get("LLM_API_KEY"))

        if mode == "real":
            missing = []
            if not paypal_id:
                missing.append("PAYPAL_CLIENT_ID")
            if not paypal_secret:
                missing.append("PAYPAL_CLIENT_SECRET")
            if missing:
                raise ConfigError(
                    "SPLITMATE_MODE=real requires {}. Add them to .env (copy "
                    ".env.example) or run in mock mode. See RUNBOOK.md.".format(
                        " and ".join(missing)
                    )
                )
        elif mode == "auto":
            if bool(paypal_id) != bool(paypal_secret):
                warnings.append(
                    "Only one PayPal credential is set; falling back to the offline mock "
                    "PayPal client. Set both PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET."
                )

        base_url = (get("PAYPAL_BASE_URL", DEFAULT_PAYPAL_BASE_URL) or "").strip() or DEFAULT_PAYPAL_BASE_URL
        if "sandbox" not in base_url and mode != "mock":
            warnings.append(
                "PAYPAL_BASE_URL does not look like the PayPal sandbox endpoint. "
                "For the hackathon use https://api-m.sandbox.paypal.com."
            )

        currency = (get("PAYPAL_CURRENCY", "USD") or "USD").strip().upper()
        if currency not in SUPPORTED_CURRENCIES:
            warnings.append(
                "PAYPAL_CURRENCY {!r} is not in {}; defaulting to USD.".format(
                    currency, ", ".join(SUPPORTED_CURRENCIES)
                )
            )
            currency = "USD"

        public_base = (
            get("SPLITMATE_PUBLIC_BASE_URL", "") or ""
        ).strip() or "http://{}:{}".format(DEFAULT_HOST, port)
        public_base = public_base.rstrip("/")

        llm_timeout_raw = get("LLM_TIMEOUT_SECONDS", "30") or "30"
        try:
            llm_timeout = float(llm_timeout_raw)
        except ValueError:
            raise ConfigError("LLM_TIMEOUT_SECONDS must be a number")

        return cls(
            mode=mode,
            host=(get("SPLITMATE_HOST", DEFAULT_HOST) or DEFAULT_HOST).strip(),
            port=port,
            public_base_url=public_base,
            paypal_client_id=paypal_id,
            paypal_client_secret=paypal_secret,
            paypal_base_url=base_url,
            currency=currency,
            paypal_experience_context=_as_bool(get("PAYPAL_EXPERIENCE_CONTEXT"), False),
            llm_api_key=llm_key,
            llm_base_url=(get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL) or "").strip()
            or DEFAULT_LLM_BASE_URL,
            llm_model=(get("LLM_MODEL", DEFAULT_LLM_MODEL) or "").strip() or DEFAULT_LLM_MODEL,
            llm_timeout_seconds=llm_timeout,
            warnings=tuple(warnings),
        )
