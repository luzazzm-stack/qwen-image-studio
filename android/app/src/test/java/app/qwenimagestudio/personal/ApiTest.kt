package app.qwenimagestudio.personal

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiTest {
    @Test
    fun createBody() {
        val b = Requests.create("  a red bicycle  ", " blurry ", "9:16", "quality", 9, 42, nsfw = true, loraStrength = 3.0)
        assertEquals("create", b.getString("kind"))
        assertEquals("a red bicycle", b.getString("prompt"))
        assertEquals("blurry", b.getString("negative"))
        assertEquals("9:16", b.getString("aspect"))
        assertEquals("quality", b.getString("quality"))
        assertEquals(4, b.getInt("count"))
        assertEquals(42L, b.getLong("seed"))
        assertTrue(b.getBoolean("nsfw"))
        assertEquals(2.0, b.getDouble("lora_strength"), 0.0)
        assertEquals("phone", b.getString("source"))
    }

    @Test
    fun createDefaultsAndLimits() {
        val b = Requests.create("x", "", "2:1", "fast", 0, -5, false, 1.0)
        assertEquals("1:1", b.getString("aspect"))
        assertEquals("turbo", b.getString("quality"))
        assertEquals(1, b.getInt("count"))
        assertEquals(-1, b.getInt("seed"))
        assertFalse(b.getBoolean("nsfw"))
        assertEquals(4000, Requests.create("y".repeat(5000), "", "1:1", "turbo", 1, -1, false, 1.0).getString("prompt").length)
    }

    @Test(expected = IllegalArgumentException::class)
    fun createNeedsPrompt() {
        Requests.create("   ", "", "1:1", "turbo", 1, -1, false, 1.0)
    }

    @Test
    fun editAndRembgNeverCarryNsfw() {
        val e = Requests.edit("make it snow", "up_abc.jpg", 2, -1)
        assertEquals("edit", e.getString("kind"))
        assertEquals("up_abc.jpg", e.getString("image"))
        assertEquals(2, e.getInt("count"))
        assertFalse(e.has("nsfw"))
        val r = Requests.rembg("up_abc.jpg")
        assertEquals("rembg", r.getString("kind"))
        assertFalse(r.has("nsfw"))
        assertFalse(Requests.edit("x", null, 1, -1).has("image"))
    }

    @Test
    fun improveBody() {
        val b = Requests.improve("a cat", "3:4", true)
        assertEquals("improve", b.getString("kind"))
        assertEquals("t2i", b.getString("mode"))
        assertEquals("3:4", b.getString("aspect"))
        assertTrue(b.getBoolean("nsfw"))
        assertTrue(Requests.improve("a cat", "x", false).isNull("aspect"))
    }

    @Test
    fun seeds() {
        assertEquals(-1L, Requests.parseSeed(""))
        assertEquals(-1L, Requests.parseSeed("abc"))
        assertEquals(1234L, Requests.parseSeed(" 1234 "))
    }

    @Test
    fun refusalIsReadable() {
        val e = GatewayError.from(422, """{"error": "Blocked: this request combines minors with sexual or nude content.", "blocked": true}""")
        assertTrue(e.blocked)
        assertTrue(e.message!!.startsWith("Blocked:"))
        assertEquals("bad key", GatewayError.from(401, """{"error": "bad key"}""").message)
        assertEquals("The server did not accept this app's key.", GatewayError.from(401, "").message)
        assertTrue(GatewayError.from(502, "<html>").message!!.contains("not answering"))
        assertFalse(GatewayError.from(400, """{"error": "Write a prompt first"}""").blocked)
    }

    // A real answer of the live gateway (GET /api/jobs), 2026-11-05.
    private val sampleJob = """{"id": "10ced01602", "kind": "create", "status": "done", "gpu": 0, "progress": 1, "phase": "finishing",
        "created": 1793898800.8011131, "started": 1793898800.900362, "finished": 1793898870.806753, "error": null, "note": null,
        "took": 70, "source": "laptop", "text": null, "blocked": null, "params": {"prompt": "A Nepali grandmother making momos",
        "negative": "", "aspect": "1:1", "w": 1024, "h": 1024, "quality": "turbo", "nsfw": false, "lora_strength": 1.0, "seed": 1234},
        "result": {"w": 1024, "h": 1024, "bytes": 1678738, "name": "qis_10ced01602_00001_.png"}}"""

    @Test
    fun parsesJobs() {
        val j = Job.parse(JSONObject(sampleJob))
        assertEquals("10ced01602", j.id)
        assertEquals("done", j.status)
        assertEquals(0, j.gpu)
        assertTrue(j.hasImage)
        assertFalse(j.active)
        assertNull(j.error)
        assertNull(j.text)
        assertEquals("A Nepali grandmother making momos", j.prompt)
        assertEquals(1234L, j.params.getLong("seed"))
        assertEquals(1024, j.resultW)
        val q = Job.parse(JSONObject("""{"id": "a", "kind": "create", "status": "queued", "gpu": null, "progress": 0, "params": {}, "estimate": 30}"""))
        assertTrue(q.active)
        assertNull(q.gpu)
        assertFalse(q.hasImage)
        assertEquals(30, q.estimate)
    }

    @Test
    fun parsesHealth() {
        val h = Health.parse(JSONObject("""{"nsfw": "ok", "stage": "ready", "pct": 100, "detail": "", "ready": true, "error": null,
            "gpus": [{"prompt_id": "4c0a", "value": 3, "max": 8, "node": null}, {}], "pe": {"t2i": true, "i2i": false},
            "uptime": 976, "idle_s": 11, "idle_limit_s": 1500.0, "queue": 2}"""))
        assertTrue(h.ready)
        assertNull(h.error)
        assertEquals(listOf(3 to 8, 0 to 0), h.gpus)
        assertEquals(listOf(true, false), h.gpuBusy)
        assertTrue(h.peT2i)
        assertFalse(h.peI2i)
        assertEquals(1500, h.idleLimitS)
    }

    @Test
    fun ntfyMessages() {
        val body = """
            {"id":"a","time":10,"event":"open","topic":"t"}
            {"id":"b","time":20,"event":"message","topic":"t","message":"{\"type\": \"url\", \"url\": \"https://a.trycloudflare.com\", \"t\": 20.5}"}
            {"id":"c","time":30,"event":"message","topic":"t","message":"{\"type\": \"ready\", \"url\": \"https://a.trycloudflare.com\", \"t\": 30.1}"}
            not json
        """.trimIndent()
        val m = Ntfy.parse(body)
        assertEquals(listOf("url", "ready"), m.map { it.type })
        assertEquals("https://a.trycloudflare.com", Ntfy.verdict(m).url)
        val stopped = m + Ntfy.Msg("stopped", null, null, "idle", 40.0)
        assertNull(Ntfy.verdict(stopped).url)
        assertEquals("idle", Ntfy.verdict(stopped).stopped)
        val restarted = stopped + Ntfy.Msg("url", "https://b.trycloudflare.com", null, null, 50.0)
        assertEquals("https://b.trycloudflare.com", Ntfy.verdict(restarted).url)
        assertEquals("boom", Ntfy.verdict(listOf(Ntfy.Msg("error", null, "boom", null, 1.0))).error)
        assertEquals("https://ntfy.sh/qis-x/json?poll=1&since=123", Ntfy.pollUrl("qis-x", 123))
    }

    @Test
    fun kaggleCalls() {
        assertEquals("running", Kaggle.normStatus("RUNNING"))
        assertEquals("queued", Kaggle.normStatus("KernelWorkerStatus.QUEUED"))
        assertEquals("complete", Kaggle.normStatus("KERNEL_WORKER_STATUS_COMPLETE"))
        val q = Kaggle.parseQuota(JSONObject("""{"quotaRefreshTime":"2026-11-07T00:00:00Z","gpuQuota":{"timeUsed":"2130.9s","timeReserved":"0s","totalTimeAllowed":"108000s"}}"""))
        assertEquals(30.0, q.totalH, 0.001)
        assertEquals(0.592, q.usedH, 0.001)
        assertEquals("2026-11-07T00:00:00Z", q.refresh)
        val acc = Account.parse("""{"kaggle_user":"leomaharjan","kaggle_slug":"qwen-image-studio-server","kaggle_token":"KGAT_x","key":"k","topic":"t"}""")
        val b = Kaggle.pushBody(acc, "print(1)")
        assertEquals("leomaharjan/qwen-image-studio-server", b.getString("slug"))
        assertEquals("qwen image studio server", b.getString("newTitle"))
        assertEquals("script", b.getString("kernelType"))
        assertEquals("NvidiaTeslaT4", b.getString("machineShape"))
        assertTrue(b.getBoolean("isPrivate") && b.getBoolean("enableGpu") && b.getBoolean("enableInternet"))
        assertEquals(20, acc.idleMin)
    }

    @Test
    fun signInAccount() {
        // new API tokens use Bearer, legacy kaggle.json keys use Basic user:key
        assertEquals("Bearer KGAT_abc", Account.authHeader("u", "KGAT_abc"))
        val legacy = "0123456789abcdef0123456789abcdef"
        assertTrue(Account.isLegacyKey(legacy))
        assertEquals("Basic " + java.util.Base64.getEncoder().encodeToString("u:$legacy".toByteArray()), Account.authHeader("u", legacy))
        // a pasted kaggle.json fills in the username
        assertEquals("bob" to legacy, Account.parseToken("", """{"username":"bob","key":"$legacy"}"""))
        assertEquals("amy" to "KGAT_x", Account.parseToken(" amy ", "  KGAT_x \n"))
        // a fresh install gets its own random key + topic, in the user's own account
        val a = Account.fresh("amy", "KGAT_x")
        val b = Account.fresh("amy", "KGAT_x")
        assertEquals("amy/qwen-image-studio-server", a.kernel)
        assertTrue(a.key != b.key && a.topic != b.topic)
        assertTrue(Regex("qis-[0-9a-f]{16}").matches(a.topic))
        assertTrue(a.key.length >= 30 && '"' !in a.key && '\\' !in a.key)
        val back = Account.parse(a.toJson())
        assertEquals(a.key, back.key)
        assertEquals(a.topic, back.topic)
        assertEquals("KGAT_x", back.kaggleToken)
        assertEquals(20, back.idleMin)
        assertEquals(Account.NSFW_REPO, back.nsfwRepo)
    }

    @Test
    fun multipartBody() {
        val body = String(Http.multipart("file", "p.jpg", "image/jpeg", "DATA".toByteArray(), "BND"))
        assertEquals("--BND\r\nContent-Disposition: form-data; name=\"file\"; filename=\"p.jpg\"\r\nContent-Type: image/jpeg\r\n\r\nDATA\r\n--BND--\r\n", body)
    }
}
