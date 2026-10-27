"""Natural-language event-plan parsing.

Two parsers with the same output contract:

``llm``
    An OpenAI-compatible chat completion is asked for strict JSON (see
    :func:`_parse_with_llm`).  This is the real AI step.  The response is
    validated with the same schema as everything else, so a hallucinated or
    truncated response can never reach the split math.

``rules``
    A deterministic, dependency-free parser used in mock mode and as an
    automatic fallback whenever the LLM is unavailable, errors, or returns
    invalid JSON.  It understands the phrasings the demo relies on:
    "Cabin $620 paid by Alex", "Alex paid the cabin", "$1,234.56",
    "split between Alex and Priya", "Sam left ... so he only shares the cabin
    and gas", "Jordan skipped the wine".  It is intentionally best-effort --
    the UI lets a human fix anything.

Both paths return::

    {"plan": <canonical-or-best-effort plan>, "split": <split preview|null>,
     "warnings": [...], "parser": "llm"|"rules", "model": str|null,
     "ready": bool}
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .llm import LLMError, chat_completion
from .plan import MAX_AMOUNT_CENTS, PlanValidationError, normalize_plan, parse_money_to_cents
from .splits import compute_split

MAX_PLAN_TEXT = 8000


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def parse_plan(text: str, config: Config) -> Dict[str, Any]:
    """Parse free text into a plan + split preview, with graceful fallback."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Paste an event plan first.")
    if len(text) > MAX_PLAN_TEXT:
        raise ValueError(
            "That plan is too long ({} characters). Keep it under {}.".format(
                len(text), MAX_PLAN_TEXT
            )
        )

    warnings: List[str] = []
    if config.ai_mode == "llm":
        try:
            plan = _parse_with_llm(text, config)
            return _result(plan, parser="llm", model=config.llm_model, warnings=warnings)
        except Exception as exc:  # noqa: BLE001 - any AI failure must degrade, never crash
            warnings.append(
                "AI parsing was unavailable ({}). Used the built-in deterministic "
                "parser instead -- review the plan before collecting.".format(
                    _safe_message(exc)
                )
            )

    try:
        plan = _parse_with_rules(text, config)
    except Exception as exc:  # noqa: BLE001 - defensive: a parser bug shouldn't 500
        raise PlanValidationError(
            ["Could not understand that plan ({}). Try the demo plan, or write "
             "one expense per line like: 'Cabin $620 paid by Alex'.".format(_safe_message(exc))]
        )
    return _result(plan, parser="rules", model=None, warnings=warnings + plan.pop("_warnings", []))


def _result(
    plan: Dict[str, Any],
    parser: str,
    model: Optional[str],
    warnings: List[str],
) -> Dict[str, Any]:
    ready = bool(plan.get("participants") and len(plan["participants"]) >= 2
                 and plan.get("expenses")
                 and all(expense.get("paid_by") for expense in plan["expenses"]))
    split = None
    missing = [
        expense["description"]
        for expense in plan.get("expenses") or []
        if not expense.get("paid_by")
    ]
    if plan.get("participants") and len(plan["participants"]) >= 2 and plan.get("expenses"):
        try:
            split = compute_split(plan)
        except (ValueError, KeyError):
            split = None
    if missing:
        warnings.append(
            "Choose who paid for: {}. Then recalculate the split.".format(
                ", ".join(missing[:8])
            )
        )
    if len(plan.get("participants") or []) < 2:
        warnings.append(
            "I could not find at least two participants. Add names to the plan "
            "(e.g. 'Participants: Alex, Sam, Priya') or add them in the editor."
        )
    return {
        "plan": plan,
        "split": split,
        "warnings": warnings,
        "parser": parser,
        "model": model,
        "ready": ready,
    }


def _safe_message(exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    return re.sub(r"\s+", " ", str(message))[:240]


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are SplitMate's expense-plan parser. Convert the user's natural-language group-expense plan into STRICT JSON.

Return ONLY one JSON object with this schema:
{
  "title": "short event title",
  "currency": "USD",
  "participants": ["Name", ...],
  "expenses": [
    {
      "description": "short item name",
      "amount": 620.00,
      "paid_by": "Name",
      "shared_by": ["Name", ...],
      "note": "short reason if the split is unusual, else empty string"
    }
  ],
  "notes": ["one short note per adjustment, e.g. 'Sam left Saturday morning'"]
}

Rules:
- amount is a number in major currency units (620.00, never cents, never a string).
- paid_by and every shared_by name MUST be one of the participants, spelled exactly the same way.
- Default shared_by is all participants. Only narrow it when the plan says someone missed a cost, left early, or the item was explicitly split between a subset.
- Include every expense exactly once. Never invent expenses or people who are not in the text.
- Keep descriptions under 40 characters, notes under 160 characters.
- No markdown, no commentary, no code fences: output raw JSON only."""


def _parse_with_llm(text: str, config: Config) -> Dict[str, Any]:
    content = chat_completion(
        config,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    raw = _extract_json(content)
    try:
        plan = normalize_plan(raw, allow_missing_payers=True, source="llm")
    except PlanValidationError as exc:
        raise LLMError("AI plan failed validation: {}".format("; ".join(exc.errors[:3])))
    # A plan with fewer than 2 participants is useless; let the rules path try.
    if len(plan["participants"]) < 2 or not plan["expenses"]:
        raise LLMError("AI plan was incomplete")
    return plan


def _extract_json(content: str) -> Dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("AI response was not JSON")
        try:
            value = json.loads(text[start:end + 1])
        except ValueError:
            raise LLMError("AI response was not valid JSON")
    if not isinstance(value, dict):
        raise LLMError("AI response was not a JSON object")
    return value


# ---------------------------------------------------------------------------
# Rule-based parser
# ---------------------------------------------------------------------------

MONEY_RE = re.compile(
    r"(?P<prefix>\$|\u20ac|\u00a3|\bUSD\b|\bEUR\b|\bGBP\b|\bCAD\b|\bAUD\b)?\s*"
    r"(?P<number>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"(?P<suffix>\s*(?:dollars?|bucks|euros?|pounds?|\bUSD\b|\bEUR\b|\bGBP\b))?",
    re.IGNORECASE,
)

SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+|\n+")

NAME_TOKEN_RE = re.compile(r"\b([A-Z][a-zA-Z'\u2019-]{1,24})\b")

PAID_BY_RE = re.compile(r"\bpaid\s+(?:by|for)\s+([A-Z][a-zA-Z'\u2019-]{1,24})", re.IGNORECASE)
NAME_PAID_RE = re.compile(
    r"\b([A-Z][a-zA-Z'\u2019-]{1,24})\s+(?:paid|covered|fronted|venmoed|zelled|sent)\b"
)
WAS_PAID_RE = re.compile(
    r"\b([^.;]{2,60}?)\s+was\s+(?:paid|covered|fronted)\s+by\s+([A-Z][a-zA-Z'\u2019-]{1,24})",
    re.IGNORECASE,
)
ONLY_SHARES_RE = re.compile(
    r"\b([A-Z][a-zA-Z'\u2019-]{1,24})\s+(?:left|departed|skipped|missed|sat\s+out|"
    r"was\s+away|wasn['\u2019]?t\s+there|had\s+to\s+leave|went\s+home)\b"
    r"[^.!?]*?\bonly\s+(?:shares?|pays?\s+for|owes?\s+for|is\s+in\s+for|splits?)\b"
    r"([^.!?]*)",
    re.IGNORECASE,
)
SKIPPED_RE = re.compile(
    r"\b([A-Z][a-zA-Z'\u2019-]{1,24})\s+(?:skipped|missed|sat\s+out|didn['\u2019]?t\s+join|"
    r"wasn['\u2019]?t\s+there\s+for|didn['\u2019]?t\s+have)\b\s*(?:the\s+)?([^.!?;,]+)",
    re.IGNORECASE,
)
SPLIT_BETWEEN_RE = re.compile(
    r"\b(?:split|shared|divided)\s+(?:between|among|across)\s+([^.!?;]+?)(?:\s+only)?(?:[.!?;]|$)",
    re.IGNORECASE,
)
EXCEPT_RE = re.compile(r"\bexcept\s+(?:for\s+)?([A-Z][a-zA-Z'\u2019-]{1,24})", re.IGNORECASE)

# Words that are never participant names even when capitalised.
NAME_STOPWORDS = {
    "a", "airbnb", "all", "also", "an", "and", "another", "apartment", "arrived",
    "as", "at", "bar", "beer", "bills", "both", "breakfast", "but", "cabin", "cabin.",
    "cabins", "car", "carpool", "cost", "costs", "crackers", "day", "dinner",
    "drinks", "each", "early", "evening", "everybody", "everyone", "everything",
    "except", "expense", "expenses", "firewood", "food", "for", "friday", "fuel",
    "gas", "grocery", "groceries", "group", "guests", "he", "her", "here", "his",
    "hotel", "house", "i", "if", "in", "is", "it", "item", "items", "its", "just",
    "late", "left", "lunch", "lyft", "minus", "monday", "morning", "my", "night",
    "no", "none", "not", "notes", "of", "on", "only", "or", "our", "parking",
    "participant", "participants", "people", "plus", "pot", "priya.", "rent",
    "restaurant", "saturday", "she", "shop", "snacks", "so", "some", "someone",
    "split", "stuff", "sunday", "supplies", "taxi", "tent", "thanks", "that",
    "the", "their", "then", "there", "these", "they", "things", "this", "thursday",
    "tickets", "to", "total", "trip", "tuesday", "uber", "us", "van", "was", "we",
    "wednesday", "weekend", "were", "when", "where", "who", "wine", "with", "wood",
    "you", "your",
}

_LEAD_CONNECTORS = re.compile(
    r"^(?:\s|,|;|-|\u2013|\u2014|and\b|plus\b|also\b|then\b|for\b|with\b|"
    r"including\b|on\b|at\b|of\b|the\b|a\b|an\b)+",
    re.IGNORECASE,
)
_PAYER_PHRASE_RE = re.compile(
    r"\b(?:paid|pays|pay|covered|covers|fronted|fronts|venmoed|zelled|sent|splits?|"
    r"shared|was\s+paid|were\s+paid)\b(?:\s+(?:by|for|to))?\s+"
    r"[A-Z][a-zA-Z'\u2019-]{1,24}",
    re.IGNORECASE,
)
_TRAILING_PAYER_RE = re.compile(
    r"[\(\-\u2013\u2014]\s*[A-Z][a-zA-Z'\u2019-]{1,24}\s*"
    r"(?:paid|covered|fronted)?\s*\)?\s*$",
    re.IGNORECASE,
)


def _tokens(text: str) -> List[str]:
    return [
        token.strip(" .,;:!?()[]\"'")
        for token in re.split(r"\s*(?:,|;|/|\band\b|&|\+)\s*", text or "")
        if token and token.strip(" .,;:!?()[]\"'")
    ]


def _is_name(word: str) -> bool:
    if not word or not word[0].isupper():
        return False
    return word.lower().strip(".") not in NAME_STOPWORDS


def _names_in(text: str) -> List[str]:
    names: List[str] = []
    for match in NAME_TOKEN_RE.finditer(text or ""):
        word = match.group(1)
        if _is_name(word) and word not in names:
            names.append(word)
    return names


def _clean_name_candidates(names: List[str], known: Optional[List[str]] = None) -> List[str]:
    known_lookup = {name.lower(): name for name in known or []}
    result: List[str] = []
    for name in names:
        if not _is_name(name):
            continue
        canonical = known_lookup.get(name.lower(), name)
        if canonical not in result:
            result.append(canonical)
    return result


def _detect_currency(text: str, default: str) -> str:
    lowered = text.lower()
    if "\u20ac" in text or "eur" in lowered or "euro" in lowered:
        return "EUR"
    if "\u00a3" in text or "gbp" in lowered or "pound" in lowered:
        return "GBP"
    if "cad" in lowered or "canadian" in lowered:
        return "CAD"
    if "aud" in lowered or "australian" in lowered:
        return "AUD"
    if "usd" in lowered or "$" in text or "dollar" in lowered:
        return "USD"
    return default


def _sentence_title(text: str) -> str:
    first_line = text.strip().split("\n", 1)[0].strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0]
    candidate = first_sentence
    colon = first_sentence.find(":")
    if 0 <= colon < 80:
        candidate = first_sentence[:colon]
    candidate = re.sub(r"(?i)\s+for\s+\d+\s*$", "", candidate.strip())
    candidate = candidate.strip(" .,:;-\u2013\u2014")
    candidate = re.sub(r"\s+", " ", candidate)
    # A title should not be an expense sentence or contain money.
    if re.search(r"[$\u20ac\u00a3]|\d{2,}", candidate):
        candidate = ""
    if not candidate:
        candidate = "Group expenses"
    if len(candidate) > 60:
        candidate = " ".join(candidate.split()[:8])
    return candidate[:1].upper() + candidate[1:]


def _money_candidates(sentence: str) -> List[Tuple[re.Match, int]]:
    results: List[Tuple[re.Match, int]] = []
    for match in MONEY_RE.finditer(sentence):
        prefix = (match.group("prefix") or "").strip()
        suffix = (match.group("suffix") or "").strip()
        number = match.group("number")
        has_symbol = bool(prefix or suffix)
        if not has_symbol:
            if "." not in number:
                try:
                    value = int(number.replace(",", ""))
                except ValueError:
                    continue
                if value < 10:
                    continue
                if 1900 <= value <= 2100:
                    continue
                before = sentence[: match.start()].lower()
                after = sentence[match.end(): match.end() + 14].lower()
                if re.search(r"\bfor\s+$", before):
                    continue
                if re.match(r"\s*(?:ways|people|persons|friends|of\s+us)", after):
                    continue
        cents = parse_money_to_cents(number)
        if cents is None or cents <= 0 or cents > MAX_AMOUNT_CENTS:
            continue
        results.append((match, cents))
    return results


def _find_payer(clause: str, known_names: List[str]) -> Optional[str]:
    lookup = {name.lower(): name for name in known_names}

    def canonical(candidate: str) -> Optional[str]:
        if not candidate:
            return None
        if lookup:
            return lookup.get(candidate.lower())
        return candidate if _is_name(candidate) else None

    for pattern in (PAID_BY_RE, NAME_PAID_RE):
        for match in pattern.finditer(clause):
            name = canonical(match.group(1))
            if name:
                return name
    # "cabin was paid by Alex" / "the cabin was covered by Alex"
    match = re.search(
        r"\bwas\s+(?:paid|covered|fronted)\s+by\s+([A-Z][a-zA-Z'\u2019-]{1,24})",
        clause,
        re.IGNORECASE,
    )
    if match:
        name = canonical(match.group(1))
        if name:
            return name
    return None


def _clean_description(text: str) -> str:
    value = text or ""
    value = re.sub(
        r"\b[A-Z][a-zA-Z'\u2019-]{1,24}\s+(?:paid|covered|fronted|venmoed|zelled|sent)\b",
        " ",
        value,
    )
    value = _PAYER_PHRASE_RE.sub(" ", value)
    value = re.sub(
        r"\b(?:split|shared|divided)\s+(?:between|among|across)\b[^,.;!?]*",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\b(?:was|were|is|are)\s+(?:paid|covered|fronted)\b", " ", value, flags=re.I)
    value = re.sub(r"\b(?:split|shared|divided)\b", " ", value, flags=re.I)
    value = _LEAD_CONNECTORS.sub("", value)
    if ":" in value:
        left, right = value.rsplit(":", 1)
        value = right if re.search(r"[A-Za-z]", right) else left
    value = re.sub(r"^[^A-Za-z0-9$]+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .,:;-\u2013\u2014")
    value = re.sub(r"(?i)\s+(?:cost|costs|was|were|is|are|for|on|at|of|the|a|an)$", "", value)
    value = value.strip(" .,:;-\u2013\u2014")
    if not value or not re.search(r"[A-Za-z]", value):
        return ""
    if len(value) > 60:
        value = " ".join(value.split()[:6])
    return value[:1].upper() + value[1:]


def _parse_with_rules(text: str, config: Config) -> Dict[str, Any]:
    warnings: List[str] = []
    currency = _detect_currency(text, config.currency)
    sentences = [part.strip() for part in SENTENCE_BREAK_RE.split(text) if part.strip()]
    if not sentences:
        raise PlanValidationError(["Plan text is empty."])

    # ---- participants: explicit list first, otherwise infer from roles -----
    explicit: List[str] = []
    for match in re.finditer(
        r"(?:participants?|people|guests?|attendees?|group\s+of\s+\d+|for\s+\d+)"
        r"\s*[:\-\u2013\u2014]\s*(.+)",
        text,
        re.IGNORECASE,
    ):
        explicit.extend(_names_in(match.group(1)))
        if explicit:
            break
    explicit = _clean_name_candidates(explicit)

    role_names: List[str] = []
    for pattern in (
        NAME_PAID_RE,
        PAID_BY_RE,
        ONLY_SHARES_RE,
        SKIPPED_RE,
        EXCEPT_RE,
    ):
        for match in pattern.finditer(text):
            role_names.extend(_names_in(match.group(1)))
    for match in SPLIT_BETWEEN_RE.finditer(text):
        role_names.extend(_names_in(match.group(1)))
    for pattern in (
        r"\b(?:among|between)\s+([A-Z][^.;!?]{1,90})",
        r"\b(?:with|including)\s+([A-Z][^.;!?]{1,90})",
    ):
        for match in re.finditer(pattern, text):
            names = _names_in(match.group(1))
            if len(names) >= 2:
                role_names.extend(names)
    role_names = _clean_name_candidates(role_names)

    participants = explicit or role_names
    if not explicit:
        if participants:
            warnings.append(
                "Participants were inferred from names mentioned in the text; "
                "edit them if anyone is missing."
            )

    lookup = {name.lower(): name for name in participants}

    def canonical(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        if lookup:
            return lookup.get(name.lower())
        return name if _is_name(name) else None

    # ---- expenses ----------------------------------------------------------
    expenses: List[Dict[str, Any]] = []
    notes: List[str] = []

    for sentence in sentences:
        money = _money_candidates(sentence)
        if not money:
            stripped = _clean_note(sentence)
            if stripped and _looks_like_adjustment(stripped):
                notes.append(stripped)
            continue

        sentence_expenses: List[Dict[str, Any]] = []
        payer_hints: List[Optional[str]] = []
        for index, (match, cents) in enumerate(money):
            head_start = 0 if index == 0 else money[index - 1][0].end()
            head = sentence[head_start: match.start()]
            tail_end = money[index + 1][0].start() if index + 1 < len(money) else len(sentence)
            tail = sentence[match.end(): tail_end]
            clause = head + " " + tail

            description = _clean_description(head)
            if not description:
                description = _clean_description(_PAYER_PHRASE_RE.sub(" ", tail))
            if not description:
                description = "Expense {}".format(len(expenses) + 1)

            payer = _find_payer(tail, participants) or _find_payer(head, participants)
            payer_hints.append(payer)

            item_note = ""
            split_between = None
            split_match = SPLIT_BETWEEN_RE.search(clause)
            if split_match:
                names = _clean_name_candidates(_names_in(split_match.group(1)))
                resolved = [name for name in (canonical(n) for n in names) if name]
                if len(resolved) >= 2:
                    split_between = resolved
                else:
                    warnings.append(
                        "Could not read the 'split between' list for '{}'; "
                        "using all participants.".format(description)
                    )

            except_names = [
                canonical(name) for name in _names_in(EXCEPT_RE.search(clause).group(1))
                if canonical(name)
            ] if EXCEPT_RE.search(clause) else []

            expense: Dict[str, Any] = {
                "description": description,
                "amount_cents": cents,
                "paid_by": payer,
                "shared_by": None,
                "note": item_note,
                "_sentence": sentence,
            }
            if split_between:
                expense["shared_by"] = split_between
                expense["note"] = "Split between " + _join_names(split_between)
            if except_names:
                base = expense["shared_by"] or list(participants)
                expense["shared_by"] = [n for n in base if n not in except_names]
                expense["note"] = _join_notes(
                    expense["note"],
                    "Excluding " + ", ".join(except_names),
                )
            sentence_expenses.append(expense)

        # Propagate a single payer across expenses that share a sentence, e.g.
        # "Groceries $180 and gas $95 paid by Priya".
        unique_hints = [name for name in payer_hints if name]
        if len(unique_hints) == 1:
            only = unique_hints[0]
            for expense in sentence_expenses:
                if not expense["paid_by"]:
                    expense["paid_by"] = only
        expenses.extend(sentence_expenses)

    if not expenses:
        raise PlanValidationError(
            [
                "No expenses found. Add amounts, e.g. 'Cabin $620 paid by Alex' "
                "or click 'Load demo plan'."
            ]
        )

    # ---- participant list fallback: payers are participants ----------------
    if not participants:
        participants = _clean_name_candidates(
            [expense["paid_by"] for expense in expenses if expense["paid_by"]]
        )
        if participants:
            warnings.append(
                "Participants were inferred from who paid; add everyone else if needed."
            )
    lookup = {name.lower(): name for name in participants}

    def canonical2(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return lookup.get(name.lower())

    for expense in expenses:
        if expense["paid_by"]:
            expense["paid_by"] = canonical2(expense["paid_by"])
        shared = [canonical2(name) for name in (expense["shared_by"] or participants)]
        expense["shared_by"] = [name for name in shared if name] or list(participants)

    # ---- no-money attribution sentences ------------------------------------
    for sentence in sentences:
        if _money_candidates(sentence):
            continue
        match = re.search(
            r"([A-Z][a-zA-Z'\u2019-]{1,24})\s+(?:paid|covered|fronted|got)\s+"
            r"(?:for\s+|the\s+|this\s+)?([^.!?;]+)",
            sentence,
        )
        if match:
            payer = canonical2(match.group(1))
            target = _clean_description(match.group(2))
            if payer and target:
                for expense in expenses:
                    if _descriptions_match(target, expense["description"]) and not expense["paid_by"]:
                        expense["paid_by"] = payer
        match = WAS_PAID_RE.search(sentence)
        if match:
            target = _clean_description(match.group(1))
            payer = canonical2(match.group(2))
            if payer and target:
                for expense in expenses:
                    if _descriptions_match(target, expense["description"]) and not expense["paid_by"]:
                        expense["paid_by"] = payer

    # ---- partial-attendance adjustments -----------------------------------
    sentence_expense_map: Dict[str, List[Dict[str, Any]]] = {}
    for sentence in sentences:
        sentence_expense_map.setdefault(sentence, [])
    for expense in expenses:
        raw_sentence = expense.pop("_sentence", None)
        if raw_sentence in sentence_expense_map:
            sentence_expense_map[raw_sentence].append(expense)
    for sentence in sentences:
        _apply_adjustments(
            sentence,
            expenses,
            sentence_expense_map.get(sentence, []),
            participants,
            canonical2,
            warnings,
            notes,
        )

    for index, expense in enumerate(expenses, start=1):
        if not expense["paid_by"]:
            warnings.append(
                "Expense #{} ({}) has no payer -- choose one in the editor.".format(
                    index, expense["description"]
                )
            )
        if not expense["shared_by"]:
            warnings.append(
                "Expense #{} ({}) has nobody sharing it -- it will use all "
                "participants.".format(index, expense["description"])
            )
            expense["shared_by"] = list(participants)

    raw_plan = {
        "title": _sentence_title(text),
        "currency": currency,
        "participants": participants,
        "expenses": expenses,
        "notes": _dedupe(notes)[:10],
    }
    plan = _soft_normalize(raw_plan)
    plan["_warnings"] = _dedupe(warnings)
    return plan


def _looks_like_adjustment(sentence: str) -> bool:
    lowered = sentence.lower()
    markers = (
        "left", "skipped", "missed", "only shares", "only pays", "split between",
        "except", "sat out", "didn't", "did not", "wasn't", "was not", "except for",
        "covered", "paid", "fronted", "everyone", "we all",
    )
    return any(marker in lowered for marker in markers)


def _clean_note(sentence: str) -> str:
    value = re.sub(r"\s+", " ", sentence).strip()
    value = value[:300]
    return value


def _join_names(names: List[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _join_notes(first: str, second: str) -> str:
    if first and second:
        return first + "; " + second
    return first or second


def _descriptions_match(target: str, description: str) -> bool:
    target_tokens = _item_tokens(target)
    description_tokens = _item_tokens(description)
    if not target_tokens or not description_tokens:
        return False
    for left in target_tokens:
        for right in description_tokens:
            if left == right or (len(left) >= 4 and left in right) or (len(right) >= 4 and right in left):
                return True
    return False


_ITEM_STOPWORDS = {
    "the", "a", "an", "run", "trip", "thing", "stuff", "cost", "costs", "bill",
    "bills", "for", "and", "of", "on", "at", "to", "this", "that", "it", "all",
    "some", "our", "my", "their", "his", "her",
}


def _item_tokens(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'\u2019-]{1,24}", (text or "").lower())
    return [word for word in words if word not in _ITEM_STOPWORDS]


def _apply_adjustments(
    sentence: str,
    expenses: List[Dict[str, Any]],
    sentence_expenses: List[Dict[str, Any]],
    participants: List[str],
    canonical,
    warnings: List[str],
    notes: List[str],
) -> None:
    # 1) "Sam left Saturday morning, so he only shares the cabin and gas."
    for match in ONLY_SHARES_RE.finditer(sentence):
        name = canonical(match.group(1))
        if not name:
            continue
        items = _item_tokens(match.group(2))
        if not items:
            continue
        matched = [expense for expense in expenses if _matches_items(expense, items)]
        if not matched:
            warnings.append(
                "Could not match '{}' to any expense for {}.".format(
                    match.group(2).strip()[:60], name
                )
            )
            continue
        allowed_ids = {id(expense) for expense in matched}
        labels = _join_names([expense["description"] for expense in matched])
        for expense in expenses:
            if id(expense) in allowed_ids:
                if name not in expense["shared_by"]:
                    expense["shared_by"].append(name)
                expense["note"] = _join_notes(
                    expense["note"], "{} only shares {}".format(name, labels)
                )
            elif name in expense["shared_by"]:
                expense["shared_by"].remove(name)
                expense["note"] = _join_notes(
                    expense["note"], "{} left early and is excluded".format(name)
                )
        notes.append(_clean_note(sentence))

    # 2) "Jordan skipped the wine."
    for match in SKIPPED_RE.finditer(sentence):
        name = canonical(match.group(1))
        if not name:
            continue
        items = _item_tokens(match.group(2))
        if not items:
            continue
        matched = [expense for expense in expenses if _matches_items(expense, items)]
        if not matched:
            warnings.append(
                "Could not match '{}' to any expense for {}.".format(
                    match.group(2).strip()[:60], name
                )
            )
            continue
        for expense in matched:
            if name in expense["shared_by"]:
                expense["shared_by"].remove(name)
                expense["note"] = _join_notes(
                    expense["note"], "{} skipped this".format(name)
                )
        notes.append(_clean_note(sentence))

    # 3) "Everyone except Priya was in for the wine."
    for match in EXCEPT_RE.finditer(sentence):
        name = canonical(match.group(1))
        if not name:
            continue
        for expense in sentence_expenses:
            if name in expense["shared_by"]:
                expense["shared_by"].remove(name)
                expense["note"] = _join_notes(expense["note"], "{} excluded".format(name))


def _matches_items(expense: Dict[str, Any], items: List[str]) -> bool:
    description_tokens = _item_tokens(expense["description"])
    for item in items:
        for token in description_tokens:
            if token == item or (len(item) >= 4 and item in token) or (len(token) >= 4 and token in item):
                return True
    return False


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result


def _soft_normalize(raw_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort canonicalisation so previews work even for broken plans."""
    try:
        return normalize_plan(raw_plan, allow_missing_payers=True, source="rules")
    except PlanValidationError:
        participants = [
            str(name).strip()
            for name in (raw_plan.get("participants") or [])
            if str(name).strip()
        ]
        expenses = []
        for item in raw_plan.get("expenses") or []:
            amount = item.get("amount_cents")
            if not isinstance(amount, int) or amount <= 0:
                continue
            expenses.append(
                {
                    "description": str(item.get("description") or "Expense")[:60],
                    "amount_cents": amount,
                    "paid_by": item.get("paid_by") if item.get("paid_by") in participants else None,
                    "shared_by": [
                        name for name in (item.get("shared_by") or participants) if name in participants
                    ] or list(participants),
                    "note": str(item.get("note") or "")[:240],
                }
            )
        return {
            "title": str(raw_plan.get("title") or "Group expenses")[:80],
            "currency": "USD",
            "participants": participants,
            "expenses": expenses,
            "notes": [str(note)[:300] for note in (raw_plan.get("notes") or [])][:10],
            "source": "rules",
        }
