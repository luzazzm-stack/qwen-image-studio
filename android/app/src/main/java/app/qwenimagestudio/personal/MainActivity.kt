package app.qwenimagestudio.personal

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

class MainActivity : ComponentActivity() {
    /** null in the public app until the user has signed in to Kaggle. */
    private var studio by mutableStateOf<Studio?>(null)
    private var pendingDelete: Saved? = null
    private var started = false

    private val deleteRequest = registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { r ->
        val s = pendingDelete
        pendingDelete = null
        if (r.resultCode == Activity.RESULT_OK && s != null) studio?.forget(s)
    }

    private fun attach() {
        val s = Studio.get(this) ?: return
        s.deleteLauncher = { sender, saved ->
            pendingDelete = saved
            deleteRequest.launch(IntentSenderRequest.Builder(sender).build())
        }
        s.onSignedOut = { studio = null }
        studio = s
        if (started) s.start()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        attach()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Q.accent, background = Q.bg, surface = Q.bg)) {
                val s = studio
                if (s != null) App(s) else SignInScreen { attach() }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        started = true
        studio?.start()
    }

    override fun onStop() {
        started = false
        studio?.stop()
        super.onStop()
    }
}
