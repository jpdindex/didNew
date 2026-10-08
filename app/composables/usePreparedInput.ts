import type { DraftEnterResponse } from '~/composables/useMatchCollaboration'

// A one-use, in-memory handoff. Refreshes and direct URLs still use server recovery.
export function usePreparedInput() {
  return useState<DraftEnterResponse | null>('did-prepared-input', () => null)
}
