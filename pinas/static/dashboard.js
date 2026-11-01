(() => {
  const fmt = (n) => {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return i === 0 ? `${n} B` : `${n.toFixed(1).replace(".", ",")} ${units[i]}`;
  };
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };

  async function refresh() {
    try {
      const r = await fetch("/api/stats", { credentials: "same-origin" });
      if (!r.ok) return;
      const s = await r.json();
      if (s.temp != null) set("temp", `${s.temp.toFixed(1).replace(".", ",")} °C`);
      if (s.load) set("load", s.load.join(" · "));
      if (s.uptime) { set("uptime", s.uptime.text); set("boot", s.uptime.since); }
      if (s.history) {
        if (s.history.total_text) set("uptime-total", s.history.total_text);
        if (s.history.temp_max != null) {
          set("temp-max", `${s.history.temp_max.toFixed(1).replace(".", ",")} °C`);
          set("temp-max-at", s.history.temp_max_at_text ? `am ${s.history.temp_max_at_text}` : "");
        }
      }
      if (s.io) { set("io-read", fmt(s.io.read)); set("io-written", fmt(s.io.written)); }
      if (s.net) set("net-total", `${fmt(s.net.rx)} empfangen, ${fmt(s.net.tx)} gesendet`);
      set("throttled", s.throttled || "–");
      const pct = s.disk.total ? (s.disk.used / s.disk.total) * 100 : 0;
      set("disk-used", fmt(s.disk.used));
      set("disk-free", fmt(s.disk.free));
      set("disk-total", fmt(s.disk.total));
      set("donut-pct", `${Math.round(pct)} %`);
      set("donut-title", `${Math.round(pct)} % belegt: ${fmt(s.disk.used)} von ${fmt(s.disk.total)}`);
      document.getElementById("donut-used").setAttribute("stroke-dasharray", `${pct.toFixed(2)} 100`);
      document.getElementById("disk-warn").hidden = pct < 90;
      if (s.memory) {
        set("mem", fmt(s.memory.used));
        document.getElementById("mem-bar").style.width = (s.memory.used / s.memory.total * 100) + "%";
      }
    } catch {}
  }
  setInterval(refresh, 5000);
})();
