(() => {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");
  const actionForm = document.getElementById("action-form");
  const urls = document.getElementById("urls").dataset;
  // Bis zu dieser Größe lädt die Seite selbst herunter und zeigt den Fortschritt; größere Dateien
  // übernimmt der Browser direkt (sonst müsste die ganze Datei erst in den Arbeitsspeicher)
  const SELF_DOWNLOAD_MAX = 1024 ** 3;

  // Dateiliste und Speicheranzeige neu laden, ohne die ganze Seite neu aufzubauen
  async function refreshListing() {
    try {
      const r = await fetch(location.href, { cache: "no-store", credentials: "same-origin" });
      if (!r.ok) throw new Error(r.status);
      const doc = new DOMParser().parseFromString(await r.text(), "text/html");
      for (const id of ["listing", "usage"]) {
        const fresh = doc.getElementById(id);
        const old = document.getElementById(id);
        if (fresh && old) old.replaceWith(fresh);
      }
      window.PiNASView?.refresh();  // gewählte Ansicht und Sortierung auf die neue Liste anwenden
    } catch {
      location.reload();
    }
  }

  function formatTime(secs) {
    secs = Math.round(secs);
    if (secs < 60) return `${secs} s`;
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    return h ? `${h} Std ${m} Min` : `${m}:${String(s).padStart(2, "0")} Min`;
  }

  function formatSize(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return i === 0 ? `${bytes} B` : `${bytes.toFixed(1).replace(".", ",")} ${units[i]}`;
  }

  // Aktueller Ordner für die Anzeige, z. B. „Gemeinsam › Fotos › 2024“
  const place = () => [...document.querySelectorAll(".crumbs a")].map((a) => a.textContent.trim()).join(" › ");

  // ---------- Übertragungsfenster wie der Kopierdialog unter Windows ----------
  const stack = document.createElement("div");
  stack.className = "xfer-stack";
  document.body.append(stack);
  const SMALL_KEY = "pinas.uebertragung.klein";
  let small = false;
  try { small = localStorage.getItem(SMALL_KEY) === "1"; } catch {}
  let running = 0;  // laufende Übertragungen: beim Verlassen der Seite nachfragen
  addEventListener("beforeunload", (e) => { if (running) { e.preventDefault(); e.returnValue = ""; } });

  const GRAPH_POINTS = 60;

  // onCancel bricht die Übertragung ab; ohne onCancel (Browser lädt selbst) gibt es nur „Schließen“
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
        <dt data-row>Verbleibende Zeit</dt><dd data-row class="xfer-left">wird berechnet …</dd>
        <dt data-row>Übertragen</dt><dd data-row class="xfer-done">–</dd>
      </dl>
      <footer>
        <button type="button" class="btn-sm xfer-more">${small ? "Mehr Details" : "Weniger Details"}</button>
        <button type="button" class="btn-sm xfer-cancel">${onCancel ? "Abbrechen" : "Schließen"}</button>
      </footer>`;
    const $ = (s) => w.querySelector(s);
    const titleEl = $(".xfer-title"), pctEl = $(".xfer-pct"), barEl = $(".xfer-bar span"), graph = $(".xfer-graph");
    const cancelBtn = $(".xfer-cancel");
    stack.append(w);

    let loaded = 0, total = 0, speed = 0, last = null, raf = 0, state = "run", onClose = null;
    const points = [];  // Geschwindigkeitsverlauf: {x: 0…1, v: Bytes/s}

    function close() {
      if (!w.isConnected) return;
      w.remove();
      onClose?.();
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
        const pct = Math.min(100, Math.floor((loaded / total) * 100));
        titleEl.textContent = `${title} – ${pct} %`;
        if (loaded < total) pctEl.textContent = `${pct} % abgeschlossen`;
        barEl.style.width = pct + "%";
        if (loaded < total) {
          $(".xfer-left").textContent = speed ? "ca. " + formatTime((total - loaded) / speed) : "wird berechnet …";
        }
        $(".xfer-done").textContent = `${formatSize(loaded)} von ${formatSize(total)}`;
      }
      draw();
    }
    const schedule = () => { if (!raf) raf = requestAnimationFrame(render); };

    function addPoint(v, x) {
      if (x !== undefined && !points.length) points.push({ v, x: 0 });  // Kurve am linken Rand beginnen
      points.push({ v, x });
      if (x === undefined) {  // ohne Fortschritt: die letzten Messwerte über die ganze Breite
        if (points.length > GRAPH_POINTS) points.shift();
        points.forEach((p, i) => { p.x = points.length > 1 ? i / (points.length - 1) : 0; });
      }
    }

    function end(cls, text) {
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
        const t = performance.now();
        if (!last) last = { loaded: now, t };
        const secs = (t - last.t) / 1000;
        if (secs >= 0.5) {  // Geschwindigkeit geglättet, damit die Anzeige nicht springt
          const current = (now - last.loaded) / secs;
          speed = speed ? speed * 0.7 + current * 0.3 : current;
          last = { loaded: now, t };
          addPoint(speed, all ? now / all : undefined);
        }
        schedule();
      },
      saving() {
        pctEl.textContent = "Wird auf dem NAS gespeichert …";
        $(".xfer-left").textContent = "gleich fertig";
      },
      // Nur Geschwindigkeit, ohne Prozent (wenn der Browser selbst herunterlädt)
      passive(text) {
        w.classList.add("passive");
        pctEl.textContent = text;
      },
      rate(bps) { speed = bps; addPoint(bps); schedule(); },
      finish(text) {
        end("done", text);
        barEl.style.width = "100%";
        setTimeout(close, 6000);
      },
      fail(text) { end("failed", text); },
      close,
      set onClose(fn) { onClose = fn; },
    };
  }

  // ---------- Hochladen ----------
  function upload(fileList) {
    const files = [...fileList];
    input.value = "";  // dieselbe Datei kann erneut gewählt werden
    if (!files.length) return;
    const dest = place();
    const needed = files.reduce((sum, f) => sum + f.size, 0);
    const title = files.length === 1 ? "Hochladen" : `${files.length} Dateien hochladen`;
    // Persönliches Speicherlimit schon vor dem Senden prüfen (der Server prüft trotzdem)
    const quotaFree = zone.dataset.quotaFree;
    if (quotaFree !== undefined && needed > +quotaFree) {
      const win = transferWindow(title);
      win.what(`nach ${dest}`);
      win.fail(`Nicht genug Speicherplatz: ${formatSize(needed)} gewählt, frei sind noch `
        + `${formatSize(+quotaFree)}. Bitte Dateien löschen oder den Administrator um mehr Platz bitten.`);
      return;
    }
    const data = new FormData();
    data.append("dir", zone.dataset.dir);
    for (const f of files) data.append("files", f, f.name);
    // Wo endet welche Datei? Daraus ergibt sich, welche gerade übertragen wird
    let sum = 0;
    const ends = files.map((f) => (sum += f.size));

    const xhr = new XMLHttpRequest();
    xhr.open("POST", zone.dataset.upload);
    xhr.setRequestHeader("X-CSRF-Token", window.CSRF);
    const win = transferWindow(title, () => xhr.abort());
    const describe = (i) => win.what(files.length === 1 ? `„${files[0].name}“ nach ${dest}`
                                                        : `„${files[i].name}“ (${i + 1} von ${files.length}) nach ${dest}`);
    describe(0);
    running++;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const i = ends.findIndex((end) => e.loaded < end);
      describe(i < 0 ? files.length - 1 : i);
      win.progress(e.loaded, e.total);
      if (e.loaded >= e.total) win.saving();
    };
    xhr.onload = async () => {
      let res = {};
      try { res = JSON.parse(xhr.responseText); } catch {}
      await refreshListing();
      if (res.errors && res.errors.length) {
        win.fail("Fehler beim Hochladen: " + res.errors.join(", "));
      } else if (xhr.status >= 400) {
        win.fail(`Hochladen fehlgeschlagen (Fehler ${xhr.status}).`);
      } else {
        const n = (res.saved || []).length;
        win.finish(`✓ ${n} ${n === 1 ? "Datei" : "Dateien"} hochgeladen (${formatSize(needed)})`);
      }
    };
    xhr.onerror = () => win.fail("Verbindung abgebrochen – bitte erneut versuchen.");
    xhr.onabort = () => win.fail("Abgebrochen – es wurde nichts gespeichert.");
    xhr.onloadend = () => { running--; };
    xhr.send(data);
  }

  // ---------- Herunterladen ----------
  async function download(link, name) {
    const ctrl = new AbortController();
    const win = transferWindow("Herunterladen", () => ctrl.abort());
    win.what(`„${name}“ aus ${place()}`);
    running++;
    try {
      const r = await fetch(link.href, { credentials: "same-origin", cache: "no-store", signal: ctrl.signal });
      if (!r.ok) throw new Error(r.status === 503 ? "Die Festplatte ist ausgeschaltet." : `Fehler ${r.status}`);
      const total = +r.headers.get("Content-Length") || 0;
      const reader = r.body.getReader();
      const chunks = [];
      let loaded = 0;
      win.progress(0, total);
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        loaded += value.length;
        win.progress(loaded, total);
      }
      // Fertig geladene Datei an den Browser übergeben, der sie im Download-Ordner speichert
      const url = URL.createObjectURL(new Blob(chunks, { type: r.headers.get("Content-Type") || "" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      win.finish(`✓ Heruntergeladen (${formatSize(loaded)}) – der Browser speichert die Datei jetzt.`);
    } catch (err) {
      if (err.name === "AbortError") win.fail("Abgebrochen.");
      else if (err instanceof TypeError) win.fail("Verbindung abgebrochen – bitte erneut versuchen.");
      else win.fail(`Herunterladen fehlgeschlagen: ${err.message}`);
    } finally {
      running--;
    }
  }

  // Sehr große Dateien lädt der Browser selbst. Das Fenster zeigt dann, wie schnell das NAS sendet
  // (Messwert von netspeed.js, enthält auch andere Übertragungen wie das Netzlaufwerk).
  function browserDownload(name) {
    const win = transferWindow("Herunterladen");
    win.what(`„${name}“ aus ${place()}`);
    win.passive("Große Datei: Der Browser lädt sie direkt herunter – den Fortschritt siehst du in seiner Download-Liste.");
    const since = performance.now();
    let seen = false, quiet = 0;
    const onRate = (e) => {
      const { tx } = e.detail;
      win.rate(tx);
      if (tx > NET_BUSY) { seen = true; quiet = 0; return; }
      if (performance.now() - since < 6000) return;  // der Browser braucht manchmal einen Moment zum Starten
      if (++quiet >= 3) {
        stop();
        if (seen) win.finish("✓ Das NAS sendet nicht mehr – der Download ist fertig.");
        else win.close();
      }
    };
    const stop = () => document.removeEventListener("pinas:netrate", onRate);
    document.addEventListener("pinas:netrate", onRate);
    win.onClose = stop;
  }

  input.addEventListener("change", () => upload(input.files));
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.remove("drag");
  }));
  zone.addEventListener("drop", (e) => upload(e.dataTransfer.files));

  document.getElementById("mkdir-btn").addEventListener("click", () => {
    const name = prompt("Name des neuen Ordners:");
    if (!name) return;
    const form = document.getElementById("mkdir-form");
    form.elements.name.value = name;
    form.submit();
  });

  function submitAction(url, path, name) {
    actionForm.action = url;
    actionForm.elements.path.value = path;
    actionForm.elements.name.value = name || "";
    actionForm.submit();
  }

  // Ereignis-Delegation: funktioniert auch für Zeilen, die nach einem Upload neu eingefügt wurden
  zone.addEventListener("click", async (e) => {
    const dl = e.target.closest("[data-download]");
    if (dl) {
      if (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;  // z. B. Strg+Klick: neuer Tab
      const item = dl.closest(".fv-item");
      if (+item.dataset.size > SELF_DOWNLOAD_MAX || !window.ReadableStream) {
        browserDownload(item.dataset.name);  // der normale Link lädt weiter herunter
        return;
      }
      e.preventDefault();
      download(dl, item.dataset.name);
      return;
    }
    const btn = e.target.closest("[data-rename], [data-delete]");
    if (!btn) return;
    if (btn.dataset.rename !== undefined) {
      const name = prompt("Neuer Name:", btn.dataset.name);
      if (name && name !== btn.dataset.name) submitAction(urls.renameUrl, btn.dataset.rename, name);
      return;
    }
    const yes = await askConfirm({
      title: "Löschen?",
      text: `„${btn.dataset.name}“ wird in den Papierkorb verschoben. Von dort kannst du es wiederherstellen.`,
      ok: "In den Papierkorb",
      danger: true,
    });
    if (yes) submitAction(urls.deleteUrl, btn.dataset.delete);
  });
})();
