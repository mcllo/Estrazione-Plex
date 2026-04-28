package it.mcllo.plexinventory

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.StringReader
import java.net.URLEncoder
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.TimeUnit

data class PlexServerRef(val name: String, val accessToken: String?, val connections: List<String>)
data class PlexLibraryRef(val title: String, val type: String, val key: String)

data class InventoryOptions(
    val profile: OutputProfile = OutputProfile.SLIM_BUDGET,
    val durationMode: DurationMode = DurationMode.HMS,
    val topNMovies: Int = 0,
    val topNShows: Int = 0,
    val skipShortClips: Boolean = true,
    val clipMinSeconds: Int = 300,
)

enum class OutputProfile { SLIM_BUDGET, SLIM_RAW, FULL }
enum class DurationMode { HMS, BOTH }

data class InventoryRow(val values: Map<String, String>) {
    fun get(name: String) = values[name].orEmpty()
}

class PlexClient {
    private val http = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .build()

    private val milan = ZoneId.of("Europe/Rome")
    private val dateFmt = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss").withZone(milan)

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
                            p.attr("uri").trimEnd('/').takeIf { it.isNotBlank() }?.let(conns::add)
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
                if (type == "movie" || type == "show") libs += PlexLibraryRef(p.attr("title"), type, p.attr("key"))
            }
        }
        return base to libs
    }

    fun inventory(
        base: String,
        token: String,
        libraries: List<PlexLibraryRef>,
        options: InventoryOptions = InventoryOptions(),
        onProgress: (done: Int, total: Int, label: String) -> Unit = { _, _, _ -> },
    ): List<InventoryRow> {
        val jobs = mutableListOf<Pair<PlexLibraryRef, Item>>()
        for (lib in libraries) {
            val xml = get("$base/library/sections/${lib.key}/all?X-Plex-Token=${enc(token)}")
            val parsed = parseItems(xml, lib.type)
            val limited = when (lib.type) {
                "movie" -> if (options.topNMovies > 0) parsed.take(options.topNMovies) else parsed
                "show" -> if (options.topNShows > 0) parsed.take(options.topNShows) else parsed
                else -> parsed
            }
            jobs += limited.map { lib to it }
        }

        val rows = mutableListOf<InventoryRow>()
        jobs.forEachIndexed { idx, (lib, item) ->
            onProgress(idx, jobs.size, item.title)
            val detail = runCatching {
                get("$base/library/metadata/${item.ratingKey}?includeAllStreams=1&includeGuids=1&X-Plex-Token=${enc(token)}")
            }.getOrDefault("")
            rows += parseDetail(detail, item, lib.type, options)
        }
        onProgress(jobs.size, jobs.size, "Completato")
        return rows
    }

    fun columns(profile: OutputProfile, durationMode: DurationMode): List<String> {
        val slim = mutableListOf(
            "type", "title_or_series", "season", "episode", "episode_title", "year",
            "added_at_milan", "resolution", "hdr", "videoCodec", "container",
            "bitrate_mbps_total", "bitrate_mbps_video",
            "audio_it_bitrate_mbps", "audio_it_quality", "audio_en_bitrate_mbps", "audio_en_quality",
            "size_gib", "imdb_id", "imdb_rating", "tmdb_id", "rating_key", "genres", "file"
        )
        val fullExtras = listOf(
            "bitrate_total_source", "bitrate_mbps_video_est", "bitrate_mbps_video_final",
            "audio_bitrate_total_mbps_raw", "secondary_video_mbps_raw", "container_overhead_mbps_raw",
            "audio_bitrate_total_mbps", "secondary_video_mbps", "container_overhead_mbps"
        )
        val out = if (profile == OutputProfile.FULL) {
            val idx = slim.indexOf("audio_it_bitrate_mbps")
            (slim.take(idx) + fullExtras + slim.drop(idx)).toMutableList()
        } else slim
        val pos = out.indexOf("bitrate_mbps_total")
        if (durationMode == DurationMode.BOTH) {
            out.add(pos, "duration_s")
            out.add(pos, "duration_hms")
        } else out.add(pos, "duration_hms")
        return out
    }

    fun toCsv(rows: List<InventoryRow>, headers: List<String>): String = buildString {
        appendLine(headers.joinToString(","))
        rows.forEach { row -> appendLine(headers.joinToString(",") { csv(row.get(it)) }) }
    }

    fun xlsxRows(rows: List<InventoryRow>, headers: List<String>): List<List<String>> = rows.map { row -> headers.map { row.get(it) } }

    private data class Item(
        val ratingKey: String,
        val title: String,
        val year: String,
        val addedAt: String,
        val parentIndex: String = "",
        val index: String = "",
        val grandparentTitle: String = "",
        val episodeTitle: String = "",
    )

    private data class StreamInfo(val attrs: Map<String, String>) {
        val type = attrs["streamType"]?.toIntOrNull()
        val bitrateMbps = attrs["bitrate"]?.toDoubleOrNull()?.div(1000.0)
        val lang = listOf(attrs["languageCode"], attrs["language"], attrs["title"], attrs["displayTitle"], attrs["extendedDisplayTitle"]).joinToString(" ").lowercase()
        val codec = attrs["codec"].orEmpty()
        val hdrText = attrs.values.joinToString(" ").lowercase()
    }

    private fun parseItems(xml: String, type: String): List<Item> {
        val out = mutableListOf<Item>()
        parse(xml) { p ->
            val tag = if (type == "show") "Directory" else "Video"
            if (p.name == tag) out += Item(
                ratingKey = p.attr("ratingKey"),
                title = p.attr("title"),
                year = p.attr("year"),
                addedAt = p.attr("addedAt"),
                parentIndex = p.attr("parentIndex"),
                index = p.attr("index"),
                grandparentTitle = p.attr("grandparentTitle"),
                episodeTitle = p.attr("title")
            )
        }
        return out.filter { it.ratingKey.isNotBlank() }
    }

    private fun parseDetail(xml: String, item: Item, libType: String, options: InventoryOptions): List<InventoryRow> {
        val out = mutableListOf<InventoryRow>()
        var meta = mapOf<String, String>()
        var media = mapOf<String, String>()
        val streams = mutableListOf<StreamInfo>()
        val guids = mutableMapOf<String, String>()
        val genres = linkedSetOf<String>()
        var imdbRating = ""

        parse(xml) { p ->
            when (p.name) {
                "Video", "Directory" -> if (p.attr("ratingKey") == item.ratingKey || meta.isEmpty()) meta = p.attrs()
                "Guid" -> {
                    val id = p.attr("id")
                    if (id.startsWith("imdb://")) guids["imdb_id"] = id.removePrefix("imdb://")
                    if (id.startsWith("tmdb://")) guids["tmdb_id"] = id.removePrefix("tmdb://")
                }
                "Rating" -> if ("imdb" in p.attr("image").lowercase()) imdbRating = p.attr("value")
                "Genre" -> p.attr("tag").ifBlank { p.attr("title") }.takeIf { it.isNotBlank() }?.let(genres::add)
                "Media" -> media = p.attrs()
                "Stream" -> streams += StreamInfo(p.attrs())
                "Part" -> {
                    val part = p.attrs()
                    val durationMs = (part["duration"] ?: media["duration"] ?: meta["duration"]).toLongOrNull()
                    val durationS = durationMs?.div(1000L)
                    val container = part["container"].orEmpty().ifBlank { media["container"].orEmpty() }
                    if (options.skipShortClips && durationS != null && durationS < options.clipMinSeconds && container.lowercase() in setOf("ts", "m2ts", "m2t", "mpegts")) return@parse
                    out += buildRow(item, libType, meta, media, part, streams.toList(), guids, genres, imdbRating, durationS, options)
                    streams.clear()
                }
            }
        }
        return out
    }

    private fun buildRow(
        item: Item,
        libType: String,
        meta: Map<String, String>,
        media: Map<String, String>,
        part: Map<String, String>,
        streams: List<StreamInfo>,
        guids: Map<String, String>,
        genres: Set<String>,
        imdbRating: String,
        durationS: Long?,
        options: InventoryOptions,
    ): InventoryRow {
        val video = streams.firstOrNull { it.type == 1 }
        val audios = streams.filter { it.type == 2 }
        val totalMbps = media["bitrate"]?.toDoubleOrNull()?.div(1000.0)
        val videoMbps = video?.bitrateMbps
        val audioTotal = audios.mapNotNull { it.bitrateMbps }.sum().takeIf { it > 0.0 }
        val itAudio = bestLang(audios, setOf(" it ", "ita", "italian", "italiano"))
        val enAudio = bestLang(audios, setOf(" en ", "eng", "english", "inglese"))
        val sizeGiB = part["size"]?.toDoubleOrNull()?.div(1024.0 * 1024.0 * 1024.0)
        val file = part["file"].orEmpty()
        val overhead = estimateOverhead(media["container"].orEmpty(), audios.size, streams.count { it.type == 3 })
        val videoEst = listOfNotNull(totalMbps, audioTotal, overhead).takeIf { totalMbps != null }?.let { totalMbps!! - (audioTotal ?: 0.0) - overhead }
        val videoFinal = videoMbps ?: videoEst
        val season = meta["parentIndex"].orEmpty().ifBlank { item.parentIndex }
        val episode = meta["index"].orEmpty().ifBlank { item.index }
        val isMovie = libType == "movie"

        val values = linkedMapOf<String, String>()
        values["type"] = if (isMovie) "Movie" else "TV"
        values["title_or_series"] = if (isMovie) item.title else meta["grandparentTitle"].orEmpty().ifBlank { item.grandparentTitle.ifBlank { item.title } }
        values["season"] = if (isMovie) "" else season
        values["episode"] = if (isMovie) "" else episode
        values["episode_title"] = if (isMovie) "" else meta["title"].orEmpty().ifBlank { item.episodeTitle }
        values["year"] = meta["year"].orEmpty().ifBlank { item.year }
        values["added_at_milan"] = formatAdded(meta["addedAt"].orEmpty().ifBlank { item.addedAt })
        values["resolution"] = normResolution(media["videoResolution"].orEmpty())
        values["hdr"] = detectHdr(media, video, file)
        values["videoCodec"] = media["videoCodec"].orEmpty().ifBlank { video?.codec.orEmpty() }
        values["container"] = media["container"].orEmpty().ifBlank { part["container"].orEmpty() }
        values["duration_hms"] = durationS?.let(::hms).orEmpty()
        values["duration_s"] = durationS?.toString().orEmpty()
        values["bitrate_mbps_total"] = fmt(totalMbps)
        values["bitrate_total_source"] = if (totalMbps != null) "media.bitrate" else ""
        values["bitrate_mbps_video"] = fmt(videoMbps)
        values["bitrate_mbps_video_est"] = fmt(videoEst)
        values["bitrate_mbps_video_final"] = fmt(videoFinal)
        values["audio_bitrate_total_mbps_raw"] = fmt(audioTotal)
        values["secondary_video_mbps_raw"] = ""
        values["container_overhead_mbps_raw"] = fmt(overhead)
        values["audio_bitrate_total_mbps"] = fmt(audioTotal)
        values["secondary_video_mbps"] = ""
        values["container_overhead_mbps"] = fmt(overhead)
        values["audio_it_bitrate_mbps"] = fmt(itAudio?.bitrateMbps)
        values["audio_it_quality"] = audioQuality(itAudio)
        values["audio_en_bitrate_mbps"] = fmt(enAudio?.bitrateMbps)
        values["audio_en_quality"] = audioQuality(enAudio)
        values["size_gib"] = fmt(sizeGiB, 2)
        values["imdb_id"] = guids["imdb_id"].orEmpty()
        values["imdb_rating"] = imdbRating
        values["tmdb_id"] = guids["tmdb_id"].orEmpty()
        values["rating_key"] = item.ratingKey
        values["genres"] = genres.joinToString("|")
        values["file"] = file
        return InventoryRow(values)
    }

    private fun bestLang(audios: List<StreamInfo>, needles: Set<String>): StreamInfo? = audios
        .filter { a -> needles.any { n -> " ${a.lang} ".contains(n) } }
        .maxByOrNull { it.bitrateMbps ?: 0.0 }

    private fun audioQuality(a: StreamInfo?): String {
        if (a == null) return ""
        val c = a.codec.lowercase()
        val b = a.bitrateMbps ?: 0.0
        return when {
            c.contains("truehd") || c.contains("dca") || c.contains("dts") && b >= 1.5 -> "lossless/high"
            b >= 0.64 -> "high"
            b >= 0.32 -> "medium"
            b > 0.0 -> "low"
            else -> c
        }
    }

    private fun detectHdr(media: Map<String, String>, video: StreamInfo?, file: String): String {
        val text = (media.values.joinToString(" ") + " " + video?.hdrText + " " + file).lowercase()
        return when {
            "dolby vision" in text || "dovi" in text || " dv " in " $text " -> "DV"
            "hdr10+" in text -> "HDR10+"
            "hdr10" in text || " hdr " in " $text " -> "HDR10"
            "hlg" in text -> "HLG"
            "sdr" in text -> "SDR"
            else -> ""
        }
    }

    private fun estimateOverhead(container: String, audioCount: Int, subtitleCount: Int): Double {
        val base = when (container.lowercase()) {
            "mkv" -> 0.20
            "mp4", "m4v" -> 0.15
            "m2ts", "m2t", "ts", "mpegts" -> 1.00
            else -> 0.20
        }
        return base + subtitleCount * 0.005 + maxOf(0, audioCount - 1) * 0.07
    }

    private fun firstWorkingBase(server: PlexServerRef, accountToken: String): String {
        val token = server.accessToken ?: accountToken
        val candidates = server.connections.sortedWith(compareBy<String> { if (it.startsWith("http://")) 0 else 1 }.thenBy { if (it.contains("plex.direct")) 1 else 0 })
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
        if (xml.isBlank()) return
        val parser = XmlPullParserFactory.newInstance().newPullParser()
        parser.setInput(StringReader(xml))
        while (parser.next() != XmlPullParser.END_DOCUMENT) {
            if (parser.eventType == XmlPullParser.START_TAG) onStart(parser)
        }
    }

    private fun normResolution(s: String) = when (s.lowercase()) {
        "2160", "2160p", "4k", "uhd" -> "2160p"
        "1440", "1440p", "qhd" -> "1440p"
        "1080", "1080p", "fhd" -> "1080p"
        "720", "720p", "hd" -> "720p"
        "sd" -> "SD"
        else -> s.uppercase()
    }

    private fun formatAdded(v: String): String = v.toLongOrNull()?.let { dateFmt.format(Instant.ofEpochSecond(it)) }.orEmpty()
    private fun hms(seconds: Long): String = "%02d:%02d:%02d".format(seconds / 3600, seconds % 3600 / 60, seconds % 60)
    private fun fmt(v: Double?, digits: Int = 3): String = v?.takeIf { it.isFinite() }?.let { String.format(Locale.US, "%.${digits}f", it) }.orEmpty()
    private fun XmlPullParser.attr(name: String): String = getAttributeValue(null, name).orEmpty()
    private fun XmlPullParser.attrs(): Map<String, String> = (0 until attributeCount).associate { getAttributeName(it) to getAttributeValue(it) }
    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
    private fun csv(s: String): String = "\"" + s.replace("\"", "\"\"") + "\""
}
