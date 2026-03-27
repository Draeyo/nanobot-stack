// Service Worker — nanobot-stack PWA
// Cache strategy: cache-first for /static/*, network-first for /api/*, offline fallback for navigation

const CACHE_NAME = "nanobot-v1";

const PRECACHE_URLS = [
  "/",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nanobot — Hors ligne</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#0f0f1a;color:#e0e0e0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h1{font-size:1.5rem;color:#3b82f6;margin-bottom:1rem}
  p{color:#888;margin-bottom:2rem}
  button{background:#3b82f6;color:#fff;border:none;padding:10px 24px;
    border-radius:6px;font-size:1rem;cursor:pointer}
  button:hover{opacity:.85}
</style></head>
<body>
  <h1>Hors ligne</h1>
  <p>Reconnectez-vous au serveur Nanobot.</p>
  <button onclick="location.reload()">Réessayer</button>
</body></html>`;

const OFFLINE_JSON = JSON.stringify({ error: "offline" });

// ---------------------------------------------------------------------------
// install — precache critical assets
// ---------------------------------------------------------------------------
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // Cache what we can; ignore failures for assets not yet generated (icons)
      return Promise.allSettled(
        PRECACHE_URLS.map((url) =>
          cache.add(url).catch(() => {
            console.warn("[SW] Precache miss:", url);
          })
        )
      );
    }).then(() => self.skipWaiting())
  );
});

// ---------------------------------------------------------------------------
// activate — clean old caches, claim clients
// ---------------------------------------------------------------------------
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

// ---------------------------------------------------------------------------
// fetch — routing strategy
// ---------------------------------------------------------------------------
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // /api/* — network-first, offline JSON fallback
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request).catch(() =>
        new Response(OFFLINE_JSON, {
          status: 503,
          headers: { "Content-Type": "application/json" },
        })
      )
    );
    return;
  }

  // /static/* — cache-first
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Navigation requests — network-first, offline HTML fallback
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match("/").then((cached) =>
          cached ||
          new Response(OFFLINE_HTML, {
            status: 200,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          })
        )
      )
    );
    return;
  }
});

// ---------------------------------------------------------------------------
// push — show notification
// ---------------------------------------------------------------------------
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || "Nanobot";
  const options = {
    body: data.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: data.url || "/" },
    vibrate: [200, 100, 200],
    requireInteraction: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// ---------------------------------------------------------------------------
// notificationclick — open or focus window
// ---------------------------------------------------------------------------
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data.url || "/";
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if (client.url === targetUrl && "focus" in client) return client.focus();
        }
        if (clients.openWindow) return clients.openWindow(targetUrl);
      })
  );
});
