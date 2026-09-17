from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta
import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="212 Trend Trader Backend")

PAPER_STATE_FILE = Path(__file__).resolve().parent / "paper_state.json"

def default_paper_state():
    return {
        "cash": 1000.0,
        "position": None,
        "events": ["Backend paper account ready."],
        "automation": {
            "enabled": True,
            "profit_target_pct": 4.0,
            "stop_loss_pct": 2.0,
            "min_score": 75.0,
            "scan_interval_seconds": 900,
            "check_interval_seconds": 60,
            "last_scan_at": None,
            "last_action": "Waiting for first automated scan",
        },
    }

def load_paper_state():
    try:
        if PAPER_STATE_FILE.exists():
            data = json.loads(PAPER_STATE_FILE.read_text())
            if isinstance(data, dict) and "cash" in data and "position" in data:
                defaults = default_paper_state()
                data.setdefault("events", [])
                data.setdefault("automation", defaults["automation"])
                for key, value in defaults["automation"].items():
                    data["automation"].setdefault(key, value)
                return data
    except (OSError, json.JSONDecodeError):
        pass

    return default_paper_state()

def save_paper_state():
    try:
        temp_file = PAPER_STATE_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(PAPER_STATE, indent=2))
        temp_file.replace(PAPER_STATE_FILE)
    except OSError as exc:
        print(f"PAPER STATE SAVE ERROR: {exc}")

PAPER_STATE = load_paper_state()
PAPER_AUTOMATION_TASK = None


def append_paper_event(message):
    PAPER_STATE["events"].append(message)
    PAPER_STATE["events"] = PAPER_STATE["events"][-100:]


@app.get("/paper/automation")
async def paper_automation_status():
    return {
        "automation": PAPER_STATE["automation"],
        "cash": PAPER_STATE["cash"],
        "position": PAPER_STATE["position"],
    }


@app.post("/paper/automation")
async def configure_paper_automation(
    enabled: bool = True,
    profit_target_pct: float = 4.0,
    stop_loss_pct: float = 2.0,
    min_score: float = 75.0,
    scan_interval_seconds: int = 900,
    check_interval_seconds: int = 60,
):
    if profit_target_pct <= 0 or stop_loss_pct <= 0:
        raise HTTPException(
            status_code=400,
            detail="Profit target and stop loss must be positive",
        )

    if not 0 <= min_score <= 100:
        raise HTTPException(
            status_code=400,
            detail="Minimum score must be between 0 and 100",
        )

    if scan_interval_seconds < 60 or check_interval_seconds < 30:
        raise HTTPException(
            status_code=400,
            detail="Scan interval must be at least 60 seconds and check interval at least 30 seconds",
        )

    automation = PAPER_STATE["automation"]
    automation.update({
        "enabled": enabled,
        "profit_target_pct": profit_target_pct,
        "stop_loss_pct": stop_loss_pct,
        "min_score": min_score,
        "scan_interval_seconds": scan_interval_seconds,
        "check_interval_seconds": check_interval_seconds,
    })
    automation["last_action"] = (
        "Automation enabled" if enabled else "Automation paused"
    )
    append_paper_event(automation["last_action"])
    save_paper_state()
    return await paper_automation_status()


@app.post("/paper/buy")
async def paper_buy(symbol: str, profit_target_pct: float = 4.0, stop_loss_pct: float = 2.0):
    if PAPER_STATE["position"] is not None:
        raise HTTPException(status_code=400, detail="A paper position is already open")

    quote = await market_quote(symbol)
    price = float(quote["price"])

    cash = float(PAPER_STATE["cash"])
    if cash <= 0:
        raise HTTPException(status_code=400, detail="No paper cash available")

    quantity = cash / price

    PAPER_STATE["position"] = {
        "symbol": symbol.upper(),
        "entry": price,
        "quantity": quantity,
        "target": price * (1.0 + profit_target_pct / 100.0),
        "stop": price * (1.0 - stop_loss_pct / 100.0),
        "profit_target_pct": profit_target_pct,
        "stop_loss_pct": stop_loss_pct
    }
    PAPER_STATE["cash"] = 0.0
    append_paper_event(
        f"PAPER BUY {symbol.upper()} qty {quantity:.4f} @ {price:.2f}"
    )
    save_paper_state()

    return PAPER_STATE


@app.post("/paper/sell")
async def paper_sell():
    position = PAPER_STATE["position"]

    if position is None:
        raise HTTPException(
            status_code=400,
            detail="No paper position is open"
        )

    quote = await market_quote(position["symbol"])
    current = float(quote["price"])
    value = position["quantity"] * current

    PAPER_STATE["cash"] = value
    append_paper_event(
        f"PAPER SELL {position['symbol']} qty {position['quantity']:.4f} @ {current:.2f}"
    )
    PAPER_STATE["position"] = None
    save_paper_state()

    return PAPER_STATE


@app.post("/paper/check")
async def paper_check():
    position = PAPER_STATE["position"]

    if position is None:
        return PAPER_STATE

    quote = await market_quote(position["symbol"])
    current = float(quote["price"])
    quote_source = quote.get("source", "unknown")

    position["current_price"] = current
    position["quote_source"] = quote_source
    position["latest_trading_day"] = quote.get("latest_trading_day", "")

    reason = None

    # Cached daily closes are useful for display, but must never trigger
    # an automatic target/stop exit because they may be stale.
    if quote_source == "live":
        position["monitoring_status"] = "LIVE - automatic exits enabled"

        if current >= position["target"]:
            reason = "TARGET HIT"
        elif current <= position["stop"]:
            reason = "STOP HIT"
    else:
        position["monitoring_status"] = (
            "CACHED PRICE - automatic exits paused"
        )

    if reason is not None:
        value = position["quantity"] * current
        PAPER_STATE["cash"] = value
        append_paper_event(
            f"AUTO PAPER SELL {position['symbol']} @ {current:.2f} {reason}"
        )
        PAPER_STATE["position"] = None

    save_paper_state()
    return PAPER_STATE


@app.get("/paper/status")
async def paper_status():
    return PAPER_STATE


@app.get("/health")
def health():
    return {
        "status": "ok",
        "live_trading_enabled": os.getenv(
            "LIVE_TRADING_ENABLED", "false"
        ).lower() == "true"
    }


@app.get("/market/candles")
async def market_candles(
    symbol: str,
    interval: str = "1day",
    outputsize: str = "compact"
):
    cache_key = f"{symbol.upper()}:{outputsize}"
    cached = MARKET_CACHE.get(cache_key)

    if cached:
        age = datetime.utcnow() - cached["time"]
        if age < timedelta(minutes=CACHE_MINUTES):
            return cached["data"]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol.upper()}_{outputsize}.json"

    if cache_file.exists():
        age_seconds = datetime.now().timestamp() - cache_file.stat().st_mtime
        if age_seconds < CACHE_MINUTES * 60:
            try:
                disk_data = json.loads(cache_file.read_text())
                MARKET_CACHE[cache_key] = {
                    "time": datetime.utcnow(),
                    "data": disk_data,
                }
                return disk_data
            except (OSError, json.JSONDecodeError):
                pass

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Market data API key is not configured"
        )

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": outputsize,
        "apikey": api_key,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)

        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Market data connection failed ({response.status_code})"
            )

        data = response.json()

    if "Error Message" in data:
        raise HTTPException(
            status_code=502,
            detail=data["Error Message"]
        )

    if "Note" in data:
        raise HTTPException(
            status_code=429,
            detail=data["Note"]
        )

    time_series = data.get("Time Series (Daily)")

    if not time_series:
        raise HTTPException(
            status_code=502,
            detail="Alpha Vantage returned no daily price data"
        )

    candles = []

    for date, values in sorted(time_series.items()):
        try:
            candles.append({
                "time": date,
                "open": float(values["1. open"]),
                "high": float(values["2. high"]),
                "low": float(values["3. low"]),
                "close": float(values["4. close"]),
                "volume": float(values["5. volume"])
            })
        except (KeyError, ValueError):
            continue

    if not candles:
        raise HTTPException(
            status_code=502,
            detail="No valid candles returned"
        )

    result = {
        "symbol": symbol.upper(),
        "count": len(candles),
        "candles": candles
    }

    MARKET_CACHE[cache_key] = {
        "time": datetime.utcnow(),
        "data": result
    }

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except OSError:
        pass

    return result



@app.get("/market/quote")
async def market_quote(symbol: str):
    symbol = symbol.upper()
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")

    if api_key:
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://www.alphavantage.co/query",
                    params=params
                )

            if response.status_code == 200:
                data = response.json()
                quote = data.get("Global Quote", {})
                price_raw = quote.get("05. price")

                if price_raw:
                    return {
                        "symbol": symbol,
                        "price": float(price_raw),
                        "latest_trading_day": quote.get(
                            "07. latest trading day", ""
                        ),
                        "source": "live"
                    }
        except (httpx.HTTPError, ValueError):
            pass

    # Fallback to the latest cached daily candle.
    cache_file = CACHE_DIR / f"{symbol}_compact.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            candles = cached.get("candles", [])

            if candles:
                latest = candles[-1]
                return {
                    "symbol": symbol,
                    "price": float(latest["close"]),
                    "latest_trading_day": latest.get("time", ""),
                    "source": "cached"
                }
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass

    raise HTTPException(
        status_code=502,
        detail="No live or cached quote available"
    )


TRADING212_POSITIONS_URL = "https://live.trading212.com/api/v0/equity/positions"

@app.get("/trading212/position-price")
async def trading212_position_price(symbol: str):
    symbol = symbol.upper()
    api_key = os.getenv("TRADING212_API_KEY")
    secret_key = os.getenv("TRADING212_SECRET_KEY")

    if not api_key or not secret_key:
        raise HTTPException(
            status_code=503,
            detail="Trading 212 credentials are not configured"
        )

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            TRADING212_POSITIONS_URL,
            auth=(api_key, secret_key)
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="Trading 212 positions request failed"
        )

    positions = response.json()

    for position in positions:
        instrument = position.get("instrument") or {}
        broker_ticker = str(instrument.get("ticker", "")).upper()

        # Trading 212 US tickers normally look like AAPL_US_EQ.
        market_symbol = broker_ticker.split("_", 1)[0]

        if symbol in (broker_ticker, market_symbol):
            current_price = position.get("currentPrice")

            if current_price is None:
                raise HTTPException(
                    status_code=502,
                    detail="Trading 212 position has no current price"
                )

            return {
                "symbol": market_symbol,
                "broker_ticker": broker_ticker,
                "price": float(current_price),
                "source": "trading212",
                "instrument_name": instrument.get("name", "")
            }

    raise HTTPException(
        status_code=404,
        detail=f"No open Trading 212 position found for {symbol}"
    )


TRADING212_ACCOUNT_SUMMARY_URL = "https://live.trading212.com/api/v0/equity/account/summary"

@app.get("/trading212/account-summary")
async def trading212_account_summary():
    api_key = os.getenv("TRADING212_API_KEY")
    secret_key = os.getenv("TRADING212_SECRET_KEY")

    if not api_key or not secret_key:
        raise HTTPException(
            status_code=503,
            detail="Trading 212 credentials are not configured"
        )

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            TRADING212_ACCOUNT_SUMMARY_URL,
            auth=(api_key, secret_key)
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="Trading 212 account summary request failed"
        )

    return response.json()


@app.get("/trading212/positions")
async def trading212_positions():
    api_key = os.getenv("TRADING212_API_KEY")
    secret_key = os.getenv("TRADING212_SECRET_KEY")

    if not api_key or not secret_key:
        raise HTTPException(
            status_code=503,
            detail="Trading 212 credentials are not configured"
        )

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            TRADING212_POSITIONS_URL,
            auth=(api_key, secret_key)
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="Trading 212 positions request failed"
        )

    result = []

    for position in response.json():
        instrument = position.get("instrument") or {}
        broker_ticker = str(instrument.get("ticker", "")).upper()

        wallet_impact = position.get("walletImpact") or {}

        result.append({
            "symbol": broker_ticker.split("_", 1)[0],
            "broker_ticker": broker_ticker,
            "name": instrument.get("name", ""),
            "current_price": position.get("currentPrice"),
            "average_price": position.get("averagePricePaid"),
            "quantity": position.get("quantity"),
            "currency": wallet_impact.get("currency", ""),
            "position_value": wallet_impact.get("currentValue"),
            "position_cost": wallet_impact.get("totalCost"),
            "unrealized_profit_loss": wallet_impact.get("unrealizedProfitLoss")
        })

    return {
        "count": len(result),
        "positions": result
    }


from strategy import analyse

TRADING212_INSTRUMENTS_URL = "https://live.trading212.com/api/v0/equity/metadata/instruments"

async def trading212_instruments():
    cache_file = Path(__file__).resolve().parent / "trading212_instruments.json"

    if cache_file.exists():
        age_seconds = datetime.now().timestamp() - cache_file.stat().st_mtime
        if age_seconds < 86400:
            try:
                return json.loads(cache_file.read_text())
            except (OSError, json.JSONDecodeError):
                pass

    api_key = os.getenv("TRADING212_API_KEY")
    secret_key = os.getenv("TRADING212_SECRET_KEY")

    if not api_key or not secret_key:
        raise HTTPException(
            status_code=503,
            detail="Trading 212 credentials are not configured"
        )

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            TRADING212_INSTRUMENTS_URL,
            auth=(api_key, secret_key)
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="Trading 212 instrument request failed"
        )

    data = response.json()

    try:
        cache_file.write_text(json.dumps(data))
    except OSError:
        pass

    return data


MARKET_CACHE = {}
CACHE_MINUTES = 1440
CACHE_DIR = Path(__file__).resolve().parent / "market_cache"
CACHE_WARM_DAILY_LIMIT = 20
CACHE_WARM_INTERVAL_SECONDS = 240
CACHE_WARM_STATE_FILE = CACHE_DIR / "warm_state.json"
ALPHA_FAILED_FILE = CACHE_DIR / "alpha_failed.json"
ALPHA_FAILED_RETRY_HOURS = 24


async def trading212_us_stock_universe():
    instruments = await trading212_instruments()

    return [
        item.get("shortName")
        for item in instruments
        if item.get("type") == "STOCK"
        and str(item.get("ticker", "")).endswith("_US_EQ")
        and item.get("shortName")
    ]


CACHE_WARM_TASK = None


async def paper_automation_loop():
    await asyncio.sleep(10)

    while True:
        automation = PAPER_STATE["automation"]
        sleep_seconds = max(
            30,
            int(automation.get("check_interval_seconds", 60)),
        )

        try:
            if not automation.get("enabled", False):
                await asyncio.sleep(sleep_seconds)
                continue

            if PAPER_STATE["position"] is not None:
                await paper_check()
                automation["last_action"] = "Open position checked"
                save_paper_state()
            else:
                now = datetime.utcnow()
                last_scan_at = automation.get("last_scan_at")
                scan_due = True

                if last_scan_at:
                    try:
                        last_scan = datetime.fromisoformat(last_scan_at)
                        scan_due = (
                            now - last_scan
                        ).total_seconds() >= int(
                            automation.get("scan_interval_seconds", 900)
                        )
                    except (TypeError, ValueError):
                        scan_due = True

                if scan_due:
                    cached_symbols = sorted({
                        cache_file.name.replace("_compact.json", "")
                        for cache_file in CACHE_DIR.glob("*_compact.json")
                    })
                    automation["last_scan_at"] = now.isoformat()

                    if not cached_symbols:
                        automation["last_action"] = (
                            "Automated scan waiting for cached market data"
                        )
                    else:
                        scan = await scanner_scan(
                            symbols=",".join(cached_symbols),
                            min_score=float(
                                automation.get("min_score", 75.0)
                            ),
                            profit_target_pct=float(
                                automation.get("profit_target_pct", 4.0)
                            ),
                            stop_loss_pct=float(
                                automation.get("stop_loss_pct", 2.0)
                            ),
                        )
                        qualified = scan.get("qualified", [])

                        if qualified:
                            best = qualified[0]
                            symbol = best["symbol"]
                            score = best["analysis"]["score"]

                            await paper_buy(
                                symbol=symbol,
                                profit_target_pct=float(
                                    automation.get(
                                        "profit_target_pct", 4.0
                                    )
                                ),
                                stop_loss_pct=float(
                                    automation.get("stop_loss_pct", 2.0)
                                ),
                            )
                            automation["last_action"] = (
                                f"Bought {symbol} automatically "
                                f"from score {score:.2f}"
                            )
                            append_paper_event(
                                automation["last_action"]
                            )
                        else:
                            automation["last_action"] = (
                                "Automated scan found no qualifying signal"
                            )

                    save_paper_state()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            automation["last_action"] = (
                f"Automation error: {str(exc)[:160]}"
            )
            append_paper_event(automation["last_action"])
            save_paper_state()
            print(f"PAPER AUTOMATION ERROR: {exc}")

        await asyncio.sleep(sleep_seconds)


def load_alpha_failed_symbols():
    try:
        if not ALPHA_FAILED_FILE.exists():
            return {}

        data = json.loads(ALPHA_FAILED_FILE.read_text())

        if isinstance(data, dict):
            return data

        if isinstance(data, list):
            now = datetime.utcnow().isoformat()
            return {symbol: now for symbol in data}

    except (OSError, json.JSONDecodeError):
        pass

    return {}


def save_alpha_failed_symbols(data):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ALPHA_FAILED_FILE.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def alpha_failure_is_recent(symbol):
    failed = load_alpha_failed_symbols()
    timestamp = failed.get(symbol.upper())

    if not timestamp:
        return False

    try:
        failed_at = datetime.fromisoformat(timestamp)
        return (
            datetime.utcnow() - failed_at
            < timedelta(hours=ALPHA_FAILED_RETRY_HOURS)
        )
    except (TypeError, ValueError):
        return False


def load_cache_warm_state():
    today = datetime.utcnow().date().isoformat()

    state = {
        "date": today,
        "requests_today": 0,
        "cursor": 0,
    }

    try:
        if CACHE_WARM_STATE_FILE.exists():
            loaded = json.loads(CACHE_WARM_STATE_FILE.read_text())

            if isinstance(loaded, dict):
                state.update(loaded)

    except (OSError, json.JSONDecodeError):
        pass

    if state.get("date") != today:
        state["date"] = today
        state["requests_today"] = 0

    return state


def save_cache_warm_state(state):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_WARM_STATE_FILE.write_text(
            json.dumps(state, indent=2)
        )
    except OSError:
        pass


async def cache_warm_loop():
    # Give the API time to finish starting before background work begins.
    await asyncio.sleep(10)

    while True:
        try:
            state = load_cache_warm_state()

            if state["requests_today"] >= CACHE_WARM_DAILY_LIMIT:
                await asyncio.sleep(CACHE_WARM_INTERVAL_SECONDS)
                continue

            universe = await trading212_us_stock_universe()

            if not universe:
                await asyncio.sleep(CACHE_WARM_INTERVAL_SECONDS)
                continue

            start = int(state.get("cursor", 0)) % len(universe)
            selected = None
            selected_index = None

            for offset in range(len(universe)):
                index = (start + offset) % len(universe)
                symbol = universe[index].upper()

                cache_file = CACHE_DIR / f"{symbol}_compact.json"

                cache_is_fresh = (
                    cache_file.exists()
                    and (
                        datetime.now().timestamp()
                        - cache_file.stat().st_mtime
                    ) < CACHE_MINUTES * 60
                )

                if cache_is_fresh:
                    continue

                if alpha_failure_is_recent(symbol):
                    continue

                selected = symbol
                selected_index = index
                break

            if selected is None:
                await asyncio.sleep(CACHE_WARM_INTERVAL_SECONDS)
                continue

            state["cursor"] = (selected_index + 1) % len(universe)

            # Count the request even when Alpha Vantage rejects the symbol,
            # so the daily API allowance remains protected.
            state["requests_today"] += 1
            save_cache_warm_state(state)

            try:
                await market_candles(
                    symbol=selected,
                    outputsize="compact"
                )

                print(
                    f"CACHE WARM: {selected} cached "
                    f"({state['requests_today']}/{CACHE_WARM_DAILY_LIMIT})"
                )

            except HTTPException as exc:
                detail = str(exc.detail)

                if "no daily price data" in detail.lower():
                    failed = load_alpha_failed_symbols()
                    failed[selected] = datetime.utcnow().isoformat()
                    save_alpha_failed_symbols(failed)

                print(
                    f"CACHE WARM: {selected} skipped - {detail}"
                )

        except Exception as exc:
            print(f"CACHE WARM ERROR: {exc}")

        await asyncio.sleep(CACHE_WARM_INTERVAL_SECONDS)


@app.on_event("startup")
async def start_background_tasks():
    global CACHE_WARM_TASK, PAPER_AUTOMATION_TASK

    if CACHE_WARM_TASK is None or CACHE_WARM_TASK.done():
        CACHE_WARM_TASK = asyncio.create_task(cache_warm_loop())

    if PAPER_AUTOMATION_TASK is None or PAPER_AUTOMATION_TASK.done():
        PAPER_AUTOMATION_TASK = asyncio.create_task(
            paper_automation_loop()
        )


@app.on_event("shutdown")
async def stop_background_tasks():
    global CACHE_WARM_TASK, PAPER_AUTOMATION_TASK

    for task in (CACHE_WARM_TASK, PAPER_AUTOMATION_TASK):
        if task is not None:
            task.cancel()

    CACHE_WARM_TASK = None
    PAPER_AUTOMATION_TASK = None


@app.get("/cache/warm-status")
async def cache_warm_status():
    state = load_cache_warm_state()
    failed = load_alpha_failed_symbols()

    cached_files = list(CACHE_DIR.glob("*_compact.json"))

    return {
        "cached_symbols": len(cached_files),
        "requests_today": state.get("requests_today", 0),
        "daily_limit": CACHE_WARM_DAILY_LIMIT,
        "cursor": state.get("cursor", 0),
        "failed_symbols": len(failed),
        "interval_seconds": CACHE_WARM_INTERVAL_SECONDS,
    }


@app.get("/universe/stocks")
async def universe_stocks():
    instruments = await trading212_instruments()

    stocks = [
        item for item in instruments
        if item.get("type") == "STOCK"
    ]

    return {
        "count": len(stocks),
        "stocks": stocks
    }


@app.get("/strategy/analyse")
async def strategy_analyse(symbol: str):
    candles_response = await market_candles(
        symbol=symbol,
        outputsize="compact"
    )

    candles = candles_response["candles"]
    result = analyse(candles)

    return {
        "symbol": symbol.upper(),
        "analysis": result
    }

@app.get("/scanner/scan")
async def scanner_scan(symbols: str = "",
                       min_score: float = 75.0,
                       profit_target_pct: float = 4.0,
                       stop_loss_pct: float = 2.0):
    results = []
    max_uncached_fetches = 5
    uncached_fetches = 0

    if symbols.strip():
        symbol_list = [
            s.strip().upper()
            for s in symbols.split(",")
            if s.strip()
        ]
    else:
        universe = await trading212_us_stock_universe()

        cached = []
        uncached = []

        for symbol in universe:
            cache_file = CACHE_DIR / f"{symbol.upper()}_compact.json"
            cache_is_fresh = (
                cache_file.exists()
                and (datetime.now().timestamp() - cache_file.stat().st_mtime)
                < CACHE_MINUTES * 60
            )

            if cache_is_fresh:
                cached.append(symbol)
            else:
                uncached.append(symbol)

        symbol_list = cached + uncached[:max_uncached_fetches]

    for symbol in symbol_list:
        try:
            cache_file = CACHE_DIR / f"{symbol.upper()}_compact.json"
            cache_is_fresh = (
                cache_file.exists()
                and (datetime.now().timestamp() - cache_file.stat().st_mtime) < CACHE_MINUTES * 60
            )

            if not cache_is_fresh:
                if uncached_fetches >= max_uncached_fetches:
                    results.append({
                        "symbol": symbol,
                        "error": "Historical cache not ready yet"
                    })
                    continue
                uncached_fetches += 1

            candles_response = await market_candles(
                symbol=symbol,
                outputsize="compact"
            )

            analysis = analyse(candles_response["candles"], min_score=min_score, profit_target_pct=profit_target_pct, stop_loss_pct=stop_loss_pct)

            results.append({
                "symbol": symbol,
                "analysis": analysis
            })

        except HTTPException as exc:
            if "no daily price data" in str(exc.detail).lower():
                try:
                    ALPHA_FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)

                    failed_symbols = {}
                    if ALPHA_FAILED_FILE.exists():
                        loaded_failed_symbols = json.loads(ALPHA_FAILED_FILE.read_text())
                        if isinstance(loaded_failed_symbols, dict):
                            failed_symbols = loaded_failed_symbols
                        elif isinstance(loaded_failed_symbols, list):
                            failed_symbols = {
                                item: datetime.utcnow().isoformat()
                                for item in loaded_failed_symbols
                            }

                    failed_symbols[symbol] = datetime.utcnow().isoformat()
                    ALPHA_FAILED_FILE.write_text(
                        json.dumps(failed_symbols, indent=2)
                    )
                except (OSError, json.JSONDecodeError):
                    pass

            results.append({
                "symbol": symbol,
                "error": exc.detail
            })

    results.sort(
        key=lambda x: x.get("analysis", {}).get("score", -1),
        reverse=True
    )

    qualified = [
        r for r in results
        if r.get("analysis", {}).get("qualified") is True
    ]

    unavailable = [
        r for r in results
        if "error" in r
    ]

    return {
        "count": len(results),
        "valid_count": len(results) - len(unavailable),
        "qualified_count": len(qualified),
        "qualified": qualified,
        "unavailable": unavailable,
        "results": results
    }
