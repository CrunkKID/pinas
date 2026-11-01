// Live-Netzwerkgeschwindigkeit des Pi. Aus Sicht des Nutzers:
// empfängt der Pi Daten (rx), wird gerade zum NAS hochgeladen; sendet er (tx), wird heruntergeladen.
// Erfasst auch Übertragungen über das Netzlaufwerk und Browser-Downloads.
window.fmtRate = (bps) => {
  if (bps < 1024) return "0 KB/s";
  if (bps < 1024 * 1024) return `${Math.round(bps / 1024)} KB/s`;
  return `${(bps / 1024 / 1024).toFixed(1).replace(".", ",")} MB/s`;
};

(() => {
  const rxEl = document.getElementById("net-rx");
  const txEl = document.getElementById("net-tx");
  if (!rxEl && !txEl) return;
  let last = null;

  async function poll() {
    try {
      const r = await fetch("/api/netz", { cache: "no-store", credentials: "same-origin" });
      const n = await r.json();
      const now = performance.now();
      if (last && n.rx !== undefined) {
        const secs = (now - last.t) / 1000;
        if (rxEl) rxEl.textContent = fmtRate(Math.max(0, n.rx - last.rx) / secs);
        if (txEl) txEl.textContent = fmtRate(Math.max(0, n.tx - last.tx) / secs);
      }
      last = { ...n, t: now };
    } catch {}
  }
  poll();
  setInterval(poll, 2000);
})();
