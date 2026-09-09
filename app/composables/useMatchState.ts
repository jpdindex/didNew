import type { DidRecord } from '~/utils/didLogic'
import type { GrassLines, GrassPattern } from '~/utils/grass'
import type { Half, HalfStatus, RecorderLevel } from '~/types/schema'

// TeamSelection ↔ DidInput 이 공유하는 임시 상태.
// Firestore 연동 전까지 useState 로 메모리에만 들고 있는다 (새로고침하면 사라짐).
// 필드 모양은 나중에 Firestore 문서로 그대로 옮길 수 있도록, 계산 가능한 값(KPI 등)은
// 저장하지 않고 records 로부터 항상 다시 계산한다.
//
// HalfStatus/RecorderLevel 은 types/schema.ts 가 정본이다 — recordings.status /
// recorders.level 과 같은 타입을 써야 나중에 Firestore 연동 시 값이 어긋나지 않는다.
// 기존 import 경로(~/composables/useMatchState)를 쓰는 다른 파일들이 계속 동작하도록 재수출한다.
export type { HalfStatus, RecorderLevel }

/**
 * 선수 교체 1건. 레거시 ff_game_player_log 대응이지만, 새 스키마에는 별도 컬렉션이
 * 없고 RecordingDoc.lineup[playerId] 의 inHalf/inSeconds/outHalf/outSeconds 로 병합된다
 * (docs/05_legacy_field_mapping.md). 화면에서는 "교체 이력" 표를 그려야 해서 이벤트
 * 단위로 들고 있다가, Firestore 저장 단계에서 lineup 쪽으로 접어 넣는다.
 *
 * outPlayer/inPlayer 는 players(HOME_SQUAD/AWAY_SQUAD) 배열의 인덱스다 —
 * assigned 와 같은 키 체계를 써야 슬롯이 바뀌어도 사람이 안 바뀐다.
 */
export interface SubRecord {
  half: Half
  seconds: number
  outPlayer: number
  inPlayer: number
}
export interface CardRecord { half: Half; seconds: number; player: number; card: 'Y' | 'R' }

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
  // 화면 좌우 반전. 경기장·기록표(left)와 액트 입력판(right)의 위치를 통째로 바꾼다.
  // 레거시 recorders.handedness(좌/우손)에 대응하는 개인 취향 설정이다.
  mirrored: boolean
  // DidInput 진행 상황
  halfStatus: HalfStatus
  // 현재 half 의 경과초. "대기방으로 나가기"로 빠져나왔다가 다시 들어올 때 이 시간부터 이어간다.
  // 새 half 를 시작할 때(전반전 시작/후반전 시작)는 0 으로 초기화한다.
  seconds: number
  // 전반/후반이 끝난 시점의 확정 경과초. schema.ts 의 HalfTiming.seconds 대응.
  // "수정" 화면에서 전반↔후반을 전환할 때 각자의 시간을 그대로 보여주는 데 쓴다 —
  // seconds 하나로는 마지막에 종료한 half 값만 남아 다른 half 시간을 알 수 없다.
  h1Seconds: number
  h2Seconds: number
  homeScore: number
  awayScore: number
  records: DidRecord[]
  // 선수 교체 이력. 경기 시작 전(halfStatus:'ready')의 라인업 변경은 교체가 아니므로
  // 여기 남기지 않는다 — 그건 그냥 명단을 고치는 것이다.
  subs: SubRecord[]
  cards: CardRecord[]
  // ---- 갱신(=KPI 확정) 흐름. §11.2.1 참고 ----
  recorderLevel: RecorderLevel
  // 전반전/후반전 갱신을 누르면 true. basic 등급에서만 의미가 있으며, true 인 동안은
  // 그 half 의 "수정"·"갱신" 버튼이 함께 잠긴다. 관리자 잠금 해제로만 다시 false 가 된다.
  h1Locked: boolean
  h2Locked: boolean
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
    mirrored: false,
    halfStatus: 'ready',
    seconds: 0,
    h1Seconds: 0,
    h2Seconds: 0,
    homeScore: 0,
    awayScore: 0,
    records: [],
    subs: [],
    cards: [],
    recorderLevel: 'advanced',
    h1Locked: false,
    h2Locked: false,
  }
}

export function useMatchState() {
  return useState<MatchState>('did-match-state', defaultMatchState)
}

export function resetMatchState() {
  useMatchState().value = defaultMatchState()
}
