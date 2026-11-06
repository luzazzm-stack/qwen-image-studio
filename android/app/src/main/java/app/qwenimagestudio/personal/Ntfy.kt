package app.qwenimagestudio.personal

import org.json.JSONObject

/** The kernel announces {"type": "url"|"ready"|"error"|"stopped", ..., "t": unix} on ntfy.sh/<topic>. */
object Ntfy {
    data class Msg(val type: String, val url: String?, val error: String?, val reason: String?, val t: Double)

    fun pollUrl(topic: String, since: Long) = "https://ntfy.sh/$topic/json?poll=1&since=$since"

    fun parse(body: String): List<Msg> = body.lineSequence().mapNotNull { line ->
        try {
            val outer = JSONObject(line)
            if (outer.optString("event") != "message") null else {
                val m = JSONObject(outer.optString("message").ifBlank { "{}" })
                val type = m.optString("type")
                if (type.isBlank()) null else Msg(
                    type, m.optString("url").ifBlank { null }, m.optString("error").ifBlank { null },
                    m.optString("reason").ifBlank { null }, m.optDouble("t", outer.optDouble("time", 0.0)),
                )
            }
        } catch (e: Exception) {
            null
        }
    }.sortedBy { it.t }.toList()

    fun fetch(topic: String, since: Long): List<Msg> {
        val r = Http.request("GET", pollUrl(topic, since), timeoutMs = 20_000)
        if (r.code >= 400) throw RuntimeException("ntfy HTTP ${r.code}")
        return parse(r.text)
    }

    /** What the latest messages say: the live address, or that the server stopped / failed. */
    data class Verdict(val url: String?, val stopped: String?, val error: String?)

    fun verdict(msgs: List<Msg>): Verdict {
        var url: String? = null
        var stopped: String? = null
        var error: String? = null
        for (m in msgs) when (m.type) {
            "url", "ready" -> if (m.url != null) { url = m.url; stopped = null; error = null }
            "stopped" -> { stopped = m.reason ?: "stopped"; url = null }
            "error" -> { error = m.error ?: "failed"; url = null }
        }
        return Verdict(url, stopped, error)
    }
}
