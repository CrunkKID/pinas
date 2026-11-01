package de.pinas.app

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import java.net.Inet4Address

/**
 * Sucht im WLAN nach Netzlaufwerken (Samba meldet sich per Bonjour/mDNS als „_smb._tcp“).
 * Ob es wirklich ein PiNAS ist, prüft danach SetupActivity.
 */
class NasDiscovery(context: Context, private val onFound: (name: String, ip: String) -> Unit) {
    private val nsd = context.getSystemService(NsdManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val queue = ArrayDeque<NsdServiceInfo>()
    private var resolving = false
    private var running = false

    private val discovery = object : NsdManager.DiscoveryListener {
        override fun onDiscoveryStarted(serviceType: String) {}
        override fun onDiscoveryStopped(serviceType: String) {}
        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
            main.post { running = false }
        }
        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
        override fun onServiceLost(info: NsdServiceInfo) {}
        override fun onServiceFound(info: NsdServiceInfo) {
            main.post {
                queue.addLast(info)
                resolveNext()
            }
        }
    }

    fun start() {
        if (running) return
        running = true
        runCatching { nsd.discoverServices("_smb._tcp", NsdManager.PROTOCOL_DNS_SD, discovery) }
            .onFailure { running = false }
    }

    fun stop() {
        if (!running) return
        running = false
        queue.clear()
        runCatching { nsd.stopServiceDiscovery(discovery) }
    }

    // Android kann nur eine Auflösung gleichzeitig – daher der Reihe nach
    @Suppress("DEPRECATION")
    private fun resolveNext() {
        if (resolving || !running) return
        val info = queue.removeFirstOrNull() ?: return
        resolving = true
        nsd.resolveService(info, object : NsdManager.ResolveListener {
            override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                main.post { resolving = false; resolveNext() }
            }
            override fun onServiceResolved(info: NsdServiceInfo) {
                // Nur IPv4: Das Zertifikat des PiNAS enthält keine IPv6-Adressen
                val ip = (info.host as? Inet4Address)?.hostAddress
                main.post {
                    resolving = false
                    if (running && ip != null) onFound(info.serviceName, ip)
                    resolveNext()
                }
            }
        })
    }
}
