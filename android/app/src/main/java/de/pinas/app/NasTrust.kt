package de.pinas.app

import java.io.ByteArrayInputStream
import java.io.FileNotFoundException
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Vertrauen zum eigenen PiNAS – ohne dass das Zertifikat in Android installiert werden muss.
 *
 * Jeder Pi hat eine eigene Zertifizierungsstelle (deploy/pinas-cert). Die App lädt sie beim Verbinden
 * einmal herunter, zeigt den Fingerabdruck zum Vergleichen und vertraut danach nur Zertifikaten,
 * die von genau dieser CA unterschrieben sind.
 */
object NasTrust {

    fun parseCert(bytes: ByteArray): X509Certificate =
        CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(bytes)) as X509Certificate

    /** Schreibweise wie auf der Seite „Sichere Verbindung“: AB:CD:… */
    fun fingerprint(cert: X509Certificate, algorithm: String): String =
        MessageDigest.getInstance(algorithm).digest(cert.encoded).joinToString(":") { "%02X".format(it) }

    fun commonName(cert: X509Certificate): String =
        Regex("CN=([^,]+)").find(cert.subjectX500Principal.name)?.groupValues?.get(1) ?: "PiNAS"

    /** Stammt das Server-Zertifikat von unserer CA und ist es noch gültig? */
    fun isSignedBy(cert: X509Certificate, ca: X509Certificate): Boolean = try {
        if (cert != ca) cert.verify(ca.publicKey)
        cert.checkValidity()
        true
    } catch (e: Exception) {
        false
    }

    /** Dieselbe Prüfung wie in der WebView (MainActivity) – der Name wird zusätzlich von Android geprüft. */
    private fun sslContextFor(ca: X509Certificate): SSLContext {
        val pinned = object : X509TrustManager {
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                if (chain.isEmpty() || !isSignedBy(chain[0], ca)) {
                    throw java.security.cert.CertificateException("Nicht vom eigenen PiNAS unterschrieben")
                }
            }
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
                throw java.security.cert.CertificateException("nicht verwendet")
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf(ca)
        }
        return SSLContext.getInstance("TLS").apply { init(null, arrayOf(pinned), null) }
    }

    /** Verbindung zum PiNAS, bei https nur mit dessen CA (für Downloads außerhalb der WebView). */
    fun open(url: String, ca: X509Certificate?): HttpURLConnection {
        val conn = URL(url).openConnection() as HttpURLConnection
        if (conn is HttpsURLConnection && ca != null) conn.sslSocketFactory = sslContextFor(ca).socketFactory
        conn.connectTimeout = 8000
        conn.readTimeout = 60000
        return conn
    }

    /**
     * Eingabe wie „pinas.local“, „192.168.178.20“ oder „https://pinas.local/dateien“ → „https://pinas.local“.
     * Ohne Angabe wird https genommen, so wie es das PiNAS seit 1.2.0 macht.
     */
    fun normalize(input: String): String? {
        var text = input.trim().trimEnd('/')
        if (text.isEmpty()) return null
        if (!text.contains("://")) text = "https://$text"
        val url = runCatching { URL(text) }.getOrNull() ?: return null
        if (url.protocol != "https" && url.protocol != "http") return null
        if (url.host.isNullOrEmpty()) return null
        val port = if (url.port == -1 || url.port == url.defaultPort) "" else ":${url.port}"
        return "${url.protocol}://${url.host}$port"
    }

    sealed class Probe {
        /** Ein PiNAS mit https – CA zum Bestätigen durch den Nutzer */
        data class Secure(val ca: X509Certificate) : Probe()
        /** Ein PiNAS ohne https (nur Entwicklung) */
        data object Plain : Probe()
        data class Failed(val message: String) : Probe()
    }

    /**
     * Prüft, ob unter [baseUrl] ein PiNAS antwortet, und holt bei https seine CA.
     * Nur hier wird einem unbekannten Zertifikat vorläufig vertraut („Trust on first use“):
     * Die geladene CA muss das Zertifikat des Servers unterschrieben haben, und der Nutzer
     * vergleicht danach den Fingerabdruck.
     */
    fun probe(baseUrl: String): Probe = try {
        if (baseUrl.startsWith("http://")) {
            val conn = URL("$baseUrl/manifest.webmanifest").openConnection() as HttpURLConnection
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            conn.instanceFollowRedirects = false
            val code = conn.responseCode
            val body = if (code == 200) conn.inputStream.bufferedReader().use { it.readText() } else ""
            conn.disconnect()
            when {
                code in 301..308 -> Probe.Failed("Dieses PiNAS nutzt https. Bitte die Adresse ohne „http://“ eingeben.")
                body.contains("\"PiNAS\"") -> Probe.Plain
                else -> Probe.Failed("Unter dieser Adresse antwortet kein PiNAS.")
            }
        } else {
            val capture = CapturingTrustManager()
            val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(capture), null) }
            val conn = URL("$baseUrl/zertifikat/pinas.crt").openConnection() as HttpsURLConnection
            conn.sslSocketFactory = ctx.socketFactory
            conn.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            val bytes = conn.inputStream.use { it.readBytes() }
            conn.disconnect()
            val ca = parseCert(bytes)
            val leaf = capture.chain?.firstOrNull()
            when {
                ca.basicConstraints < 0 -> Probe.Failed("Das PiNAS hat kein gültiges Zertifikat geliefert.")
                leaf == null || !isSignedBy(leaf, ca) ->
                    Probe.Failed("Das Zertifikat passt nicht zu diesem Gerät. Ist das wirklich dein PiNAS?")
                else -> Probe.Secure(ca)
            }
        }
    } catch (e: UnknownHostException) {
        Probe.Failed("Adresse nicht gefunden. Probiere die IP-Adresse, die der Bildschirm am Pi anzeigt (z. B. 192.168.178.20).")
    } catch (e: ConnectException) {
        Probe.Failed("Keine Antwort. Ist PiNAS eingeschaltet und dein Handy im selben WLAN?")
    } catch (e: SocketTimeoutException) {
        Probe.Failed("Keine Antwort. Ist PiNAS eingeschaltet und dein Handy im selben WLAN?")
    } catch (e: FileNotFoundException) {
        Probe.Failed("Dort antwortet kein PiNAS – oder es hat noch kein Zertifikat. Dann den Pi einmal neu starten.")
    } catch (e: Exception) {
        Probe.Failed("Verbindung fehlgeschlagen: ${e.message ?: e.javaClass.simpleName}")
    }

    /** Merkt sich das Zertifikat des Servers, statt es zu prüfen – geprüft wird danach in [probe]. */
    private class CapturingTrustManager : X509TrustManager {
        var chain: Array<X509Certificate>? = null
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            this.chain = chain
        }
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
            throw java.security.cert.CertificateException("nicht verwendet")
        }
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }
}
