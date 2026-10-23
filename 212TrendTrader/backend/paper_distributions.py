"""Audited gross USD paper distributions. Unknown corporate actions need review.

Verified events use the exchange ex-date for entitlement, including due bills.
Unverified dividends/splits pause exits; they never manufacture cash credits.
"""
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ELME_SOURCE = "https://ir.elmecommunities.com/news-events/press-releases/detail/382/elme-communities-provides-update-onliquidation-activities"
VERIFIED_DISTRIBUTIONS = ({
    "symbol": "ELME", "ex_date": "2026-10-23", "pay_date": "2026-10-22",
    "amount_per_share": 1.74, "currency": "USD",
    "kind": "liquidating_distribution", "source": ELME_SOURCE,
},)
ENTRY_BLOCKS = {"ELME": "Liquidating company: excluded from new paper entries"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def market_date(timestamp=None):
    instant = datetime.now(timezone.utc) if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc)
    return instant.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def opened_date(position):
    stamp = position.get("opened_at")
    if not stamp:
        return None
    instant = datetime.fromisoformat(stamp)
    if instant.tzinfo is None:
        raise ValueError("Position entry timestamp must include timezone")
    return instant.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def action_key(action):
    return action["symbol"].upper() + ":" + action["ex_date"] + ":distribution"


def quote_actions(chart):
    actions = []
    for kind, values in (chart.get("events") or {}).items():
        for event in (values or {}).values():
            stamp = event.get("date")
            if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
                raise ValueError("Invalid corporate action timestamp")
            action = {"kind": kind, "ex_date": market_date(stamp)}
            if kind == "dividends":
                amount = float(event["amount"])
                if not math.isfinite(amount) or amount <= 0:
                    raise ValueError("Invalid distribution amount")
                action["amount_per_share"] = amount
            actions.append(action)
    return {"corporate_actions": actions, "corporate_actions_checked": True,
            "currency": (chart.get("meta") or {}).get("currency", "USD")}


def settle_receivables(state, today=None):
    today = today or market_date()
    changed = False
    for event in state.setdefault("distribution_ledger", []):
        if event["status"] == "receivable" and event["pay_date"] <= today:
            state["cash"] = float(state["cash"]) + event["gross_amount"]
            event.update(status="credited", credited_at=utc_now())
            changed = True
    return changed


def distribution_review_reason(state, position, quote):
    if quote.get("currency", "USD") != "USD":
        return "Non-USD quote requires currency review"
    if quote.get("provider") == "yahoo_intraday" and not quote.get("corporate_actions_checked"):
        return "Corporate action coverage unavailable"
    today = market_date(quote.get("quote_timestamp"))
    opened = opened_date(position)
    # Review the complete observed set before making any accounting changes.
    for observed in quote.get("corporate_actions", []):
        ex_date = observed["ex_date"]
        if ex_date > today or (opened and ex_date <= opened):
            continue
        if not opened:
            return "Legacy position needs an entry-date review for corporate actions"
        verified = next((a for a in VERIFIED_DISTRIBUTIONS
                         if a["symbol"] == position["symbol"].upper()
                         and a["ex_date"] == ex_date), None)
        if observed["kind"] != "dividends" or not verified or not math.isclose(
                observed.get("amount_per_share", 0), verified["amount_per_share"],
                rel_tol=0, abs_tol=0.000001):
            return "Unrecognised corporate action: review required before automatic exits"
    for action in VERIFIED_DISTRIBUTIONS:
        if action["symbol"] != position["symbol"].upper() or action["ex_date"] > today:
            continue
        if not opened:
            return "Legacy position needs an entry-date review for distributions"
        if opened >= action["ex_date"]:
            continue
        key = action_key(action)
        if any(e["id"] == key and e.get("position_opened_at") == position["opened_at"]
               for e in state.setdefault("distribution_ledger", [])):
            continue
        gross = float(position["quantity"]) * action["amount_per_share"]
        state["distribution_ledger"].append(dict(
            action, id=key, quantity=position["quantity"], gross_amount=gross,
            position_opened_at=position["opened_at"], recorded_at=utc_now(),
            status="receivable", accounting="gross_no_tax_or_fx"))
        position["distribution_per_share"] = position.get("distribution_per_share", 0.0) + action["amount_per_share"]
        position.setdefault("original_target", position["target"])
        position.setdefault("original_stop", position["stop"])
        position["target"] -= action["amount_per_share"]
        position["stop"] -= action["amount_per_share"]
    settle_receivables(state, today)
    return None


def reconcile_elme(state, evidence):
    """Idempotent credit for the closed trade; preserve its actual execution."""
    action = VERIFIED_DISTRIBUTIONS[0]
    key = action_key(action)
    ledger = state.setdefault("distribution_ledger", [])
    if any(e["id"] == key for e in ledger):
        return False
    trades = [t for t in state.get("trades", []) if t["symbol"] == "ELME"]
    if len(trades) != 1:
        raise ValueError("Expected exactly one ELME trade for reconciliation")
    trade = trades[0]
    if not (trade["exit_at"][:10] >= action["ex_date"] and
            math.isclose(trade["entry"], 1.75) and
            math.isclose(trade["quantity"], evidence["quantity"]) and
            evidence["held_on"] == "2026-10-13"):
        raise ValueError("ELME entitlement evidence does not match")
    gross = trade["quantity"] * action["amount_per_share"]
    before = float(state["cash"])
    ledger.append(dict(
        action, id=key, quantity=trade["quantity"], gross_amount=gross,
        recorded_at=utc_now(), credited_at=utc_now(), status="credited",
        accounting="gross_no_tax_or_fx", correction=True, evidence=evidence,
        cash_before=before, cash_after=before + gross))
    state["cash"] = before + gross
    trade["price_return_pct"] = trade["return_pct"]
    trade["distribution_per_share"] = action["amount_per_share"]
    trade["distribution_gross"] = gross
    trade["profit_gross"] = trade["quantity"] * (trade["exit"] + action["amount_per_share"] - trade["entry"])
    trade["return_pct"] = (trade["exit"] + action["amount_per_share"] - trade["entry"]) / trade["entry"] * 100
    trade.update(currency="USD", accounting="gross_no_tax_or_fx", reconciliation_id=key,
                 exit_context="Original stop sale retained; stop did not account for liquidation distribution")
    state["currency"] = "USD"
    state.setdefault("events", []).append(
        f"ELME reconciliation: credited USD {gross:.2f} gross liquidating distribution; original sale retained")
    return True
