package app.qwenimagestudio.personal

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest
import java.util.Base64

class KernelTemplateTest {
    // Gradle runs unit tests in the module folder: android/app -> ../../ is the Windows app's folder (read-only).
    private val studio = File("../..").canonicalFile
    private fun read(p: String) = File(studio, p).readText(Charsets.UTF_8)
    private val template get() = read("kaggle/qis_server.py")
    private val t2i get() = read("prompts/pe_t2i_system.txt")
    private val i2i get() = read("prompts/pe_i2i_system.txt")

    private fun fill(key: String = "testKey_AZ-09", topic: String = "qis-testtopic", idle: Int = 25, hours: Double = 6.0) =
        KernelTemplate.fill(template, t2i, i2i, key, topic, idle, hours, "f23gg/NSFW-LORA-Qwen-Image-2.1", "NSFW Qwen Lora.safetensors")

    private fun sha(f: String) = MessageDigest.getInstance("SHA-256").digest(File(studio, f).readBytes()).joinToString("") { "%02x".format(it) }

    /** Byte-for-byte equal to kaggle_ctl.kernel_source() (golden made with the Windows app's own Python code). */
    @Test
    fun matchesWindowsAppOutput() {
        val want = javaClass.getResource("/golden_inputs.sha256")!!.readText().trim().lines()
        val have = listOf(sha("kaggle/qis_server.py"), sha("prompts/pe_i2i_system.txt"), sha("prompts/pe_t2i_system.txt"))
        assumeTrue("template changed since the golden file was made; regenerate it (see README)", want == have)
        val golden = javaClass.getResource("/golden_kernel.py.txt")!!.readText(Charsets.UTF_8)
        assertEquals(golden, fill())
    }

    @Test
    fun everyPlaceholderIsFilled() {
        val src = fill()
        for (p in listOf("__KEY__", "__TOPIC__", "__IDLE_MIN__", "__MAX_HOURS__", "__PE_SYSTEM_B64__", "__NSFW_REPO__", "__NSFW_FILE__"))
            assertFalse("$p left", src.contains("\"$p\""))
        assertTrue(src.contains("KEY, TOPIC = \"testKey_AZ-09\", \"qis-testtopic\""))
        assertTrue(src.contains("IDLE_MIN, MAX_HOURS = \"25\", \"6.0\""))
        assertTrue(src.contains("NSFW_REPO, NSFW_FILE = \"f23gg/NSFW-LORA-Qwen-Image-2.1\", \"NSFW Qwen Lora.safetensors\""))
        // the template's own checks like PE_SYSTEM_B64.startswith("__") stay untouched
        assertTrue(src.contains("startswith(\"__\")"))
    }

    @Test
    fun promptEnhancerPromptsRoundTrip() {
        val b64 = Regex("PE_SYSTEM_B64 = \"([^\"]+)\"").find(fill())!!.groupValues[1]
        val j = JSONObject(String(Base64.getDecoder().decode(b64), Charsets.UTF_8))
        assertEquals(KernelTemplate.nl(t2i), j.getString("t2i"))
        assertEquals(KernelTemplate.nl(i2i), j.getString("i2i"))
    }

    @Test
    fun pythonJsonDumps() {
        assertEquals("\"a\\\"b\\\\c\\nd\\te\"", KernelTemplate.pyJsonString("a\"b\\c\nd\te"))
        assertEquals("\"caf\\u00e9 \\u0928\"", KernelTemplate.pyJsonString("café न"))
        assertEquals("\"\\ud83d\\ude00\"", KernelTemplate.pyJsonString("\uD83D\uDE00"))
        assertEquals("\"\\u007f\\u0001/\"", KernelTemplate.pyJsonString("\u007f\u0001/"))
    }

    @Test
    fun pythonFloats() {
        assertEquals("6.0", KernelTemplate.pyFloat(6.0))
        assertEquals("2.5", KernelTemplate.pyFloat(2.5))
        assertEquals("12.0", KernelTemplate.pyFloat(12.0))
    }

    @Test
    fun crlfIsReadLikePython() {
        val out = KernelTemplate.fill("A = \"__KEY__\"\r\nB = 1\r\n", "x\r\ny", "z", "k", "t", 1, 1.0, "", "")
        assertEquals("A = \"k\"\nB = 1\n", out)
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsQuotesInValues() {
        fill(key = "bad\"key")
    }
}
