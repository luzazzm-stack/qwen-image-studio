package app.qwenimagestudio.personal

import org.json.JSONArray
import org.json.JSONObject

/** Refused or failed gateway call. blocked = the minors safety block (HTTP 422). */
class GatewayError(val code: Int, message: String, val blocked: Boolean = false) : Exception(message) {
    companion object {
        fun from(code: Int, body: String): GatewayError {
            val j = runCatching { JSONObject(body) }.getOrNull()
            val msg = j?.optString("error")?.ifBlank { null }
                ?: when (code) {
                    401 -> "The server did not accept this app's key."
                    404 -> "Not found on the server."
                    in 500..599 -> "The server is not answering right now (HTTP $code)."
                    else -> "HTTP $code"
                }
            return GatewayError(code, msg, j?.optBoolean("blocked") == true || code == 422)
        }
    }
}

data class Job(
    val id: String,
    val kind: String,
    val status: String,
    val gpu: Int?,
    val progress: Double,
    val phase: String?,
    val created: Double,
    val started: Double?,
    val finished: Double?,
    val error: String?,
    val note: String?,
    val took: Int?,
    val source: String,
    val text: String?,
    val blocked: Boolean,
    val params: JSONObject,
    val resultW: Int?,
    val resultH: Int?,
    val estimate: Int?,
    val eta: Int?,
) {
    val prompt: String get() = params.optString("prompt")
    val active get() = status in ACTIVE
    val hasImage get() = status == "done" && resultW != null && kind != "improve"

    companion object {
        val ACTIVE = setOf("queued", "submitting", "running")

        private fun JSONObject.str(k: String) = if (isNull(k)) null else optString(k).ifBlank { null }
        private fun JSONObject.dbl(k: String) = if (isNull(k) || !has(k)) null else optDouble(k)
        private fun JSONObject.int(k: String) = if (isNull(k) || !has(k)) null else optInt(k)

        fun parse(j: JSONObject): Job {
            val res = j.optJSONObject("result")
            return Job(
                id = j.getString("id"), kind = j.optString("kind"), status = j.optString("status"),
                gpu = j.int("gpu"), progress = j.optDouble("progress", 0.0).let { if (it.isNaN()) 0.0 else it },
                phase = j.str("phase"), created = j.optDouble("created", 0.0), started = j.dbl("started"),
                finished = j.dbl("finished"), error = j.str("error"), note = j.str("note"), took = j.int("took"),
                source = j.optString("source"), text = j.str("text"), blocked = j.optBoolean("blocked", false),
                params = j.optJSONObject("params") ?: JSONObject(),
                resultW = res?.int("w"), resultH = res?.int("h"), estimate = j.int("estimate"), eta = j.int("eta"),
            )
        }

        fun parseList(a: JSONArray?): List<Job> = (0 until (a?.length() ?: 0)).map { parse(a!!.getJSONObject(it)) }
    }
}

/** Server status from GET /health (also embedded in GET /api/jobs). */
data class Health(
    val stage: String, val pct: Int, val detail: String, val ready: Boolean, val error: String?,
    val gpus: List<Pair<Int, Int>>, val gpuBusy: List<Boolean>, val peT2i: Boolean, val peI2i: Boolean,
    val nsfw: String, val nsfwDetail: String, val uptime: Int, val idleS: Int, val idleLimitS: Int, val queue: Int,
) {
    companion object {
        fun parse(j: JSONObject): Health {
            val g = j.optJSONArray("gpus") ?: JSONArray()
            val gpus = (0 until g.length()).map { i -> g.optJSONObject(i) ?: JSONObject() }
            val pe = j.optJSONObject("pe") ?: JSONObject()
            return Health(
                stage = j.optString("stage"), pct = j.optInt("pct"), detail = j.optString("detail"),
                ready = j.optBoolean("ready"), error = if (j.isNull("error")) null else j.optString("error").ifBlank { null },
                gpus = gpus.map { it.optInt("value", 0) to it.optInt("max", 0) },
                gpuBusy = gpus.map { !it.isNull("prompt_id") && it.optString("prompt_id").isNotBlank() },
                peT2i = pe.optBoolean("t2i"), peI2i = pe.optBoolean("i2i"),
                nsfw = j.optString("nsfw", "off"), nsfwDetail = j.optString("nsfw_detail"),
                uptime = j.optInt("uptime"), idleS = j.optInt("idle_s"), idleLimitS = j.optDouble("idle_limit_s", 0.0).toInt(),
                queue = j.optInt("queue"),
            )
        }
    }
}

/** The request bodies, exactly as README "Gateway API" describes them. Pure, so they are unit-tested. */
object Requests {
    val ASPECTS = listOf("1:1", "3:4", "4:3", "9:16", "16:9")
    const val SOURCE = "phone"

    fun create(
        prompt: String, negative: String, aspect: String, quality: String, count: Int, seed: Long,
        nsfw: Boolean, loraStrength: Double,
    ): JSONObject {
        val p = prompt.trim()
        require(p.isNotEmpty()) { "Write a prompt first" }
        return JSONObject()
            .put("kind", "create").put("prompt", p.take(4000)).put("negative", negative.trim().take(2000))
            .put("aspect", if (aspect in ASPECTS) aspect else "1:1")
            .put("quality", if (quality == "quality") "quality" else "turbo")
            .put("count", count.coerceIn(1, 4)).put("seed", if (seed < 0) -1 else seed)
            .put("nsfw", nsfw).put("lora_strength", loraStrength.coerceIn(0.0, 2.0))
            .put("source", SOURCE)
    }

    /** Photo edits never carry the NSFW flag. */
    fun edit(prompt: String, image: String?, count: Int, seed: Long): JSONObject {
        val p = prompt.trim()
        require(p.isNotEmpty()) { "Write what to change first" }
        return JSONObject().put("kind", "edit").put("prompt", p.take(4000)).also { if (image != null) it.put("image", image) }
            .put("count", count.coerceIn(1, 4)).put("seed", if (seed < 0) -1 else seed).put("source", SOURCE)
    }

    fun rembg(image: String?): JSONObject =
        JSONObject().put("kind", "rembg").also { if (image != null) it.put("image", image) }.put("seed", -1).put("source", SOURCE)

    fun improve(prompt: String, aspect: String?, nsfw: Boolean): JSONObject {
        val p = prompt.trim()
        require(p.isNotEmpty()) { "Write a short idea first" }
        return JSONObject().put("kind", "improve").put("prompt", p.take(2000)).put("mode", "t2i")
            .put("aspect", if (aspect in ASPECTS) aspect else JSONObject.NULL).put("nsfw", nsfw).put("source", SOURCE)
    }

    fun parseSeed(s: String): Long = s.trim().toLongOrNull()?.takeIf { it >= 0 } ?: -1
}

/** The Kaggle gateway's JSON API (header X-Key). Blocking: call from Dispatchers.IO. */
class Gateway(val base: String, private val key: String) {
    private val auth = mapOf("X-Key" to key)

    private fun json(method: String, path: String, body: JSONObject? = null, timeoutMs: Int = 30_000): JSONObject {
        val r = if (body != null) Http.postJson(base + path, body.toString(), auth, timeoutMs)
        else Http.request(method, base + path, auth, timeoutMs = timeoutMs)
        if (r.code >= 400) throw GatewayError.from(r.code, r.text)
        return runCatching { JSONObject(r.text) }.getOrElse { throw GatewayError(502, "The server sent an unreadable answer.") }
    }

    fun health(): Health = Health.parse(json("GET", "/health", timeoutMs = 20_000))

    data class JobList(val jobs: List<Job>, val status: Health?, val now: Double)

    fun jobs(since: Double = 0.0): JobList {
        val j = json("GET", "/api/jobs?since=${since.toLong()}", timeoutMs = 25_000)
        return JobList(Job.parseList(j.optJSONArray("jobs")), j.optJSONObject("status")?.let { Health.parse(it) }, j.optDouble("now"))
    }

    fun job(id: String): Job = Job.parse(json("GET", "/api/jobs/$id"))

    fun submit(body: JSONObject): List<Job> = Job.parseList(json("POST", "/api/jobs", body, 60_000).optJSONArray("jobs"))

    fun cancel(id: String): Job = Job.parse(json("POST", "/api/jobs/$id/cancel", JSONObject()))

    /** Returns the server-side name to use as "image". */
    fun upload(data: ByteArray, filename: String, mime: String): String {
        val boundary = "qis" + System.nanoTime()
        val r = Http.request("POST", "$base/api/upload", auth, Http.multipart("file", filename, mime, data, boundary),
            "multipart/form-data; boundary=$boundary", 180_000)
        if (r.code >= 400) throw GatewayError.from(r.code, r.text)
        return JSONObject(r.text).getString("name")
    }

    fun image(id: String): ByteArray {
        val r = Http.request("GET", "$base/api/jobs/$id/image", auth, timeoutMs = 120_000)
        if (r.code >= 400) throw GatewayError.from(r.code, r.text)
        return r.bytes
    }

    fun imageUrl(id: String) = "$base/api/jobs/$id/image"

    fun shutdown() {
        runCatching { json("POST", "/shutdown", JSONObject(), 15_000) }
    }
}
