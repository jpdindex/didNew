export default defineNuxtRouteMiddleware(async (to) => {
  if (import.meta.server) return

  const { $auth, $authReady } = useNuxtApp()
  await $authReady

  const isLoggedIn = !!$auth.currentUser

  if (!isLoggedIn && to.path !== '/login') {
    return navigateTo('/login')
  }

  if (isLoggedIn && to.path === '/login') {
    return navigateTo('/schedule')
  }
})
