/* Service worker de GolModel: guarda SOLO la cáscara estática de la app (HTML/CSS/JS/iconos) para que abra rápido y sin red.
   NUNCA guarda respuestas de la API ni nada con datos: lo que no es de /app/ pasa directo a la red. */
const VERSION = "golmodel-pwa-v1";
const CASCARA = ["./", "index.html", "app.css", "app.js", "icon.svg", "icon-192.png", "icon-512.png", "manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(CASCARA)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || !url.pathname.startsWith("/app/")) return;   // API y todo lo demás: red directa
  e.respondWith(
    fetch(e.request).then((r) => {                                                    // primero la red (siempre la versión nueva); la caché es el respaldo sin conexión
      if (r.ok) { const copia = r.clone(); caches.open(VERSION).then((c) => c.put(e.request, copia)); }
      return r;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("index.html")))
  );
});
