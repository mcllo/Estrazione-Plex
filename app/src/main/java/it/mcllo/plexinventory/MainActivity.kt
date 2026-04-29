package it.mcllo.plexinventory

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { PlexInventoryApp() }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlexInventoryApp() {
    val client = remember { PlexClient() }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    var token by remember { mutableStateOf("") }
    var servers by remember { mutableStateOf<List<PlexServerRef>>(emptyList()) }
    var selectedServer by remember { mutableStateOf<PlexServerRef?>(null) }
    var baseUrl by remember { mutableStateOf("") }
    var libraries by remember { mutableStateOf<List<PlexLibraryRef>>(emptyList()) }
    var selectedLibraries by remember { mutableStateOf<Set<String>>(emptySet()) }
    var rows by remember { mutableStateOf<List<InventoryRow>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var log by remember { mutableStateOf("Pronto") }
    var profile by remember { mutableStateOf(OutputProfile.SLIM_BUDGET) }
    var durationMode by remember { mutableStateOf(DurationMode.HMS) }
    var writeCsv by remember { mutableStateOf(true) }
    var writeXlsx by remember { mutableStateOf(true) }
    var topNMovies by remember { mutableStateOf("0") }
    var topNShows by remember { mutableStateOf("0") }
    var skipShortClips by remember { mutableStateOf(true) }
    var clipMinSeconds by remember { mutableStateOf("300") }
    var progress by remember { mutableStateOf("") }

    fun headers() = client.columns(profile, durationMode)

    fun writeCsvTo(uri: Uri) {
        runCatching {
            ctx.contentResolver.openOutputStream(uri)?.use { out ->
                out.write(client.toCsv(rows, headers()).toByteArray(Charsets.UTF_8))
            } ?: error("Impossibile aprire il file CSV scelto")
        }.onSuccess {
            log = "CSV salvato nel percorso scelto."
        }.onFailure {
            log = it.stackTraceToString()
        }
    }

    fun writeXlsxTo(uri: Uri) {
        runCatching {
            ctx.contentResolver.openOutputStream(uri)?.use { out ->
                XlsxWriter.write(out, headers(), client.xlsxRows(rows, headers()))
            } ?: error("Impossibile aprire il file XLSX scelto")
        }.onSuccess {
            log = "XLSX salvato nel percorso scelto."
        }.onFailure {
            log = it.stackTraceToString()
        }
    }

    val csvPicker = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { uri ->
        if (uri != null) writeCsvTo(uri) else log = "Salvataggio CSV annullato."
    }

    val xlsxPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    ) { uri ->
        if (uri != null) writeXlsxTo(uri) else log = "Salvataggio XLSX annullato."
    }

    MaterialTheme {
        Surface(Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier
                    .padding(16.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text("Plex Inventory Android", style = MaterialTheme.typography.headlineSmall)

                OutlinedTextField(
                    value = token,
                    onValueChange = { token = it },
                    label = { Text("X-Plex-Token") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = token.isNotBlank() && !busy, onClick = {
                        scope.launch {
                            busy = true
                            log = "Carico server..."
                            runCatching { withContext(Dispatchers.IO) { client.listServers(token) } }
                                .onSuccess { servers = it; log = "Server trovati: ${it.size}" }
                                .onFailure { log = it.stackTraceToString() }
                            busy = false
                        }
                    }) { Text("Carica server") }

                    Button(enabled = selectedServer != null && !busy, onClick = {
                        scope.launch {
                            busy = true
                            log = "Carico librerie..."
                            runCatching { withContext(Dispatchers.IO) { client.listLibraries(selectedServer!!, token) } }
                                .onSuccess { (base, libs) ->
                                    baseUrl = base
                                    libraries = libs
                                    selectedLibraries = libs.map { it.key }.toSet()
                                    log = "Librerie trovate: ${libs.size}"
                                }
                                .onFailure { log = it.stackTraceToString() }
                            busy = false
                        }
                    }) { Text("Carica librerie") }
                }

                if (busy) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                    if (progress.isNotBlank()) Text(progress)
                }

                if (servers.isNotEmpty()) {
                    Text("Server", style = MaterialTheme.typography.titleMedium)
                    servers.forEach { srv ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(srv.name, modifier = Modifier.weight(1f))
                            RadioButton(selected = selectedServer == srv, onClick = { selectedServer = srv })
                        }
                    }
                }

                if (libraries.isNotEmpty()) {
                    Text("Librerie", style = MaterialTheme.typography.titleMedium)
                    libraries.forEach { lib ->
                        val checked = lib.key in selectedLibraries
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("${lib.title} (${lib.type})", modifier = Modifier.weight(1f))
                            Checkbox(checked = checked, onCheckedChange = {
                                selectedLibraries = if (checked) selectedLibraries - lib.key else selectedLibraries + lib.key
                            })
                        }
                    }

                    Text("Opzioni", style = MaterialTheme.typography.titleMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(selected = profile == OutputProfile.SLIM_BUDGET, onClick = { profile = OutputProfile.SLIM_BUDGET }, label = { Text("SLIM_BUDGET") })
                        FilterChip(selected = profile == OutputProfile.SLIM_RAW, onClick = { profile = OutputProfile.SLIM_RAW }, label = { Text("SLIM_RAW") })
                        FilterChip(selected = profile == OutputProfile.FULL, onClick = { profile = OutputProfile.FULL }, label = { Text("FULL") })
                        FilterChip(selected = durationMode == DurationMode.BOTH, onClick = { durationMode = if (durationMode == DurationMode.BOTH) DurationMode.HMS else DurationMode.BOTH }, label = { Text("Durata BOTH") })
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(selected = writeCsv, onClick = { writeCsv = !writeCsv }, label = { Text("CSV") })
                        FilterChip(selected = writeXlsx, onClick = { writeXlsx = !writeXlsx }, label = { Text("XLSX") })
                        FilterChip(selected = skipShortClips, onClick = { skipShortClips = !skipShortClips }, label = { Text("Salta clip brevi") })
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(value = topNMovies, onValueChange = { topNMovies = it }, label = { Text("TOP_N_MOVIES") }, modifier = Modifier.weight(1f), singleLine = true)
                        OutlinedTextField(value = topNShows, onValueChange = { topNShows = it }, label = { Text("TOP_N_SHOWS") }, modifier = Modifier.weight(1f), singleLine = true)
                        OutlinedTextField(value = clipMinSeconds, onValueChange = { clipMinSeconds = it }, label = { Text("Clip s") }, modifier = Modifier.weight(1f), singleLine = true)
                    }

                    Button(enabled = !busy && selectedLibraries.isNotEmpty(), onClick = {
                        scope.launch {
                            busy = true
                            log = "Genero inventario..."
                            progress = ""
                            val chosen = libraries.filter { it.key in selectedLibraries }
                            val options = InventoryOptions(
                                profile = profile,
                                durationMode = durationMode,
                                topNMovies = topNMovies.toIntOrNull() ?: 0,
                                topNShows = topNShows.toIntOrNull() ?: 0,
                                skipShortClips = skipShortClips,
                                clipMinSeconds = clipMinSeconds.toIntOrNull() ?: 300,
                            )
                            runCatching {
                                withContext(Dispatchers.IO) {
                                    client.inventory(baseUrl, selectedServer!!.accessToken ?: token, chosen, options) { done, total, label ->
                                        scope.launch { progress = "$done/$total $label" }
                                    }
                                }
                            }
                                .onSuccess { rows = it; log = "Righe create: ${it.size}" }
                                .onFailure { log = it.stackTraceToString() }
                            busy = false
                        }
                    }) { Text("Avvia inventario") }
                }

                if (rows.isNotEmpty()) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(enabled = writeCsv, onClick = {
                            csvPicker.launch("plex_inventory_android.csv")
                        }) { Text("Salva CSV...") }
                        Button(enabled = writeXlsx, onClick = {
                            xlsxPicker.launch("plex_inventory_android.xlsx")
                        }) { Text("Salva XLSX...") }
                    }
                    Text("Anteprima")
                    Text(rows.take(8).joinToString("\n") { r ->
                        listOf(r.get("title_or_series"), r.get("resolution"), r.get("hdr"), r.get("bitrate_mbps_total"), r.get("size_gib")).filter { it.isNotBlank() }.joinToString(" | ")
                    })
                }

                Text("Log", style = MaterialTheme.typography.titleMedium)
                Text(log)
            }
        }
    }
}
