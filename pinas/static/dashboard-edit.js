// Dashboard anpassen: Kacheln verschieben (Pfeile oder Ziehen), Breite ändern, ein-/ausblenden, hinzufügen.
// Die Reihenfolge steckt in CSS "order", die Breite in data-span, gespeichert wird pro Benutzer auf dem NAS.
// Festgepinnt lässt sich nichts ändern, bis es wieder gelöst ist. dashboard.js ändert nur Texte, das stört hier nichts.
(() => {
  const editBtn = document.getElementById("dash-edit");
  const resetBtn = document.getElementById("dash-reset");
  const pinBtn = document.getElementById("dash-pin");
  const addBtn = document.getElementById("dash-add-btn");
  const addDialog = document.getElementById("dash-add");
  const addList = document.getElementById("dash-add-list");
  const hint = document.querySelector(".dash-hint");
  const grid = document.querySelector(".dash-grid");
  if (!editBtn) return;
  const tiles = () => [...document.querySelectorAll("[data-tile]")];
  // Geschwister im selben Bereich (Kacheln oben bzw. Dienste/Laufwerke unten), sortiert nach Position
  const group = (el) => [...el.parentElement.querySelectorAll(":scope > [data-tile]")]
    .sort((a, b) => a.style.order - b.style.order);
  let editing = false;
  let dragged = null;

  async function save(body) {
    try {
      const r = await fetch("/api/dashboard-layout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": window.CSRF },
        body: JSON.stringify(body),
      });
      if (r.status === 409) location.reload();  // auf einem anderen Gerät festgepinnt
      return r.ok;
    } catch {
      return false;
    }
  }

  function persist() {
    const all = tiles().sort((a, b) => a.style.order - b.style.order);
    const sizes = {};
    all.forEach((t) => { if (t.dataset.span) sizes[t.dataset.tile] = +t.dataset.span; });
    save({ order: all.map((t) => t.dataset.tile), hidden: all.filter((t) => t.hasAttribute("data-off")).map((t) => t.dataset.tile), sizes });
  }

  // Aktuelle Breite in Spalten und wie viele Spalten das Fenster gerade hat
  const spanOf = (tile) => parseInt(getComputedStyle(tile).gridColumnStart.replace("span", ""), 10) || 1;
  const columns = () => getComputedStyle(grid).gridTemplateColumns.split(" ").length;

  function refreshSizeButtons() {
    tiles().forEach((t) => {
      const less = t.querySelector(":scope > .tile-ctl .tc-less");
      const more = t.querySelector(":scope > .tile-ctl .tc-more");
      if (!less) return;
      less.disabled = spanOf(t) <= 1;
      more.disabled = spanOf(t) >= columns();
    });
  }

  function resize(tile, by) {
    tile.dataset.span = Math.max(1, Math.min(columns(), spanOf(tile) + by));
    refreshSizeButtons();
    persist();
  }
  addEventListener("resize", () => { if (editing) refreshSizeButtons(); });

  // Positionen eines Bereichs neu durchnummerieren; die Zahlen bleiben über alle Bereiche eindeutig
  function renumber(list) {
    const slots = list.map((t) => +t.style.order).sort((a, b) => a - b);
    list.forEach((t, i) => { t.style.order = slots[i]; });
  }

  function move(tile, step) {
    const list = group(tile);
    const i = list.indexOf(tile);
    const j = i + step;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    renumber(list);
    persist();
    tile.querySelector(step < 0 ? ".tc-left" : ".tc-right")?.focus();
  }

  function setOff(tile, off) {
    tile.toggleAttribute("data-off", off);
    const box = tile.querySelector(":scope > .tile-ctl input");
    if (box) box.checked = !off;
    persist();
  }

  // Auswahl „Kachel hinzufügen“: alles, was gerade ausgeblendet ist
  function fillAddList() {
    const off = tiles().filter((t) => t.hasAttribute("data-off"));
    document.getElementById("dash-add-empty").hidden = off.length > 0;
    addList.replaceChildren(...off.map((t) => {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = t.dataset.title;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-sm";
      btn.textContent = "Hinzufügen";
      btn.addEventListener("click", () => {
        setOff(t, false);
        fillAddList();
        refreshSizeButtons();
        addDialog.close();
        t.scrollIntoView({ block: "center" });
      });
      li.append(name, btn);
      return li;
    }));
  }
  addBtn.addEventListener("click", () => { fillAddList(); addDialog.showModal(); });
  document.getElementById("dash-add-close").addEventListener("click", () => addDialog.close());
  addDialog.addEventListener("click", (e) => { if (e.target === addDialog) addDialog.close(); });

  // Alles festpinnen: Anpassen ist gesperrt, bis es wieder gelöst ist (gilt auf dem Server, also auf allen Geräten)
  pinBtn.addEventListener("click", async () => {
    const on = pinBtn.getAttribute("aria-pressed") !== "true";
    if (!await save({ pinned: on })) return;
    pinBtn.setAttribute("aria-pressed", String(on));
    pinBtn.textContent = on ? "📌 Festgepinnt" : "📌 Festpinnen";
    editBtn.disabled = on;
    editBtn.title = on ? "Zum Anpassen zuerst das Festpinnen lösen" : "";
  });

  function addControls(tile) {
    if (tile.querySelector(":scope > .tile-ctl")) return;
    const ctl = document.createElement("div");
    ctl.className = "tile-ctl";
    ctl.innerHTML = `
      <label class="switch" title="Kachel anzeigen"><input type="checkbox"><span></span></label>
      <span class="tc-name"></span>
      <button type="button" class="btn-sm tc-left" title="Nach vorne">◀</button>
      <button type="button" class="btn-sm tc-right" title="Nach hinten">▶</button>`;
    ctl.querySelector(".tc-name").textContent = tile.dataset.title;
    // Nur die Kacheln oben stehen im Raster, die Bereiche darunter sind immer ganz breit
    if (tile.parentElement === grid) {
      ctl.insertAdjacentHTML("beforeend", `
      <button type="button" class="btn-sm tc-size tc-less" title="Schmaler" aria-label="${tile.dataset.title} schmaler">−</button>
      <button type="button" class="btn-sm tc-size tc-more" title="Breiter" aria-label="${tile.dataset.title} breiter">+</button>`);
      ctl.querySelector(".tc-less").addEventListener("click", () => resize(tile, -1));
      ctl.querySelector(".tc-more").addEventListener("click", () => resize(tile, 1));
    }
    const box = ctl.querySelector("input");
    box.checked = !tile.hasAttribute("data-off");
    box.setAttribute("aria-label", `${tile.dataset.title} anzeigen`);
    box.addEventListener("change", () => setOff(tile, !box.checked));
    ctl.querySelector(".tc-left").addEventListener("click", () => move(tile, -1));
    ctl.querySelector(".tc-right").addEventListener("click", () => move(tile, 1));
    tile.prepend(ctl);
  }

  function setEditing(on) {
    editing = on;
    document.body.classList.toggle("dash-edit", on);
    hint.hidden = resetBtn.hidden = addBtn.hidden = !on;
    pinBtn.hidden = on;
    editBtn.textContent = on ? "✓ Fertig" : "✎ Dashboard anpassen";
    editBtn.classList.toggle("primary", on);
    tiles().forEach((t) => {
      if (on) addControls(t);
      t.draggable = on;
    });
    if (on) refreshSizeButtons();
  }

  editBtn.addEventListener("click", () => setEditing(!editing));

  resetBtn.addEventListener("click", async () => {
    const yes = await askConfirm({
      title: "Dashboard zurücksetzen?",
      text: "Alle Kacheln werden wieder eingeblendet und in die ursprüngliche Reihenfolge gebracht.",
      ok: "Zurücksetzen",
    });
    if (!yes) return;
    await save({ reset: true });
    location.reload();
  });

  // Ziehen und Ablegen (Maus); auf dem Handy gibt es dafür die Pfeile
  document.addEventListener("dragstart", (e) => {
    const t = e.target.closest?.("[data-tile]");
    if (!editing || !t) return;
    dragged = t;
    t.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", t.dataset.tile);
  });
  document.addEventListener("dragover", (e) => {
    const over = e.target.closest?.("[data-tile]");
    if (!dragged || !over || over === dragged || over.parentElement !== dragged.parentElement) return;
    e.preventDefault();
    // Nach hinten gezogen: hinter die Kachel, nach vorne gezogen: davor
    const after = +over.style.order > +dragged.style.order;
    const list = group(dragged).filter((t) => t !== dragged);
    list.splice(list.indexOf(over) + (after ? 1 : 0), 0, dragged);
    renumber(list);
  });
  document.addEventListener("drop", (e) => { if (dragged) e.preventDefault(); });
  document.addEventListener("dragend", () => {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    dragged = null;
    persist();
  });
})();
