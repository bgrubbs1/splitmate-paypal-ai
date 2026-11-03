"""Application service layer: plans -> events -> PayPal orders -> statuses.

All state is in memory (a dict) on purpose: the hackathon demo never persists
personal data to disk and a restart gives you a clean dashboard.  Swapping the
store for SQLite/Postgres is the obvious next step (see README "What's next").

Money model
-----------
For every participant: ``net = paid - share``.

* ``net < 0`` -> they owe the pot; SplitMate creates ONE PayPal order for
  exactly that amount.
* ``net > 0`` -> they fronted more than their share; the dashboard shows a
  credit (the pot is owed to them).
* ``net == 0`` -> settled.

This collapses a web of pairwise IOUs into the smallest set of payments.  The
orders are created on the PayPal sandbox account behind the configured API
credentials; the capture step is what actually moves sandbox money.
"""

import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .parser import parse_plan
from .paypal_client import BasePayPalClient, PayPalAPIError, PayPalError, build_paypal_client
from .plan import PlanValidationError, money, normalize_plan
from .reminders import draft_reminder
from .splits import compute_split

DEMO_PLAN_FALLBACK = """Cabin weekend for 4: Alex, Sam, Priya and Jordan.
Cabin $620 paid by Alex.
Groceries $180 paid by Priya.
Gas $95 paid by Priya.
Firewood $40 paid by Jordan.
Wine $90 paid by Alex, split between Alex and Priya only.
Sam left Saturday morning, so he only shares the cabin and gas.
Jordan skipped the wine.
"""

STATUS_PENDING = "pending"
STATUS_ORDER_CREATED = "order_created"
STATUS_APPROVED = "approved"
STATUS_CAPTURED = "captured"
STATUS_CAPTURE_PENDING = "capture_pending"
STATUS_CAPTURE_FAILED = "capture_failed"
STATUS_CAPTURE_UNKNOWN = "capture_unknown"
STATUS_FAILED = "failed"
STATUS_CREDIT = "credit"
STATUS_SETTLED = "settled"

ORDER_STATUS_MAP = {
    "CREATED": STATUS_ORDER_CREATED,
    "PAYER_ACTION_REQUIRED": STATUS_ORDER_CREATED,
    "SAVED": STATUS_ORDER_CREATED,
    "APPROVED": STATUS_APPROVED,
}


class NotFoundError(LookupError):
    """Unknown event / participant / order."""


class ConflictError(RuntimeError):
    """The requested transition is not valid yet (e.g. capture before approval)."""


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value or "").strip("-").lower()
    return cleaned[:40] or "share"


class SplitMateService:
    """Owns the in-memory event store and all PayPal interactions."""

    def __init__(
        self,
        config: Config,
        paypal_client: Optional[BasePayPalClient] = None,
        demo_plan_path: Optional[str] = None,
    ):
        self.config = config
        self.paypal = paypal_client or build_paypal_client(config)
        self._events: Dict[str, Dict[str, Any]] = {}
        self._order_index: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()
        if demo_plan_path:
            self.demo_plan_path = Path(demo_plan_path)
        else:
            self.demo_plan_path = Path(__file__).resolve().parent.parent / "samples" / "demo-plan.txt"

    # -- demo / parse ------------------------------------------------------
    def demo_text(self) -> str:
        try:
            text = self.demo_plan_path.read_text(encoding="utf-8")
        except OSError:
            return DEMO_PLAN_FALLBACK
        return text.strip() + "\n"

    def health(self) -> Dict[str, Any]:
        info = self.config.public_description()
        info["demo_plan"] = True
        with self._lock:
            info["events_in_memory"] = len(self._events)
        return info

    def parse(self, text: str) -> Dict[str, Any]:
        return parse_plan(text, self.config)

    def preview(self, raw_plan: Any) -> Dict[str, Any]:
        """Recompute the split for an edited plan (no money moves)."""
        plan = normalize_plan(raw_plan, allow_missing_payers=True, source="manual")
        missing = [expense["description"] for expense in plan["expenses"] if not expense["paid_by"]]
        split = None
        warnings: List[str] = []
        if len(plan["participants"]) >= 2 and plan["expenses"]:
            split = compute_split(plan)
            if missing:
                warnings.append(
                    "Choose who paid: {}. Orders are disabled until every expense has a payer.".format(
                        ", ".join(missing[:8])
                    )
                )
        return {
            "plan": plan,
            "split": split,
            "warnings": warnings,
            "ready": not missing and bool(split),
            "parser": "manual",
            "model": None,
        }

    # -- events ------------------------------------------------------------
    def create_event(self, raw_plan: Any) -> Dict[str, Any]:
        plan = normalize_plan(raw_plan, allow_missing_payers=False, source="manual")
        split = compute_split(plan)
        event_id = "evt_" + secrets.token_hex(5)
        now = _iso_now()

        shares: List[Dict[str, Any]] = []
        share_by_name: Dict[str, Dict[str, Any]] = {}
        for row in split["participants"]:
            name = row["name"]
            share = {
                "name": name,
                "net_cents": row["net_cents"],
                "share_cents": row["share_cents"],
                "paid_cents": row["paid_cents"],
                "amount_cents": abs(row["net_cents"]),
                "status": STATUS_SETTLED,
                "order_id": None,
                "approve_url": None,
                "capture_id": None,
                "currency": plan["currency"],
                "capture_status": None,
                "provider_order_status": None,
                "capture_attempted": False,
                "reconciliation_required": False,
                "capture_evidence": [],
                "capture_validation_errors": [],
                "capture_history": [],
                "last_error": None,
                "updated_at": now,
            }
            if row["net_cents"] > 0:
                share["status"] = STATUS_CREDIT
            elif row["net_cents"] == 0:
                share["status"] = STATUS_SETTLED
            shares.append(share)
            share_by_name[name] = share

        event = {
            "id": event_id,
            "title": plan["title"],
            "currency": plan["currency"],
            "created_at": now,
            "paypal_mode": self.paypal.mode,
            "ai_mode": self.config.ai_mode,
            "plan": plan,
            "split": split,
            "shares": shares,
        }
        with self._lock:
            self._events[event_id] = event
            for order_row in split["orders"]:
                share = share_by_name[order_row["name"]]
                self._create_order_locked(event, share)
            return self._view_locked(event)

    def get_event(self, event_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._view_locked(self._get_locked(event_id))

    def list_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": event["id"],
                    "title": event["title"],
                    "created_at": event["created_at"],
                    "paypal_mode": event["paypal_mode"],
                    "orders": len([s for s in event["shares"] if s["order_id"]]),
                    "captured": len(
                        [s for s in event["shares"] if s["status"] == STATUS_CAPTURED]
                    ),
                }
                for event in self._events.values()
            ]

    def sync_share(self, event_id: str, name: str) -> Dict[str, Any]:
        with self._lock:
            event = self._get_locked(event_id)
            share = self._get_share_locked(event, name)
            if not share["order_id"]:
                raise ConflictError("This share has no PayPal order.")
            try:
                order = self.paypal.get_order(share["order_id"])
            except PayPalError as exc:
                share["last_error"] = str(exc)
                share["updated_at"] = _iso_now()
                raise
            self._apply_order_status(share, order)
            return {"event": self._view_locked(event), "share": dict(share)}

    def sync_all(self, event_id: str) -> Dict[str, Any]:
        with self._lock:
            event = self._get_locked(event_id)
            errors = []
            for share in event["shares"]:
                if not share["order_id"]:
                    continue
                try:
                    self._apply_order_status(share, self.paypal.get_order(share["order_id"]))
                except PayPalError as exc:
                    share["last_error"] = str(exc)
                    errors.append({"name": share["name"], "error": str(exc)})
            return {"event": self._view_locked(event), "errors": errors}

    def capture_share(self, event_id: str, name: str) -> Dict[str, Any]:
        with self._lock:
            event = self._get_locked(event_id)
            share = self._get_share_locked(event, name)
            if not share["order_id"]:
                raise ConflictError("This share has no PayPal order.")
            if share["status"] == STATUS_CAPTURED:
                return {"event": self._view_locked(event), "share": dict(share), "already": True}
            if self._capture_is_held(share):
                return {"event": self._view_locked(event), "share": dict(share),
                        "already": False, "reconciliation_required": True}
            # Latch BEFORE the money call. Lost/error/malformed responses must
            # never unlock a second capture; only a read-only sync may resolve it.
            share["capture_attempted"] = True
            try:
                result = self.paypal.capture_order(share["order_id"])
            except PayPalAPIError as exc:
                share["last_error"] = str(exc)
                share["updated_at"] = _iso_now()
                if exc.is_not_approved():
                    # A definite provider rejection is not an uncertain capture.
                    # A later, explicit user action after approval is safe.
                    share["capture_attempted"] = False
                    raise ConflictError(
                        "{} hasn't approved the PayPal order yet. Open the checkout "
                        "link, finish the sandbox payment, then capture.".format(name)
                    )
                self._hold_capture_error(share, str(exc))
                raise
            except PayPalError as exc:
                self._hold_capture_error(share, str(exc))
                raise
            except Exception:
                self._hold_capture_error(share, "Capture response could not be verified.")
                raise PayPalError(share["last_error"])
            self._apply_capture(share, result)
            return {"event": self._view_locked(event), "share": dict(share)}

    def capture_approved(self, event_id: str) -> Dict[str, Any]:
        with self._lock:
            event = self._get_locked(event_id)
            captured: List[str] = []
            failed: List[Dict[str, str]] = []
            unresolved: List[Dict[str, str]] = []
            for share in event["shares"]:
                if share["status"] in (STATUS_CAPTURED, STATUS_CREDIT, STATUS_SETTLED, STATUS_FAILED):
                    continue
                if not share["order_id"]:
                    continue
                try:
                    self.capture_share(event_id, share["name"])
                    if share["status"] == STATUS_CAPTURED:
                        captured.append(share["name"])
                    else:
                        unresolved.append({"name": share["name"], "status": share["status"],
                                           "error": share["last_error"] or "Sync to reconcile this capture."})
                except ConflictError as exc:
                    failed.append({"name": share["name"], "error": str(exc)})
                except PayPalError as exc:
                    error = share["last_error"] or str(exc)
                    failed.append({"name": share["name"], "error": error})
                    if share["reconciliation_required"]:
                        unresolved.append({"name": share["name"], "status": share["status"],
                                           "error": error})
            return {"event": self._view_locked(event), "captured": captured,
                    "failed": failed, "unresolved": unresolved}

    def retry_share(self, event_id: str, name: str) -> Dict[str, Any]:
        with self._lock:
            event = self._get_locked(event_id)
            share = self._get_share_locked(event, name)
            if share["status"] != STATUS_FAILED:
                raise ConflictError("Only failed share orders can be retried.")
            share["last_error"] = None
            self._create_order_locked(event, share)
            return {"event": self._view_locked(event), "share": dict(share)}

    # -- order helpers (call while holding the lock) -----------------------
    def _create_order_locked(self, event: Dict[str, Any], share: Dict[str, Any]) -> None:
        if share["amount_cents"] <= 0:
            return
        reference_id = "{}:{}".format(event["id"], _slug(share["name"]))[:200]
        description = "SplitMate - {} - {} share".format(event["title"], share["name"])[:120]
        try:
            order = self.paypal.create_order(
                amount_cents=share["amount_cents"],
                currency=event["currency"],
                description=description,
                reference_id=reference_id,
                return_url=self.config.return_url,
                cancel_url=self.config.cancel_url,
            )
        except PayPalError as exc:
            share["status"] = STATUS_FAILED
            share["last_error"] = str(exc)
            share["updated_at"] = _iso_now()
            return
        except Exception:  # noqa: BLE001 - never let one order kill the whole event
            share["status"] = STATUS_FAILED
            share["last_error"] = "Unexpected error while creating the PayPal order."
            share["updated_at"] = _iso_now()
            return
        share["status"] = STATUS_ORDER_CREATED
        share["provider_order_status"] = order.get("status")
        share["order_id"] = order.get("order_id")
        share["approve_url"] = order.get("approve_url")
        share["last_error"] = None
        share["updated_at"] = _iso_now()
        if share["order_id"]:
            self._order_index[share["order_id"]] = {
                "event_id": event["id"],
                "name": share["name"],
            }

    def _apply_order_status(self, share: Dict[str, Any], order: Dict[str, Any]) -> None:
        provider_status = str(order.get("status") or "UNKNOWN").upper()
        share["provider_order_status"] = provider_status
        if (order.get("capture_validation_errors")
                or order.get("order_id") != share["order_id"]
                or order.get("capture_evidence") or order.get("capture_id")
                or order.get("capture_status") or self._capture_is_held(share)
                or provider_status not in ORDER_STATUS_MAP):
            self._apply_capture(share, order)
            return
        share["status"] = ORDER_STATUS_MAP[provider_status]
        if order.get("approve_url"):
            share["approve_url"] = order["approve_url"]
        share["last_error"] = None
        share["updated_at"] = _iso_now()

    def _apply_capture(self, share: Dict[str, Any], result: Dict[str, Any]) -> None:
        evidence = result.get("capture_evidence")
        if evidence is None:
            evidence = [{key: result.get(key) for key in
                         ("capture_id", "capture_status", "amount_cents", "currency")}]
        # Retain observations for reconciliation, without storing payer PII.
        validation_errors = result.get("capture_validation_errors") or []
        observation = {"provider_order_id": result.get("order_id"),
                       "provider_order_status": result.get("status"), "captures": evidence,
                       "validation_errors": validation_errors}
        history = share["capture_history"]
        if not history or history[-1] != observation:
            history.append(observation)
        share["provider_order_status"] = result.get("status")
        share["capture_validation_errors"] = validation_errors
        if any(item.get("capture_id") or item.get("capture_status") for item in evidence):
            share["capture_evidence"] = evidence
            share["capture_id"] = result.get("capture_id") or share.get("capture_id")
            share["capture_status"] = result.get("capture_status")
        status = result.get("capture_status") or "UNKNOWN"
        if (validation_errors or len(evidence) != 1
                or result.get("order_id") != share["order_id"]):
            status = "UNKNOWN"
        complete = (
            len(evidence) == 1 and isinstance(result.get("capture_id"), str)
            and bool(result["capture_id"].strip())
            and result.get("order_id") == share["order_id"]
            and status == "COMPLETED"
            and result.get("amount_cents") == share["amount_cents"]
            and result.get("currency") == share["currency"]
        )
        if complete:
            share["status"] = STATUS_CAPTURED
            share["last_error"] = None
        elif status == "PENDING":
            share["status"] = STATUS_CAPTURE_PENDING
            share["last_error"] = "Capture pending; funds are not confirmed. Sync to reconcile; do not pay again."
        elif status in ("DECLINED", "FAILED", "DENIED", "REFUNDED", "PARTIALLY_REFUNDED"):
            share["status"] = STATUS_CAPTURE_FAILED
            share["last_error"] = "Capture is not completed ({}). Sync or review the existing order; do not pay again.".format(status)
        else:
            share["status"] = STATUS_CAPTURE_UNKNOWN
            share["last_error"] = "Capture outcome or amount could not be verified. Sync to reconcile; do not pay again."
        share["reconciliation_required"] = not complete
        share["updated_at"] = _iso_now()

    @staticmethod
    def _capture_is_held(share: Dict[str, Any]) -> bool:
        return bool(share.get("capture_attempted") or share.get("capture_id")
                    or share.get("capture_status") or share["status"] in
                    (STATUS_CAPTURED, STATUS_CAPTURE_PENDING, STATUS_CAPTURE_FAILED, STATUS_CAPTURE_UNKNOWN))

    @staticmethod
    def _hold_capture_error(share: Dict[str, Any], message: str) -> None:
        share["status"] = STATUS_CAPTURE_UNKNOWN
        share["reconciliation_required"] = True
        share["last_error"] = message + " Outcome unconfirmed; sync to reconcile before any further payment action."
        share["updated_at"] = _iso_now()
        share["capture_history"].append({"outcome": "unknown", "reason": "capture_request_failed",
                                         "observed_at": share["updated_at"]})

    # -- mock checkout -----------------------------------------------------
    def mock_checkout(self, order_id: str) -> Dict[str, Any]:
        """Details for the app's simulated checkout page (mock mode only)."""
        if self.paypal.mode != "mock":
            raise NotFoundError("Mock checkout is only available in mock mode.")
        with self._lock:
            event_id, name = self._order_info_locked(order_id)
            event = self._get_locked(event_id)
            share = self._get_share_locked(event, name)
            return {
                "order_id": order_id,
                "event_id": event["id"],
                "name": share["name"],
                "amount_cents": share["amount_cents"],
                "currency": event["currency"],
                "title": event["title"],
                "status": share["status"],
            }

    def approve_mock_order(self, order_id: str) -> Dict[str, Any]:
        """Simulate the buyer approving + paying on the mock checkout page."""
        if self.paypal.mode != "mock":
            raise NotFoundError("Mock checkout is only available in mock mode.")
        with self._lock:
            event_id, name = self._order_info_locked(order_id)
            event = self._get_locked(event_id)
            share = self._get_share_locked(event, name)
            if share["status"] == STATUS_CAPTURED:
                return {"event": self._view_locked(event), "share": dict(share), "already": True}
            if self._capture_is_held(share):
                return self.capture_share(event_id, name)
            if hasattr(self.paypal, "mark_approved"):
                self.paypal.mark_approved(order_id)  # type: ignore[attr-defined]
            return self.capture_share(event_id, name)

    # -- lookup / reminders ------------------------------------------------
    def find_order(self, order_id: str) -> Dict[str, Any]:
        with self._lock:
            event_id, name = self._order_info_locked(order_id)
            event = self._get_locked(event_id)
            share = self._get_share_locked(event, name)
            return {"event": self._view_locked(event), "share": dict(share)}

    def reminder(self, event_id: str, name: str) -> Dict[str, Any]:
        with self._lock:
            event = self._get_locked(event_id)
            share = self._get_share_locked(event, name)
            if share.get("reconciliation_required"):
                raise ConflictError("Reconcile this capture with Sync before drafting another payment reminder.")
            reminder = draft_reminder(event["plan"], share, self.config)
            return {
                "name": share["name"],
                "status": share["status"],
                "message": reminder["message"],
                "mode": reminder["mode"],
                "warning": reminder["warning"],
            }

    # -- internals ---------------------------------------------------------
    def _get_locked(self, event_id: str) -> Dict[str, Any]:
        event = self._events.get(str(event_id))
        if event is None:
            raise NotFoundError("Unknown event id.")
        return event

    @staticmethod
    def _get_share_locked(event: Dict[str, Any], name: str) -> Dict[str, Any]:
        wanted = str(name).strip().lower()
        for share in event["shares"]:
            if share["name"].lower() == wanted:
                return share
        raise NotFoundError("Unknown participant for this event.")

    def _order_info_locked(self, order_id: str) -> Any:
        info = self._order_index.get(str(order_id))
        if not info:
            raise NotFoundError("Unknown order id.")
        return info["event_id"], info["name"]

    @staticmethod
    def _view_locked(event: Dict[str, Any]) -> Dict[str, Any]:
        shares = [dict(share) for share in event["shares"]]
        orders = [
            {
                "name": share["name"],
                "amount_cents": share["amount_cents"],
                "status": share["status"],
                "order_id": share["order_id"],
                "approve_url": share["approve_url"],
                "capture_id": share["capture_id"],
                "capture_status": share["capture_status"],
                "reconciliation_required": share["reconciliation_required"],
                "last_error": share["last_error"],
            }
            for share in shares
            if share["order_id"] or share["status"] == STATUS_FAILED
        ]
        return {
            "id": event["id"],
            "title": event["title"],
            "currency": event["currency"],
            "created_at": event["created_at"],
            "paypal_mode": event["paypal_mode"],
            "ai_mode": event["ai_mode"],
            "plan": event["plan"],
            "split": event["split"],
            "shares": shares,
            "orders": orders,
            "totals": {
                "expenses_cents": event["split"]["total_cents"],
                "collected_cents": sum(
                    order["amount_cents"] for order in orders if order["status"] != STATUS_FAILED
                ),
                # Legacy collected_cents is a requested target, never proof of
                # payment. Keep its API meaning; offer an unambiguous alias.
                "collection_target_cents": sum(
                    order["amount_cents"] for order in orders if order["status"] != STATUS_FAILED
                ),
                "credits_cents": sum(
                    share["net_cents"] for share in shares if share["net_cents"] > 0
                ),
                "captured_cents": sum(
                    order["amount_cents"]
                    for order in orders
                    if order["status"] == STATUS_CAPTURED
                ),
                "order_count": len(orders),
                "captured_count": len(
                    [order for order in orders if order["status"] == STATUS_CAPTURED]
                ),
            },
        }


def explain_validation_error(exc: PlanValidationError) -> Dict[str, Any]:
    return {
        "message": "The plan needs a small fix before PayPal orders can be created.",
        "details": exc.errors,
    }


def money_text(cents: int, currency: str) -> str:
    return money(cents, currency)
