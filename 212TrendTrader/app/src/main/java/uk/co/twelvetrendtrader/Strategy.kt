package uk.co.twelvetrendtrader

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

data class Signal(
    val symbol: String,
    val score: Double,
    val price: Double,
    val target: Double,
    val stop: Double,
    val reasons: List<String>
)

data class StrategySettings(
    val profitTargetPct: Double = 4.0,
    val stopLossPct: Double = 2.0,
    val minScore: Double = 75.0,
    val maxPositionPct: Double = 10.0
)

object TrendStrategy {
    fun evaluate(symbol: String, candles: List<Candle>, s: StrategySettings): Signal? {
        if (candles.size < 60) return null
        val closes = candles.map { it.close }
        val price = closes.last()
        val sma20 = closes.takeLast(20).average()
        val sma50 = closes.takeLast(50).average()
        val rsi = rsi(closes.takeLast(15))
        val momentum20 = price / closes[closes.size - 21] - 1.0
        val recent = closes.takeLast(10)
        val direction = recent.zipWithNext().count { it.second > it.first }
        val score =
        25.0 * (if (price > sma20) 1 else 0) +
        25.0 * (if (sma20 > sma50) 1 else 0) +
            25.0 * (rsi.coerceIn(0.0, 100.0) / 100.0) +
            25.0 * ((momentum20.coerceIn(-0.10, 0.10) + 0.10) / 0.20)

        if (score < s.minScore || price <= 0.0) return null

        val reasons = mutableListOf<String>()
        if (price > sma20) reasons += "Price above 20-day average"
        if (sma20 > sma50) reasons += "20-day trend above 50-day trend"
        if (rsi in 50.0..70.0) reasons += "RSI in bullish zone"
        if (momentum20 > 0) reasons += "20-day momentum positive"
        if (direction >= 6) reasons += "Recent closes mostly rising"

        return Signal(
            symbol = symbol,
            score = score.coerceIn(0.0, 100.0),
            price = price,
            target = price * (1 + s.profitTargetPct / 100),
            stop = price * (1 - s.stopLossPct / 100),
            reasons = reasons
        )
    }

    private fun rsi(values: List<Double>): Double {
        if (values.size < 2) return 50.0
        var gain = 0.0
        var loss = 0.0
        for (i in 1 until values.size) {
            val d = values[i] - values[i - 1]
            if (d >= 0) gain += d else loss += abs(d)
        }
        if (loss == 0.0) return 100.0
        val rs = (gain / max(1, values.size - 1)) / (loss / max(1, values.size - 1))
        return 100.0 - (100.0 / (1.0 + rs))
    }
}
