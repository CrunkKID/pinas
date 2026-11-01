package de.pinas.app

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.widget.Toast
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.URLDecoder

/**
 * Lädt Dateien vom PiNAS in den Ordner „Download“ des Handys.
 * Der Android-Download-Manager kennt das PiNAS-Zertifikat nicht – deshalb lädt die App selbst,
 * mit derselben Anmeldung (Cookie) wie die Weboberfläche.
 */
class NasDownloader(private val app: PiNasApp) {

    fun download(url: String, userAgent: String, contentDisposition: String?, mimeType: String?) {
        val name = fileName(url, contentDisposition, mimeType)
        toast(app.getString(R.string.download_started, name))
        app.scope.launch {
            val result = runCatching { fetch(url, userAgent, name, mimeType) }
            withContext(Dispatchers.Main) {
                result.fold(
                    onSuccess = { toast(app.getString(R.string.download_done, name)) },
                    onFailure = { toast(app.getString(R.string.download_failed, name)) },
                )
            }
        }
    }

    private fun fetch(url: String, userAgent: String, name: String, mimeType: String?): Uri {
        val conn = NasTrust.open(url, app.store.ca)
        CookieManager.getInstance().getCookie(url)?.let { conn.setRequestProperty("Cookie", it) }
        conn.setRequestProperty("User-Agent", userAgent)
        if (conn.responseCode != 200) throw IllegalStateException("HTTP ${conn.responseCode}")

        val resolver = app.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, conn.contentType?.substringBefore(';') ?: mimeType ?: "application/octet-stream")
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val target = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IllegalStateException("Download-Ordner nicht verfügbar")
        try {
            conn.inputStream.use { input ->
                resolver.openOutputStream(target)!!.use { output -> input.copyTo(output, 64 * 1024) }
            }
            resolver.update(target, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            return target
        } catch (e: Exception) {
            resolver.delete(target, null, null) // keine halben Dateien liegen lassen
            throw e
        } finally {
            conn.disconnect()
        }
    }

    private fun toast(text: String) = Toast.makeText(app, text, Toast.LENGTH_SHORT).show()

    companion object {
        /** Flask schickt Umlaute als filename*=UTF-8''…, das versteht URLUtil nicht. */
        fun fileName(url: String, contentDisposition: String?, mimeType: String?): String {
            contentDisposition?.let { cd ->
                Regex("filename\\*=UTF-8''([^;]+)", RegexOption.IGNORE_CASE).find(cd)?.let {
                    return URLDecoder.decode(it.groupValues[1].trim(), "UTF-8")
                }
            }
            return URLUtil.guessFileName(url, contentDisposition, mimeType)
        }
    }
}
