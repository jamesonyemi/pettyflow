/**
 * PettyFlow Custodian PWA Service Worker
 * Implements Cache-First caching for static shell and network-first for API queries.
 * Supports offline asset loading and background sync cues for pending expense drafts.
 */

const CACHE_NAME = "pettyflow-pwa-v1";
const STATIC_ASSETS = [
  "/",
  "/manifest.json",
  "/static/css/app.css",
  "/static/js/app.js",
];

// Install event: cache application shell
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

// Activate event: clean up stale caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event: Network-first for API & WS; Cache-first for shell assets
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Bypass service worker for WebSocket and non-GET requests
  if (event.request.method !== "GET" || url.pathname.startsWith("/v1/ws")) {
    return;
  }

  // API endpoints: network-first with graceful fallback
  if (url.pathname.startsWith("/v1/")) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return new Response(
          JSON.stringify({ error: "Offline mode. Operation queued locally." }),
          {
            status: 503,
            headers: { "Content-Type": "application/json" },
          }
        );
      })
    );
    return;
  }

  // Shell assets: cache-first
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(event.request).then((networkResponse) => {
        if (
          !networkResponse ||
          networkResponse.status !== 200 ||
          networkResponse.type !== "basic"
        ) {
          return networkResponse;
        }
        const responseToCache = networkResponse.clone();
        caches.open(CACHE_NAME).then((cache) => {
          cache.put(event.request, responseToCache);
        });
        return networkResponse;
      });
    })
  );
});

// Listen for messages from frontend
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});
