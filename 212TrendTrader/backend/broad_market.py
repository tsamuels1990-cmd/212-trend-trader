"""Bounded Yahoo daily-history access for the Trading 212 paper universe."""
import asyncio
import json
import math
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
import httpx
from fastapi import HTTPException

HEADERS = {"User-Agent": "Mozilla/5.0 212TrendTrader/1.0"}
HISTORY_BATCH_SIZE = 20
HISTORY_DAILY_LIMIT = 7200
HISTORY_INTERVAL_SECONDS = 120
HISTORY_CONCURRENCY = 3

def parse_history(chart, now=None):
    now = time.time() if now is None else now
    meta = chart.get("meta") or {}
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = regular.get("start", 0), regular.get("end", 0)
    quotes = (chart.get("indicators") or {}).get("quote") or []
    if not quotes:
        raise ValueError("Missing daily OHLC data")
    values = quotes[0]
    candles = []
    for index, stamp in enumerate(chart.get("timestamp") or []):
        # The strategy uses completed daily sessions, never today's partial bar.
        if start <= stamp < end and now < end:
            continue
        try:
            row = {key: float(values[key][index]) for key in ("open", "high", "low", "close", "volume")}
            if not all(math.isfinite(v) for v in row.values()):
                continue
            if min(row[k] for k in ("open", "high", "low", "close")) <= 0 or row["volume"] < 0:
                continue
            row["time"] = datetime.fromtimestamp(stamp, tz).date().isoformat()
            candles.append(row)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    if len(candles) < 51:
        raise ValueError("At least 51 completed daily bars required")
    return candles[-100:]

async def yahoo_history(symbol):
    async with httpx.AsyncClient(timeout=12, headers=HEADERS) as client:
        response = await client.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe=""),
            params={"interval": "1d", "range": "6mo"})
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Daily history unavailable ({response.status_code})")
    try:
        results = response.json().get("chart", {}).get("result") or []
        if not results:
            raise ValueError("No daily history for symbol")
        candles = parse_history(results[0])
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"symbol": symbol.upper(), "count": len(candles), "candles": candles,
            "provider": "yahoo_daily", "fetched_at": datetime.now(timezone.utc).isoformat()}

def quote_entry_reason(quote, analysis):
    if quote.get("source") != "live":
        age = quote.get("quote_age_seconds")
        suffix = f" (quote age: {int(age)//60} minutes)" if isinstance(age, (int,float)) else ""
        return "Fresh live quote required for a paper entry" + suffix
    try:
        price = float(quote["price"])
        average = float(analysis.get("sma20", price))
        if not math.isfinite(price) or price <= 0:
            return "Invalid live quote"
        if price <= average:
            return "Live price no longer supports the bullish daily signal"
    except (KeyError, ValueError, TypeError):
        return "Invalid live quote"
    return None

async def warm_history_batch(universe, cache_dir, state, save_state):
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("date") != today:
        state.update(date=today, requests_today=0)
    universe = list(dict.fromkeys(universe))
    if not universe or state.get("requests_today", 0) >= HISTORY_DAILY_LIMIT:
        return
    start = int(state.get("cursor", 0)) % len(universe)
    failures = state.setdefault("failures", {})
    selected = []
    for offset in range(len(universe)):
        index = (start + offset) % len(universe)
        symbol = universe[index]
        path = cache_dir / (symbol + "_compact.json")
        if path.exists() and time.time() - path.stat().st_mtime < 86400:
            continue
        if time.time() - failures.get(symbol, 0) < 86400:
            continue
        selected.append(symbol)
        state["cursor"] = (index + 1) % len(universe)
        if len(selected) >= min(HISTORY_BATCH_SIZE, HISTORY_DAILY_LIMIT-state.get("requests_today",0)):
            break
    state["requests_today"] = state.get("requests_today", 0) + len(selected)
    save_state(state)
    semaphore = asyncio.Semaphore(HISTORY_CONCURRENCY)
    async def fetch(symbol):
        async with semaphore:
            try:
                result = await yahoo_history(symbol)
                path = cache_dir / (symbol + "_compact.json")
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(result))
                tmp.replace(path)
                failures.pop(symbol, None)
                return True
            except (HTTPException, httpx.HTTPError, OSError, ValueError):
                failures[symbol] = time.time()
                return False
    outcomes = await asyncio.gather(*(fetch(s) for s in selected))
    state["last_batch_cached"] = sum(outcomes)
    state["last_batch_attempted"] = len(selected)
    state["last_batch_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"HISTORY WARM: {sum(outcomes)}/{len(selected)} cached; requests today {state['requests_today']}")
