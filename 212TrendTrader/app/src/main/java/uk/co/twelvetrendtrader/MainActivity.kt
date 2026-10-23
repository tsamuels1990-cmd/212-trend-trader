@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
package uk.co.twelvetrendtrader


import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
import java.util.Locale
import java.text.SimpleDateFormat
import java.util.Date
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext


private val backendClient = OkHttpClient.Builder()
    .addInterceptor { chain ->
        val authenticatedRequest = chain.request().newBuilder()
            .header("X-API-Key", BuildConfig.BACKEND_API_TOKEN)
            .build()
        chain.proceed(authenticatedRequest)
    }
    .build()


private val TraderDarkColors = darkColorScheme(
    primary = Color(0xFFB5A1F4), onPrimary = Color(0xFF261F3C),
    background = Color(0xFF0D1117), onBackground = Color(0xFFE6EDF3),
    surface = Color(0xFF161B22), onSurface = Color(0xFFE6EDF3),
    surfaceVariant = Color(0xFF222A35), onSurfaceVariant = Color(0xFFBCC6D3),
    outline = Color(0xFF667487), error = Color(0xFFFF8585)
)

private fun displayBackendTime(raw: String): String = runCatching {
    java.time.LocalDateTime.parse(raw).atOffset(java.time.ZoneOffset.UTC)
        .atZoneSameInstant(java.time.ZoneId.systemDefault())
        .format(java.time.format.DateTimeFormatter.ofPattern("dd MMM HH:mm:ss"))
}.getOrDefault("Not yet recorded")

private fun quoteAgeLabel(timestamp: Long?): String {
    if (timestamp == null) return "Quote age unavailable"
    val age = (System.currentTimeMillis() / 1000 - timestamp).coerceAtLeast(0)
    return if (age < 60) "${age}s old" else "${age / 60}m ${age % 60}s old"
}

data class SimPosition(
    val ticker: String,
    val entry: Double,
    val quantity: Double,
    var price: Double,
    val target: Double,
    val stop: Double,
    val distributionPerShare: Double = 0.0
)


data class LivePosition(
    val symbol: String,
    val brokerTicker: String,
    val name: String,
    val currentPrice: Double,
    val averagePrice: Double,
    val quantity: Double,
    val currency: String,
    val positionValue: Double,
    val positionCost: Double,
    val unrealizedProfitLoss: Double
)

data class LiveAccountSummary(
    val currency: String,
    val cash: Double,
    val totalValue: Double,
    val investmentsValue: Double,
    val investmentsCost: Double,
    val unrealizedProfitLoss: Double
)


private data class BackendScanResult(val signals: List<Signal>, val analysed: Int, val cached: Int, val universe: Int)

private fun backendFailure(label: String, code: Int, body: String?): Exception {
    val detail = runCatching { JSONObject(body ?: "{}").optString("detail", "") }
        .getOrDefault("").take(300)
    return Exception("$label: HTTP $code" + if (detail.isBlank()) "" else " — $detail")
}

private suspend fun scanBackend(minScore: Double, profitTargetPct: Double, stopLossPct: Double): BackendScanResult =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/scanner/scan?min_score=$minScore&profit_target_pct=$profitTargetPct&stop_loss_pct=$stopLossPct"

        val client = backendClient.newBuilder().readTimeout(180, java.util.concurrent.TimeUnit.SECONDS).build()
        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw backendFailure("Backend error", response.code, response.body?.string())
            }

            val body = response.body?.string()
                ?: throw Exception("Backend returned an empty response")

            val root = JSONObject(body)
            val qualified = root.getJSONArray("qualified")
            val signals = mutableListOf<Signal>()

            for (i in 0 until qualified.length()) {
                val item = qualified.getJSONObject(i)
                val symbol = item.getString("symbol")
                val analysis = item.getJSONObject("analysis")

                val price = analysis.getDouble("price")
                val score = analysis.getDouble("score")
                val profitPct = analysis.getDouble("profit_target_pct")
                val stopPct = analysis.getDouble("stop_loss_pct")

                signals += Signal(
                    symbol = symbol,
                    score = score,
                    price = price,
                    target = price * (1.0 + profitPct / 100.0),
                    stop = price * (1.0 - stopPct / 100.0),
                    reasons = listOf("Qualified with a fresh market quote")
                )
            }

            BackendScanResult(signals, root.optInt("valid_count"), root.optInt("cached_count"), root.optInt("universe_count"))
        }
    }



private suspend fun backendLivePositions(): List<LivePosition> =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/trading212/positions"

        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        backendClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw Exception("Live portfolio error: HTTP ${response.code}")
            }

            val body = response.body?.string()
                ?: throw Exception("Live portfolio returned an empty response")

            val root = JSONObject(body)
            val array = root.getJSONArray("positions")
            val result = mutableListOf<LivePosition>()

            for (i in 0 until array.length()) {
                val item = array.getJSONObject(i)

                result += LivePosition(
                    symbol = item.optString("symbol", ""),
                    brokerTicker = item.optString("broker_ticker", ""),
                    name = item.optString("name", ""),
                    currentPrice = item.optDouble("current_price", 0.0),
                    averagePrice = item.optDouble("average_price", 0.0),
                    quantity = item.optDouble("quantity", 0.0),
                    currency = item.optString("currency", "GBP"),
                    positionValue = item.optDouble("position_value", 0.0),
                    positionCost = item.optDouble("position_cost", 0.0),
                    unrealizedProfitLoss = item.optDouble("unrealized_profit_loss", 0.0)
                )
            }

            result
        }
    }


private suspend fun backendLiveAccountSummary(): LiveAccountSummary =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/trading212/account-summary"

        val request = Request.Builder().url(url).get().build()

        backendClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw Exception("Account summary error: HTTP ${response.code}")
            }

            val body = response.body?.string()
                ?: throw Exception("Account summary returned an empty response")

            val root = JSONObject(body)
            val investments = root.optJSONObject("investments")

            LiveAccountSummary(
                currency = root.optString("currency", "GBP"),
                cash = root.optDouble("cash", 0.0),
                totalValue = root.optDouble("totalValue", 0.0),
                investmentsValue = investments?.optDouble("currentValue", 0.0) ?: 0.0,
                investmentsCost = investments?.optDouble("totalCost", 0.0) ?: 0.0,
                unrealizedProfitLoss = investments?.optDouble("unrealizedProfitLoss", 0.0) ?: 0.0
            )
        }
    }


private suspend fun backendPaperBuy(
    symbol: String,
    profitTargetPct: Double,
    stopLossPct: Double
): JSONObject =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/paper/buy?symbol=$symbol&profit_target_pct=$profitTargetPct&stop_loss_pct=$stopLossPct"

        val request = Request.Builder()
            .url(url)
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        backendClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw backendFailure("Paper buy error", response.code, response.body?.string())
            }

            val body = response.body?.string()
                ?: throw Exception("Paper buy returned an empty response")

            JSONObject(body)
        }
    }

private suspend fun backendPaperSell(): JSONObject =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/paper/sell"

        val request = Request.Builder()
            .url(url)
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        backendClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val body = response.body?.string()
                throw Exception("Paper sell error: HTTP ${response.code} ${body ?: ""}")
            }

            val body = response.body?.string()
                ?: throw Exception("Paper sell returned an empty response")

            JSONObject(body)
        }
    }


private suspend fun backendPaperStatus(): JSONObject =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/paper/status"

        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        backendClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val body = response.body?.string()
                throw Exception("Paper status error: HTTP ${response.code} ${body ?: ""}")
            }

            val body = response.body?.string()
                ?: throw Exception("Paper status returned an empty response")

            JSONObject(body)
        }
    }


private suspend fun backendPaperAutomationStatus(): JSONObject =
    withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("https://trendtrader212.duckdns.org/paper/automation")
            .get()
            .build()

        backendClient.newCall(request).execute().use { response ->
            val body = response.body?.string()
                ?: throw Exception("Automation status returned an empty response")
            if (!response.isSuccessful) {
                throw Exception("Automation status error: HTTP ${response.code} $body")
            }
            JSONObject(body)
        }
    }

private suspend fun configureBackendPaperAutomation(
    enabled: Boolean,
    profitTargetPct: Double,
    stopLossPct: Double,
    minScore: Double
): JSONObject =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/paper/automation" +
                "?enabled=$enabled" +
                "&profit_target_pct=$profitTargetPct" +
                "&stop_loss_pct=$stopLossPct" +
                "&min_score=$minScore" +
                "&scan_interval_seconds=900" +
                "&check_interval_seconds=60"

        val request = Request.Builder()
            .url(url)
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        backendClient.newCall(request).execute().use { response ->
            val body = response.body?.string()
                ?: throw Exception("Automation update returned an empty response")
            if (!response.isSuccessful) {
                throw Exception("Automation update error: HTTP ${response.code} $body")
            }
            JSONObject(body)
        }
    }


private suspend fun backendPaperCheck(): JSONObject =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/paper/check"

        val request = Request.Builder()
            .url(url)
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        backendClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val body = response.body?.string()
                throw Exception("Paper check error: HTTP ${response.code} ${body ?: ""}")
            }

            val body = response.body?.string()
                ?: throw Exception("Paper check returned an empty response")

            JSONObject(body)
        }
    }


private suspend fun backendCurrentPrice(symbol: String): Double =
    withContext(Dispatchers.IO) {
        val url =
            "https://trendtrader212.duckdns.org/market/quote?symbol=$symbol"

        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        backendClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw Exception("Quote error: HTTP ${response.code}")
            }

            val body = response.body?.string()
                ?: throw Exception("Quote returned an empty response")

            JSONObject(body).getDouble("price")
        }
    }

@OptIn(ExperimentalMaterial3Api::class)
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = android.graphics.Color.rgb(13, 17, 23)
        window.navigationBarColor = android.graphics.Color.rgb(13, 17, 23)
        setContent { App() }
    }
}

@Composable
fun App() {
    val scope = rememberCoroutineScope()
    val market = remember { StooqProvider() }
    val api = remember { Trading212Api() }

    var mode by remember { mutableStateOf("PAPER") }
    var key by remember { mutableStateOf("") }
    var secret by remember { mutableStateOf("") }
    var profitTarget by remember { mutableStateOf("4.0") }
    var stopLoss by remember { mutableStateOf("2.0") }
    var minScore by remember { mutableStateOf("75") }
    var cash by remember { mutableStateOf(1000.0) }
    var status by remember { mutableStateOf("Paper mode — no real orders") }
    var scanning by remember { mutableStateOf(false) }
    var positions by remember { mutableStateOf(listOf<SimPosition>()) }
    var livePositions by remember { mutableStateOf(listOf<LivePosition>()) }
    var livePortfolioLoading by remember { mutableStateOf(false) }
    var liveLastUpdated by remember { mutableStateOf("Not yet updated") }
    var liveRefreshStatus by remember { mutableStateOf("Waiting for first refresh") }
    var liveAccountSummary by remember { mutableStateOf<LiveAccountSummary?>(null) }
    var signals by remember { mutableStateOf(listOf<Signal>()) }
    var log by remember { mutableStateOf(listOf("Ready.")) }
    var automationEnabled by remember { mutableStateOf(false) }
    var automationLoaded by remember { mutableStateOf(false) }
    var automationBusy by remember { mutableStateOf(false) }
    var automationLastAction by remember { mutableStateOf("Loading...") }
    var automationLastScan by remember { mutableStateOf("Not yet scanned") }
    var automationNextScan by remember { mutableStateOf("Waiting") }
    var automationLastUpdated by remember { mutableStateOf("Not yet updated") }
    var backgroundScanStatus by remember { mutableStateOf("Loading...") }
    var scanCoverage by remember { mutableStateOf("Waiting for first background scan") }
    var lastPositionCheck by remember { mutableStateOf("Not yet recorded") }
    var positionCheckError by remember { mutableStateOf("") }
    var checkInterval by remember { mutableStateOf(60) }
    var scanInterval by remember { mutableStateOf(900) }
    var positionQuoteTimestamp by remember { mutableStateOf<Long?>(null) }
    var shortlistRefreshed by remember { mutableStateOf("Not yet recorded") }
    var shortlist by remember { mutableStateOf(listOf<JSONObject>()) }

    fun addLog(s: String) { log = (log + s).takeLast(100) }

    LaunchedEffect(Unit) {
        runCatching {
            backendPaperStatus()
        }.onSuccess { state ->
            cash = state.optDouble("cash", cash)
            val backendPosition = state.optJSONObject("position")

            if (backendPosition != null) {
                val symbol = backendPosition.optString("symbol", "")
                val entry = backendPosition.optDouble("entry", 0.0)
                val quantity = backendPosition.optDouble("quantity", 0.0)
                val target = backendPosition.optDouble("target", 0.0)
                val stop = backendPosition.optDouble("stop", 0.0),
                                distributionPerShare = backendPosition.optDouble("distribution_per_share", 0.0)

                positions = listOf(
                    SimPosition(
                        ticker = symbol,
                        entry = entry,
                        quantity = quantity,
                        price = entry,
                        target = target,
                        stop = stop,
                        distributionPerShare = backendPosition.optDouble("distribution_per_share", 0.0)
                    )
                )

                status = "Restored paper position: $symbol"
                addLog("Restored backend paper position: $symbol")
            }
        }.onFailure { e ->
            if (e is CancellationException) throw e
            addLog("Paper account restore failed: ${e.message}")
        }
    }

    LaunchedEffect(mode, positions.firstOrNull()?.ticker) {
        while (mode == "PAPER" && positions.isNotEmpty()) {
            val p = positions.first()

            runCatching {
                backendPaperCheck()
            }.onSuccess { state ->
                val backendPosition = state.optJSONObject("position")

                if (backendPosition == null) {
                    cash = state.optDouble("cash", cash)
                    positions = emptyList()
                    status = "Paper position closed by backend"
                    addLog("AUTO PAPER SELL confirmed by backend; cash USD ${"%.2f".format(cash)}")
                } else {
                    val current = backendPosition.optDouble("current_price", p.price)
                    cash = state.optDouble("cash", cash)
                    positions = listOf(p.copy(price = current,
                        target = backendPosition.optDouble("target", p.target),
                        stop = backendPosition.optDouble("stop", p.stop),
                        distributionPerShare = backendPosition.optDouble("distribution_per_share", 0.0)))
                    val quoteSource = backendPosition.optString("quote_source", "unknown")
                    val latestTradingDay = backendPosition.optString("latest_trading_day", "")
                    val monitoringStatus = backendPosition.optString("monitoring_status", "")

                    status = when (quoteSource) {
                        "live" -> "FRESH QUOTE • ${p.ticker} @ ${"%.2f".format(current)} • automatic exits enabled"
                        "cached" -> {
                            val dateText = if (latestTradingDay.isNotBlank()) " • close $latestTradingDay" else ""
                            "CACHED • ${p.ticker} @ ${"%.2f".format(current)}$dateText • automatic exits paused"
                        }
                        else -> {
                            if (monitoringStatus.isNotBlank()) {
                                "${p.ticker} @ ${"%.2f".format(current)} • $monitoringStatus"
                            } else {
                                "Monitoring ${p.ticker} @ ${"%.2f".format(current)}"
                            }
                        }
                    }
                }
            }.onFailure { e ->
                if (e is CancellationException) throw e
                addLog("Price monitor failed: ${e.message}")
            }

            delay(60000)
        }
    }


    LaunchedEffect(mode) {
        if (mode == "PAPER") {
            while (true) {
                runCatching {
                    backendPaperAutomationStatus()
                }.onSuccess { state ->
                    val automation = state.getJSONObject("automation")
                    val firstLoad = !automationLoaded
                    val previousAction = automationLastAction

                    automationEnabled = automation.optBoolean("enabled", false)
                    automationLastAction =
                        automation.optString("last_action", "No action recorded")
                    automationLastScan = displayBackendTime(automation.optString("last_scan_at", ""))
                    scanInterval = automation.optInt("scan_interval_seconds", 900)
                    checkInterval = automation.optInt("check_interval_seconds", 60)
                    backgroundScanStatus = automation.optString("scan_status", "Background scan status unavailable")
                    scanCoverage = if (automation.has("scan_valid_count"))
                        "${automation.optInt("scan_valid_count")} analysed / ${automation.optInt("scan_universe_count")} US stocks"
                    else "Waiting for first background scan"
                    lastPositionCheck = displayBackendTime(automation.optString("last_position_check_at", ""))
                    positionCheckError = if (automation.isNull("last_position_check_error")) "" else automation.optString("last_position_check_error", "")
                    shortlistRefreshed = displayBackendTime(automation.optString("shortlist_refreshed_at", ""))
                    val candidateArray = automation.optJSONArray("shortlist")
                    shortlist = (0 until (candidateArray?.length() ?: 0)).mapNotNull { candidateArray?.optJSONObject(it) }
                    val backendPosition = state.optJSONObject("position")
                    positionQuoteTimestamp = if (backendPosition != null && !backendPosition.isNull("quote_timestamp"))
                        backendPosition.optLong("quote_timestamp") else null
                    automationNextScan = if (!automationEnabled) "Paused" else runCatching {
                        java.time.LocalDateTime.parse(automation.optString("last_scan_at", ""))
                            .plusSeconds(scanInterval.toLong()).atOffset(java.time.ZoneOffset.UTC)
                            .atZoneSameInstant(java.time.ZoneId.systemDefault())
                            .format(java.time.format.DateTimeFormatter.ofPattern("HH:mm:ss"))
                    }.getOrDefault("Due shortly")

                    if (firstLoad) {
                        profitTarget = automation
                            .optDouble("profit_target_pct", 4.0).toString()
                        stopLoss = automation
                            .optDouble("stop_loss_pct", 2.0).toString()
                        minScore = automation
                            .optDouble("min_score", 75.0).toString()
                    }

                    cash = state.optDouble("cash", cash)

                    if (backendPosition != null) {
                        val entry = backendPosition.optDouble("entry", 0.0)
                        positions = listOf(
                            SimPosition(
                                ticker = backendPosition.optString("symbol", ""),
                                entry = entry,
                                quantity = backendPosition.optDouble("quantity", 0.0),
                                price = backendPosition.optDouble(
                                    "current_price", entry
                                ),
                                target = backendPosition.optDouble("target", 0.0),
                                stop = backendPosition.optDouble("stop", 0.0)
                            )
                        )
                    } else {
                        positions = emptyList()
                    }

                    automationLastUpdated =
                        SimpleDateFormat("HH:mm:ss", Locale.UK).format(Date())
                    automationLoaded = true

                    if (!firstLoad &&
                        automationLastAction != previousAction
                    ) {
                        addLog("AUTO: $automationLastAction")
                    }
                }.onFailure { e ->
                    if (e is CancellationException) throw e
                    automationLastUpdated = "Connection failed"
                    addLog("Automation refresh failed: ${e.message}")
                }

                delay(30000)
            }
        }
    }

    LaunchedEffect(mode) {
        if (mode == "LIVE") {
            while (true) {
                runCatching {
                    val positions = backendLivePositions()
                    val summary = backendLiveAccountSummary()
                    Pair(positions, summary)
                }.onSuccess { result ->
                    livePositions = result.first
                    liveAccountSummary = result.second
                    liveLastUpdated = SimpleDateFormat("HH:mm:ss", Locale.UK).format(Date())
                    liveRefreshStatus = "Connected • ${result.first.size} positions"
                }.onFailure { e ->
                    if (e is CancellationException) throw e
                    liveRefreshStatus = "Refresh failed"
                    addLog("Live portfolio auto-refresh failed: ${e.message}")
                }

                delay(60000)
            }
        }
    }


    MaterialTheme(colorScheme = TraderDarkColors) {
        Scaffold(
            topBar = {
                TopAppBar(title = { Text("212 Trend Trader") })
            }
        ) { pad ->
            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(pad).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                item {
                    Text("AUTOMATION", style = MaterialTheme.typography.titleLarge)
                    Text(status)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(mode == "PAPER", { mode = "PAPER"; api.setMode(false) }, { Text("PAPER") })
                        FilterChip(mode == "LIVE", { mode = "LIVE"; api.setMode(true) }, { Text("LIVE") })
                    }
                    if (mode == "LIVE") {
                        Text("LIVE MODE: order placement is enabled only by an explicit action.", color = MaterialTheme.colorScheme.error)
                        OutlinedTextField(key, { key = it }, label = { Text("Trading 212 API key") }, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(secret, { secret = it }, label = { Text("API secret") }, modifier = Modifier.fillMaxWidth())
                    }
                }

                if (mode == "PAPER") {
                    item {
                        Card(Modifier.fillMaxWidth()) {
                            Column(
                                Modifier.padding(12.dp),
                                verticalArrangement =
                                    Arrangement.spacedBy(6.dp)
                            ) {
                                Text(
                                    if (automationLastUpdated == "Connection failed") "PAPER AUTOMATION: STATUS UNVERIFIED" else if (automationEnabled)
                                        "PAPER AUTOMATION: ON"
                                    else
                                        "PAPER AUTOMATION: PAUSED",
                                    style = MaterialTheme.typography.titleMedium,
                                    color =
                                        if (automationLastUpdated == "Connection failed") MaterialTheme.colorScheme.error else if (automationEnabled)
                                            Color(0xFF69DB8C)
                                        else
                                            Color(0xFFFF8585)
                                )
                                Text("Last action: $automationLastAction")
                                Text("Last scan: $automationLastScan")
                                Text("Next background scan: $automationNextScan")
                                Text("Scanning: every ${scanInterval / 60} minutes, including while holding")
                                Text("Position check: every $checkInterval seconds")
                                Text("Last position check: $lastPositionCheck")
                                if (positionCheckError.isNotBlank()) Text("Monitor error: $positionCheckError", color = MaterialTheme.colorScheme.error)
                                Text("Background scan: $backgroundScanStatus")
                                Text(scanCoverage)
                                Text("Dashboard updated: $automationLastUpdated")

                                val open = positions.firstOrNull()
                                if (open != null) {
                                    val value = open.quantity * open.price
                                    val cost = open.quantity * open.entry
                                    val gain = value + open.quantity * open.distributionPerShare - cost
                                    val gainPct =
                                        if (cost > 0.0) gain / cost * 100.0
                                        else 0.0
                                    val gainColor =
                                        if (gain >= 0.0)
                                            Color(0xFF69DB8C)
                                        else
                                            Color(0xFFFF8585)

                                    Text(
                                        "Paper price: ${"%.2f".format(open.price)} • ${quoteAgeLabel(positionQuoteTimestamp)}"
                                    )
                                    Text(
                                        "Paper P/L: USD ${"%+.2f".format(gain)} " +
                                            "(${"%+.2f".format(gainPct)}%)",
                                        color = gainColor
                                    )
                                }

                                Button(
                                    enabled = !automationBusy,
                                    onClick = {
                                        automationBusy = true
                                        scope.launch {
                                            runCatching {
                                                configureBackendPaperAutomation(
                                                    enabled = true,
                                                    profitTargetPct =
                                                        profitTarget.toDoubleOrNull()
                                                            ?: 4.0,
                                                    stopLossPct =
                                                        stopLoss.toDoubleOrNull()
                                                            ?: 2.0,
                                                    minScore =
                                                        minScore.toDoubleOrNull()
                                                            ?: 75.0
                                                )
                                            }.onSuccess { state ->
                                                automationEnabled = true
                                                automationLastAction =
                                                    state.getJSONObject("automation")
                                                        .optString(
                                                            "last_action",
                                                            "Settings saved"
                                                        )
                                                status =
                                                    "Paper automation settings saved"
                                                addLog(
                                                    "Automation settings saved and enabled"
                                                )
                                            }.onFailure { e ->
                                                if (e is CancellationException) throw e
                                                addLog(
                                                    "Automation update failed: ${e.message}"
                                                )
                                            }
                                            automationBusy = false
                                        }
                                    },
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text(
                                        if (automationBusy) "SAVING..."
                                        else "SAVE SETTINGS & ENABLE"
                                    )
                                }

                                OutlinedButton(
                                    enabled = !automationBusy,
                                    onClick = {
                                        automationBusy = true
                                        scope.launch {
                                            val newEnabled = !automationEnabled
                                            runCatching {
                                                configureBackendPaperAutomation(
                                                    enabled = newEnabled,
                                                    profitTargetPct =
                                                        profitTarget.toDoubleOrNull()
                                                            ?: 4.0,
                                                    stopLossPct =
                                                        stopLoss.toDoubleOrNull()
                                                            ?: 2.0,
                                                    minScore =
                                                        minScore.toDoubleOrNull()
                                                            ?: 75.0
                                                )
                                            }.onSuccess {
                                                automationEnabled = newEnabled
                                                automationLastAction =
                                                    if (newEnabled)
                                                        "Automation enabled"
                                                    else
                                                        "Automation paused"
                                                addLog(automationLastAction)
                                            }.onFailure { e ->
                                                if (e is CancellationException) throw e
                                                addLog(
                                                    "Automation toggle failed: ${e.message}"
                                                )
                                            }
                                            automationBusy = false
                                        }
                                    },
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text(
                                        if (automationEnabled)
                                            "PAUSE AUTOMATION"
                                        else
                                            "RESUME AUTOMATION"
                                    )
                                }
                            }
                        }
                    }
                }

                if (mode == "LIVE") {
                    item {
                        Text("LIVE PORTFOLIO — READ ONLY", style = MaterialTheme.typography.titleLarge)
                        Text("Last updated: $liveLastUpdated")
                        Text("Auto refresh: every 60 seconds")
                        Text(liveRefreshStatus)

                        val summary = liveAccountSummary

                        if (summary != null) {
                            val glPct =
                                if (summary.investmentsCost > 0.0)
                                    (summary.unrealizedProfitLoss / summary.investmentsCost) * 100.0
                                else 0.0

                            val summaryGlColor =
                                if (summary.unrealizedProfitLoss >= 0.0)
                                    Color(0xFF69DB8C)
                                else
                                    Color(0xFFFF8585)

                            Text(
                                "Positions: ${livePositions.size}",
                                style = MaterialTheme.typography.titleMedium
                            )
                            Text("Cash: ${summary.currency} ${"%.2f".format(summary.cash)}")
                            Text("Cost basis: ${summary.currency} ${"%.2f".format(summary.investmentsCost)}")
                            Text(
                                "Investments value: ${summary.currency} ${"%.2f".format(summary.investmentsValue)}",
                                style = MaterialTheme.typography.titleMedium
                            )
                            Text(
                                "Unrealised G/L: ${summary.currency} ${"%+.2f".format(summary.unrealizedProfitLoss)} (${"%+.2f".format(glPct)}%)",
                                color = summaryGlColor
                            )
                            Text(
                                "Total account value: ${summary.currency} ${"%.2f".format(summary.totalValue)}",
                                style = MaterialTheme.typography.titleMedium
                            )
                        }

                        Button(
                            enabled = !livePortfolioLoading,
                            onClick = {
                                livePortfolioLoading = true
                                scope.launch {
                                    runCatching {
                                        val positions = backendLivePositions()
                                        val summary = backendLiveAccountSummary()
                                        Pair(positions, summary)
                                    }.onSuccess { result ->
                                        livePositions = result.first
                                        liveAccountSummary = result.second
                                        liveLastUpdated = SimpleDateFormat("HH:mm:ss", Locale.UK).format(Date())
                                        liveRefreshStatus = "Connected • ${result.first.size} positions"
                                        status = "Live Trading 212 portfolio loaded"
                                        addLog("Loaded ${result.first.size} read-only Trading 212 positions")
                                    }.onFailure { e ->
                                        if (e is CancellationException) throw e
                                        liveRefreshStatus = "Refresh failed"
                                        status = "Live portfolio failed: ${e.message}"
                                        addLog("Live portfolio failed: ${e.message}")
                                    }

                                    livePortfolioLoading = false
                                }
                            },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(if (livePortfolioLoading) "LOADING..." else "REFRESH LIVE PORTFOLIO")
                        }

                        Text("No order execution permission is used by this screen.")
                    }

                    items(livePositions) { p ->
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp)) {
                                Text(
                                    "${p.symbol} — ${p.name}",
                                    style = MaterialTheme.typography.titleMedium
                                )
                                Text("Current: ${"%.2f".format(p.currentPrice)}")
                                Text("Average paid: ${"%.2f".format(p.averagePrice)}")
                                Text("Quantity: ${"%.4f".format(p.quantity)}")
                                val gainLoss = p.unrealizedProfitLoss
                                val gainLossPct =
                                    if (p.positionCost > 0.0)
                                        (gainLoss / p.positionCost) * 100.0
                                    else 0.0

                                val gainLossColor =
                                    if (gainLoss >= 0.0)
                                        Color(0xFF69DB8C)
                                    else
                                        Color(0xFFFF8585)

                                Text(
                                    "Position value: ${p.currency} ${"%.2f".format(p.positionValue)}"
                                )
                                Text(
                                    "Cost basis: ${p.currency} ${"%.2f".format(p.positionCost)}"
                                )
                                Text(
                                    "Gain/Loss: ${p.currency} ${"%+.2f".format(gainLoss)} (${"%+.2f".format(gainLossPct)}%)",
                                    color = gainLossColor
                                )
                                Text(
                                    if (gainLoss >= 0.0) "Status: PROFIT" else "Status: LOSS",
                                    color = gainLossColor
                                )

                                Text("Broker ticker: ${p.brokerTicker}")
                            }
                        }
                    }
                }

                item {
                    Text("STRATEGY", style = MaterialTheme.typography.titleLarge)
                    OutlinedTextField(profitTarget, { profitTarget = it }, label = { Text("Profit target %") })
                    OutlinedTextField(stopLoss, { stopLoss = it }, label = { Text("Stop loss %") })
                    OutlinedTextField(minScore, { minScore = it }, label = { Text("Minimum trend score") })
                    Text("Paper cash: USD ${"%.2f".format(Locale.UK, cash)}")
                    Text("Gross USD simulation • distributions included • no tax or FX")
                }

                item {
                    Button(
                        enabled = !scanning,
                        onClick = {
                            scanning = true
                            scope.launch {
                                try {
                                    try {
                                        val result = scanBackend(
                                            minScore.toDoubleOrNull() ?: 75.0,
                                            profitTarget.toDoubleOrNull() ?: 4.0,
                                            stopLoss.toDoubleOrNull() ?: 2.0
                                        )
                                        signals = result.signals
                                        status = "Analysed ${result.analysed} stocks • ${result.signals.size} eligible signals • ${result.cached} cached / ${result.universe} US stocks"
                                        addLog(status)
                                        addLog("Top eligible: ${signals.firstOrNull()?.symbol ?: "none"}")
                                    } catch (e: CancellationException) {
                                        throw e
                                    } catch (e: Exception) {
                                        signals = emptyList()
                                        status = "Scan failed: ${e.message}"
                                        addLog(status)
                                    }
                                } finally { scanning = false }
                            }
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text(if (scanning) "SCANNING..." else "SCAN MARKET") }
                }

                item { Text("TOP SIGNALS", style = MaterialTheme.typography.titleLarge) }
                items(signals) { sig ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Text("${sig.symbol} — score ${"%.0f".format(sig.score)}", style = MaterialTheme.typography.titleMedium)
                            Text("Price: ${"%.2f".format(sig.price)}  Target: ${"%.2f".format(sig.target)}  Stop: ${"%.2f".format(sig.stop)}")
                            Text(sig.reasons.joinToString(" • "))
                            if (mode == "PAPER") {
                                Button(onClick = {
                                    if (positions.isNotEmpty()) {
                                        addLog("Paper buy blocked: one position at a time.")
                                    } else if (cash <= 0) {
                                        addLog("Paper buy blocked: no cash.")
                                    } else {
                                        scope.launch {
        try {
            val state = backendPaperBuy(
                sig.symbol,
                profitTarget.toDoubleOrNull() ?: 4.0,
                stopLoss.toDoubleOrNull() ?: 2.0
            )

            val pos = state.getJSONObject("position")
            cash = state.getDouble("cash")

            positions = listOf(
                SimPosition(
                    ticker = pos.getString("symbol"),
                    entry = pos.getDouble("entry"),
                    quantity = pos.getDouble("quantity"),
                    price = pos.getDouble("entry"),
                    target = pos.getDouble("target"),
                    stop = pos.getDouble("stop"),
                    distributionPerShare = pos.optDouble("distribution_per_share", 0.0)
                )
            )

            addLog("PAPER BUY confirmed by backend")
        } catch (e: Exception) {
            addLog("Paper buy failed: ${e.message}")
        }
    }
    }
                            }) { Text("PAPER BUY") }
                            }
                        }
                    }
                }

                item {
                    Text("OPEN POSITION", style = MaterialTheme.typography.titleLarge)
                    if (positions.isEmpty()) Text("None")
                }
                items(positions) { p ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Text(p.ticker, style = MaterialTheme.typography.titleMedium)
                            Text("Entry ${"%.2f".format(p.entry)} • Target ${"%.2f".format(p.target)} • Stop ${"%.2f".format(p.stop)}")
                            Text("Quantity ${"%.4f".format(p.quantity)}")
                            Text("Current ${"%.2f".format(p.price)}")
                            val paperGain =
                                p.quantity * (p.price + p.distributionPerShare - p.entry)
                            val paperGainPct =
                                if (p.entry > 0.0)
                                    (p.price + p.distributionPerShare - p.entry) / p.entry * 100.0
                                else 0.0
                            Text(
                                "P/L USD ${"%+.2f".format(paperGain)} " +
                                    "(${"%+.2f".format(paperGainPct)}%)",
                                color =
                                    if (paperGain >= 0.0)
                                        Color(0xFF69DB8C)
                                    else
                                        Color(0xFFFF8585)
                            )
                            Button(onClick = {
                                scope.launch {
                                    try {
                                        val state = backendPaperSell()
                                        cash = state.getDouble("cash")
                                        positions = emptyList()
                                        status = "Paper position closed"
                                        addLog(
                                            "PAPER SELL confirmed by backend; cash USD ${"%.2f".format(cash)}"
                                        )
                                    } catch (e: Exception) {
                                        addLog("Paper sell failed: ${e.message}")
                                    }
                                }
                            }) { Text("PAPER SELL") }
                        }
                    }
                }

                if (mode == "PAPER") {
                    item {
                        Text("BACKGROUND SHORTLIST", style = MaterialTheme.typography.titleLarge)
                        Text("${shortlist.size} candidates • refreshed $shortlistRefreshed")
                        Text("Candidates are checked again before buying.", style = MaterialTheme.typography.bodySmall)
                        if (automationLastUpdated == "Connection failed") Text("Connection failed — shortlist and prices may be stale", color = MaterialTheme.colorScheme.error)
                        if (shortlist.isEmpty()) Text("No candidates yet")
                    }
                    items(shortlist) { candidate ->
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                val score = candidate.optJSONObject("analysis")?.optDouble("score", 0.0) ?: 0.0
                                val timestamp = if (candidate.isNull("quote_timestamp")) null else candidate.optLong("quote_timestamp")
                                val price = if (candidate.has("price") && !candidate.isNull("price")) "%.3f".format(candidate.optDouble("price")) else "unavailable"
                                Text("${candidate.optString("symbol")} • score ${"%.1f".format(score)}", style = MaterialTheme.typography.titleMedium)
                                Text("Price $price • ${quoteAgeLabel(timestamp)}")
                                val fresh = timestamp?.let { System.currentTimeMillis() / 1000 - it in 0..120 } ?: false
                                val verified = automationLastUpdated != "Connection failed" && fresh
                                val eligible = candidate.optBoolean("entry_eligible", false)
                                Text(if (!verified) "Fresh quote required" else if (eligible) "Eligible at last check" else "Entry blocked",
                                    color = if (verified && eligible) Color(0xFF69DB8C) else MaterialTheme.colorScheme.onSurfaceVariant)
                                Text(candidate.optString("entry_reason", "Waiting for quote"), style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }

                item {
                    Text("EVENT LOG", style = MaterialTheme.typography.titleLarge)
                    log.takeLast(20).forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
                }
            }
        }
    }
}
