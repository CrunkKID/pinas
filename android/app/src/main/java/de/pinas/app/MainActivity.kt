package de.pinas.app

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.net.http.SslError
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.button.MaterialButton
import com.google.android.material.progressindicator.LinearProgressIndicator

/**
 * Die PiNAS-Weboberfläche als App. Anders als die Web-App (pwa.py) braucht sie kein in Android
 * installiertes Zertifikat: Sie vertraut nur der CA des eigenen PiNAS (siehe NasTrust).
 */
class MainActivity : AppCompatActivity() {
    private val app get() = application as PiNasApp
    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var progress: LinearProgressIndicator
    private lateinit var errorPanel: View
    private lateinit var errorTitle: TextView
    private lateinit var errorText: TextView
    private lateinit var downloader: NasDownloader
    private lateinit var base: Uri

    private var mainFrameFailed = false
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private val handler = Handler(Looper.getMainLooper())
    private val retry = object : Runnable {
        override fun run() {
            if (errorPanel.isVisible && !certProblem) web.reload()
            handler.postDelayed(this, RETRY_MS)
        }
    }
    private var certProblem = false

    private val pickFiles = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val callback = filePathCallback ?: return@registerForActivityResult
        filePathCallback = null
        val data = result.data
        val uris = when {
            result.resultCode != RESULT_OK || data == null -> null
            data.clipData != null -> Array(data.clipData!!.itemCount) { data.clipData!!.getItemAt(it).uri }
            data.data != null -> arrayOf(data.data!!)
            else -> null
        }
        callback.onReceiveValue(uris)
    }

    // WLAN wieder da → sofort neu versuchen statt auf den nächsten Takt zu warten
    private val network = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(net: Network) {
            handler.post { if (errorPanel.isVisible && !certProblem) web.reload() }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val baseUrl = app.store.baseUrl
        if (baseUrl == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        base = Uri.parse(baseUrl)
        downloader = NasDownloader(app)

        enableEdgeToEdge()
        setContentView(R.layout.activity_main)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.root)) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        web = findViewById(R.id.web)
        refresh = findViewById(R.id.refresh)
        progress = findViewById(R.id.progress)
        errorPanel = findViewById(R.id.error_panel)
        errorTitle = findViewById(R.id.error_title)
        errorText = findViewById(R.id.error_text)
        findViewById<MaterialButton>(R.id.error_retry).setOnClickListener { certProblem = false; web.reload() }
        findViewById<MaterialButton>(R.id.error_setup).setOnClickListener { openSetup() }

        refresh.setOnRefreshListener { web.reload() }
        setupWebView()

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack() && !errorPanel.isVisible) {
                    web.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl(startUrl(intent))
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            builtInZoomControls = true
            displayZoomControls = false
            // Daran erkennt die Weboberfläche die App (z. B. auf der Seite „Konto“)
            userAgentString = "$userAgentString PiNAS-App/${BuildConfig.VERSION_NAME}"
        }
        CookieManager.getInstance().setAcceptCookie(true)

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                return when {
                    url.scheme == APP_SCHEME -> { handleAppLink(url); true }
                    url.host == base.host && url.port == base.port -> false
                    else -> { openExternal(url); true } // fremde Links (z. B. GitHub) im Browser
                }
            }

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                mainFrameFailed = false
            }

            override fun onPageFinished(view: WebView, url: String?) {
                refresh.isRefreshing = false
                if (!mainFrameFailed) showError(null, null)
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame || certProblem) return
                mainFrameFailed = true
                showError(getString(R.string.offline_title), getString(R.string.offline_text))
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                val ca = app.store.ca
                val cert = error.certificate.x509Certificate
                val sameHost = Uri.parse(error.url).host == base.host
                if (ca != null && cert != null && sameHost && NasTrust.isSignedBy(cert, ca)) {
                    handler.proceed() // Zertifikat des eigenen PiNAS
                } else {
                    handler.cancel()
                    certProblem = true
                    mainFrameFailed = true
                    showError(getString(R.string.cert_title), getString(R.string.cert_text))
                }
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progress.isVisible = newProgress < 100
                progress.setProgressCompat(newProgress, true)
            }

            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback
                val intent = params.createIntent().apply {
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.mode == FileChooserParams.MODE_OPEN_MULTIPLE)
                }
                return try {
                    pickFiles.launch(intent)
                    true
                } catch (e: ActivityNotFoundException) {
                    filePathCallback = null
                    false
                }
            }
        }

        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            downloader.download(url, userAgent, contentDisposition, mimeType)
        }
    }

    /** Startseite: wie die Web-App „/?app=1“, App-Verknüpfungen (shortcuts.xml) springen direkt hin. */
    private fun startUrl(intent: Intent?): String {
        val path = when (intent?.data?.takeIf { it.scheme == APP_SCHEME }?.lastPathSegment) {
            "dashboard" -> "/dashboard"
            "dateien" -> "/"
            else -> "/?app=1"
        }
        return base.toString() + path
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.data?.scheme == APP_SCHEME) web.loadUrl(startUrl(intent))
    }

    /** Links der Weboberfläche zur App, z. B. pinasapp://verbindung auf der Seite „Konto“. */
    private fun handleAppLink(url: Uri) {
        when (url.host) {
            "verbindung" -> openSetup()
            "open" -> web.loadUrl(startUrl(Intent().setData(url)))
        }
    }

    private fun openSetup() {
        startActivity(Intent(this, SetupActivity::class.java))
    }

    private fun openExternal(url: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, url))
        } catch (e: ActivityNotFoundException) {
            // kein Browser installiert – dann eben nicht
        }
    }

    private fun showError(title: String?, text: String?) {
        if (title == null) {
            errorPanel.isVisible = false
            web.isVisible = true
            return
        }
        errorTitle.text = title
        errorText.text = text
        errorPanel.isVisible = true
        web.isVisible = false
        refresh.isRefreshing = false
    }

    override fun onStart() {
        super.onStart()
        if (!::web.isInitialized) return // noch nicht eingerichtet → SetupActivity
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(network)
        handler.postDelayed(retry, RETRY_MS)
    }

    override fun onStop() {
        handler.removeCallbacks(retry)
        if (::web.isInitialized) runCatching { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(network) }
        CookieManager.getInstance().flush() // Anmeldung bleibt nach dem Schließen erhalten
        super.onStop()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

    override fun onDestroy() {
        if (::web.isInitialized) web.destroy()
        super.onDestroy()
    }

    companion object {
        const val APP_SCHEME = "pinasapp"
        private const val RETRY_MS = 10_000L
    }
}
