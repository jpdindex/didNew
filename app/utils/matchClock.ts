import type { Half } from '~/types/schema'

const halfStartMinutes: Record<Half, number> = { H1: 0, H2: 45, H3: 90, H4: 105 }

export function formatMatchMinute(elapsedSeconds: number, half: Half) {
  return String(halfStartMinutes[half] + Math.floor(elapsedSeconds / 60)).padStart(2, '0')
}

export function formatMatchClock(elapsedSeconds: number, half: Half) {
  const seconds = String(elapsedSeconds % 60).padStart(2, '0')
  return `${formatMatchMinute(elapsedSeconds, half)}:${seconds}`
}
