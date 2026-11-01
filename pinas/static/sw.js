// Service Worker der PiNAS-App: Er macht die Oberfläche installierbar und zeigt eine eigene
// Offline-Seite, wenn das NAS nicht erreichbar ist. Persönliche Seiten und Dateien werden
// bewusst NICHT zwischengespeichert – nur Design, Skripte und Symbole.
const CACHE = "pinas-app-v1";
const SHELL = [
  "/offline",
  "/static/style.css",
  "/static/confirm.js",
  "/static/countdown.js",
  "/static/app/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;

  // Seiten: immer frisch vom NAS; nur wenn es nicht antwortet, die Offline-Seite
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  // Design und Skripte: vom NAS, bei Ausfall aus dem Zwischenspeicher
  const path = new URL(req.url).pathname;
  if (path.startsWith("/static/")) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) caches.open(CACHE).then((c) => c.put(path, res.clone()));
          return res;
        })
        .catch(() => caches.match(path)),
    );
  }
});
