import type { TeamSeasonEntry } from '~/types/schema'

export type RegistrationSeasonOption = { id: string; label: string; leagueIds: string[] }

export function registrationSeasonId(value: string): string {
  const digits = value.replace(/\D/g, '')
  const start = Number(digits.slice(0, 4))
  if (![6, 8].includes(digits.length) || start < 1900 || start > 9998) return ''
  const end = start + 1
  if (digits.slice(4) !== String(end).slice(digits.length === 6 ? 2 : 0)) return ''
  return `${start}${end}`
}

export function registrationSeasonOptions(documents: { id: string; leagueId?: string }[]): RegistrationSeasonOption[] {
  const options = new Map<string, RegistrationSeasonOption>()
  for (const document of documents) {
    const id = registrationSeasonId(document.id)
    if (!id) continue
    const previous = options.get(id)
    const leagueIds = document.leagueId ? [document.leagueId] : []
    options.set(id, {
      id, label: `${id.slice(0, 4)}-${id.slice(4)}`,
      leagueIds: previous ? (!previous.leagueIds.length || !leagueIds.length ? [] : [...new Set([...previous.leagueIds, ...leagueIds])]) : leagueIds,
    })
  }
  return [...options.values()].sort((a, b) => b.id.localeCompare(a.id))
}

export function isRegistrationFirstDivision(entry: TeamSeasonEntry | undefined, leagueId: string): boolean {
  if (entry?.division !== 'D1') return false
  // Explicit league memberships take precedence over the legacy cache.
  return entry.leagueIds?.length ? entry.leagueIds.includes(leagueId) : entry.leagueId === leagueId
}
