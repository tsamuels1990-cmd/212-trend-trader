package uk.co.twelvetrendtrader

import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

data class T212Position(
    val ticker: String,
    val quantity: Double,
    val averagePrice: Double,
    val currentPrice: Double,
    val pnl: Double
)

class Trading212Api {
    private val http = OkHttpClient()
    private var base = "https://demo.trading212.com/api/v0"

    fun setMode(live: Boolean) {
        base = if (live) "https://live.trading212.com/api/v0"
        else "https://demo.trading212.com/api/v0"
    }

    private fun auth(key: String, secret: String): String {
        val raw = "$key:$secret".toByteArray(Charsets.UTF_8)
        return "Basic " + Base64.encodeToString(raw, Base64.NO_WRAP)
    }

    private suspend fun request(path: String, key: String, secret: String): String =
        withContext(Dispatchers.IO) {
            val req = Request.Builder()
                .url("$base$path")
                .header("Authorization", auth(key, secret))
                .get().build()
            http.newCall(req).execute().use { r ->
                if (!r.isSuccessful) error("Trading 212 HTTP ${r.code}: ${r.body?.string() ?: ""}")
                r.body?.string() ?: ""
            }
        }

    suspend fun accountSummary(key: String, secret: String): JSONObject =
        JSONObject(request("/equity/account/summary", key, secret))

    suspend fun positions(key: String, secret: String): List<T212Position> {
        val arr = JSONArray(request("/equity/positions", key, secret))
        return buildList {
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                add(
                    T212Position(
                        ticker = o.optString("ticker"),
                        quantity = o.optDouble("quantity"),
                        averagePrice = o.optDouble("averagePrice"),
                        currentPrice = o.optDouble("currentPrice"),
                        pnl = o.optDouble("ppl")
                    )
                )
            }
        }
    }

    suspend fun instruments(key: String, secret: String): List<String> {
        val arr = JSONArray(request("/equity/metadata/instruments", key, secret))
        return buildList {
            for (i in 0 until arr.length()) add(arr.getJSONObject(i).optString("ticker"))
        }
    }

    suspend fun marketOrder(
        key: String, secret: String, ticker: String, quantity: Double, extendedHours: Boolean = false
    ): JSONObject = withContext(Dispatchers.IO) {
        val json = JSONObject()
            .put("ticker", ticker)
            .put("quantity", quantity)
            .put("extendedHours", extendedHours)
        val req = Request.Builder()
            .url("$base/equity/orders/market")
            .header("Authorization", auth(key, secret))
            .header("Content-Type", "application/json")
            .post(json.toString().toRequestBody())
            .build()
        http.newCall(req).execute().use { r ->
            if (!r.isSuccessful) error("Trading 212 HTTP ${r.code}: ${r.body?.string() ?: ""}")
            JSONObject(r.body?.string() ?: "{}")
        }
    }
}
