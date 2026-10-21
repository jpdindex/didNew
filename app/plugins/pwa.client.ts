export default defineNuxtPlugin(() => {
  if (!('serviceWorker' in navigator)) return

  navigator.serviceWorker.register('/sw.js').catch((error) => {
    console.warn('PWA service worker registration failed:', error)
  })
})
