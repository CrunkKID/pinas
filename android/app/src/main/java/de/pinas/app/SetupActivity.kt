package de.pinas.app

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.security.cert.X509Certificate

/** Erster Start bzw. „Verbindung ändern“: PiNAS finden, Zertifikat bestätigen, Adresse merken. */
class SetupActivity : AppCompatActivity() {
    private val store get() = (application as PiNasApp).store
    private lateinit var address: TextInputEditText
    private lateinit var connect: MaterialButton
    private lateinit var progress: LinearProgressIndicator
    private lateinit var error: TextView
    private lateinit var found: LinearLayout
    private lateinit var searching: TextView
    private lateinit var discovery: NasDiscovery
    private val seen = mutableSetOf<String>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_setup)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.root)) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        address = findViewById(R.id.address)
        connect = findViewById(R.id.connect)
        progress = findViewById(R.id.progress)
        error = findViewById(R.id.error)
        found = findViewById(R.id.found)
        searching = findViewById(R.id.searching)

        address.setText(store.baseUrl?.substringAfter("://") ?: "pinas.local")
        connect.setOnClickListener { connect(address.text.toString()) }
        address.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_GO) connect(address.text.toString())
            action == EditorInfo.IME_ACTION_GO
        }

        discovery = NasDiscovery(this) { name, ip -> onDeviceFound(name, ip) }
    }

    override fun onStart() {
        super.onStart()
        discovery.start()
    }

    override fun onStop() {
        discovery.stop()
        super.onStop()
    }

    /** Gefundenes Netzlaufwerk: nur anzeigen, wenn dort wirklich ein PiNAS antwortet. */
    private fun onDeviceFound(name: String, ip: String) {
        if (!seen.add(ip)) return
        lifecycleScope.launch {
            val probe = withContext(Dispatchers.IO) { NasTrust.probe("https://$ip") }
            if (probe is NasTrust.Probe.Failed) return@launch
            searching.text = getString(R.string.setup_found_hint)
            val button = layoutInflater.inflate(R.layout.item_found, found, false) as MaterialButton
            button.text = getString(R.string.setup_found_item, name, ip)
            button.setOnClickListener { connect(ip) }
            found.addView(button)
        }
    }

    private fun connect(input: String) {
        val base = NasTrust.normalize(input)
        if (base == null) {
            showError(getString(R.string.setup_invalid))
            return
        }
        showError(null)
        setBusy(true)
        lifecycleScope.launch {
            val probe = withContext(Dispatchers.IO) { NasTrust.probe(base) }
            setBusy(false)
            when (probe) {
                is NasTrust.Probe.Failed -> showError(probe.message)
                is NasTrust.Probe.Plain -> finishWith(base, null)
                is NasTrust.Probe.Secure -> confirmCertificate(base, probe.ca)
            }
        }
    }

    /** Wie beim PC: Fingerabdruck vergleichen, bevor dem Gerät vertraut wird. */
    private fun confirmCertificate(base: String, ca: X509Certificate) {
        val message = getString(
            R.string.setup_cert_message,
            base.substringAfter("://"),
            NasTrust.commonName(ca),
            NasTrust.fingerprint(ca, "SHA-1"),
        )
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.setup_cert_title)
            .setMessage(message)
            .setPositiveButton(R.string.setup_cert_ok) { _, _ -> finishWith(base, ca) }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun finishWith(base: String, ca: X509Certificate?) {
        store.save(base, ca)
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
        )
        finish()
    }

    private fun setBusy(busy: Boolean) {
        progress.isVisible = busy
        connect.isEnabled = !busy
        address.isEnabled = !busy
    }

    private fun showError(text: String?) {
        error.text = text
        error.visibility = if (text == null) View.GONE else View.VISIBLE
    }
}
