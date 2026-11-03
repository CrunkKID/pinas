(() => {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");
  const actionForm = document.getElementById("action-form");
  const urls = document.getElementById("urls").dataset;
  // Die Übertragung läuft in der obersten Seite, damit sie einen Reiterwechsel übersteht (transfer.js)
  const transfer = window.top.PiNASTransfer;

  // Dateiliste und Speicheranzeige neu laden, ohne die ganze Seite neu aufzubauen
  async function refreshListing() {
    const r = await fetch(location.href, { cache: "no-store", credentials: "same-origin" });
    if (!r.ok) return;
    const doc = new DOMParser().parseFromString(await r.text(), "text/html");
    for (const id of ["listing", "usage"]) {
      const fresh = doc.getElementById(id);
      const old = document.getElementById(id);
      if (fresh && old) old.replaceWith(fresh);
    }
    window.PiNASView?.refresh();  // gewählte Ansicht und Sortierung auf die neue Liste anwenden
    showPicks();
    window.PiNASEdit?.refresh();
  }

  // Aktueller Ordner für die Anzeige, z. B. „Gemeinsam › Fotos › 2024“
  const crumbs = () => [...document.querySelectorAll(".crumbs a")].map((a) => a.textContent.trim());
  const place = () => crumbs().join(" › ");

  // Ordnergrößen nachladen: Die Seite ist längst da, der Pi zählt im Hintergrund und nennt den Stand.
  // Nur für Ansichten, die eine Größe zeigen, und für Ausgewähltes: Sonst bliebe die Platte unnötig beschäftigt.
  const SIZE_VIEWS = new Set(["details", "tiles", "content"]);
  const SIZE_WORKERS = 2;
  const SIZE_POLL = 1500;
  let sizeWorkers = 0;

  function showSize(item, text, title) {
    item.querySelectorAll(".fv-dirsize").forEach((el) => { el.textContent = text; el.title = title; });
  }

  async function countFolder(item) {
    while (item.isConnected) {
      let st;
      try {
        const r = await fetch(item.dataset.sizeUrl, { cache: "no-store", credentials: "same-origin" });
        if (r.redirected || !r.ok) throw new Error();
        st = await r.json();
      } catch {
        showSize(item, "–", "Die Größe ließ sich gerade nicht ermitteln.");
        item.dataset.sized = "1";
        return;
      }
      if (st.done) {
        item.dataset.size = st.size;
        item.dataset.sized = "1";
        if (st.partial) showSize(item, `≥ ${st.text}`, "Nicht alle Unterordner ließen sich lesen, es kann mehr sein.");
        else showSize(item, st.text, `${st.text} samt Unterordnern`);
        window.PiNASView?.sizeChanged();
        showPicks();
        return;
      }
      if (st.size) showSize(item, `${st.text} …`, `Wird noch berechnet, bisher ${st.text}`);
      await new Promise((r) => setTimeout(r, SIZE_POLL));
    }
  }

  function loadSizes() {
    const scope = SIZE_VIEWS.has(zone.dataset.view) ? ".fv-item" : ".fv-item.fv-selected";
    while (sizeWorkers < SIZE_WORKERS) {
      const item = zone.querySelector(`${scope}[data-size-url]:not([data-sized])`);
      if (!item) return;
      item.dataset.sized = "0";
      sizeWorkers++;
      countFolder(item).finally(() => { sizeWorkers--; loadSizes(); });
    }
  }

  // Auswahl: gemerkt wird der Pfad, so übersteht sie das Neuladen der Liste und das Umsortieren
  const picked = new Set();
  const pickBar = document.getElementById("pick-bar");
  let lastPick = null;
  const allItems = () => [...zone.querySelectorAll(".fv-item")];
  const pickedItems = () => allItems().filter((it) => picked.has(it.dataset.path));

  function formatSize(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return i === 0 ? `${bytes} B` : `${bytes.toFixed(1).replace(".", ",")} ${units[i]}`;
  }

  function showPicks() {
    const items = allItems();
    const present = new Set(items.map((it) => it.dataset.path));
    for (const path of picked) if (!present.has(path)) picked.delete(path);  // inzwischen gelöscht oder umbenannt
    let sum = 0, open = false;
    for (const it of items) {
      const on = picked.has(it.dataset.path);
      it.classList.toggle("fv-selected", on);
      it.querySelector("[data-select]").checked = on;
      if (on) {
        if (it.dataset.dir === "1" && it.dataset.sized !== "1") open = true;
        else sum += Math.max(0, +it.dataset.size);
      }
    }
    const head = zone.querySelector("[data-select-all]");
    if (head) {
      head.checked = picked.size > 0 && picked.size === items.length;
      head.indeterminate = picked.size > 0 && picked.size < items.length;
    }
    pickBar.hidden = !picked.size;
    if (picked.size) {
      document.getElementById("pick-count").textContent = `${picked.size} ausgewählt · ${formatSize(sum)}${open ? " …" : ""}`;
    }
    loadSizes();
  }

  function pickAll(on) {
    picked.clear();
    if (on) allItems().forEach((it) => picked.add(it.dataset.path));
    lastPick = null;
    showPicks();
  }

  // Umschalt: alles zwischen dem zuletzt angeklickten und diesem Eintrag, in der angezeigten Reihenfolge
  function pick(item, on, range) {
    const items = allItems();
    const from = items.findIndex((it) => it.dataset.path === lastPick), to = items.indexOf(item);
    const hit = range && from >= 0 ? items.slice(Math.min(from, to), Math.max(from, to) + 1) : [item];
    hit.forEach((it) => (on ? picked.add(it.dataset.path) : picked.delete(it.dataset.path)));
    lastPick = item.dataset.path;
    showPicks();
  }

  zone.addEventListener("mousedown", (e) => {
    if (e.shiftKey && e.target.closest(".fv-item")) e.preventDefault();  // sonst markiert der Browser den Text
  });
  zone.addEventListener("click", (e) => {
    if (e.target.matches("[data-select-all]")) { pickAll(e.target.checked); return; }
    const item = e.target.closest(".fv-item");
    if (!item) return;
    if (e.target.matches("[data-select]")) { pick(item, e.target.checked, e.shiftKey); return; }
    // Strg- und Umschalt-Klick auf den Namen wählen aus, wie im Explorer
    if ((e.ctrlKey || e.metaKey || e.shiftKey) && !e.button && e.target.closest(".fv-main")) {
      e.preventDefault();
      pick(item, e.shiftKey || !picked.has(item.dataset.path), e.shiftKey);
    }
  });
  document.getElementById("pick-none").addEventListener("click", () => pickAll(false));
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea, dialog")) return;
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "a" && allItems().length) {
      e.preventDefault();
      pickAll(true);
    } else if (e.key === "Escape" && picked.size) {
      pickAll(false);
    }
  });

  // Als ZIP nur auf ausdrücklichen Wunsch: im Rechtsklick-Menü oder wenn der Browser keine Ordner anlegen kann
  function zipJob(items) {
    if (items.length === 1) {
      const it = items[0];
      const dest = it.querySelector(".fv-where")?.dataset.place || place();  // Suchtreffer liegen anderswo
      return { href: it.querySelector("[data-download]").href, name: it.dataset.name, dest, size: 0, isDir: true };
    }
    const found = !!items[0].querySelector(".fv-where");
    const dest = found ? crumbs()[0] : place();
    return {
      paths: items.map((it) => it.dataset.path), url: zone.dataset.selection, size: 0, isDir: true, dest,
      name: found ? "Suchtreffer" : `Auswahl aus ${crumbs().pop()}`,
      what: `${items.length} Einträge als ZIP aus ${dest}`,
    };
  }

  // Synchron im Klick, der Dialog für den Zielordner ist nur dort erlaubt
  function downloadSingly(items) {
    const zip = zipJob(items);
    transfer.downloadItems({
      paths: items.map((it) => it.dataset.path), url: zone.dataset.singles, dest: zip.dest,
      hasDirs: items.some((it) => it.dataset.dir === "1"), zip,
    });
  }

  // Eine Auswahl aus nur einer Datei lädt wie deren eigener Knopf; sonst Datei für Datei in einen Zielordner
  document.getElementById("pick-download").addEventListener("click", () => {
    const items = pickedItems();
    if (items.length === 1 && items[0].dataset.dir !== "1") items[0].querySelector("[data-download]").click();
    else downloadSingly(items);
  });

  window.PiNASFiles = { refresh: refreshListing, sizes: loadSizes, selected: pickedItems, clearSelection: () => pickAll(false), place,
                        downloadZip: (item) => transfer.download(zipJob([item])) };
  loadSizes();

  // Suche: Das Formular lädt die Seite ganz normal neu (geht so auch im Rahmen und mit „Zurück“).
  // Hier nur der Hinweis, solange der Pi sucht. „Abbrechen“ zeigt danach, was bis dahin gefunden wurde.
  const searchForm = document.getElementById("search-form");
  const searchBusy = document.getElementById("search-busy");
  const cancelSearch = document.getElementById("search-cancel");
  let searching = false;
  searchForm.addEventListener("submit", (e) => {
    if (!searchForm.elements.q.value.trim()) {
      e.preventDefault();
      location.href = searchForm.action;  // leeres Feld: zurück zum Ordner
      return;
    }
    searching = true;
    searchBusy.hidden = false;
    cancelSearch.disabled = false;
  });
  cancelSearch.addEventListener("click", () => {
    cancelSearch.disabled = true;
    fetch("/api/suche/abbrechen", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": window.CSRF } }).catch(() => {});
  });
  // Woandershin geklickt, während sie noch läuft: Niemand wartet mehr darauf, also die Platte nicht weiter beschäftigen
  addEventListener("pagehide", () => {
    if (!searching) return;
    const form = new FormData();
    form.append("csrf_token", window.CSRF);
    navigator.sendBeacon("/api/suche/abbrechen", form);
  });
  addEventListener("pageshow", (e) => {
    if (e.persisted) { searching = false; searchBusy.hidden = true; }
  });

  function upload(fileList) {
    const files = [...fileList];
    input.value = "";  // dieselbe Datei kann erneut gewählt werden
    transfer.upload({ files, url: zone.dataset.upload, dir: zone.dataset.dir, dest: place(), quotaFree: zone.dataset.quotaFree });
  }

  input.addEventListener("change", () => upload(input.files));
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault();
    if (!e.dataTransfer.types.includes("application/x-pinas")) zone.classList.add("drag");  // nicht beim Ziehen innerhalb der Liste (fileedit.js)
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
      e.preventDefault();
      const item = dl.closest(".fv-item");
      if (item.dataset.dir === "1") { downloadSingly([item]); return; }
      const dest = item.querySelector(".fv-where")?.dataset.place || place();  // Suchtreffer liegen anderswo
      transfer.download({ href: dl.href, name: item.dataset.name, dest, size: +item.dataset.size, isDir: false });
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
