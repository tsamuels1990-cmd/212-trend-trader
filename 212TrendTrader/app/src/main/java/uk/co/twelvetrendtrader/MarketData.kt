package uk.co.twelvetrendtrader

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray

data class Candle(
    val time: Long,
    val open: Double,
    val high: Double,
    val low: Double,
    val close: Double,
    val volume: Double
)

interface MarketDataProvider {
    suspend fun candles(symbol: String): List<Candle>
}

/**
 * Public Stooq CSV provider. It is intended as a simple first-version research
 * feed; quotes may be delayed and availability varies by symbol/exchange.
 * The strategy must not be interpreted as guaranteed real-time data.
 */
class StooqProvider : MarketDataProvider {
    private val http = OkHttpClient()

    override suspend fun candles(symbol: String): List<Candle> = withContext(Dispatchers.IO) {
        val s = symbol.substringBefore("_").lowercase()
        val url = "https://stooq.com/q/d/l/?s=$s&i=d"
        val req = Request.Builder().url(url).build()
        http.newCall(req).execute().use { response ->
            if (!response.isSuccessful) error("Market data HTTP ${response.code}")
            val text = response.body?.string().orEmpty()
            text.lines().drop(1).mapNotNull { line ->
                val p = line.split(",")
                if (p.size < 6) null else runCatching {
                    Candle(
                        time = p[0].hashCode().toLong(),
                        open = p[1].toDouble(),
                        high = p[2].toDouble(),
                        low = p[3].toDouble(),
                        close = p[4].toDouble(),
                        volume = p[5].toDoubleOrNull() ?: 0.0
                    )
                }.getOrNull()
            }
        }
    }
}
