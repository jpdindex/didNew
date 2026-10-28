const managementEmail = 'jpdindex@gmail.com'

export function canAccessManagement(email?: string | null): boolean {
  return email?.trim().toLowerCase() === managementEmail
}

export function isRestrictedManagementPath(path: string): boolean {
  const normalizedPath = path.toLowerCase().replace(/\/+$/, '')
  // New management pages are restricted by default; only the menu and the
  // analyst dashboard remain available to every signed-in user.
  return normalizedPath.startsWith('/manage/')
    && normalizedPath !== '/manage/analyst-dashboard'
}
