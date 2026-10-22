const CACHE_NAME = 'gamebridge-static-v18';
const STATIC_ASSETS = [
  '/',
  '/static/css/style.css?v=20',
  '/static/js/htmx.min.js',
  '/static/js/app.js?v=10',
  '/static/manifest.json',
  '/offline'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // NEVER cache sensitive or state-changing routes
  if (
    event.request.method !== 'GET' ||
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/uploads/') ||
    url.pathname.startsWith('/captain')
  ) {
    return;
  }

  // Cache-first for static CSS/JS, network-first for pages with offline fallback
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  } else {
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match(event.request).then((cached) => cached || caches.match('/offline'));
      })
    );
  }
});
