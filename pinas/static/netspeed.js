// Live-Netzwerkgeschwindigkeit des Pi. Aus Sicht des Nutzers:
// empfängt der Pi Daten (rx), wird gerade zum NAS hochgeladen; sendet er (tx), wird heruntergeladen.
// Erfasst auch Übertragungen über das Netzlaufwerk und Browser-Downloads.
window.fmtRate = (bps) => {
  if (bps < 1024) return "0 KB/s";
  if (bps < 1024 * 1024) return `${Math.round(bps / 1024)} KB/s`;
  return `${(bps / 1024 / 1024).toFixed(1).replace(".", ",")} MB/s`;
};
// Dieselbe Rate in Megabit pro Sekunde (wie bei Internet- und WLAN-Angaben üblich)
window.fmtMbit = (bps) => {
  const mbit = (bps * 8) / 1e6;
  if (mbit < 0.05) return "0 Mbit/s";
  return `${mbit < 100 ? mbit.toFixed(1).replace(".", ",") : Math.round(mbit)} Mbit/s`;
};
window.NET_BUSY = 25000;  // ab ca. 0,2 Mbit/s gilt eine Richtung als „läuft gerade“

(() => {
  const rxEl = document.getElementById("net-rx");
  const txEl = document.getElementById("net-tx");
  if (!rxEl && !txEl) return;
  const box = (rxEl || txEl).closest(".netspeed");
  const fmt = box && box.dataset.unit === "mbit" ? fmtMbit : fmtRate;
  let last = null;

  function show(el, bps) {
    if (!el) return;
    el.textContent = fmt(bps);
    if (box) el.parentElement.classList.toggle("busy", bps > NET_BUSY);
  }

  async function poll() {
    try {
      const r = await fetch("/api/netz", { cache: "no-store", credentials: "same-origin" });
      const n = await r.json();
      const now = performance.now();
      if (last && n.rx !== undefined) {
        const secs = (now - last.t) / 1000;
        const rx = Math.max(0, n.rx - last.rx) / secs;
        const tx = Math.max(0, n.tx - last.tx) / secs;
        show(rxEl, rx);
        show(txEl, tx);
        document.dispatchEvent(new CustomEvent("pinas:netrate", { detail: { rx, tx } }));
      }
      last = { ...n, t: now };
    } catch {}
  }
  poll();
  setInterval(poll, 2000);
})();
