// =============================================================================
// Firestore 스키마 타입 정의
//
// docs/04_firestore_schema.md 가 사람이 읽는 설계도라면, 이 파일은 그걸 코드가
// 강제하게 만든 것이다. 컬렉션 구조가 바뀌면 문서를 먼저 고치고 여기를 맞춘다.
// 아직 실제 Firestore 읽기/쓰기 코드(컴포저블)는 없다 — 그건 다음 단계(B/D)다.
// 여기 정의된 각 인터페이스가 곧 해당 문서가 반드시 지켜야 할 필드 계약이다.
// =============================================================================

import type { Timestamp } from 'firebase/firestore'
import type { ActCode, ResCode } from '~/utils/didLogic'

// -----------------------------------------------------------------------------
// 공통 타입
// -----------------------------------------------------------------------------

/** 'H1'|'H2' = 전·후반, 'H3'|'H4' = 연장전반·연장후반. 45분을 넘긴 것("추가시간")은
 *  half 가 바뀌는 게 아니라 halfSeconds 가 계속 늘어나는 것뿐이다. */
export type Half = 'H1' | 'H2' | 'H3' | 'H4'

/** recordings 문서 ID와 동일. gi_write_code 대응 (§3, §6) */
export type Side = 'H' | 'A'

/** recordings.status. 화면 상태와 1:1이라 매핑 코드가 필요 없다 (§6) */
export type HalfStatus = 'ready' | 'H1' | 'H1_done' | 'H2' | 'H2_done' | 'final'

/** 집계·전송 트리거를 가른다. 레코드 저장 자체는 두 모드 동일 (§11.2) */
export type InputMode = '분석' | '실시간'

/** recorders/{uid}.level. 갱신 버튼 구성을 가른다 (§11.2.1) */
export type RecorderLevel = 'basic' | 'advanced'

/** recordings.recorders 맵의 값. 조직상 구분일 뿐 권한이 아니다 (§6.3) */
export type RecorderRank = 'main' | 'sub'

/** 이 값을 누가 입력했나. 비전 연동 전까지는 전부 'did' (§7, 원칙 8) */
export type DataSource = 'did' | 'vision'

// -----------------------------------------------------------------------------
// matches/{gm_id} — 일정 (§5)
// -----------------------------------------------------------------------------

/** 문서 ID 자체가 gm_id 이므로 별도 필드로 두지 않는다. 구조는 §5 참고:
 *  시즌(8) + 리그(3) + 리그 고정 상수(17) + 일련번호(4) = 32자.
 *  일정은 만들지 않고 footballx match_schedule.json 에서 그대로 가져온다 — 우리가
 *  일련번호를 발급하면 안 된다. */
export interface MatchDoc {
  date: string
  kickoffTime?: string
  leagueId: string
  seasonId: string
  round: number
  stadiumId: string
  homeTeamId: string
  awayTeamId: string
  /** 각 recording 이 자기 쪽 필드만 merge 로 쓴다 (§5.1) */
  score: { home: number; away: number }
  createdAt: Timestamp
  updatedAt: Timestamp
}

// -----------------------------------------------------------------------------
// matches/{gm_id}/recordings/{H|A} — 팀별 기록 세션 ★ (§6)
// -----------------------------------------------------------------------------

/** halves 맵의 값. 레코드에는 half+halfSeconds 만 저장하고, 경기 전체 기준 초는
 *  이 값으로 export 시점에 계산한다 — 저장 안 함 = 어긋날 일 없음 (§6.1) */
export interface HalfTiming {
  startedAt: Timestamp
  /** 종료 시 확정되는 그 half 의 총 경과초 */
  seconds: number
}

/** lineup 맵의 값. no/name/pos 는 스냅샷이다 — 이적·번호 변경이 과거 경기를
 *  바꾸면 안 된다 (원칙 7, §6.2) */
export interface LineupEntry {
  /** TeamSelection 의 assigned 키 그대로 (예: 'o0', 'gk', 'b3') */
  slot: string
  order: number
  type: 'START' | 'BENCH'
  no: string
  name: string
  pos: string
  inHalf: Half | null
  inSeconds: number | null
  outHalf: Half | null
  outSeconds: number | null
}

/** recordings.recorders 맵의 값 (§6.3) */
export interface RecorderEntry {
  rank: RecorderRank
  joinedAt: Timestamp
}

/** recordings.kpi — 확정 스냅샷. dMST 중 우리가 산출하는 14개 그대로 (§9.2) */
export interface RecordingKpi {
  /** gi_tmp */
  TAP: number
  DAP: number
  TTP: number
  DTP: number
  BAP: number
  /** 빌더 수 (gr_is_ctb 집계) */
  B: number
  /** 메이커 수 (gr_is_ctm 집계) */
  M: number
  /** 어시스터 수 (gr_is_cta 집계) */
  A: number
  /** 슈터 수 (gr_is_cts 집계) */
  S: number
  SHOT: number
  ASR: number
  /** (GOAL − OG) / SHOT (§7.3) */
  SSR: number
  /** 자책골 포함 (§7.3) */
  GOAL: number
  /** 자책골 수 */
  OG: number
}

export interface RecordingDoc {
  side: Side
  teamId: string
  opponentTeamId: string
  /** map. Firestore 는 map 의 키로 질의할 수 없어 recorderIds 를 따로 둔다 */
  recorders: Record<string, RecorderEntry>
  /** 질의용(array-contains). "내가 기록한 경기" 대시보드에 쓴다 */
  recorderIds: string[]
  status: HalfStatus
  inputMode: InputMode
  /** 전반 기준 진영. 구역코드 반전에 쓰인다 */
  fieldSide: 'left' | 'right'
  formationKey: string
  lineup: Record<string, LineupEntry>
  halves: Partial<Record<Half, HalfTiming>>
  /**
   * D-MST 한 행(= 이 기록 세션)을 가리키는 JaionX 쪽 키.
   * `시즌(4) + 라운드(2) + 그 라운드 안 팀 순번(2)` — 예: 26270219.
   * 날짜와 무관하며 라운드가 바뀌면 순번은 1부터 다시 시작한다.
   *
   * 계산하지 않고 match_schedule.json 에 있는 값을 그대로 옮긴다 — 채번 규칙이
   * 대외비 쪽에 있고 아직 확정되지 않아, 다시 계산했다가 틀리면 JaionX 와
   * 영영 맞지 않기 때문이다(규칙 0).
   */
  sRound?: number
  /** 레코드 정렬키 발급용. 세션 로드 시 여기서 이어받는다 (§7.2) */
  maxSeq: number
  /** 확정 전에는 null. 기록 중에는 저장하지 않는다 (§8) */
  kpi: RecordingKpi | null
  kpiComputedAt: Timestamp | null
  /** JaionX/aifootballx 동기화 매핑용. 신규 경기는 null */
  legacyGiId: number | null
  syncedAt: Timestamp | null
  createdAt: Timestamp
  updatedAt: Timestamp
}

// -----------------------------------------------------------------------------
// matches/{gm_id}/recordings/{H|A}/records/{recordId} — 플레이 1건 (§7)
// -----------------------------------------------------------------------------

/**
 * app/utils/didLogic.ts 의 DidRecord 가 클라이언트 스크래치 타입이고, 이게 그걸
 * Firestore 문서 모양으로 옮긴 것이다. 다른 점 하나: DidRecord 는 half 구분 없이
 * `seconds` 하나뿐이라 후반에도 0부터 다시 센다(§6.1 의 버그). Firestore 로 쓸 때는
 * 반드시 half + halfSeconds 로 분리해야 한다 — 이 변환은 다음 단계(D)에서 한다.
 */
export interface RecordDoc {
  half: Half
  /** 해당 half 기준 경과초. 4초 룰의 입력값 */
  halfSeconds: number
  /** 같은 초 안의 순서. 정렬은 half→halfSeconds→seq 순 (§7.2) */
  seq: number
  act: ActCode
  res: ResCode
  /** 1~18 구역코드 */
  area: number
  /** 0~100%. 레거시는 픽셀값이라 export 시 변환 필요 (§15 미결정) */
  posX?: number
  posY?: number
  shootPosX?: number
  shootPosY?: number
  /** 골포스트·크로스바 바깥 1m 판정 캐시 */
  shootDspRange?: boolean
  /** write-back 으로 act 가 비워져도 원래 슛이었음을 보존 */
  isShot?: boolean
  /** 'OWN' = 자책골 */
  playerId?: string
  /** 이 레코드(액트·위치)를 만든 사람의 uid */
  createdBy: string
  /** 선수를 넣은 사람의 uid. 2인 기록에서 createdBy 와 달라질 수 있다 (§6.3) */
  playerIdBy?: string
  source: DataSource
  playerIdSource?: DataSource
  /**
   * 이 레코드가 BAP 이벤트면 그 사유(didLogic.ts BapEvent.reason). BAP은 레거시
   * ff_game_bap 처럼 별도 테이블·ID가 필요 없다 — computeBap() 이 항상 레코드 1개당
   * 이벤트 0~1개를 산출하는 1:1 관계라, 별도 문서 대신 레코드 자신에게 표시하면 된다.
   * 확정(status:'final') 시점에만 채운다 — 기록 중에는 다른 파생값처럼 계산만 한다.
   */
  bapReason?: string
  createdAt: Timestamp
}

// -----------------------------------------------------------------------------
// 파생 데이터 — 확정(status:'final') 시점에만 배치로 쓴다 (§8)
// -----------------------------------------------------------------------------

/** .../paths/{pathId}. didLogic.ts 의 AttackPath 를 그대로 스냅샷한다 */
export interface PathSnapshotDoc {
  gtId: string
  recordIds: string[]
  ptype: 'UPP' | 'UTP' | 'DTP' | 'STP'
  /** DTP 중 유효슈팅을 포함하는 건 */
  dsp: boolean
  /** UTP 또는 DTP */
  ttp: boolean
  resCode: ResCode
}

/**
 * .../playerStats/{playerId}. playerMST 의 gp_* 컬럼과 대응하지만, 저장은 신용어로
 * 한다(원칙 2: CT*→DT*, 레거시 이름은 매핑에만 남긴다). JaionX playerMST 자체는 아직
 * gp_ctb 같은 옛 이름을 그대로 쓰지만(팀 단위 dMST 는 이미 B/M/A/S 로 바뀐 것과 다르게
 * 선수 단위는 안 바뀌었다), 그건 jpd-rating 이 내보낼 때의 이름이지 우리 저장소의
 * 이름이 아니다 — RecordFlags(isDtb/isDtm/isDta/isDts), RecordingKpi(B/M/A/S) 와
 * 같은 어휘를 쓴다.
 * gp_score_rel/gp_score_abs/gp_score(평점)는 여기 없다 — jpd-rating(대외비)만 채운다.
 */
export interface PlayerStatsDoc {
  /** gp_tmp */
  TAP: number
  /** gp_tap */
  DAP: number
  /** gp_utp (변경 없음) */
  UTP: number
  /** gp_ctp */
  DTP: number
  /** gp_ttp (변경 없음) */
  TTP: number
  /** gp_sht (변경 없음) */
  SHOT: number
  /** gp_ast (변경 없음) */
  AST: number
  /** gp_goal (변경 없음) */
  GOAL: number
  /** gp_ctb */
  DTB: number
  /** gp_ctm */
  DTM: number
  /** gp_cta */
  DTA: number
  /** gp_cts */
  DTS: number
  /** gp_gtb (변경 없음) */
  GTB: number
  /** gp_gtm (변경 없음) */
  GTM: number
  /** gp_asr (변경 없음) */
  ASR: number
  /** gp_ssr (변경 없음) */
  SSR: number
}

// -----------------------------------------------------------------------------
// 참조 · 데이터 관리 (§10) — 아직 화면 미구현(manage/* 는 "준비 중"). 형태만 먼저 고정한다.
// -----------------------------------------------------------------------------

/** teams/players/contracts/stadiums/leagues/seasons 공통 감사 필드 (§10.6).
 *  records 에는 붙이지 않는다 — 세션이 이미 recorders 로 귀속된다. */
export interface AuditFields {
  createdAt: Timestamp
  createdBy: string
  updatedAt: Timestamp
  updatedBy: string
}

/** players/{playerId} — 정체성만. 소속 팀은 시간에 따라 변하므로 여기 두지 않는다 (§10.1) */
export interface PlayerDoc extends AuditFields {
  name: string
  nameEn?: string
  nameFull?: string
  birth?: string
  height?: number
  foot?: 'L' | 'R' | 'B'
  nation?: string
  /** 은퇴/삭제 대신 비활성 — 과거 기록을 고아로 만들지 않는다 */
  active: boolean
  /** 파생 캐시. 계약 원장에서 갱신된다 */
  currentTeamId?: string
  currentNo?: string
  currentPos?: string
  legacyPlayerId?: number
}

/** players/{playerId}/contracts/{contractId} — 이적시장 원장 ★. 추가 전용 (§10.2) */
export interface ContractDoc extends AuditFields {
  teamId: string
  leagueId?: string
  seasonId?: string
  from: string
  /** null 이면 현재 소속 */
  to: string | null
  no?: string
  pos?: string
  /** 직전 소속팀. 이 한 문서로 "A→B" 이적 한 건이 완성된다 */
  fromTeamId?: string
  transferType?: 'TRANSFER' | 'LOAN' | 'LOAN_RETURN' | 'FREE' | 'YOUTH' | 'RETIRE'
  fee?: number
  currency?: string
}

/** teams/{teamId}/squad/{playerId} — 현재 스쿼드 파생 뷰. 원본이 아니다 (§10.3) */
export interface SquadEntry {
  name: string
  no: string
  pos: string
  contractId: string
  since: string
}

/** teams/{teamId} (§10.4) */
export interface TeamDoc extends AuditFields {
  name: string
  nameKr?: string
  nameFull?: string
  nameShort?: string
  textColor?: string
  stadiumId?: string
  coach?: string
  coachKr?: string
  foundedAt?: string
  dissolvedAt?: string
  /** 목록 필터용 캐시일 뿐. 승강제는 teams/{id}/seasons 서브컬렉션으로 표현한다 */
  currentLeagueId?: string
  crestUrl?: string
}

/** teams/{teamId}/seasons/{seasonId} — 시즌별 소속 리그. 승강제 대응 (§10.4) */
export interface TeamSeasonEntry {
  leagueId: string
  division?: string
  finalRank?: number
}

export interface LeagueDoc extends AuditFields {
  name: string
  nameEn?: string
  country?: string
}

export interface SeasonDoc extends AuditFields {
  leagueId: string
  name: string
  from: string
  to: string
  alias?: string
}

export interface StadiumDoc extends AuditFields {
  name: string
  nameKr?: string
  seats?: number
  country?: string
  city?: string
  homeTeamId?: string
  surface?: string
}

/** recorders/{uid} — 로그인 프로필. recordings.recorders(RecorderEntry, rank)와는
 *  다른 것이다 — 이건 사람 한 명의 계정 정보, 그건 한 세션 안에서의 참여 기록. */
export interface RecorderProfileDoc {
  name: string
  teamId?: string
  /** 데이터 관리(이적시장·팀 정보) 권한 기준 */
  role: 'recorder' | 'admin'
  /** 갱신 버튼 구성을 가른다 (§11.2.1) */
  level: RecorderLevel
  handedness?: 'L' | 'R'
}
