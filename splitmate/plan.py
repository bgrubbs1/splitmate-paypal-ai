"""Plan schema: normalisation, validation and money helpers.

A *plan* is the structured form of a natural-language event plan::

    {
      "title": "Cabin weekend",
      "currency": "USD",
      "participants": ["Alex", "Sam", "Priya", "Jordan"],
      "expenses": [
        {"description": "Cabin", "amount_cents": 62000, "paid_by": "Alex",
         "shared_by": ["Alex", "Sam", "Priya", "Jordan"], "note": ""},
        ...
      ],
      "notes": ["Sam left Saturday morning, so he only shares the cabin and gas."]
    }

Amounts are always handled as integer cents internally so the split math is
exact.  Strings are length-capped so a hostile plan cannot blow up the UI or
the PayPal request payload.
"""

import re
from typing import Any, Dict, Iterable, List, Optional

SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP", "CAD", "AUD")

MAX_PARTICIPANTS = 20
MAX_EXPENSES = 50
MAX_AMOUNT_CENTS = 10_000_000  # $100,000.00
MAX_DESCRIPTION = 60
MAX_TITLE = 80
MAX_NOTE = 240
MAX_NOTES = 10
MAX_NAME = 40

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "CAD": "CA$", "AUD": "A$"}

_MONEY_TEXT_RE = re.compile(
    r"^\s*(?:([$€£])|(USD|EUR|GBP|CAD|AUD)\s*)?\s*"
    r"(\d{1,3}(?:,\d{3})*(?:\.\d{1,4})?|\d+(?:\.\d{1,4})?)"
    r"\s*(USD|EUR|GBP|CAD|AUD|dollars?|bucks|euros?|pounds?)?\s*$",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PlanValidationError(ValueError):
    """Raised when a plan is structurally invalid.  ``errors`` is user-facing."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "invalid plan")


def _clip(text: str, limit: int) -> str:
    return text[:limit].rstrip()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = _CTRL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def parse_money_to_cents(value: Any) -> Optional[int]:
    """Parse ``"$1,234.56"`` / ``"1234.56"`` / ``1234.56`` / ``"95"`` to cents.

    Returns ``None`` when the value cannot be parsed as a positive money
    amount.  Decimal parsing is string-based so no floating point rounding
    surprises leak into the split math.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100 if value >= 0 else None
    if isinstance(value, float):
        # Only accept sane dollar floats (never derive cents from binary noise).
        if value < 0:
            return None
        return int(round(value * 100))
    if not isinstance(value, str):
        return None
    match = _MONEY_TEXT_RE.match(value)
    if not match:
        return None
    number = match.group(3).replace(",", "")
    if "." in number:
        whole, frac = number.split(".", 1)
        frac = (frac + "00")[:2]
    else:
        whole, frac = number, "00"
    try:
        cents = int(whole) * 100 + int(frac)
    except ValueError:
        return None
    return cents if cents >= 0 else None


def cents_to_value(cents: int) -> str:
    """Format integer cents as the PayPal decimal string, e.g. ``15500 -> "155.00"``."""
    if not isinstance(cents, int):
        raise TypeError("cents must be an int")
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "{}{}.{:02d}".format(sign, cents // 100, cents % 100)


def money(cents: int, currency: str = "USD") -> str:
    """Human-facing money string, e.g. ``money(15500) -> "$155.00"``."""
    value = cents_to_value(int(cents))
    whole, _, fraction = value.partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    grouped = "{:,}".format(int(whole)) if whole else "0"
    value = "{}{}.{}".format(sign, grouped, fraction)
    symbol = CURRENCY_SYMBOLS.get((currency or "USD").upper())
    if symbol:
        return symbol + value
    return "{} {}".format(value, currency)


def _participant_lookup(participants: Iterable[str]) -> Dict[str, str]:
    lookup = {}
    for name in participants:
        lookup.setdefault(name.lower(), name)
    return lookup


def _canonical_participant(name: str, lookup: Dict[str, str]) -> Optional[str]:
    return lookup.get(name.lower())


def validate_plan(raw: Any, allow_missing_payers: bool = False) -> List[str]:
    """Return a list of human-readable validation errors (empty when valid)."""
    errors: List[str] = []
    if not isinstance(raw, dict):
        return ["Plan must be a JSON object."]

    title = _clean_text(raw.get("title"))
    if len(title) > MAX_TITLE:
        errors.append("title must be at most {} characters.".format(MAX_TITLE))

    currency = _clean_text(raw.get("currency") or "USD").upper()
    if currency not in SUPPORTED_CURRENCIES:
        errors.append(
            "currency must be one of {}.".format(", ".join(SUPPORTED_CURRENCIES))
        )

    participants_raw = raw.get("participants")
    participants: List[str] = []
    if not isinstance(participants_raw, list):
        errors.append("participants must be a list of names.")
    else:
        seen = set()
        for item in participants_raw:
            name = _clean_text(item)
            if not name:
                errors.append("participant names cannot be empty.")
                continue
            if len(name) > MAX_NAME:
                errors.append("participant name {!r} is too long.".format(name[:20]))
                continue
            if name.lower() in seen:
                errors.append("participant {!r} is listed twice.".format(name))
                continue
            seen.add(name.lower())
            participants.append(name)
        if len(participants) < 2 and not errors:
            errors.append("A split needs at least 2 participants.")
        if len(participants) > MAX_PARTICIPANTS:
            errors.append("At most {} participants are supported.".format(MAX_PARTICIPANTS))

    expenses_raw = raw.get("expenses")
    lookup = _participant_lookup(participants)
    if not isinstance(expenses_raw, list):
        errors.append("expenses must be a list.")
    elif not expenses_raw:
        errors.append("Add at least one expense.")
    elif len(expenses_raw) > MAX_EXPENSES:
        errors.append("At most {} expenses are supported.".format(MAX_EXPENSES))
    else:
        for index, item in enumerate(expenses_raw, start=1):
            if not isinstance(item, dict):
                errors.append("expense #{} must be an object.".format(index))
                continue
            description = _clean_text(item.get("description"))
            if not description:
                errors.append("expense #{} needs a description.".format(index))
            elif len(description) > MAX_DESCRIPTION:
                errors.append(
                    "expense #{} description must be at most {} characters.".format(
                        index, MAX_DESCRIPTION
                    )
                )
            amount_cents = item.get("amount_cents")
            if amount_cents is None and "amount" in item:
                amount_cents = parse_money_to_cents(item.get("amount"))
            if not isinstance(amount_cents, int) or amount_cents <= 0:
                errors.append(
                    "expense #{} ({!r}) needs a positive amount.".format(
                        index, description[:30]
                    )
                )
            elif amount_cents > MAX_AMOUNT_CENTS:
                errors.append(
                    "expense #{} is above the {} limit.".format(
                        index, money(MAX_AMOUNT_CENTS, currency)
                    )
                )
            paid_by = _clean_text(item.get("paid_by"))
            if paid_by:
                if participants and not _canonical_participant(paid_by, lookup):
                    errors.append(
                        "expense #{} payer {!r} is not a participant.".format(
                            index, paid_by
                        )
                    )
            elif not allow_missing_payers:
                errors.append(
                    "expense #{} needs paid_by (who fronted the money?).".format(index)
                )
            note = _clean_text(item.get("note"))
            if len(note) > MAX_NOTE:
                errors.append("expense #{} note is too long.".format(index))
            shared_by = item.get("shared_by")
            if shared_by is not None:
                if not isinstance(shared_by, list) or not shared_by:
                    errors.append(
                        "expense #{} shared_by must be a non-empty list.".format(index)
                    )
                else:
                    seen_shared = set()
                    for name in shared_by:
                        canonical = _canonical_participant(_clean_text(name), lookup)
                        if canonical is None:
                            errors.append(
                                "expense #{} shared_by contains {!r} which is not a "
                                "participant.".format(index, _clean_text(name))
                            )
                        elif canonical.lower() in seen_shared:
                            errors.append(
                                "expense #{} shared_by lists {!r} twice.".format(
                                    index, canonical
                                )
                            )
                        else:
                            seen_shared.add(canonical.lower())

    notes_raw = raw.get("notes")
    if notes_raw is not None:
        if not isinstance(notes_raw, list):
            errors.append("notes must be a list of strings.")
        else:
            if len(notes_raw) > MAX_NOTES:
                errors.append("At most {} notes are supported.".format(MAX_NOTES))
            for item in notes_raw:
                if len(_clean_text(item)) > MAX_NOTE + 100:
                    errors.append("A note is too long.")
                    break
    return errors


def normalize_plan(
    raw: Any,
    allow_missing_payers: bool = False,
    source: str = "manual",
) -> Dict[str, Any]:
    """Validate and return a canonical plan with defaults filled in.

    ``paid_by`` is allowed to be ``None`` only for previews
    (``allow_missing_payers=True``); creating an event requires every expense
    to have a payer.
    """
    errors = validate_plan(raw, allow_missing_payers=allow_missing_payers)
    if errors:
        raise PlanValidationError(errors)

    participants = [_clean_text(name) for name in raw.get("participants") or []]
    lookup = _participant_lookup(participants)
    currency = _clean_text(raw.get("currency") or "USD").upper()
    title = _clip(_clean_text(raw.get("title")) or "Group expenses", MAX_TITLE)
    notes = [
        _clip(_clean_text(note), MAX_NOTE + 100)
        for note in (raw.get("notes") or [])
        if _clean_text(note)
    ][:MAX_NOTES]

    expenses = []
    for item in raw.get("expenses") or []:
        description = _clip(_clean_text(item.get("description")) or "Expense", MAX_DESCRIPTION)
        amount_cents = item.get("amount_cents")
        if amount_cents is None:
            amount_cents = parse_money_to_cents(item.get("amount"))
        paid_by_raw = _clean_text(item.get("paid_by"))
        paid_by = _canonical_participant(paid_by_raw, lookup) if paid_by_raw else None
        shared_raw = item.get("shared_by")
        if isinstance(shared_raw, list) and shared_raw:
            shared_by = []
            for name in shared_raw:
                canonical = _canonical_participant(_clean_text(name), lookup)
                if canonical and canonical not in shared_by:
                    shared_by.append(canonical)
            if not shared_by:
                shared_by = list(participants)
        else:
            shared_by = list(participants)
        expenses.append(
            {
                "description": description,
                "amount_cents": int(amount_cents),
                "paid_by": paid_by,
                "shared_by": shared_by,
                "note": _clip(_clean_text(item.get("note")), MAX_NOTE),
            }
        )

    return {
        "title": title,
        "currency": currency,
        "participants": participants,
        "expenses": expenses,
        "notes": notes,
        "source": source,
    }
