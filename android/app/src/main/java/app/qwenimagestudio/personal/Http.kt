package app.qwenimagestudio.personal

import java.net.HttpURLConnection
import java.net.URL

/** Tiny blocking HTTP client (call it from Dispatchers.IO). */
object Http {
    class Res(val code: Int, val bytes: ByteArray) {
        val text: String get() = String(bytes, Charsets.UTF_8)
    }

    fun request(
        method: String, url: String, headers: Map<String, String> = emptyMap(),
        body: ByteArray? = null, contentType: String? = null, timeoutMs: Int = 30_000,
    ): Res {
        val c = URL(url).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = minOf(timeoutMs, 20_000)
            c.readTimeout = timeoutMs
            c.instanceFollowRedirects = true
            c.setRequestProperty("User-Agent", "QwenImageStudio-Android/1.0")
            headers.forEach { (k, v) -> c.setRequestProperty(k, v) }
            val payload = body ?: if (method == "POST") ByteArray(0) else null
            if (payload != null) {
                c.doOutput = true
                c.setFixedLengthStreamingMode(payload.size)
                if (contentType != null) c.setRequestProperty("Content-Type", contentType)
                c.outputStream.use { it.write(payload) }
            }
            val code = c.responseCode
            val stream = if (code < 400) c.inputStream else c.errorStream
            return Res(code, stream?.use { it.readBytes() } ?: ByteArray(0))
        } finally {
            c.disconnect()
        }
    }

    fun postJson(url: String, json: String, headers: Map<String, String> = emptyMap(), timeoutMs: Int = 30_000) =
        request("POST", url, headers, json.toByteArray(Charsets.UTF_8), "application/json", timeoutMs)

    /** One multipart/form-data body with a single file field. */
    fun multipart(field: String, filename: String, mime: String, data: ByteArray, boundary: String): ByteArray {
        val head = "--$boundary\r\nContent-Disposition: form-data; name=\"$field\"; filename=\"$filename\"\r\n" +
            "Content-Type: $mime\r\n\r\n"
        val tail = "\r\n--$boundary--\r\n"
        return head.toByteArray() + data + tail.toByteArray()
    }
}
