package app.qwenimagestudio.personal

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.LruCache
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** One image saved on this phone (Pictures/Qwen Image Studio) with what made it. */
data class Saved(
    val jobId: String, val uri: String, val kind: String, val prompt: String, val negative: String,
    val aspect: String?, val quality: String?, val seed: Long, val nsfw: Boolean, val w: Int, val h: Int,
    val created: Long, val source: String,
) {
    fun toJson(): JSONObject = JSONObject().put("job", jobId).put("uri", uri).put("kind", kind).put("prompt", prompt)
        .put("negative", negative).put("aspect", aspect).put("quality", quality).put("seed", seed).put("nsfw", nsfw)
        .put("w", w).put("h", h).put("created", created).put("source", source)

    companion object {
        fun from(j: JSONObject) = Saved(
            j.getString("job"), j.getString("uri"), j.optString("kind"), j.optString("prompt"), j.optString("negative"),
            j.optString("aspect").ifBlank { null }, j.optString("quality").ifBlank { null }, j.optLong("seed", -1),
            j.optBoolean("nsfw"), j.optInt("w"), j.optInt("h"), j.optLong("created"), j.optString("source"),
        )

        fun fromJob(job: Job, uri: Uri) = Saved(
            job.id, uri.toString(), job.kind, job.prompt, job.params.optString("negative"),
            job.params.optString("aspect").ifBlank { null }, job.params.optString("quality").ifBlank { null },
            job.params.optLong("seed", -1), job.params.optBoolean("nsfw"), job.resultW ?: 0, job.resultH ?: 0,
            System.currentTimeMillis(), job.source,
        )
    }
}

class Library(private val ctx: Context) {
    private val index = File(ctx.filesDir, "library.json")

    fun load(): List<Saved> = runCatching {
        val a = JSONArray(index.readText())
        (0 until a.length()).map { Saved.from(a.getJSONObject(it)) }
    }.getOrElse { emptyList() }

    fun store(items: List<Saved>) {
        val tmp = File(ctx.filesDir, "library.json.tmp")
        tmp.writeText(JSONArray().apply { items.forEach { put(it.toJson()) } }.toString())
        tmp.renameTo(index)
    }

    /** Writes the PNG into Pictures/Qwen Image Studio and returns its content Uri. */
    fun savePng(bytes: ByteArray, name: String): Uri {
        val cr = ctx.contentResolver
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val v = ContentValues().apply {
                put(MediaStore.Images.Media.DISPLAY_NAME, name)
                put(MediaStore.Images.Media.MIME_TYPE, "image/png")
                put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/" + FOLDER)
                put(MediaStore.Images.Media.IS_PENDING, 1)
            }
            val uri = cr.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, v) ?: error("Could not create the picture file")
            cr.openOutputStream(uri)?.use { it.write(bytes) } ?: error("Could not write the picture")
            cr.update(uri, ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) }, null, null)
            return uri
        }
        @Suppress("DEPRECATION")
        val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES), FOLDER).apply { mkdirs() }
        val f = File(dir, name)
        f.writeBytes(bytes)
        val v = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, name)
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            @Suppress("DEPRECATION") put(MediaStore.Images.Media.DATA, f.absolutePath)
        }
        return cr.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, v) ?: Uri.fromFile(f)
    }

    /** True if deleted; throws SecurityException when Android wants the user's OK (after a reinstall). */
    fun delete(uri: Uri): Boolean = ctx.contentResolver.delete(uri, null, null) > 0

    fun exists(uri: Uri): Boolean = runCatching { ctx.contentResolver.openFileDescriptor(uri, "r")?.use { true } ?: false }.getOrDefault(false)

    companion object {
        const val FOLDER = "Qwen Image Studio"

        fun fileName(job: Job): String {
            val words = job.prompt.lowercase().replace(Regex("[^a-z0-9 ]"), " ").split(" ").filter { it.isNotBlank() }.take(5)
            val slug = (if (job.kind == "rembg") listOf("no", "background") else words).joinToString("-").ifBlank { job.kind }
            return "qis_${job.id}_$slug.png"
        }
    }
}

/** Small in-memory thumbnail cache. Loads from content Uris or the gateway (with the key). */
object Images {
    private val cache = object : LruCache<String, ImageBitmap>(48 * 1024 * 1024) {
        override fun sizeOf(key: String, value: ImageBitmap) = value.width * value.height * 4
    }

    fun cached(key: String): ImageBitmap? = cache.get(key)

    fun decode(bytes: ByteArray, maxSide: Int): ImageBitmap? {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
        var s = 1
        while (maxOf(o.outWidth, o.outHeight) / (s * 2) >= maxSide) s *= 2
        val bmp: Bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = s }) ?: return null
        return bmp.asImageBitmap()
    }

    /** Blocking; call from Dispatchers.IO. */
    fun load(ctx: Context, src: String, maxSide: Int, key: String? = null): ImageBitmap? {
        val ck = "$src@$maxSide"
        cache.get(ck)?.let { return it }
        val bytes = if (src.startsWith("http")) {
            val r = Http.request("GET", src, if (key != null) mapOf("X-Key" to key) else emptyMap(), timeoutMs = 60_000)
            if (r.code >= 400) return null
            r.bytes
        } else {
            ctx.contentResolver.openInputStream(Uri.parse(src))?.use { it.readBytes() } ?: return null
        }
        return decode(bytes, maxSide)?.also { cache.put(ck, it) }
    }
}
