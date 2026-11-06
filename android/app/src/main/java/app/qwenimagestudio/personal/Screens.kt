package app.qwenimagestudio.personal

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsBottomHeight
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Locale

private val SIZES = mapOf("1:1" to "1024 × 1024", "3:4" to "896 × 1184", "4:3" to "1184 × 896", "9:16" to "768 × 1344", "16:9" to "1344 × 768")

/** What the viewer shows: a picture saved on the phone, or one still only on the server. */
data class ViewItem(val saved: Saved? = null, val job: Job? = null)

@Composable
fun App(s: Studio) {
    var confirm by remember { mutableStateOf(false) }
    var view by remember { mutableStateOf<ViewItem?>(null) }
    BackHandler(s.tab != Tab.CREATE) { s.tab = Tab.CREATE }
    Backdrop {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            Header(s)
            Box(Modifier.weight(1f).fillMaxWidth()) {
                when (s.tab) {
                    Tab.CREATE -> Page { CreateScreen(s) { confirm = true } }
                    Tab.EDIT -> Page { EditScreen(s) { confirm = true } }
                    Tab.IMAGES -> ImagesScreen(s) { view = it }
                    Tab.SETTINGS -> Page { SettingsScreen(s) { confirm = true } }
                }
            }
        }
        Dock(s, Modifier.align(Alignment.BottomCenter)) { confirm = true }
    }
    if (confirm) PowerDialog(s) { confirm = false }
    view?.let { v -> Viewer(s, v) { view = null } }
    s.notice?.let { n ->
        AlertDialog(
            onDismissRequest = { s.notice = null }, containerColor = Color(0xFF141416), titleContentColor = Q.text, textContentColor = Q.text2,
            title = { Text(n.title, fontWeight = FontWeight.SemiBold) }, text = { Text(n.text, fontSize = 15.sp, lineHeight = 21.sp) },
            confirmButton = { TextLink("OK", Q.accent) { s.notice = null } },
        )
    }
}

@Composable
private fun Page(content: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = 16.dp)) {
        content()
        Spacer(Modifier.height(112.dp))
        Spacer(Modifier.windowInsetsBottomHeight(WindowInsets.navigationBars))
    }
}

@Composable
private fun Header(s: Studio) {
    Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 16.dp, top = 12.dp, bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(QIcons.image, null, Modifier.size(24.dp), tint = Q.text)
        Spacer(Modifier.width(10.dp))
        Text("Qwen Image Studio", color = Q.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.3).sp)
        Spacer(Modifier.weight(1f))
        Row(Modifier.clip(Pill).background(Q.island).border(1.dp, Q.line, Pill).padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Dot(powerColor(s.power), ring = s.power == Power.READY)
            Spacer(Modifier.width(7.dp))
            Text(powerShort(s), color = Q.text2, fontSize = 12.5.sp)
        }
    }
}

fun powerColor(p: Power) = when (p) {
    Power.READY -> Q.accent
    Power.STARTING, Power.STOPPING -> Q.warn
    Power.ERROR -> Q.bad
    else -> Color(0x66FFFFFF)
}

fun powerShort(s: Studio) = when (s.power) {
    Power.CHECKING -> "Checking"
    Power.OFF -> "Server off"
    Power.STARTING -> "Starting" + (s.health?.pct?.takeIf { it > 0 }?.let { " $it%" } ?: "")
    Power.READY -> "Ready"
    Power.STOPPING -> "Turning off"
    Power.ERROR -> "Problem"
}

// ---------------------------------------------------------------- dock
@Composable
private fun Dock(s: Studio, modifier: Modifier, onPower: () -> Unit) {
    val active = s.jobs.count { it.active } + s.outbox.size
    Row(
        modifier.navigationBarsPadding().padding(bottom = 14.dp).clip(Pill).background(Color(0xE6141416)).border(1.dp, Q.line, Pill).padding(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        DockItem("Create", QIcons.sparkle, s.tab == Tab.CREATE) { s.tab = Tab.CREATE }
        DockItem("Edit", QIcons.pencil, s.tab == Tab.EDIT) { s.tab = Tab.EDIT }
        DockItem("Images", QIcons.images, s.tab == Tab.IMAGES, badge = active.takeIf { it > 0 }) { s.tab = Tab.IMAGES; s.pruneLibrary() }
        DockItem("Settings", QIcons.gear, s.tab == Tab.SETTINGS) { s.tab = Tab.SETTINGS }
        Box(Modifier.padding(horizontal = 4.dp).width(1.dp).height(26.dp).background(Q.line))
        Box(Modifier.size(48.dp).clip(CircleShape).clickable(onClick = onPower), contentAlignment = Alignment.Center) {
            Icon(QIcons.power, "Server power", Modifier.size(22.dp), tint = if (s.power == Power.READY) Q.accent else Q.text2)
            Box(Modifier.align(Alignment.TopEnd).padding(top = 9.dp, end = 9.dp).size(8.dp).clip(CircleShape).background(powerColor(s.power)))
        }
    }
}

@Composable
private fun DockItem(label: String, icon: androidx.compose.ui.graphics.vector.ImageVector, on: Boolean, badge: Int? = null, onClick: () -> Unit) {
    Row(
        Modifier.height(48.dp).clip(Pill).background(if (on) Color.White else Color.Transparent).clickable(onClick = onClick)
            .padding(horizontal = if (on) 16.dp else 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, label, Modifier.size(20.dp), tint = if (on) Color.Black else Q.muted)
        if (on) { Spacer(Modifier.width(7.dp)); Text(label, color = Color.Black, fontSize = 14.sp, fontWeight = FontWeight.Medium) }
        if (badge != null) {
            Spacer(Modifier.width(5.dp))
            Text("$badge", Modifier.clip(Pill).background(if (on) Color.Black else Q.accent).padding(horizontal = 6.dp),
                color = if (on) Color.White else Q.accentInk, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun PowerDialog(s: Studio, close: () -> Unit) {
    val on = s.power == Power.READY || s.power == Power.STARTING
    if (s.power == Power.STOPPING || s.power == Power.CHECKING || s.busy) { close(); return }
    val left = s.quota?.let { "%.1f h of %.0f h left this week.".format(it.leftH, it.totalH) } ?: ""
    AlertDialog(
        onDismissRequest = close, containerColor = Color(0xFF141416), titleContentColor = Q.text, textContentColor = Q.text2,
        title = { Text(if (on) "Turn the server off?" else "Turn the server on?", fontWeight = FontWeight.SemiBold) },
        text = {
            Text(
                if (on) "Images that are still being made stop. Jobs sent from this phone go back to the waiting list. The laptop app uses the same server."
                else "Starts Qwen-Image-2.1 on your Kaggle account (${s.acc.kaggleUser}), 2 GPUs. About 5 to 7 minutes. It uses GPU hours until it is turned off or stops by itself after ${s.idleMin} min without use. $left",
                fontSize = 15.sp, lineHeight = 21.sp,
            )
        },
        confirmButton = { TextLink(if (on) "Turn off" else "Turn on", if (on) Q.bad else Q.accent) { close(); if (on) s.powerOff() else s.powerOn() } },
        dismissButton = { TextLink("Cancel") { close() } },
    )
}

// ---------------------------------------------------------------- server card (Create, Edit)
@Composable
fun StatusCard(s: Studio, onPower: () -> Unit) {
    Glass {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Dot(powerColor(s.power), ring = s.power == Power.READY)
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(when (s.power) { Power.READY -> "Working"; else -> powerShort(s) }, color = Q.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                val d = when (s.power) {
                    Power.READY -> s.jobs.count { it.active }.let { if (it > 0) "$it in the queue" else "Ready for new images" }
                    Power.OFF -> s.powerDetail.takeIf { it != "Server off" } ?: "Turn it on to make images (5-7 min)"
                    else -> s.powerDetail
                }
                Text(d, color = if (s.power == Power.ERROR) Q.bad else Q.muted, fontSize = 13.sp, lineHeight = 18.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.width(10.dp))
            val on = s.power == Power.READY || s.power == Power.STARTING
            GhostButton(if (on) "Turn off" else "Turn on", enabled = !s.busy && s.power != Power.STOPPING && s.power != Power.CHECKING, onClick = onPower)
        }
        val h = s.health
        if (s.power == Power.STARTING && h != null && h.pct > 0) {
            Spacer(Modifier.height(14.dp))
            Bar(h.pct / 100f, color = Q.warn)
        }
        if ((s.power == Power.READY || s.power == Power.STARTING) && h != null && h.gpus.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            h.gpus.forEachIndexed { i, (v, m) ->
                val busy = h.gpuBusy.getOrElse(i) { false }
                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("GPU ${i + 1}", Modifier.width(56.dp), color = Q.text2, fontSize = 13.sp)
                    Bar(if (m > 0) v.toFloat() / m else if (busy) .04f else 0f, Modifier.weight(1f))
                    Text(if (m > 0) "$v/$m" else if (busy) "busy" else "idle", Modifier.width(52.dp).padding(start = 8.dp), color = Q.muted, fontSize = 12.sp)
                }
            }
        }
        s.quota?.let { q ->
            Spacer(Modifier.height(10.dp))
            Hint("GPU hours: %.1f free of %.0f this week".format(q.leftH, q.totalH))
        }
        if (s.outbox.isNotEmpty() && s.power != Power.READY) {
            Spacer(Modifier.height(4.dp))
            Hint("${s.outbox.size} waiting on this phone: they are sent once the server is on.", color = Q.text2)
        }
    }
}

// ---------------------------------------------------------------- Create
@Composable
fun CreateScreen(s: Studio, onPower: () -> Unit) {
    var advanced by remember { mutableStateOf(s.negative.isNotBlank() || s.seedText.isNotBlank()) }
    Glass {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Label("Describe your image")
            Spacer(Modifier.weight(1f))
            Hint("${s.prompt.trim().split(Regex("\\s+")).count { it.isNotBlank() }} words")
        }
        Spacer(Modifier.height(10.dp))
        Field(s.prompt, { s.prompt = it }, "A cosy mountain teahouse at dawn in the Himalayas, prayer flags in the wind, warm light from the windows, a wooden sign that reads \"NAMASTE\", photorealistic, 35mm photo...", minLines = 6, big = true)
        Spacer(Modifier.height(14.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (s.improving != null) {
                GhostButton("Improving... (1-3 min)", icon = QIcons.sparkle, color = Q.accent) {}
                TextLink("Stop") { s.cancelImprove() }
            } else {
                GhostButton("Improve my prompt", icon = QIcons.sparkle) { s.improve() }
                TextLink("Clear") { s.prompt = "" }
            }
        }
    }
    Spacer(Modifier.height(16.dp))
    Glass {
        Label("Shape")
        Spacer(Modifier.height(10.dp))
        ShapePicker(s.aspect) { s.aspect = it }
        Spacer(Modifier.height(8.dp))
        Hint("${SIZES[s.aspect]} pixels")
        Spacer(Modifier.height(18.dp))
        Label("Quality")
        Spacer(Modifier.height(10.dp))
        Segmented(listOf("Turbo" to "~25 s", "Quality" to "~3 min"), if (s.quality == "quality") 1 else 0) { s.quality = if (it == 1) "quality" else "turbo" }
        Spacer(Modifier.height(18.dp))
        Label("How many images")
        Spacer(Modifier.height(10.dp))
        Segmented((1..4).map { "$it" to null }, s.count - 1) { s.count = it + 1 }
        Spacer(Modifier.height(16.dp))
        Box(Modifier.fillMaxWidth().height(1.dp).background(Q.line))
        Row(Modifier.fillMaxWidth().clip(Shape16).clickable { advanced = !advanced }.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(QIcons.sliders, null, Modifier.size(18.dp), tint = Q.text2)
            Spacer(Modifier.width(10.dp))
            Text("Advanced", color = Q.text2, fontSize = 15.sp)
            Spacer(Modifier.weight(1f))
            Text(if (advanced) "Hide" else "Show", color = Q.muted, fontSize = 13.sp)
        }
        if (advanced) {
            Label("Seed")
            Spacer(Modifier.height(8.dp))
            Field(s.seedText, { v -> s.seedText = v.filter { it.isDigit() }.take(12) }, "Random", number = true)
            Spacer(Modifier.height(4.dp))
            Hint("Same seed + same prompt = the same picture.")
            Spacer(Modifier.height(14.dp))
            Label("Things to avoid")
            Spacer(Modifier.height(8.dp))
            Field(s.negative, { s.negative = it }, "blurry, extra fingers, watermark...", minLines = 2)
            Spacer(Modifier.height(10.dp))
        }
        Spacer(Modifier.height(10.dp))
        Hint(
            when (s.power) {
                Power.READY -> (if (s.quality == "quality") "About 3 min per image." else "About 25 s per image.") +
                    s.jobs.count { it.active }.let { if (it > 0) " $it jobs ahead." else "" }
                else -> "Server is off: the image starts once it's on."
            },
        )
        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (s.nsfw) {
                Badge("NSFW on", warn = true)
                Spacer(Modifier.width(10.dp))
            }
            PrimaryButton("Generate", Modifier.weight(1f)) { s.generate() }
        }
    }
    Spacer(Modifier.height(16.dp))
    StatusCard(s, onPower)
}

// ---------------------------------------------------------------- Edit
@Composable
fun EditScreen(s: Studio, onPower: () -> Unit) {
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri -> if (uri != null) s.editImage = uri }
    val choose = { pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }
    Glass {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Label("Your photo")
            Spacer(Modifier.weight(1f))
            if (s.editImage != null) TextLink("Change", Q.accent) { choose() }
        }
        Spacer(Modifier.height(10.dp))
        val img = s.editImage
        if (img == null) {
            Column(
                Modifier.fillMaxWidth().height(200.dp).clip(Shape16).background(Color(0x59000000)).border(1.dp, Q.line2, Shape16).clickable { choose() },
                horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
            ) {
                IconTile(QIcons.image)
                Spacer(Modifier.height(12.dp))
                Text("Choose a photo", color = Q.text, fontSize = 15.sp, fontWeight = FontWeight.Medium)
                Hint("from your gallery")
            }
        } else {
            Thumb(img.toString(), null, 1280, Modifier.fillMaxWidth().height(260.dp).clip(Shape16).clickable { choose() }, ContentScale.Fit)
        }
        Spacer(Modifier.height(18.dp))
        Label("What should change")
        Spacer(Modifier.height(10.dp))
        Field(s.editPrompt, { s.editPrompt = it }, "Make it golden hour and add snow on the roofs...", minLines = 3)
        Spacer(Modifier.height(16.dp))
        Label("How many versions")
        Spacer(Modifier.height(10.dp))
        Segmented((1..4).map { "$it" to null }, s.editCount - 1) { s.editCount = it + 1 }
        Spacer(Modifier.height(18.dp))
        PrimaryButton("Edit photo", Modifier.fillMaxWidth()) { s.editPhoto(rembg = false) }
        Spacer(Modifier.height(10.dp))
        GhostButton("Remove background", Modifier.fillMaxWidth(), icon = QIcons.scissors) { s.editPhoto(rembg = true) }
        Spacer(Modifier.height(12.dp))
        Hint("Edit about 45 s, Remove background about 4 min. NSFW detail is never used on photos." +
            if (s.power != Power.READY) " Server is off: it starts once it's on." else "")
    }
    Spacer(Modifier.height(16.dp))
    StatusCard(s, onPower)
}

// ---------------------------------------------------------------- My images
@Composable
fun ImagesScreen(s: Studio, open: (ViewItem) -> Unit) {
    val saved = s.library.sortedByDescending { it.created }
    val savedIds = saved.map { it.jobId }.toSet()
    val remote = s.jobs.filter { it.hasImage && it.id !in savedIds && it.id !in s.hidden }.sortedByDescending { it.finished ?: 0.0 }
    val active = s.jobs.filter { it.active && it.kind != "improve" }.sortedBy { it.created }
    val failed = s.jobs.filter { it.status == "failed" && it.kind != "improve" && it.id !in s.hidden }
    LazyVerticalGrid(
        GridCells.Fixed(3), Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 140.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item(span = { GridItemSpan(3) }) {
            Text("My images", Modifier.padding(top = 4.dp, bottom = 8.dp), color = Q.text, fontSize = 30.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-1).sp)
        }
        if (active.isNotEmpty() || s.outbox.isNotEmpty() || s.fails.isNotEmpty() || failed.isNotEmpty()) {
            item(span = { GridItemSpan(3) }) {
                Glass(Modifier.padding(bottom = 8.dp), pad = 16.dp) {
                    H2("Queue")
                    s.fails.toList().forEach { f ->
                        JobRow(f.label, f.error, null, bad = true) { s.dismissFail(f) }
                    }
                    failed.forEach { j -> JobRow(jobTitle(s, j), j.error ?: "Failed", null, bad = true) { s.hide(j) } }
                    active.forEachIndexed { i, j ->
                        val sub = when (j.status) {
                            "running", "submitting" -> "GPU ${(j.gpu ?: 0) + 1} · ${j.phase ?: "working"}" + (j.eta?.let { " · about $it s left" } ?: "")
                            else -> if (i == 0) "Starts next" else "$i ahead"
                        } + (if (j.source != Requests.SOURCE && j.source.isNotBlank()) " · from ${j.source}" else "") + (j.note?.let { "\n$it" } ?: "")
                        JobRow(jobTitle(s, j), sub, if (j.status == "running") j.progress.toFloat() else null) { s.cancel(j) }
                    }
                    s.outbox.toList().forEach { o ->
                        JobRow(s.label(o.body), o.note ?: "Waiting for the server to be on", null) { s.removeOutbox(o) }
                    }
                }
            }
        }
        if (saved.isEmpty() && remote.isEmpty()) {
            item(span = { GridItemSpan(3) }) {
                Glass {
                    H2("No images yet")
                    Hint("Make one in Create. Finished images are saved in Pictures/${Library.FOLDER} on this phone.")
                }
            }
        }
        if (remote.isNotEmpty()) {
            item(span = { GridItemSpan(3) }) { Section("On the server", "${remote.size} not saved on this phone") }
            items(remote, key = { "r" + it.id }) { j ->
                Tile(s.imageUrl(j), s.acc.key, tag(j.kind, j.params.optString("aspect")), from = j.source.takeIf { it != Requests.SOURCE }) { open(ViewItem(job = j)) }
            }
        }
        if (saved.isNotEmpty()) {
            item(span = { GridItemSpan(3) }) { Section("On this phone", "${saved.size} in Pictures/${Library.FOLDER}") }
            items(saved, key = { "s" + it.jobId }) { v -> Tile(v.uri, null, tag(v.kind, v.aspect)) { open(ViewItem(saved = v)) } }
        }
    }
}

private fun jobTitle(s: Studio, j: Job) = when (j.kind) {
    "rembg" -> "Remove background"
    "edit" -> "Edit: ${j.prompt}"
    else -> j.prompt
}

private fun tag(kind: String, aspect: String?) = when (kind) { "edit" -> "Edit"; "rembg" -> "No bg"; else -> aspect ?: "" }

@Composable
private fun Section(title: String, sub: String) {
    Row(Modifier.padding(top = 10.dp, bottom = 2.dp), verticalAlignment = Alignment.Bottom) {
        Text(title, color = Q.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.width(10.dp))
        Hint(sub)
    }
}

@Composable
private fun JobRow(title: String, sub: String, progress: Float?, bad: Boolean = false, onX: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(top = 10.dp).clip(Shape16).background(Q.panel2).border(1.dp, Q.line, Shape16).padding(start = 14.dp, top = 12.dp, bottom = 12.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title.ifBlank { "Image" }, color = Q.text, fontSize = 14.5.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(3.dp))
            Text(sub, color = if (bad) Q.bad else Q.muted, fontSize = 13.sp, lineHeight = 18.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
            if (progress != null) { Spacer(Modifier.height(8.dp)); Bar(progress) }
        }
        Box(Modifier.size(40.dp).clip(CircleShape).clickable(onClick = onX), contentAlignment = Alignment.Center) {
            Icon(QIcons.close, if (bad) "Dismiss" else "Cancel", Modifier.size(16.dp), tint = Q.muted)
        }
    }
}

@Composable
private fun Tile(src: String?, key: String?, tag: String, from: String? = null, onClick: () -> Unit) {
    Box(Modifier.aspectRatio(1f).clip(RoundedCornerShape(14.dp)).border(1.dp, Q.line, RoundedCornerShape(14.dp)).clickable(onClick = onClick)) {
        Thumb(src, key, 360, Modifier.fillMaxSize())
        if (tag.isNotBlank()) Text(tag, Modifier.padding(6.dp).clip(Pill).background(Color(0x99000000)).padding(horizontal = 7.dp, vertical = 1.dp), color = Q.text, fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold)
        if (from != null) Text(from, Modifier.align(Alignment.BottomStart).padding(6.dp).clip(Pill).background(Color(0x99000000)).padding(horizontal = 7.dp, vertical = 1.dp), color = Q.accent, fontSize = 10.sp)
    }
}

@Composable
fun Thumb(src: String?, key: String?, maxSide: Int, modifier: Modifier, scale: ContentScale = ContentScale.Crop) {
    val ctx: Context = LocalContext.current
    var img by remember(src, maxSide) { mutableStateOf<ImageBitmap?>(src?.let { Images.cached("$it@$maxSide") }) }
    LaunchedEffect(src, maxSide) {
        if (img == null && src != null) img = withContext(Dispatchers.IO) { runCatching { Images.load(ctx, src, maxSide, key) }.getOrNull() }
    }
    Box(modifier.background(Q.panel3)) {
        img?.let { Image(it, null, Modifier.fillMaxSize(), contentScale = scale) }
    }
}

// ---------------------------------------------------------------- viewer
@Composable
private fun Viewer(s: Studio, v: ViewItem, close: () -> Unit) {
    val sv = v.saved
    val j = v.job
    val prompt = sv?.prompt ?: j?.prompt ?: ""
    val kind = sv?.kind ?: j?.kind ?: "create"
    val aspect = sv?.aspect ?: j?.params?.optString("aspect")?.ifBlank { null }
    val quality = sv?.quality ?: j?.params?.optString("quality")?.ifBlank { null }
    val seed = sv?.seed ?: j?.params?.optLong("seed", -1) ?: -1
    val neg = sv?.negative ?: j?.params?.optString("negative") ?: ""
    var confirmDelete by remember { mutableStateOf(false) }
    Dialog(close, DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
        Box(Modifier.fillMaxSize().background(Color(0xF2000000))) {
            Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
                Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(48.dp).clip(CircleShape).clickable(onClick = close), contentAlignment = Alignment.Center) {
                        Icon(QIcons.close, "Close", Modifier.size(22.dp), tint = Q.text)
                    }
                    Spacer(Modifier.weight(1f))
                    if (sv == null && j != null) Hint("On the server" + (j.source.takeIf { it.isNotBlank() && it != Requests.SOURCE }?.let { " · made on $it" } ?: ""))
                    else if (sv != null) Hint(SimpleDateFormat("d MMM, HH:mm", Locale.getDefault()).format(sv.created))
                    Spacer(Modifier.width(12.dp))
                }
                Thumb(sv?.uri ?: j?.let { s.imageUrl(it) }, if (sv == null) s.acc.key else null, 2048, Modifier.weight(1f).fillMaxWidth(), ContentScale.Fit)
                Glass(Modifier.padding(12.dp), pad = 16.dp) {
                    Text(if (kind == "rembg") "Remove background" else prompt, Modifier.heightIn(max = 110.dp).verticalScroll(rememberScrollState()),
                        color = Q.text, fontSize = 14.5.sp, lineHeight = 20.sp)
                    Spacer(Modifier.height(8.dp))
                    Hint(listOfNotNull(
                        when (kind) { "edit" -> "Edit"; "rembg" -> "No background"; else -> aspect }, quality?.replaceFirstChar { it.uppercase() }?.takeIf { kind == "create" },
                        seed.takeIf { it >= 0 }?.let { "seed $it" }, (sv?.w ?: j?.resultW)?.let { "$it × ${sv?.h ?: j?.resultH}" },
                        "NSFW".takeIf { sv?.nsfw ?: j?.params?.optBoolean("nsfw") ?: false },
                    ).joinToString(" · "))
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (sv != null) {
                            ActionChip("Share", QIcons.share, Modifier.weight(1f)) { s.share(sv) }
                            if (kind != "rembg") ActionChip("Reuse", QIcons.reuse, Modifier.weight(1f)) { s.reuse(kind, prompt, neg, aspect, quality, seed); close() }
                            ActionChip("Edit", QIcons.pencil, Modifier.weight(1f)) { s.editThis(sv); close() }
                            ActionChip("Delete", QIcons.trash, Modifier.weight(1f), Q.bad) { confirmDelete = true }
                        } else if (j != null) {
                            ActionChip("Save", QIcons.download, Modifier.weight(1f)) { s.save(j) { close() } }
                            ActionChip("Share", QIcons.share, Modifier.weight(1f)) { s.save(j, quiet = true) { saved -> s.share(saved) } }
                            if (kind != "rembg") ActionChip("Reuse", QIcons.reuse, Modifier.weight(1f)) { s.reuse(kind, prompt, neg, aspect, quality, seed); close() }
                            ActionChip("Hide", QIcons.eyeOff, Modifier.weight(1f)) { s.hide(j); close() }
                        }
                    }
                }
            }
        }
    }
    if (confirmDelete && sv != null) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false }, containerColor = Color(0xFF141416), titleContentColor = Q.text, textContentColor = Q.text2,
            title = { Text("Delete this image?", fontWeight = FontWeight.SemiBold) },
            text = { Text("It is removed from Pictures/${Library.FOLDER} on this phone. The laptop keeps its own copy.") },
            confirmButton = { TextLink("Delete", Q.bad) { confirmDelete = false; s.delete(sv); close() } },
            dismissButton = { TextLink("Cancel") { confirmDelete = false } },
        )
    }
}

@Composable
private fun ActionChip(label: String, icon: androidx.compose.ui.graphics.vector.ImageVector, modifier: Modifier, color: Color = Q.text, onClick: () -> Unit) {
    Column(
        modifier.clip(Shape16).background(Q.panel3).border(1.dp, Q.line, Shape16).clickable(onClick = onClick).padding(vertical = 10.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(icon, null, Modifier.size(20.dp), tint = color)
        Spacer(Modifier.height(4.dp))
        Text(label, color = color, fontSize = 12.5.sp)
    }
}

// ---------------------------------------------------------------- Settings
@Composable
fun SettingsScreen(s: Studio, onPower: () -> Unit) {
    var adv by remember { mutableStateOf(false) }
    Text("Settings", Modifier.padding(top = 4.dp, bottom = 12.dp), color = Q.text, fontSize = 30.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-1).sp)
    Glass {
        IconTile(QIcons.server)
        Spacer(Modifier.height(14.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            H2("Server", Modifier.weight(1f))
            val on = s.power == Power.READY || s.power == Power.STARTING
            GhostButton(if (on) "Turn off" else "Turn on", enabled = !s.busy && s.power != Power.STOPPING && s.power != Power.CHECKING, onClick = onPower)
        }
        Spacer(Modifier.height(8.dp))
        KeyValue("Status", powerShort(s))
        if (s.power == Power.STARTING || s.power == Power.ERROR) Text(s.powerDetail, color = if (s.power == Power.ERROR) Q.bad else Q.muted, fontSize = 13.sp, lineHeight = 18.sp)
        KeyValue("Kaggle", s.acc.kernel)
        val h = s.health
        if (h != null && (s.power == Power.READY || s.power == Power.STARTING)) {
            KeyValue("Up for", mins(h.uptime))
            KeyValue("Idle", "${mins(h.idleS)} of ${mins(h.idleLimitS)}")
            KeyValue("Prompt helper", if (h.peT2i) "ready" else "loading")
            KeyValue("NSFW detail", when (h.nsfw) { "ok" -> "working"; "unavailable" -> "unavailable"; "checking" -> "checking"; else -> "off" })
        }
        s.quota?.let { q ->
            KeyValue("GPU hours", "%.1f used of %.0f".format(q.usedH, q.totalH))
            q.refresh?.let { KeyValue("Resets", it.take(10)) }
        }
    }
    Spacer(Modifier.height(16.dp))
    Glass {
        IconTile(QIcons.bolt)
        Spacer(Modifier.height(14.dp))
        H2("Saving GPU hours")
        Spacer(Modifier.height(6.dp))
        SliderRow("Turn off after no use", "${s.idleMin} min", s.idleMin.toFloat(), 10f..60f, 9) { s.idleMin = (it / 5).toInt() * 5; s.savePrefs() }
        SliderRow("Longest session", "${s.maxHours} h", s.maxHours.toFloat(), 1f..12f, 10) { s.maxHours = it.toInt(); s.savePrefs() }
        Hint("Used the next time the server is turned on. The server stops itself when nobody uses it, even if this phone is off.")
    }
    Spacer(Modifier.height(16.dp))
    Glass {
        IconTile(QIcons.eyeOff)
        Spacer(Modifier.height(14.dp))
        H2("NSFW detail")
        Spacer(Modifier.height(10.dp))
        Row(
            Modifier.fillMaxWidth().clip(Shape16).background(Q.panel2).border(1.dp, Q.line, Shape16).padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            QSwitch(s.nsfw) { s.nsfw = it; s.savePrefs() }
            Spacer(Modifier.width(14.dp))
            Column {
                Text("Allow adult detail", color = Q.text, fontSize = 15.sp, fontWeight = FontWeight.Medium)
                Text("adds the NSFW LoRA to every new Create image", color = Q.muted, fontSize = 12.5.sp, lineHeight = 17.sp)
            }
        }
        Spacer(Modifier.height(10.dp))
        when (s.health?.nsfw) {
            "ok" -> Hint("On and working on this server.")
            "unavailable" -> Hint("NSFW detail unavailable on this server, so images are made without it.", color = Q.warn)
            else -> {}
        }
        Row(Modifier.fillMaxWidth().clip(Shape16).clickable { adv = !adv }.padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(QIcons.sliders, null, Modifier.size(18.dp), tint = Q.text2)
            Spacer(Modifier.width(10.dp))
            Text("Advanced", color = Q.text2, fontSize = 15.sp)
        }
        if (adv) SliderRow("Strength", "%.1f".format(s.nsfwStrength), s.nsfwStrength, 0f..2f, 19) { s.nsfwStrength = Math.round(it * 10) / 10f; s.savePrefs() }
        Hint("Create only: never for photo edits (Edit, Remove background). Anything sexual involving minors is always blocked.")
    }
    if (Accounts.signIn) {
        var askOut by remember { mutableStateOf(false) }
        Spacer(Modifier.height(16.dp))
        Glass {
            IconTile(QIcons.user)
            Spacer(Modifier.height(14.dp))
            H2("Kaggle account")
            Spacer(Modifier.height(6.dp))
            KeyValue("Signed in as", s.acc.kaggleUser)
            Hint("Your server runs in your own Kaggle account and uses your free GPU hours.")
            Spacer(Modifier.height(12.dp))
            GhostButton("Sign out", color = Q.bad) { askOut = true }
        }
        if (askOut) AlertDialog(
            onDismissRequest = { askOut = false }, containerColor = Color(0xFF141416), titleContentColor = Q.text, textContentColor = Q.text2,
            title = { Text("Sign out of Kaggle?", fontWeight = FontWeight.SemiBold) },
            text = { Text("The token is removed from this phone. Your saved pictures stay.", fontSize = 15.sp, lineHeight = 21.sp) },
            confirmButton = { TextLink("Sign out", Q.bad) { askOut = false; s.signOut() } },
            dismissButton = { TextLink("Cancel") { askOut = false } },
        )
    }
    Spacer(Modifier.height(16.dp))
    Glass {
        IconTile(QIcons.info)
        Spacer(Modifier.height(14.dp))
        H2("About")
        Spacer(Modifier.height(6.dp))
        KeyValue("Images", "Pictures/${Library.FOLDER}")
        KeyValue("On this phone", "${s.library.size}")
        KeyValue("Shared queue", s.acc.topic)
        KeyValue("Version", BuildConfig.VERSION_NAME + if (BuildConfig.EDITION.isNotEmpty()) " (${BuildConfig.EDITION})" else "")
        Spacer(Modifier.height(6.dp))
        if (!Accounts.signIn) Hint("This phone and the Windows app use the same server and the same queue.")
        Spacer(Modifier.height(10.dp))
        Hint("Qwen-Image-2.1 model licence: research and non-commercial use only.")
    }
}

private fun mins(sec: Int) = if (sec < 60) "$sec s" else if (sec < 3600) "${sec / 60} min" else "%d h %02d min".format(sec / 3600, (sec % 3600) / 60)

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun SliderRow(label: String, value: String, v: Float, range: ClosedFloatingPointRange<Float>, steps: Int, onChange: (Float) -> Unit) {
    Spacer(Modifier.height(10.dp))
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), color = Q.text2, fontSize = 14.sp)
        Text(value, color = Q.accent, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
    }
    val c = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Q.accent, inactiveTrackColor = Color(0x24FFFFFF),
        activeTickColor = Color.Transparent, inactiveTickColor = Color.Transparent)
    Slider(
        value = v, onValueChange = onChange, valueRange = range, steps = steps, colors = c,
        thumb = { Box(Modifier.size(22.dp).clip(CircleShape).background(Color.White)) },
        track = { st -> SliderDefaults.Track(sliderState = st, modifier = Modifier.height(6.dp), colors = c, drawStopIndicator = null, thumbTrackGapSize = 0.dp) },
    )
}
