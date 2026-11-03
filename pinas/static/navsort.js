// Reiter der Seitenleiste sortieren. Gespeichert wird pro Benutzer auf dem NAS (dashboard.save_nav).
// - Maus: einfach ziehen; erst ab ein paar Pixeln Bewegung, ein normaler Klick öffnet den Reiter wie immer
// - Finger und Tastatur: Knopf „Reiter sortieren“. Dann ziehen, oder Reiter wählen und mit ▲▼ bzw. den Pfeiltasten
//   verschieben. Außerhalb dieses Modus bleibt das Wischen durch die Leiste auf dem Handy ungestört.
(() => {
  const nav = document.querySelector(".subnav");
  const list = nav?.querySelector(".inner");
  const toggle = document.getElementById("nav-sort");
  if (!list || !toggle) return;
  const panel = document.getElementById("nav-panel");
  const status = document.getElementById("nav-status");
  const THRESHOLD = 6;

  const tabs = () => [...list.querySelectorAll("a[data-tab]")];
  const current = () => tabs().map((t) => t.dataset.tab);
  const say = (text) => { status.textContent = text; };

  function apply(order) {
    const byId = new Map(tabs().map((t) => [t.dataset.tab, t]));
    order.forEach((id) => { if (byId.has(id)) list.append(byId.get(id)); });
  }
  window.PiNASNavApply = apply;

  async function post(body, fallback) {
    try {
      const r = await fetch("/api/reiter-reihenfolge", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": window.CSRF },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error();
      const { order } = await r.json();
      apply(order);
      // Während einer Übertragung liegt diese Seite im Rahmen über der eigentlichen: Dort gleich mitziehen
      try { if (window.top !== window) window.top.PiNASNavApply?.(order); } catch {}
      return true;
    } catch {
      apply(fallback);
      say("Das ließ sich nicht speichern. Die Reihenfolge bleibt wie zuvor.");
      return false;
    }
  }

  // ---------- Sortieren-Modus ----------
  let sorting = false;
  let selected = null;

  function select(tab) {
    selected?.classList.remove("nav-selected");
    selected = tab;
    tab?.classList.add("nav-selected");
  }

  function setSorting(on) {
    sorting = on;
    nav.classList.toggle("sorting", on);
    panel.hidden = !on;
    toggle.setAttribute("aria-pressed", String(on));
    toggle.querySelector(".nav-tool-text").textContent = on ? " Fertig" : " Reiter sortieren";
    toggle.title = on ? "Fertig" : "Reiter sortieren";
    toggle.setAttribute("aria-label", toggle.title);
    say("");
    select(on ? (list.querySelector("a.active") || tabs()[0]) : null);
  }

  async function step(tab, by) {
    const before = current();
    const all = tabs();
    const i = all.indexOf(tab);
    const j = i + by;
    if (!tab || j < 0 || j >= all.length) return;
    list.insertBefore(tab, by < 0 ? all[j] : all[j].nextSibling);
    tab.focus();
    say(`${tab.textContent}: Position ${j + 1} von ${all.length}`);
    await post({ order: current() }, before);
  }

  toggle.addEventListener("click", () => setSorting(!sorting));
  document.getElementById("nav-earlier").addEventListener("click", () => step(selected, -1));
  document.getElementById("nav-later").addEventListener("click", () => step(selected, 1));
  document.getElementById("nav-reset").addEventListener("click", async () => {
    const yes = await window.askConfirm({
      title: "Standard wiederherstellen?",
      text: "Die Reiter stehen danach wieder in der ursprünglichen Reihenfolge.",
      ok: "Wiederherstellen",
    });
    if (yes && await post({ reset: true }, current())) say("Standard-Reihenfolge wiederhergestellt.");
  });

  // Im Modus wählt ein Klick nur aus, sonst bleibt alles wie immer
  list.addEventListener("click", (e) => {
    const tab = e.target.closest("a[data-tab]");
    if (!sorting || !tab) return;
    e.preventDefault();
    select(tab);
  });

  list.addEventListener("keydown", (e) => {
    const tab = e.target.closest("a[data-tab]");
    if (!sorting || !tab) return;
    const by = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[e.key];
    if (by) {
      e.preventDefault();
      select(tab);
      step(tab, by);
    }
  });

  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sorting && !drag) setSorting(false); });

  // ---------- Ziehen ----------
  let press = null;  // gedrückt, aber noch nicht bewegt
  let drag = null;   // zieht gerade

  // Vor welchem Reiter landet der gezogene? (null = ans Ende)
  function dropTarget(x, y, moving) {
    const row = getComputedStyle(list).flexDirection === "row";
    for (const t of tabs()) {
      if (t === moving) continue;
      const r = t.getBoundingClientRect();
      if (row ? (y < r.bottom && (y < r.top || x < r.left + r.width / 2)) : y < r.top + r.height / 2) return t;
    }
    return null;
  }

  function placeMarker(marker, target) {
    const row = getComputedStyle(list).flexDirection === "row";
    const others = tabs().filter((t) => t !== drag.tab);
    const anchor = target || others[others.length - 1];
    const r = anchor.getBoundingClientRect();
    Object.assign(marker.style, row
      ? { left: `${(target ? r.left : r.right) - 1}px`, top: `${r.top}px`, width: "3px", height: `${r.height}px` }
      : { left: `${r.left}px`, top: `${(target ? r.top : r.bottom) - 1}px`, width: `${r.width}px`, height: "3px" });
  }

  function endDrag() {
    drag?.marker.remove();
    drag?.tab.classList.remove("nav-dragging");
    drag = null;
    press = null;
  }

  list.addEventListener("pointerdown", (e) => {
    const tab = e.target.closest("a[data-tab]");
    // Ohne Modus nur mit der Maus, damit Finger weiter durch die Leiste wischen
    if (!tab || e.button !== 0 || (e.pointerType !== "mouse" && !sorting)) return;
    press = { tab, x: e.clientX, y: e.clientY, id: e.pointerId };
  });

  list.addEventListener("pointermove", (e) => {
    if (!press || e.pointerId !== press.id) return;
    if (!drag) {
      if (Math.hypot(e.clientX - press.x, e.clientY - press.y) < THRESHOLD) return;
      const marker = document.createElement("div");
      marker.className = "nav-drop";
      document.body.append(marker);
      drag = { tab: press.tab, marker, target: undefined };
      drag.tab.classList.add("nav-dragging");
      try { list.setPointerCapture(e.pointerId); } catch {}
    }
    e.preventDefault();
    drag.target = dropTarget(e.clientX, e.clientY, drag.tab);
    placeMarker(drag.marker, drag.target);
  });

  // Der Klick direkt nach dem Ziehen darf den Reiter nicht öffnen
  function swallowClick() {
    const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
    addEventListener("click", stop, { capture: true, once: true });
    // Kommt keiner, gilt das nicht für den nächsten richtigen Klick
    addEventListener("pointerdown", () => removeEventListener("click", stop, { capture: true }), { capture: true, once: true });
  }

  list.addEventListener("pointerup", (e) => {
    if (!press || e.pointerId !== press.id) return;
    if (!drag) { press = null; return; }
    const { tab, target } = drag;
    const before = current();
    endDrag();
    swallowClick();
    if (target !== undefined) list.insertBefore(tab, target);
    if (current().join() !== before.join()) post({ order: current() }, before);
    if (sorting) select(tab);
  });

  list.addEventListener("pointercancel", endDrag);
  // Nicht als Link in andere Fenster ziehen
  list.addEventListener("dragstart", (e) => e.preventDefault());
})();
