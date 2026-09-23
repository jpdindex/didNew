export type FormationSlot = { x: number; y: number }

const line = (count: number, y: number): FormationSlot[] => {
  const xs: Record<number, number[]> = {
    1: [50],
    2: [35, 65],
    3: [20, 50, 80],
    4: [14, 38, 62, 86],
    5: [10, 30, 50, 70, 90],
    6: [8, 25, 42, 58, 75, 92],
  }
  return (xs[count] ?? []).map(x => ({ x, y }))
}

export const FORMATIONS: Record<string, { label: string; slots: FormationSlot[] }> = {
  // 4-back
  '4-3-3': {
    label: '4-3-3', slots: [
      { x: 14, y: 72 }, { x: 38, y: 72 }, { x: 62, y: 72 }, { x: 86, y: 72 },
      { x: 26, y: 48 }, { x: 50, y: 48 }, { x: 74, y: 48 },
      { x: 20, y: 16 }, { x: 50, y: 16 }, { x: 80, y: 16 },
    ],
  },
  '4-2-3-1': {
    label: '4-2-3-1', slots: [
      { x: 14, y: 75 }, { x: 38, y: 75 }, { x: 62, y: 75 }, { x: 86, y: 75 },
      { x: 36, y: 54 }, { x: 64, y: 54 },
      { x: 18, y: 32 }, { x: 50, y: 32 }, { x: 82, y: 32 },
      { x: 50, y: 12 },
    ],
  },
  '4-1-4-1': {
    label: '4-1-4-1', slots: [
      ...line(4, 78), ...line(1, 59), ...line(4, 36), ...line(1, 12),
    ],
  },
  '4-4-2': {
    label: '4-4-2', slots: [
      { x: 14, y: 72 }, { x: 38, y: 72 }, { x: 62, y: 72 }, { x: 86, y: 72 },
      { x: 14, y: 46 }, { x: 38, y: 46 }, { x: 62, y: 46 }, { x: 86, y: 46 },
      { x: 35, y: 18 }, { x: 65, y: 18 },
    ],
  },
  '4-4-1-1': {
    label: '4-4-1-1', slots: [
      ...line(4, 78), ...line(4, 55), ...line(1, 33), ...line(1, 12),
    ],
  },
  '4-1-2-3': {
    label: '4-1-2-3', slots: [
      ...line(4, 78), ...line(1, 59), ...line(2, 38), ...line(3, 14),
    ],
  },
  '4-3-2-1': {
    label: '4-3-2-1', slots: [
      ...line(4, 78), ...line(3, 55), ...line(2, 32), ...line(1, 10),
    ],
  },
  '4-3-1-2': {
    label: '4-3-1-2', slots: [
      ...line(4, 78), ...line(3, 55), ...line(1, 34), ...line(2, 12),
    ],
  },
  '4-5-1': {
    label: '4-5-1', slots: [
      ...line(4, 74), ...line(5, 44), ...line(1, 14),
    ],
  },
  '4-2-1-3': {
    label: '4-2-1-3', slots: [
      ...line(4, 78), ...line(2, 57), ...line(1, 36), ...line(3, 14),
    ],
  },
  '4-2-2-2': {
    label: '4-2-2-2', slots: [
      ...line(4, 78), ...line(2, 57), ...line(2, 35), ...line(2, 12),
    ],
  },
  '4-2-4': {
    label: '4-2-4', slots: [
      ...line(4, 74), ...line(2, 46), ...line(4, 16),
    ],
  },

  // 3-back
  '3-5-2': {
    label: '3-5-2', slots: [
      { x: 26, y: 75 }, { x: 50, y: 75 }, { x: 74, y: 75 },
      { x: 10, y: 46 }, { x: 30, y: 46 }, { x: 50, y: 46 }, { x: 70, y: 46 }, { x: 90, y: 46 },
      { x: 35, y: 16 }, { x: 65, y: 16 },
    ],
  },
  '3-4-3': {
    label: '3-4-3', slots: [
      ...line(3, 74), ...line(4, 46), ...line(3, 16),
    ],
  },
  '3-1-3-3': {
    label: '3-1-3-3', slots: [
      ...line(3, 79), ...line(1, 60), ...line(3, 39), ...line(3, 14),
    ],
  },
  '3-4-2-1': {
    label: '3-4-2-1', slots: [
      ...line(3, 79), ...line(4, 57), ...line(2, 34), ...line(1, 11),
    ],
  },
  '3-6-1': {
    label: '3-6-1', slots: [
      ...line(3, 75), ...line(6, 44), ...line(1, 14),
    ],
  },
  '3-4-1-2': {
    label: '3-4-1-2', slots: [
      ...line(3, 79), ...line(4, 57), ...line(1, 34), ...line(2, 12),
    ],
  },
  '3-3-2-2': {
    label: '3-3-2-2', slots: [
      ...line(3, 79), ...line(3, 57), ...line(2, 34), ...line(2, 12),
    ],
  },
  '3-2-4-1': {
    label: '3-2-4-1', slots: [
      ...line(3, 79), ...line(2, 60), ...line(4, 36), ...line(1, 12),
    ],
  },
  '3-3-3-1': {
    label: '3-3-3-1', slots: [
      ...line(3, 79), ...line(3, 58), ...line(3, 36), ...line(1, 12),
    ],
  },
  '3-1-4-2': {
    label: '3-1-4-2', slots: [
      ...line(3, 79), ...line(1, 61), ...line(4, 38), ...line(2, 12),
    ],
  },
  '3-5-1-1': {
    label: '3-5-1-1', slots: [
      ...line(3, 79), ...line(5, 51), ...line(1, 31), ...line(1, 11),
    ],
  },

  // 5-back
  '5-3-2': {
    label: '5-3-2', slots: [
      ...line(5, 74), ...line(3, 46), ...line(2, 16),
    ],
  },
  '5-4-1': {
    label: '5-4-1', slots: [
      ...line(5, 74), ...line(4, 44), ...line(1, 14),
    ],
  },
}

export const GK_SLOT: FormationSlot = { x: 50, y: 94 }
export const BENCH_COUNT = 15
export const BENCH_IDS = Array.from({ length: BENCH_COUNT }, (_, index) => `b${index}`)

/** Screen reading order: top row left→right, then each lower row left→right. */
export function orderedOutfieldSlotIds(formationKey: string): string[] {
  const formation = FORMATIONS[formationKey]
  if (!formation) return []
  return formation.slots
    .map((slot, index) => ({ id: `o${index}`, ...slot }))
    .sort((left, right) => left.y - right.y || left.x - right.x || left.id.localeCompare(right.id))
    .map(slot => slot.id)
}

export function lineupAssignmentOrder(formationKey: string): string[] {
  return [...orderedOutfieldSlotIds(formationKey), 'gk', ...BENCH_IDS]
}

export function lineupOrderForSlot(formationKey: string, slot: string): number {
  if (slot === 'gk') return 1
  const outfieldIndex = orderedOutfieldSlotIds(formationKey).indexOf(slot)
  if (outfieldIndex >= 0) return outfieldIndex + 1
  const benchIndex = BENCH_IDS.indexOf(slot)
  return benchIndex >= 0 ? benchIndex + 1 : Number.MAX_SAFE_INTEGER
}

type DurableLineupEntry = {
  playerId?: unknown
  slot?: unknown
  order?: unknown
  type?: unknown
  pos?: unknown
}

function entryOrder(entry: DurableLineupEntry): number {
  const value = Number(entry.order)
  return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER
}

function normalizedPosition(value: unknown): string {
  const raw = String(value ?? '').toUpperCase().replace(/[^A-Z]/g, '')
  if (['GK', 'G', 'GOALKEEPER', 'KEEPER'].includes(raw)) return 'GK'
  return raw
}

function isGoalkeeper(entry: DurableLineupEntry): boolean {
  return entry.slot === 'gk' || normalizedPosition(entry.pos) === 'GK'
}

/**
 * Durable lineup order is a formation reading order, never the incidental o0…o9
 * coordinate-array order. Historic SQL has three independent lanes: GK order=1,
 * START order=1..10, and BENCH order=1..N.
 */
export function assignmentsFromLineup(formationKey: string, lineup: DurableLineupEntry[]): Record<string, string> {
  const valid = lineup.filter((entry): entry is DurableLineupEntry & { playerId: string } => typeof entry.playerId === 'string')
  const byOrder = (left: DurableLineupEntry & { playerId: string }, right: DurableLineupEntry & { playerId: string }) =>
    entryOrder(left) - entryOrder(right) || left.playerId.localeCompare(right.playerId)
  const starters = valid.filter(entry => entry.type === 'START')
  // A normalized snapshot's explicit GK lane is authoritative. Position is only
  // the fallback for older/newer payloads that do not yet carry slot='gk'.
  const goalkeeper = starters.find(entry => entry.slot === 'gk') ?? starters.find(isGoalkeeper)
  const outfield = starters.filter(entry => entry !== goalkeeper).sort(byOrder)
  const bench = valid.filter(entry => entry.type === 'BENCH').sort(byOrder)
  const outfieldSlots = orderedOutfieldSlotIds(formationKey)

  return Object.fromEntries([
    ...(goalkeeper ? [['gk', goalkeeper.playerId] as const] : []),
    ...outfield.slice(0, outfieldSlots.length).map((entry, index) => [outfieldSlots[index], entry.playerId] as const),
    ...bench.slice(0, BENCH_IDS.length).map((entry, index) => [BENCH_IDS[index], entry.playerId] as const),
  ])
}
