(() => {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");
  const progress = document.getElementById("progress");
  const label = progress.querySelector("small");
  const bar = progress.querySelector("span");
  const actionForm = document.getElementById("action-form");
  const urls = document.getElementById("urls").dataset;
  let hideTimer;

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

  function showStatus(text, done) {
    clearTimeout(hideTimer);
    progress.hidden = false;
    label.textContent = text;
    if (done) hideTimer = setTimeout(() => { progress.hidden = true; bar.style.width = "0"; }, 4000);
  }

  function upload(files) {
    if (!files.length) return;
    const count = files.length;
    const data = new FormData();
    data.append("dir", zone.dataset.dir);
    for (const f of files) data.append("files", f, f.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", zone.dataset.upload);
    xhr.setRequestHeader("X-CSRF-Token", window.CSRF);
    bar.style.width = "0";
    showStatus(`Lade ${count} Datei(en) hoch …`);
    let last = { loaded: 0, t: performance.now() };
    let speed = 0;  // Bytes pro Sekunde, geglättet
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const now = performance.now();
      const secs = (now - last.t) / 1000;
      if (secs >= 0.5) {
        const current = (e.loaded - last.loaded) / secs;
        speed = speed ? speed * 0.7 + current * 0.3 : current;
        last = { loaded: e.loaded, t: now };
      }
      const pct = Math.round((e.loaded / e.total) * 100);
      bar.style.width = pct + "%";
      if (pct >= 100) { showStatus("Wird gespeichert …"); return; }
      let text = `Lade ${count} Datei(en) hoch … ${pct} %`;
      if (speed > 0) text += ` · ${fmtRate(speed)} · noch ${formatTime((e.total - e.loaded) / speed)}`;
      showStatus(text);
    };
    xhr.onload = async () => {
      let res = {};
      try { res = JSON.parse(xhr.responseText); } catch {}
      await refreshListing();
      if (res.errors && res.errors.length) {
        showStatus("Fehler beim Hochladen: " + res.errors.join(", "), true);
      } else if (xhr.status >= 400) {
        showStatus(`Hochladen fehlgeschlagen (${xhr.status})`, true);
      } else {
        const n = (res.saved || []).length;
        showStatus(`✓ ${n} ${n === 1 ? "Datei" : "Dateien"} hochgeladen`, true);
      }
      input.value = "";  // dieselbe Datei kann erneut gewählt werden
    };
    xhr.onerror = () => { showStatus("Verbindung abgebrochen – bitte erneut versuchen.", true); input.value = ""; };
    xhr.send(data);
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
