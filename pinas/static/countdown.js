// Countdowns: Restzeit bis zum Herunterfahren und die ablaufende Uhr für den Neustart nach einem Update:
//   <div class="clock" data-at="UNIX-ZEIT" data-total="15"> … (Markup siehe Makro countdown_clock in base.html)
// Der Ring leert sich, der Knopf am Ringende wandert mit, in der Mitte stehen die Sekunden.
(() => {
  document.querySelectorAll(".clock").forEach((clock) => {
    const at = +clock.dataset.at * 1000;
    const total = (+clock.dataset.total || 15) * 1000;
    const ring = clock.querySelector(".clock-ring");
    const hand = clock.querySelector(".clock-hand");
    const secs = clock.querySelector(".clock-s");
    const frame = () => {
      const left = Math.max(0, at - Date.now());
      const part = Math.min(1, left / total);
      ring.setAttribute("stroke-dasharray", `${(part * 100).toFixed(2)} 100`);
      // Der Ring läuft gegen den Uhrzeigersinn leer – der Knopf sitzt an seinem Ende
      hand.setAttribute("transform", `rotate(${(part * 360).toFixed(1)} 60 60)`);
      secs.textContent = Math.ceil(left / 1000);
      clock.classList.toggle("soon", left <= 5000);
      if (left > 0) {
        requestAnimationFrame(frame);
      } else {
        clock.classList.add("done");
        clock.querySelector(".clock-label").textContent = "Neu Start läuft …";
      }
    };
    frame();
  });

  // Restzeit bis zum geplanten Herunterfahren/Neustart (Hinweis oben auf jeder Seite)
  document.querySelectorAll(".power-countdown").forEach((el) => {
    const tick = () => {
      const s = Math.max(0, Math.round(+el.dataset.at - Date.now() / 1000));
      el.textContent = s > 59 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} Min` : `${s} s`;
      if (s > 0) setTimeout(tick, 1000);
    };
    tick();
  });

  // Datum und Uhrzeit des Pi in der Kopfzeile: Die Seite bringt die Zeit mit, der Browser zählt nur weiter.
  // So fällt eine falsche Uhr am Pi auf, Abstand und Zeitzone des Geräts spielen keine Rolle.
  const piTime = document.querySelector(".header-time");
  if (piTime) {
    const WEEKDAYS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
    const pad = (n) => String(n).padStart(2, "0");
    let offset = +piTime.dataset.now - Date.now();
    try {
      // Mit „Zurück“ kann die Seite aus dem Browser-Speicher kommen, ihre mitgebrachte Zeit ist dann alt
      const back = performance.getEntriesByType("navigation")[0]?.type === "back_forward";
      const saved = sessionStorage.getItem("pinasClockOffset");
      if (back && saved !== null) offset = +saved;
      else sessionStorage.setItem("pinasClockOffset", offset);
    } catch {}
    const date = piTime.querySelector(".ht-date");
    const clock = piTime.querySelector(".ht-clock");
    const paint = () => {
      const t = new Date(Date.now() + offset + +piTime.dataset.tz * 1000);  // Felder als UTC lesen = Ortszeit des Pi
      date.textContent = `${WEEKDAYS[t.getUTCDay()]}, ${pad(t.getUTCDate())}.${pad(t.getUTCMonth() + 1)}.${t.getUTCFullYear()} · `;
      clock.textContent = `${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
      return t;
    };
    const tick = () => {
      const t = paint();
      setTimeout(tick, 60000 - t.getUTCSeconds() * 1000 - t.getUTCMilliseconds());
    };
    tick();
    // Nach dem Aufwachen (Handy, Laptop) gleich richtig anzeigen, die Zeitgeber standen still
    document.addEventListener("visibilitychange", () => { if (!document.hidden) paint(); });
  }
})();
