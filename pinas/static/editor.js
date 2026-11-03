// Textdateien direkt im Browser ändern. Speichern mit dem Knopf oder Strg+S.
// Der Server speichert nur, wenn die Datei seit dem Öffnen unverändert ist (Gegenstück in fileops.py).
(() => {
  const box = document.getElementById("editor");
  const text = document.getElementById("editor-text");
  const btn = document.getElementById("editor-save");
  const state = document.getElementById("editor-state");
  if (!box || !text || !btn) return;
  let saved = text.value, version = box.dataset.version, busy = false;

  const changed = () => text.value !== saved;
  function show() {
    btn.disabled = busy || !changed();
    if (!busy && changed()) state.textContent = "Nicht gespeichert";
  }
  text.addEventListener("input", show);

  async function send(force) {
    busy = true;
    show();
    state.textContent = "Wird gespeichert …";
    const content = text.value;
    try {
      const r = await fetch(box.dataset.url, {
        method: "POST", credentials: "same-origin",
        headers: { "X-CSRF-Token": window.CSRF, "X-Version": version, "X-Force": force ? "1" : "0", "Content-Type": "text/plain; charset=utf-8" },
        body: content,
      });
      const res = await r.json().catch(() => ({}));
      if (r.status === 409) {
        busy = false;
        const yes = await askConfirm({ title: "Datei wurde geändert", text: `${res.error} Soll deine Fassung sie überschreiben?`, ok: "Überschreiben", danger: true });
        if (yes) return send(true);
        state.textContent = "Nicht gespeichert";
        show();
        return;
      }
      if (!r.ok) throw new Error(res.error || `Fehler ${r.status}`);
      saved = content;
      version = String(res.version);
      state.textContent = "Gespeichert";
    } catch (err) {
      state.textContent = `Nicht gespeichert: ${err.message}`;
    }
    busy = false;
    show();
  }

  btn.addEventListener("click", () => send(false));
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (!busy && changed()) send(false);
    }
  });
  addEventListener("beforeunload", (e) => { if (changed()) { e.preventDefault(); e.returnValue = ""; } });
})();
