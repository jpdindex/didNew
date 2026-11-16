const managementEmails = new Set(['jpdindex@gmail.com'])

export function canAccessManagement(email?: string | null): boolean {
  return managementEmails.has(email?.trim().toLowerCase() ?? '')
}

export function isRestrictedManagementPath(path: string): boolean {
  const normalizedPath = path.toLowerCase().replace(/\/+$/, '')
  // New management pages are restricted by default. Analysts can access the
  // dashboard, GoalX and player requests.
  return normalizedPath.startsWith('/manage/')
    && normalizedPath !== '/manage/analyst-dashboard'
    && normalizedPath !== '/manage/player-requests'
    && normalizedPath !== '/manage/goalx'
}
