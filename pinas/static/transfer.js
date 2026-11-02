// Hoch- und Herunterladen mit Fenster wie beim Kopieren unter Windows – auf allen Reitern sichtbar.
//
// Ein normaler Seitenwechsel würde laufende Übertragungen abbrechen. Deshalb gehört die Übertragung immer
// zur obersten Seite: Solange etwas läuft, öffnen Links und Formulare dort in einem Rahmen, der über die
// Seite gelegt wird (<base target>), und die oberste Seite bleibt bestehen. Seiten im Rahmen geben
// Übertragungen an sie weiter (window.top.PiNASTransfer). Ist alles fertig, geht der nächste Klick
// wieder ganz normal in die oberste Seite.
(() => {
  const FRAME = "pinas-seite";
  const inFrame = window.top !== window;

  function setBase(target) {
    let base = document.getElementById("xfer-base");
    if (!target) { base?.remove(); return; }
    if (!base) {
      base = document.createElement("base");
      base.id = "xfer-base";
      document.head.append(base);
    }
    base.target = target;
  }

  // Abmelden, Ein/Aus-Knöpfe, Festplatte und Update sperren; gilt für die oberste Seite und die im Rahmen
  window.PiNASSync = ({ power, logout, text, local }) => {
    const lock = (btn, locked) => {
      if (btn.disabled && btn.dataset.xferTitle === undefined) return;  // aus anderem Grund gesperrt, z. B. Prüfung nicht bestanden
      if (locked && btn.dataset.xferTitle === undefined) btn.dataset.xferTitle = btn.title;
      btn.disabled = locked;
      btn.classList.toggle("xfer-locked", locked);
      if (locked) btn.title = text;
      else if (btn.dataset.xferTitle !== undefined) { btn.title = btn.dataset.xferTitle; delete btn.dataset.xferTitle; }
    };
    document.querySelectorAll(".header-power button, .power-bar button, form[data-lock-idle] button, fieldset[data-lock-idle]").forEach((b) => lock(b, power));
    document.querySelectorAll("[data-lock-logout]").forEach((b) => lock(b, logout));
    setBase(inFrame ? (local ? null : "_top") : (local ? FRAME : null));
  };

  if (inFrame) {
    try { window.top.PiNASTransfer?.sync(); } catch {}
    return;
  }

  // Gerundet, damit die Anzeige ruhig bleibt: unter 10 mit einer Nachkommastelle, ab 100 in Fünferschritten
  const roundShort = (n) => (n < 10 ? n.toFixed(1).replace(".", ",") : n < 100 ? Math.round(n) : Math.round(n / 5) * 5);
  const fmtRate = (bps) => (bps < 1024 * 1024 ? `${Math.round(bps / 1024)} KB/s` : `${roundShort(bps / 1024 / 1024)} MB/s`);
  const fmtMbit = (bps) => `${roundShort((bps * 8) / 1e6)} Mbit/s`;

  // Restzeit in groben Schritten, wie im Windows-Kopierdialog
  function formatTime(secs) {
    if (secs < 10) return "wenige Sekunden";
    if (secs < 60) return `ca. ${Math.round(secs / 5) * 5} s`;
    if (secs < 600) {
      const halves = Math.round(secs / 30);
      return `ca. ${Math.floor(halves / 2)}:${halves % 2 ? "30" : "00"} Min`;
    }
    if (secs < 3600) return `ca. ${Math.round(secs / 60)} Min`;
    const mins = Math.round(secs / 300) * 5;
    return `ca. ${Math.floor(mins / 60)} Std ${mins % 60} Min`;
  }

  function formatSize(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return i === 0 ? `${bytes} B` : `${bytes.toFixed(1).replace(".", ",")} ${units[i]}`;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- Rahmen und Sperren ----------
  let frame = null;
  let running = 0;  // laufende Übertragungen dieser Seite
  let uploads = 0;  // Hochladen bricht beim Verlassen ab, Herunterladen läuft im Browser weiter
  let server = { busy: false, mine: false, text: "" };
  addEventListener("beforeunload", (e) => { if (uploads) { e.preventDefault(); e.returnValue = ""; } });

  function ensureFrame() {
    if (frame) return;
    frame = document.createElement("iframe");
    frame.name = FRAME;
    frame.className = "xfer-frame";
    frame.hidden = true;
    frame.title = "Seite";
    frame.addEventListener("load", () => {
      const win = frame.contentWindow;
      if (win.location.href === "about:blank") return;
      frame.hidden = false;
      history.replaceState(null, "", win.location.href);
      document.title = win.document.title;
    });
    document.body.append(frame);
  }

  function sync() {
    const local = running > 0;
    if (local) ensureFrame();
    const state = {
      power: local || server.busy,
      logout: local || server.mine,
      text: server.text || "Gerade läuft eine Übertragung. Das geht erst, wenn alles fertig ist.",
      local,
    };
    window.PiNASSync(state);
    try { frame?.contentWindow?.PiNASSync?.(state); } catch {}
  }

  async function poll() {
    if (document.hidden) return;
    try {
      const r = await fetch("/api/uebertragung", { cache: "no-store", credentials: "same-origin" });
      if (r.ok) server = await r.json();
    } catch {}
    sync();
  }
  setInterval(poll, 5000);
  poll();

  function changeRunning(by, isUpload) {
    running += by;
    if (isUpload) uploads += by;
    sync();
    poll();
  }

  // ---------- Fenster ----------
  const SPEED_WINDOW = 6000;  // Geschwindigkeit: Mittelwert über so viele Millisekunden
  const GRAPH_POINTS = 300;
  const stack = document.createElement("div");
  stack.className = "xfer-stack";
  document.body.append(stack);
  const SMALL_KEY = "pinas.uebertragung.klein";
  let small = false;
  try { small = localStorage.getItem(SMALL_KEY) === "1"; } catch {}

  // Verschieben an der Titelleiste (Maus und Finger). pos ist die gewünschte Stelle; wird das Fenster
  // zu groß dafür, rückt es nur so weit wie nötig und kommt wieder zurück, sobald es kleiner wird.
  const POS_KEY = "pinas.uebertragung.position";
  const EDGE = 8;
  let pos = { right: 16, bottom: 16 };
  try {
    const saved = JSON.parse(localStorage.getItem(POS_KEY) || "{}");
    if (Number.isFinite(saved.right) && Number.isFinite(saved.bottom)) pos = saved;
  } catch {}

  function placeStack() {
    const r = stack.getBoundingClientRect();
    const fit = (want, room) => Math.max(EDGE, Math.min(want, room - EDGE));
    const room = document.documentElement;  // ohne Bildlaufleiste
    const placed = { right: fit(pos.right, room.clientWidth - r.width), bottom: fit(pos.bottom, room.clientHeight - r.height) };
    stack.style.right = placed.right + "px";
    stack.style.bottom = placed.bottom + "px";
    return placed;
  }
  placeStack();
  addEventListener("resize", placeStack);
  new ResizeObserver(placeStack).observe(stack);

  stack.addEventListener("pointerdown", (e) => {
    if (e.button || !e.target.closest(".xfer header") || e.target.closest("button")) return;
    e.preventDefault();
    const start = { x: e.clientX, y: e.clientY, ...placeStack() };
    stack.setPointerCapture(e.pointerId);  // auch über dem Rahmen der Seite weiter mitbekommen
    const move = (ev) => {
      pos = { right: start.right - (ev.clientX - start.x), bottom: start.bottom - (ev.clientY - start.y) };
      placeStack();
    };
    const up = () => {
      stack.removeEventListener("pointermove", move);
      stack.removeEventListener("pointerup", up);
      stack.removeEventListener("pointercancel", up);
      pos = placeStack();
      try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch {}
    };
    stack.addEventListener("pointermove", move);
    stack.addEventListener("pointerup", up);
    stack.addEventListener("pointercancel", up);
  });

  // onCancel bricht die Übertragung ab
  function transferWindow(title, onCancel) {
    const w = document.createElement("section");
    w.className = "xfer" + (small ? " small" : "");
    w.setAttribute("role", "status");
    w.innerHTML = `
      <header><b class="xfer-title"></b><button type="button" class="xfer-x" title="Schließen" aria-label="Schließen">✕</button></header>
      <p class="xfer-what"></p>
      <p class="xfer-pct">Wird gestartet …</p>
      <div class="xfer-bar"><span></span></div>
      <canvas class="xfer-graph" aria-hidden="true"></canvas>
      <dl>
        <dt>Geschwindigkeit</dt><dd class="xfer-speed">–</dd>
        <dt>Verbleibende Zeit</dt><dd class="xfer-left">wird berechnet …</dd>
        <dt>Übertragen</dt><dd class="xfer-done">–</dd>
      </dl>
      <footer>
        <button type="button" class="btn-sm xfer-more">${small ? "Mehr Details" : "Weniger Details"}</button>
        <button type="button" class="btn-sm xfer-cancel">${onCancel ? "Abbrechen" : "Schließen"}</button>
      </footer>`;
    const $ = (s) => w.querySelector(s);
    const titleEl = $(".xfer-title"), pctEl = $(".xfer-pct"), barEl = $(".xfer-bar span"), graph = $(".xfer-graph");
    const cancelBtn = $(".xfer-cancel");
    stack.append(w);
    placeStack();

    let loaded = 0, total = 0, speed = 0, eta = null, raf = 0, state = "run", saving = false, timer = 0;
    let samples = [];  // Messwerte der letzten Sekunden: {t, loaded}
    let points = [];  // Geschwindigkeitsverlauf: {x: 0…1, v: Bytes/s}

    const close = () => w.remove();

    // Einmal pro Sekunde messen, über die letzten Sekunden gemittelt. Einzelne Meldungen kommen in Schüben
    // (beim Hochladen wartet der Browser nach jedem Teilstück auf die Bestätigung) und sagen wenig aus.
    function measure() {
      if (saving) return;
      const t = performance.now();
      if (samples.length && loaded < samples[samples.length - 1].loaded) samples = [];  // Teilstück wird wiederholt
      samples.push({ t, loaded });
      while (samples.length > 2 && t - samples[0].t > SPEED_WINDOW) samples.shift();
      const first = samples[0], secs = (t - first.t) / 1000;
      if (secs < 1) return;
      speed = Math.max(0, loaded - first.loaded) / secs;
      // Die Restzeit zählt einfach herunter und wird nur neu gesetzt, wenn sie deutlich danebenliegt
      const raw = speed > 0 ? (total - loaded) / speed : null;
      const counted = eta === null ? null : Math.max(0, eta - 1);
      eta = raw === null || counted === null || Math.abs(raw - counted) > Math.max(5, counted * 0.15) ? raw : counted;
      if (!points.length) points.push({ v: speed, x: 0 });  // Kurve am linken Rand beginnen
      points.push({ v: speed, x: total ? loaded / total : 0 });
      if (points.length > GRAPH_POINTS) points = points.filter((_, i) => i % 2 === 0);
      schedule();
    }

    // Verlaufskurve wie im Windows-Kopierdialog: x = Fortschritt, y = Geschwindigkeit
    function draw() {
      const cw = graph.clientWidth, ch = graph.clientHeight;
      if (!cw || !points.length) return;
      const dpr = devicePixelRatio || 1;
      graph.width = cw * dpr;
      graph.height = ch * dpr;
      const c = graph.getContext("2d");
      c.scale(dpr, dpr);
      const max = Math.max(...points.map((p) => p.v), 1);
      const xy = points.map((p) => [p.x * cw, ch - (p.v / max) * (ch - 6)]);
      const color = getComputedStyle(w).getPropertyValue("--accent").trim() || "#c51a4a";
      c.beginPath();
      c.moveTo(xy[0][0], ch);
      xy.forEach(([x, y]) => c.lineTo(x, y));
      c.lineTo(xy[xy.length - 1][0], ch);
      c.closePath();
      c.globalAlpha = 0.22;
      c.fillStyle = color;
      c.fill();
      c.globalAlpha = 1;
      c.beginPath();
      xy.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
      c.strokeStyle = color;
      c.lineWidth = 1.5;
      c.stroke();
    }

    function render() {
      raf = 0;
      $(".xfer-speed").textContent = speed ? `${fmtMbit(speed)} (${fmtRate(speed)})` : "–";
      if (state === "run" && total) {
        // 100 % erst, wenn wirklich alles abgeschlossen ist (finish)
        const pct = Math.min(99, Math.floor((loaded / total) * 100));
        titleEl.textContent = `${title} – ${pct} %`;
        barEl.style.width = pct + "%";
        if (saving) {
          pctEl.textContent = "Wird gespeichert …";
          $(".xfer-left").textContent = "gleich fertig";
        } else {
          pctEl.textContent = `${pct} % abgeschlossen`;
          $(".xfer-left").textContent = eta === null ? "wird berechnet …" : formatTime(eta);
        }
        $(".xfer-done").textContent = `${formatSize(loaded)} von ${formatSize(total)}`;
      }
      draw();
    }
    const schedule = () => { if (!raf) raf = requestAnimationFrame(render); };

    function end(cls, text) {
      clearInterval(timer);
      state = cls;
      w.classList.add(cls);
      titleEl.textContent = title;
      pctEl.textContent = text;
      $(".xfer-left").textContent = "–";
      cancelBtn.textContent = "Schließen";
      schedule();
    }

    $(".xfer-more").addEventListener("click", (e) => {
      small = w.classList.toggle("small");
      e.target.textContent = small ? "Mehr Details" : "Weniger Details";
      try { localStorage.setItem(SMALL_KEY, small ? "1" : "0"); } catch {}
      schedule();
    });
    cancelBtn.addEventListener("click", () => {
      if (state === "run" && onCancel) onCancel(); else close();
    });
    $(".xfer-x").addEventListener("click", async () => {
      if (state !== "run" || !onCancel) return close();
      const yes = await askConfirm({ title: "Übertragung abbrechen?", text: `${$(".xfer-what").textContent} – wirklich abbrechen?`,
                                     ok: "Abbrechen", danger: true });
      if (yes && state === "run") onCancel();
    });

    titleEl.textContent = title;
    return {
      what(text) { const el = $(".xfer-what"); el.textContent = text; el.title = text; },
      progress(now, all) {
        loaded = now;
        total = all;
        saving = false;  // bei mehreren Dateien geht es mit der nächsten weiter
        if (!timer && state === "run") {
          measure();
          timer = setInterval(measure, 1000);
        }
        schedule();
      },
      saving() { saving = true; schedule(); },
      get speed() { return speed; },
      finish(text) {
        end("done", text);
        barEl.style.width = "100%";
        setTimeout(close, 2000);
      },
      fail(text) { end("failed", text); },
    };
  }

  // Dateiliste der gerade sichtbaren Seite neu laden
  function refreshFiles() {
    const page = frame && !frame.hidden ? frame.contentWindow : window;
    return Promise.resolve(page.PiNASFiles?.refresh()).catch(() => {});
  }

  async function post(url, fields) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": window.CSRF },
      body: fields ? new URLSearchParams(fields) : undefined,
    });
    const res = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(res.error || (r.status === 503 ? "Die Festplatte ist ausgeschaltet." : `Fehler ${r.status}`));
    return res;
  }

  // ---------- Hochladen in Teilstücken (Gegenstück in files.py) ----------
  const CHUNK = 16 * 1024 * 1024;
  const RETRIES = 3;
  const ABORTED = new Error("abgebrochen");

  function upload({ files, url, dir, dest, quotaFree }) {
    // Eigene Kopien der Datei-Objekte: Die Seite, die sie ausgewählt hat, kann inzwischen weg sein
    files = files.map((f) => new File([f], f.name, { type: f.type, lastModified: f.lastModified }));
    if (!files.length) return;
    const needed = files.reduce((sum, f) => sum + f.size, 0);
    const title = files.length === 1 ? "Hochladen" : `${files.length} Dateien hochladen`;
    // Persönliches Speicherlimit schon vor dem Senden prüfen (der Server prüft trotzdem)
    if (quotaFree !== undefined && needed > +quotaFree) {
      const win = transferWindow(title);
      win.what(`nach ${dest}`);
      win.fail(`Nicht genug Speicherplatz: ${formatSize(needed)} gewählt, frei sind noch `
        + `${formatSize(+quotaFree)}. Bitte Dateien löschen oder den Administrator um mehr Platz bitten.`);
      return;
    }

    let xhr = null, aborted = false;
    const win = transferWindow(title, () => { aborted = true; xhr?.abort(); });

    const sendPart = (id, part, offset, onProgress) => new Promise((resolve, reject) => {
      xhr = new XMLHttpRequest();
      xhr.open("POST", `${url}/${id}`);
      xhr.setRequestHeader("X-CSRF-Token", window.CSRF);
      xhr.setRequestHeader("X-Offset", offset);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        let res = {};
        try { res = JSON.parse(xhr.responseText); } catch {}
        if (xhr.status === 200 && res.received === offset + part.size) resolve();
        else reject(Object.assign(new Error(res.error || (xhr.status === 503 ? "Die Festplatte ist ausgeschaltet."
          : `Fehler ${xhr.status}`)), { final: xhr.status < 500 || xhr.status === 503 }));
      };
      xhr.onerror = () => reject(new Error("Verbindung abgebrochen – bitte erneut versuchen."));
      xhr.onabort = () => reject(ABORTED);
      xhr.send(part);
    });

    async function sendFile(file, before) {
      const { id } = await post(url, { dir, name: file.name, size: file.size });
      if (!id) throw new Error("Bitte die Seite neu laden und erneut anmelden.");
      try {
        for (let offset = 0; offset < file.size; offset += CHUNK) {
          const part = file.slice(offset, offset + CHUNK);
          for (let attempt = 1; ; attempt++) {
            try {
              await sendPart(id, part, offset, (n) => win.progress(before + offset + n, needed));
              break;
            } catch (err) {  // kurzer Wackler im Netz: dasselbe Stück noch einmal
              if (err === ABORTED || err.final || attempt > RETRIES) throw err;
              await sleep(1000 * attempt);
              if (aborted) throw ABORTED;
            }
          }
        }
        if (aborted) throw ABORTED;
        win.progress(before + file.size, needed);
        win.saving();
        return (await post(`${url}/${id}/fertig`)).saved;
      } catch (err) {
        post(`${url}/${id}/abbrechen`).catch(() => {});
        throw err;
      }
    }

    (async () => {
      changeRunning(1, true);
      const saved = [], errors = [];
      let before = 0;
      for (const [i, file] of files.entries()) {
        if (aborted) break;
        win.what(files.length === 1 ? `„${file.name}“ nach ${dest}` : `„${file.name}“ (${i + 1} von ${files.length}) nach ${dest}`);
        try {
          saved.push(await sendFile(file, before));
        } catch (err) {
          if (err !== ABORTED) errors.push(files.length === 1 ? err.message : `${file.name}: ${err.message}`);
        }
        before += file.size;
      }
      await refreshFiles();
      changeRunning(-1, true);
      if (aborted) {
        win.fail(saved.length ? `Abgebrochen – ${saved.length} fertige Datei(en) bleiben gespeichert.`
                              : "Abgebrochen – es wurde nichts gespeichert.");
      } else if (errors.length) {
        win.fail("Fehler beim Hochladen: " + errors.join(", "));
      } else {
        win.finish(`✓ ${saved.length} ${saved.length === 1 ? "Datei" : "Dateien"} hochgeladen (${formatSize(needed)})`);
      }
    })();
  }

  // ---------- Herunterladen ----------
  // Der Browser lädt selbst (ohne Umweg über den Arbeitsspeicher, läuft auch bei Seitenwechseln weiter).
  // Wie weit er ist, meldet der Server: Er zählt mit, was er schon abgeschickt hat.
  function download({ href, name, dest, size }) {
    const id = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
    const a = document.createElement("a");
    a.href = `${href}${href.includes("?") ? "&" : "?"}t=${id}`;
    a.download = name;
    a.hidden = true;
    document.body.append(a);
    a.click();
    a.remove();

    let cancelled = false;
    const win = transferWindow("Herunterladen", () => {
      cancelled = true;
      post(`/api/uebertragung/${id}/abbrechen`).catch(() => {});
    });
    win.what(`„${name}“ aus ${dest}`);

    (async () => {
      changeRunning(1, false);
      const started = performance.now();
      let seen = false, text = null, ok = false;
      while (text === null) {
        if (cancelled) { text = "Abgebrochen."; break; }
        let st = null;
        try {
          const r = await fetch(`/api/uebertragung/${id}`, { cache: "no-store", credentials: "same-origin" });
          if (r.ok) st = await r.json();
        } catch {}
        if (st?.known) {
          seen = true;
          win.progress(st.done, st.size || size);
          if (st.finished) {
            ok = st.done >= st.size;
            if (ok) {
              // Der letzte Rest ist noch unterwegs zum Browser (höchstens ein paar MB)
              win.saving();
              await sleep(Math.min(3000, (8e6 / Math.max(win.speed, 1e5)) * 1000));
              text = `✓ Heruntergeladen (${formatSize(st.size)})`;
            } else {
              text = "Herunterladen abgebrochen – die Verbindung wurde getrennt.";
            }
          }
        } else if (performance.now() - started > 15000) {
          text = seen ? "Die Verbindung zum NAS ist abgebrochen." : "Der Download ist nicht gestartet – bitte erneut versuchen.";
        }
        if (text === null) await sleep(500);
      }
      changeRunning(-1, false);
      if (ok) win.finish(text); else win.fail(text);
    })();
  }

  window.PiNASTransfer = { upload, download, sync };
})();
