import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Two apps from one source:
//   personal  app.qwenimagestudio.personal  the owner's account bundled at build time (never published)
//   client    app.qwenimagestudio           public: every user signs in with their own Kaggle account
// The signing keys and the bundled account secrets stay out of git (see .gitignore).
fun props(name: String) = Properties().apply {
    val f = rootProject.file("keystore/$name")
    if (f.exists()) f.inputStream().use { load(it) }
}
val keyProps = props("keystore.properties")       // personal key
val clientKeyProps = props("client.properties")   // public app key (a different one)

android {
    namespace = "app.qwenimagestudio.personal"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.qwenimagestudio.personal"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        base.archivesName.set("Qwen-Image-Studio-v$versionName")
    }

    signingConfigs {
        for ((name, p) in listOf("release" to keyProps, "client" to clientKeyProps)) {
            if (p.isNotEmpty()) {
                create(name) {
                    storeFile = rootProject.file("keystore/" + p.getProperty("storeFile"))
                    storePassword = p.getProperty("storePassword")
                    keyAlias = p.getProperty("keyAlias")
                    keyPassword = p.getProperty("keyPassword")
                }
            }
        }
    }

    flavorDimensions += "edition"
    productFlavors {
        create("personal") {
            dimension = "edition"
            applicationId = "app.qwenimagestudio.personal"
            buildConfigField("boolean", "SIGN_IN", "false")
            buildConfigField("String", "EDITION", "\"personal\"")
            // debug and release both use the personal key (as before), so a debug build installs over the release
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
        create("client") {
            dimension = "edition"
            applicationId = "app.qwenimagestudio"
            buildConfigField("boolean", "SIGN_IN", "true")
            buildConfigField("String", "EDITION", "\"\"")
            signingConfig = signingConfigs.findByName("client") ?: signingConfigs.getByName("debug")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = null      // each flavor's own key (above)
        }
        debug {
            signingConfig = null
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

// ---- Build-time bundling (one source of truth: the Windows app's folder, read-only) ----
// 1. Both apps: the kernel template + prompt-enhancer prompts, so On pushes exactly what the Windows app pushes.
// 2. personal only: the account: Kaggle token (~/.kaggle-leo6/access_token), the permanent gateway key + ntfy topic
//    (../data/state.json) and the server defaults (../data/settings.json). Written only into build/, never into src/.
//    The client app never gets these: it has no task for them and its users sign in on the phone.
val studio = rootProject.projectDir.resolve("..")
val genAssets = layout.buildDirectory.dir("generated/qisAssets")
val genAccount = layout.buildDirectory.dir("generated/qisAccount")
val bundleServer = tasks.register("bundleServer") {
    inputs.file(studio.resolve("kaggle/qis_server.py"))
    inputs.dir(studio.resolve("prompts"))
    outputs.dir(genAssets)
    doLast {
        val out = genAssets.get().asFile.resolve("qis")
        out.mkdirs()
        out.resolve("account.json").delete()      // older builds put the account here
        studio.resolve("kaggle/qis_server.py").copyTo(out.resolve("qis_server.py"), overwrite = true)
        for (m in listOf("t2i", "i2i")) studio.resolve("prompts/pe_${m}_system.txt").copyTo(out.resolve("pe_${m}_system.txt"), overwrite = true)
    }
}
val bundleAccount = tasks.register("bundlePersonalAccount") {
    val tokenFile = File(System.getProperty("user.home"), ".kaggle-leo6/access_token")
    inputs.file(studio.resolve("data/state.json"))
    inputs.file(studio.resolve("data/settings.json"))
    inputs.file(tokenFile)
    outputs.dir(genAccount)
    doLast {
        val out = genAccount.get().asFile.resolve("qis")
        out.mkdirs()
        val state = groovy.json.JsonSlurper().parse(studio.resolve("data/state.json")) as Map<*, *>
        val settings = groovy.json.JsonSlurper().parse(studio.resolve("data/settings.json")) as Map<*, *>
        val key = state["key"] as String? ?: error("data/state.json has no key")
        val topic = state["topic"] as String? ?: error("data/state.json has no topic")
        val account = mapOf(
            "kaggle_user" to (settings["kaggle_user"] ?: "leomaharjan"),
            "kaggle_slug" to (settings["kaggle_slug"] ?: "qwen-image-studio-server"),
            "kaggle_token" to tokenFile.readText().trim(),
            "key" to key, "topic" to topic,
            "idle_min" to (settings["idle_min"] ?: 20), "max_hours" to (settings["max_hours"] ?: 6),
            "nsfw_lora_repo" to (settings["nsfw_lora_repo"] ?: "f23gg/NSFW-LORA-Qwen-Image-2.1"),
            "nsfw_lora_file" to (settings["nsfw_lora_file"] ?: "NSFW Qwen Lora.safetensors"),
        )
        out.resolve("account.json").writeText(groovy.json.JsonOutput.toJson(account))
    }
}
android.sourceSets["main"].assets.srcDir(genAssets)
android.sourceSets["personal"].assets.srcDir(genAccount)
tasks.named("preBuild") { dependsOn(bundleServer) }
tasks.configureEach { if (name.startsWith("prePersonal") && name.endsWith("Build")) dependsOn(bundleAccount) }

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.02")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
