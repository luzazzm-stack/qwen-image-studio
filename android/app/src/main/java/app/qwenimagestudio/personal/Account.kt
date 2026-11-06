package app.qwenimagestudio.personal

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * The Kaggle account and this install's server identity (gateway key + ntfy topic).
 * personal app: bundled at build time from the Windows app's files (see app/build.gradle.kts).
 * client app:   the user signs in on the phone; kept encrypted by [AccountStore].
 */
class Account(
    val kaggleUser: String,
    val kaggleSlug: String,
    val kaggleToken: String,
    val key: String,
    val topic: String,
    val idleMin: Int,
    val maxHours: Double,
    val nsfwRepo: String,
    val nsfwFile: String,
) {
    val kernel get() = "$kaggleUser/$kaggleSlug"

    /** New API tokens (KGAT_...) are Bearer tokens; the legacy 32-hex keys from kaggle.json use Basic auth. */
    fun authHeader(): String = authHeader(kaggleUser, kaggleToken)

    fun toJson(): String = JSONObject()
        .put("kaggle_user", kaggleUser).put("kaggle_slug", kaggleSlug).put("kaggle_token", kaggleToken)
        .put("key", key).put("topic", topic).put("idle_min", idleMin).put("max_hours", maxHours)
        .put("nsfw_lora_repo", nsfwRepo).put("nsfw_lora_file", nsfwFile).toString()

    companion object {
        const val SLUG = "qwen-image-studio-server"
        const val NSFW_REPO = "f23gg/NSFW-LORA-Qwen-Image-2.1"
        const val NSFW_FILE = "NSFW Qwen Lora.safetensors"

        fun parse(json: String): Account {
            val j = JSONObject(json)
            return Account(
                j.getString("kaggle_user"), j.getString("kaggle_slug"), j.getString("kaggle_token"),
                j.getString("key"), j.getString("topic"),
                j.optInt("idle_min", 20), j.optDouble("max_hours", 6.0),
                j.optString("nsfw_lora_repo"), j.optString("nsfw_lora_file"),
            )
        }

        fun load(ctx: Context) = parse(ctx.assets.open("qis/account.json").bufferedReader().use { it.readText() })

        fun isLegacyKey(token: String) = Regex("[0-9a-f]{32}").matches(token)

        fun authHeader(user: String, token: String): String =
            if (isLegacyKey(token)) "Basic " + java.util.Base64.getEncoder().encodeToString("$user:$token".toByteArray())
            else "Bearer $token"

        /** Accepts a new API token, a legacy key or a whole pasted kaggle.json. Returns (username, token). */
        fun parseToken(user: String, text: String): Pair<String, String> {
            val t = text.trim()
            if (t.startsWith("{")) runCatching {
                val j = JSONObject(t)
                return (j.optString("username").ifBlank { user }.trim()) to j.optString("key").trim()
            }
            return user.trim() to t
        }

        /** A fresh account for a signed-in user: a random gateway key and ntfy topic only this install knows. */
        fun fresh(user: String, token: String): Account {
            val rnd = SecureRandom()
            fun b64(n: Int) = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(n).also { rnd.nextBytes(it) })
            val topic = "qis-" + ByteArray(8).also { rnd.nextBytes(it) }.joinToString("") { "%02x".format(it) }
            return Account(user, SLUG, token, b64(24), topic, 20, 6.0, NSFW_REPO, NSFW_FILE)
        }
    }
}

/** Where the account comes from: bundled (personal app) or signed in on this phone (client app). */
object Accounts {
    val signIn get() = BuildConfig.SIGN_IN

    fun current(ctx: Context): Account? = if (!signIn) Account.load(ctx) else AccountStore(ctx).account

    fun save(ctx: Context, acc: Account) { AccountStore(ctx).account = acc }

    fun clear(ctx: Context) { AccountStore(ctx).account = null }
}

/** The signed-in account, encrypted with an AES key that never leaves the Android Keystore. */
class AccountStore(ctx: Context) {
    private val prefs: SharedPreferences = ctx.getSharedPreferences("qis_account", Context.MODE_PRIVATE)

    var account: Account?
        get() = secret("account")?.let { runCatching { Account.parse(it) }.getOrNull() }
        set(v) = setSecret("account", v?.toJson())

    private fun secret(name: String): String? {
        val raw = prefs.getString(name, null) ?: return null
        return try {
            val blob = Base64.decode(raw, Base64.NO_WRAP)
            val c = Cipher.getInstance(TRANSFORM)
            c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, blob, 0, IV_SIZE))
            String(c.doFinal(blob, IV_SIZE, blob.size - IV_SIZE), Charsets.UTF_8)
        } catch (e: Exception) {
            null   // the Keystore key is gone (restored or reset phone): treat as signed out
        }
    }

    private fun setSecret(name: String, value: String?) {
        if (value == null) {
            prefs.edit().remove(name).commit()
            return
        }
        val c = Cipher.getInstance(TRANSFORM)
        c.init(Cipher.ENCRYPT_MODE, key())
        val out = c.iv + c.doFinal(value.toByteArray(Charsets.UTF_8))
        prefs.edit().putString(name, Base64.encodeToString(out, Base64.NO_WRAP)).commit()
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }

    private companion object {
        const val KEY_ALIAS = "qis_account"
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val IV_SIZE = 12
    }
}
