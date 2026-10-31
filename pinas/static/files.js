(() => {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");
  const progress = document.getElementById("progress");
  const actionForm = document.getElementById("action-form");
  const urls = document.getElementById("urls").dataset;

  function upload(files) {
    if (!files.length) return;
    const data = new FormData();
    data.append("dir", zone.dataset.dir);
    for (const f of files) data.append("files", f, f.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", zone.dataset.upload);
    xhr.setRequestHeader("X-CSRF-Token", window.CSRF);
    progress.hidden = false;
    const label = progress.querySelector("small");
    const bar = progress.querySelector("span");
    label.textContent = `Lade ${files.length} Datei(en) hoch …`;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      bar.style.width = pct + "%";
      label.textContent = `Lade ${files.length} Datei(en) hoch … ${pct} %`;
    };
    xhr.onload = () => {
      let res = {};
      try { res = JSON.parse(xhr.responseText); } catch {}
      if (res.errors && res.errors.length) alert("Fehler beim Hochladen:\n" + res.errors.join("\n"));
      else if (xhr.status >= 400) alert("Hochladen fehlgeschlagen (" + xhr.status + ")");
      location.reload();
    };
    xhr.onerror = () => { alert("Verbindung abgebrochen."); location.reload(); };
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

  document.querySelectorAll("[data-rename]").forEach((btn) => btn.addEventListener("click", () => {
    const name = prompt("Neuer Name:", btn.dataset.name);
    if (name && name !== btn.dataset.name) submitAction(urls.renameUrl, btn.dataset.rename, name);
  }));
  document.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", () => {
    if (confirm(`„${btn.dataset.name}“ endgültig löschen?`)) submitAction(urls.deleteUrl, btn.dataset.delete);
  }));
})();
