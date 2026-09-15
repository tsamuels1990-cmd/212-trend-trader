import math


STRATEGY_VERSION = "2.0"


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values, period=14):
    if len(values) <= period:
        return None

    gains = []
    losses = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[index]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index]) / period

    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def atr(candles, period=14):
    if len(candles) <= period:
        return None
    ranges = []
    for index in range(1, len(candles)):
        high = float(candles[index]["high"])
        low = float(candles[index]["low"])
        previous_close = float(candles[index - 1]["close"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(ranges[-period:]) / period


def _clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(maximum, value))


def _rsi_quality(value):
    # Prefer healthy momentum, while penalising weak and overextended entries.
    if 52.0 <= value <= 68.0:
        return 1.0
    if 45.0 <= value < 52.0:
        return (value - 45.0) / 7.0
    if 68.0 < value <= 75.0:
        return (75.0 - value) / 7.0
    return 0.0


def analyse(candles, min_score: float = 75.0,
            profit_target_pct: float = 4.0, stop_loss_pct: float = 2.0):
    if profit_target_pct <= 0 or stop_loss_pct <= 0:
        return {"qualified": False, "score": 0, "reason": "Target and stop must be positive"}
    if len(candles) < 51:
        return {"qualified": False, "score": 0, "reason": "At least 51 candles required"}

    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("volume", 0.0)) for c in candles]
    price = closes[-1]
    sma20_value = sma(closes, 20)
    sma50_value = sma(closes, 50)
    rsi_value = rsi(closes, 14)
    atr_value = atr(candles, 14)
    atr_pct = (atr_value / price) * 100.0 if price > 0 else 0.0
    momentum20_pct = ((price / closes[-21]) - 1.0) * 100.0
    average_volume20 = sum(volumes[-21:-1]) / 20.0
    volume_ratio = volumes[-1] / average_volume20 if average_volume20 > 0 else 1.0

    trend = 1.0 if price > sma20_value > sma50_value else (
        0.55 if price > sma20_value and sma20_value <= sma50_value else 0.0
    )
    momentum = _clamp((momentum20_pct + 2.0) / 10.0)
    momentum *= _clamp((12.0 - momentum20_pct) / 6.0) if momentum20_pct > 6.0 else 1.0
    momentum = _clamp(momentum)
    rsi_score = _rsi_quality(rsi_value)
    volume = _clamp((volume_ratio - 0.7) / 0.8)

    # Estimate whether the requested target is plausible within roughly 10 sessions.
    expected_ten_day_move_pct = atr_pct * math.sqrt(10.0)
    target_feasibility = _clamp(expected_ten_day_move_pct / profit_target_pct)
    stop_noise_ratio = stop_loss_pct / atr_pct if atr_pct > 0 else 0.0
    stop_quality = _clamp((stop_noise_ratio - 0.75) / 1.25)

    components = {
        "trend": 25.0 * trend,
        "momentum": 20.0 * momentum,
        "rsi": 15.0 * rsi_score,
        "volume": 10.0 * volume,
        "target_feasibility": 20.0 * target_feasibility,
        "stop_quality": 10.0 * stop_quality,
    }
    score = round(sum(components.values()), 2)

    blockers = []
    if not price > sma20_value > sma50_value:
        blockers.append("Price trend is not fully bullish")
    if rsi_value >= 75.0:
        blockers.append("RSI is overextended")
    if target_feasibility < 0.65:
        blockers.append(f"A {profit_target_pct:.1f}% target is large relative to recent volatility")
    if stop_noise_ratio < 0.75:
        blockers.append(f"A {stop_loss_pct:.1f}% stop is tight relative to normal price movement")

    qualified = score >= min_score and not blockers
    reason = (
        "Qualified: bullish trend with target and stop supported by recent movement"
        if qualified else "; ".join(blockers) or f"Score {score:.2f} is below minimum {min_score:.2f}"
    )

    return {
        "strategy_version": STRATEGY_VERSION,
        "price": round(price, 4),
        "sma20": round(sma20_value, 4),
        "sma50": round(sma50_value, 4),
        "rsi14": round(rsi_value, 2),
        "momentum20": round(momentum20_pct, 2),
        "atr14": round(atr_value, 4),
        "atr_pct": round(atr_pct, 2),
        "volume_ratio": round(volume_ratio, 2),
        "expected_ten_day_move_pct": round(expected_ten_day_move_pct, 2),
        "target_feasibility": round(target_feasibility, 3),
        "stop_noise_ratio": round(stop_noise_ratio, 2),
        "components": {key: round(value, 2) for key, value in components.items()},
        "score": score,
        "qualified": qualified,
        "reason": reason,
        "blockers": blockers,
        "profit_target_pct": profit_target_pct,
        "stop_loss_pct": stop_loss_pct,
    }
