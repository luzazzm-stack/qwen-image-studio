package app.qwenimagestudio.personal

import android.content.Context
import android.content.Intent
import android.content.IntentSender
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.widget.Toast
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job as CJob
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID

enum class Power { CHECKING, OFF, STARTING, READY, STOPPING, ERROR }
enum class Tab { CREATE, EDIT, IMAGES, SETTINGS }

/** A job made while the server is off (or could not be sent yet). Sent in order once the server is ready. */
data class OutItem(val id: String, val body: JSONObject, val imagePath: String?, val created: Long, val note: String? = null) {
    fun toJson(): JSONObject = JSONObject().put("id", id).put("body", body).put("image", imagePath).put("created", created).put("note", note)

    companion object {
        fun from(j: JSONObject) = OutItem(j.getString("id"), j.getJSONObject("body"),
            j.optString("image").ifBlank { null }?.takeIf { it != "null" }, j.optLong("created"),
            j.optString("note").ifBlank { null }?.takeIf { it != "null" })
    }
}

data class LocalFail(val id: String, val label: String, val error: String, val blocked: Boolean)
data class Notice(val title: String, val text: String)

class Studio private constructor(private val ctx: Context, val acc: Account) {
    private val kaggle = Kaggle(acc)
    private val lib = Library(ctx)
    private val prefs = ctx.getSharedPreferences("qis", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    // ---------------------------------------------------------------- state the UI reads
    var tab by mutableStateOf(Tab.CREATE)
    var power by mutableStateOf(Power.CHECKING)
    var powerDetail by mutableStateOf("Checking the server...")
    var url by mutableStateOf<String?>(null)
    var health by mutableStateOf<Health?>(null)
    var quota by mutableStateOf<Kaggle.Quota?>(null)
    val jobs = mutableStateListOf<Job>()
    val outbox = mutableStateListOf<OutItem>()
    val fails = mutableStateListOf<LocalFail>()
    val library = mutableStateListOf<Saved>()
    var hidden by mutableStateOf(prefs.getStringSet("hidden", emptySet())!!.toSet())
    var notice by mutableStateOf<Notice?>(null)
    var busy by mutableStateOf(false)            // power button in progress
    var improving by mutableStateOf<String?>(null) // job id of the running prompt helper
    var serverStartedAt by mutableStateOf(0L)

    // Create form
    var prompt by mutableStateOf(prefs.getString("prompt", "")!!)
    var negative by mutableStateOf(prefs.getString("negative", "")!!)
    var seedText by mutableStateOf("")
    var aspect by mutableStateOf(prefs.getString("aspect", "1:1")!!)
    var quality by mutableStateOf(prefs.getString("quality", "turbo")!!)
    var count by mutableStateOf(prefs.getInt("count", 1))
    // Edit form
    var editPrompt by mutableStateOf("")
    var editImage by mutableStateOf<Uri?>(null)
    var editCount by mutableStateOf(1)
    // Settings
    var nsfw by mutableStateOf(prefs.getBoolean("nsfw", false))
    var nsfwStrength by mutableStateOf(prefs.getFloat("nsfw_strength", 1f))
    var idleMin by mutableStateOf(prefs.getInt("idle_min", acc.idleMin))
    var maxHours by mutableStateOf(prefs.getInt("max_hours", acc.maxHours.toInt()))

    private var pushedAt = prefs.getLong("pushed_at", 0L)
    private var healthFail = 0
    private var lastStatusCheck = 0L
    private var lastQuota = 0L
    private val deadUrls = prefs.getStringSet("dead_urls", emptySet())!!.toMutableSet()
    private val saving = mutableSetOf<String>()
    private val sent = HashMap<String, OutItem>()   // jobId -> request, to requeue if the server stops first
    private var loop: CJob? = null
    var deleteLauncher: ((IntentSender, Saved) -> Unit)? = null

    init {
        library.addAll(lib.load())
        prefs.getString("outbox", null)?.let { s -> runCatching { JSONArray(s).let { a -> (0 until a.length()).forEach { outbox.add(OutItem.from(a.getJSONObject(it))) } } } }
        prefs.getString("sent", null)?.let { s -> runCatching { JSONObject(s).let { o -> o.keys().forEach { k -> sent[k] = OutItem.from(o.getJSONObject(k)) } } } }
    }

    fun savePrefs() {
        prefs.edit().putString("prompt", prompt).putString("negative", negative).putString("aspect", aspect)
            .putString("quality", quality).putInt("count", count).putBoolean("nsfw", nsfw).putFloat("nsfw_strength", nsfwStrength)
            .putInt("idle_min", idleMin).putInt("max_hours", maxHours).putStringSet("hidden", hidden)
            .putLong("pushed_at", pushedAt).putStringSet("dead_urls", deadUrls.toList().takeLast(20).toSet())
            .putString("outbox", JSONArray().apply { outbox.forEach { put(it.toJson()) } }.toString())
            .putString("sent", JSONObject().apply { sent.forEach { (k, v) -> put(k, v.toJson()) } }.toString())
            .apply()
    }

    private fun toast(s: String) = Toast.makeText(ctx, s, Toast.LENGTH_SHORT).show()
    private fun gw() = url?.let { Gateway(it, acc.key) }
    private suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) { block() }

    // ---------------------------------------------------------------- lifecycle
    fun start() {
        if (loop?.isActive == true) return
        loop = scope.launch {
            while (isActive) {
                runCatching { tick() }.onFailure { if (power == Power.CHECKING) { power = Power.OFF; powerDetail = "Could not check the server: ${it.message}" } }
                delay(if (power == Power.READY || power == Power.STARTING) 4000 else 8000)
            }
        }
    }

    fun stop() {
        loop?.cancel()
        savePrefs()
    }

    // ---------------------------------------------------------------- power
    private fun up(st: String) = st == "running" || st == "queued"

    private suspend fun refreshQuota(force: Boolean = false) {
        if (!force && System.currentTimeMillis() - lastQuota < 5 * 60_000) return
        lastQuota = System.currentTimeMillis()
        runCatching { io { kaggle.quota() } }.onSuccess { quota = it }
    }

    private suspend fun tick() {
        refreshQuota()
        if (power == Power.STOPPING || busy) return
        val now = System.currentTimeMillis() / 1000
        if (url == null) {
            val since = if (pushedAt > 0 && power == Power.STARTING) pushedAt - 5 else now - 12 * 3600
            val v = runCatching { io { Ntfy.verdict(Ntfy.fetch(acc.topic, since)) } }.getOrNull()
            if (v?.url != null && v.url !in deadUrls) {
                url = v.url
                healthFail = 0
            } else if (v?.error != null && power == Power.STARTING && pushedAt > 0) {
                fail("The Kaggle server failed to start: ${v.error}")
                return
            } else {
                if (power == Power.STARTING) {
                    val waited = now - pushedAt
                    powerDetail = "Waiting for Kaggle to give us 2 GPUs (${waited} s)"
                    if (System.currentTimeMillis() - lastStatusCheck > 60_000) {
                        lastStatusCheck = System.currentTimeMillis()
                        val st = runCatching { io { kaggle.status() } }.getOrDefault("unknown")
                        if (waited > 900 && !up(st) && st != "unknown") fail("The Kaggle session did not start (status: $st).")
                    }
                } else if (System.currentTimeMillis() - lastStatusCheck > 30_000 || power == Power.CHECKING) {
                    lastStatusCheck = System.currentTimeMillis()
                    val st = runCatching { io { kaggle.status() } }.getOrDefault("unknown")
                    if (up(st)) {
                        power = Power.STARTING
                        if (pushedAt == 0L) pushedAt = now
                        powerDetail = "Starting on Kaggle (turned on from another device)"
                    } else if (power != Power.ERROR) {
                        power = Power.OFF
                        powerDetail = if (st == "unknown") "Could not reach Kaggle" else "Server off"
                    }
                }
                return
            }
        }
        val g = gw() ?: return
        try {
            val h = io { g.health() }
            healthFail = 0
            health = h
            if (h.error != null) {
                fail("The Kaggle server failed to start: ${h.error.take(600)}")
                runCatching { io { kaggle.delete() } }
                return
            }
            if (h.ready) {
                if (power != Power.READY) { power = Power.READY; serverStartedAt = now - h.uptime }
                powerDetail = "Ready"
                sync(g)
            } else {
                power = Power.STARTING
                powerDetail = h.detail.ifBlank { "Starting: ${h.stage}" }
            }
        } catch (e: Exception) {
            healthFail++
            if (power == Power.STARTING) powerDetail = "Connecting to the Kaggle server..."
            if (power == Power.CHECKING || healthFail == 2 || healthFail % 10 == 0) {
                val st = runCatching { io { kaggle.status() } }.getOrDefault("unknown")
                if (st != "unknown" && !up(st)) {
                    gone("Server off")
                    return
                }
                if (power == Power.CHECKING) { power = Power.STARTING; powerDetail = "Connecting to the Kaggle server..." }
            }
            if (healthFail >= 3) {   // maybe a new tunnel address
                val v = runCatching { io { Ntfy.verdict(Ntfy.fetch(acc.topic, now - 12 * 3600)) } }.getOrNull()
                if (v?.url != null && v.url != url) { url = v.url; healthFail = 0 }
            }
            if (healthFail > 40) fail("Lost contact with the Kaggle server: ${e.message}")
        }
    }

    private fun fail(msg: String) {
        power = Power.ERROR
        powerDetail = msg
        url?.let { deadUrls += it }
        url = null
        savePrefs()
    }

    /** The session ended (turned off here, elsewhere, or by itself). */
    private fun gone(detail: String) {
        url?.let { deadUrls += it }
        requeueUnfinished("The server stopped before this finished; it will run next time.")
        url = null
        health = null
        jobs.clear()
        power = Power.OFF
        powerDetail = detail
        pushedAt = 0
        savePrefs()
    }

    private fun requeueUnfinished(note: String) {
        val activeIds = jobs.filter { it.active }.map { it.id }.toSet()
        for ((id, item) in sent) if (id in activeIds) outbox.add(item.copy(id = "o" + UUID.randomUUID().toString().take(9), note = note))
        sent.clear()
    }

    fun powerOn() = scope.launch {
        if (power == Power.READY || power == Power.STARTING || busy) return@launch
        busy = true
        power = Power.STARTING
        powerDetail = "Sending the server to Kaggle"
        try {
            val st = io { kaggle.status() }
            if (up(st)) {
                powerDetail = "Already starting on Kaggle"   // started from the laptop: join it, don't replace it
                if (pushedAt == 0L) pushedAt = System.currentTimeMillis() / 1000 - 600
            } else {
                val src = io { kernelSource() }
                io { kaggle.push(src) }
                pushedAt = System.currentTimeMillis() / 1000
                url = null
                jobs.clear()
                powerDetail = "Waiting for Kaggle to give us 2 GPUs"
            }
            lastStatusCheck = System.currentTimeMillis()
            savePrefs()
            refreshQuota(true)
        } catch (e: Exception) {
            val m = e.message ?: ""
            fail(if (m.contains("phone", true) || m.contains("verif", true))
                "Kaggle gives GPUs only to accounts with a verified phone number. Verify yours at kaggle.com/settings, then try again. ($m)"
            else "Could not start on Kaggle: $m")
        } finally {
            busy = false
        }
    }

    fun powerOff() = scope.launch {
        if (busy) return@launch
        busy = true
        power = Power.STOPPING
        powerDetail = "Turning off"
        try {
            gw()?.let { g ->
                runCatching { sync(g) }
                io { g.shutdown() }
            }
            delay(3000)
            runCatching { io { kaggle.delete() } }.onFailure { toast("Could not delete the Kaggle session: ${it.message}") }
        } finally {
            gone("Turned off from the phone")
            busy = false
            refreshQuota(true)
        }
    }

    fun kernelSource(): String {
        fun asset(n: String) = ctx.assets.open("qis/$n").bufferedReader(Charsets.UTF_8).use { it.readText() }
        return KernelTemplate.fill(asset("qis_server.py"), asset("pe_t2i_system.txt"), asset("pe_i2i_system.txt"),
            acc.key, acc.topic, idleMin + 5, maxHours.toDouble(), acc.nsfwRepo, acc.nsfwFile)
    }

    // ---------------------------------------------------------------- queue
    private suspend fun sync(g: Gateway) {
        val list = io { g.jobs(0.0) }
        jobs.clear()
        jobs.addAll(list.jobs)
        list.status?.let { health = it }
        val known = list.jobs.associateBy { it.id }
        sent.keys.retainAll { known[it]?.active == true }
        for (j in list.jobs) if (j.hasImage && j.source == Requests.SOURCE && library.none { it.jobId == j.id } && j.id !in hidden) save(j, quiet = true)
        sendOutbox(g)
    }

    private suspend fun sendOutbox(g: Gateway) {
        while (outbox.isNotEmpty()) {
            val item = outbox.first()
            try {
                val body = JSONObject(item.body.toString())
                item.imagePath?.let { p -> body.put("image", io { upload(g, File(p).readBytes(), File(p).name) }) }
                val made = io { g.submit(body) }
                made.forEach { sent[it.id] = item.copy(body = JSONObject(item.body.toString()).put("count", 1).put("seed", it.params.optLong("seed", -1))) }
                jobs.addAll(made)
                outbox.remove(item)
                item.imagePath?.let { if (outbox.none { o -> o.imagePath == it }) File(it).delete() }
            } catch (e: GatewayError) {
                if (e.code == 400 || e.code == 422) {
                    outbox.remove(item)
                    fails.add(LocalFail(item.id, label(item.body), e.message ?: "Refused", e.blocked))
                } else break
            } catch (e: Exception) {
                break
            }
        }
        savePrefs()
    }

    private fun upload(g: Gateway, bytes: ByteArray, name: String): String {
        val mime = when (name.substringAfterLast('.').lowercase()) { "png" -> "image/png"; "webp" -> "image/webp"; else -> "image/jpeg" }
        return g.upload(bytes, name, mime)
    }

    fun label(body: JSONObject): String = when (body.optString("kind")) {
        "rembg" -> "Remove background"
        "edit" -> "Edit: " + body.optString("prompt")
        else -> body.optString("prompt")
    }

    /** Sends now if the server is ready, else keeps it in the outbox. */
    private fun enqueue(body: JSONObject, imagePath: String?) = scope.launch {
        val item = OutItem("o" + UUID.randomUUID().toString().take(9), body, imagePath, System.currentTimeMillis())
        outbox.add(item)
        savePrefs()
        val g = gw()
        if (power == Power.READY && g != null) {
            val before = fails.size
            sendOutbox(g)
            if (fails.size > before) {
                val f = fails.last()
                notice = Notice(if (f.blocked) "Not allowed" else "Not sent", f.error)
                fails.removeAt(fails.size - 1)
            } else if (outbox.none { it.id == item.id }) toast("Sent. Follow it in My images.")
            else toast("Saved. It will be sent when the server answers.")
        } else toast("Saved. It starts once the server is on.")
    }

    fun generate() {
        val body = try {
            Requests.create(prompt, negative, aspect, quality, count, Requests.parseSeed(seedText), nsfw, nsfwStrength.toDouble())
        } catch (e: IllegalArgumentException) {
            notice = Notice("Create", e.message ?: ""); return
        }
        savePrefs()
        enqueue(body, null)
    }

    fun editPhoto(rembg: Boolean) {
        val img = editImage ?: run { notice = Notice("Edit", "Choose a photo first."); return }
        val body = try {
            if (rembg) Requests.rembg(null) else Requests.edit(editPrompt, null, editCount, -1)
        } catch (e: IllegalArgumentException) {
            notice = Notice("Edit", e.message ?: ""); return
        }
        scope.launch {
            val path = runCatching { io { preparePhoto(img) } }.getOrElse { notice = Notice("Edit", "Could not read that photo: ${it.message}"); return@launch }
            enqueue(body, path)
        }
    }

    /** Copies the photo into app storage: JPEG/PNG/WEBP up to 2048 px are kept as they are, others re-encoded (rotation applied). */
    private fun preparePhoto(uri: Uri): String {
        val cr = ctx.contentResolver
        val bytes = cr.openInputStream(uri)?.use { it.readBytes() } ?: error("empty")
        val mime = cr.getType(uri) ?: ""
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
        if (o.outWidth <= 0) error("not an image")
        val dir = File(ctx.filesDir, "outbox").apply { mkdirs() }
        val id = UUID.randomUUID().toString().take(10)
        val ext = when (mime) { "image/jpeg" -> "jpg"; "image/png" -> "png"; "image/webp" -> "webp"; else -> null }
        if (ext != null && maxOf(o.outWidth, o.outHeight) <= 2048 && bytes.size < 30_000_000) {
            return File(dir, "photo_$id.$ext").apply { writeBytes(bytes) }.absolutePath
        }
        var s = 1
        while (maxOf(o.outWidth, o.outHeight) / (s * 2) >= 2048) s *= 2
        var bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = s }) ?: error("decode failed")
        val rot = runCatching {
            when (ExifInterface(bytes.inputStream()).getAttributeInt(ExifInterface.TAG_ORIENTATION, 1)) {
                ExifInterface.ORIENTATION_ROTATE_90 -> 90f; ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                ExifInterface.ORIENTATION_ROTATE_270 -> 270f; else -> 0f
            }
        }.getOrDefault(0f)
        val scale = 2048f / maxOf(bmp.width, bmp.height)
        val m = Matrix().apply { if (scale < 1f) postScale(scale, scale); if (rot != 0f) postRotate(rot) }
        if (!m.isIdentity) bmp = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.JPEG, 92, out)
        return File(dir, "photo_$id.jpg").apply { writeBytes(out.toByteArray()) }.absolutePath
    }

    fun improve() {
        val g = gw()
        if (power != Power.READY || g == null) {
            notice = Notice("Improve my prompt", "Turn the server on to use the prompt helper (it runs on the Kaggle GPUs)."); return
        }
        val body = try { Requests.improve(prompt, aspect, nsfw) } catch (e: IllegalArgumentException) { notice = Notice("Improve my prompt", e.message ?: ""); return }
        scope.launch {
            try {
                val j = io { g.submit(body) }.first()
                improving = j.id
                val t0 = System.currentTimeMillis()
                while (improving == j.id && System.currentTimeMillis() - t0 < 8 * 60_000) {
                    delay(3000)
                    val cur = runCatching { io { g.job(j.id) } }.getOrNull() ?: continue
                    when (cur.status) {
                        "done" -> { cur.text?.trim()?.takeIf { it.isNotEmpty() }?.let { prompt = it; savePrefs(); toast("Prompt improved") }; break }
                        "failed", "cancelled" -> { if (cur.status == "failed") notice = Notice(if (cur.blocked) "Not allowed" else "Improve my prompt", cur.error ?: "It failed."); break }
                    }
                }
            } catch (e: GatewayError) {
                notice = Notice(if (e.blocked) "Not allowed" else "Improve my prompt", e.message ?: "")
            } catch (e: Exception) {
                notice = Notice("Improve my prompt", "Could not reach the server: ${e.message}")
            } finally {
                improving = null
            }
        }
    }

    fun cancelImprove() {
        val id = improving ?: return
        improving = null
        gw()?.let { g -> scope.launch { runCatching { io { g.cancel(id) } } } }
    }

    fun cancel(job: Job) {
        val g = gw() ?: return
        scope.launch {
            runCatching { io { g.cancel(job.id) } }.onSuccess { c -> jobs.indexOfFirst { it.id == c.id }.takeIf { it >= 0 }?.let { jobs[it] = c } }
                .onFailure { toast("Could not cancel: ${it.message}") }
        }
    }

    fun removeOutbox(item: OutItem) {
        outbox.remove(item)
        item.imagePath?.let { p -> if (outbox.none { it.imagePath == p }) File(p).delete() }
        savePrefs()
    }

    fun dismissFail(f: LocalFail) { fails.remove(f) }

    // ---------------------------------------------------------------- images
    fun imageUrl(job: Job) = gw()?.imageUrl(job.id)

    fun save(job: Job, quiet: Boolean = false, then: ((Saved) -> Unit)? = null) {
        val g = gw() ?: return
        if (!saving.add(job.id)) return
        scope.launch {
            try {
                val bytes = io { g.image(job.id) }
                val uri = io { lib.savePng(bytes, Library.fileName(job)) }
                val s = Saved.fromJob(job, uri)
                library.add(s)
                io { lib.store(library.toList()) }
                if (!quiet) toast("Saved to Pictures/${Library.FOLDER}")
                then?.invoke(s)
            } catch (e: Exception) {
                if (!quiet) toast("Could not save: ${e.message}")
            } finally {
                saving.remove(job.id)
            }
        }
    }

    fun share(s: Saved) {
        val i = Intent(Intent.ACTION_SEND).setType("image/png").putExtra(Intent.EXTRA_STREAM, Uri.parse(s.uri))
            .putExtra(Intent.EXTRA_TEXT, s.prompt).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        ctx.startActivity(Intent.createChooser(i, "Share image").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun reuse(kind: String, p: String, neg: String, asp: String?, q: String?, seed: Long) {
        if (kind == "edit") { editPrompt = p; tab = Tab.EDIT; return }
        prompt = p; negative = neg
        asp?.takeIf { it in Requests.ASPECTS }?.let { aspect = it }
        q?.let { quality = if (it == "quality") "quality" else "turbo" }
        seedText = if (seed >= 0) seed.toString() else ""
        tab = Tab.CREATE
    }

    fun editThis(s: Saved) { editImage = Uri.parse(s.uri); tab = Tab.EDIT }

    fun delete(s: Saved) {
        val uri = Uri.parse(s.uri)
        try {
            lib.delete(uri)
            forget(s)
        } catch (e: SecurityException) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                deleteLauncher?.invoke(MediaStore.createDeleteRequest(ctx.contentResolver, listOf(uri)).intentSender, s)
            } else toast("Android did not allow deleting this picture.")
        } catch (e: Exception) {
            forget(s)
        }
    }

    fun forget(s: Saved) {
        library.removeAll { it.jobId == s.jobId }
        hidden = hidden + s.jobId     // don't download it again from the server
        lib.store(library.toList())
        savePrefs()
    }

    fun hide(job: Job) { hidden = hidden + job.id; savePrefs() }

    /** Removes saved entries whose picture was deleted in the gallery. */
    fun pruneLibrary() = scope.launch {
        val gone = io { library.filter { !lib.exists(Uri.parse(it.uri)) } }
        if (gone.isNotEmpty()) { library.removeAll(gone); io { lib.store(library.toList()) } }
    }

    /** Client app: forgets the Kaggle account on this phone (saved pictures stay). Only while the server is off. */
    var onSignedOut: (() -> Unit)? = null

    fun signOut() {
        if (busy || power == Power.READY || power == Power.STARTING || power == Power.STOPPING) {
            notice = Notice("Turn the server off first", "Signing out while your Kaggle server runs would leave it using your GPU hours.")
            return
        }
        loop?.cancel()
        scope.coroutineContext[CJob]?.cancel()
        outbox.forEach { o -> o.imagePath?.let { File(it).delete() } }
        prefs.edit().remove("pushed_at").remove("dead_urls").remove("outbox").remove("sent").remove("hidden").commit()
        Accounts.clear(ctx)
        synchronized(Companion) { if (inst === this) inst = null }
        onSignedOut?.invoke()
    }

    companion object {
        @Volatile private var inst: Studio? = null

        /** The app for the current account, or null when nobody is signed in (client app only). */
        fun get(ctx: Context): Studio? = inst ?: synchronized(this) {
            inst ?: Accounts.current(ctx.applicationContext)?.let { Studio(ctx.applicationContext, it).also { s -> inst = s } }
        }
    }
}
