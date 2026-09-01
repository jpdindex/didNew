// =============================================================================
// DID KPI 로직 (공격루트 판정 + DAP 산출 + BAP 카운트)
//
// 원본: lib/extra.lib.php 의 dplay_game_path_* / dplay_game_bap_* 함수군
// 평점(rating) 계산은 포함하지 않음 — 별도 처리 예정.
//
// 용어 변경 이력과 근거는 docs/03_kpi_terminology.md 참고. 요약:
//   TMP → TAP  (gr_is_tmp)  : 액트 발생 자체
//   TAP → DAP  (gr_is_tap)  : 유효 공격 포인트  ※ 레거시 TAP 과 의미가 다르니 혼동 주의
//   CTP → DTP  (gt_ctp)     : 슛을 포함한 공격루트
//   CSP → DSP  (gt_csp)     : DTP 중 유효슈팅을 포함하는 건
//   CTS → DTS / CTA → DTA / CTM → DTM / CTB → DTB   (선수 역할)
//   변경 없음: UPP, UTP, STP, TTP, GTB, GTM, SHOT, ASR, GSR, SSR, BAP
//
// -----------------------------------------------------------------------------
// [데이터 모델] 플레이 하나 = 레코드 하나. (APK DPlayInputDefaultFragment 로 확인)
// -----------------------------------------------------------------------------
//   1) 액트 버튼(C/P/K/F/S/H/R)을 누르면        → act = 누른 값, res = 'O'
//      ('O' 는 앱이 실제로 저장하는 값이다. APK onClickAction: setResCode("O"))
//   2) 결과 버튼(X/B, 골대 존)을 누르면          → 같은 레코드의 res 만 덮어쓴다
//   3) 예외: 슛(S/H/R)에 X/B 가 찍힌 경우에만    → act 를 '' 로 비운다
//      (APK onClickResult: setActCode(null))
//   4) 3) 이 발생하면 서버가 직전 레코드의 res 도 같은 값으로 덮어쓴다 (write-back)
//      (extra.lib.php:1211) → applyResult() 가 이걸 재현한다
//
// -----------------------------------------------------------------------------
// [판정 기준] 서버 배치가 아니라 "단말(APK)" 로직을 따른다
// -----------------------------------------------------------------------------
//   레거시에는 같은 계산이 두 벌 있고, 둘이 서로 다르게 동작한다.
//
//   (1) 단말 실시간 판정 — DPlayInputFragment.calcMadenPath / calcBapData
//       보정 없이 원본 res 를 그대로 본다. res 가 'O'가 아니면 즉시 루트 종료.
//       → X, B, 슛 결과 모두 루트를 끊는다.
//
//   (2) 서버 배치 재계산 — extra.lib.php dplay_game_path_reset_calc (:2148)
//       act 가 있는 레코드의 res 를 'O'로 되돌리는 lookahead 보정이 있다.
//       → C/P/K/F 는 res 가 X/B 여도 루트를 끊지 않는다.
//       주석상 의도는 "DID와 동일한 기준 및 순서로 판단하기 위한 처리" 지만,
//       실제로는 (1)과 결과가 달라진다.
//
//   우리가 다시 만드는 것은 입력 단말(DID) 자체이고, 실제 운영에서 관측된 동작도
//   "X/B/슛이 나오면 연결이 끊긴다" 이므로 (1)을 기준으로 삼는다.
//   BAP 도 마찬가지로 단말 calcBapData 를 따른다 (서버판에 있는 empty($act) 검사가
//   단말에는 없어서, 중앙선 돌파 분기의 조건이 서로 다르다).
//
//   분류/플래그 산출(classifyChain)은 두 구현이 동일하므로 그대로 포팅했다.
// =============================================================================

/** 액트 코드. C/P/K/F = 연결 액션, S/H/R = 슈팅 액션. '' = 슛이 X/B 로 끝나 비워진 상태. */
export type ActCode = 'C' | 'P' | 'K' | 'F' | 'S' | 'H' | 'R' | ''

/**
 * 결과 코드.
 *   'O'                  진행중. 액트를 누른 직후의 값이며 실제로 저장된다.
 *   'X'                  실책으로 뺏김        / 'B' 상대 수비 블락
 *   'GOAL'               득점 (레거시 res_code 'G' 에 대응)
 *   'L' | 'H' | 'R'      유효 슈팅 — 골대 안쪽을 탭한 좌표에서 앱이 파생시키는 값
 *   'LX' | 'HX' | 'RX'   빗나간 슈팅
 *   ''                   결과 미입력 — 판정 대상에서 제외된다
 */
export type ResCode = 'O' | 'X' | 'B' | 'GOAL' | 'L' | 'H' | 'R' | 'LX' | 'HX' | 'RX' | ''

/** 레코드 한 건. 레거시 ff_game_record 1행에 대응. */
export interface DidRecord {
  id: string
  /** 해당 half 시작 기준 경과초(정수). 레거시 gr_seconds. 4초 규칙 판정에 쓰인다. */
  seconds: number
  act: ActCode
  res: ResCode
  /** 1~18 구역코드 (03.areacode.sql 스펙). 1~6 = 공격영역, 10 이상 = 자기 진영 */
  area: number
  /** 경기장을 클릭한 실제 좌표(0~100%). area 는 이 좌표가 속한 구역일 뿐이라 정밀도가
   *  떨어진다 — 기록 수정 화면에서 "정확한 위치"를 보여줄 때는 area 대신 이 값을 쓴다. */
  posX?: number
  posY?: number
  /** 레거시 p_id. 'OWN' = 자책골 */
  playerId?: string
  /** 같은 초에 여러 레코드가 있을 때의 순서 보정 (레거시 gr_regdt 대응) */
  seq?: number
  /**
   * 골대 안쪽을 탭한 좌표. DSP(유효슛) 판정에 필요하다.
   * ※ 현재 DidInput.vue 는 골대가 이산 존 버튼이라 이 좌표를 수집하지 않는다.
   *   그래서 res='B' 인 유효슛이 DSP 로 잡히지 않는다 — docs/03_kpi_terminology.md 참고.
   */
  shootPosX?: number
  shootPosY?: number
  /** 골포스트·크로스바 바깥 1m를 포함한 DSP 타깃 범위인지 여부. */
  shootDspRange?: boolean
  /** X/B 결과 처리 뒤에도 원래 슈팅 액트였음을 보존한다. DSP 판정에 사용한다. */
  isShot?: boolean
}

function isShotAct(act: ActCode) {
  return act === 'S' || act === 'H' || act === 'R'
}

/** 'GOAL' 을 레거시 res_code 'G' 와 동일하게 취급한다. */
function isGoal(res: ResCode) {
  return res === 'GOAL'
}

/**
 * 판정 대상 레코드만 시간순으로 추린다.
 * 레거시 정렬 기준: gr_seconds asc, gr_regdt asc → seconds, seq 순.
 * (Array.sort 는 안정 정렬이므로 seq 가 없으면 입력 순서가 유지된다)
 */
function resolvedOnly(records: DidRecord[]) {
  return records
    .filter(r => r.res !== '')
    .slice()
    .sort((a, b) => (a.seconds - b.seconds) || ((a.seq ?? 0) - (b.seq ?? 0)))
}

// -----------------------------------------------------------------------------
// 레코드 생성/갱신 헬퍼 — 위 데이터 모델을 코드로 강제한다.
// -----------------------------------------------------------------------------

let recSeq = 0

/** 액트 버튼을 누른 시점의 레코드를 만든다. res 는 항상 'O'. */
export function createActRecord(
  act: Exclude<ActCode, ''>,
  seconds: number,
  area: number,
  extra: Partial<Pick<DidRecord, 'playerId' | 'shootPosX' | 'shootPosY' | 'posX' | 'posY'>> = {}
): DidRecord {
  return { id: `rec_${++recSeq}`, seconds, act, res: 'O', area, seq: recSeq, isShot: isShotAct(act), ...extra }
}

/**
 * 결과 버튼(X/B, 골대 존)을 누른 것을 반영한다.
 *
 * X/B 는 액트의 "결과"이므로 액트 레코드에 결과를 기록하되,
 * **결과 자체를 나타내는 act 없는 레코드를 하나 더 남긴다.** 레거시와 동일한 형태다.
 *
 *   비-슛 (C/P/K/F) + X/B →  액트 레코드는 act 를 유지한 채 res 만 채우고,
 *                            바로 뒤에 `act='' , res=X|B` 레코드를 추가한다.
 *                            액트 레코드는 여전히 P/C 라는 행동이므로 DAP 대상이 되고,
 *                            추가된 결과 레코드는 act 가 없어 DAP 에서 자동 제외된다.
 *   슛 (S/H/R) + X/B      →  슛 레코드 자체의 act 가 비워져 그것이 결과 레코드가 되고
 *                            (APK onClickResult), 직전 레코드에 write-back 한다
 *                            (extra.lib.php:1211). 별도 레코드를 만들지 않는다.
 *   골대 존 결과           →  해당 슛 레코드의 res 만 덮어쓴다.
 *
 * @param records 시간순 레코드 배열 (제자리에서 수정된다)
 * @param recordId 결과를 적용할 레코드 id
 */
export function applyResult(
  records: DidRecord[],
  recordId: string,
  res: Exclude<ResCode, 'O' | ''>,
  opts: {
    shootPos?: { x: number; y: number }
    shootDspRange?: boolean
    seconds?: number
    area?: number
    /** X/B 를 누르기 전에 새로 클릭한 정확한 좌표(0~100%). 결과 레코드의 posX/posY 로 저장된다. */
    pos?: { x: number; y: number }
  } = {}
): void {
  const idx = records.findIndex(r => r.id === recordId)
  if (idx < 0) return

  const rec = records[idx]!
  const wasShot = isShotAct(rec.act)

  rec.res = res
  if (opts.shootPos) {
    rec.shootPosX = opts.shootPos.x
    rec.shootPosY = opts.shootPos.y
  }
  if (opts.shootDspRange !== undefined) rec.shootDspRange = opts.shootDspRange

  if (res !== 'X' && res !== 'B') return

  if (wasShot) {
    // 슛 레코드가 곧 결과 레코드가 된다 + 직전 레코드에 write-back
    rec.act = ''
    const prev = records[idx - 1]
    if (prev) prev.res = res
    return
  }

  // 비-슛: 결과 레코드를 액트 레코드 "뒤"에 하나 더 남긴다.
  // area/좌표는 X/B 를 누르기 전에 새로 클릭한 위치(실책·블락이 일어난 지점)를 쓴다.
  // 넘어오지 않으면(레거시 재현 등) 직전 액트 레코드의 area 로 대체한다.
  records.splice(idx + 1, 0, {
    id: `rec_${++recSeq}`,
    seconds: opts.seconds ?? rec.seconds,
    act: '',
    posX: opts.pos?.x,
    posY: opts.pos?.y,
    res,
    area: opts.area ?? rec.area,
    seq: recSeq,
  })
}

// =============================================================================
// 1. 공격루트(Path) 그룹핑 + DAP 판정
//    원본: dplay_game_path_reset / _reset_calc / _reset_done / dplay_game_path_update
// =============================================================================

/** 공격루트 분류. UPP=무효, UTP=공격영역 진입, DTP=슛 포함, STP=단독슛 */
export type PathType = 'UPP' | 'UTP' | 'DTP' | 'STP'

/** 레코드별 산출 플래그. 레거시 ff_game_record 의 gr_is_* 컬럼들. */
export interface RecordFlags {
  /** 이 레코드가 속한 공격루트 id (레거시 gt_id) */
  gtId: string
  isTap: boolean //  gr_is_tmp   (구 TMP) : 액트 발생 자체
  isTapS: boolean // gr_is_tmp_s (구 TMP) : 액트가 진행/득점으로 이어짐
  isDap: boolean //  gr_is_tap   (구 TAP) : DAP 인정 여부 — 선수 입력창을 띄우는 기준
  isDapS: boolean // gr_is_tap_s (구 TAP) : DAP 성공
  isSht: boolean //  gr_is_sht   : 슈팅 여부
  isShtS: boolean // gr_is_sht_s : 유효 슈팅 여부
  isGol: boolean //  gr_is_gol   : 득점 여부
  isAst: boolean //  gr_is_ast   : 어시스트 (골로 이어진 DTA)
  isDts: boolean //  gr_is_cts   (구 CTS) : 체인 내 슈터
  isDta: boolean //  gr_is_cta   (구 CTA) : 체인 내 어시스터
  isDtm: boolean //  gr_is_ctm   (구 CTM) : 체인 내 메이커
  isDtb: boolean //  gr_is_ctb   (구 CTB) : 체인 내 빌더 (메이커 이전 전원)
  isGtm: boolean //  gr_is_gtm   : 메이커의 득점 관여
  isGtb: boolean //  gr_is_gtb   : 빌더의 득점 관여
}

/** 하나의 공격루트 요약. 레거시 ff_game_path 1행. */
export interface AttackPath {
  gtId: string
  recordIds: string[]
  ptype: PathType
  /** gt_csp (구 CSP) : DTP 중 유효슈팅을 포함하는 건 */
  dsp: boolean
  /** gt_ttp : UTP 또는 DTP ("유효 공격" 총합) */
  ttp: boolean
  /** gt_res_code : 루트 마지막 레코드의 결과 */
  resCode: ResCode
}

export interface PathComputeResult {
  paths: AttackPath[]
  /** 레코드 id → 플래그 */
  flags: Map<string, RecordFlags>
}

function emptyFlags(gtId: string): RecordFlags {
  return {
    gtId,
    isTap: false, isTapS: false, isDap: false, isDapS: false,
    isSht: false, isShtS: false, isGol: false,
    isAst: false, isDts: false, isDta: false, isDtm: false,
    isDtb: false, isGtm: false, isGtb: false,
  }
}

let gtSeq = 0
function nextGtId() {
  return `path_${++gtSeq}`
}

/**
 * 레코드를 시간순으로 훑으며 공격루트 단위로 자른다.
 * 원본: dplay_game_path_reset_calc / dplay_game_path_reset_done
 *
 * 종료 조건:
 *   1) 진행중인 루트가 있고 직전 레코드와 4초 초과 공백
 *      → 즉시 마감하고 현재 레코드부터 새 루트 시작
 *   2) 보정된 res 가 종결형(!= 'O')
 *      → 현재 레코드까지 포함해 마감
 *
 * ※ 2) 는 보정된 res 를 쓰므로 C/P/K/F 는 res 가 X/B 여도 루트를 끊지 않는다.
 *   자세한 이유는 파일 상단 [lookahead 보정] 참고.
 */
function splitIntoChains(records: DidRecord[], closeTrailing: boolean): DidRecord[][] {
  const chains: DidRecord[][] = []
  let current: DidRecord[] = []

  for (let i = 0; i < records.length; i++) {
    const r = records[i]!
    const last = current[current.length - 1]

    // 4초 초과 공백 → 강제 마감 후 새 루트 시작
    // (레거시도 진행중($g_path_mking)일 때만 검사하므로 current 가 비어있으면 건너뛴다)
    if (last && r.seconds - last.seconds > 4) {
      chains.push(current)
      current = []
    }

    current.push(r)

    // 결과가 나왔으면(res != 'O') 루트 종료. 단말 calcMadenPath 와 동일:
    //   if (isEmptyString(resCode) || resCode.equals("O")) return false;  // 진행중
    //   Log.d("## PATH ##", "DONE : Res = " + resCode);                   // 종료
    if (r.res !== 'O') {
      chains.push(current)
      current = []
    }
  }

  // 종료되지 못하고 남은 진행중 루트 처리.
  // 레거시 dplay_game_path_reset 은 "마지막 남은 레코드 강제 실행"으로 마감하는데,
  // 그건 경기가 끝난 뒤 전체를 다시 계산하는 배치이기 때문이다.
  // 실시간 입력 중에는 아직 끝나지 않은 루트를 마감하면 안 된다 — 마감해버리면
  // 아직 진행중인 레코드에까지 DAP 가 붙어 선수 입력 버튼이 미리 떠버린다.
  if (current.length > 0 && closeTrailing) chains.push(current)

  return chains
}

/**
 * 공격루트 하나를 분류하고 레코드별 플래그를 산출한다.
 * 원본: dplay_game_path_update — 여기서는 보정하지 않은 원본 res 를 쓴다.
 */
function classifyChain(chain: DidRecord[]): { path: AttackPath; flags: Map<string, RecordFlags> } {
  const gtId = nextGtId()
  const flags = new Map<string, RecordFlags>()
  for (const r of chain) flags.set(r.id, emptyFlags(gtId))

  let ptype: PathType = 'UPP'
  let isGoalChain = false // 레거시 gtype == "GOL"
  let dsp = false //         레거시 stype == "CSP"
  let count = 0 //           실제 공격 카운트 (act 없는 레코드는 제외)
  let utpBeginId: string | null = null

  // ---- 1차 패스: 루트 전체를 보고 ptype / dsp / UTP 진입지점 판정 ----
  for (const r of chain) {
    // 자살골은 분기 (TAP, DAP, DTP, Shoot 집계에서 제외) → act 가 없는 것처럼 취급
    const isOwnGoal = isGoal(r.res) && r.playerId === 'OWN'
    const act = isOwnGoal ? '' : r.act

    if (act) count++

    if (r.isShot || (act && isShotAct(act))) {
      // 슛이 있으면 전/후방 무관하게 DTP
      ptype = 'DTP'
      if (isGoal(r.res)) isGoalChain = true

      // DSP: 유효슈팅(득점 / 유효방향 L·H·R / 슛 좌표가 찍힌 블락)을 포함하는지
      const hasShootPos = (r.shootPosX ?? 0) > 0 || (r.shootPosY ?? 0) > 0
      if (isGoal(r.res) || r.res === 'R' || r.res === 'L' || r.res === 'H' ||
        (r.res === 'B' && hasShootPos) || r.shootDspRange) {
        dsp = true
      }
    } else if (r.area < 7 && ptype === 'UPP') {
      // 공격영역(구역 1~6) 진입 → UTP 로 승격하고 진입지점을 기록
      ptype = 'UTP'
      utpBeginId = r.id
    }
  }

  // 단독슛(빌드업 없이 바로 슛)이면 STP 로 재분류
  if (ptype === 'DTP' && count < 2) ptype = 'STP'

  // UTP 는 포인트 최소 2개 이상만 인정.
  // 예외: 레코드가 1개여도 K(코너킥)/F(프리킥)이면 인정 (레거시 2021.12.13 추가)
  if (ptype === 'UTP' && count < 2 && chain[0]?.act !== 'K' && chain[0]?.act !== 'F') {
    ptype = 'UPP'
  }

  // ---- 2차 패스: 유효 루트만, 역순으로 DAP 여부와 선수 역할 산출 ----
  if (ptype === 'DTP' || ptype === 'STP' || ptype === 'UTP') {
    let pidS: string | false = false // 슈터
    let pidA: string | false = false // 어시스터
    let pidM: string | false = false // 메이커
    let pidB: string | false = false // 빌더
    let utpN = -1

    for (let i = chain.length - 1; i >= 0; i--) {
      const r = chain[i]!
      const f = flags.get(r.id)!
      const isOwnGoal = isGoal(r.res) && r.playerId === 'OWN'
      const act = isOwnGoal ? '' : r.act

      if (ptype === 'DTP' || ptype === 'STP') {
        // DTP, STP 의 레코드는 무조건 DAP 인정
        f.isDap = !!act

        if (!pidS && act && isShotAct(act)) {
          if (r.playerId) { pidS = r.playerId; f.isDts = true }
        } else if (!pidA && pidS && pidS !== r.playerId) {
          if (r.playerId) { pidA = r.playerId; f.isDta = true; f.isAst = isGoalChain }
        } else if (!pidM && pidA && pidA !== r.playerId) {
          if (r.playerId) { pidM = r.playerId; f.isDtm = true; f.isGtm = isGoalChain }
        } else if (pidM && ((!pidB && pidM !== r.playerId) || (pidB && pidB !== r.playerId))) {
          // 레거시도 빌더는 p_id 존재 여부를 검사하지 않고 그대로 대입한다
          pidB = r.playerId ?? ''
          f.isDtb = true
          f.isGtb = isGoalChain
        }
      } else {
        // UTP: 공격영역 최초 진입 시점 이후는 전부 인정하고,
        //      진입 이전(시간상 앞쪽)은 2개까지만 DAP 로 인정한다.
        if (utpN < 0) {
          f.isDap = !!act
        } else {
          utpN++
          if (utpN <= 2) f.isDap = !!act
        }
        if (r.id === utpBeginId) utpN = 0
      }

      f.isDapS = f.isDap && (r.res === 'O' || isGoal(r.res))
    }
  }

  // ---- 공통 플래그 (TAP / SHT / GOL) ----
  for (const r of chain) {
    const f = flags.get(r.id)!
    const { act, res } = r
    const hasShootPos = (r.shootPosX ?? 0) > 0 || (r.shootPosY ?? 0) > 0

    f.isTap = !!act
    f.isTapS = !!act && (res === 'O' || isGoal(res))
    f.isSht = isShotAct(act) || !!r.isShot
    f.isShtS = (isShotAct(act) || !!r.isShot) &&
      (isGoal(res) || res === 'R' || res === 'L' || res === 'H' ||
        (res === 'B' && hasShootPos) || !!r.shootDspRange)
    f.isGol = isGoal(res)
  }

  const last = chain[chain.length - 1]

  return {
    path: {
      gtId,
      recordIds: chain.map(r => r.id),
      ptype,
      dsp,
      ttp: ptype === 'UTP' || ptype === 'DTP',
      resCode: last ? last.res : '',
    },
    flags,
  }
}

/**
 * 한 half 분량의 레코드로 전체 공격루트를 재계산한다.
 * 레거시는 배치(dplay_game_path_reset)로 돌렸지만, 여기서는 레코드가 추가/수정될 때마다
 * 다시 호출하는 방식으로 쓴다 (레코드 수가 적어 전체 재계산이 저렴하다).
 *
 * @param opts.closeTrailing
 *   아직 끝나지 않은 마지막 루트까지 마감할지 여부.
 *   · true  (기본) — 레거시 배치와 동일. 전/후반 종료 후 전체 재계산할 때 쓴다.
 *   · false        — 실시간 입력 중. 루트가 실제로 끊겨야(X/B/슛 결과, 4초 규칙)
 *                    DAP 가 확정되고 선수 입력 버튼이 뜬다.
 */
export function computeAttackPaths(
  records: DidRecord[],
  opts: { closeTrailing?: boolean } = {}
): PathComputeResult {
  const chains = splitIntoChains(resolvedOnly(records), opts.closeTrailing ?? true)

  const paths: AttackPath[] = []
  const flags = new Map<string, RecordFlags>()

  for (const chain of chains) {
    const { path, flags: chainFlags } = classifyChain(chain)
    paths.push(path)
    for (const [id, f] of chainFlags) flags.set(id, f)
  }

  return { paths, flags }
}

// =============================================================================
// 2. BAP 카운트 (공격 시도/빌드업 횟수 — DAP 와 무관한 별도 카운터)
//    원본: dplay_game_bap_reset / dplay_game_bap_reset_calc
// =============================================================================

export interface BapEvent {
  recordId: string
  /** 레거시 desc 문자열 그대로 (디버깅/검증용) */
  reason: string
}

/**
 * 한 half 분량의 레코드로 BAP 이벤트를 산출한다.
 * Ready / Defer 상태머신이며 조건은 레거시와 1:1 동일:
 *
 *   [Ready 상태]
 *     · 직전 레코드와 4초 초과 공백        → UP "4 SECONDS"
 *     · 결과가 종결형으로 마무리            → UP "DONE"
 *     · 공격영역(1~6) 진입                 → UP "ATTACK"
 *   [Ready 아님]
 *     · 시작 직후 바로 공격 진영(area<10)   → READY
 *     · 후방(area>9)에서 슛                → 결과까지 났으면 UP "SHOOT", 아니면 READY
 *     · 중앙선 돌파(area_last>9 → <10)     → X로 넘어온 건 보류(defer),
 *                                            B로 마무리면 UP "CROSS B",
 *                                            공격영역까지면 UP "CROSS ATTACK",
 *                                            중앙선만 넘었으면 READY
 *     · 보류 상태에서 전방 포인트 발생       → 공격영역이면 UP "KEEP", 아니면 READY
 *
 * res 는 splitIntoChains 와 동일하게 lookahead 보정된 값을 쓴다.
 */
export function computeBap(records: DidRecord[]): BapEvent[] {
  const list = resolvedOnly(records)
  const events: BapEvent[] = []

  let ready = false // 단말 mBapReady
  let keep = false //  단말 mBapKeep
  let lastR: DidRecord | null = null

  for (let i = 0; i < list.length; i++) {
    const currR = list[i]!
    const { act, res, area: areaCurr } = currR
    const areaLast = lastR ? lastR.area : 0

    let reason: string | null = null

    if (ready) {
      // 단말은 여기서 else-if 가 아니라 if 3개를 순서대로 검사한다.
      // 마지막으로 걸린 조건이 최종 사유가 된다.
      if (lastR && currR.seconds - lastR.seconds > 4) reason = '4 SECONDS'
      if (res && res !== 'O') reason = 'DONE'
      if (areaCurr < 7) reason = 'ATTACK'
    } else if (i < 1 && areaCurr < 10) {
      // 단말 조건: mRecordList.getChildCount() < 2 (현재 레코드가 첫 레코드)
      ready = true // READY : BEGIN
    } else if (areaCurr > 9 && isShotAct(act) && res === 'O') {
      ready = true // READY : SHOOT
    } else if (areaLast > 9 && areaCurr < 10) {
      // 중앙선 돌파. ※ 서버판과 달리 단말에는 empty(act) 검사가 없다.
      if (res === 'X') keep = true
      else if (areaCurr < 7 || res === 'B') reason = 'CROSS'
      else ready = true // READY
    } else if (!keep || areaCurr >= 10) {
      // 아무것도 하지 않음
    } else if (areaCurr < 7) {
      reason = 'KEEP'
    } else {
      ready = true // READY : KEEP
    }

    if (reason) {
      events.push({ recordId: currR.id, reason })
      ready = false
      keep = false
    } else if (ready) {
      keep = false
    }

    lastR = currR
  }

  return events
}
