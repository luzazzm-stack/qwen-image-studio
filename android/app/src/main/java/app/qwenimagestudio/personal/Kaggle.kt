package app.qwenimagestudio.personal

import org.json.JSONArray
import org.json.JSONObject

class KaggleError(val code: Int, message: String) : Exception(message)

/** Kaggle's REST API (the calls kagglesdk makes) with the account's token. Blocking: use Dispatchers.IO. */
class Kaggle(private val acc: Account) {
    private fun call(method: String, body: JSONObject): JSONObject {
        val r = Http.postJson("$API/$method", body.toString(), mapOf("Authorization" to acc.authHeader()), 60_000)
        val j = runCatching { JSONObject(r.text.ifBlank { "{}" }) }.getOrElse { JSONObject() }
        if (r.code >= 400) {
            val msg = j.optJSONObject("error")?.optString("message")?.ifBlank { null } ?: j.optString("message").ifBlank { "HTTP ${r.code}" }
            throw KaggleError(r.code, msg)
        }
        return j
    }

    /** Same metadata as kaggle_ctl.push. Returns the version number. */
    fun push(kernelText: String): Int {
        val r = call("SaveKernel", pushBody(acc, kernelText))
        r.optString("error").ifBlank { null }?.let { throw KaggleError(400, it) }
        return r.optInt("versionNumber")
    }

    /** "running", "queued", "complete", "error", "cancel...", or "none" (the kernel does not exist: Kaggle says 403). */
    fun status(): String = try {
        normStatus(call("GetKernelSessionStatus", ref()).optString("status"))
    } catch (e: KaggleError) {
        if (e.code == 403 || e.code == 404) "none" else throw e
    }

    /** Deletes the kernel, which surely ends the GPU session. False if there was nothing to delete. */
    fun delete(): Boolean = try {
        call("DeleteKernel", ref()); true
    } catch (e: KaggleError) {
        if (e.code == 403 || e.code == 404) false else throw e
    }

    data class Quota(val usedH: Double, val totalH: Double, val refresh: String?) {
        val leftH get() = (totalH - usedH).coerceAtLeast(0.0)
    }

    fun quota(): Quota = parseQuota(call("GetAcceleratorQuotaStatistics", JSONObject()))

    private fun ref() = JSONObject().put("userName", acc.kaggleUser).put("kernelSlug", acc.kaggleSlug)

    companion object {
        const val API = "https://api.kaggle.com/v1/kernels.KernelsApiService"
        const val OAUTH = "https://api.kaggle.com/v1/security.OAuthService"

        /**
         * Sign-in: asks Kaggle whether the token works (read-only). Returns the real username.
         * New tokens: IntrospectToken tells us who owns it. Legacy keys: a quota call with Basic auth.
         */
        fun checkToken(user: String, token: String): String {
            if (token.isBlank()) throw KaggleError(400, "Paste your Kaggle API token.")
            if (Account.isLegacyKey(token)) {
                if (user.isBlank()) throw KaggleError(400, "Type your Kaggle username too (this kind of key needs it).")
                val r = Http.postJson("$API/GetAcceleratorQuotaStatistics", "{}", mapOf("Authorization" to Account.authHeader(user, token)), 30_000)
                if (r.code != 200) throw KaggleError(401, "Kaggle did not accept that username and key.")
                return user
            }
            val r = Http.postJson("$OAUTH/IntrospectToken", JSONObject().put("token", token).toString(), emptyMap(), 30_000)
            val j = runCatching { JSONObject(r.text.ifBlank { "{}" }) }.getOrElse { JSONObject() }
            val name = j.optString("username")
            if (r.code != 200 || !j.optBoolean("active") || name.isBlank())
                throw KaggleError(401, "Kaggle did not accept that token. Make a new one at kaggle.com/settings (API section).")
            return name
        }

        fun pushBody(acc: Account, kernelText: String): JSONObject = JSONObject()
            .put("slug", acc.kernel)
            .put("newTitle", acc.kaggleSlug.replace("-", " "))
            .put("text", kernelText)
            .put("language", "python")
            .put("kernelType", "script")
            .put("isPrivate", true)
            .put("enableGpu", true)
            .put("enableInternet", true)
            .put("machineShape", "NvidiaTeslaT4")
            .put("datasetDataSources", JSONArray())
            .put("competitionDataSources", JSONArray())
            .put("kernelDataSources", JSONArray())
            .put("modelDataSources", JSONArray())
            .put("categoryIds", JSONArray())

        fun normStatus(s: String): String =
            s.lowercase().substringAfterLast('.').removePrefix("kernel_worker_status_").ifBlank { "unknown" }

        fun parseQuota(r: JSONObject): Quota {
            val q = r.getJSONObject("gpuQuota")
            return Quota(hours(q.optString("timeUsed")), hours(q.optString("totalTimeAllowed")), r.optString("quotaRefreshTime").ifBlank { null })
        }

        fun hours(duration: String) = (duration.removeSuffix("s").toDoubleOrNull() ?: 0.0) / 3600
    }
}
