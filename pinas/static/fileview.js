// Dateiansicht wie im Windows-Explorer: acht Darstellungen, Sortierung, Dateinamenerweiterungen,
// Vorschaubilder und ein Rechtsklick-Menü. Die Einstellung merkt sich der Browser.
// files.js ruft PiNASView.refresh() auf, nachdem es die Liste neu geladen hat.
(() => {
  const zone = document.getElementById("dropzone");
  if (!zone) return;

  const KEY = "pinas.ansicht";
  const VIEWS = ["xl", "large", "medium", "small", "list", "details", "tiles", "content"];
  const THUMB_VIEWS = new Set(["xl", "large", "medium", "tiles", "content"]);
  const state = { view: "details", sort: "name", desc: false, ext: true, thumbs: true };
  try { Object.assign(state, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch {}

  // „Datei 2“ vor „Datei 10“ – natürliche Sortierung wie im Explorer
  const coll = new Intl.Collator("de", { numeric: true, sensitivity: "base" });
  const compare = {
    name: (a, b) => coll.compare(a.dataset.name, b.dataset.name),
    date: (a, b) => a.dataset.ts - b.dataset.ts,
    size: (a, b) => a.dataset.size - b.dataset.size,
    type: (a, b) => coll.compare(a.dataset.type, b.dataset.type),
  };
  if (!VIEWS.includes(state.view)) state.view = "details";
  if (!compare[state.sort]) state.sort = "name";

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch {}
  }

  function sortItems() {
    const list = zone.querySelector(".fv-list");
    if (!list) return;
    const dir = state.desc ? -1 : 1;
    const items = [...list.children].sort((a, b) =>
      (b.dataset.dir - a.dataset.dir)  // Ordner immer zuerst
      || compare[state.sort](a, b) * dir
      || coll.compare(a.dataset.name, b.dataset.name));
    list.append(...items);
    zone.querySelectorAll(".fv-head [data-sort]").forEach((h) => {
      h.setAttribute("aria-sort", h.dataset.sort !== state.sort ? "none" : state.desc ? "descending" : "ascending");
    });
  }

  // Vorschaubilder erst laden, wenn eine Ansicht sie zeigt (spart Datenverkehr in „Details“)
  function loadThumbs() {
    if (!zone.classList.contains("thumbs-on")) return;
    zone.querySelectorAll("img.fv-thumb:not([src])").forEach((img) => {
      img.addEventListener("load", () => img.parentElement.classList.add("has-thumb"), { once: true });
      img.addEventListener("error", () => img.remove(), { once: true });
      img.src = img.dataset.src;
    });
  }

  function apply() {
    zone.dataset.view = state.view;
    zone.classList.toggle("hide-ext", !state.ext);
    zone.classList.toggle("thumbs-on", state.thumbs && THUMB_VIEWS.has(state.view));
    const check = (sel, on) => document.querySelectorAll(sel).forEach((b) => b.setAttribute("aria-checked", on(b)));
    check("[data-set-view]", (b) => b.dataset.setView === state.view);
    check("[data-set-sort]", (b) => b.dataset.setSort === state.sort);
    check("[data-set-desc]", (b) => (b.dataset.setDesc === "1") === state.desc);
    check("[data-toggle]", (b) => !!state[b.dataset.toggle]);
    sortItems();
    loadThumbs();
    window.PiNASFiles?.sizes();
  }

  function closeMenus(except) {
    document.querySelectorAll("details.menu[open]").forEach((d) => { if (d !== except) d.open = false; });
  }

  // Kontextmenü (Rechtsklick bzw. langes Antippen)
  let ctx = null;
  function closeCtx() {
    if (ctx) { ctx.remove(); ctx = null; }
  }
  function openCtx(x, y, entries) {
    closeCtx();
    ctx = document.createElement("div");
    ctx.className = "ctx";
    ctx.setAttribute("role", "menu");
    for (const en of entries) {
      if (en === "-") { ctx.append(document.createElement("hr")); continue; }
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.textContent = en.label;
      if (en.danger) b.className = "danger-text";
      b.addEventListener("click", () => { closeCtx(); en.run(); });
      ctx.append(b);
    }
    document.body.append(ctx);
    const r = ctx.getBoundingClientRect();
    ctx.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + "px";
    ctx.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + "px";
    ctx.querySelector("button")?.focus();
  }

  zone.addEventListener("contextmenu", (e) => {
    if (e.shiftKey) return;  // Umschalt + Rechtsklick: normales Browser-Menü
    const item = e.target.closest(".fv-item");
    const entries = [];
    if (item) {
      const q = (s) => item.querySelector(s);
      const isDir = item.dataset.dir === "1";
      const selected = zone.querySelectorAll(".fv-selected").length;
      if (item.classList.contains("fv-selected") && selected > 1) {
        entries.push({ label: `${selected} ausgewählte herunterladen`, run: () => document.getElementById("pick-download").click() }, "-");
      }
      entries.push({ label: isDir ? "Öffnen" : "Öffnen (neuer Tab)", run: () => q(".fv-main").click() });
      if (q(".fv-where")) entries.push({ label: "Speicherort öffnen", run: () => q(".fv-where").click() });
      entries.push({ label: isDir ? "Als ZIP herunterladen" : "Herunterladen", run: () => q("[data-download]").click() });
      entries.push("-", ...(window.PiNASEdit?.itemEntries(item) || []));
      if (q("[data-rename]")) {  // fehlen bei „nur ansehen“
        const many = item.classList.contains("fv-selected") && selected > 1;
        entries.push("-");
        if (!many) entries.push({ label: "Umbenennen", run: () => q("[data-rename]").click() });
        entries.push(many ? { label: `Löschen (${selected})`, danger: true, run: () => window.PiNASEdit.removeSelected() }
                          : { label: "Löschen", danger: true, run: () => q("[data-delete]").click() });
      }
    } else {
      if (zone.dataset.writable === "1") {
        entries.push(
          { label: "Hochladen …", run: () => document.getElementById("file-input").click() },
          { label: "Neuer Ordner", run: () => document.getElementById("mkdir-btn").click() });
      }
      entries.push(...(window.PiNASEdit?.blankEntries() || []));
      if (!entries.length) return;
    }
    e.preventDefault();
    openCtx(e.clientX, e.clientY, entries);
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".ctx")) closeCtx();
    closeMenus(e.target.closest("details.menu"));
    const b = e.target.closest("[data-set-view], [data-set-sort], [data-set-desc], [data-toggle], .fv-head [data-sort]");
    if (!b) return;
    const d = b.dataset;
    if (d.setView) state.view = d.setView;
    else if (d.setSort) state.sort = d.setSort;
    else if (d.setDesc) state.desc = d.setDesc === "1";
    else if (d.toggle) state[d.toggle] = !state[d.toggle];
    else if (state.sort === d.sort) state.desc = !state.desc;  // Spaltenkopf: gleiche Spalte → umdrehen
    else { state.sort = d.sort; state.desc = false; }
    save();
    apply();
    if (!d.toggle) closeMenus();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeCtx(); closeMenus(); }
    // Strg+Umschalt+1 … 8 wie im Explorer
    const m = /^Digit([1-8])$/.exec(e.code);
    if (m && e.ctrlKey && e.shiftKey && !e.altKey) {
      e.preventDefault();
      state.view = VIEWS[m[1] - 1];
      save();
      apply();
    }
  });
  addEventListener("resize", closeCtx);
  addEventListener("scroll", closeCtx, true);

  window.PiNASView = { refresh: apply, sizeChanged: () => { if (state.sort === "size") sortItems(); } };
  apply();
})();
