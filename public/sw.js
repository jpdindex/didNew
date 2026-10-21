// PWA installation requires a service worker. Network responses are left
// untouched so local development always shows the newest Nuxt build.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', () => {})
