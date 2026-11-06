package app.qwenimagestudio.personal

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Wan Studio / Qwen Image Studio tokens (web/style.css). */
object Q {
    val bg = Color(0xFF000000)
    val panel = Color(0x661E1E1E)
    val panel2 = Color(0x08FFFFFF)
    val panel3 = Color(0x0FFFFFFF)
    val line = Color(0x1AFFFFFF)
    val line2 = Color(0x2EFFFFFF)
    val text = Color(0xFFFFFFFF)
    val text2 = Color(0xB8FFFFFF)
    val muted = Color(0x85FFFFFF)
    val accent = Color(0xFF81D8D0)
    val accentSoft = Color(0x2481D8D0)
    val accentInk = Color(0xFF04201D)
    val warn = Color(0xFFF5C451)
    val bad = Color(0xFFFF7A7A)
    val island = Color(0x8C141416)
}

// ---------------------------------------------------------------- line icons (stroke 1.8, like the web app's)
private fun lineIcon(name: String, block: PathBuilder.() -> Unit) = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f)
    .path(fill = null, stroke = SolidColor(Color.White), strokeLineWidth = 1.8f, strokeLineCap = StrokeCap.Round,
        strokeLineJoin = StrokeJoin.Round, pathBuilder = block).build()

private fun PathBuilder.circle(cx: Float, cy: Float, r: Float) {
    moveTo(cx - r, cy); arcTo(r, r, 0f, false, true, cx + r, cy); arcTo(r, r, 0f, false, true, cx - r, cy); close()
}

private fun PathBuilder.rrect(l: Float, t: Float, r: Float, b: Float, rad: Float) {
    moveTo(l + rad, t); lineTo(r - rad, t); arcTo(rad, rad, 0f, false, true, r, t + rad); lineTo(r, b - rad)
    arcTo(rad, rad, 0f, false, true, r - rad, b); lineTo(l + rad, b); arcTo(rad, rad, 0f, false, true, l, b - rad)
    lineTo(l, t + rad); arcTo(rad, rad, 0f, false, true, l + rad, t); close()
}

object QIcons {
    val power = lineIcon("power") { moveTo(12f, 3f); lineTo(12f, 11f); moveTo(7.05f, 6.05f); arcTo(8f, 8f, 0f, true, false, 16.95f, 6.05f) }
    val sparkle = lineIcon("sparkle") {
        moveTo(12f, 3.5f); lineTo(13.7f, 10.3f); lineTo(20.5f, 12f); lineTo(13.7f, 13.7f); lineTo(12f, 20.5f)
        lineTo(10.3f, 13.7f); lineTo(3.5f, 12f); lineTo(10.3f, 10.3f); close()
    }
    val image = lineIcon("image") { rrect(3.5f, 4.5f, 20.5f, 19.5f, 3f); moveTo(4f, 17f); lineTo(9f, 12f); lineTo(13f, 16f); lineTo(15f, 14f); lineTo(20f, 18.5f); circle(15.5f, 9f, 1.6f) }
    val images = lineIcon("images") { rrect(6.5f, 3.5f, 20.5f, 15.5f, 2.5f); moveTo(3.5f, 7.5f); lineTo(3.5f, 18f); arcTo(2.5f, 2.5f, 0f, false, false, 6f, 20.5f); lineTo(16.5f, 20.5f); moveTo(7f, 13f); lineTo(11f, 9.5f); lineTo(14f, 12f); lineTo(16f, 10.5f); lineTo(20f, 13.5f) }
    val pencil = lineIcon("pencil") { moveTo(4f, 20f); lineTo(5f, 15.5f); lineTo(15.5f, 5f); arcTo(2.1f, 2.1f, 0f, false, true, 19f, 8.5f); lineTo(8.5f, 19f); close(); moveTo(13.5f, 7f); lineTo(17f, 10.5f) }
    val gear = lineIcon("gear") {
        circle(12f, 12f, 3f)
        moveTo(12f, 2.8f); lineTo(12f, 5.2f); moveTo(12f, 18.8f); lineTo(12f, 21.2f); moveTo(2.8f, 12f); lineTo(5.2f, 12f); moveTo(18.8f, 12f); lineTo(21.2f, 12f)
        moveTo(5.5f, 5.5f); lineTo(7.2f, 7.2f); moveTo(16.8f, 16.8f); lineTo(18.5f, 18.5f); moveTo(5.5f, 18.5f); lineTo(7.2f, 16.8f); moveTo(16.8f, 7.2f); lineTo(18.5f, 5.5f)
        circle(12f, 12f, 6.8f)
    }
    val arrow = lineIcon("arrow") { moveTo(5f, 12f); lineTo(19f, 12f); moveTo(13f, 6f); lineTo(19f, 12f); lineTo(13f, 18f) }
    val share = lineIcon("share") { circle(17.5f, 5.5f, 2.5f); circle(6.5f, 12f, 2.5f); circle(17.5f, 18.5f, 2.5f); moveTo(8.7f, 10.8f); lineTo(15.3f, 6.8f); moveTo(8.7f, 13.2f); lineTo(15.3f, 17.2f) }
    val trash = lineIcon("trash") { moveTo(4f, 7f); lineTo(20f, 7f); moveTo(9.5f, 7f); lineTo(9.5f, 4.5f); lineTo(14.5f, 4.5f); lineTo(14.5f, 7f); moveTo(6f, 7f); lineTo(7f, 20f); lineTo(17f, 20f); lineTo(18f, 7f); moveTo(10f, 11f); lineTo(10f, 16f); moveTo(14f, 11f); lineTo(14f, 16f) }
    val download = lineIcon("download") { moveTo(12f, 4f); lineTo(12f, 15f); moveTo(7f, 10.5f); lineTo(12f, 15.5f); lineTo(17f, 10.5f); moveTo(4.5f, 19.5f); lineTo(19.5f, 19.5f) }
    val reuse = lineIcon("reuse") { moveTo(4f, 12f); arcTo(8f, 8f, 0f, false, true, 18.2f, 7f); moveTo(19f, 3.5f); lineTo(19f, 7.5f); lineTo(15f, 7.5f); moveTo(20f, 12f); arcTo(8f, 8f, 0f, false, true, 5.8f, 17f); moveTo(5f, 20.5f); lineTo(5f, 16.5f); lineTo(9f, 16.5f) }
    val close = lineIcon("close") { moveTo(6f, 6f); lineTo(18f, 18f); moveTo(18f, 6f); lineTo(6f, 18f) }
    val sliders = lineIcon("sliders") { moveTo(4f, 7f); lineTo(20f, 7f); moveTo(4f, 17f); lineTo(20f, 17f); circle(9f, 7f, 2.2f); circle(15f, 17f, 2.2f) }
    val scissors = lineIcon("scissors") { circle(6.5f, 6.5f, 2.5f); circle(6.5f, 17.5f, 2.5f); moveTo(8.5f, 8f); lineTo(20f, 18f); moveTo(8.5f, 16f); lineTo(20f, 6f) }
    val eyeOff = lineIcon("eye") { moveTo(3f, 10f); arcTo(10f, 7f, 0f, false, false, 21f, 10f); moveTo(6f, 13.5f); lineTo(4.5f, 15.5f); moveTo(10f, 15f); lineTo(9.5f, 17.5f); moveTo(14f, 15f); lineTo(14.5f, 17.5f); moveTo(18f, 13.5f); lineTo(19.5f, 15.5f) }
    val bolt = lineIcon("bolt") { rrect(3.5f, 3.5f, 20.5f, 20.5f, 4f); moveTo(13f, 6.5f); lineTo(8.5f, 12.5f); lineTo(12f, 12.5f); lineTo(11f, 17.5f); lineTo(15.5f, 11.5f); lineTo(12f, 11.5f); close() }
    val server = lineIcon("server") { rrect(3.5f, 4f, 20.5f, 11f, 2.5f); rrect(3.5f, 13f, 20.5f, 20f, 2.5f); moveTo(7f, 7.5f); lineTo(7.1f, 7.5f); moveTo(7f, 16.5f); lineTo(7.1f, 16.5f) }
    val user = lineIcon("user") { circle(12f, 8.5f, 3.8f); moveTo(4.5f, 20f); arcTo(7.5f, 6.5f, 0f, false, true, 19.5f, 20f) }
    val info = lineIcon("info") { circle(12f, 12f, 8.5f); moveTo(12f, 11f); lineTo(12f, 16f); moveTo(12f, 8f); lineTo(12f, 8.1f) }
}

// ---------------------------------------------------------------- building blocks
@Composable
fun Backdrop(content: @Composable BoxScope.() -> Unit) {
    Box(Modifier.fillMaxSize().background(Q.bg)) {
        Canvas(Modifier.fillMaxSize()) {
            fun glow(c: Color, a: Float, center: Offset, r: Float) =
                drawCircle(Brush.radialGradient(listOf(c.copy(alpha = a), Color.Transparent), center, r), r, center)
            glow(Color(0xFF81D8D0), .10f, Offset(size.width * .05f, size.height * .02f), size.width * 0.95f)
            glow(Color(0xFF1E3A8A), .30f, Offset(size.width * 1.0f, size.height * .98f), size.width * 1.1f)
            glow(Color(0xFF7C3AED), .08f, Offset(size.width * .6f, size.height * .45f), size.width * .7f)
        }
        content()
    }
}

val Shape24 = RoundedCornerShape(24.dp)
val Shape16 = RoundedCornerShape(16.dp)
val Pill = RoundedCornerShape(999.dp)

@Composable
fun Glass(modifier: Modifier = Modifier, pad: Dp = 20.dp, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier.fillMaxWidth().clip(Shape24).background(Q.panel).border(1.dp, Q.line, Shape24).padding(pad),
        content = content,
    )
}

@Composable
fun H2(t: String, modifier: Modifier = Modifier) = Text(t, modifier, color = Q.text, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.3).sp)

@Composable
fun Label(t: String, modifier: Modifier = Modifier) = Text(t, modifier, color = Q.text2, fontSize = 14.sp, fontWeight = FontWeight.Medium)

@Composable
fun Hint(t: String, modifier: Modifier = Modifier, color: Color = Q.muted) = Text(t, modifier, color = color, fontSize = 13.sp, lineHeight = 18.sp)

/** White pill (primary). */
@Composable
fun PrimaryButton(text: String, modifier: Modifier = Modifier, enabled: Boolean = true, icon: ImageVector? = QIcons.arrow, onClick: () -> Unit) {
    Row(
        modifier.heightIn(min = 52.dp).clip(Pill).background(if (enabled) Color.White else Color(0x40FFFFFF))
            .clickable(enabled = enabled, onClick = onClick).padding(horizontal = 26.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center,
    ) {
        Text(text, color = Color.Black, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        if (icon != null) { Spacer(Modifier.width(8.dp)); Icon(icon, null, Modifier.size(18.dp), tint = Color.Black) }
    }
}

/** Dark glass pill with a hairline (secondary). */
@Composable
fun GhostButton(text: String, modifier: Modifier = Modifier, icon: ImageVector? = null, enabled: Boolean = true, color: Color = Q.text, onClick: () -> Unit) {
    Row(
        modifier.heightIn(min = 44.dp).clip(Pill).background(Q.panel3).border(1.dp, Q.line2, Pill)
            .clickable(enabled = enabled, onClick = onClick).padding(horizontal = 18.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.Center,
    ) {
        if (icon != null) { Icon(icon, null, Modifier.size(18.dp), tint = if (enabled) color else Q.muted); Spacer(Modifier.width(8.dp)) }
        Text(text, color = if (enabled) color else Q.muted, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, maxLines = 1)
    }
}

@Composable
fun TextLink(text: String, color: Color = Q.text2, onClick: () -> Unit) =
    Text(text, Modifier.clip(Pill).clickable(onClick = onClick).padding(horizontal = 10.dp, vertical = 10.dp), color = color, fontSize = 14.5.sp)

/** Segmented control: white pill on the chosen item. */
@Composable
fun Segmented(options: List<Pair<String, String?>>, selected: Int, onSelect: (Int) -> Unit) {
    Row(Modifier.fillMaxWidth().clip(Pill).background(Color(0x66000000)).border(1.dp, Q.line, Pill).padding(5.dp)) {
        options.forEachIndexed { i, (t, sub) ->
            val on = i == selected
            Column(
                Modifier.weight(1f).clip(Pill).background(if (on) Color.White else Color.Transparent)
                    .clickable { onSelect(i) }.padding(vertical = if (sub != null) 7.dp else 10.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(t, color = if (on) Color.Black else Q.text2, fontSize = 14.5.sp, fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal)
                if (sub != null) Text(sub, color = if (on) Color(0x99000000) else Q.muted, fontSize = 11.5.sp)
            }
        }
    }
}

@Composable
fun QSwitch(on: Boolean, onChange: (Boolean) -> Unit) {
    val bg by animateColorAsState(if (on) Q.accent else Q.panel3, label = "sw")
    val x by animateDpAsState(if (on) 18.dp else 0.dp, label = "knob")
    Box(Modifier.size(46.dp, 28.dp).clip(Pill).background(bg).border(1.dp, if (on) Q.accent else Q.line2, Pill).clickable { onChange(!on) }) {
        Box(Modifier.padding(3.dp).offset(x = x).size(20.dp).clip(CircleShape).background(Color.White))
    }
}

/** Text area / input in the web app's style. */
@Composable
fun Field(
    value: String, onChange: (String) -> Unit, placeholder: String, modifier: Modifier = Modifier,
    minLines: Int = 1, big: Boolean = false, number: Boolean = false, secret: Boolean = false,
) {
    val style = TextStyle(color = Q.text, fontSize = if (big) 18.sp else 15.sp, lineHeight = if (big) 26.sp else 21.sp)
    BasicTextField(
        value, onChange, modifier.fillMaxWidth(), textStyle = style, minLines = minLines, cursorBrush = SolidColor(Q.accent),
        keyboardOptions = if (number) KeyboardOptions(keyboardType = KeyboardType.Number)
            else if (secret) KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false) else KeyboardOptions.Default,
        visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        singleLine = secret,
        decorationBox = { inner ->
            Box(Modifier.fillMaxWidth().clip(Shape16).background(Color(0x59000000)).border(1.dp, Q.line, Shape16).padding(horizontal = 16.dp, vertical = 14.dp)) {
                if (value.isEmpty()) Text(placeholder, style = style.copy(color = Q.muted))
                inner()
            }
        },
    )
}

/** The five shape tiles. */
@Composable
fun ShapePicker(selected: String, onSelect: (String) -> Unit) {
    val names = mapOf("1:1" to "Square", "3:4" to "Portrait", "4:3" to "Landscape", "9:16" to "Story", "16:9" to "Wide")
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
        for (a in Requests.ASPECTS) {
            val on = a == selected
            val (w, h) = a.split(":").map { it.toFloat() }
            Column(
                Modifier.weight(1f).clip(Shape16).background(if (on) Q.accentSoft else Q.panel2)
                    .border(1.dp, if (on) Q.accent else Q.line, Shape16).clickable { onSelect(a) }.padding(vertical = 12.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Box(Modifier.height(24.dp), contentAlignment = Alignment.Center) {
                    val s = 22f / maxOf(w, h)
                    Box(Modifier.size((w * s).dp, (h * s).dp).border(1.6.dp, if (on) Q.text else Q.text2, RoundedCornerShape(4.dp)))
                }
                Spacer(Modifier.height(6.dp))
                Text(a, color = Q.text, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold)
                Text(names[a]!!, color = Q.muted, fontSize = 10.5.sp, maxLines = 1, textAlign = TextAlign.Center)
            }
        }
    }
}

@Composable
fun Badge(text: String, warn: Boolean = false) {
    val c = if (warn) Q.warn else Q.accent
    Text(text, Modifier.clip(Pill).background(c.copy(alpha = .14f)).border(1.dp, c.copy(alpha = .5f), Pill).padding(horizontal = 10.dp, vertical = 4.dp),
        color = c, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
}

@Composable
fun Bar(fraction: Float, modifier: Modifier = Modifier, color: Color = Q.accent) {
    Box(modifier.fillMaxWidth().height(4.dp).clip(Pill).background(Q.panel3)) {
        Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(4.dp).clip(Pill).background(color))
    }
}

@Composable
fun Dot(color: Color, ring: Boolean = false) {
    Box(Modifier.size(12.dp).clip(CircleShape).background(color.copy(alpha = if (ring) .25f else 0f)), contentAlignment = Alignment.Center) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
    }
}

@Composable
fun IconTile(icon: ImageVector) {
    Box(Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(Q.panel3).border(1.dp, Q.line, RoundedCornerShape(14.dp)), contentAlignment = Alignment.Center) {
        Icon(icon, null, Modifier.size(20.dp), tint = Q.accent)
    }
}

@Composable
fun KeyValue(k: String, v: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
        Text(k, Modifier.width(120.dp), color = Q.muted, fontSize = 14.sp)
        Text(v, color = Q.text, fontSize = 14.sp)
    }
}

@Composable
fun RowScope.Grow() = Spacer(Modifier.weight(1f))

@Composable
fun StrokeRing(modifier: Modifier, color: Color) = Canvas(modifier) { drawCircle(color, style = Stroke(2.dp.toPx())) }
