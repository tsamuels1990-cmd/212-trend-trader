"""Isolated AI research, chronological portfolio replay and historical import.

No broker API, credentials, or writes to either existing paper strategy.
Only load trusted, locally generated model bundles (Python pickle).
"""
import argparse
import hashlib
import json
import math
import pickle
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FEATURES = ["return_5m", "return_15m", "return_60m", "volume_ratio",
            "range_pct", "distance_mean20", "minute_of_session"]
SYMBOLS = "AAPL MSFT NVDA AMZN META GOOGL TSLA AMD AVGO ORCL CRM ADBE JPM BAC GS V MA UNH JNJ LLY ABBV XOM CVX COP WMT COST HD PG KO CAT".split()


@dataclass(frozen=True)
class Policy:
    seed: float = 1000.
    target: float = .01
    stop: float = .0075
    cost: float = .001  # adverse execution assumption per side; not measured
    max_hold: int = 3600
    allocation: float = .25
    daily_loss: float = .01
    max_trades: int = 5
    cooldown: int = 1800
    min_probability: float = .55
    min_expected_return: float = .001

    def __post_init__(self):
        for k in ["seed", "target", "stop", "cost", "allocation", "daily_loss",
                  "min_probability", "min_expected_return"]:
            if not math.isfinite(getattr(self, k)):
                raise ValueError("Nonfinite policy: " + k)
        if not (self.seed > 0 and 0 < self.target < 1 and 0 < self.stop < 1
                and 0 <= self.cost < .05 and 0 < self.allocation <= 1
                and 0 < self.daily_loss < 1 and 0 <= self.min_probability <= 1
                and self.max_hold > 0 and self.max_hold % 300 == 0
                and self.max_trades > 0 and self.cooldown >= 0):
            raise ValueError("Invalid policy")


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def features(bars, session_start):
    """Rows: timestamp, open, high, low, close, volume. Past 21 bars only."""
    if len(bars) < 21:
        return None
    h = bars[-21:]
    if any(b[0] - a[0] != 300 for a, b in zip(h, h[1:])):
        return None
    closes = [b[4] for b in h]
    volume = sum(b[5] for b in h[:-1]) / 20
    if volume <= 0:
        return None
    x = [(closes[-1] / closes[-2] - 1) * 100,
         (closes[-1] / closes[-4] - 1) * 100,
         (closes[-1] / closes[-13] - 1) * 100,
         h[-1][5] / volume, (h[-1][2] - h[-1][3]) / closes[-1] * 100,
         (closes[-1] / (sum(closes[:-1]) / 20) - 1) * 100,
         (h[-1][0] - session_start) / 60]
    return x if all(math.isfinite(v) for v in x) else None


def groups(db):
    result = defaultdict(list)
    today = datetime.now(NY).date().isoformat()
    for symbol, day, *row in db.execute(
            "SELECT symbol,session,stamp,open,high,low,close,volume FROM bars "
            "WHERE revisions=0 AND session<? ORDER BY symbol,stamp", (today,)):
        # Reject mislabelled sessions; keep half-days but require an opening bar.
        local = datetime.fromtimestamp(row[0], NY)
        if local.date().isoformat() != day or local.weekday() >= 5:
            continue
        result[(day, symbol)].append(tuple(row))
    return {k: v for k, v in result.items()
            if v and (datetime.fromtimestamp(v[0][0], NY).hour,
                      datetime.fromtimestamp(v[0][0], NY).minute) == (9, 30)}


def exit_trade(future, policy):
    """Enter next bar open. Gap exits at open; same-candle ambiguity stop first.

    Missing bars force a conservative low/open exit at the next observation.
    Session end is a mandatory exit. Costs are applied on both sides.
    """
    entry = future[0][1] * (1 + policy.cost)
    target = entry * (1 + policy.target) / (1 - policy.cost)
    stop = entry * (1 - policy.stop) / (1 - policy.cost)
    deadline = future[0][0] + policy.max_hold
    previous = future[0][0] - 300
    for n, row in enumerate(future):
        stamp, o, h, low, close, _ = row
        if stamp != previous + 300:
            raw, reason, when = min(o, low, stop), "DATA_GAP_CONSERVATIVE", stamp + 300
        elif o <= stop or o >= target:
            raw, reason, when = o, "STOP_GAP" if o <= stop else "TARGET_GAP", stamp
        elif low <= stop:
            raw, reason, when = stop, "STOP_AMBIGUOUS" if h >= target else "STOP", stamp + 300
        elif h >= target:
            raw, reason, when = target, "TARGET", stamp + 300
        elif stamp + 300 >= deadline or n == len(future) - 1:
            raw, reason, when = close, "TIMEOUT" if stamp + 300 >= deadline else "SESSION_END", stamp + 300
        else:
            previous = stamp
            continue
        return {"entry_time": future[0][0], "exit_time": when, "entry_price": entry,
                "exit_price": raw * (1 - policy.cost), "reason": reason,
                "net_return": raw * (1 - policy.cost) / entry - 1}
    raise ValueError("No future bars")


def samples(data, policy):
    out = []
    for (day, symbol), bars in sorted(data.items()):
        for i in range(20, len(bars) - 1):
            x = features(bars[:i+1], bars[0][0])
            # Require 75 minutes until the observed historical session end.
            if x is None or bars[-1][0] - bars[i][0] < 4500:
                continue
            future = bars[i+1:i+1+policy.max_hold//300]
            if len(future) != policy.max_hold//300 or future[0][0] != bars[i][0] + 300:
                continue
            if any(b[0]-a[0] != 300 for a,b in zip(future, future[1:])):
                continue  # exclude invalid labels, not silent interpolated history
            trade = exit_trade(future, policy)
            label = 2 if trade["reason"].startswith("TARGET") else (
                0 if trade["reason"].startswith("STOP") else 1)
            out.append({"day": day, "symbol": symbol, "signal": bars[i][0],
                        "x": x, "label": label, "net_return": trade["net_return"]})
    return sorted(out, key=lambda s: (s["day"], s["signal"], s["symbol"]))


def probabilities(bundle, xx):
    import numpy as np
    raw = bundle["model"].predict_proba(xx)
    calibrated = np.column_stack([
        calibrator.predict_proba(np.log(np.clip(raw[:, i:i+1], 1e-6, 1-1e-6) /
                                       (1 - np.clip(raw[:, i:i+1], 1e-6, 1-1e-6))))[:, 1]
        for i, calibrator in enumerate(bundle["calibrators"])])
    return calibrated / calibrated.sum(axis=1, keepdims=True)


def score(bundle, xx):
    p = probabilities(bundle, xx)
    return [(float(row[2]), float(sum(a*b for a,b in zip(row, bundle["class_returns"]))))
            for row in p]


def replay(data, days, bundle, policy, baseline=False):
    """One account, one position, max allocation, daily loss lock and cooldown.

    Candidate generation uses only past bars. Future bars are consumed solely
    after an entry has been selected. A missing future bar never filters signals.
    """
    candidates = []
    for (day, symbol), bars in sorted(data.items()):
        if day not in days:
            continue
        for i in range(20, len(bars)-1):
            x = features(bars[:i+1], bars[0][0])
            if x is not None and bars[-1][0]-bars[i][0] >= 4500:
                candidates.append((bars[i][0]+300, symbol, day, x, bars, i))
    candidates.sort(key=lambda r: (r[0], r[1]))
    predictions = score(bundle, [r[3] for r in candidates]) if candidates and not baseline else [(1., 1.)]*len(candidates)
    by_stamp = defaultdict(list)
    for row, (p, expected) in zip(candidates, predictions):
        # Baseline: positive 15m momentum; identical capital/risk/execution rules.
        qualified = row[3][1] > 0 if baseline else p >= policy.min_probability and expected >= policy.min_expected_return
        if qualified:
            by_stamp[row[0]].append((expected if not baseline else row[3][1], row))
    equity, peak, max_dd, available = policy.seed, policy.seed, 0., 0
    trades, blocked, daily = [], {}, {}
    marks = [(0, equity)]
    for stamp, choices in sorted(by_stamp.items()):
        if stamp < available:
            continue
        for _, row in sorted(choices, key=lambda r: (-r[0], r[1][1])):
            _, symbol, day, _, bars, i = row
            ledger = daily.setdefault(day, {"start": equity, "trades": 0, "locked": False})
            if ledger["locked"] or ledger["trades"] >= policy.max_trades or stamp < blocked.get(symbol, 0):
                continue
            future = bars[i+1:]
            if future[0][0] != stamp:
                continue  # unavailable next open; unfilled signal
            execution = exit_trade(future, policy)
            invested = equity * policy.allocation
            profit = invested * execution["net_return"]
            # Mark open-position lows for conservative intratrade drawdown.
            for b in future:
                if b[0] >= execution["exit_time"]:
                    break
                value = equity-invested + invested*b[3]*(1-policy.cost)/execution["entry_price"]
                max_dd = max(max_dd, (peak-value)/peak)
            equity += profit
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak-equity)/peak)
            trades.append(dict(execution, symbol=symbol, profit=profit, equity=equity))
            marks.append((execution["exit_time"], equity))
            available = execution["exit_time"] + 1
            ledger["trades"] += 1
            ledger["locked"] = equity <= ledger["start"]*(1-policy.daily_loss)
            if profit < 0:
                blocked[symbol] = execution["exit_time"] + policy.cooldown
            break
    gross_win = sum(max(0,t["profit"]) for t in trades)
    gross_loss = -sum(min(0,t["profit"]) for t in trades)
    return {"start": policy.seed, "end": equity, "return_pct": (equity/policy.seed-1)*100,
            "max_drawdown_pct": max_dd*100, "trades": len(trades),
            "wins": sum(t["profit"] > 0 for t in trades),
            "profit_factor": gross_win/gross_loss if gross_loss else None,
            "trade_log": trades, "equity_curve": marks,
            "daily_loss_rule": "Locks new entries after realised loss; not a guaranteed loss cap"}


def train(db_path, output, development=False):
    import numpy as np
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    policy = Policy()
    db = sqlite3.connect("file:" + str(Path(db_path).resolve()) + "?mode=ro", uri=True)
    data = groups(db)
    rows = samples(data, policy)
    days = sorted({r["day"] for r in rows})
    if len(days) < (25 if development else 60):
        raise ValueError("Need 60 usable complete sessions (25 for development-only); found " + str(len(days)))
    a,b,c = [int(len(days)*v) for v in [.55,.70,.85]]
    partitions = {"train": days[:a], "calibration": days[a+1:b],
                  "validation": days[b+1:c], "test": days[c+1:]}
    def select(name):
        return [r for r in rows if r["day"] in partitions[name]]
    training, calibration = select("train"), select("calibration")
    for name in partitions:
        if {r["label"] for r in select(name)} != {0,1,2}:
            raise ValueError("All three outcome classes required in " + name)
    x = np.asarray([r["x"] for r in training])
    y = np.asarray([r["label"] for r in training])
    model = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=7,
        max_depth=3, min_samples_leaf=100, l2_regularization=1., random_state=212)
    model.fit(x,y)
    raw = model.predict_proba([r["x"] for r in calibration])
    calibrators = []
    cy = np.asarray([r["label"] for r in calibration])
    for i in range(3):
        clipped = np.clip(raw[:, i:i+1],1e-6,1-1e-6)
        calibrator = LogisticRegression(C=1., random_state=212)
        calibrator.fit(np.log(clipped/(1-clipped)), (cy==i).astype(int))
        calibrators.append(calibrator)
    returns = [float(np.mean([r["net_return"] for r in training if r["label"]==i])) for i in range(3)]
    bundle = {"model":model,"calibrators":calibrators,"class_returns":returns}
    reports = {}
    frequency = np.bincount(y, minlength=3)/len(y)
    for name in ["validation","test"]:
        selected=select(name)
        pp=probabilities(bundle,[r["x"] for r in selected])
        reports[name] = {"first":min(partitions[name]),"last":max(partitions[name]),
            "examples":len(selected), "class_counts":dict(Counter(r["label"] for r in selected)),
            "log_loss":float(log_loss([r["label"] for r in selected],pp,labels=[0,1,2])),
            "baseline_log_loss":float(log_loss([r["label"] for r in selected],np.tile(frequency,(len(selected),1)),labels=[0,1,2])),
            "portfolio":replay(data,set(partitions[name]),bundle,policy),
            "momentum_baseline":replay(data,set(partitions[name]),bundle,policy,True),
            "double_cost":replay(data,set(partitions[name]),bundle,Policy(cost=.002))}
    test = reports["test"]
    blockers = []
    if development: blockers.append("Development-only model; no paper trading promotion")
    if len(days)<60: blockers.append("Fewer than 60 usable complete sessions")
    if test["portfolio"]["trades"]<30: blockers.append("Fewer than 30 held-out simulated trades")
    if test["portfolio"]["return_pct"]<=0: blockers.append("Held-out simulated return is not positive")
    if test["double_cost"]["return_pct"]<=0: blockers.append("Double-cost held-out simulated return is not positive")
    if test["log_loss"]>=test["baseline_log_loss"]: blockers.append("Prediction loss does not beat class-frequency baseline")
    if test["portfolio"]["return_pct"]<=test["momentum_baseline"]["return_pct"]: blockers.append("Does not outperform simple momentum baseline")
    if test["portfolio"]["max_drawdown_pct"]>5: blockers.append("Held-out drawdown exceeds 5% research threshold")
    blockers.append("Forward shadow observation and execution review still required")
    manifest = {"version":1,"created_at":datetime.now(timezone.utc).isoformat(),
        "mode":"development" if development else "research", "usable_sessions":len(days),
        "symbols":sorted({r["symbol"] for r in rows}), "features":FEATURES,
        "partitions":partitions,"policy":asdict(policy),"sklearn_version":sklearn.__version__,
        "latest_evaluation_day":max(days),"paper_eligible":False,"blockers":blockers,
        "reports":reports,"limitations":[
            "Current large-cap selection has survivorship/selection bias; not the full Trading 212 universe",
            "Yahoo chart history is revised, unverified and not point-in-time data",
            "Assumed execution costs; no measured bid/ask, liquidity, latency, FX, fees or tax",
            "No corporate-action validation; split gaps may contaminate features",
            "Historical session end inferred from observed bars; incomplete current session excluded",
            "Training labels require contiguous future bars; missing history can bias evaluation",
            "Conservative bar drawdown is an estimate; daily loss rule blocks entries after exit",
            "One fixed chronological split; repeated test inspection risks overfitting; no profitability claim"]}
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    model_path=output/"model.pkl"
    model_path.write_bytes(pickle.dumps(bundle))
    manifest["model_sha256"]=hashlib.sha256(model_path.read_bytes()).hexdigest()
    atomic_json(output/"report.json",manifest)
    print(json.dumps({k:manifest[k] for k in ["mode","usable_sessions","symbols","blockers"]}))
    print(json.dumps({name:{k:reports[name]["portfolio"][k] for k in ["trades","return_pct","max_drawdown_pct"]} for name in reports}))
    return manifest


def load_bundle(directory):
    import sklearn
    p=Path(directory)
    manifest=json.loads((p/"report.json").read_text())
    raw=(p/"model.pkl").read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest["model_sha256"]:
        raise ValueError("Model checksum mismatch")
    if manifest["sklearn_version"]!=sklearn.__version__ or manifest["features"]!=FEATURES:
        raise ValueError("Model runtime/schema mismatch")
    return pickle.loads(raw),manifest


def backfill(output, symbols):
    """Separate historical archive. Never modifies intraday.sqlite."""
    from ai_research_data import fetch,clean_bars,connect,save
    path=Path(output)
    if path.exists():
        raise ValueError("Use a new history output path")
    path.parent.mkdir(parents=True,exist_ok=True)
    db=connect(path)
    audit={"source":"Yahoo chart 60d five-minute historical snapshot",
           "fetched_at":datetime.now(timezone.utc).isoformat(),"point_in_time_verified":False,
           "requested_symbols":symbols,"results":{}}
    try:
        for symbol in symbols:
            try:
                chart=fetch(symbol,"60d")
                rows=clean_bars(chart,time.time())
                if not rows: raise ValueError("Empty history")
                save(db,symbol,rows,time.time())
                audit["results"][symbol]={"bars":len(rows),"sessions":len({r[1] for r in rows}),
                                         "first":min(r[1] for r in rows),"last":max(r[1] for r in rows)}
            except Exception as e:
                audit["results"][symbol]={"error":type(e).__name__}
            print(json.dumps({symbol:audit["results"][symbol]}),flush=True)
            time.sleep(1)
    finally:
        db.close()
        atomic_json(path.with_suffix(".provenance.json"),audit)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    b=sub.add_parser("backfill")
    b.add_argument("--output",type=Path,required=True)
    b.add_argument("--symbols",nargs="+",default=SYMBOLS)
    t=sub.add_parser("train")
    t.add_argument("--db",type=Path,required=True)
    t.add_argument("--output",type=Path,required=True)
    t.add_argument("--development",action="store_true")
    a=p.parse_args()
    if a.command=="backfill":
        if any(not s.isalpha() or not s.isupper() or len(s)>8 for s in a.symbols):
            p.error("Use plain uppercase US equity symbols")
        backfill(a.output,a.symbols)
    else: train(a.db,a.output,a.development)


if __name__=="__main__":
    main()
