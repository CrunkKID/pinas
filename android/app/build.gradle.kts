plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Signatur: Auf GitHub kommt der Schlüssel aus den Secrets (siehe Workflow). Fehlt er, wird mit dem
// Debug-Schlüssel signiert – installierbar, aber ein späteres Update verlangt dann eine Neuinstallation.
val keystoreFile: String? = System.getenv("PINAS_KEYSTORE_FILE")

android {
    namespace = "de.pinas.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.pinas.app"
        minSdk = 29 // Android 10 – ab hier liefert die WebView das Zertifikat für die eigene Prüfung
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    signingConfigs {
        if (keystoreFile != null) {
            create("pinas") {
                storeFile = file(keystoreFile)
                storePassword = System.getenv("PINAS_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("PINAS_KEY_ALIAS")
                keyPassword = System.getenv("PINAS_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("pinas") ?: signingConfigs.getByName("debug")
        }
    }

    lint {
        // Die eigene Zertifikatsprüfung (NasTrust) löst Warnungen aus, die hier gewollt sind
        checkReleaseBuilds = false
        abortOnError = false
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
