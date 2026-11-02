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
      transfer.download({ href: dl.href, name: item.dataset.name, dest: place(), size: isDir ? 0 : +item.dataset.size, isDir });
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
