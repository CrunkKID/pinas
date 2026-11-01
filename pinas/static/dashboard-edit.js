// Dashboard anpassen: Kacheln ein-/ausblenden und verschieben (Pfeile oder Ziehen).
// Die Reihenfolge steckt in CSS "order", gespeichert wird pro Benutzer auf dem NAS.
(() => {
  const editBtn = document.getElementById("dash-edit");
  const resetBtn = document.getElementById("dash-reset");
  const hint = document.querySelector(".dash-hint");
  if (!editBtn) return;
  const tiles = () => [...document.querySelectorAll("[data-tile]")];
  // Geschwister im selben Bereich (Kacheln oben bzw. Dienste/Laufwerke unten), sortiert nach Position
  const group = (el) => [...el.parentElement.querySelectorAll(":scope > [data-tile]")]
    .sort((a, b) => a.style.order - b.style.order);
  let editing = false;
  let dragged = null;

  async function save(body) {
    try {
      await fetch("/api/dashboard-layout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": window.CSRF },
        body: JSON.stringify(body),
      });
    } catch {}
  }

  function persist() {
    const all = tiles().sort((a, b) => a.style.order - b.style.order);
    save({ order: all.map((t) => t.dataset.tile), hidden: all.filter((t) => t.hasAttribute("data-off")).map((t) => t.dataset.tile) });
  }

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
    persist();
  }

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
    hint.hidden = resetBtn.hidden = !on;
    editBtn.textContent = on ? "✓ Fertig" : "✎ Dashboard anpassen";
    editBtn.classList.toggle("primary", on);
    tiles().forEach((t) => {
      if (on) addControls(t);
      t.draggable = on;
    });
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
