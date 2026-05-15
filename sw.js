/* DEADLINE — service worker v3.7.54 (2026-05-14)
   Cache-first strategy for offline support after first load.
   Bump CACHE_VERSION whenever modes.css/modes.js/index.html change. */

const CACHE_VERSION = 'deadline-v3.7.54';
const CORE_ASSETS = [
  './',
  './index.html',
  './modes.css?v=3.7.54',
  './modes.js?v=3.7.54',
  './manifest.json',
  './icon.svg',
  './Prototypes/Resort_skins/00_master.html?v=3.7.54',
  // Mobile-optimized matrix video (smaller, almost-always-needed)
  './assets/video/morpheus-hero-720p.mp4'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) =>
      // addAll is atomic — if any single asset 404s the whole install fails.
      // Wrap each in catch to make partial caching OK.
      Promise.all(
        CORE_ASSETS.map((url) =>
          cache.add(url).catch((err) => {
            console.warn('[sw] failed to cache', url, err.message);
          })
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_VERSION)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  // Skip cross-origin requests (Unsplash, Google Fonts, GSAP CDN, Lenis CDN)
  // — let the network handle them with browser's own cache.
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Cache-first with network fallback. On success, update cache silently.
  event.respondWith(
    caches.match(req).then((cached) => {
      const networkFetch = fetch(req)
        .then((res) => {
          if (res && res.status === 200 && res.type === 'basic') {
            const clone = res.clone();
            caches.open(CACHE_VERSION).then((c) => c.put(req, clone));
          }
          return res;
        })
        .catch(() => cached);
      return cached || networkFetch;
    })
  );
});
