"""Fair-split math.

The important properties, which the unit tests pin down:

* every expense is allocated to integer cents using the largest-remainder
  method, so ``sum(shares) == amount`` exactly -- no lost cents anywhere;
* remainder cents are handed out in a deterministic order (the order of
  ``shared_by``), so the same plan always produces the same split;
* a participant's *net* position is ``paid - share``.  Positive means they
  fronted money and are owed a credit; negative means they owe the pot.
  Creating PayPal orders only for the net debtors collapses a messy web of
  pairwise IOUs into the smallest sensible set of payments.
"""

from typing import Any, Dict, List

from .plan import money


def split_amount(amount_cents: int, participants: List[str]) -> Dict[str, int]:
    """Split ``amount_cents`` evenly across ``participants``.

    Largest-remainder rounding: the first ``amount % n`` participants pay one
    extra cent.  Deterministic given the input order.
    """
    if not isinstance(amount_cents, int):
        raise TypeError("amount_cents must be an int")
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")
    if not participants:
        raise ValueError("need at least one participant to split an amount")
    count = len(participants)
    base, remainder = divmod(amount_cents, count)
    return {
        name: base + (1 if index < remainder else 0)
        for index, name in enumerate(participants)
    }


def compute_split(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Compute itemised shares, per-person nets, orders and reasoning.

    ``plan`` must be a canonical plan (see :func:`splitmate.plan.normalize_plan`).
    """
    participants: List[str] = list(plan["participants"])
    currency = plan.get("currency", "USD")
    expense_rows: List[Dict[str, Any]] = []
    share_totals = {name: 0 for name in participants}
    paid_totals = {name: 0 for name in participants}
    reasoning: List[str] = []
    rounding_notes: List[str] = []
    total_cents = 0

    for expense in plan["expenses"]:
        amount = expense["amount_cents"]
        total_cents += amount
        shared_by = [name for name in expense.get("shared_by") or participants]
        # Keep the plan's participant order so rounding is deterministic.
        ordered = [name for name in participants if name in shared_by]
        if not ordered:
            ordered = list(shared_by)
        shares = split_amount(amount, ordered)
        for name, value in shares.items():
            share_totals[name] += value
        payer = expense.get("paid_by")
        if payer in paid_totals:
            paid_totals[payer] += amount

        description = expense.get("description") or "Expense"
        split_reason = "{} -- {} shared by {} ({}) = {} each".format(
            description,
            money(amount, currency),
            len(ordered),
            ", ".join(ordered),
            money(shares[ordered[0]], currency) if len(set(shares.values())) == 1 else _range_text(shares, currency),
        )
        note = expense.get("note") or ""
        if note:
            split_reason += " [{}]".format(note)
        reasoning.append(split_reason)

        if amount % len(ordered):
            extra = amount % len(ordered)
            rounding_notes.append(
                "{} {} split {} ways: {} participant(s) pay 1 cent more "
                "(largest-remainder rounding, {} cent remainder).".format(
                    description, money(amount, currency), len(ordered), extra, extra
                )
            )

        expense_rows.append(
            {
                "description": description,
                "amount_cents": amount,
                "paid_by": payer,
                "shared_by": ordered,
                "shares": {name: shares[name] for name in ordered},
                "note": note,
            }
        )

    rows = []
    for name in participants:
        share_cents = share_totals[name]
        paid_cents = paid_totals[name]
        net_cents = paid_cents - share_cents
        if net_cents > 0:
            status = "credit"
        elif net_cents < 0:
            status = "owes"
        else:
            status = "settled"
        rows.append(
            {
                "name": name,
                "share_cents": share_cents,
                "paid_cents": paid_cents,
                "net_cents": net_cents,
                "status": status,
            }
        )

    debtors = [
        {"name": row["name"], "amount_cents": -row["net_cents"]}
        for row in rows
        if row["net_cents"] < 0
    ]
    creditors = [
        {"name": row["name"], "amount_cents": row["net_cents"]}
        for row in rows
        if row["net_cents"] > 0
    ]
    orders = [{"name": item["name"], "amount_cents": item["amount_cents"]} for item in debtors]
    collected = sum(item["amount_cents"] for item in orders)
    credits = sum(item["amount_cents"] for item in creditors)

    for row in rows:
        if row["net_cents"] > 0:
            reasoning.append(
                "{} fronted {} and owes {} -- is owed a {} credit.".format(
                    row["name"],
                    money(row["paid_cents"], currency),
                    money(row["share_cents"], currency),
                    money(row["net_cents"], currency),
                )
            )
        elif row["net_cents"] < 0:
            reasoning.append(
                "{} pays {} (share {} minus {} already fronted).".format(
                    row["name"],
                    money(-row["net_cents"], currency),
                    money(row["share_cents"], currency),
                    money(row["paid_cents"], currency),
                )
            )
        else:
            reasoning.append("{} is all settled up.".format(row["name"]))

    return {
        "currency": currency,
        "total_cents": total_cents,
        "participants": rows,
        "expenses": expense_rows,
        "debtors": debtors,
        "creditors": creditors,
        "orders": orders,
        "totals": {
            "expenses_cents": total_cents,
            "orders_cents": collected,
            "credits_cents": credits,
            "balanced": collected == credits,
        },
        "reasoning": reasoning,
        "rounding_notes": rounding_notes,
    }


def _range_text(shares: Dict[str, int], currency: str) -> str:
    low = min(shares.values())
    high = max(shares.values())
    if low == high:
        return money(low, currency)
    return "{} / {}".format(money(low, currency), money(high, currency))
