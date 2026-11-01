// Eigener Bestätigungsdialog statt des Browser-Fensters "confirm()".
// - askConfirm({title, text, ok, danger}) liefert ein Promise<boolean>
// - Formulare mit data-confirm="Text" fragen vor dem Absenden automatisch nach
//   (optional: data-confirm-title, data-confirm-ok, data-confirm-danger)
(() => {
  const dialog = document.getElementById("confirm-dialog");

  window.askConfirm = ({ title = "Bitte bestätigen", text = "", ok = "OK", danger = false } = {}) =>
    new Promise((resolve) => {
      if (!dialog || typeof dialog.showModal !== "function") {
        resolve(window.confirm(`${title}\n\n${text}`));  // sehr alte Browser
        return;
      }
      document.getElementById("confirm-title").textContent = title;
      document.getElementById("confirm-text").textContent = text;
      const okBtn = document.getElementById("confirm-ok");
      okBtn.textContent = ok;
      okBtn.classList.toggle("danger-fill", danger);
      dialog.returnValue = "";
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
      dialog.showModal();
      document.getElementById("confirm-cancel").focus();  // Enter bestätigt nicht versehentlich
    });

  // Klick neben den Dialog = Abbrechen
  dialog?.addEventListener("click", (e) => { if (e.target === dialog) dialog.close("cancel"); });

  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.dataset || form.dataset.confirm === undefined || form.dataset.confirmed) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const yes = await askConfirm({
      title: form.dataset.confirmTitle || "Bitte bestätigen",
      text: form.dataset.confirm,
      ok: form.dataset.confirmOk || "OK",
      danger: form.dataset.confirmDanger !== undefined,
    });
    if (!yes) return;
    form.dataset.confirmed = "1";
    form.requestSubmit ? form.requestSubmit() : form.submit();
  }, true);
})();
