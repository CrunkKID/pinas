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
  }
  window.PiNASFiles = { refresh: refreshListing };

  // Aktueller Ordner für die Anzeige, z. B. „Gemeinsam › Fotos › 2024“
  const place = () => [...document.querySelectorAll(".crumbs a")].map((a) => a.textContent.trim()).join(" › ");

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
      e.preventDefault();
      const item = dl.closest(".fv-item");
      const isDir = item.dataset.dir === "1";
      const dest = item.querySelector(".fv-where")?.dataset.place || place();  // Suchtreffer liegen anderswo
      transfer.download({ href: dl.href, name: item.dataset.name, dest, size: isDir ? 0 : +item.dataset.size, isDir });
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
