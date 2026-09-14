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
    val quantity: Double
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
                    quantity = item.optDouble("quantity", 0.0)
                )
            }

            result
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
    var signals by remember { mutableStateOf(listOf<Signal>()) }
    var log by remember { mutableStateOf(listOf("Ready.")) }

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
        if (mode == "LIVE") {
            while (true) {
                runCatching {
                    backendLivePositions()
                }.onSuccess { result ->
                    livePositions = result
                    liveLastUpdated = SimpleDateFormat("HH:mm:ss", Locale.UK).format(Date())
                    liveRefreshStatus = "Connected • ${result.size} positions"
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

                if (mode == "LIVE") {
                    item {
                        Text("LIVE PORTFOLIO — READ ONLY", style = MaterialTheme.typography.titleLarge)
                        Text("Last updated: $liveLastUpdated")
                        Text("Auto refresh: every 60 seconds")
                        Text(liveRefreshStatus)

                        val totalValue = livePositions.sumOf {
                            it.currentPrice * it.quantity
                        }

                        val totalCost = livePositions.sumOf {
                            it.averagePrice * it.quantity
                        }

                        val totalGainLoss = totalValue - totalCost
                        val totalGainLossPct =
                            if (totalCost > 0.0)
                                (totalGainLoss / totalCost) * 100.0
                            else 0.0

                        if (livePositions.isNotEmpty()) {
                            Text(
                                "Positions: ${livePositions.size}",
                                style = MaterialTheme.typography.titleMedium
                            )
                            Text("Cost basis: ${"%.2f".format(totalCost)}")
                            Text(
                                "Portfolio value: ${"%.2f".format(totalValue)}",
                                style = MaterialTheme.typography.titleMedium
                            )
                            Text(
                                "Unrealised G/L: ${"%+.2f".format(totalGainLoss)} (${ "%+.2f".format(totalGainLossPct)}%)"
                            )
                        }

                        Button(
                            enabled = !livePortfolioLoading,
                            onClick = {
                                livePortfolioLoading = true
                                scope.launch {
                                    runCatching {
                                        backendLivePositions()
                                    }.onSuccess { result ->
                                        livePositions = result
                                        liveLastUpdated = SimpleDateFormat("HH:mm:ss", Locale.UK).format(Date())
                                        liveRefreshStatus = "Connected • ${result.size} positions"
                                        status = "Live Trading 212 portfolio loaded"
                                        addLog("Loaded ${result.size} read-only Trading 212 positions")
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
                                Text("Position value: ${"%.2f".format(p.currentPrice * p.quantity)}")

                                val gainLoss = (p.currentPrice - p.averagePrice) * p.quantity
                                val gainLossPct =
                                    if (p.averagePrice > 0.0)
                                        ((p.currentPrice - p.averagePrice) / p.averagePrice) * 100.0
                                    else 0.0

                                Text(
                                    "Gain/Loss: ${"%+.2f".format(gainLoss)} (${ "%+.2f".format(gainLossPct)}%)"
                                )
                                Text(
                                    if (gainLoss >= 0.0) "Status: PROFIT" else "Status: LOSS"
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
