"""Independent, opt-in momentum paper experiment. Never submits broker orders."""
import argparse
import fcntl
import json
import math
from pathlib import Path
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

VERSION = "momentum-paper-0.1"
DEFAULT_SYMBOLS = "AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA"


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Expected a finite positive number")
    return value


def ema(values, period):
    result = values[0]
    for value in values[1:]:
        result += (value - result) * 2 / (period + 1)
    return result


def parse_chart(chart, now):
    meta = chart["meta"]
    regular = meta["currentTradingPeriod"]["regular"]
    start, end = int(regular["start"]), int(regular["end"])
    values = chart["indicators"]["quote"][0]
    bars = []
    for i, timestamp in enumerate(chart.get("timestamp", [])):
        stamp = int(timestamp)
        # Current session, completed five-minute bars only.
        if not start <= stamp < end or stamp + 300 > now:
            continue
        try:
            row = {key: positive(values[key][i]) for key in ("open", "high", "low", "close")}
            volume = float(values["volume"][i])
            if not math.isfinite(volume) or volume < 0:
                continue
            if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                continue
            bars.append(dict(row, time=stamp, volume=volume))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return {"bars": sorted(bars, key=lambda b: b["time"]),
            "price": positive(meta["regularMarketPrice"]),
            "quote_time": int(meta["regularMarketTime"]),
            "session_start": start, "session_end": end,
            "currency": meta.get("currency"), "provider": "yahoo_chart"}


def fetch_snapshot(symbol):
    request = Request(
        "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="") + "?interval=5m&range=5d",
        headers={"User-Agent": "Mozilla/5.0 212TrendTrader/1.0"})
    with urlopen(request, timeout=12) as response:
        payload = json.load(response)
    return parse_chart(payload["chart"]["result"][0], time.time())


def quote_reason(snapshot, now):
    try:
        positive(snapshot["price"])
        if snapshot["currency"] != "USD":
            return "USD quotes required"
        if not snapshot["session_start"] <= now < snapshot["session_end"]:
            return "Outside regular session; simulated exits also paused"
        age = now - snapshot["quote_time"]
        if not math.isfinite(age) or not 0 <= age <= 90:
            return "Quote stale or in future; simulated trading paused"
    except (KeyError, TypeError, ValueError):
        return "Invalid quote"
    return None


def entry_signal(snapshot, now):
    reason = quote_reason(snapshot, now)
    if reason:
        return {"qualified": False, "reason": reason}
    bars = snapshot["bars"]
    if len(bars) < 22:
        return {"qualified": False, "reason": "Need 22 completed bars in current session"}
    recent = bars[-22:]
    if any(b["time"] - a["time"] != 300 for a, b in zip(recent, recent[1:])):
        return {"qualified": False, "reason": "Missing or duplicated five-minute bars"}
    if not 300 <= now - bars[-1]["time"] <= 660:
        return {"qualified": False, "reason": "Completed bars stale or in future"}
    closes = [b["close"] for b in bars]
    fast, slow = ema(closes, 9), ema(closes, 21)
    avg_volume = sum(b["volume"] for b in bars[-21:-1]) / 20
    dollar_volume = sum(b["close"] * b["volume"] for b in bars[-21:-1]) / 20
    volume_ratio = bars[-1]["volume"] / avg_volume if avg_volume else 0
    move_pct = (closes[-1] / closes[-4] - 1) * 100
    chase_pct = abs(snapshot["price"] / closes[-1] - 1) * 100
    blockers = []
    if not snapshot["price"] >= closes[-1] > fast > slow:
        blockers.append("Short-term trend not confirmed")
    if closes[-1] <= max(b["high"] for b in bars[-4:-1]):
        blockers.append("No three-bar breakout")
    if volume_ratio < 1.2:
        blockers.append("Insufficient relative volume")
    if dollar_volume < 1_000_000:
        blockers.append("Average five-minute dollar volume below $1m")
    if not 0 < move_pct <= 1.5 or chase_pct > 0.25:
        blockers.append("Move overextended or quote too far from signal close")
    return {"qualified": not blockers, "reason": "; ".join(blockers) or "Momentum breakout",
            "score": round(min(volume_ratio, 3) * 10 + move_pct, 4),
            "signal_time": bars[-1]["time"], "volume_ratio": round(volume_ratio, 3)}


def new_state(seed, config):
    return {"version": VERSION, "seed": seed, "cash": seed, "currency": "USD",
            "config": config, "position": None, "trades": [], "blocked": {},
            "used_signals": {}, "status": "Waiting for data", "last_poll": None,
            "execution_model": "Last price with assumed adverse cost per side; no executable bid/ask data"}


def step(state, snapshots, now):
    config = state["config"]
    # Costs are an explicit assumption, not measured spreads or guaranteed fills.
    rate = (config["cost_per_side_pct"] + config["fx_per_side_pct"]) / 100
    state["last_poll"] = datetime.fromtimestamp(now, timezone.utc).isoformat()
    position = state["position"]
    if position:
        snapshot = snapshots.get(position["symbol"])
        reason = quote_reason(snapshot or {}, now)
        if reason:
            state["status"] = reason
            return
        exit_price = snapshot["price"] * (1 - rate)
        proceeds = position["quantity"] * exit_price
        net_return = (proceeds / position["invested"] - 1) * 100
        state["equity"] = state["cash"] + proceeds
        reason = ("TARGET" if net_return >= config["target_pct"] else
                  "STOP" if net_return <= -config["stop_pct"] else
                  "TIMEOUT" if now - position["opened_at"] >= config["max_hold_minutes"] * 60 else None)
        if reason:
            trade = dict(position, closed_at=now, exit_price=exit_price,
                         raw_exit_price=snapshot["price"], reason=reason,
                         net_return_pct=net_return, net_profit=proceeds-position["invested"],
                         estimated_exit_cost=position["quantity"] * snapshot["price"] * rate)
            state["cash"] += proceeds
            state["trades"].append(trade)
            state["position"] = None
            state["blocked"][position["symbol"]] = {"until": now + 1800,
                                                       "reset_after": position["signal_time"],
                                                       "reset_seen": None}
            state["status"] = reason + "; next entry evaluated on a later poll"
        else:
            state["status"] = "Holding; fresh quote checked"
        return
    candidates = []
    diagnostics = {}
    for symbol in config["symbols"]:
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            diagnostics[symbol] = "Data unavailable"
            continue
        signal = entry_signal(snapshot, now)
        diagnostics[symbol] = signal["reason"]
        block = state["blocked"].get(symbol)
        if block:
            # Only a valid, later, non-qualifying signal can reset an old setup.
            signal_time = signal.get("signal_time", 0)
            if signal_time > block["reset_after"] and not signal["qualified"]:
                block["reset_seen"] = signal_time
            if now < block["until"] or not block["reset_seen"] or signal_time <= block["reset_seen"]:
                diagnostics[symbol] = "Cooldown / waiting for a fresh setup after reset"
                continue
        if signal["qualified"] and signal["signal_time"] > state["used_signals"].get(symbol, 0):
            candidates.append((signal["score"], symbol, signal))
    state["diagnostics"] = diagnostics
    state["equity"] = state["cash"]
    if not candidates:
        outside = snapshots and all(not q["session_start"] <= now < q["session_end"] for q in snapshots.values())
        state["status"] = "Outside regular session; waiting" if outside else "Waiting; no qualifying fresh entry"
        return
    _, symbol, signal = max(candidates)
    raw = snapshots[symbol]["price"]
    price = raw * (1 + rate)
    cash = state["cash"]
    if cash <= 0:
        state["status"] = "No paper cash"
        return
    state["position"] = {"symbol": symbol, "opened_at": now,
                         "entry_price": price, "raw_entry_price": raw,
                         "quantity": cash / price, "invested": cash,
                         "estimated_entry_cost": cash / price * raw * rate,
                         "signal_time": signal["signal_time"], "entry_signal": signal}
    state["used_signals"][symbol] = signal["signal_time"]
    state["cash"] = 0.0
    state["status"] = "Simulated buy " + symbol
    state["equity"] = cash / price * raw * (1 - rate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--seed", type=positive, default=1000.0)
    parser.add_argument("--target-pct", type=positive, default=1.0)
    parser.add_argument("--stop-pct", type=positive, default=0.75)
    parser.add_argument("--max-hold-minutes", type=positive, default=60.0)
    parser.add_argument("--cost-per-side-pct", type=positive, default=0.10)
    parser.add_argument("--fx-per-side-pct", type=float, default=0.0)
    parser.add_argument("--loop", action="store_true", help="Opt in to polling; default is one pass")
    args = parser.parse_args()
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
    if not 1 <= len(symbols) <= 20 or any(not s.replace(".", "").replace("-", "").isalnum() for s in symbols):
        parser.error("Provide 1 to 20 valid symbols")
    if not math.isfinite(args.fx_per_side_pct) or not 0 <= args.fx_per_side_pct < 1:
        parser.error("FX cost must be between 0 and 1 percent per side")
    if args.cost_per_side_pct + args.fx_per_side_pct >= 5:
        parser.error("Total cost assumption must be below 5 percent per side")
    config = {"symbols": symbols, "target_pct": args.target_pct, "stop_pct": args.stop_pct,
              "max_hold_minutes": args.max_hold_minutes, "cost_per_side_pct": args.cost_per_side_pct,
              "fx_per_side_pct": args.fx_per_side_pct}
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another runner already owns this state file")
        state = json.loads(args.state.read_text()) if args.state.exists() else new_state(args.seed, config)
        if state["config"] != config or state["seed"] != args.seed or state["version"] != VERSION:
            parser.error("Configuration changed; use a different state file for a new experiment")
        while True:
            started = time.time()
            # Outside regular hours, probe one symbol only, then sleep. No fabricated fills.
            wanted = [state["position"]["symbol"]] if state["position"] else symbols
            snapshots, errors = {}, {}
            for symbol in wanted:
                try:
                    snapshot = fetch_snapshot(symbol)
                    snapshots[symbol] = snapshot
                    if not snapshot["session_start"] <= time.time() < snapshot["session_end"]:
                        break
                except Exception as exc:
                    errors[symbol] = type(exc).__name__
            step(state, snapshots, time.time())
            state["data_errors"] = errors
            state["quote_ages_seconds"] = {s: round(time.time()-v["quote_time"]) for s,v in snapshots.items()}
            temp = args.state.with_suffix(".tmp")
            temp.write_text(json.dumps(state, indent=2, allow_nan=False))
            temp.replace(args.state)
            print(json.dumps({"status": state["status"], "cash": state["cash"],
                              "equity": state.get("equity"), "closed_trades": len(state["trades"]),
                              "quote_ages_seconds": state["quote_ages_seconds"]}), flush=True)
            if not args.loop:
                break
            outside = snapshots and all(not q["session_start"] <= time.time() < q["session_end"] for q in snapshots.values())
            time.sleep(max(1, (300 if outside else 60) - (time.time()-started)))


if __name__ == "__main__":
    main()
