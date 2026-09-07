<script setup lang="ts">
import { computeAttackPaths, computeBap } from '~/utils/didLogic'
import type { HalfStatus } from '~/composables/useMatchState'
import {
  GRASS_LINE_OPTIONS,
  GRASS_PATTERNS,
  grassBackground,
} from '~/utils/grass'

const route = useRoute()
const matchId = computed(() => String(route.query.matchId ?? ''))
const match = computed(() => ({
  date: String(route.query.date ?? '') || '2024-12-15', time: String(route.query.time ?? '12:00'),
  league: String(route.query.league ?? '') || 'LALIGA', round: String(route.query.round ?? '') || '17R',
  stadium: String(route.query.stadium ?? '') || 'Fubol de Vallecas',
  home: String(route.query.home ?? '') || 'Vallecano', away: String(route.query.away ?? '') || 'Real Madrid',
}))
const homePlayers = [
  ['1','D. Raya','GK'],['13','A. Ramsdale','GK'],['30','M. Turner','GK'],['7','B. Saka','FW'],['9','G. Jesus','FW'],['12','J. Timber','FW'],['14','E. Nketiah','FW'],
  ['18','T. Tomiyasu','FW'],['19','L. Trossard','FW'],['21','F. Vieira','FW'],['28','Marquinhos','FW'],['6','Gabriel','MF'],['8','Odegaard','MF'],['10','Smith Rowe','MF'],
  ['11','Martinelli','MF'],['15','Kiwior','MF'],['17','Soares','MF'],['23','Lokonga','MF'],['29','Havertz','MF'],['2','Saliba','DF'],['3','Tierney','DF'],
  ['4','B. White','DF'],['5','Partey','DF'],['16','Holding','DF'],['20','Jorginho','DF'],['22','P. Mari','DF'],['24','Nelson','DF'],['26','Balogun','DF'],['27','M. Smith','DF'],
]
const awayPlayers = [
  ['1','T. Courtois','GK'],['13','A. Lunin','GK'],['26','K. Fernandez','GK'],['9','K. Mbappe','FW'],['7','Vinicius Jr','FW'],['11','R. Diaz','FW'],['14','Endrick','FW'],
  ['20','Rodrygo','FW'],['24','A. Gonzalez','FW'],['21','B. Mayoral','FW'],['17','L. Vazquez','FW'],['5','Jude Bellingham','MF'],['15','F. Valverde','MF'],['12','Eduardo Camavinga','MF'],
  ['8','Toni Kroos','MF'],['19','D. Ceballos','MF'],['6','Nacho','MF'],['22','Aurelien Tchouameni','MF'],['16','A. Modric','MF'],['4','David Alaba','DF'],['2','Dani Carvajal','DF'],
  ['3','Eder Militao','DF'],['23','Fran Garcia','DF'],['18','Alvaro Odriozola','DF'],['25','Antonio Rudiger','DF'],['27','Nacho Fernandez','DF'],['28','Jesus Vallejo','DF'],['32','Rafa Marin','DF'],['35','Chema Andres','DF'],
]
const kpis = ['TAP','DAP','DTP','Shoot','ASR','GSR','SSR','BAP']

// ---- 공유 상태 ----
// TeamSelection ↔ DidInput 이 함께 쓰는 임시 스토어(useState). 전반/후반 종료 후
// 이 화면(대기 화면)으로 돌아왔을 때 라인업·스코어·기록이 그대로 남아있어야 하므로,
// selectedTeam/formationKey/assigned/side/inputMode/잔디 설정을 전부 여기로 옮겼다.
const game = useMatchState()

// schedule 에서 다른 경기를 새로 선택해 들어온 경우(matchId 가 바뀐 경우)에는
// 이전 경기의 라인업·기록이 남아있으면 안 되므로 초기화한다.
if (matchId.value && game.value.matchId !== matchId.value) {
  resetMatchState()
  game.value.matchId = matchId.value
}

const players = computed(() => (game.value.team === 'home' ? homePlayers : awayPlayers))

// ---- Player List 정렬 (포지션 / 등번호 / 이름) ----
// 배치 정보(game.assigned)는 players 배열의 "원본 인덱스"를 키로 쓰므로,
// 정렬해도 인덱스가 어긋나지 않도록 { p, i } 짝으로 넘긴다.
type SortKey = 'position' | 'number' | 'name'
const sortKey = ref<SortKey>('position')
const POS_ORDER: Record<string, number> = { GK: 0, FW: 1, MF: 2, DF: 3 }

const sortedPlayers = computed(() => {
  const list = players.value.map((p, i) => ({ p, i }))
  if (sortKey.value === 'number') {
    return list.sort((a, b) => Number(a.p[0]) - Number(b.p[0]))
  }
  if (sortKey.value === 'name') {
    return list.sort((a, b) => String(a.p[1]).localeCompare(String(b.p[1])))
  }
  // 포지션: GK → FW → MF → DF. 같은 포지션 안에서는 원래 순서를 유지한다(안정 정렬).
  return list.sort((a, b) => (POS_ORDER[a.p[2] ?? ''] ?? 9) - (POS_ORDER[b.p[2] ?? ''] ?? 9))
})

// ---- 포메이션 ----
const formations: Record<string, { label: string; slots: { x: number; y: number }[] }> = {
  '4-4-2': { label: '4-4-2', slots: [
    { x: 14, y: 72 }, { x: 38, y: 72 }, { x: 62, y: 72 }, { x: 86, y: 72 },
    { x: 14, y: 46 }, { x: 38, y: 46 }, { x: 62, y: 46 }, { x: 86, y: 46 },
    { x: 35, y: 18 }, { x: 65, y: 18 },
  ] },
  '4-3-3': { label: '4-3-3', slots: [
    { x: 14, y: 72 }, { x: 38, y: 72 }, { x: 62, y: 72 }, { x: 86, y: 72 },
    { x: 26, y: 48 }, { x: 50, y: 48 }, { x: 74, y: 48 },
    { x: 20, y: 16 }, { x: 50, y: 16 }, { x: 80, y: 16 },
  ] },
  '3-5-2': { label: '3-5-2', slots: [
    { x: 26, y: 75 }, { x: 50, y: 75 }, { x: 74, y: 75 },
    { x: 10, y: 46 }, { x: 30, y: 46 }, { x: 50, y: 46 }, { x: 70, y: 46 }, { x: 90, y: 46 },
    { x: 35, y: 16 }, { x: 65, y: 16 },
  ] },
  '4-2-3-1': { label: '4-2-3-1', slots: [
    { x: 14, y: 75 }, { x: 38, y: 75 }, { x: 62, y: 75 }, { x: 86, y: 75 },
    { x: 36, y: 54 }, { x: 64, y: 54 },
    { x: 18, y: 32 }, { x: 50, y: 32 }, { x: 82, y: 32 },
    { x: 50, y: 12 },
  ] },
}
const gkSlot = { x: 50, y: 94 }
const BENCH_COUNT = 7
const benchIds = Array.from({ length: BENCH_COUNT }, (_, i) => `b${i}`)

// 화면 전환/편집 중에만 의미있는 순수 UI 상태 — 공유할 필요 없어 로컬로 둔다.
const menuOpen = ref(false)
const activeSlot = ref<string | null>(null)

const outfieldSlots = computed(() => (game.value.formationKey ? formations[game.value.formationKey].slots : []))

function pickFormation(key: string) {
  game.value.formationKey = key
  menuOpen.value = false
  // 포메이션 바꾸면 배치 초기화
  Object.keys(game.value.assigned).forEach(k => delete game.value.assigned[k])
  activeSlot.value = 'o0'
}

// 개발용 등급 토글. recorders/{uid}.level 연동 전까지 화면에서 직접 전환한다.
function toggleRecorderLevel() {
  game.value.recorderLevel = game.value.recorderLevel === 'basic' ? 'advanced' : 'basic'
  // 실시간 모드는 advanced 전용. basic 으로 내려가면 분석으로 고정한다.
  if (game.value.recorderLevel === 'basic') game.value.inputMode = '분석'
}

// 테스트용: 포메이션/진영/전체 슬롯을 랜덤으로 한 번에 채움
function fillTestData() {
  const keys = Object.keys(formations)
  const key = keys[Math.floor(Math.random() * keys.length)]
  game.value.formationKey = key
  menuOpen.value = false
  Object.keys(game.value.assigned).forEach(k => delete game.value.assigned[k])

  const pool = Array.from({ length: players.value.length }, (_, i) => i)
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }

  const outfieldIds = outfieldSlots.value.map((_, i) => `o${i}`)
  const allIds = [...outfieldIds, 'gk', ...benchIds]
  allIds.forEach((id, i) => { game.value.assigned[id] = pool[i] })

  activeSlot.value = null
  game.value.side = Math.random() < 0.5 ? 'left' : 'right'
}

function pickTeam(team: 'home' | 'away') {
  if (game.value.team === team) return
  game.value.team = team
  // 팀이 바뀌면 선수 명단 자체가 달라지므로 기존 배치는 초기화
  Object.keys(game.value.assigned).forEach(k => delete game.value.assigned[k])
  activeSlot.value = game.value.formationKey ? 'o0' : null
}

// 탭-탭으로 자리 교환: 채워진 슬롯을 탭해 선택한 뒤 다른 슬롯을 탭하면 두 선수 자리가
// 바뀐다(목표가 빈 자리면 그냥 그리로 옮긴다). 태블릿 터치 기준 — 드래그 앤 드롭 대신
// 이 방식을 쓴다. 같은 슬롯을 다시 탭하면 선택 취소.
// 선택된 슬롯이 비어 있으면(=일반적인 배정 흐름) 기존처럼 activeSlot 만 바뀐다.
function clickSlot(id: string) {
  if (id !== 'gk' && !game.value.formationKey) return
  const prev = activeSlot.value
  if (prev === id) {
    activeSlot.value = null
    return
  }
  if (prev && game.value.assigned[prev] !== undefined) {
    const fromIdx = game.value.assigned[prev]!
    const toIdx = game.value.assigned[id]
    if (toIdx === undefined) delete game.value.assigned[prev]
    else game.value.assigned[prev] = toIdx
    game.value.assigned[id] = fromIdx
    activeSlot.value = null
    return
  }
  activeSlot.value = id
}

const usedPlayerIndexes = computed(() => new Set(Object.values(game.value.assigned)))

function advanceAfter(slotId: string) {
  if (slotId.startsWith('o')) {
    const n = outfieldSlots.value.length
    const next = Array.from({ length: n }, (_, i) => `o${i}`).find(id => game.value.assigned[id] === undefined)
    activeSlot.value = next ?? null // 필드 10명 다 차면 GK로 자동 이동 안 함
  } else if (slotId.startsWith('b')) {
    const next = benchIds.find(id => game.value.assigned[id] === undefined)
    activeSlot.value = next ?? null
  } else {
    activeSlot.value = null
  }
}

function assignToSlot(slotId: string, index: number) {
  if (usedPlayerIndexes.value.has(index)) return
  game.value.assigned[slotId] = index
  advanceAfter(slotId)
}

function pickPlayer(index: number) {
  if (!activeSlot.value) return
  assignToSlot(activeSlot.value, index)
}

// ---- 드래그 앤 드롭으로 선수 배정 ----
// (Player List 패널에서 슬롯으로 끌어다 놓는 기존 기능. 마우스 기준이라 태블릿에서도
// 되는지는 별개 — 여기서는 손대지 않는다.)
const dragIndex = ref<number | null>(null)
function onDragStart(index: number) {
  dragIndex.value = index
}
function onDrop(slotId: string) {
  if (dragIndex.value === null) return
  if (slotId !== 'gk' && !slotId.startsWith('b') && !game.value.formationKey) return
  assignToSlot(slotId, dragIndex.value)
  dragIndex.value = null
}

const filledCount = computed(() => Object.keys(game.value.assigned).length)
const totalSlots = computed(() => outfieldSlots.value.length + 1 + BENCH_COUNT)
// 후보까지 전부(18/18) 채워야 전반전 시작 가능
const canStart = computed(() => !!game.value.formationKey && !!game.value.side && filledCount.value >= totalSlots.value)

// DidInput 으로 넘어갈 때 공통으로 실어보내는 쿼리.
// date/league/round/stadium/time 은 TeamSelection 표시에만 쓰지만, DidInput 은 이 값을
// 그대로 들고 있다가 전반/후반 종료 시 TeamSelection 으로 돌아올 때 되돌려준다.
// editReturnStatus: 수정 화면에서 "대기방으로 나가기"를 눌렀을 때 되돌아갈 halfStatus.
// 이미 끝난 half 를 고치러 온 거면 'H1_done'/'H2_done' 으로, 정지 중이던 half 를
// 고치러 온 거면 'H1'/'H2' 로 넘긴다 — 그래야 나갈 때 원래 있던 화면으로 정확히 복귀한다.
function didInputQuery(resumeHalf?: '전반' | '후반', edit?: boolean, editReturnStatus?: HalfStatus) {
  return {
    matchId: matchId.value,
    date: match.value.date,
    time: match.value.time,
    league: match.value.league,
    round: match.value.round,
    stadium: match.value.stadium,
    home: match.value.home,
    away: match.value.away,
    side: game.value.side,
    mode: game.value.inputMode,
    team: game.value.team,
    grass: game.value.grassPattern,
    lines: game.value.grassLines,
    ...(resumeHalf ? { resumeHalf } : {}),
    ...(edit ? { edit: '1' } : {}),
    ...(editReturnStatus ? { editReturn: editReturnStatus } : {}),
  }
}

function startFirstHalf() {
  if (!canStart.value) return
  if (!confirm('전반전을 시작하시겠습니까?\n시작 후에는 라인업을 수정할 수 없습니다.')) return
  game.value.halfStatus = 'H1'
  game.value.seconds = 0 // 새 half 는 0초부터
  navigateTo({ path: '/DidInput', query: didInputQuery() })
}

// "대기방으로 나가기"로 빠져나온 상태(halfStatus 가 H1/H2 인데 이 화면에 있는 경우)에서
// 나갔던 시간 그대로 다시 들어간다. game.seconds 를 건드리지 않는 것이 핵심이다.
const isPaused = computed(() => game.value.halfStatus === 'H1' || game.value.halfStatus === 'H2')
const pausedHalf = computed<'전반' | '후반'>(() => (game.value.halfStatus === 'H2' ? '후반' : '전반'))
const pausedClock = computed(() => {
  const s = game.value.seconds
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
})
function reenterHalf() {
  navigateTo({ path: '/DidInput', query: didInputQuery(pausedHalf.value) })
}
// 정지된 채로 나온 상태에서도 "수정"이 가능해야 한다. halfStatus 는 이미 H1/H2 로
// 올바르게 남아있으므로 건드리지 않고, edit=1 만 붙여 시간이 멈춘 채로 들어간다.
// editReturn 에도 지금 상태(H1/H2)를 그대로 넘겨서, 나갈 때 같은 "정지" 화면으로 돌아오게 한다.
function editPausedHalf() {
  navigateTo({ path: '/DidInput', query: didInputQuery(pausedHalf.value, true, game.value.halfStatus) })
}

// 전반 종료 후 대기 화면(이 화면)에서 고르는 두 가지 선택.
// "수정" = 방금 끝난 전반 기록을 다시 보면서 고치러 DidInput 으로 돌아간다 (PPT 슬라이드 42).
// "후반전 시작" = 스코어/기록을 이어서 후반을 시작한다.
// editReturn 에 "끝난 상태"(H1_done/H2_done)를 넘겨서, 수정 후 "대기방으로 나가기"를
// 누르면 다시 그 끝난 상태(= 후반전 시작 화면)로 돌아오게 한다. halfStatus 를 여기서
// 미리 H1/H2 로 바꾸는 건 DidInput 진입 화면(정지 상태 표시)을 위한 것일 뿐이다.
function editHalf() {
  // 갱신으로 잠긴 half 는 관리자가 풀어주기 전까지 수정할 수 없다.
  if (game.value.halfStatus === 'H1_done' && game.value.h1Locked) return
  if (game.value.halfStatus === 'H2_done' && game.value.h2Locked) return
  const prevStatus = game.value.halfStatus // 'H1_done' | 'H2_done'
  game.value.halfStatus = prevStatus === 'H2_done' ? 'H2' : 'H1'
  navigateTo({ path: '/DidInput', query: didInputQuery(game.value.halfStatus === 'H2' ? '후반' : '전반', true, prevStatus) })
}
function startSecondHalf() {
  game.value.halfStatus = 'H2'
  game.value.seconds = 0 // 새 half 는 0초부터
  navigateTo({ path: '/DidInput', query: didInputQuery('후반') })
}
function finishMatch() {
  if (!confirm('경기를 종료하시겠습니까?')) return
  // TODO: 여기서 파이썬 평점 서비스(jpd-rating) 호출 — 전체 확정 집계 + 평점 + JaionX 전송.
  // 백엔드 연동 전까지는 상태 전환만 한다.
  game.value.halfStatus = 'final'
  navigateTo('/schedule')
}

// basic 등급 전용: 전반/후반 갱신. 누르면 확인 후 그 half 의 수정 버튼과 함께 잠긴다.
// 관리자 잠금 해제 전까지는 다시 누를 수 없다 — 갱신된 데이터를 함부로 고치지 못하게 하려는 것.
function refreshH1() {
  if (game.value.h1Locked) return
  if (!confirm('갱신하면 DB 데이터가 저장됩니다.\n정말 진행하시겠습니까?')) return
  // TODO: 여기서 파이썬 KPI 서비스(jpd-did) 호출 — 판정 + KPI + 5분 구간 1~10.
  // 백엔드 연동 전까지는 잠금 상태만 반영한다.
  game.value.h1Locked = true
}
function refreshH2() {
  if (game.value.h2Locked) return
  if (!confirm('갱신하면 DB 데이터가 저장됩니다.\n정말 진행하시겠습니까?')) return
  // TODO: 여기서 파이썬 KPI 서비스(jpd-did) 호출 — 판정 + KPI + 5분 구간 1~20.
  game.value.h2Locked = true
}
// 잠금 해제는 이 화면(기록자용)에 두지 않는다. 여기 두면 basic 사용자 본인이
// 스스로 풀 수 있게 되어 "관리자만 해제" 라는 전제가 무의미해진다.
// 해제는 /manage(데이터 관리, 관리자 전용 화면)에서만 한다.

// ---- 대기 화면 상태 표시 ----
const statusLabel = computed(() => ({
  ready: '준비중',
  H1: '전반 진행중',
  H1_done: '전반 종료',
  H2: '후반 진행중',
  H2_done: '후반 종료',
  final: '경기 종료',
}[game.value.halfStatus]))

// KPI 는 저장하지 않고 기록(game.records)으로부터 항상 다시 계산한다.
// 현재는 한 번에 한 팀(game.team)의 기록만 입력하므로, 그 팀 쪽 칸에만 값을 채운다.
const kpiValues = computed(() => {
  const { paths, flags } = computeAttackPaths(game.value.records, { closeTrailing: true })
  let tap = 0, dap = 0, dapSc = 0, sht = 0, gol = 0
  for (const f of flags.values()) {
    if (f.isTap) tap++
    if (f.isDap) { dap++; if (f.isDapS) dapSc++ }
    if (f.isSht) sht++
    if (f.isGol) gol++
  }
  const dtp = paths.filter(p => p.ptype === 'DTP' || p.ptype === 'STP').length
  const bap = computeBap(game.value.records).length
  return {
    TAP: tap, DAP: dap, DTP: dtp, Shoot: sht,
    ASR: dap ? Math.round((dapSc / dap) * 100) : 0,
    GSR: dap ? Math.round((gol / dap) * 100) : 0,
    SSR: sht ? Math.round((gol / sht) * 100) : 0,
    BAP: bap,
  }
})

// ---- 잔디선택 ----
// 레거시 APK 이미지 7종(p000/p1xx/p2xx)과 동일한 조합. 상세는 app/utils/grass.ts.
// 여기서 고른 값을 DidInput 으로 넘겨 경기장 배경에 그대로 적용한다.
const grassOpen = ref(false)
const grassBg = computed(() => grassBackground(game.value.grassPattern, game.value.grassLines))
function openGrass() {
  grassOpen.value = !grassOpen.value
}

// ---- 선수교체 ----
// PPT 슬라이드 37: 좌측 = 교체 아웃 선수, 우측 = 교체 투입 선수, 선택 후 저장.
// Player List 자리에서 화면을 전환한다.
const subOpen = ref(false)
const subOut = ref<string | null>(null) // 빠질 선수의 슬롯 id (선발)
const subIn = ref<string | null>(null) //  들어올 선수의 슬롯 id (후보)

const starterSlots = computed(() =>
  [...outfieldSlots.value.map((_, i) => `o${i}`), 'gk']
    .filter(id => game.value.assigned[id] !== undefined)
    .map(id => ({ id, p: playerAt(id) }))
)
const benchSlots = computed(() =>
  benchIds
    .filter(id => game.value.assigned[id] !== undefined)
    .map(id => ({ id, p: playerAt(id) }))
)

// 교체는 고르는 즉시 반영해서 왼쪽 포메이션에 바로 보이게 한다.
// 취소를 누르면 열었을 때 상태로 되돌리기 위해 스냅샷을 떠둔다.
let subSnapshot: Record<string, number> | null = null
const subDragId = ref<string | null>(null)

const isBenchSlot = (id: string) => id.startsWith('b')

function openSub() {
  if (subOpen.value) { closeSub(); return }
  subSnapshot = { ...game.value.assigned }
  subOpen.value = true
  subOut.value = null
  subIn.value = null
}
function closeSub() {
  subOpen.value = false
  subSnapshot = null
  subOut.value = null
  subIn.value = null
  subDragId.value = null
}
/** 선발 ↔ 후보 자리를 맞바꾼다. 즉시 반영되므로 포메이션에 바로 보인다. */
function swapSlots(a: string, b: string) {
  const tmp = game.value.assigned[a]!
  game.value.assigned[a] = game.value.assigned[b]!
  game.value.assigned[b] = tmp
  subOut.value = null
  subIn.value = null
}
function pickOut(id: string) {
  subOut.value = id
  if (subIn.value) swapSlots(id, subIn.value)
}
function pickIn(id: string) {
  subIn.value = id
  if (subOut.value) swapSlots(subOut.value, id)
}
function onSubDragStart(id: string) {
  subDragId.value = id
}
function onSubDrop(targetId: string) {
  const src = subDragId.value
  subDragId.value = null
  if (!src || src === targetId) return
  // 선발끼리, 후보끼리는 교체가 아니므로 무시한다
  if (isBenchSlot(src) === isBenchSlot(targetId)) return
  swapSlots(src, targetId)
}
/** 저장: 이미 반영된 상태를 그대로 유지하고 닫는다. */
function saveSub() {
  closeSub()
}
/** 취소: 열었을 때 상태로 되돌린다. */
function cancelSub() {
  if (subSnapshot) {
    Object.keys(game.value.assigned).forEach(k => delete game.value.assigned[k])
    Object.assign(game.value.assigned, subSnapshot)
  }
  closeSub()
}
function playerAt(id: string) {
  const idx = game.value.assigned[id]
  return idx === undefined ? null : players.value[idx]
}
</script>

<template>
  <div class="page"><div class="bg" />
    <div class="frame">
      <aside class="sidebar">
        <div class="matchDate">{{ match.date.replaceAll('-', '.') }}</div>
        <div class="teamPick">입력할 팀 선택</div>
        <div class="teams">
          <button class="club" :class="{ active: game.team === 'home' }" @click="pickTeam('home')"><div class="crest homeCrest">V</div><span>{{ match.home }}</span></button>
          <span class="versus">VS</span>
          <button class="club" :class="{ active: game.team === 'away' }" @click="pickTeam('away')"><div class="crest awayCrest">RM</div><span>{{ match.away }}</span></button>
        </div>
        <div class="score">{{ game.homeScore }} : {{ game.awayScore }}</div><div class="status">{{ statusLabel }}</div>
        <div class="matchMeta">{{ match.time }} | {{ match.league }} | {{ match.round }}</div><div class="stadium">{{ match.stadium }}</div>
        <div class="kpis">
          <div v-for="key in kpis" :key="key" class="kpiRow">
            <span>{{ game.team === 'home' ? kpiValues[key] : 0 }}</span><b>{{ key }}</b><span>{{ game.team === 'away' ? kpiValues[key] : 0 }}</span>
          </div>
        </div>
        <button class="testBtn" @click="fillTestData">TEST</button>
        <!-- 개발용 등급 토글. 실제로는 recorders/{uid}.level 을 읽어와야 하지만
             그 연동 전까지 여기서 basic/advanced 화면을 바로 바꿔가며 확인한다. -->
        <button class="levelToggle" :class="{ basic: game.recorderLevel === 'basic' }" @click="toggleRecorderLevel">
          등급: {{ game.recorderLevel === 'basic' ? 'BASIC' : 'ADVANCED' }}
        </button>
        <button class="backBtn" @click="navigateTo('/schedule')">◀ 이전화면으로</button>
      </aside>
      <main class="content"><h1>Player List</h1>
        <div class="workspace">
        <div class="topRow">
          <section class="formationPanel">
            <div class="selectBar" :class="{ open: menuOpen }" @click="menuOpen = !menuOpen">
              <span>{{ game.formationKey ? `${formations[game.formationKey].label} 포메이션` : '포메이션을 선택하세요' }}</span><span>⌄</span>
              <div v-if="menuOpen" class="menu" @click.stop>
                <div v-for="(f, key) in formations" :key="key" class="menuItem" @click="pickFormation(key)">{{ f.label }}</div>
              </div>
            </div>
            <div class="pitch">
              <div class="halfway"/><div class="centerCircle"/>
              <div class="penaltyArc"/><div class="penaltyBox"/><div class="penaltySpot"/>
              <div class="goalBox"/><div class="goalPost"/>
              <div class="cornerArc left"/><div class="cornerArc right"/>
              <template v-if="game.formationKey">
                <button
                  v-for="(s, i) in outfieldSlots" :key="`o${i}`"
                  class="slot" :class="{ active: activeSlot === `o${i}`, filled: game.assigned[`o${i}`] !== undefined }"
                  :style="{ left: s.x + '%', top: s.y + '%' }"
                  @click="clickSlot(`o${i}`)"
                  @dragover.prevent
                  @drop="onDrop(`o${i}`)"
                >{{ playerAt(`o${i}`)?.[0] ?? '' }}</button>
                <button
                  class="slot gk" :class="{ active: activeSlot === 'gk', filled: game.assigned['gk'] !== undefined }"
                  :style="{ left: gkSlot.x + '%', top: gkSlot.y + '%' }"
                  @click="clickSlot('gk')"
                  @dragover.prevent
                  @drop="onDrop('gk')"
                >{{ playerAt('gk')?.[0] ?? 'GK' }}</button>
              </template>
              <div v-else class="shirt"/>
              <b class="count">{{ filledCount }} / {{ game.formationKey ? totalSlots : 16 }}</b>
            </div>
            <div v-if="game.formationKey" class="bench">
              <button
                v-for="id in benchIds" :key="id" class="slot benchSlot"
                :class="{ active: activeSlot === id, filled: game.assigned[id] !== undefined }"
                @click="clickSlot(id)"
                @dragover.prevent
                @drop="onDrop(id)"
              >{{ playerAt(id)?.[0] ?? '' }}</button>
            </div>
          </section>
          <section class="playerPanel">
            <template v-if="!subOpen">
              <div class="tabs">
                <button :class="{ off: sortKey !== 'position' }" @click="sortKey = 'position'">Position</button>
                <button :class="{ off: sortKey !== 'number' }" @click="sortKey = 'number'">Number</button>
                <button :class="{ off: sortKey !== 'name' }" @click="sortKey = 'name'">Name</button>
              </div>
              <div class="playerGrid">
                <button
                  v-for="{ p, i } in sortedPlayers" :key="i" class="player" :class="[p[2]?.toLowerCase(), { used: usedPlayerIndexes.has(i), pickable: !usedPlayerIndexes.has(i) }]"
                  :draggable="!usedPlayerIndexes.has(i)"
                  @click="pickPlayer(i)"
                  @dragstart="onDragStart(i)"
                ><strong>{{ p[0] }}</strong><span>{{ p[1] }}</span></button>
                <div v-for="i in 13" :key="`e${i}`" class="player empty"/>
              </div>
              <div class="legend"><span class="gk">GK</span><span class="fw">FW</span><span class="mf">MF</span><span class="df">DF</span></div>
            </template>

            <!-- 선수 교체: 좌 = OUT(선발), 우 = IN(후보) -->
            <template v-else>
              <div class="tabs subTabs">
                <button class="subTitle">선수 교체</button>
              </div>
              <p class="subHint">서로 끌어다 놓거나, 양쪽에서 하나씩 눌러 교체하세요</p>
              <div class="subCols">
                <div class="subCol">
                  <div class="subColHead out">교체 OUT · 선발</div>
                  <div class="subList" @dragover.prevent>
                    <button
                      v-for="s in starterSlots" :key="s.id"
                      class="subItem" :class="[s.p?.[2]?.toLowerCase(), { on: subOut === s.id, dragging: subDragId === s.id }]"
                      draggable="true"
                      @click="pickOut(s.id)"
                      @dragstart="onSubDragStart(s.id)"
                      @dragend="subDragId = null"
                      @dragover.prevent
                      @drop="onSubDrop(s.id)"
                    ><strong>{{ s.p?.[0] }}</strong><span>{{ s.p?.[1] }}</span></button>
                  </div>
                </div>
                <div class="subCol">
                  <div class="subColHead in">교체 IN · 후보</div>
                  <div class="subList" @dragover.prevent>
                    <button
                      v-for="s in benchSlots" :key="s.id"
                      class="subItem" :class="[s.p?.[2]?.toLowerCase(), { on: subIn === s.id, dragging: subDragId === s.id }]"
                      draggable="true"
                      @click="pickIn(s.id)"
                      @dragstart="onSubDragStart(s.id)"
                      @dragend="subDragId = null"
                      @dragover.prevent
                      @drop="onSubDrop(s.id)"
                    ><strong>{{ s.p?.[0] }}</strong><span>{{ s.p?.[1] }}</span></button>
                    <div v-if="!benchSlots.length" class="subEmpty">후보 선수가 없습니다</div>
                  </div>
                </div>
              </div>
              <div class="subActions">
                <button class="subCancel" @click="cancelSub">취소</button>
                <button class="subSave" @click="saveSub">저장</button>
              </div>
            </template>
          </section>
        </div>
        <div class="bottomRow">
          <section class="fieldChoice">
            <h2>진영선택</h2>
            <div class="miniPitch">
              <div class="miniHalf"/><div class="miniCircle"/><div class="miniPenalty left"/><div class="miniPenalty right"/><div class="miniGoal left"/><div class="miniGoal right"/>
              <div class="miniBox leftBox" :class="{ active: game.side === 'left' }" @click="game.side = 'left'"/>
              <div class="miniBox rightBox" :class="{ active: game.side === 'right' }" @click="game.side = 'right'"/>
            </div>
          </section>
          <section class="toolPanel">
            <div class="toolGrid">
              <button class="toolBtn" :class="{ on: grassOpen }" @click="openGrass">
                <span class="toolIcon grassIcon" :style="{ background: grassBg }" />
                <span class="toolLabel">잔디선택</span>
              </button>
              <button class="toolBtn" :class="{ on: subOpen }" @click="openSub">
                <span class="toolIcon subIcon">⇄</span>
                <span class="toolLabel">선수교체</span>
              </button>
            </div>

            <div v-if="grassOpen" class="grassPop">
              <div class="popRow">
                <span class="popLabel">잔디 패턴</span>
                <div class="popOpts">
                  <button
                    v-for="g in GRASS_PATTERNS" :key="g.value"
                    class="popBtn" :class="{ on: game.grassPattern === g.value }"
                    @click="game.grassPattern = g.value"
                  >{{ g.label }}</button>
                </div>
              </div>
              <div class="popRow">
                <span class="popLabel">잔디 라인</span>
                <div class="popOpts">
                  <button
                    v-for="n in GRASS_LINE_OPTIONS" :key="n"
                    class="popBtn" :class="{ on: game.grassLines === n }"
                    :disabled="game.grassPattern === 0"
                    @click="game.grassLines = n"
                  >{{ n }}줄</button>
                </div>
              </div>
              <div class="popPreview" :style="{ background: grassBg }" />
              <button class="popOk" @click="grassOpen = false">확인</button>
            </div>
          </section>
          <section class="startPanel">
            <template v-if="game.halfStatus === 'ready'">
              <div v-if="game.recorderLevel === 'advanced'" class="modeToggle">
                <button class="modeBtn" :class="{ on: game.inputMode === '분석' }" @click="game.inputMode = '분석'">분석<small>정지 가능</small></button>
                <button class="modeBtn" :class="{ on: game.inputMode === '실시간' }" @click="game.inputMode = '실시간'">실시간<small>정지 불가</small></button>
              </div>
              <p>아래의 버튼을 터치하시면<br><b>경기데이터 입력이 시작됩니다.</b></p>
              <button class="startBtn" :disabled="!canStart" @click="startFirstHalf">전반전 시작</button>
            </template>
            <template v-else-if="game.halfStatus === 'H1_done'">
              <p>전반 기록을 확인하세요<br><b>기록을 수정하거나 후반전을 시작할 수 있습니다.</b></p>
              <p v-if="game.recorderLevel === 'basic' && game.h1Locked" class="lockNotice">🔒 갱신됨 — 관리자만 수정 잠금을 풀 수 있습니다. (데이터 관리에서 해제)</p>
              <div v-if="game.recorderLevel === 'basic'" class="halfActions halfActions3">
                <button class="editBtn" :disabled="game.h1Locked" @click="editHalf">수정</button>
                <button class="refreshBtn" :disabled="game.h1Locked" @click="refreshH1">전반전 갱신</button>
                <button class="startBtn" @click="startSecondHalf">후반전 시작</button>
              </div>
              <div v-else class="halfActions">
                <button class="editBtn" @click="editHalf">수정</button>
                <button class="startBtn" @click="startSecondHalf">후반전 시작</button>
              </div>
            </template>
            <template v-else-if="game.halfStatus === 'H2_done'">
              <p>후반 기록을 확인하세요<br><b>기록을 수정하거나 경기를 종료할 수 있습니다.</b></p>
              <p v-if="game.recorderLevel === 'basic' && game.h2Locked" class="lockNotice">🔒 갱신됨 — 관리자만 수정 잠금을 풀 수 있습니다. (데이터 관리에서 해제)</p>
              <div v-if="game.recorderLevel === 'basic'" class="halfActions halfActions3">
                <button class="editBtn" :disabled="game.h2Locked" @click="editHalf">수정</button>
                <button class="refreshBtn" :disabled="game.h2Locked" @click="refreshH2">후반전 갱신</button>
                <button class="startBtn" @click="finishMatch">최종 데이터 갱신 &amp; 경기 종료</button>
              </div>
              <div v-else class="halfActions">
                <button class="editBtn" @click="editHalf">수정</button>
                <button class="startBtn" @click="finishMatch">최종 데이터 갱신 &amp; 경기 종료</button>
              </div>
            </template>
            <template v-else-if="isPaused">
              <p>{{ pausedHalf }} 기록이 대기 중입니다<br><b>{{ pausedClock }} 시점부터 이어서 입력합니다.</b></p>
              <div class="halfActions">
                <button class="editBtn" @click="editPausedHalf">수정</button>
                <button class="startBtn" @click="reenterHalf">{{ pausedHalf }}전 입장</button>
              </div>
            </template>
            <template v-else>
              <p>{{ statusLabel }}</p>
            </template>
            <small v-if="matchId">matchId: {{ matchId }}</small>
          </section>
        </div>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
*{box-sizing:border-box}button{font:inherit}
.page{width:1280px;height:800px;padding:0;display:grid;place-items:center;overflow:hidden;position:relative;background:#0b0f17;color:#fff;font-family:Arial,"Noto Sans KR",sans-serif}
.bg{position:absolute;inset:0;background:radial-gradient(1200px 500px at 50% 25%,rgba(255,255,255,.08),transparent 60%),radial-gradient(900px 400px at 20% 70%,rgba(111,159,186,.09),transparent 55%),radial-gradient(900px 400px at 80% 70%,rgba(241,180,0,.08),transparent 55%),linear-gradient(180deg,rgba(0,0,0,.55),rgba(0,0,0,.78))}

.frame{position:relative;width:1280px;height:800px;border-radius:0;display:grid;grid-template-columns:266px 1014px;overflow:hidden;border:1px solid rgba(255,255,255,.08);background:rgba(10,14,22,.78);backdrop-filter:blur(8px);box-shadow:0 18px 60px rgba(0,0,0,.55)}

.sidebar{padding:12px 12px 10px;border-right:1px solid rgba(255,255,255,.06);display:flex;flex-direction:column}
.matchDate{text-align:center;font-size:16px;line-height:22px;font-weight:900;color:rgba(255,255,255,.85)}
.teamPick{margin-top:8px;text-align:center;font-size:13px;font-weight:800;letter-spacing:.03em;color:rgba(255,255,255,.7)}
.teams{height:92px;display:grid;grid-template-columns:1fr 22px 1fr;align-items:center;border-bottom:1px solid rgba(255,255,255,.08)}
.club{min-width:0;display:grid;justify-items:center;gap:7px;font-size:10px;font-weight:800;text-align:center;color:rgba(255,255,255,.85);background:none;border:1px solid transparent;border-radius:6px;padding:6px 4px;cursor:pointer}
.club:hover{background:rgba(255,255,255,.04)}
.club.active{border-color:#f0b429;background:rgba(240,180,41,.1)}
.crest{width:30px;height:34px;display:grid;place-items:center;border-radius:45% 45% 55% 55%;font-size:9px;font-weight:900;color:white;border:2px solid #e7d365}
.homeCrest{background:linear-gradient(135deg,#fff 0 38%,#e43f3f 38% 53%,#fff 53%);color:#273246}
.awayCrest{background:#fff;color:#2649a2;border-color:#e0bc42}
.versus{width:21px;height:21px;display:grid;place-items:center;border-radius:50%;background:rgba(255,255,255,.9);color:#222;font-size:8px;font-weight:900}
.score{margin-top:12px;text-align:center;font-size:20px;line-height:24px;font-weight:900;color:rgba(255,255,255,.9)}
.status{color:rgba(241,180,0,.95);text-align:center;font-size:12px;font-weight:800}
.matchMeta,.stadium{margin-top:8px;text-align:center;color:rgba(255,255,255,.45);font-size:11px}
.stadium{margin-top:2px}
.kpis{margin-top:12px;display:grid;gap:4px}
.kpiRow{height:28px;display:grid;grid-template-columns:1fr 1.15fr 1fr;place-items:center;background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.07);border-radius:4px;color:rgba(255,255,255,.7);font-size:10.5px}
.kpiRow b{font-size:10px;letter-spacing:.04em;color:rgba(255,255,255,.55)}
.testBtn{margin-top:auto;height:30px;border-radius:4px;border:1px dashed rgba(240,180,41,.5);background:rgba(240,180,41,.08);color:#f0b429;cursor:pointer;font-size:11px;font-weight:800;letter-spacing:.05em}
.backBtn{margin-top:8px;height:34px;border-radius:4px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.02);color:rgba(255,255,255,.75);cursor:pointer;font-size:12px}

.content{min-width:0;padding:14px 18px 18px}
.content h1{height:28px;margin:0;text-align:center;font-size:18px;font-weight:900;line-height:28px;color:rgba(255,255,255,.85)}
.workspace{height:calc(100% - 28px);display:flex;flex-direction:column;gap:10px}
.topRow{flex:2.2;min-height:0;display:grid;grid-template-columns:1fr 1fr;gap:10px}
.bottomRow{flex:1;min-height:0;max-height:170px;display:grid;grid-template-columns:0.7fr 1.3fr 1fr;gap:10px}

.formationPanel,.playerPanel,.fieldChoice,.toolPanel,.startPanel{min-width:0;min-height:0;padding:12px;border-radius:6px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03)}
.formationPanel{display:grid;grid-template-rows:28px minmax(0,1fr) 46px;gap:8px}
.playerPanel{display:flex;flex-direction:column}
.fieldChoice{display:flex;flex-direction:column}
.toolPanel{display:flex;flex-direction:column;justify-content:center}
.startPanel{position:relative;display:flex;flex-direction:column;justify-content:center}

.selectBar{position:relative;display:flex;align-items:center;justify-content:space-between;padding:0 10px;background:#f2f2f2;color:#252525;font-size:11px;border-radius:4px;border:1px solid rgba(255,255,255,.15);cursor:pointer;user-select:none}
.selectBar.open{border-radius:4px 4px 0 0}
.menu{position:absolute;left:0;right:0;top:100%;z-index:5;background:#fff;border:1px solid rgba(255,255,255,.15);border-top:none;border-radius:0 0 4px 4px;box-shadow:0 8px 20px rgba(0,0,0,.4)}
.menuItem{padding:8px 10px;color:#252525;font-size:11px;cursor:pointer}
.menuItem:hover{background:#eef6f8}

.pitch{position:relative;overflow:hidden;background:rgba(0,0,0,.18);border-radius:6px;border:1px solid rgba(255,255,255,.1)}
.halfway{position:absolute;left:0;right:0;top:0;height:43%;border-bottom:2px solid rgba(255,255,255,.22)}
.centerCircle{position:absolute;width:104px;height:104px;border:2px solid rgba(255,255,255,.22);border-radius:50%;left:50%;top:-53px;transform:translateX(-50%)}
/* 페널티 에어리어 — 실제 규격 비율로 그린다.
   페널티박스 40.32×16.5m, 골에어리어 18.32×5.5m, 페널티마크 11m,
   아크 반지름 9.15m(박스 위로 3.65m, 폭 14.6m), 골대 7.32m. 아래쪽이 골라인. */
.penaltyBox{position:absolute;left:50%;bottom:0;transform:translateX(-50%);width:58%;height:24%;border:2px solid rgba(255,255,255,.22);border-bottom:0}
.goalBox{position:absolute;left:50%;bottom:0;transform:translateX(-50%);width:26.3%;height:8%;border:2px solid rgba(255,255,255,.22);border-bottom:0}
.penaltySpot{position:absolute;left:50%;bottom:16%;width:4px;height:4px;margin:0 0 -2px -2px;border-radius:50%;background:rgba(255,255,255,.4)}
.penaltyArc{position:absolute;left:50%;bottom:24%;transform:translateX(-50%);width:21%;height:5.3%;border:2px solid rgba(255,255,255,.22);border-bottom:0;border-radius:50% 50% 0 0 / 100% 100% 0 0}
.goalPost{position:absolute;left:50%;bottom:0;transform:translateX(-50%);width:10.5%;height:2.6%;border:2px solid rgba(255,255,255,.45);border-bottom:0;background:rgba(255,255,255,.05)}
.cornerArc{position:absolute;bottom:-9px;width:18px;height:18px;border:2px solid rgba(255,255,255,.18);border-radius:50%}
.cornerArc.left{left:-9px}
.cornerArc.right{right:-9px}
.shirt{position:absolute;left:50%;bottom:47px;width:46px;height:37px;transform:translateX(-50%);background:#e7ecf5;clip-path:polygon(22% 0,38% 10%,62% 10%,78% 0,100% 25%,82% 42%,75% 34%,75% 100%,25% 100%,25% 34%,18% 42%,0 25%)}
.bench{min-height:0;display:flex;align-items:center;justify-content:center;gap:10px;border-radius:6px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.02)}
.slot.benchSlot{position:static;width:40px;height:40px;transform:none;font-size:15px}
.count{position:absolute;right:12px;bottom:8px;color:rgba(241,180,0,.95);font-size:13px;font-weight:800}
.slot{position:absolute;transform:translate(-50%,-50%);width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.14);border:2px solid rgba(255,255,255,.25);color:#fff;font-size:16px;font-weight:800;cursor:pointer;display:grid;place-items:center;padding:0}
.slot:hover{background:rgba(255,255,255,.22)}
.slot.filled{background:rgba(111,159,186,.22);border-color:#5fb8c9}
.slot.active{outline:2px solid #f0b429;outline-offset:2px}
.slot.gk{background:rgba(255,255,255,.2)}
.slot.gk.filled{background:rgba(180,190,200,.4);border-color:#c9d2db}

.fieldChoice h2{margin:0 0 8px;font-size:13px;text-align:center;color:rgba(255,255,255,.8);font-weight:800}

/* 잔디선택 / 선수교체 */
.toolPanel{position:relative}
.toolGrid{flex:1;min-height:0;display:grid;grid-template-columns:1fr 1fr;gap:12px}
.toolBtn.on{border-color:#f0b429;background:rgba(240,180,41,.12)}

.grassPop{position:absolute;z-index:30;left:12px;right:12px;bottom:calc(100% + 8px);padding:10px;border-radius:6px;border:1px solid rgba(255,255,255,.16);background:#1b1e22;box-shadow:0 14px 40px rgba(0,0,0,.6);display:flex;flex-direction:column;gap:9px}
.popRow{display:flex;flex-direction:column;gap:5px}
.popLabel{font-size:10px;font-weight:800;color:rgba(255,255,255,.5)}
.popOpts{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}
.popBtn{height:24px;border-radius:4px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);color:#ddd;font-size:10px;font-weight:700;cursor:pointer;padding:0}
.popBtn.on{border-color:#f0b429;background:rgba(240,180,41,.2);color:#f0b429}
.popBtn:disabled{opacity:.35;cursor:not-allowed}
.popPreview{height:34px;border-radius:4px;border:1px solid rgba(255,255,255,.18)}
.popOk{height:26px;border-radius:4px;border:none;background:#f0b429;color:#191919;font-weight:800;font-size:11px;cursor:pointer}
.toolBtn{min-width:0;min-height:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:10px;border-radius:6px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);color:rgba(255,255,255,.85);cursor:pointer}
.toolBtn:hover{border-color:#f0b429;background:rgba(240,180,41,.12)}
.toolBtn:active{transform:scale(.98)}
.toolIcon{width:52px;height:44px;border-radius:4px;display:grid;place-items:center;font-size:24px;color:#eee}
.grassIcon{background:repeating-linear-gradient(90deg,#2f7d3c 0 8px,#256a31 8px 16px);border:1px solid rgba(255,255,255,.25)}
.subIcon{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.2);color:#f0b429}
.toolLabel{font-size:13px;font-weight:800;letter-spacing:.02em}
.miniPitch{flex:1;min-height:0;position:relative;border-radius:6px;border:1px solid rgba(255,255,255,.15);background:rgba(0,0,0,.14);overflow:hidden}
.miniHalf{position:absolute;left:50%;top:0;bottom:0;border-left:1px solid rgba(255,255,255,.25);z-index:1;pointer-events:none}
.miniCircle{position:absolute;width:34%;aspect-ratio:1;max-width:60px;border:1px solid rgba(255,255,255,.25);border-radius:50%;left:50%;top:50%;transform:translate(-50%,-50%);z-index:1;pointer-events:none}
.miniPenalty{position:absolute;top:18%;bottom:18%;width:14%;border:1px solid rgba(255,255,255,.25);z-index:1;pointer-events:none}
.miniPenalty.left{left:0;border-left:none}
.miniPenalty.right{right:0;border-right:none}
.miniGoal{position:absolute;top:38%;bottom:38%;width:4%;border:1px solid rgba(255,255,255,.25);z-index:1;pointer-events:none}
.miniGoal.left{left:0;border-left:none}
.miniGoal.right{right:0;border-right:none}
.miniBox{position:absolute;top:0;bottom:0;width:50%;cursor:pointer}
.miniBox:hover{background:rgba(255,255,255,.08)}
.miniBox.active{background:rgba(111,159,186,.22)}
.leftBox{left:0}.rightBox{right:0}

.playerPanel{}
.tabs{height:30px;display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.tabs button{padding:0;font:inherit;display:grid;place-items:center;background:#f2f2f2;border-radius:4px;border:1px solid rgba(255,255,255,.15);color:#222;font-size:10px;font-weight:800;cursor:pointer}
.tabs .off{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.1);color:rgba(255,255,255,.5)}
.tabs .off:hover{background:rgba(255,255,255,.1);color:rgba(255,255,255,.8)}
.playerGrid{flex:1;margin-top:8px;display:grid;grid-template-columns:repeat(7,1fr);grid-template-rows:repeat(6,1fr);gap:1px;background:rgba(255,255,255,.06);border-radius:4px;overflow:hidden}
.player{min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;border:1px solid rgba(255,255,255,.04);background:rgba(255,255,255,.03);overflow:hidden;padding:0;cursor:default}
.player.pickable{cursor:grab}
.player.pickable:active{cursor:grabbing}
.player.pickable:hover{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.15)}
.player.used{opacity:.3;cursor:not-allowed}
.player strong{font-size:19px;line-height:20px;color:rgba(255,255,255,.85)}
.player span{max-width:100%;padding:0 2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:rgba(255,255,255,.45);font-size:8px}
.player.gk strong{color:rgba(255,255,255,.55)}
.player.fw strong{color:#5fb8c9}
.player.mf strong{color:#d98671}
.player.df strong{color:#93b56a}
.player.empty{min-height:20px}
/* 선수 교체 */
.subTabs{grid-template-columns:1fr}
.subTitle{cursor:default}
.subHint{margin:8px 0 0;text-align:center;font-size:10px;color:rgba(255,255,255,.4)}
.subCols{flex:1;min-height:0;display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:8px 0}
.subItem{cursor:grab}
.subItem:active{cursor:grabbing}
.subItem.dragging{opacity:.4}
.subCol{min-width:0;min-height:0;display:flex;flex-direction:column;gap:6px}
.subColHead{height:22px;display:grid;place-items:center;border-radius:4px;font-size:10px;font-weight:800;letter-spacing:.02em}
.subColHead.out{background:rgba(217,134,113,.18);color:#d98671;border:1px solid rgba(217,134,113,.4)}
.subColHead.in{background:rgba(147,181,106,.18);color:#93b56a;border:1px solid rgba(147,181,106,.4)}
.subList{flex:1;min-height:0;overflow-y:auto;display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:34px;gap:5px;align-content:start;padding-right:2px}
.subItem{min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;border-radius:4px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);cursor:pointer;padding:0 4px}
.subItem strong{font-size:13px;font-weight:900;line-height:1}
.subItem span{font-size:8px;color:rgba(255,255,255,.6);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.subItem.gk strong{color:#ddd}.subItem.fw strong{color:#5fb8c9}.subItem.mf strong{color:#d98671}.subItem.df strong{color:#93b56a}
.subItem:hover{border-color:rgba(240,180,41,.6);background:rgba(240,180,41,.1)}
.subItem.on{border-color:#f0b429;background:rgba(240,180,41,.24);box-shadow:0 0 0 1px #f0b429 inset}
.subEmpty{grid-column:1/3;color:rgba(255,255,255,.35);font-size:10px;text-align:center;padding-top:14px}
.subActions{height:32px;display:grid;grid-template-columns:1fr 1fr;gap:8px}
.subCancel,.subSave{border-radius:4px;font-size:12px;font-weight:800;cursor:pointer;border:1px solid #f0b429;background:transparent;color:#f0b429}
.subCancel:hover{background:rgba(240,180,41,.14)}
.subSave{background:#f0b429;color:#191919}
.subSave:disabled{background:transparent;border-color:rgba(255,255,255,.18);color:rgba(255,255,255,.35);cursor:not-allowed}

.legend{height:19px;display:flex;align-items:end;justify-content:flex-end;gap:7px;font-size:9px}
.legend span{padding:1px 8px;border-radius:3px;color:white;font-weight:700}
.legend .gk{background:rgba(255,255,255,.25)}
.legend .fw{background:#5fb8c9}
.legend .mf{background:#d98671}
.legend .df{background:#93b56a}


.modeToggle{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:8px}
.modeBtn{height:22px;border-radius:4px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.03);color:rgba(255,255,255,.45);font-size:10px;font-weight:800;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:4px}
.modeBtn small{font-size:8px;font-weight:600;color:rgba(255,255,255,.3)}
.modeBtn.on{background:rgba(240,180,41,.18);border-color:#f0b429;color:#f0b429}
.modeBtn.on small{color:rgba(240,180,41,.65)}
.startPanel p{margin:0 0 10px;text-align:center;font-size:11px;line-height:1.4;color:rgba(255,255,255,.75)}
.startPanel p b{color:rgba(241,180,0,.95)}
.startBtn{height:43px;border-radius:4px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04);color:rgba(255,255,255,.35);cursor:not-allowed}
.startBtn:not(:disabled){background:#f0b429;border-color:#f0b429;color:#161200;font-weight:800;cursor:pointer}
.startPanel small{position:absolute;right:8px;bottom:3px;color:rgba(255,255,255,.25);font-size:8px}
.halfActions{display:grid;grid-template-columns:1fr 1.4fr;gap:8px}
.halfActions3{grid-template-columns:1fr 1fr 1.3fr}
.editBtn{height:43px;border-radius:4px;border:1px solid rgba(255,255,255,.16);background:rgba(255,255,255,.04);color:#ddd;font-weight:800;cursor:pointer}
.editBtn:hover{background:rgba(255,255,255,.1)}
.editBtn:disabled{opacity:.35;cursor:not-allowed}
.halfActions .startBtn{background:#f0b429;border-color:#f0b429;color:#161200;cursor:pointer}
.refreshBtn{height:43px;border-radius:4px;border:1px solid rgba(240,180,41,.4);background:rgba(240,180,41,.08);color:#f0b429;font-weight:800;cursor:pointer;font-size:12px}
.refreshBtn:hover:not(:disabled){background:rgba(240,180,41,.18)}
.refreshBtn:disabled{opacity:.35;cursor:not-allowed;color:rgba(255,255,255,.35);border-color:rgba(255,255,255,.16);background:transparent}
.lockNotice{margin:0 0 8px;font-size:11px;color:#f0b429;text-align:center}
.levelToggle{margin-top:6px;height:26px;border-radius:4px;border:1px dashed rgba(240,180,41,.5);background:rgba(240,180,41,.08);color:#f0b429;cursor:pointer;font-size:10px;font-weight:800;letter-spacing:.03em}
.levelToggle.basic{background:rgba(99,192,162,.12);border-color:rgba(99,192,162,.5);color:#63c0a2}

</style>
