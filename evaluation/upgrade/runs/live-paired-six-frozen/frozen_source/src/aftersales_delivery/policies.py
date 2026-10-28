"""Pure strict sandbox policy checks. Absence preserves legacy rules."""

from datetime import UTC, datetime


def violation(order, policy, kind, amount, *, now=None, receipt=False):
    if policy is None:
        return None
    if order["status"] == "cancelled":
        return "ORDER_CANCELLED"
    # An authorized return remains receivable after its application window expires.
    if kind != "logistics" and not receipt:
        try:
            deadline = datetime.fromisoformat(policy["aftersales_until"])
            if deadline.tzinfo is None:
                return "INVALID_POLICY"
        except (TypeError, ValueError):
            return "INVALID_POLICY"
        if (now or datetime.now(UTC)) > deadline:
            return "AFTERSALES_EXPIRED"
    if kind == "refund" and order["status"] in {"shipped", "delivered"}:
        return "RETURN_REQUIRED"
    if kind == "return_refund" and order["status"] not in {"shipped", "delivered"}:
        return "RETURN_INELIGIBLE"
    if kind == "cancel_order" and order["status"] != "paid":
        return "CANCEL_INELIGIBLE"
    return None


def risk(policy, amount):
    if policy is None:
        return "low", False
    manual = policy["risk_level"] == "high" or (
        amount is not None and amount >= policy["manual_review_threshold_cents"]
    )
    return ("high" if manual else policy["risk_level"]), manual
