"""Minimal OpenAI-compatible Chat Completions client (standard library only).

Works with OpenAI and any compatible endpoint (Azure OpenAI gateways, Groq,
Ollama's OpenAI shim, etc.) via ``LLM_BASE_URL``.

Secrets are never logged and never included in raised error messages.
"""

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import Config


class LLMError(RuntimeError):
    """A sanitised, user-safe LLM failure."""

    def __init__(self, message: str, status: Optional[int] = None):
        self.status = status
        super().__init__(message)


def _redact(text: str, config: Config) -> str:
    """Remove credentials from a string before it is surfaced to a user/log."""
    cleaned = str(text or "")
    for secret in (config.llm_api_key, config.paypal_client_secret, config.paypal_client_id):
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = cleaned.replace("\n", " ").strip()
    return cleaned[:300]


def chat_completion(
    config: Config,
    messages: List[Dict[str, str]],
    temperature: float = 0.1,
    response_format: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> str:
    """POST ``/chat/completions`` and return the assistant message content."""
    if config.ai_mode != "llm":
        raise LLMError("LLM mode is not configured")

    payload: Dict[str, Any] = {
        "model": config.llm_model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format

    url = config.llm_base + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + config.llm_api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or config.llm_timeout_seconds) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error_body = json.loads(exc.read().decode("utf-8", "replace"))
            error = error_body.get("error") if isinstance(error_body, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("type") or "")
        except (ValueError, OSError):
            detail = ""
        raise LLMError(
            _redact(
                "LLM request failed (HTTP {}): {}".format(exc.code, detail or "no detail"),
                config,
            ),
            status=exc.code,
        )
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise LLMError(_redact("LLM request failed: {}".format(exc), config))

    try:
        decoded = json.loads(body)
        choices = decoded.get("choices") or []
        content = choices[0]["message"].get("content")
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("LLM response did not contain choices[0].message.content")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM returned an empty response")
    return content
