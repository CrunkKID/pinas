package de.pinas.app

import android.app.Application
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class PiNasApp : Application() {
    /** Läuft weiter, auch wenn der Bildschirm wechselt – z. B. für Downloads. */
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val store by lazy { NasStore(this) }
}
