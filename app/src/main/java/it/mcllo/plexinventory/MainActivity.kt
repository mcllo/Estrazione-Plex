package it.mcllo.plexinventory

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
import java.io.File

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { PlexInventoryApp() }
    }
}

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

                if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())

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
                    Button(enabled = !busy && selectedLibraries.isNotEmpty(), onClick = {
                        scope.launch {
                            busy = true
                            log = "Genero inventario base..."
                            val chosen = libraries.filter { it.key in selectedLibraries }
                            runCatching { withContext(Dispatchers.IO) { client.inventory(baseUrl, selectedServer!!.accessToken ?: token, chosen) } }
                                .onSuccess { rows = it; log = "Righe create: ${it.size}" }
                                .onFailure { log = it.stackTraceToString() }
                            busy = false
                        }
                    }) { Text("Avvia inventario") }
                }

                if (rows.isNotEmpty()) {
                    Button(onClick = {
                        val file = File(ctx.getExternalFilesDir(null), "plex_inventory_android.csv")
                        file.writeText(client.toCsv(rows))
                        log = "CSV salvato: ${file.absolutePath}"
                    }) { Text("Salva CSV") }
                    Text("Anteprima: ${rows.take(5).joinToString("\n") { it.title + " - " + it.resolution }}")
                }

                Text("Log", style = MaterialTheme.typography.titleMedium)
                Text(log)
            }
        }
    }
}
