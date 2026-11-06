package app.qwenimagestudio.personal

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** First screen of the public app: the user's own Kaggle account (username + API token). */
@Composable
fun SignInScreen(onSignedIn: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var user by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    fun open(url: String) = runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }

    Backdrop {
        Column(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()
                .verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp),
        ) {
            Row(Modifier.padding(start = 4.dp, top = 4.dp, bottom = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(QIcons.image, null, Modifier.size(24.dp), tint = Q.text)
                Spacer(Modifier.width(10.dp))
                Text("Qwen Image Studio", color = Q.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
            }
            Text("Sign in to Kaggle", color = Q.text, fontSize = 30.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-1).sp)
            Spacer(Modifier.height(8.dp))
            Hint("Pictures are made on your own free Kaggle GPUs (about 30 hours a week). Nothing is shared with anyone else.")
            Spacer(Modifier.height(16.dp))
            Glass {
                H2("Before you start")
                Spacer(Modifier.height(10.dp))
                Step("1", "A free Kaggle account with a verified phone number. Kaggle only gives GPUs to phone-verified accounts.")
                Row { TextLink("Create an account", Q.accent) { open("https://www.kaggle.com/account/login?phase=startRegisterTab") }; TextLink("Verify phone", Q.accent) { open("https://www.kaggle.com/settings") } }
                Spacer(Modifier.height(6.dp))
                Step("2", "An API token: on kaggle.com/settings, in the API section, tap Create New Token and copy it.")
                TextLink("Open kaggle.com/settings", Q.accent) { open("https://www.kaggle.com/settings") }
            }
            Spacer(Modifier.height(16.dp))
            Glass {
                Label("Kaggle username")
                Spacer(Modifier.height(8.dp))
                Field(user, { user = it.trim(); error = null }, "your-username")
                Spacer(Modifier.height(14.dp))
                Label("API token")
                Spacer(Modifier.height(8.dp))
                Field(token, { token = it; error = null }, "Paste the token (KGAT_...)", secret = true)
                error?.let { Spacer(Modifier.height(10.dp)); Hint(it, color = Q.bad) }
                Spacer(Modifier.height(16.dp))
                PrimaryButton(if (busy) "Checking..." else "Sign in", Modifier.fillMaxWidth(), enabled = !busy) {
                    busy = true
                    error = null
                    scope.launch {
                        try {
                            val (u, t) = Account.parseToken(user, token)
                            val name = withContext(Dispatchers.IO) { Kaggle.checkToken(u, t) }
                            withContext(Dispatchers.IO) { Accounts.save(ctx, Account.fresh(name, t)) }
                            token = ""
                            onSignedIn()
                        } catch (e: KaggleError) {
                            error = e.message
                        } catch (e: Exception) {
                            error = "Could not reach Kaggle. Check the internet and try again."
                        } finally {
                            busy = false
                        }
                    }
                }
                Spacer(Modifier.height(10.dp))
                Hint("The token is stored encrypted on this phone and used only to start and stop your own Kaggle server.")
            }
            Spacer(Modifier.height(16.dp))
            Hint("Qwen-Image-2.1 model licence: research and non-commercial use only.", Modifier.padding(horizontal = 4.dp))
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun Step(n: String, text: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text(n, color = Q.accent, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.width(22.dp))
        Text(text, color = Q.text2, fontSize = 14.5.sp, lineHeight = 20.sp)
    }
}
