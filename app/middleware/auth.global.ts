import { canAccessManagement, isRestrictedManagementPath } from '~/utils/managementAccess'

export default defineNuxtRouteMiddleware(async (to) => {
  if (import.meta.server) return

  const { $auth, $authReady } = useNuxtApp()
  await $authReady

  const isLoggedIn = !!$auth.currentUser

  if (!isLoggedIn && to.path !== '/login') {
    return navigateTo('/login')
  }

  if (isRestrictedManagementPath(to.path) && !canAccessManagement($auth.currentUser?.email)) {
    return navigateTo('/manage')
  }

})
