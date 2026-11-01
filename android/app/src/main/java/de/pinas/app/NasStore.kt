package de.pinas.app

import android.content.Context
import android.util.Base64
import java.security.cert.X509Certificate

/** Gemerkte Verbindung: Adresse des PiNAS und seine Zertifizierungsstelle (CA). */
class NasStore(context: Context) {
    private val prefs = context.getSharedPreferences("pinas", Context.MODE_PRIVATE)

    /** z. B. "https://192.168.178.20" – ohne Schrägstrich am Ende */
    val baseUrl: String? get() = prefs.getString("base_url", null)

    val ca: X509Certificate?
        get() = prefs.getString("ca", null)?.let {
            runCatching { NasTrust.parseCert(Base64.decode(it, Base64.DEFAULT)) }.getOrNull()
        }

    val isConfigured: Boolean get() = baseUrl != null

    fun save(baseUrl: String, ca: X509Certificate?) {
        prefs.edit()
            .putString("base_url", baseUrl)
            .putString("ca", ca?.let { Base64.encodeToString(it.encoded, Base64.NO_WRAP) })
            .apply()
    }
}
