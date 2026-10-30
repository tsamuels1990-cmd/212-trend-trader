"""Isolated research archive. No broker imports, orders, or paper account writes."""
import argparse
import fcntl
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA")
NY = ZoneInfo("America/New_York")


def clean_bars(chart, now):
    if chart["meta"].get("currency") != "USD":
        raise ValueError("USD data required")
    q = chart["indicators"]["quote"][0]
    rows = {}
    for i, stamp in enumerate(chart.get("timestamp", [])):
        try:
            stamp = int(stamp)
            local = datetime.fromtimestamp(stamp, NY)
            minute = local.hour * 60 + local.minute
            if local.weekday() >= 5 or not 570 <= minute < 960:
                continue
            if stamp % 300 or stamp + 300 > now:
                continue
            o, h, l, c, v = [float(q[k][i]) for k in ("open", "high", "low", "close", "volume")]
            if not all(math.isfinite(x) for x in (o, h, l, c, v)):
                continue
            if min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
                continue
            rows[stamp] = (stamp, local.date().isoformat(), o, h, l, c, v)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            continue
    return list(rows.values())


def connect(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT, stamp INTEGER, session TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            first_seen REAL, last_seen REAL, revisions INTEGER DEFAULT 0,
            PRIMARY KEY(symbol, stamp));
        CREATE TABLE IF NOT EXISTS fetches (
            fetched_at REAL, symbol TEXT, status TEXT, rows INTEGER, message TEXT);
        CREATE TABLE IF NOT EXISTS revisions (
            symbol TEXT, stamp INTEGER, observed_at REAL, previous_values TEXT);
    """)
    return db


def save(db, symbol, rows, now):
    with db:
        for row in rows:
            old = db.execute("SELECT open,high,low,close,volume FROM bars WHERE symbol=? AND stamp=?",
                             (symbol, row[0])).fetchone()
            if old is None:
                db.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                           (symbol, *row, now, now))
            elif tuple(row[2:]) != old:
                db.execute("INSERT INTO revisions VALUES (?,?,?,?)",
                           (symbol, row[0], now, json.dumps(old)))
                db.execute("""UPDATE bars SET open=?,high=?,low=?,close=?,volume=?,
                              last_seen=?,revisions=revisions+1 WHERE symbol=? AND stamp=?""",
                           (*row[2:], now, symbol, row[0]))
            else:
                db.execute("UPDATE bars SET last_seen=? WHERE symbol=? AND stamp=?",
                           (now, symbol, row[0]))
        db.execute("INSERT INTO fetches VALUES (?,?,?,?,?)", (now, symbol, "ok", len(rows), ""))


def fetch(symbol, history):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + symbol
           + "?interval=5m&includePrePost=false&range=" + history)
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 212TrendTrader/1.0"}), timeout=12) as r:
        payload = json.load(r)
    return payload["chart"]["result"][0]


def summary(db):
    return [dict(zip(("symbol", "bars", "sessions", "first", "last"), r))
            for r in db.execute("""SELECT symbol,COUNT(*),COUNT(DISTINCT session),
                                   MIN(session),MAX(session) FROM bars GROUP BY symbol""")]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--loop", action="store_true")
    p.add_argument("--summary", action="store_true")
    a = p.parse_args()
    a.db.parent.mkdir(parents=True, exist_ok=True)
    db = connect(a.db)
    if a.summary:
        print(json.dumps(summary(db)))
        return
    with a.db.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        first = True
        while True:
            local = datetime.now(NY)
            # Source supplies regular-session data; this clock is only a polling
            # budget, not an exchange calendar or a tradability check.
            active = local.weekday() < 5 and 570 <= local.hour * 60 + local.minute <= 965
            if first or active:
                for symbol in SYMBOLS:
                    now = time.time()
                    try:
                        known = db.execute("SELECT 1 FROM bars WHERE symbol=? LIMIT 1", (symbol,)).fetchone()
                        chart = fetch(symbol, "5d" if known else "1mo")
                        rows = clean_bars(chart, now)
                        save(db, symbol, rows, now)
                        print(json.dumps({"at": now, "symbol": symbol, "bars_received": len(rows)}), flush=True)
                    except Exception as e:
                        with db:
                            db.execute("INSERT INTO fetches VALUES (?,?,?,?,?)",
                                       (now, symbol, "error", 0, type(e).__name__))
                        print(json.dumps({"at": now, "symbol": symbol, "error": type(e).__name__}), flush=True)
                    time.sleep(1)
                print(json.dumps({"summary": summary(db)}), flush=True)
            first = False
            if not a.loop:
                break
            time.sleep(300)


if __name__ == "__main__":
    main()
