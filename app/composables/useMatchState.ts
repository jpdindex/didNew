import type { DidRecord } from '~/utils/didLogic'
import type { GrassLines, GrassPattern } from '~/utils/grass'

// TeamSelection ↔ DidInput 이 공유하는 임시 상태.
// Firestore 연동 전까지 useState 로 메모리에만 들고 있는다 (새로고침하면 사라짐).
// 필드 모양은 나중에 Firestore 문서로 그대로 옮길 수 있도록, 계산 가능한 값(KPI 등)은
// 저장하지 않고 records 로부터 항상 다시 계산한다.

export type HalfStatus = 'ready' | 'H1' | 'H1_done' | 'H2' | 'H2_done' | 'final'

export interface MatchState {
  // 어느 경기의 상태인지. schedule 에서 다른 경기를 새로 선택하면 초기화 판단 기준이 된다.
  matchId: string
  // TeamSelection 라인업 관련
  team: 'home' | 'away'
  formationKey: string
  assigned: Record<string, number> // slotId -> players 배열 인덱스
  side: 'left' | 'right' | null
  inputMode: '분석' | '실시간'
  grassPattern: GrassPattern
  grassLines: GrassLines
  // DidInput 진행 상황
  halfStatus: HalfStatus
  homeScore: number
  awayScore: number
  records: DidRecord[]
}

function defaultMatchState(): MatchState {
  return {
    matchId: '',
    team: 'home',
    formationKey: '',
    assigned: {},
    side: null,
    inputMode: '분석',
    grassPattern: 1,
    grassLines: 10,
    halfStatus: 'ready',
    homeScore: 0,
    awayScore: 0,
    records: [],
  }
}

export function useMatchState() {
  return useState<MatchState>('did-match-state', defaultMatchState)
}

export function resetMatchState() {
  useMatchState().value = defaultMatchState()
}
