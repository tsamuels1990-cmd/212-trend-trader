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
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import java.util.Locale
import java.text.SimpleDateFormat
import java.util.Date
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class SimPosition(
    val ticker: String,
    val entry: Double,
    val quantity: Double,
    var price: Double,
    val target: Double,
    val stop: Double
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


private suspend fun scanBackend(minScore: Double, profitTargetPct: Double, stopLossPct: Double): List<Signal> =
    withContext(Dispatchers.IO) {
        val url =
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/scanner/scan?min_score=$minScore&profit_target_pct=$profitTargetPct&stop_loss_pct=$stopLossPct"

        val client = OkHttpClient()
        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw Exception("Backend error: HTTP ${response.code}")
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
                    reasons = listOf("Qualified by backend scanner")
                )
            }

            signals
        }
    }



private suspend fun backendLivePositions(): List<LivePosition> =
    withContext(Dispatchers.IO) {
        val url =
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/trading212/positions"

        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        OkHttpClient().newCall(request).execute().use { response ->
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
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/trading212/account-summary"

        val request = Request.Builder().url(url).get().build()

        OkHttpClient().newCall(request).execute().use { response ->
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
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/paper/buy?symbol=$symbol&profit_target_pct=$profitTargetPct&stop_loss_pct=$stopLossPct"

        val request = Request.Builder()
            .url(url)
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        OkHttpClient().newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw Exception("Paper buy error: HTTP ${response.code}")
            }

            val body = response.body?.string()
                ?: throw Exception("Paper buy returned an empty response")

            JSONObject(body)
        }
    }

private suspend fun backendPaperSell(): JSONObject =
    withContext(Dispatchers.IO) {
        val url =
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/paper/sell"

        val request = Request.Builder()
            .url(url)
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        OkHttpClient().newCall(request).execute().use { response ->
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
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/paper/status"

        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        OkHttpClient().newCall(request).execute().use { response ->
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
            .url("https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/paper/automation")
            .get()
            .build()

        OkHttpClient().newCall(request).execute().use { response ->
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
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/paper/automation" +
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

        OkHttpClient().newCall(request).execute().use { response ->
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
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/paper/check"

        val request = Request.Builder()
            .url(url)
            .post(okhttp3.RequestBody.create(null, ByteArray(0)))
            .build()

        OkHttpClient().newCall(request).execute().use { response ->
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
            "https://redesigned-orbit-4qwqr959pr7ph9gv-8001.app.github.dev/market/quote?symbol=$symbol"

        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        OkHttpClient().newCall(request).execute().use { response ->
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
                val stop = backendPosition.optDouble("stop", 0.0)

                positions = listOf(
                    SimPosition(
                        ticker = symbol,
                        entry = entry,
                        quantity = quantity,
                        price = entry,
                        target = target,
                        stop = stop
                    )
                )

                status = "Restored paper position: $symbol"
                addLog("Restored backend paper position: $symbol")
            }
        }.onFailure { e ->
            addLog("Paper account restore failed: ${e.message}")
        }
    }

    LaunchedEffect(positions) {
        while (positions.isNotEmpty()) {
            val p = positions.first()

            runCatching {
                backendPaperCheck()
            }.onSuccess { state ->
                val backendPosition = state.optJSONObject("position")

                if (backendPosition == null) {
                    cash = state.optDouble("cash", cash)
                    positions = emptyList()
                    status = "Paper position closed by backend"
                    addLog("AUTO PAPER SELL confirmed by backend; cash £${"%.2f".format(cash)}")
                } else {
                    val current = backendPosition.optDouble("current_price", p.price)
                    positions = listOf(p.copy(price = current))
                    val quoteSource = backendPosition.optString("quote_source", "unknown")
                    val latestTradingDay = backendPosition.optString("latest_trading_day", "")
                    val monitoringStatus = backendPosition.optString("monitoring_status", "")

                    status = when (quoteSource) {
                        "live" -> "LIVE • ${p.ticker} @ ${"%.2f".format(current)} • automatic exits enabled"
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
                    automationLastScan =
                        automation.optString("last_scan_at", "")
                            .takeIf { it.isNotBlank() }
                            ?.replace("T", " ")
                            ?.take(19)
                            ?: "Not yet scanned"

                    val scanSeconds =
                        automation.optInt("scan_interval_seconds", 900)
                    val backendPosition = state.optJSONObject("position")

                    automationNextScan =
                        if (backendPosition != null) {
                            "After the open position closes"
                        } else {
                            val raw = automation.optString("last_scan_at", "")
                            runCatching {
                                java.time.LocalDateTime.parse(raw)
                                    .plusSeconds(scanSeconds.toLong())
                                    .format(
                                        java.time.format.DateTimeFormatter
                                            .ofPattern("HH:mm:ss 'UTC'")
                                    )
                            }.getOrDefault("Due shortly")
                        }

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
                    liveRefreshStatus = "Refresh failed"
                    addLog("Live portfolio auto-refresh failed: ${e.message}")
                }

                delay(60000)
            }
        }
    }


    MaterialTheme {
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
                                    if (automationEnabled)
                                        "PAPER AUTOMATION: ON"
                                    else
                                        "PAPER AUTOMATION: PAUSED",
                                    style = MaterialTheme.typography.titleMedium,
                                    color =
                                        if (automationEnabled)
                                            androidx.compose.ui.graphics.Color(0xFF2E7D32)
                                        else
                                            androidx.compose.ui.graphics.Color(0xFFC62828)
                                )
                                Text("Last action: $automationLastAction")
                                Text("Last scan: $automationLastScan")
                                Text("Next scan: $automationNextScan")
                                Text("Position check: every 60 seconds")
                                Text("Dashboard updated: $automationLastUpdated")

                                val open = positions.firstOrNull()
                                if (open != null) {
                                    val value = open.quantity * open.price
                                    val cost = open.quantity * open.entry
                                    val gain = value - cost
                                    val gainPct =
                                        if (cost > 0.0) gain / cost * 100.0
                                        else 0.0
                                    val gainColor =
                                        if (gain >= 0.0)
                                            androidx.compose.ui.graphics.Color(0xFF2E7D32)
                                        else
                                            androidx.compose.ui.graphics.Color(0xFFC62828)

                                    Text(
                                        "Live paper price: ${"%.2f".format(open.price)}"
                                    )
                                    Text(
                                        "Paper P/L: £${"%+.2f".format(gain)} " +
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
                                    androidx.compose.ui.graphics.Color(0xFF2E7D32)
                                else
                                    androidx.compose.ui.graphics.Color(0xFFC62828)

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
                                        androidx.compose.ui.graphics.Color(0xFF2E7D32)
                                    else
                                        androidx.compose.ui.graphics.Color(0xFFC62828)

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
                    Text("Paper cash: £${"%.2f".format(Locale.UK, cash)}")
                }

                item {
                    Button(
                        enabled = !scanning,
                        onClick = {
                            scanning = true
                            scope.launch {
                                try {
                                    val found = runCatching {
                                scanBackend(
                                    minScore.toDoubleOrNull() ?: 75.0,
                                    profitTarget.toDoubleOrNull() ?: 4.0,
                                    stopLoss.toDoubleOrNull() ?: 2.0
                                )
                            }.getOrElse { e ->
                                status = "Scan failed: ${e.message}"
                                addLog("Backend scan failed: ${e.message}")
                                emptyList()
                            }

                            signals = found
                            status = "Scan complete → ${found.size} qualifying signals"
                            addLog("Scan completed. Top: ${found.firstOrNull()?.symbol ?: "none"}")
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
                    stop = pos.getDouble("stop")
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
                                p.quantity * (p.price - p.entry)
                            val paperGainPct =
                                if (p.entry > 0.0)
                                    (p.price - p.entry) / p.entry * 100.0
                                else 0.0
                            Text(
                                "P/L £${"%+.2f".format(paperGain)} " +
                                    "(${"%+.2f".format(paperGainPct)}%)",
                                color =
                                    if (paperGain >= 0.0)
                                        androidx.compose.ui.graphics.Color(0xFF2E7D32)
                                    else
                                        androidx.compose.ui.graphics.Color(0xFFC62828)
                            )
                            Button(onClick = {
                                scope.launch {
                                    try {
                                        val state = backendPaperSell()
                                        cash = state.getDouble("cash")
                                        positions = emptyList()
                                        status = "Paper position closed"
                                        addLog(
                                            "PAPER SELL confirmed by backend; cash £${"%.2f".format(cash)}"
                                        )
                                    } catch (e: Exception) {
                                        addLog("Paper sell failed: ${e.message}")
                                    }
                                }
                            }) { Text("PAPER SELL") }
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
