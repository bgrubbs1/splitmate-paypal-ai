"""AI-drafted payment reminders (with a deterministic offline fallback).

This is the second, smaller place AI is used in SplitMate: the same
OpenAI-compatible model drafts a short, polite nudge for one participant.
The mock mode template keeps the demo fully offline.
"""

from typing import Any, Dict

from .config import Config
from .llm import LLMError, chat_completion
from .plan import money

REMINDER_SYSTEM_PROMPT = """You are SplitMate, a friendly group-expense assistant.
Write a short payment reminder (2-3 sentences, under 60 words) to one person
about their share of a group expense. Be warm, specific, never passive-aggressive.
Mention the amount and the event, and include the PayPal link if one is provided.
Return only the message text, no subject line, no markdown."""


def draft_reminder(plan: Dict[str, Any], share: Dict[str, Any], config: Config) -> Dict[str, Any]:
    """Return ``{"message": str, "mode": "llm"|"mock", "warning": str|None}``."""
    status = share.get("status")
    amount_text = money(share.get("amount_cents") or 0, plan.get("currency", "USD"))
    event_title = plan.get("title") or "our group expense"
    name = share.get("name") or "friend"
    link = share.get("approve_url") or ""

    if config.ai_mode == "llm":
        user_prompt = (
            "Event: {event}\nPerson: {name}\nStatus: {status}\nAmount: {amount}\n"
            "PayPal link: {link}\nWrite the reminder."
        ).format(
            event=event_title,
            name=name,
            status=status,
            amount=amount_text,
            link=link or "(none — ask the organiser for a link)",
        )
        try:
            text = chat_completion(
                config,
                messages=[
                    {"role": "system", "content": REMINDER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
            )
            cleaned = " ".join(text.strip().strip('"').split())
            if cleaned:
                return {"message": cleaned[:600], "mode": "llm", "warning": None}
        except LLMError as exc:
            warning = "AI reminder drafting was unavailable ({}); used the built-in template.".format(exc)
            return {
                "message": _mock_reminder(plan, share, amount_text, name, link),
                "mode": "mock",
                "warning": warning,
            }
    return {
        "message": _mock_reminder(plan, share, amount_text, name, link),
        "mode": "mock",
        "warning": None,
    }


def _mock_reminder(
    plan: Dict[str, Any],
    share: Dict[str, Any],
    amount_text: str,
    name: str,
    link: str,
) -> str:
    event_title = plan.get("title") or "our group expense"
    status = share.get("status") or "order_created"
    if status == "captured":
        return (
            "Thanks {name}! Your {amount} share for {event} is paid — you're all "
            "settled up. Have a great week!"
        ).format(name=name, amount=amount_text, event=event_title)
    if status == "credit":
        return (
            "Hi {name} — you fronted more than your share of {event}. You're owed "
            "{amount}, and SplitMate's dashboard tracks the credit for you."
        ).format(name=name, event=event_title, amount=amount_text)
    link_line = (
        " Pay securely with PayPal here: {}".format(link)
        if link
        else " Ask the organiser to resend your PayPal checkout link."
    )
    return (
        "Hi {name}! Friendly nudge from SplitMate: your {amount} share of {event} "
        "is still open.{link}"
    ).format(name=name, amount=amount_text, event=event_title, link=link_line)
