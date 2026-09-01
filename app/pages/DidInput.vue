<script setup lang="ts">
import {
  applyResult,
  computeAttackPaths,
  computeBap,
  createActRecord,
  type ActCode,
  type DidRecord,
  type ResCode,
} from '~/utils/didLogic'
import { AWAY_SQUAD, HOME_SQUAD, type SquadPlayer } from '~/utils/squads'
import {
  GRASS_LINE_OPTIONS,
  GRASS_PATTERNS,
  grassBackground,
  type GrassLines,
  type GrassPattern,
} from '~/utils/grass'
import { GOAL_TARGET, goalFramePoint, goalOuterPoint, isWithinGoalOneMeter } from '~/utils/goalCoordinates'

const route = useRoute()
const home = computed(() => String(route.query.home ?? route.query.homeName ?? 'Vallecano').trim() || 'Vallecano')
const away = computed(() => String(route.query.away ?? route.query.awayName ?? route.query.opponent ?? 'Real Madrid').trim() || 'Real Madrid')
const gi_part = computed(() => (route.query.side === 'right' ? 'R' : 'L')) // TeamSelection의 진영선택 값
const inputMode = computed(() => (route.query.mode === '실시간' ? '실시간' : '분석')) // 실시간=정지불가, 분석(디폴트)=정지가능

// TeamSelection(대기 화면)과 공유하는 상태. 전반/후반 종료 시 여기에 저장하고
// TeamSelection 으로 돌아가며, "수정"/"후반전 시작"으로 다시 들어올 때 이어서 불러온다.
const game = useMatchState()
const resumeHalf = route.query.resumeHalf === '후반' ? '후반' : route.query.resumeHalf === '전반' ? '전반' : null

const homeScore = ref(game.value.homeScore)
const awayScore = ref(game.value.awayScore)
const half = ref<'전반' | '후반'>(resumeHalf ?? '전반')
const seconds = ref(0)
const paused = ref(false)
const clock = computed(() => {
  const m = String(Math.floor(seconds.value / 60)).padStart(2, '0')
  const s = String(seconds.value % 60).padStart(2, '0')
  return `${m}:${s}`
})
let timer: ReturnType<typeof setInterval> | undefined
function startTicking() {
  if (timer) clearInterval(timer)
  timer = setInterval(() => { seconds.value++ }, 1000)
}
onMounted(() => { startTicking() })
onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (flashTimer) clearTimeout(flashTimer)
})

function togglePause() {
  if (inputMode.value === '실시간') return // 실시간 모드는 정지 불가
  paused.value = !paused.value
  if (paused.value) {
    if (timer) clearInterval(timer)
  } else {
    startTicking()
  }
}

// 레코드는 시간순(오래된 것 → 최신)으로 보관한다. 표시할 때만 뒤집는다.
// 플레이 하나 = 레코드 하나이며, 결과(X/B/골존)는 새 레코드가 아니라
// 직전 레코드의 res 를 덮어쓴다. 근거는 docs/03_kpi_terminology.md 참고.
// 이어서 입력/수정하는 경우(resumeHalf) 저장해둔 기록을 그대로 불러온다.
const records = ref<DidRecord[]>(resumeHalf ? [...game.value.records] : [])

// 입력 중인 팀. TeamSelection 에서 team 쿼리로 넘어온다.
const team = computed(() => (route.query.team === 'away' ? 'away' : 'home'))
const squad = computed<SquadPlayer[]>(() => (team.value === 'away' ? AWAY_SQUAD : HOME_SQUAD))

// 진행 중인 루트도 매번 판정한다. 그래야 DAP 존(구역 1~6)에 찍는 순간
// 그 루트가 UTP 로 확정되어 진입 레코드 + 직전 2개에 선수 입력 버튼이 바로 뜬다.
// (DAP 존에 못 들어갔고 슛도 없으면 여전히 UPP 라 뜨지 않는다)
const analysis = computed(() => computeAttackPaths(records.value, { closeTrailing: true }))
const bapCount = computed(() => computeBap(records.value).length)

function fmtTime(sec: number) {
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
}

// 표시용 행. 시간순 그대로 — 가장 최근 기록이 맨 아래에 온다.
const rows = computed(() => {
  const flags = analysis.value.flags
  return records.value
    .map((r, i) => ({
      id: r.id,
      no: i + 1,
      time: fmtTime(r.seconds),
      act: r.act,
      result: r.res, // 성공이면 'O' 를 그대로 보여준다
      area: String(r.area),
      // DAP 로 인정된 레코드에만 선수를 입력한다.
      // 레거시 조건과 동일 — `is_tap == 1 && act 있음` (APK DPlayInputFragment:1661).
      //
      // X/B 로 끝난 액트 레코드(P|B, P|X)도 액트는 성립하므로 대상이 된다. 단독인
      // 경우는 레거시 규칙(UTP 는 포인트 2개 이상)에 의해 UPP 로 강등되어 자동 제외되고,
      // 뒤따라 생기는 act 없는 결과 레코드도 isDap = !!act 규칙으로 자동 제외된다.
      isDap: flags.get(r.id)?.isDap ?? false,
      playerName: squad.value.find(p => p.no === r.playerId)?.name ?? '',
    }))
})

// 최신 기록이 맨 아래에 쌓이므로, 기록이 늘면 표를 아래로 붙여준다.
const tableEl = ref<HTMLElement | null>(null)
watch(() => records.value.length, async () => {
  await nextTick()
  if (tableEl.value) tableEl.value.scrollTop = tableEl.value.scrollHeight
})

// ---- 기록 보기(짧은 클릭) / 수정(길게 눌러서 진입) ----
// PPT 슬라이드 26-27: 수정할 데이터를 길게 클릭 → 시간 수정 → 적용/삭제/취소.
// 짧게 클릭하면 그 레코드의 위치를 경기장에 잠깐 보여주기만 한다(수정 아님).
const peekId = ref<string | null>(null)
const editingId = ref<string | null>(null)
const editSeconds = ref(0)
// 수정 중인 레코드의 액트/위치 "임시값". 적용을 눌러야 실제 레코드에 반영된다.
// 수정 모드에서는 경기장 클릭·액트 클릭이 새 레코드를 만들지 않고 이 값만 바꾼다.
const editAct = ref<ActCode>('')
const editPos = ref<{ x: number; y: number } | null>(null)
let pressTimer: ReturnType<typeof setTimeout> | undefined
let longPressed = false

function startPress(id: string) {
  cancelPress()
  longPressed = false
  pressTimer = setTimeout(() => { longPressed = true; openEdit(id) }, 500)
}
function cancelPress() {
  if (pressTimer) clearTimeout(pressTimer)
  pressTimer = undefined
}
/** mouseup/touchend 에서 호출. 길게 눌러서 이미 수정모드로 들어간 경우가 아니면 "짧은 클릭"으로 본다. */
function endPress(id: string) {
  cancelPress()
  if (!longPressed) clickRecord(id)
}
function clickRecord(id: string) {
  peekId.value = peekId.value === id ? null : id
}
function openEdit(id: string) {
  peekId.value = null
  const rec = records.value.find(r => r.id === id)
  if (!rec) return
  editingId.value = id
  editSeconds.value = rec.seconds
  editAct.value = rec.act
  editPos.value = rec.posX !== undefined && rec.posY !== undefined ? { x: rec.posX, y: rec.posY } : null
}
function stepEditSeconds(delta: number) {
  editSeconds.value = Math.max(0, editSeconds.value + delta)
}
function applyEdit() {
  const rec = records.value.find(r => r.id === editingId.value)
  if (rec) {
    rec.seconds = editSeconds.value
    rec.act = editAct.value
    if (editPos.value) {
      rec.posX = editPos.value.x
      rec.posY = editPos.value.y
      rec.area = Number(areaFromPos(editPos.value))
    }
  }
  editingId.value = null
}
function deleteEdit() {
  if (!editingId.value) return
  if (!confirm('이 기록을 삭제하시겠습니까?')) return
  records.value = records.value.filter(r => r.id !== editingId.value)
  editingId.value = null
}
function cancelEdit() {
  editingId.value = null
}

// ---- 잔디 (레거시 APK 이미지 7종과 동일한 조합) ----
// TeamSelection 에서 고른 값을 쿼리로 받는다. 없으면 레거시 기본값 p110
// (패턴1 = 연두 시작, 10줄)을 쓴다.
const queryGrass = Number(route.query.grass)
const queryLines = Number(route.query.lines)
const grassPattern = ref<GrassPattern>(
  [0, 1, 2].includes(queryGrass) ? (queryGrass as GrassPattern) : 1
)
const grassLines = ref<GrassLines>(
  [9, 10, 11].includes(queryLines) ? (queryLines as GrassLines) : 10
)
const grassOpen = ref(false)
const grassBg = computed(() => grassBackground(grassPattern.value, grassLines.value))

const pendingPos = ref<{ x: number; y: number } | null>(null)
// 슛(S/H/R)은 결과(HX/LX/RX/H/L/R, GB/GOAL/GX)가 정해지기 전엔 레코드를 만들지 않는다 —
// res:'O' 로 남는 슛은 존재하지 않는다. 골 존 결과가 나올 때까지는 여기 초안으로만 들고 있는다.
interface PendingShot {
  act: Exclude<ActCode, ''>
  seconds: number
  area: number
  posX?: number
  posY?: number
}
const pendingShot = ref<PendingShot | null>(null)
const playerPickFor = ref<string | null>(null) // 선수 입력창을 띄운 레코드
const pickedNo = ref<string | null>(null) // 선수선택 화면에서 고른 등번호 (Submit 전)

// 선발 라인업. TeamSelection 에서 넘겨받는 구조가 아직 없어 임시로 스쿼드 앞에서 채운다.
// 좌표는 4-2-3-1 기준이며, 실제 포메이션 연동 시 이 부분만 교체하면 된다.
const LINEUP_SLOTS = [
  { x: 50, y: 8 },
  { x: 20, y: 26 }, { x: 50, y: 26 }, { x: 80, y: 26 },
  { x: 33, y: 45 }, { x: 67, y: 45 },
  { x: 14, y: 64 }, { x: 38, y: 64 }, { x: 62, y: 64 }, { x: 86, y: 64 },
  { x: 50, y: 85 },
]
const lineup = computed(() => {
  const byPos = (pos: SquadPlayer['pos'], n: number) =>
    squad.value.filter(p => p.pos === pos).slice(0, n)
  const mf = byPos('MF', 5)
  const ordered: SquadPlayer[] = [
    ...byPos('FW', 1), // 최전방
    ...mf.slice(0, 3), // 2선
    ...mf.slice(3, 5), // 중앙
    ...byPos('DF', 4), // 수비
    ...byPos('GK', 1), // GK
  ]
  return ordered.map((p, i) => ({ ...p, slot: LINEUP_SLOTS[i] ?? { x: 50, y: 50 } }))
})

// 버튼은 항상 활성 상태로 둔다. 블락/실책이 언제 나올지 알 수 없기 때문에
// 상황에 따라 흐려지면 안 된다. 유효하지 않은 클릭은 각 핸들러에서 무시한다.
const flashCell = ref<{ col: number; row: number } | null>(null)
let flashTimer: ReturnType<typeof setTimeout> | undefined

const kickActs = [
  { k: 'C', label: 'Cross' },
  { k: 'P', label: 'Pass' },
]
const kickActs2 = [
  { k: 'K', label: 'Corner Kick' },
  { k: 'F', label: 'Free Kick' },
]
const kickResultActs = [
  { k: 'X', label: 'Failure' },
  { k: 'B', label: 'Blocking' },
]

// 아무것도 보고 있지 않을 때 기본으로 보여줄 레코드 = 시간순으로 진짜 마지막 레코드.
// (act 없는 결과 전용 레코드를 건너뛰고 이전 액트로 되돌아가면 안 된다 —
//  예: P 가 B 로 막히면 배열 끝은 {act:'', res:'B'} 이고, 그게 "가장 최근 상태"다.
//  그러면 P 는 더 이상 채워지지 않고 B 만 채워져야 한다.)
const lastRecord = computed(() => records.value[records.value.length - 1] ?? null)
const shootActs = [
  { k: 'S', label: 'Shooting' },
  { k: 'H', label: 'Heading' },
  { k: 'R', label: 'Free kick' },
]
const ACT_LABELS: Record<string, string> = Object.fromEntries(
  [...kickActs, ...kickActs2, ...shootActs].map(a => [a.k, a.label])
)

// 03.areacode.sql 스펙: 원본 캔버스 971x634, 가로 6칸 x 세로 3칸 = 18구역.
// gi_part(진영선택 L/R) + 현재 half(전/후반)로 dir(L/R)이 정해지고, dir에 따라 번호가 반전된다.
const COL_BOUNDS = [0, 152, 318, 484, 651, 818, 971] // 6칸 경계 (971 기준)
const ROW_BOUNDS = [0, 180, 451, 634] // 3칸 경계 (634 기준, 위→아래)
const COL_PCT = COL_BOUNDS.map(v => (v / 971) * 100)
const ROW_PCT = ROW_BOUNDS.map(v => (v / 634) * 100)

const pendingCell = ref<{ col: number; row: number } | null>(null)
const cellRect = computed(() => {
  if (!flashCell.value) return null
  const { col, row } = flashCell.value
  return {
    left: COL_PCT[col] + '%',
    width: (COL_PCT[col + 1] - COL_PCT[col]) + '%',
    top: ROW_PCT[row] + '%',
    height: (ROW_PCT[row + 1] - ROW_PCT[row]) + '%',
  }
})

// 짧게 클릭(보기)했거나 길게 눌러 수정 중인 레코드의 위치를 경기장에 계속 표시한다
// (깜빡였다 사라지지 않고 유지됨). posX/posY(클릭한 정확한 좌표)가 있으면 점으로,
// 없으면(좌표 없이 만들어진 옛 레코드) area 가 속한 구역 사각형으로 대신 보여준다.
const peekRecord = computed(() => records.value.find(r => r.id === peekId.value) ?? null)
const editingRecord = computed(() => records.value.find(r => r.id === editingId.value) ?? null)
const infoRecord = computed(() => editingRecord.value ?? peekRecord.value)

// Kick/Shooting 패널에 "채워서" 보여줄 레코드. 레코드를 클릭/롱프레스해서 보고 있는
// 중이면 그 레코드를, 아무것도 안 보고 있으면 진짜 마지막 레코드를 기준으로 삼는다.
const displayRecord = computed(() => infoRecord.value ?? lastRecord.value)

// 위 레코드의 act 를 채워서 보여준다. act 가 없는 레코드(결과 전용)면 아무 액트도
// 채우지 않는다 — 예를 들어 P 가 B 로 막힌 직후엔 P 를 더 이상 채우지 않는다.
// 수정 중일 때는 저장된 값이 아니라 아직 적용 전인 임시값(editAct)을 보여준다.
const activeAct = computed(() => (
  editingId.value ? editAct.value : pendingShot.value ? pendingShot.value.act : displayRecord.value?.act
) || null)

// 위 레코드의 결과가 X/B 면 그 결과 버튼도 "선택됨"으로 보여준다.
// 하나의 레코드에 act 와 res(X/B) 가 함께 있는 경우(예: 표에서 "P|B" 한 줄로 보이는
// 레코드를 직접 클릭해서 보는 중) 는 activeAct 와 동시에 채워진다 — 의도된 동작이다.
const activeResult = computed(() => {
  const res = displayRecord.value?.res
  return res === 'X' || res === 'B' ? res : null
})

// 수정 중일 때는 저장된 좌표가 아니라 아직 적용 전인 임시 위치(editPos)를 보여준다.
const displayPos = computed(() => {
  if (editingId.value) return editPos.value
  const r = infoRecord.value
  return r && r.posX !== undefined && r.posY !== undefined ? { x: r.posX, y: r.posY } : null
})
const infoMarkerPos = computed(() => (
  displayPos.value ? { left: displayPos.value.x + '%', top: displayPos.value.y + '%' } : null
))
const infoCellRect = computed(() => {
  if (displayPos.value) return null // 정확한 좌표가 있으면 사각형 대신 점으로 보여준다
  const r = editingId.value ? editingRecord.value : infoRecord.value
  if (!r) return null
  const { col, row } = cellFromArea(r.area)
  return {
    left: COL_PCT[col] + '%',
    width: (COL_PCT[col + 1] - COL_PCT[col]) + '%',
    top: ROW_PCT[row] + '%',
    height: (ROW_PCT[row + 1] - ROW_PCT[row]) + '%',
  }
})

function cellFromPos(pos: { x: number; y: number }) {
  const px = (pos.x / 100) * 971
  const py = (pos.y / 100) * 634
  let col = COL_BOUNDS.length - 2
  for (let i = 0; i < COL_BOUNDS.length - 1; i++) {
    if (px < COL_BOUNDS[i + 1]) { col = i; break }
  }
  let row = ROW_BOUNDS.length - 2
  for (let i = 0; i < ROW_BOUNDS.length - 1; i++) {
    if (py < ROW_BOUNDS[i + 1]) { row = i; break }
  }
  return { col, row }
}

function clickPitch(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  const x = ((e.clientX - rect.left) / rect.width) * 100
  const y = ((e.clientY - rect.top) / rect.height) * 100

  // 수정 중이면 새 레코드를 만들지 않고, 지금 수정 중인 레코드의 위치만 바꾼다.
  if (editingId.value) {
    editPos.value = { x, y }
    return
  }

  // 경기장을 새로 찍으면 보고 있던(peek) 레코드의 위치/액트 하이라이트는 지운다 —
  // 이제부터는 새 입력을 하는 것이므로 옛 레코드 표시가 계속 남아있으면 안 된다.
  peekId.value = null
  // 결과를 고르지 않은 채 새 위치를 찍으면 대기 중이던 슛 초안은 버려진다(레코드로 남지 않았으므로).
  pendingShot.value = null

  const nextCell = cellFromPos({ x, y })
  pendingPos.value = { x, y }
  pendingCell.value = nextCell
  flashCell.value = nextCell
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flashCell.value = null }, 420)
}

/** area 코드(1~18) → 경기장 셀(col/row). areaFromPos 의 역변환. 기록 수정 화면에서 위치를 보여줄 때 쓴다. */
function cellFromArea(area: number) {
  const isFirstHalf = half.value === '전반'
  const dir = (gi_part.value === 'L') === isFirstHalf ? 'L' : 'R'
  const zoneR = dir === 'L' ? 19 - area : area
  const col = Math.floor((zoneR - 1) / 3)
  const rowOffsetR = zoneR - col * 3 // 1=bottom, 2=mid, 3=top
  const row = rowOffsetR === 1 ? 2 : rowOffsetR === 2 ? 1 : 0
  return { col, row }
}

function areaFromPos(pos: { x: number; y: number }) {
  const { col, row } = cellFromPos(pos)
  // row: 0=top, 1=mid, 2=bottom. R 기준 번호 = col*3 + (row가 bottom=1,mid=2,top=3 순서)
  const rowOffsetR = row === 2 ? 1 : row === 1 ? 2 : 3
  const zoneR = col * 3 + rowOffsetR

  // 전반(H1/H3 개념)이면 gi_part 그대로, 후반(H2/H4)이면 반대쪽으로 스위치
  const isFirstHalf = half.value === '전반'
  const dir = (gi_part.value === 'L') === isFirstHalf ? 'L' : 'R'
  const zone = dir === 'L' ? 19 - zoneR : zoneR
  return String(zone)
}

// 액트 버튼(C/P/K/F/S/H/R): 새 레코드를 만든다. res 는 'O'(진행중)로 시작.
// 단, 슛(S/H/R)은 예외다 — 슛은 반드시 방향/골키퍼 결과가 있어야 하고 res:'O' 로
// 남는 슛은 없으므로, 결과가 정해질 때까지 레코드를 만들지 않고 초안(pendingShot)으로만 둔다.
function clickAct(actKey: string, isShot: boolean) {
  // 수정 중이면 새 레코드를 만들지 않고, 지금 수정 중인 레코드의 액트만 바꾼다.
  if (editingId.value) {
    editAct.value = actKey as Exclude<ActCode, ''>
    return
  }
  if (!pendingPos.value) return
  if (isShot) {
    pendingShot.value = {
      act: actKey as Exclude<ActCode, ''>,
      seconds: seconds.value,
      area: Number(areaFromPos(pendingPos.value)),
      posX: pendingPos.value.x,
      posY: pendingPos.value.y,
    }
    pendingPos.value = null
    pendingCell.value = null
    flashCell.value = null
    return
  }
  const rec = createActRecord(
    actKey as Exclude<ActCode, ''>,
    seconds.value,
    Number(areaFromPos(pendingPos.value)),
    { posX: pendingPos.value.x, posY: pendingPos.value.y }
  )
  records.value.push(rec)
  pendingPos.value = null
  pendingCell.value = null
  flashCell.value = null
}

// X/B: C/P 와 마찬가지로 위치를 먼저 찍어야 누를 수 있다 (실책·블락이 일어난 지점).
// 직전 액트 레코드에 결과를 기록하고, 그 위치를 area 로 하는 결과 레코드를 하나 더 남긴다.
function clickResult(res: 'X' | 'B') {
  if (!pendingPos.value) return
  const last = records.value[records.value.length - 1]
  if (!last || last.res !== 'O') return
  applyResult(records.value, last.id, res, {
    seconds: seconds.value,
    area: Number(areaFromPos(pendingPos.value)),
    pos: { x: pendingPos.value.x, y: pendingPos.value.y },
  })
  pendingPos.value = null
  pendingCell.value = null
  flashCell.value = null
}

// 골대 존: 결과가 정해진 순간에야 비로소 슛 레코드를 만든다(act+res 를 함께 채워서 push) —
// 그전까지는 records 배열에 아무것도 남기지 않는다.
function recordGoalResult(zone: Exclude<ResCode, 'O' | ''>, point?: { x: number; y: number }) {
  if (!pendingShot.value) return
  const shot = pendingShot.value
  const rec = createActRecord(shot.act, shot.seconds, shot.area, { posX: shot.posX, posY: shot.posY })
  rec.res = zone
  if (point) {
    rec.shootPosX = point.x
    rec.shootPosY = point.y
    rec.shootDspRange = isWithinGoalOneMeter(point)
  }
  records.value.push(rec)
  if (zone === 'GOAL') homeScore.value++
  pendingShot.value = null
}

// 골대 UI(.goal 래퍼) 안에서 골문 프레임(.goalFrame)이 차지하는 비율.
// CSS 의 .goalFrame{left:15%;right:15%;top:42%;bottom:0} 과 반드시 같이 맞춰야 한다.
const FRAME_TOP = 0.42
const FRAME_SIDE = 0.15
// "포스트·크로스바 바깥 1m" 경계선 = 프레임 자체 크기(7.32m×2.44m) 기준으로 1m가
// 래퍼 대비 몇 %인지 계산해서 프레임 경계에서 빼준다. 손으로 어림잡은 값이 아니라
// GOAL_TARGET(실측 규격)에서 그대로 유도한 값이다 — CSS 의 .meterGuide/.hxDivider 도 같이 맞춰야 한다.
const FRAME_WIDTH_FRAC = 1 - FRAME_SIDE * 2 // 0.70
const FRAME_HEIGHT_FRAC = 1 - FRAME_TOP // 0.58
const GUIDE_SIDE = FRAME_SIDE - (FRAME_WIDTH_FRAC / GOAL_TARGET.meters.width) // ≈ 0.0544
const GUIDE_TOP = FRAME_TOP - (FRAME_HEIGHT_FRAC / GOAL_TARGET.meters.height) // ≈ 0.1823

// 골문 프레임 안쪽 클릭: 바로 기록하지 않고 위치 마커만 세부 조정한다.
// 확정은 아래 B/GOAL/X 버튼을 눌러야 이루어진다.
const pendingFramePos = ref<{ x: number; y: number } | null>(null)
function clickGoalFrame(e: MouseEvent) {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  pendingFramePos.value = {
    x: (e.clientX - rect.left) / rect.width,
    y: (e.clientY - rect.top) / rect.height,
  }
}
// B/GOAL/X: 프레임 안쪽에서 마커로 찍어둔 위치를 결과로 확정한다. 마커가 없으면 무시한다.
// 버튼 표시는 B/GOAL/X 그대로지만, 골키퍼 액션이므로 저장값은 GB/GX로 킥 패널의 B/X와 구분한다.
function confirmGoalFrame(result: 'B' | 'GOAL' | 'X') {
  if (!pendingFramePos.value) return
  const zone = result === 'B' ? 'GB' : result === 'X' ? 'GX' : 'GOAL'
  recordGoalResult(zone, goalFramePoint(pendingFramePos.value.x, pendingFramePos.value.y))
  pendingFramePos.value = null
}

// 중앙 정렬된 골대 주변 영역(프레임 바깥)은 지금처럼 클릭 즉시 기록한다 — 버튼 필요 없음.
// footballX의 전체 628×300 좌표로 남긴다.
function clickGoalTarget(e: MouseEvent) {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const x = (e.clientX - rect.left) / rect.width
  const y = (e.clientY - rect.top) / rect.height

  // 골문 ↔ 외곽 1m 선 사이는 방향에 따라 L/H/R(DSP), 그 선 바깥은 LX/HX/RX다.
  if (y < FRAME_TOP) {
    const point = goalOuterPoint('HX', x, y / FRAME_TOP)
    recordGoalResult(y < GUIDE_TOP ? 'HX' : 'H', point)
  } else if (x < FRAME_SIDE) {
    const point = goalOuterPoint('LX', x / FRAME_SIDE, (y - FRAME_TOP) / (1 - FRAME_TOP))
    recordGoalResult(x < GUIDE_SIDE ? 'LX' : 'L', point)
  } else if (x > 1 - FRAME_SIDE) {
    const point = goalOuterPoint('RX', (x - (1 - FRAME_SIDE)) / FRAME_SIDE, (y - FRAME_TOP) / (1 - FRAME_TOP))
    recordGoalResult(x > 1 - GUIDE_SIDE ? 'RX' : 'R', point)
  }
}

// DAP 레코드의 선수 입력
function openPlayerPick(recordId: string) {
  playerPickFor.value = recordId
  pickedNo.value = records.value.find(r => r.id === recordId)?.playerId ?? null
}
function cancelPlayerPick() {
  playerPickFor.value = null
  pickedNo.value = null
}
function submitPlayer() {
  if (!pickedNo.value) return
  const rec = records.value.find(r => r.id === playerPickFor.value)
  if (rec) rec.playerId = pickedNo.value
  cancelPlayerPick()
}

// 전반/후반 종료: 스코어·기록을 공유 상태에 저장하고 대기 화면(TeamSelection)으로
// 돌아간다. "경기를 종료할지"는 이제 이 화면이 아니라 TeamSelection 이 판단한다
// (전반/후반 모두 끝나면 그 화면에서 수정/다음 단계를 고르게 되어 있다).
function finishHalf() {
  if (!confirm(`${half.value}을 종료하시겠습니까?`)) return

  game.value.records = records.value
  game.value.homeScore = homeScore.value
  game.value.awayScore = awayScore.value
  game.value.halfStatus = half.value === '전반' ? 'H1_done' : 'H2_done'

  navigateTo({
    path: '/TeamSelection',
    query: {
      matchId: route.query.matchId,
      date: route.query.date,
      time: route.query.time,
      league: route.query.league,
      round: route.query.round,
      stadium: route.query.stadium,
      home: home.value,
      away: away.value,
    },
  })
}
</script>

<template>
  <div class="page">
    <div class="frameViewport">
      <div class="frame">
      <section class="left">
        <div class="statBar">
          <div class="cardIcon">🟨🟥</div>
          <button class="stat">-3</button>
          <button class="stat">-1</button>
          <div class="bap">BAP: {{ bapCount }}</div>
          <div class="spacer" />
          <button class="stat">+1</button>
          <button class="stat">+3</button>
          <div class="grassWrap">
            <button class="grassIcon" :class="{ on: grassOpen }" @click="grassOpen = !grassOpen">▦</button>
            <div v-if="grassOpen" class="grassPop">
              <div class="popRow">
                <span class="popLabel">잔디 패턴</span>
                <div class="popOpts">
                  <button
                    v-for="g in GRASS_PATTERNS" :key="g.value"
                    class="popBtn" :class="{ on: grassPattern === g.value }"
                    @click="grassPattern = g.value"
                  >{{ g.label }}</button>
                </div>
              </div>
              <div class="popRow">
                <span class="popLabel">잔디 라인</span>
                <div class="popOpts">
                  <button
                    v-for="n in GRASS_LINE_OPTIONS" :key="n"
                    class="popBtn" :class="{ on: grassLines === n }"
                    :disabled="grassPattern === 0"
                    @click="grassLines = n"
                  >{{ n }}줄</button>
                </div>
              </div>
              <div class="popPreview" :style="{ background: grassBg }" />
              <button class="popOk" @click="grassOpen = false">확인</button>
            </div>
          </div>
          <div class="swapIcon">⇄</div>
        </div>

        <div class="scoreBar">
          <div class="team">{{ home }}</div>
          <div class="score">{{ homeScore }}</div>
          <div class="halfBox">
            <div class="halfLabel" :class="{ on: half === '전반' }">전반</div>
            <div class="clock" :class="{ paused }">{{ clock }}</div>
            <div class="halfLabel" :class="{ on: half === '후반' }">후반</div>
            <button
              v-if="inputMode === '분석'"
              class="pauseBtn"
              :class="{ paused }"
              @click="togglePause"
            >{{ paused ? '▶' : '❚❚' }}</button>
            <div v-else class="modeTag">실시간</div>
          </div>
          <div class="score">{{ awayScore }}</div>
          <div class="team right">{{ away }}</div>
        </div>

        <div class="pitch" :style="{ background: grassBg }" @click="clickPitch">
          <div class="lineHalf" /><div class="lineCircle" />
          <div class="boxL" /><div class="arcL" /><div class="goalNetL" />
          <div class="boxR" /><div class="arcR" /><div class="goalNetR" />
          <div class="corner cornerTL" /><div class="corner cornerBL" />
          <div class="corner cornerTR" /><div class="corner cornerBR" />
          <div v-if="cellRect" class="zoneHighlight" :style="cellRect" />
          <div v-if="infoCellRect" class="zoneHighlight editZoneHighlight" :style="infoCellRect" />
          <div v-if="infoMarkerPos" class="marker editMarker" :style="infoMarkerPos" />
          <div v-if="pendingPos" class="marker" :style="{ left: pendingPos.x + '%', top: pendingPos.y + '%' }" />
        </div>

        <div ref="tableEl" class="table">
          <div class="thead">
            <span>No.</span><span>Time</span><span>Act</span><span>Result</span><span>Area</span><span>Player</span>
          </div>
          <div class="tbody">
            <div
              v-for="r in rows" :key="r.id" class="trow"
              :class="{ pending: r.result === 'O', editing: editingId === r.id, peeking: peekId === r.id }"
              @mousedown="startPress(r.id)"
              @mouseup="endPress(r.id)"
              @mouseleave="cancelPress"
              @touchstart="startPress(r.id)"
              @touchend="endPress(r.id)"
              @contextmenu.prevent
            >
              <template v-if="editingId === r.id">
                <div class="editTimeRow" @mousedown.stop @touchstart.stop>
                  <span class="editInfo"><b>{{ ACT_LABELS[editAct] ?? r.result }}</b><i>구역 {{ editPos ? areaFromPos(editPos) : r.area }}</i></span>
                  <span class="editSpacer" />
                  <button class="editStep" @click.stop="stepEditSeconds(-1)">−</button>
                  <span class="editTimeVal">{{ fmtTime(editSeconds) }}</span>
                  <button class="editStep" @click.stop="stepEditSeconds(1)">+</button>
                </div>
              </template>
              <template v-else>
                <span>{{ r.no }}</span><span>{{ r.time }}</span><span>{{ r.act }}</span><span>{{ r.result }}</span><span>{{ r.area }}</span>
                <span>
                  <template v-if="r.playerName">{{ r.playerName }}</template>
                  <button v-else-if="r.isDap" class="playerBtn" @mousedown.stop @touchstart.stop @click.stop="openPlayerPick(r.id)">Select</button>
                </span>
              </template>
            </div>
          </div>
        </div>
      </section>

      <section class="right">
        <div v-if="editingId" class="editActions">
          <button class="editApply" @click="applyEdit">적용</button>
          <button class="editDelete" @click="deleteEdit">삭제</button>
          <button class="editCancel" @click="cancelEdit">취소</button>
        </div>
        <button v-else class="finishBtn" @click="finishHalf">{{ half }} 종료</button>
        <h1>{{ playerPickFor ? '선수선택' : 'DID-INPUT' }}</h1>

        <template v-if="!playerPickFor">
        <div class="group">
          <div class="groupTitle">Kick</div>
          <div class="kickGrid">
            <button v-for="a in kickActs" :key="a.k" class="actBtn kickPrimary" :class="[`kick-${a.k.toLowerCase()}`, { on: activeAct === a.k, available: !!pendingPos && activeAct !== a.k }]" @click="clickAct(a.k, false)"><b>{{ a.k }}</b><span>{{ a.label }}</span></button>
            <div class="stacked">
              <button v-for="a in kickActs2" :key="a.k" class="actBtn small" :class="{ on: activeAct === a.k, available: !!pendingPos && activeAct !== a.k }" @click="clickAct(a.k, false)"><b>{{ a.k }}</b><span>{{ a.label }}</span></button>
            </div>
            <button v-for="a in kickResultActs" :key="a.k" class="actBtn kickResult" :class="[`kick-${a.k.toLowerCase()}`, { on: activeResult === a.k }]" @click="clickResult(a.k as 'X' | 'B')"><b>{{ a.k }}</b><span class="divider">|</span><span>{{ a.label }}</span></button>
          </div>
        </div>

        <div class="group">
          <div class="groupTitle">Shooting</div>
          <div class="shootGrid">
            <button v-for="a in shootActs" :key="a.k" class="actBtn" :class="{ on: activeAct === a.k, available: !!pendingPos && activeAct !== a.k }" @click="clickAct(a.k, true)"><b>{{ a.k }}</b><span>{{ a.label }}</span></button>
          </div>

          <div class="goal" :class="{ active: pendingShot !== null }" @click="clickGoalTarget">
            <div class="goalZone hx">HX</div>
            <div class="goalZone lx">LX</div>
            <div class="goalZone rx">RX</div>
            <div class="hxDivider" aria-hidden="true" />
            <div class="meterGuide" aria-hidden="true" />
            <div class="goalFrame" @click.stop="clickGoalFrame">
              <div class="goalZone goalCenter">GOAL</div>
              <div
                v-if="pendingFramePos"
                class="frameMarker"
                :style="{ left: pendingFramePos.x * 100 + '%', top: pendingFramePos.y * 100 + '%' }"
              />
            </div>
          </div>
          <div class="goalResultButtons">
            <button class="goalResultBtn resB" @click="confirmGoalFrame('B')">B</button>
            <button class="goalResultBtn resGoal" @click="confirmGoalFrame('GOAL')">GOAL</button>
            <button class="goalResultBtn resX" @click="confirmGoalFrame('X')">X</button>
          </div>
        </div>

        </template>

        <!-- 선수선택: PPT 대로 액트 입력창 자리에서 UI 를 전환한다 -->
        <div v-else class="group pickGroup">
          <div class="pickField">
            <button
              v-for="p in lineup"
              :key="p.no"
              class="jersey"
              :class="{ on: pickedNo === p.no }"
              :style="{ left: p.slot.x + '%', top: p.slot.y + '%' }"
              @click="pickedNo = p.no"
            >
              <span class="shirt">{{ p.no }}</span>
              <span class="jname">{{ p.name }}</span>
            </button>
          </div>
          <div class="pickActions">
            <button class="pickCancel" @click="cancelPlayerPick">Cancel</button>
            <button class="pickSubmit" :disabled="!pickedNo" @click="submitPlayer">Submit</button>
          </div>
        </div>
      </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
*{box-sizing:border-box}button{font:inherit}
.page{width:1280px;height:800px;margin:0 auto;box-sizing:border-box;padding:0;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#0b0f17}
.frameViewport{position:relative;flex:0 0 auto;width:1280px;height:800px;overflow:hidden;box-shadow:0 14px 44px rgba(0,0,0,.55)}
.frame{position:absolute;left:0;top:0;width:1280px;height:800px;display:grid;grid-template-columns:712.6641px 567.3359px;overflow:hidden;border:1px solid rgba(255,255,255,.1)}

.left{min-width:0;background:#1b1e22;display:flex;flex-direction:column}
.statBar{flex:0 0 auto;display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.08)}
.cardIcon{font-size:14px}
.stat{height:26px;padding:0 10px;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:#ddd;font-weight:800;cursor:pointer}
.bap{color:#e8e8e8;font-size:12px;font-weight:700}
.spacer{flex:1}
.grassIcon,.swapIcon{width:26px;height:26px;display:grid;place-items:center;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:#ddd;font-size:13px;padding:0;cursor:pointer}
.grassIcon.on{border-color:#f0b429;color:#f0b429;background:rgba(240,180,41,.18)}
.grassWrap{position:relative}
.grassPop{position:absolute;z-index:30;top:32px;right:0;width:250px;padding:10px;border-radius:6px;border:1px solid rgba(255,255,255,.16);background:#1b1e22;box-shadow:0 14px 40px rgba(0,0,0,.6);display:flex;flex-direction:column;gap:9px}
.popRow{display:flex;flex-direction:column;gap:5px}
.popLabel{font-size:10px;font-weight:800;color:rgba(255,255,255,.5)}
.popOpts{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}
.popBtn{height:24px;border-radius:4px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);color:#ddd;font-size:10px;font-weight:700;cursor:pointer;padding:0}
.popBtn.on{border-color:#f0b429;background:rgba(240,180,41,.2);color:#f0b429}
.popBtn:disabled{opacity:.35;cursor:not-allowed}
.popPreview{height:34px;border-radius:4px;border:1px solid rgba(255,255,255,.18)}
.popOk{height:26px;border-radius:4px;border:none;background:#f0b429;color:#191919;font-weight:800;font-size:11px;cursor:pointer}

.scoreBar{flex:0 0 auto;min-width:0;display:grid;grid-template-columns:minmax(120px,1fr) 24px auto 24px minmax(150px,1fr);align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid rgba(255,255,255,.08)}
.team{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#eee;font-weight:800;font-size:13px}
.team.right{text-align:right}
.score{color:#fff;font-weight:900;font-size:20px;min-width:20px;text-align:center}
.halfBox{display:flex;align-items:center;gap:8px;background:#111417;border:1px solid rgba(255,255,255,.1);border-radius:6px;padding:4px 10px}
.halfLabel{font-size:10px;color:rgba(255,255,255,.35);font-weight:800}
.halfLabel.on{color:#f0b429}
.clock{font-family:monospace;font-size:18px;color:#f0b429;font-weight:800}
.clock.paused{color:rgba(255,255,255,.4)}
.pauseBtn{width:22px;height:22px;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:#f0b429;font-size:10px;cursor:pointer;display:grid;place-items:center;padding:0}
.pauseBtn.paused{background:rgba(240,180,41,.2);border-color:#f0b429}
.modeTag{font-size:9px;font-weight:800;color:rgba(255,255,255,.35);letter-spacing:.05em}

.pitch{position:relative;flex:1;min-height:200px;overflow:hidden;cursor:crosshair}
.lineHalf{position:absolute;left:50%;top:0;bottom:0;width:0;border-left:2px solid rgba(255,255,255,.75)}
.lineCircle{position:absolute;left:50%;top:50%;height:27%;aspect-ratio:1;transform:translate(-50%,-50%);border:2px solid rgba(255,255,255,.78);border-radius:50%}
.boxL{position:absolute;left:0;top:19%;bottom:19%;width:15.5%;border:2px solid rgba(255,255,255,.78);border-left:none}
.arcL{position:absolute;left:8.5%;top:50%;width:14%;aspect-ratio:1;transform:translateY(-50%);border:2px solid rgba(255,255,255,.78);border-radius:50%;clip-path:inset(0 0 0 50%)}
.boxR{position:absolute;right:0;top:19%;bottom:19%;width:15.5%;border:2px solid rgba(255,255,255,.78);border-right:none}
.arcR{position:absolute;right:8.5%;top:50%;width:14%;aspect-ratio:1;transform:translateY(-50%);border:2px solid rgba(255,255,255,.78);border-radius:50%;clip-path:inset(0 50% 0 0)}
.goalNetL,.goalNetR{position:absolute;top:35%;bottom:35%;width:5%;background:transparent;border:2px solid rgba(255,255,255,.78)}
.goalNetL{left:0;border-left:none}.goalNetR{right:0;border-right:none}
.corner{position:absolute;width:22px;height:22px;border:2px solid rgba(255,255,255,.35);border-radius:50%}.cornerTL{left:-12px;top:-12px}.cornerBL{left:-12px;bottom:-12px}.cornerTR{right:-12px;top:-12px}.cornerBR{right:-12px;bottom:-12px}
.marker{position:absolute;width:14px;height:14px;margin:-7px;border-radius:50%;background:rgba(235,235,235,.72);border:2px solid rgba(255,255,255,.9);box-shadow:0 0 0 4px rgba(210,210,210,.22);pointer-events:none}
.zoneHighlight{position:absolute;background:rgba(255,255,255,.14);border:0;pointer-events:none;box-sizing:border-box;animation:zoneFlash .42s ease-out forwards}
.editZoneHighlight{background:rgba(240,180,41,.28);border:2px solid #f0b429;animation:none}
.editMarker{background:rgba(240,180,41,.85);border-color:#fff;box-shadow:0 0 0 5px rgba(240,180,41,.3);z-index:2}
@keyframes zoneFlash{0%{opacity:1}55%{opacity:.55}100%{opacity:0}}

.table{flex:0 0 auto;height:180px;overflow-y:auto;border-top:1px solid rgba(255,255,255,.08)}
.thead,.trow{display:grid;grid-template-columns:64px 96px 78px 86px 78px minmax(210px,1fr);gap:4px;padding:4px 10px}
.thead span,.trow span{min-width:0;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.thead span:last-child,.trow span:last-child{text-align:left;padding-left:18px}
.thead{background:#f0b429;color:#1a1a1a;font-weight:800;font-size:11px;position:sticky;top:0}
.trow{color:#ddd;font-size:11px;border-bottom:1px solid rgba(255,255,255,.06);cursor:pointer;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;touch-action:manipulation}
.trow.pending{color:#f0b429}
.trow.editing{background:rgba(240,180,41,.1)}
.trow.peeking{background:rgba(240,180,41,.06)}
.playerBtn{height:18px;padding:0 8px;border-radius:3px;border:1px dashed #f0b429;background:rgba(240,180,41,.1);color:#f0b429;font-size:10px;font-weight:700;cursor:pointer}
.playerBtn:hover{background:rgba(240,180,41,.25)}

.editTimeRow{grid-column:1/-1;display:flex;align-items:center;gap:10px;padding:2px 2px}
.editInfo{display:flex;align-items:baseline;gap:8px;color:#f0b429}
.editInfo b{font-size:12px;font-weight:800}
.editInfo i{font-style:normal;font-size:10px;color:rgba(255,255,255,.5)}
.editSpacer{flex:1}
.editStep{width:20px;height:20px;border-radius:4px;border:1px solid rgba(240,180,41,.5);background:rgba(240,180,41,.1);color:#f0b429;font-size:13px;font-weight:800;cursor:pointer;display:grid;place-items:center;padding:0;line-height:1}
.editStep:hover{background:rgba(240,180,41,.25)}
.editTimeVal{font-family:monospace;font-size:13px;font-weight:800;color:#f0b429;min-width:44px;text-align:center}

/* 선수선택 화면 (액트 입력창 자리에서 전환) */
.pickGroup{display:flex;flex-direction:column;gap:12px}
.pickField{position:relative;flex:1;min-height:0;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.02)}
.jersey{position:absolute;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;gap:3px;padding:4px 6px;border:1px solid transparent;border-radius:4px;background:transparent;cursor:pointer}
.jersey:hover{border-color:rgba(240,180,41,.5);background:rgba(240,180,41,.08)}
.jersey.on{border-color:#f0b429;background:rgba(240,180,41,.2)}
.shirt{width:34px;height:30px;display:grid;place-items:center;color:#1a1a1a;font-weight:900;font-size:13px;background:#c9ccd1;clip-path:polygon(0 22%,22% 0,35% 8%,65% 8%,78% 0,100% 22%,84% 38%,84% 100%,16% 100%,16% 38%)}
.jersey.on .shirt{background:#f0b429}
.jname{color:#ddd;font-size:10px;white-space:nowrap}
.pickActions{flex:0 0 auto;display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:0 6px 4px}
.pickCancel,.pickSubmit{height:34px;border-radius:4px;font-weight:800;font-size:13px;cursor:pointer;border:1px solid #f0b429;background:transparent;color:#f0b429}
.pickSubmit{border-color:rgba(255,255,255,.2);color:rgba(255,255,255,.5)}
.pickSubmit:not(:disabled){border-color:#f0b429;background:#f0b429;color:#191919}
.pickSubmit:disabled{cursor:not-allowed}
.pickCancel:hover{background:rgba(240,180,41,.15)}

.finishBtn{position:absolute;top:8px;right:8px;height:26px;padding:0 12px;border-radius:4px;border:1px solid rgba(255,255,255,.16);background:rgba(255,255,255,.06);color:#ddd;font-weight:700;font-size:11px;cursor:pointer}
.finishBtn:hover{background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.3)}

/* 전반 종료 버튼과 같은 자리(절대위치)를 그대로 쓴다 — 레이아웃이 밀리면 안 된다 */
.editActions{position:absolute;top:8px;right:8px;display:flex;gap:6px}
.editActions button{height:32px;padding:0 14px;border-radius:5px;font-weight:800;font-size:12.5px;letter-spacing:.02em;cursor:pointer;border:1px solid transparent}
.editApply{background:#f0b429;border-color:#f0b429;color:#191919}
.editApply:hover{background:#ffc84a}
.editDelete{background:rgba(217,90,90,.15);border-color:rgba(217,90,90,.5);color:#e07a7a}
.editDelete:hover{background:rgba(217,90,90,.28)}
.editCancel{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.16);color:#ddd}
.editCancel:hover{background:rgba(255,255,255,.14)}

section.right{position:relative;width:567.3359px;min-width:567.3359px;min-height:0;background:#1e2126;display:flex;flex-direction:column;padding:10px;gap:10px;overflow:hidden}
section.right h1{margin:0;text-align:center;color:#fff;font-size:20px}
.group{border:1px solid rgba(255,255,255,.1);padding:7px;background:rgba(255,255,255,.02)}
.group:last-of-type{flex:1;min-height:0;display:flex;flex-direction:column}
.groupTitle{color:#eee;font-weight:800;margin-bottom:8px;font-size:18px}

.kickGrid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));grid-template-rows:96px 66px;gap:5px}
.stacked{grid-column:5/7;grid-row:1;min-width:0;display:grid;grid-template-rows:1fr 1fr;gap:3px}
.actBtn{min-width:0;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;height:76px;border-radius:0;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.05);color:rgba(255,255,255,.45);cursor:pointer;transition:opacity .15s,background .15s,transform .1s}
.actBtn.small{height:auto;min-height:0;flex-direction:row;justify-content:flex-start;padding:0 14px;gap:12px}
.kickPrimary{height:96px}.kick-c{grid-column:1/3;grid-row:1}.kick-p{grid-column:3/5;grid-row:1}.kickResult{height:66px;flex-direction:row;gap:16px}.kick-x{grid-column:1/4;grid-row:2}.kick-b{grid-column:4/7;grid-row:2}.kickResult .divider{color:rgba(255,255,255,.2);font-size:24px}
.actBtn b{font-size:29px}
.actBtn span{font-size:14px;font-weight:700}
.stacked .actBtn span{font-size:12px}
.kickPrimary,.shootGrid .actBtn{flex-direction:row;gap:12px}
.kickPrimary b,.shootGrid .actBtn b{padding-right:12px;border-right:1px solid rgba(255,255,255,.22)}
.actBtn:disabled{opacity:.35;border-color:rgba(255,255,255,.15);color:rgba(255,255,255,.4);cursor:not-allowed}
.actBtn:not(:disabled):hover{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.32)}
.actBtn:not(:disabled):active{transform:scale(.98)}
/* 3단계: 기본(회색) → 위치를 찍으면 available(테두리+텍스트만 노란색) → 실제 사용한 것만 on(안까지 채움).
   X/B(결과)는 이 셋 중 어느 클래스도 받지 않으므로 항상 기본 회색 그대로다. */
.actBtn.available:not(:disabled){border-color:#f0b429;background:rgba(240,180,41,.08);color:#f0b429}
.actBtn.available:not(:disabled):hover{background:rgba(240,180,41,.2)}
.actBtn.on:not(:disabled){background:#f0b429;border-color:#f0b429;color:#191919}
.actBtn.on:not(:disabled):hover{background:#ffc84a}
/* X/B 는 절대 노란색이 되지 않는다 — 결과로 선택된 상태는 회색 계열로 채운다 (specificity 로 위 규칙을 덮는다) */
.actBtn.kickResult.on:not(:disabled){background:rgba(255,255,255,.35);border-color:rgba(255,255,255,.55);color:#161616}
.actBtn.kickResult.on:not(:disabled):hover{background:rgba(255,255,255,.45)}

.shootGrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px}

.goal{position:relative;flex:0 0 285px;min-height:0;margin-top:54px;opacity:.5;pointer-events:none;transition:opacity .2s,transform .2s;transform:scale(.985);cursor:crosshair;background:linear-gradient(180deg,rgba(255,255,255,.018),transparent 40%)}
.goal.active{opacity:1;pointer-events:auto;transform:scale(1)}
.goalFrame{
  position:absolute;left:15%;right:15%;top:42%;bottom:0;width:auto;height:auto;margin:0;
  border:10px solid #e8e8e8;border-bottom:none;border-radius:2px 2px 0 0;cursor:crosshair;
  background:
    repeating-linear-gradient(115deg, rgba(255,255,255,.14) 0 1px, transparent 1px 11px),
    repeating-linear-gradient(65deg,  rgba(255,255,255,.14) 0 1px, transparent 1px 11px),
    linear-gradient(180deg,#173c20,#102a17);
  box-shadow:inset 0 0 30px rgba(0,0,0,.5),0 2px 5px rgba(0,0,0,.45);
}
/* 포스트·크로스바 바깥 1m DSP 기준선: 골문과 이 U자 선 사이 띠가 클릭 가능한 DSP 범위다. */
/* 1m 경계선 위치(18.23%/5.44%)는 스크립트의 GUIDE_TOP/GUIDE_SIDE(실측 규격 기준 계산값)와
   반드시 같이 맞춰야 한다 — 프레임 크기(70%×58%)를 7.32m×2.44m 기준으로 1m 환산한 값이다. */
.hxDivider{position:absolute;left:0;right:0;top:18.23%;border-top:2px solid rgba(225,229,232,.7);box-shadow:0 1px 0 rgba(0,0,0,.3);pointer-events:none;z-index:3}
.meterGuide{position:absolute;left:5.44%;right:5.44%;top:18.23%;bottom:0;border:2px solid rgba(225,229,232,.7);border-bottom:0;box-shadow:0 0 0 1px rgba(0,0,0,.3);pointer-events:none;z-index:3}
.goalZone{cursor:pointer;color:#f0b429;font-weight:800;font-size:11px;display:grid;place-items:center}
.hx{position:absolute;left:0;right:0;top:0;height:18.23%;text-align:center;color:rgba(255,255,255,.6);font-size:10px;letter-spacing:.08em}
.lx{position:absolute;left:0;top:42%;bottom:0;width:5.44%;color:rgba(255,255,255,.68)}
.rx{position:absolute;right:0;top:42%;bottom:0;width:5.44%;color:rgba(255,255,255,.68)}
.goalCenter{position:absolute;inset:0;color:#fff;font-size:20px;letter-spacing:.05em;text-shadow:0 2px 6px rgba(0,0,0,.6);z-index:2;pointer-events:none}
.goal:hover .hx,.goal:hover .lx,.goal:hover .rx{background:rgba(240,180,41,.08)}
.goalResultButtons{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px}
.goalResultBtn{height:40px;border:1px solid rgba(255,255,255,.38);background:#292d31;color:#f1f1f1;font-weight:900;font-size:15px;cursor:pointer}
.goalResultBtn:hover{background:#353a3f}
.goalResultBtn.resGoal{border-color:#f0b429;background:rgba(240,180,41,.16);color:#f0b429}
.goalResultBtn.resGoal:hover{background:rgba(240,180,41,.3)}
/* 프레임 안쪽에 찍어둔 위치 마커. 결과가 확정되기 전까지(B/GOAL/X 누르기 전) 계속 보인다. */
.frameMarker{position:absolute;width:16px;height:16px;margin:-8px;border-radius:50%;background:rgba(240,180,41,.85);border:2px solid #fff;box-shadow:0 0 0 5px rgba(240,180,41,.3);pointer-events:none;z-index:4}

</style>
