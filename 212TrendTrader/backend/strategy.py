def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values, period=14):
    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def analyse(candles, min_score: float = 75.0, profit_target_pct: float = 4.0, stop_loss_pct: float = 2.0):
    closes = [float(c["close"]) for c in candles]

    if len(closes) < 50:
        return {
            "qualified": False,
            "score": 0,
            "reason": "At least 50 candles required"
        }

    price = closes[-1]
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    current_rsi = rsi(closes, 14)

    momentum20 = (price / closes[-21]) - 1.0

    score = (
        25.0 * (1 if price > sma20 else 0)
        + 25.0 * (1 if sma20 > sma50 else 0)
        + 25.0 * (max(0.0, min(100.0, current_rsi)) / 100.0)
        + 25.0 * (
            (max(-0.10, min(0.10, momentum20)) + 0.10) / 0.20
        )
    )

    score = round(score, 2)

    return {
        "price": round(price, 4),
        "sma20": round(sma20, 4),
        "sma50": round(sma50, 4),
        "rsi14": round(current_rsi, 2),
        "momentum20": round(momentum20 * 100, 2),
        "score": score,
        "qualified": score >= min_score,
        "profit_target_pct": profit_target_pct,
        "stop_loss_pct": stop_loss_pct
    }
