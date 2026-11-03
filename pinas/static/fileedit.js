// Bearbeiten wie im Explorer: Kopieren, Ausschneiden und Einfügen (auch mit Strg+C/X/V), „… nach“ mit Zielauswahl,
// Ziehen auf einen Ordner, Löschen der Auswahl und das Fenster „Freigeben“. Die Auswahl selbst steht in files.js.
// Alle Rechte prüft der Server (fileops.py, shares.py), hier wird nur angeboten, was sinnvoll ist.
(() => {
  const zone = document.getElementById("dropzone");
  if (!zone) return;
  const transfer = window.top.PiNASTransfer;
  const KEY = "pinas.ablage";
  const DRAG = "application/x-pinas";
  const root = zone.dataset.root;
  const here = () => zone.dataset.dir;
  const writable = () => zone.dataset.writable === "1";
  const pasteBtn = document.getElementById("paste-btn");

  const api = async (url, body) => {
    const r = await fetch(url, body === undefined ? { cache: "no-store", credentials: "same-origin" } : {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRF-Token": window.CSRF, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const res = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(res.error || (r.status === 503 ? "Die Festplatte ist ausgeschaltet." : `Fehler ${r.status}`));
    return res;
  };

  // ---------- Ablage (bleibt beim Ordnerwechsel erhalten, auch in andere Bereiche) ----------
  function readClip() {
    try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch { return null; }
  }
  function writeClip(clip) {
    try {
      if (clip) sessionStorage.setItem(KEY, JSON.stringify(clip)); else sessionStorage.removeItem(KEY);
    } catch {}
    show();
  }
  function show() {
    const clip = readClip();
    pasteBtn.hidden = !clip || !writable();
    if (clip) pasteBtn.textContent = `📋 ${clip.paths.length === 1 ? `„${clip.names[0]}“` : `${clip.paths.length} Einträge`} einfügen`;
    const cut = clip?.action === "move" && clip.root === root ? new Set(clip.paths) : new Set();
    zone.querySelectorAll(".fv-item").forEach((it) => {
      it.classList.toggle("fv-cut", cut.has(it.dataset.path));
      it.draggable = writable();
    });
  }

  // Worauf sich ein Befehl bezieht: die Auswahl, falls der Eintrag dazugehört, sonst nur er selbst
  function targets(item) {
    const chosen = window.PiNASFiles.selected();
    return !item || chosen.includes(item) ? chosen : [item];
  }
  const describe = (items) => (items.length === 1 ? `„${items[0].dataset.name}“` : `${items.length} Einträge`);

  function toClip(action, items) {
    if (!items.length) return;
    writeClip({ action, root, paths: items.map((it) => it.dataset.path), names: items.map((it) => it.dataset.name) });
  }

  // ---------- Ausführen, bei gleichem Namen wie unter Windows nachfragen ----------
  function ask(title, text, buttons) {
    return new Promise((resolve) => {
      const d = dialog(title);
      const p = document.createElement("p");
      p.textContent = text;
      const row = actions(d);
      for (const [label, value, cls] of buttons) {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = label;
        if (cls) b.className = cls;
        b.addEventListener("click", () => { d.close(); resolve(value); });
        row.append(b);
      }
      d.insertBefore(p, row);
      d.addEventListener("cancel", () => resolve(null), { once: true });
      d.showModal();
    });
  }

  async function run(action, srcRoot, paths, destRoot, destDir, destLabel) {
    const body = { action, src_root: srcRoot, paths, dest_root: destRoot, dest_dir: destDir };
    const verb = action === "copy" ? "Kopieren" : "Verschieben";
    const what = `${paths.length === 1 ? `„${paths[0].split("/").pop()}“` : `${paths.length} Einträge`} nach ${destLabel}`;
    try {
      const { conflicts } = await api("/api/kopieren/pruefen", body);
      if (conflicts.length) {
        const names = conflicts.length === 1 ? `„${conflicts[0]}“ gibt` : `${conflicts.length} dieser Namen gibt`;
        const choice = await ask("Gleicher Name am Ziel", `${names} es dort schon. Beim Ersetzen kommt das Alte in den Papierkorb, Ordner werden zusammengeführt.`, [
          ["Beide behalten", "keep", "primary"], ["Ersetzen", "replace"], ["Überspringen", "skip"], ["Abbrechen", null],
        ]);
        if (!choice) return false;
        body.conflict = choice;
      }
    } catch (err) {
      transfer.notice(verb, what, err.message);
      return false;
    }
    transfer.copy({ body, what });
    return true;
  }

  async function paste() {
    const clip = readClip();
    if (!clip || !writable()) return;
    const started = await run(clip.action, clip.root, clip.paths, root, here(), window.PiNASFiles.place());
    if (started && clip.action === "move") writeClip(null);  // Ausgeschnittenes gibt es nur einmal
    window.PiNASFiles.clearSelection();
  }

  async function removeItems(items) {
    if (!items.length) return;
    const yes = await askConfirm({
      title: "Löschen?", text: `${describe(items)} ${items.length === 1 ? "wird" : "werden"} in den Papierkorb verschoben. Von dort lässt sich alles wiederherstellen.`,
      ok: "In den Papierkorb", danger: true,
    });
    if (!yes) return;
    try {
      const res = await api(`/api/loeschen/${root}`, { paths: items.map((it) => it.dataset.path) });
      if (res.failed.length) transfer.notice("Löschen", describe(items), `Nicht alles hat geklappt: ${res.failed.join(", ")}`);
    } catch (err) {
      transfer.notice("Löschen", describe(items), err.message);
    }
    window.PiNASFiles.clearSelection();
    window.PiNASFiles.refresh();
  }

  // ---------- Fenster ----------
  function dialog(title) {
    const d = document.createElement("dialog");
    d.className = "edit-dialog";
    const h = document.createElement("h3");
    h.textContent = title;
    d.append(h);
    d.addEventListener("close", () => d.remove());
    d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
    document.body.append(d);
    return d;
  }
  function actions(d) {
    const row = document.createElement("div");
    row.className = "edit-actions";
    d.append(row);
    return row;
  }

  // „Kopieren nach“ / „Verschieben nach“: Bereich und Ordner wählen
  async function chooseTarget(action, items) {
    if (!items.length) return;
    const verb = action === "copy" ? "kopieren" : "verschieben";
    const d = dialog(`${describe(items)} ${verb} nach …`);
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Bereich");
    const path = document.createElement("p");
    path.className = "edit-path";
    const list = document.createElement("div");
    list.className = "edit-folders";
    const note = document.createElement("p");
    note.className = "muted";
    d.append(select, path, list, note);
    const row = actions(d);
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "primary";
    ok.textContent = `Hierher ${verb}`;
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Abbrechen";
    cancel.addEventListener("click", () => d.close());
    row.append(ok, cancel);

    let cur = { root, dir: here() }, labels = {};
    const srcPaths = new Set(items.map((it) => it.dataset.path));

    async function go(nextRoot, dir) {
      let res;
      try {
        res = await api(`/api/ordner?root=${encodeURIComponent(nextRoot)}&dir=${encodeURIComponent(dir)}`);
      } catch (err) {
        note.textContent = err.message;
        return;
      }
      cur = { root: res.root, dir: res.dir };
      if (!select.options.length) {
        for (const r of res.roots) {
          labels[r.root] = r.label;
          select.add(new Option(r.writable ? r.label : `${r.label} (nur ansehen)`, r.root));
        }
      }
      select.value = cur.root;
      path.replaceChildren();
      const crumb = (text, target) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "link";
        b.textContent = text;
        b.addEventListener("click", () => go(cur.root, target));
        path.append(b);
      };
      crumb(labels[cur.root], "");
      let acc = "";
      for (const part of cur.dir ? cur.dir.split("/") : []) {
        acc += (acc ? "/" : "") + part;
        path.append(" › ");
        crumb(part, acc);
      }
      list.replaceChildren();
      for (const name of res.folders) {
        const full = (cur.dir ? `${cur.dir}/` : "") + name;
        if (cur.root === root && srcPaths.has(full)) continue;  // nicht in sich selbst
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = `📁 ${name}`;
        b.addEventListener("click", () => go(cur.root, full));
        list.append(b);
      }
      if (!list.children.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "Keine Unterordner.";
        list.append(empty);
      }
      ok.disabled = !res.writable;
      note.textContent = res.writable ? "" : "Hier darfst du nichts ändern, dieser Ordner ist nur zum Ansehen freigegeben.";
    }
    select.addEventListener("change", () => go(select.value, ""));
    ok.addEventListener("click", () => {
      const label = [labels[cur.root], ...(cur.dir ? cur.dir.split("/") : [])].join(" › ");
      const target = { ...cur };
      d.close();
      run(action, root, [...srcPaths], target.root, target.dir, label).then(() => window.PiNASFiles.clearSelection());
    });
    d.showModal();
    await go(root, here());
  }

  // „Freigeben“: pro Person nur ansehen, auch ändern oder kein Zugriff
  async function shareFolder(item) {
    const url = `/api/freigabe?path=${encodeURIComponent(item.dataset.path)}`;
    let info;
    try {
      info = await api(url);
    } catch (err) {
      transfer.notice("Freigeben", `„${item.dataset.name}“`, err.message);
      return;
    }
    const d = dialog(`„${info.name}“ freigeben`);
    const row = document.createElement("div");
    row.className = "edit-actions";
    const selects = {};
    if (!info.users.length) {
      const p = document.createElement("p");
      p.textContent = "Es gibt noch keine anderen Benutzer. Ein Administrator kann sie unter „Benutzer“ anlegen.";
      d.append(p);
    } else {
      const intro = document.createElement("p");
      intro.className = "muted";
      intro.textContent = "Neue Dateien zählen auf dein Speicherlimit, Gelöschtes kommt in deinen Papierkorb.";
      d.append(intro);
      const table = document.createElement("div");
      table.className = "edit-share";
      for (const name of info.users) {
        const label = document.createElement("label");
        const span = document.createElement("span");
        span.textContent = name;
        const sel = document.createElement("select");
        sel.add(new Option("Kein Zugriff", ""));
        sel.add(new Option("Nur ansehen", "read"));
        sel.add(new Option("Auch ändern", "write"));
        sel.value = info.members[name] || "";
        selects[name] = sel;
        label.append(span, sel);
        table.append(label);
      }
      d.append(table);
    }
    const note = document.createElement("p");
    note.className = "muted";
    if (!info.samba) note.textContent = "Dieser Ordnername enthält % oder \\. Deshalb gilt die Freigabe nur in der Weboberfläche, nicht im Netzlaufwerk.";
    else if (info.samba_name) note.textContent = `Im Netzlaufwerk heißt der Ordner „${info.samba_name}“.`;
    d.append(note);
    d.append(row);
    if (info.users.length) {
      const save = document.createElement("button");
      save.type = "button";
      save.className = "primary";
      save.textContent = "Speichern";
      save.addEventListener("click", async () => {
        const members = {};
        for (const [name, sel] of Object.entries(selects)) if (sel.value) members[name] = sel.value;
        save.disabled = true;
        try {
          const res = await api("/api/freigabe", { path: info.path, members });
          d.close();
          if (!res.samba && Object.keys(members).length) {
            transfer.notice("Freigeben", `„${info.name}“`, "In der Weboberfläche gilt die Freigabe, im Netzlaufwerk konnte sie nicht eingerichtet werden.");
          }
          window.PiNASFiles.refresh();
        } catch (err) {
          note.textContent = err.message;
          save.disabled = false;
        }
      });
      row.append(save);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Abbrechen";
    close.addEventListener("click", () => d.close());
    row.append(close);
    d.showModal();
  }

  // ---------- Menü (fileview.js fragt hier nach) ----------
  function itemEntries(item) {
    const items = targets(item);
    const many = items.length > 1 ? ` (${items.length})` : "";
    const entries = [];
    if (item.dataset.edit && items.length === 1) {
      entries.push({ label: writable() ? "Bearbeiten" : "Als Text ansehen", run: () => { location.href = item.dataset.edit; } });
    }
    entries.push({ label: `Kopieren${many}`, run: () => toClip("copy", items) });
    entries.push({ label: `Kopieren nach …${many}`, run: () => chooseTarget("copy", items) });
    if (writable()) {
      entries.push({ label: `Ausschneiden${many}`, run: () => toClip("move", items) });
      entries.push({ label: `Verschieben nach …${many}`, run: () => chooseTarget("move", items) });
    }
    if (item.dataset.dir === "1" && root === "privat" && items.length === 1) {
      entries.push({ label: "Freigeben …", run: () => shareFolder(item) });
    }
    return entries;
  }
  function blankEntries() {
    const clip = readClip();
    return clip && writable() ? ["-", { label: `Einfügen (${clip.paths.length})`, run: paste }] : [];
  }

  // ---------- Knöpfe und Tastatur ----------
  pasteBtn.addEventListener("click", paste);
  document.getElementById("pick-bar").addEventListener("click", (e) => {
    const cmd = e.target.closest("[data-pick-edit]")?.dataset.pickEdit;
    if (!cmd) return;
    const items = window.PiNASFiles.selected();
    if (cmd === "copy") toClip("copy", items);
    else if (cmd === "cut") toClip("move", items);
    else if (cmd === "copy-to") chooseTarget("copy", items);
    else if (cmd === "move-to") chooseTarget("move", items);
    else removeItems(items);
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea, dialog")) return;
    const ctrl = (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey;
    const key = e.key.toLowerCase();
    const items = window.PiNASFiles.selected();
    if (ctrl && key === "c" && items.length && !String(getSelection())) {
      e.preventDefault();
      toClip("copy", items);
    } else if (ctrl && key === "x" && items.length && writable()) {
      e.preventDefault();
      toClip("move", items);
    } else if (ctrl && key === "v" && readClip()) {
      e.preventDefault();
      paste();
    } else if (e.key === "Delete" && items.length && writable()) {
      e.preventDefault();
      removeItems(items);
    } else if (e.key === "F2" && items.length === 1 && writable()) {
      e.preventDefault();
      items[0].querySelector("[data-rename]")?.click();
    }
  });

  // ---------- Ziehen auf einen Ordner: verschiebt, mit Strg kopiert (wie im Explorer) ----------
  let dragged = null;
  const dropTarget = (e) => e.target.closest?.('.fv-item[data-dir="1"]');
  function clearOver() {
    zone.querySelectorAll(".fv-over").forEach((el) => el.classList.remove("fv-over"));
  }
  // Beim Ziehen meldet der Browser den ganzen Eintrag, nicht die Stelle, an der die Maus gedrückt wurde
  let pressedCheck = false;
  zone.addEventListener("mousedown", (e) => { pressedCheck = !!e.target.closest(".fv-check"); });
  zone.addEventListener("dragstart", (e) => {
    const item = e.target.closest?.(".fv-item");
    if (!item || !writable() || pressedCheck) { if (item) e.preventDefault(); return; }
    dragged = targets(item);
    e.dataTransfer.setData(DRAG, "1");
    e.dataTransfer.effectAllowed = "copyMove";
  });
  zone.addEventListener("dragend", () => { dragged = null; clearOver(); });
  zone.addEventListener("dragover", (e) => {
    if (!dragged) return;
    const over = dropTarget(e);
    clearOver();
    if (over && !dragged.includes(over)) {
      over.classList.add("fv-over");
      e.dataTransfer.dropEffect = e.ctrlKey ? "copy" : "move";
    } else {
      e.dataTransfer.dropEffect = "none";
    }
  });
  zone.addEventListener("drop", (e) => {
    if (!dragged) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    const over = dropTarget(e);
    const items = dragged;
    dragged = null;
    clearOver();
    zone.classList.remove("drag");
    if (!over || items.includes(over)) return;
    const label = `${window.PiNASFiles.place()} › ${over.dataset.name}`;
    run(e.ctrlKey ? "copy" : "move", root, items.map((it) => it.dataset.path), root, over.dataset.path, label)
      .then(() => window.PiNASFiles.clearSelection());
  }, true);

  window.PiNASEdit = { itemEntries, blankEntries, refresh: show, removeSelected: () => removeItems(window.PiNASFiles.selected()) };
  show();
})();
