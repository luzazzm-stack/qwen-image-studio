package app.qwenimagestudio.personal

import java.util.Base64

/**
 * Fills the kernel template exactly as the Windows app does (kaggle_ctl.kernel_source):
 * every "__NAME__" (with its quotes) becomes "value". PE_SYSTEM_B64 is base64 of Python's
 * json.dumps({"t2i": ..., "i2i": ...}) with the default ensure_ascii=True and ", " / ": " separators.
 * Python reads the files with universal newlines, so CRLF/CR become LF here too.
 */
object KernelTemplate {
    fun fill(
        template: String, peT2i: String, peI2i: String, key: String, topic: String,
        idleMin: Int, maxHours: Double, nsfwRepo: String, nsfwFile: String,
    ): String {
        val pe = linkedMapOf("t2i" to nl(peT2i), "i2i" to nl(peI2i))
        val peJson = pe.entries.joinToString(", ", "{", "}") { (k, v) -> pyJsonString(k) + ": " + pyJsonString(v) }
        val rep = linkedMapOf(
            "__KEY__" to key, "__TOPIC__" to topic, "__IDLE_MIN__" to idleMin.toString(),
            "__MAX_HOURS__" to pyFloat(maxHours),
            "__PE_SYSTEM_B64__" to Base64.getEncoder().encodeToString(peJson.toByteArray(Charsets.UTF_8)),
            "__NSFW_REPO__" to nsfwRepo, "__NSFW_FILE__" to nsfwFile,
        )
        var src = nl(template)
        for ((a, b) in rep) {
            require('"' !in b && '\\' !in b && '\n' !in b) { "bad value for $a" }
            src = src.replace("\"$a\"", "\"$b\"")
        }
        return src
    }

    fun nl(s: String) = s.replace("\r\n", "\n").replace("\r", "\n")

    /** str(float(x)) for ordinary values: 6 -> "6.0", 2.5 -> "2.5". */
    fun pyFloat(x: Double): String =
        if (x == Math.floor(x) && Math.abs(x) < 1e15) "${x.toLong()}.0" else x.toString()

    /** Python json.dumps(str) with ensure_ascii=True. */
    fun pyJsonString(s: String): String {
        val b = StringBuilder(s.length + 16).append('"')
        for (c in s) {
            when {
                c == '"' -> b.append("\\\"")
                c == '\\' -> b.append("\\\\")
                c == '\n' -> b.append("\\n")
                c == '\r' -> b.append("\\r")
                c == '\t' -> b.append("\\t")
                c == '\b' -> b.append("\\b")
                c == '\u000c' -> b.append("\\f")
                c < ' ' || c > '~' -> b.append("\\u").append(String.format("%04x", c.code))
                else -> b.append(c)
            }
        }
        return b.append('"').toString()
    }
}
