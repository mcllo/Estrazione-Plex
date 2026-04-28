package it.mcllo.plexinventory

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.StringReader
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

data class PlexServerRef(val name: String, val accessToken: String?, val connections: List<String>)
data class PlexLibraryRef(val title: String, val type: String, val key: String)
data class InventoryRow(
    val type: String,
    val title: String,
    val year: String,
    val resolution: String,
    val videoCodec: String,
    val container: String,
    val bitrateMbps: String,
    val sizeGiB: String,
    val ratingKey: String,
    val file: String,
)

class PlexClient {
    private val http = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    fun listServers(token: String): List<PlexServerRef> {
        val url = "https://plex.tv/api/resources?includeHttps=1&includeRelay=1&X-Plex-Token=${enc(token)}"
        val xml = get(url)
        val out = mutableListOf<PlexServerRef>()
        parse(xml) { p ->
            if (p.name == "Device") {
                val provides = p.attr("provides").lowercase()
                val product = p.attr("product").lowercase()
                if ("server" in provides || "plex media server" in product) {
                    val name = p.attr("name")
                    val accessToken = p.attr("accessToken").ifBlank { null }
                    val conns = mutableListOf<String>()
                    val depth = p.depth
                    while (p.next() != XmlPullParser.END_DOCUMENT) {
                        if (p.eventType == XmlPullParser.END_TAG && p.name == "Device" && p.depth == depth) break
                        if (p.eventType == XmlPullParser.START_TAG && p.name == "Connection") {
                            p.attr("uri").takeIf { it.isNotBlank() }?.let(conns::add)
                        }
                    }
                    if (name.isNotBlank()) out += PlexServerRef(name, accessToken, conns.distinct())
                }
            }
        }
        return out.sortedBy { it.name.lowercase() }
    }

    fun listLibraries(server: PlexServerRef, accountToken: String): Pair<String, List<PlexLibraryRef>> {
        val base = firstWorkingBase(server, accountToken)
        val xml = get("$base/library/sections?X-Plex-Token=${enc(server.accessToken ?: accountToken)}")
        val libs = mutableListOf<PlexLibraryRef>()
        parse(xml) { p ->
            if (p.name == "Directory") {
                val type = p.attr("type")
                if (type == "movie" || type == "show") {
                    libs += PlexLibraryRef(p.attr("title"), type, p.attr("key"))
                }
            }
        }
        return base to libs
    }

    fun inventory(base: String, token: String, libraries: List<PlexLibraryRef>, limit: Int = 200): List<InventoryRow> {
        val rows = mutableListOf<InventoryRow>()
        for (lib in libraries) {
            val xml = get("$base/library/sections/${lib.key}/all?X-Plex-Token=${enc(token)}")
            val items = parseItems(xml, lib.type).take(limit)
            for (item in items) {
                val detail = runCatching { get("$base/library/metadata/${item.ratingKey}?includeAllStreams=1&X-Plex-Token=${enc(token)}") }.getOrDefault("")
                rows += parseParts(detail, item, lib.type)
            }
        }
        return rows
    }

    fun toCsv(rows: List<InventoryRow>): String {
        val header = listOf("type", "title", "year", "resolution", "videoCodec", "container", "bitrate_mbps", "size_gib", "rating_key", "file")
        return buildString {
            appendLine(header.joinToString(","))
            rows.forEach { r ->
                appendLine(listOf(r.type, r.title, r.year, r.resolution, r.videoCodec, r.container, r.bitrateMbps, r.sizeGiB, r.ratingKey, r.file).joinToString(",") { csv(it) })
            }
        }
    }

    private data class Item(val ratingKey: String, val title: String, val year: String)

    private fun parseItems(xml: String, type: String): List<Item> {
        val out = mutableListOf<Item>()
        parse(xml) { p ->
            val tag = if (type == "show") "Directory" else "Video"
            if (p.name == tag) out += Item(p.attr("ratingKey"), p.attr("title"), p.attr("year"))
        }
        return out.filter { it.ratingKey.isNotBlank() }
    }

    private fun parseParts(xml: String, item: Item, type: String): List<InventoryRow> {
        val out = mutableListOf<InventoryRow>()
        var mediaAttrs = mapOf<String, String>()
        parse(xml) { p ->
            when (p.name) {
                "Media" -> mediaAttrs = p.attrs()
                "Part" -> {
                    val attrs = p.attrs()
                    val sizeBytes = attrs["size"]?.toDoubleOrNull()
                    out += InventoryRow(
                        type = if (type == "movie") "Movie" else "TV",
                        title = item.title,
                        year = item.year,
                        resolution = mediaAttrs["videoResolution"].orEmpty(),
                        videoCodec = mediaAttrs["videoCodec"].orEmpty(),
                        container = mediaAttrs["container"].orEmpty(),
                        bitrateMbps = mediaAttrs["bitrate"]?.toDoubleOrNull()?.div(1000.0)?.let { "%.3f".format(it) }.orEmpty(),
                        sizeGiB = sizeBytes?.div(1024.0 * 1024.0 * 1024.0)?.let { "%.2f".format(it) }.orEmpty(),
                        ratingKey = item.ratingKey,
                        file = attrs["file"].orEmpty(),
                    )
                }
            }
        }
        return out
    }

    private fun firstWorkingBase(server: PlexServerRef, accountToken: String): String {
        val token = server.accessToken ?: accountToken
        val candidates = server.connections.sortedWith(compareBy<String> { if (it.contains("plex.direct")) 1 else 0 }.thenBy { it })
        for (base in candidates) {
            val clean = base.trimEnd('/')
            if (runCatching { get("$clean/library/sections?X-Plex-Token=${enc(token)}") }.isSuccess) return clean
        }
        error("Nessuna connessione Plex funzionante per ${server.name}")
    }

    private fun get(url: String): String {
        val req = Request.Builder()
            .url(url.toHttpUrl())
            .header("X-Plex-Client-Identifier", "PlexInventoryAndroid")
            .header("X-Plex-Product", "Plex Inventory Android")
            .build()
        http.newCall(req).execute().use { res ->
            if (!res.isSuccessful) error("HTTP ${res.code}: $url")
            return res.body?.string().orEmpty()
        }
    }

    private fun parse(xml: String, onStart: (XmlPullParser) -> Unit) {
        val parser = XmlPullParserFactory.newInstance().newPullParser()
        parser.setInput(StringReader(xml))
        while (parser.next() != XmlPullParser.END_DOCUMENT) {
            if (parser.eventType == XmlPullParser.START_TAG) onStart(parser)
        }
    }

    private fun XmlPullParser.attr(name: String): String = getAttributeValue(null, name).orEmpty()
    private fun XmlPullParser.attrs(): Map<String, String> = (0 until attributeCount).associate { getAttributeName(it) to getAttributeValue(it) }
    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
    private fun csv(s: String): String = "\"" + s.replace("\"", "\"\"") + "\""
}
