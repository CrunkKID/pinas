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
  const fmtRate = (bps) => {
    const mb = bps / 1024 / 1024;
    if (mb < 1) return `${Math.round(bps / 1024)} KB/s`;
    return `${mb < 100 ? mb.toFixed(1).replace(".", ",") : Math.round(mb)} MB/s`;
  };
  const fmtMbit = (bps) => `${roundShort((bps * 8) / 1e6)} Mbit/s`;

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const twoUnits = (n, big, bigs, rest, one, many) => plural(n, big, bigs) + (rest ? ` und ${plural(rest, one, many)}` : "");

  // Restzeit wie im Windows-Kopierdialog: in groben Stufen, je länger, desto gröber. Erst runden, dann
  // aufteilen, sonst stünde dort „1 Stunde und 60 Minuten“.
  function formatTime(secs) {
    const step = secs < 60 ? 5 : secs < 120 ? 10 : secs < 3600 ? 60 : secs < 86400 ? 300 : 3600;
    const s = Math.max(5, Math.round(secs / step) * step);
    if (s < 60) return `Etwa ${s} Sekunden`;
    if (s < 120) return `Etwa ${twoUnits(1, "Minute", "Minuten", s - 60, "Sekunde", "Sekunden")}`;
    if (s < 3600) return `Etwa ${plural(Math.round(s / 60), "Minute", "Minuten")}`;
    if (s < 86400) return `Etwa ${twoUnits(Math.floor(s / 3600), "Stunde", "Stunden", Math.round((s % 3600) / 60), "Minute", "Minuten")}`;
    return `Etwa ${twoUnits(Math.floor(s / 86400), "Tag", "Tage", Math.round((s % 86400) / 3600), "Stunde", "Stunden")}`;
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
  let pageBound = 0;  // brechen beim Verlassen der Seite ab; nur was der Browser selbst herunterlädt, läuft weiter
  let server = { busy: false, mine: false, text: "" };
  addEventListener("beforeunload", (e) => { if (pageBound) { e.preventDefault(); e.returnValue = ""; } });

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

  function changeRunning(by, bound) {
    running += by;
    if (bound) pageBound += by;
    sync();
    poll();
  }

  // ---------- Fenster ----------
  const SPEED_WINDOW = 6000;  // Geschwindigkeit: Mittelwert über so viele Millisekunden
  const ETA_AFTER = 3;  // Restzeit erst nach so vielen Sekunden Messung, vorher wäre sie geraten
  const MISSING_SHOWN = 500;
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
      <p class="xfer-note" hidden></p>
      <div class="xfer-choice" hidden></div>
      <p class="xfer-pct">Wird gestartet …</p>
      <div class="xfer-bar"><span></span></div>
      <canvas class="xfer-graph" aria-hidden="true"></canvas>
      <dl>
        <dt>Verbleibende Zeit:</dt><dd class="xfer-left">Wird berechnet …</dd>
        <dt class="xfer-items-label">Verbleibend:</dt><dd class="xfer-done">–</dd>
        <dt>Geschwindigkeit:</dt><dd class="xfer-speed">–</dd>
      </dl>
      <details class="xfer-missing" hidden><summary></summary><ul></ul></details>
      <footer>
        <button type="button" class="btn-sm xfer-more">${small ? "Mehr Details" : "Weniger Details"}</button>
        <button type="button" class="btn-sm xfer-cancel">${onCancel ? "Abbrechen" : "Schließen"}</button>
      </footer>`;
    const $ = (s) => w.querySelector(s);
    const titleEl = $(".xfer-title"), pctEl = $(".xfer-pct"), barEl = $(".xfer-bar span"), graph = $(".xfer-graph");
    const cancelBtn = $(".xfer-cancel");
    stack.append(w);
    placeStack();

    let loaded = 0, total = 0, items = null, speed = 0, eta = null, raf = 0, state = "run", saving = false, timer = 0;
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
      // Die Restzeit zählt einfach herunter und wird nur neu gesetzt, wenn sie deutlich danebenliegt.
      // Ohne bekannte Gesamtgröße oder ohne Daten gibt es keine: lieber keine Zeit als eine erfundene.
      const raw = total && speed > 0 && secs >= ETA_AFTER ? Math.max(0, total - loaded) / speed : null;
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
      $(".xfer-speed").textContent = speed ? `${fmtRate(speed)} (${fmtMbit(speed)})` : "–";
      if (state === "run" && total) {
        // 100 % erst, wenn wirklich alles abgeschlossen ist (finish)
        const pct = Math.min(99, Math.floor((loaded / total) * 100));
        titleEl.textContent = `${title} – ${pct} %`;
        barEl.style.width = pct + "%";
        if (saving) {
          pctEl.textContent = "Wird gespeichert …";
          $(".xfer-left").textContent = "Gleich fertig";
        } else {
          pctEl.textContent = `${pct} % abgeschlossen`;
          $(".xfer-left").textContent = eta === null ? "Wird berechnet …" : formatTime(eta);
        }
        const rest = formatSize(Math.max(0, total - loaded));
        $(".xfer-items-label").textContent = items === null ? "Verbleibend:" : "Verbleibende Elemente:";
        $(".xfer-done").textContent = items === null ? rest : `${items} (${rest})`;
      } else if (state === "run" && loaded) {
        pctEl.textContent = "Wird übertragen …";
        $(".xfer-left").textContent = "Unbekannt";  // Daten kommen, aber wie viele noch, sagt niemand
        $(".xfer-items-label").textContent = "Übertragen:";
        $(".xfer-done").textContent = formatSize(loaded);
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
      $(".xfer-done").textContent = "–";
      $(".xfer-choice").hidden = true;
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
      // Hinweis mit „Nicht mehr anzeigen“; key merkt sich die Wahl
      note(text, key) {
        try { if (localStorage.getItem(key)) return; } catch {}
        const el = $(".xfer-note");
        el.textContent = text + " ";
        const off = document.createElement("button");
        off.type = "button";
        off.className = "link";
        off.textContent = "Nicht mehr anzeigen";
        off.addEventListener("click", () => {
          el.hidden = true;
          try { localStorage.setItem(key, "1"); } catch {}
        });
        el.append(off);
        el.hidden = false;
      },
      // count: wie viele Dateien noch offen sind, die aktuelle mitgezählt. Ohne Angabe nur die Größe
      progress(now, all, count = null) {
        loaded = now;
        total = all;
        items = count;
        saving = false;  // bei mehreren Dateien geht es mit der nächsten weiter
        if (!timer && state === "run") {
          measure();
          timer = setInterval(measure, 1000);
        }
        schedule();
      },
      // Frage im Fenster selbst, „Abbrechen“ bleibt daneben erreichbar. Liefert den Schlüssel des gewählten Knopfs
      choose(text, answers) {
        const box = $(".xfer-choice");
        box.textContent = text;
        box.hidden = false;
        const row = document.createElement("div");
        box.append(row);
        return new Promise((resolve) => {
          for (const [key, label] of Object.entries(answers)) {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "btn-sm";
            b.textContent = label;
            b.addEventListener("click", () => { box.hidden = true; resolve(key); });
            row.append(b);
          }
        });
      },
      // Was am Ende fehlt: Bis 5 Einträge gleich offen, längere Listen zum Aufklappen
      missing(rows) {
        const box = $(".xfer-missing");
        box.querySelector("summary").textContent = rows.length === 1 ? "1 Datei fehlt" : `${rows.length} Dateien fehlen`;
        const list = box.querySelector("ul");
        for (const row of rows.slice(0, MISSING_SHOWN)) {
          const li = document.createElement("li");
          li.textContent = row;
          list.append(li);
        }
        if (rows.length > MISSING_SHOWN) {
          const li = document.createElement("li");
          li.textContent = `… und ${rows.length - MISSING_SHOWN} weitere`;
          list.append(li);
        }
        box.open = rows.length <= 5;
        box.hidden = false;
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

    async function sendFile(file, before, left) {
      const { id } = await post(url, { dir, name: file.name, size: file.size });
      if (!id) throw new Error("Bitte die Seite neu laden und erneut anmelden.");
      try {
        for (let offset = 0; offset < file.size; offset += CHUNK) {
          const part = file.slice(offset, offset + CHUNK);
          for (let attempt = 1; ; attempt++) {
            try {
              await sendPart(id, part, offset, (n) => win.progress(before + offset + n, needed, left));
              break;
            } catch (err) {  // kurzer Wackler im Netz: dasselbe Stück noch einmal
              if (err === ABORTED || err.final || attempt > RETRIES) throw err;
              await sleep(1000 * attempt);
              if (aborted) throw ABORTED;
            }
          }
        }
        if (aborted) throw ABORTED;
        win.progress(before + file.size, needed, left);
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
          saved.push(await sendFile(file, before, files.length - i));
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
  const STALL_START = 300000;  // bis zur ersten Antwort: Platte aufwachen lassen, großen Ordner zusammensuchen
  const STALL_DATA = 60000;  // danach: so lange ohne neue Daten gilt die Verbindung als abgebrochen
  const WRITE_BLOCK = 1024 * 1024;  // in größeren Blöcken schreiben: Viele kleine bremsen bei voller Netzgeschwindigkeit
  const newId = () => [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  const withId = (href, id) => `${href}${href.includes("?") ? "&" : "?"}t=${id}`;
  const whatText = ({ what, name, dest, isDir }) => what || (isDir ? `Ordner „${name}“ als ZIP aus ${dest}` : `„${name}“ aus ${dest}`);

  // key: Das muss in der Antwort stehen. Sonst kam die Anmeldeseite zurück, die Sitzung ist also abgelaufen.
  async function postJson(url, body, key, signal) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin", signal,
      headers: { "X-CSRF-Token": window.CSRF, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const res = await r.json().catch(() => ({}));
    if (r.ok && res[key]) return res;
    throw new Error(res.error || (r.status === 503 ? "Die Festplatte ist ausgeschaltet."
      : r.ok ? "Du bist nicht mehr angemeldet. Bitte die Seite neu laden und anmelden." : `Fehler ${r.status}`));
  }

  // Mehrere Einträge als ZIP: Der Server merkt sich die Auswahl und nennt die Adresse des ZIPs
  const selectionHref = async ({ url, paths, name }) => (await postJson(url, { paths, name }, "href")).href;

  // Fehler dieser Art betreffen alles Weitere, nicht nur die eine Datei
  const fatal = (message) => Object.assign(new Error(message), { fatal: true });

  async function openDownload(href, signal) {
    const r = await fetch(href, { cache: "no-store", credentials: "same-origin", signal });
    if (r.redirected) throw fatal("Du bist nicht mehr angemeldet. Bitte die Seite neu laden und anmelden.");
    if (r.status === 503) throw fatal("Die Festplatte ist ausgeschaltet.");
    if (r.status === 404) throw new Error("Gibt es auf dem NAS nicht mehr.");
    if (!r.ok) throw new Error(`Fehler ${r.status}`);
    return r;
  }

  // Schreibt direkt in die Datei, Stück für Stück. Der Browser wartet dabei auf die Festplatte, im Arbeitsspeicher
  // sammelt sich nichts. Liefert die Zahl der Bytes; bricht ab, wenn weniger ankam als angekündigt.
  async function pour(r, writable, onBytes) {
    const exact = +r.headers.get("Content-Length");  // ein ZIP entsteht beim Senden und nennt nur die Summe der Inhalte
    let got = 0, filled = 0;
    const buffer = new Uint8Array(WRITE_BLOCK);
    const reader = r.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (filled + value.length > WRITE_BLOCK) {
        await writable.write(buffer.subarray(0, filled));
        filled = 0;
      }
      if (value.length > WRITE_BLOCK) await writable.write(value);
      else { buffer.set(value, filled); filled += value.length; }
      got += value.length;
      onBytes(got);
    }
    if (exact && got !== exact) throw fatal("Die Verbindung zum NAS ist abgebrochen.");
    if (filled) await writable.write(buffer.subarray(0, filled));
    return got;
  }

  // Erst der Speicherort, dann die Übertragung. Der Browser erlaubt den Dialog nur direkt im Klick,
  // deshalb vor dem ersten await. Abbrechen im Dialog: Es passiert nichts, der Server erfährt gar nichts davon.
  // Ohne diese Möglichkeit (z. B. Brave, Firefox, Handy) lädt der Browser wie bisher selbst.
  function download(job) {
    if (!window.showSaveFilePicker) return downloadByBrowser(job);
    let picked;
    try {
      picked = window.showSaveFilePicker({ id: "pinas-download", startIn: "downloads", suggestedName: job.isDir ? `${job.name}.zip` : job.name });
    } catch {
      return downloadByBrowser(job);
    }
    picked.then((handle) => downloadToFile(job, handle), (err) => {
      if (err.name !== "AbortError") downloadByBrowser(job);
    });
  }

  // Abbruch aufräumen. Der Browser schreibt in eine Zwischendatei (.crswap), die abort() verwirft;
  // der Dialog hat die Zieldatei aber schon leer angelegt. Hat sie Inhalt, war sie vorher da und bleibt.
  async function discard(handle, writable) {
    try { await writable?.abort(); } catch {}
    try {
      if ((await handle.getFile()).size > 0) return "Die vorhandene Datei am Speicherort bleibt unverändert.";
      await handle.remove();
      return "Die angefangene Datei wurde entfernt.";
    } catch (err) {
      if (err.name === "NotFoundError") return "Die angefangene Datei wurde entfernt.";
      return `Am Speicherort kann eine leere Datei „${handle.name}“ liegen, die du löschen kannst.`;
    }
  }

  // Gehört zu dieser Seite: Der Rahmen hält sie beim Reiterwechsel offen.
  async function downloadToFile(job, handle) {
    const id = newId();
    const stop = new AbortController();
    let cancelled = false, stalled = false, writable = null;
    const win = transferWindow("Herunterladen", () => {
      cancelled = true;
      post(`/api/uebertragung/${id}/abbrechen`).catch(() => {});
      stop.abort();
    });
    win.what(whatText(job));
    changeRunning(1, true);
    let limit = STALL_START, last = performance.now();
    const watchdog = setInterval(() => {
      if (performance.now() - last > limit) { stalled = true; stop.abort(); }
    }, 5000);
    try {
      const href = job.href || await selectionHref(job);
      const r = await openDownload(withId(href, id), stop.signal);
      const total = +r.headers.get("Content-Length") || +r.headers.get("X-PiNAS-Size") || job.size;
      writable = await handle.createWritable();
      limit = STALL_DATA;
      last = performance.now();
      win.progress(0, total, 1);  // auch ein ZIP aus vielen Dateien ist hier eine Datei
      const got = await pour(r, writable, (n) => {
        last = performance.now();
        win.progress(total ? Math.min(n, total) : n, total, 1);
      });
      clearInterval(watchdog);
      win.saving();
      await writable.close();  // erst jetzt wird aus der Zwischendatei die fertige Datei
      changeRunning(-1, true);
      win.finish(`✓ Gespeichert als „${handle.name}“ (${formatSize(got)})`);
    } catch (err) {
      clearInterval(watchdog);
      const left = await discard(handle, writable);
      changeRunning(-1, true);
      if (cancelled) win.fail(`Abgebrochen. ${left}`);
      else if (stalled) win.fail(`Die Verbindung zum NAS ist abgebrochen. ${left}`);
      else if (err instanceof TypeError) win.fail(`Die Verbindung zum NAS ist abgebrochen. ${left}`);
      else if (err.name === "QuotaExceededError") win.fail(`Am Speicherort ist nicht genug Platz. ${left}`);
      else if (err instanceof DOMException) win.fail(`Die Datei konnte nicht gespeichert werden. ${left}`);
      else win.fail(`${err.message} ${left}`);
    }
  }

  function clickDownload(href, name) {
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    a.hidden = true;
    document.body.append(a);
    a.click();
    a.remove();
  }

  // Der Browser lädt selbst (ohne Umweg über den Arbeitsspeicher, läuft auch bei Seitenwechseln weiter).
  // Wie weit er ist, meldet der Server: Er zählt mit, was er schon abgeschickt hat.
  // Fragt der Browser nach dem Speicherort, lädt er währenddessen schon. Das lässt sich von hier nicht verhindern.
  function downloadByBrowser(job) {
    if (!job.href) {
      selectionHref(job).then((href) => downloadByBrowser({ ...job, href }), (err) => {
        const win = transferWindow("Herunterladen");
        win.what(whatText(job));
        win.fail(err instanceof TypeError ? "Die Verbindung zum NAS ist abgebrochen." : err.message);
      });
      return;
    }
    const { href, name, size, isDir } = job;
    const id = newId();
    clickDownload(withId(href, id), isDir ? `${name}.zip` : name);

    let cancelled = false;
    const win = transferWindow("Herunterladen", () => {
      cancelled = true;
      post(`/api/uebertragung/${id}/abbrechen`).catch(() => {});
    });
    win.what(whatText(job));

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
          win.progress(st.done, st.size || size, 1);
          if (st.finished) {
            ok = st.done >= st.size;
            if (ok) {
              // Der letzte Rest ist noch unterwegs zum Browser (höchstens ein paar MB)
              win.saving();
              await sleep(Math.min(3000, (Math.min(8e6, st.size) / Math.max(win.speed, 2e6)) * 1000));
              text = `✓ Übertragen (${formatSize(st.size)}) – gespeichert wird sie von deinem Browser`;
            } else {
              text = "Herunterladen abgebrochen – im Browser abgebrochen oder die Verbindung wurde getrennt.";
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

  // ---------- Mehrere Dateien und Ordner einzeln, wie auf der Festplatte ----------
  const START_WAIT = 60000;  // fragt der Browser, ob die Seite mehrere Dateien laden darf, kann die Antwort dauern
  const SAVING_FROM = 64 * 1024 * 1024;  // „Wird gespeichert …“ nur, wo man es auch sehen kann
  const fileCount = (n) => plural(n, "Datei", "Dateien");
  const dirOf = (arc) => arc.slice(0, Math.max(0, arc.lastIndexOf("/")));
  const nameOf = (arc) => arc.slice(arc.lastIndexOf("/") + 1);
  const fileHref = (list, file) => `${list.base}${file.path.split("/").map(encodeURIComponent).join("/")}?dl=1&sammel=${list.ticket}`;
  const fileList = ({ url, paths }, signal) => postJson(url, { paths }, "ticket", signal);

  // Windows unterscheidet nicht nach Groß/Klein: „Bild.jpg“ und „bild.JPG“ aus einem Ordner dürfen sich nicht überschreiben
  function freeNames(list) {
    const used = new Set();
    for (const f of list) {
      const dot = f.arc.lastIndexOf(".");
      const stem = dot > f.arc.lastIndexOf("/") + 1 ? f.arc.slice(0, dot) : f.arc;
      const ext = f.arc.slice(stem.length);
      for (let i = 2; used.has(f.arc.toLowerCase()); i++) f.arc = `${stem} (${i})${ext}`;
      used.add(f.arc.toLowerCase());
    }
  }

  // Ordner im Ziel öffnen oder anlegen; einmal gefundene merken, das spart bei vielen Dateien viele Zugriffe
  function folderOpener(root) {
    const known = new Map([["", root]]);
    const open = async (path, create) => {
      if (known.has(path)) return known.get(path);
      const parent = await open(dirOf(path), create);
      const dir = await parent.getDirectoryHandle(nameOf(path), { create });
      known.set(path, dir);
      return dir;
    };
    return open;
  }

  async function findExisting(open, list) {
    const hits = [];
    for (const f of list) {
      try {
        await (await open(dirOf(f.arc), false)).getFileHandle(nameOf(f.arc));
        hits.push(f);
      } catch {}  // gibt es nicht; ein Name, den der PC nicht erlaubt, fällt beim Schreiben auf
    }
    return hits;
  }

  // Erst der Zielordner, dann die Übertragung. Der Browser erlaubt den Dialog nur direkt im Klick, deshalb vor dem
  // ersten await. Abbrechen im Dialog: Es passiert nichts, der Server erfährt gar nichts davon.
  function downloadItems(job) {
    if (!window.showDirectoryPicker) { itemsWithoutPicker(job); return; }
    let picked;
    try {
      picked = window.showDirectoryPicker({ id: "pinas-ordner", mode: "readwrite", startIn: "downloads" });
    } catch {
      itemsWithoutPicker(job);
      return;
    }
    picked.then((root) => itemsToFolder(job, root), (err) => {
      if (err.name !== "AbortError") itemsWithoutPicker(job);
    });
  }

  async function itemsToFolder(job, root) {
    const stop = new AbortController();
    let cancelled = false, stalled = false, id = null;
    const win = transferWindow("Herunterladen", () => {
      cancelled = true;
      if (id) post(`/api/uebertragung/${id}/abbrechen`).catch(() => {});
      stop.abort();
    });
    win.what(`Dateiliste wird geholt … (nach „${root.name}“)`);
    changeRunning(1, true);
    let limit = STALL_START, last = performance.now();
    const watchdog = setInterval(() => {
      if (performance.now() - last > limit) { stalled = true; stop.abort(); }
    }, 5000);
    const missing = [];
    let saved = 0, skipped = 0, written = 0, at = 0, problem = null, left = "";
    try {
      const list = await fileList(job, stop.signal);
      limit = Infinity;  // bis zur ersten Datei wartet nur der PC oder die Antwort im Fenster, nicht das Netz
      list.skipped.forEach((arc) => missing.push(`${arc}: ließ sich auf dem NAS nicht lesen`));
      freeNames(list.files);
      const open = folderOpener(root);
      win.what(`Zielordner „${root.name}“ wird geprüft …`);
      const hits = await findExisting(open, list.files);
      let queue = list.files;
      if (hits.length) {
        const answer = await Promise.race([
          win.choose(`${fileCount(hits.length)} gibt es dort schon, z. B. „${nameOf(hits[0].arc)}“.`, { replace: "Ersetzen", keep: "Überspringen" }),
          new Promise((resolve) => stop.signal.addEventListener("abort", () => resolve(null))),
        ]);
        if (answer === "keep") {
          const known = new Set(hits);
          queue = queue.filter((f) => !known.has(f));
          skipped = hits.length;
        }
      }
      for (const dir of list.dirs) {
        if (cancelled) break;
        try { await open(dir, true); } catch { missing.push(`${dir}/: Ordner ließ sich nicht anlegen`); }
      }
      const total = queue.reduce((sum, f) => sum + f.size, 0);
      for (const [i, f] of queue.entries()) {
        if (stop.signal.aborted) break;
        at = i;
        win.what(`„${nameOf(f.arc)}“ (${i + 1} von ${queue.length}) aus ${job.dest}`);
        win.progress(written, total, queue.length - i);
        id = newId();
        limit = STALL_START;
        last = performance.now();
        let r = null, handle = null, writable = null;
        try {
          r = await openDownload(withId(fileHref(list, f), id), stop.signal);
          limit = STALL_DATA;
          last = performance.now();
          try {
            handle = await (await open(dirOf(f.arc), true)).getFileHandle(nameOf(f.arc), { create: true });
          } catch (err) {
            if (err instanceof TypeError) throw new Error("Dieser Name ist auf dem PC nicht erlaubt.");
            throw err;
          }
          writable = await handle.createWritable();
          await pour(r, writable, (n) => {
            last = performance.now();
            win.progress(Math.min(written + n, total), total, queue.length - i);
          });
          if (f.size >= SAVING_FROM) win.saving();
          await writable.close();  // erst jetzt wird aus der Zwischendatei die fertige Datei
          saved++;
        } catch (err) {
          r?.body?.cancel().catch(() => {});
          if (handle) left = await discard(handle, writable);
          if (stop.signal.aborted) break;
          problem = err.fatal ? err.message
            : err.name === "QuotaExceededError" ? "Am Speicherort ist nicht genug Platz."
            : err.name === "NotAllowedError" ? "Der Browser erlaubt den Zugriff auf den Zielordner nicht mehr."
            : err instanceof TypeError ? "Die Verbindung zum NAS ist abgebrochen." : null;
          if (problem) {
            queue.slice(i).forEach((rest) => missing.push(rest.arc));
            break;
          }
          missing.push(`${f.arc}: ${err instanceof DOMException ? "ließ sich nicht speichern" : err.message}`);
        }
        written += f.size;
      }
      if (stalled) {  // keine Daten mehr: Die offene Datei und alle danach fehlen
        problem = "Die Verbindung zum NAS ist abgebrochen.";
        queue.slice(at).forEach((rest) => missing.push(rest.arc));
      }
    } catch (err) {
      if (stalled) problem = "Die Verbindung zum NAS ist abgebrochen.";
      else if (!cancelled) problem = err instanceof TypeError ? "Die Verbindung zum NAS ist abgebrochen." : err.message;
    }
    clearInterval(watchdog);
    changeRunning(-1, true);
    const there = `${fileCount(saved)} in „${root.name}“`;
    const also = skipped ? `, ${skipped} übersprungen` : "";
    if (cancelled) {
      win.fail(`Abgebrochen. ${saved ? `Schon gespeichert: ${there}.` : "Es wurde keine Datei gespeichert."} ${left}`);
    } else if (problem && !saved && !missing.length) {
      win.fail(problem);  // schon die Dateiliste kam nicht an
    } else if (problem || missing.length) {
      win.fail(`${problem ? problem + " " : ""}${there} gespeichert${also}. Nicht alles ist da:`);
      if (missing.length) win.missing(missing);
    } else {
      win.finish(saved || skipped ? `✓ ${there} gespeichert${also} (${formatSize(written)})` : `✓ Ordner in „${root.name}“ angelegt, darin sind keine Dateien`);
    }
  }

  // Ohne Dialog kann eine Seite keine Ordner anlegen: Die Struktur bleibt dann nur in einem ZIP erhalten
  async function itemsWithoutPicker(job) {
    if (!job.hasDirs) { itemsByBrowser(job); return; }
    const yes = await askConfirm({
      title: "Ordner als ZIP laden?",
      text: `Dein Browser erlaubt PiNAS nicht, Ordner auf dem PC anzulegen. Mit der Ordnerstruktur geht es hier deshalb nur als eine ZIP-Datei.`,
      ok: "Als ZIP laden",
    });
    if (yes) downloadByBrowser(job.zip);
  }

  // Der Browser lädt jede Datei selbst in seinen Download-Ordner. Wie weit er ist, meldet wie sonst der Server
  function itemsByBrowser(job) {
    let cancelled = false, id = null;
    const win = transferWindow("Herunterladen", () => {
      cancelled = true;
      if (id) post(`/api/uebertragung/${id}/abbrechen`).catch(() => {});
    });
    win.what(`Dateiliste wird geholt … (aus ${job.dest})`);
    if (matchMedia("(hover: hover)").matches) {
      win.note(`Die Dateien landen einzeln im Download-Ordner deines Browsers. Beim ersten Mal kann er fragen, ob PiNAS mehrere Dateien laden darf; dann bitte erlauben.`,
        "pinas.download.einzeln.hinweis");
    }
    (async () => {
      changeRunning(1, false);
      const missing = [];
      let sent = 0, stopped = null, written = 0;
      try {
        const list = await fileList(job);
        list.skipped.forEach((arc) => missing.push(`${arc}: ließ sich auf dem NAS nicht lesen`));
        const total = list.files.reduce((sum, f) => sum + f.size, 0);
        for (const [i, f] of list.files.entries()) {
          if (cancelled || stopped) break;
          win.what(`„${nameOf(f.arc)}“ (${i + 1} von ${list.files.length}) aus ${job.dest}`);
          win.progress(written, total, list.files.length - i);
          id = newId();
          clickDownload(withId(fileHref(list, f), id), nameOf(f.arc));
          const started = performance.now();
          let seen = false, ok = null;
          while (ok === null && !cancelled) {
            let st = null;
            try {
              const r = await fetch(`/api/uebertragung/${id}`, { cache: "no-store", credentials: "same-origin" });
              if (r.ok) st = await r.json();
            } catch {}
            if (st?.known) {
              seen = true;
              win.progress(Math.min(written + st.done, total), total, list.files.length - i);
              if (st.finished) ok = st.done >= st.size;
            } else if (performance.now() - started > (seen ? 15000 : START_WAIT)) {
              stopped = seen ? "Die Verbindung zum NAS ist abgebrochen."
                : "Dein Browser hat den nächsten Download nicht gestartet. Bitte erlaube dieser Seite, mehrere Dateien zu laden, und versuche es noch einmal.";
              break;
            }
            if (ok === null && !stopped) await sleep(300);
          }
          if (cancelled) break;
          if (ok) sent++;
          if (stopped) list.files.slice(i).forEach((rest) => missing.push(rest.arc));
          else if (!ok) missing.push(`${f.arc}: im Browser abgebrochen oder Verbindung getrennt`);
          written += f.size;
        }
      } catch (err) {
        stopped = err instanceof TypeError ? "Die Verbindung zum NAS ist abgebrochen." : err.message;
      }
      changeRunning(-1, false);
      if (cancelled) {
        win.fail(`Abgebrochen. Schon übertragen: ${fileCount(sent)}.`);
      } else if (stopped && !sent && !missing.length) {
        win.fail(stopped);  // schon die Dateiliste kam nicht an
      } else if (stopped || missing.length) {
        win.fail(`${stopped ? stopped + " " : ""}${fileCount(sent)} übertragen. Nicht alles ist da:`);
        if (missing.length) win.missing(missing);
      } else {
        win.finish(`✓ ${fileCount(sent)} übertragen (${formatSize(written)}) – gespeichert hat sie dein Browser`);
      }
    })();
  }

  // ---------- Kopieren und Verschieben auf dem NAS ----------
  // Der Pi arbeitet allein weiter (fileops.py), hier wird nur der Fortschritt abgefragt. Ein Verlassen der Seite
  // bricht also nichts ab, deshalb zählt es nicht als „seitengebunden“.
  function copy({ body, what }) {
    const verb = body.action === "copy" ? "Kopieren" : "Verschieben";
    const id = newId();
    const win = transferWindow(verb, () => {
      post(`/api/uebertragung/${id}/abbrechen`).catch(() => {});
    });
    win.what(what);
    (async () => {
      changeRunning(1, false);
      let failure = null, result = null;
      try {
        const r = await fetch("/api/kopieren", {
          method: "POST", credentials: "same-origin",
          headers: { "X-CSRF-Token": window.CSRF, "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, t: id }),
        });
        const res = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(res.error || `Fehler ${r.status}`);
        let misses = 0;
        while (!result) {
          let st = null;
          try {
            const s = await fetch(`/api/uebertragung/${res.id}`, { cache: "no-store", credentials: "same-origin" });
            if (s.ok) st = await s.json();
          } catch {}
          if (st?.known) {
            misses = 0;
            if (st.size) win.progress(st.done, st.size);
            if (st.finished) result = st.result || { ok: false, error: "Unbekannter Fehler." };
          } else if (++misses > 30) {
            throw new Error("Keine Antwort vom NAS. Der Vorgang kann dort noch laufen, bitte die Liste prüfen.");
          }
          if (!result) await sleep(500);
        }
      } catch (err) {
        failure = err.message;
      }
      await refreshFiles();
      changeRunning(-1, false);
      if (failure || result.error) return win.fail(failure || result.error);
      const count = (n) => `${n} ${n === 1 ? "Datei" : "Dateien"}`;
      if (result.cancelled) return win.fail(`Abgebrochen – ${count(result.copied)} waren schon fertig und bleiben am Ziel.`);
      if (result.failed.length) return win.fail(`Nicht alles hat geklappt: ${result.failed.join(", ")}`);
      const done = body.action === "copy" ? `✓ ${count(result.copied)} kopiert` : "✓ Verschoben";
      win.finish(result.skipped ? `${done}, ${result.skipped} übersprungen` : done);
    })();
  }

  // Fehler, der vor dem Start auffällt, im selben Fenster anzeigen
  function notice(title, what, text) {
    const win = transferWindow(title);
    win.what(what);
    win.fail(text);
  }

  window.PiNASTransfer = { upload, download, downloadItems, copy, notice, sync };
})();
