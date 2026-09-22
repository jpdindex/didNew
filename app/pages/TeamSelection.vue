<script setup lang="ts">
import { computeAttackPaths, computeBap } from '~/utils/didLogic'
import type { HalfStatus, MatchSnapshot, MatchSquadPlayer, SubRecord } from '~/composables/useMatchState'
import type { Half } from '~/types/schema'
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
const kpis = ['TAP', 'DAP', 'DTP', 'Shoot', 'Goal', 'SSR', 'BAP', 'ASR']
const kpiHalf = ref<'all' | 'H1' | 'H2'>('all')

// ---- 공유 상태 ----
// TeamSelection ↔ DidInput 이 함께 쓰는 임시 스토어(useState). 전반/후반 종료 후
// 이 화면(대기 화면)으로 돌아왔을 때 라인업·스코어·기록이 그대로 남아있어야 하므로,
// selectedTeam/formationKey/assigned/side/inputMode/잔디 설정을 전부 여기로 옮겼다.
const game = useMatchState()
const { request } = useBackendApi()
const { saveLocal, save: saveDraft, promoteH1, finalizeAdvanced, restoreFinalRaw, recover: recoverDraft } = useMatchDraft()
const lifecycleBusy = ref(false)
const lifecycleError = ref('')

// 전반 시작~종료 구간에는 전반 데이터를, 후반 시작~종료 구간에는 후반 데이터를 자동으로 보여준다.
watch(() => game.value.halfStatus, (st) => {
  if (st === 'H1' || st === 'H1_done') kpiHalf.value = 'H1'
  else if (st === 'H2' || st === 'H2_done') kpiHalf.value = 'H2'
  else if (st === 'final') kpiHalf.value = 'all'
}, { immediate: true })

// schedule 에서 다른 경기를 새로 선택해 들어온 경우(matchId 가 바뀐 경우)에는
// 이전 경기의 라인업·기록이 남아있으면 안 되므로 초기화한다.
if (matchId.value && game.value.matchId !== matchId.value) {
  resetMatchState()
  game.value.matchId = matchId.value
}

const players = computed<MatchSquadPlayer[]>(() => (game.value.team === 'home' ? game.value.squads.home : game.value.squads.away))

type FieldSide = 'left' | 'right'
type InputSideStatus = { rawStatus: string | null; completed: boolean; fieldSide: FieldSide | null }
const inputStatus = ref<{ H: InputSideStatus; A: InputSideStatus }>({
  H: { rawStatus: null, completed: false, fieldSide: null },
  A: { rawStatus: null, completed: false, fieldSide: null },
})

async function loadSquads() {
  if (!matchId.value) return
  const payload = await request<{
    H: MatchSquadPlayer[]
    A: MatchSquadPlayer[]
    matchSnapshot: MatchSnapshot
    inputStatus: { H: InputSideStatus; A: InputSideStatus }
  }>(`/api/v1/match-input/matches/${encodeURIComponent(matchId.value)}/squads`)
  const squads = { home: payload.H, away: payload.A }
  game.value.squads = squads
  game.value.matchSnapshot = payload.matchSnapshot
  inputStatus.value = payload.inputStatus
  return { squads, snapshot: payload.matchSnapshot }
}

function oppositeFieldSide(side: FieldSide): FieldSide {
  return side === 'left' ? 'right' : 'left'
}

function counterpartFieldSide(team: 'home' | 'away'): FieldSide | null {
  const other = team === 'home' ? inputStatus.value.A : inputStatus.value.H
  return other.fieldSide ? oppositeFieldSide(other.fieldSide) : null
}

function applyOpponentFieldSideDefault(team: 'home' | 'away') {
  if (game.value.side !== null) return
  const automatic = counterpartFieldSide(team)
  if (automatic) game.value.side = automatic
}

function applyCurrentMatchSquads(squads: { home: MatchSquadPlayer[]; away: MatchSquadPlayer[] }, snapshot: MatchSnapshot) {
  // Draft/RAW 복원에는 당시의 화면 스냅샷도 포함되어 있다. 그 스냅샷으로 현재
  // 계약 기간 기준 명단을 덮으면 새로 합류한 선수(예: 사카)가 목록에서 사라진다.
  // 기록과 배치는 그대로 두되, 선택 목록의 정본만 이번 조회 결과로 유지한다.
  game.value.squads = squads
  game.value.matchSnapshot = snapshot
}

// ---- Player List 정렬 (포지션 / 등번호 / 이름) ----
type SortKey = 'position' | 'number' | 'name'
const sortKey = ref<SortKey>('position')
const POS_ORDER: Record<string, number> = { GK: 0, FW: 1, MF: 2, DF: 3 }

const sortedPlayers = computed(() => {
  const list = players.value.map(p => ({ p, id: p.playerId }))
  if (sortKey.value === 'number') {
    return list.sort((a, b) => Number(a.p.no) - Number(b.p.no))
  }
  if (sortKey.value === 'name') {
    return list.sort((a, b) => a.p.name.localeCompare(b.p.name))
  }
  // 포지션: GK → FW → MF → DF. 같은 포지션 안에서는 원래 순서를 유지한다(안정 정렬).
  return list.sort((a, b) => (POS_ORDER[a.p.pos ?? ''] ?? 9) - (POS_ORDER[b.p.pos ?? ''] ?? 9))
})

// ---- 포메이션 ----
const formations: Record<string, { label: string; slots: { x: number; y: number }[] }> = {
  '4-4-2': {
    label: '4-4-2', slots: [
      { x: 14, y: 72 }, { x: 38, y: 72 }, { x: 62, y: 72 }, { x: 86, y: 72 },
      { x: 14, y: 46 }, { x: 38, y: 46 }, { x: 62, y: 46 }, { x: 86, y: 46 },
      { x: 35, y: 18 }, { x: 65, y: 18 },
    ]
  },
  '4-3-3': {
    label: '4-3-3', slots: [
      { x: 14, y: 72 }, { x: 38, y: 72 }, { x: 62, y: 72 }, { x: 86, y: 72 },
      { x: 26, y: 48 }, { x: 50, y: 48 }, { x: 74, y: 48 },
      { x: 20, y: 16 }, { x: 50, y: 16 }, { x: 80, y: 16 },
    ]
  },
  '3-5-2': {
    label: '3-5-2', slots: [
      { x: 26, y: 75 }, { x: 50, y: 75 }, { x: 74, y: 75 },
      { x: 10, y: 46 }, { x: 30, y: 46 }, { x: 50, y: 46 }, { x: 70, y: 46 }, { x: 90, y: 46 },
      { x: 35, y: 16 }, { x: 65, y: 16 },
    ]
  },
  '4-2-3-1': {
    label: '4-2-3-1', slots: [
      { x: 14, y: 75 }, { x: 38, y: 75 }, { x: 62, y: 75 }, { x: 86, y: 75 },
      { x: 36, y: 54 }, { x: 64, y: 54 },
      { x: 18, y: 32 }, { x: 50, y: 32 }, { x: 82, y: 32 },
      { x: 50, y: 12 },
    ]
  },
}
const gkSlot = { x: 50, y: 94 }
const BENCH_COUNT = 15
const benchIds = Array.from({ length: BENCH_COUNT }, (_, i) => `b${i}`)

// 화면 전환/편집 중에만 의미있는 순수 UI 상태 — 공유할 필요 없어 로컬로 둔다.
const menuOpen = ref(false)
const activeSlot = ref<string | null>(null)
const matchInfoEditMode = ref(false)
const isLiveLobby = computed(() => game.value.halfStatus === 'H1' || game.value.halfStatus === 'H2')
const matchInfoEditable = computed(() => !isLiveLobby.value || matchInfoEditMode.value)
let formationSaveTimer: ReturnType<typeof setTimeout> | undefined
let lobbyClockTimer: ReturnType<typeof setInterval> | undefined

const outfieldSlots = computed(() => (game.value.formationKey ? formations[game.value.formationKey].slots : []))

// 라인업 입력 순서는 화면 좌표 기준이다: 좌상단 → 우측, 그 다음 아래 줄 → ... → GK → 벤치.
// formation 배열의 원래 인덱스(o0...)는 수비부터 시작하므로 그대로 쓰면 좌하단이 첫 슬롯이 된다.
const lineupAssignmentOrder = computed(() => {
  const field = outfieldSlots.value
    .map((slot, index) => ({ id: `o${index}`, x: slot.x, y: slot.y }))
    .sort((a, b) => (a.y - b.y) || (a.x - b.x))
    .map(item => item.id)
  return [...field, 'gk', ...benchIds]
})

function firstEmptyLineupSlot() {
  return lineupAssignmentOrder.value.find(id => game.value.assigned[id] === undefined) ?? null
}

function pickFormation(key: string) {
  if (!matchInfoEditable.value) return
  game.value.formationKey = key
  menuOpen.value = false
  // 포메이션 바꾸면 배치 초기화
  Object.keys(game.value.assigned).forEach(k => delete game.value.assigned[k])
  activeSlot.value = firstEmptyLineupSlot()
}

// 개발용 등급 토글. recorders/{uid}.level 연동 전까지 화면에서 직접 전환한다.
function toggleRecorderLevel() {
  game.value.recorderLevel = game.value.recorderLevel === 'basic' ? 'advanced' : 'basic'
  // 실시간 모드는 advanced 전용. basic 으로 내려가면 분석으로 고정한다.
  if (game.value.recorderLevel === 'basic') game.value.inputMode = '분석'
}

// 테스트용: 포메이션/진영/전체 슬롯을 랜덤으로 한 번에 채움
function fillTestData() {
  if (!matchInfoEditable.value) return
  const keys = Object.keys(formations)
  const key = keys[Math.floor(Math.random() * keys.length)]
  game.value.formationKey = key
  menuOpen.value = false
  Object.keys(game.value.assigned).forEach(k => delete game.value.assigned[k])

  const pool = [...players.value]
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
      ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }

  const gkPool = pool.filter(player => player.pos === 'GK')
  const outfieldPool = pool.filter(player => player.pos !== 'GK')
  const gk = gkPool[0]
  if (gk) game.value.assigned.gk = gk.playerId
  const outfieldIds = outfieldSlots.value.map((_, i) => `o${i}`)
  outfieldIds.forEach((id, i) => {
    const player = outfieldPool[i]
    if (player) game.value.assigned[id] = player.playerId
  })
  const alreadyAssigned = new Set(Object.values(game.value.assigned))
  const benchPool = pool.filter(player => !alreadyAssigned.has(player.playerId))
  benchIds.forEach((id, i) => {
    const player = benchPool[i]
    if (player) game.value.assigned[id] = player.playerId
  })

  activeSlot.value = null
  game.value.side = Math.random() < 0.5 ? 'left' : 'right'
}

const canSwitchInputTeam = computed(() => game.value.halfStatus === 'ready' || game.value.halfStatus === 'final')

function resetForInputTeam(team: 'home' | 'away') {
  // match/squad와 화면 공통 설정은 유지하되, 실제 입력 세션은 H/A별로 완전히 분리한다.
  // 점수는 경기 공통 정보라 현재 값을 이어받는다. records/lineup/half 상태는 절대 넘기지 않는다.
  const shared = {
    matchId: game.value.matchId,
    squads: game.value.squads,
    matchSnapshot: game.value.matchSnapshot,
    homeScore: game.value.homeScore,
    awayScore: game.value.awayScore,
    inputMode: game.value.inputMode,
    grassPattern: game.value.grassPattern,
    grassLines: game.value.grassLines,
    mirrored: game.value.mirrored,
    recorderLevel: game.value.recorderLevel,
  }
  Object.assign(game.value, {
    ...shared,
    team,
    formationKey: '',
    assigned: {},
    side: null,
    halfStatus: 'ready',
    seconds: 0,
    h1Seconds: 0,
    h2Seconds: 0,
    records: [],
    subs: [],
    cards: [],
    clockStartedAt: null,
    formationChanges: [],
    h1Locked: false,
    h2Locked: false,
  })
}

async function pickTeam(team: 'home' | 'away') {
  if (!canSwitchInputTeam.value || game.value.team === team || lifecycleBusy.value) return
  lifecycleBusy.value = true
  lifecycleError.value = ''
  const squads = game.value.squads
  const snapshot = game.value.matchSnapshot
  const previousTeam = game.value.team
  const previousFieldSide = game.value.side as FieldSide | null
  try {
    // 서버 lifecycle과 별개로, 현재 팀의 프론트 상태를 IndexedDB에 먼저 보존한다.
    // H/A는 `${gmId}_H`, `${gmId}_A`로 분리되므로 팀을 왕복해도 KPI/라인업/기록이 섞이지 않는다.
    await saveLocal(game.value, game.value.halfStatus === 'final')
    resetForInputTeam(team)
    const recovered = await recoverDraft(game.value)
    if (snapshot) applyCurrentMatchSquads(squads, snapshot)
    if (!recovered) {
      // 처음 입력하는 반대 팀은 완전히 새 세션으로 시작한다.
      // 먼저 입력하던 팀의 진영을 알고 있으면 즉시 반대로 잡고,
      // 새로고침/다른 기기처럼 로컬 상태가 없으면 RAW recording의 fieldSide를 사용한다.
      game.value.halfStatus = 'ready'
      game.value.formationKey = ''
      game.value.assigned = {}
      game.value.side = previousTeam !== team && previousFieldSide
        ? oppositeFieldSide(previousFieldSide)
        : counterpartFieldSide(team)
    }
    matchInfoEditMode.value = false
    subOpen.value = false
    menuOpen.value = false
    activeSlot.value = game.value.halfStatus === 'ready' && game.value.formationKey ? firstEmptyLineupSlot() : null
  } catch (error) {
    lifecycleError.value = error instanceof Error ? error.message : '입력 팀 상태를 전환하지 못했습니다.'
  } finally {
    lifecycleBusy.value = false
  }
}

// 탭-탭으로 자리 교환: 채워진 슬롯을 탭해 선택한 뒤 다른 슬롯을 탭하면 두 선수 자리가
// 바뀐다(목표가 빈 자리면 그냥 그리로 옮긴다). 태블릿 터치 기준 — 드래그 앤 드롭 대신
// 이 방식을 쓴다. 같은 슬롯을 다시 탭하면 선택 취소.
// 선택된 슬롯이 비어 있으면(=일반적인 배정 흐름) 기존처럼 activeSlot 만 바뀐다.
function clickSlot(id: string) {
  if (!matchInfoEditable.value) return
  if (id !== 'gk' && !game.value.formationKey) return
  const prev = activeSlot.value
  if (prev === id) {
    activeSlot.value = null
    return
  }
  if (prev && game.value.assigned[prev] !== undefined) {
    const fromPlayerId = game.value.assigned[prev]!
    const toPlayerId = game.value.assigned[id]
    // GK와 필드/벤치 슬롯을 서로 바꾸면 포지션 계약이 깨진다.
    // 빈 슬롯으로 옮기는 경우도 assignToSlot과 동일한 제약을 적용한다.
    const byId = new Map(players.value.map(player => [player.playerId, player]))
    const fromIsGk = byId.get(fromPlayerId)?.pos === 'GK'
    const toIsGk = toPlayerId === undefined ? fromIsGk : byId.get(toPlayerId)?.pos === 'GK'
    const allows = (slot: string, isGk: boolean) => slot.startsWith('b') || (slot === 'gk' ? isGk : !isGk)
    if (!allows(id, fromIsGk) || (toPlayerId !== undefined && !allows(prev, toIsGk))) {
      activeSlot.value = null
      return
    }
    if (toPlayerId === undefined) delete game.value.assigned[prev]
    else game.value.assigned[prev] = toPlayerId
    game.value.assigned[id] = fromPlayerId
    activeSlot.value = null
    return
  }
  activeSlot.value = id
}

const usedPlayerIds = computed(() => new Set(Object.values(game.value.assigned)))

function advanceAfter(slotId: string) {
  const order = lineupAssignmentOrder.value
  const currentIndex = order.indexOf(slotId)
  if (currentIndex < 0) {
    activeSlot.value = firstEmptyLineupSlot()
    return
  }
  activeSlot.value = order.slice(currentIndex + 1).find(id => game.value.assigned[id] === undefined) ?? null
}

function assignToSlot(slotId: string, playerId: string) {
  if (!matchInfoEditable.value) return
  const player = players.value.find(item => item.playerId === playerId)
  if (!player) return

  // 포지션이 맞지 않는 배정은 DID 입력 단계에서 되돌릴 수 없으므로
  // 여기서 차단한다. GK 슬롯에는 GK만, 필드/벤치에는 GK 외 선수만 둔다.
  const isGk = player.pos === 'GK'
  if (slotId === 'gk' && !isGk) return
  if (slotId.startsWith('o') && isGk) return

  // 이미 다른 슬롯에 배정된 선수는 중복 배정하지 않는다. 현재 슬롯에
  // 같은 선수가 있는 경우에는 그대로 두어 탭 입력이 무해하게 동작한다.
  const current = game.value.assigned[slotId]
  if (current === playerId) {
    advanceAfter(slotId)
    return
  }
  if (usedPlayerIds.value.has(playerId)) return
  game.value.assigned[slotId] = playerId
  advanceAfter(slotId)
}

function pickPlayer(playerId: string) {
  if (!matchInfoEditable.value || !activeSlot.value) return
  assignToSlot(activeSlot.value, playerId)
}

// ---- 드래그 앤 드롭으로 선수 배정 ----
// (Player List 패널에서 슬롯으로 끌어다 놓는 기존 기능. 마우스 기준이라 태블릿에서도
// 되는지는 별개 — 여기서는 손대지 않는다.)
const dragPlayerId = ref<string | null>(null)
function onDragStart(playerId: string) {
  if (!matchInfoEditable.value) return
  dragPlayerId.value = playerId
}

function recordFormationChange() {
  if (game.value.halfStatus !== 'H1' && game.value.halfStatus !== 'H2') return
  if (!game.value.matchSnapshot) return
  if (formationSaveTimer) clearTimeout(formationSaveTimer)
  formationSaveTimer = setTimeout(() => {
    const half = game.value.halfStatus
    if (half !== 'H1' && half !== 'H2') return
    game.value.formationChanges.push({
      half,
      seconds: game.value.seconds,
      formationKey: game.value.formationKey,
      assigned: { ...game.value.assigned },
    })
    void saveDraft(game.value)
  }, 300)
}

watch([() => game.value.formationKey, () => game.value.assigned], recordFormationChange, { deep: true })
function onDrop(slotId: string) {
  if (!matchInfoEditable.value || dragPlayerId.value === null) return
  if (slotId !== 'gk' && !slotId.startsWith('b') && !game.value.formationKey) return
  assignToSlot(slotId, dragPlayerId.value)
  dragPlayerId.value = null
}

const benchFilledCount = computed(() => benchIds.filter(id => game.value.assigned[id] !== undefined).length)
const starterCount = computed(() => outfieldSlots.value.filter((_, index) => game.value.assigned[`o${index}`]).length + (game.value.assigned.gk ? 1 : 0))
// 후보는 선택 사항이다. 포메이션의 필드 10명과 GK만 확정되면 시작할 수 있다.
const canStart = computed(() => !!game.value.formationKey && !!game.value.side && starterCount.value === outfieldSlots.value.length + 1)

function removeFromSlot(slotId: string) {
  if (!matchInfoEditable.value || game.value.assigned[slotId] === undefined) return
  delete game.value.assigned[slotId]
  activeSlot.value = slotId
}

// DidInput 으로 넘어갈 때 공통으로 실어보내는 쿼리.
// date/league/round/stadium/time 은 TeamSelection 표시에만 쓰지만, DidInput 은 이 값을
// 그대로 들고 있다가 전반/후반 종료 시 TeamSelection 으로 돌아올 때 되돌려준다.
// editReturnStatus: 수정 화면에서 "대기방으로 나가기"를 눌렀을 때 되돌아갈 halfStatus.
// 이미 끝난 half 를 고치러 온 거면 'H1_done'/'H2_done' 으로, 정지 중이던 half 를
// 고치러 온 거면 'H1'/'H2' 로 넘긴다 — 그래야 나갈 때 원래 있던 화면으로 정확히 복귀한다.
function didInputQuery(resumeHalf?: '전반' | '후반', edit?: boolean, editReturnStatus?: HalfStatus, finalCorrection = false) {
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
    ...(finalCorrection ? { finalCorrection: '1' } : {}),
  }
}

function startFirstHalf() {
  if (!canStart.value) return
  if (!confirm('전반전을 시작하시겠습니까?')) return
  game.value.halfStatus = 'H1'
  game.value.seconds = 0 // 새 half 는 0초부터
  game.value.clockStartedAt = Date.now()
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
// 진행 중 대기방에서는 경기 정보가 기본 잠금 상태다.
// 이 버튼을 눌렀을 때만 라인업/포메이션/진영을 바꿀 수 있고, 변경 완료 시 Draft를 다시 저장한다.
async function toggleMatchInfoEdit() {
  lifecycleError.value = ''
  if (!matchInfoEditMode.value) {
    matchInfoEditMode.value = true
    subOpen.value = false
    menuOpen.value = false
    activeSlot.value = firstEmptyLineupSlot()
    return
  }

  lifecycleBusy.value = true
  try {
    if (!await saveDraft(game.value)) throw new Error('경기 정보 변경 내용을 Firestore Draft에 저장하지 못했습니다.')
    matchInfoEditMode.value = false
    activeSlot.value = null
    menuOpen.value = false
  } catch (error) {
    lifecycleError.value = error instanceof Error ? error.message : '경기 정보 변경 저장에 실패했습니다.'
  } finally {
    lifecycleBusy.value = false
  }
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
async function startSecondHalf() {
  if (!confirm('후반전을 시작하시겠습니까?')) return
  lifecycleBusy.value = true
  lifecycleError.value = ''
  try {
    // Advanced only: H1 becomes raw at the instant H2 starts.
    if (game.value.recorderLevel === 'advanced') await promoteH1(game.value)
    else if (!await saveDraft(game.value)) throw new Error('네트워크 연결 후 다시 시도하세요. Draft는 이 기기에 저장되었습니다.')
  } catch (error) {
    lifecycleError.value = error instanceof Error ? error.message : '전반 Draft 처리에 실패했습니다.'
    return
  } finally {
    lifecycleBusy.value = false
  }
  game.value.halfStatus = 'H2'
  game.value.seconds = 0
  game.value.clockStartedAt = Date.now()
  navigateTo({ path: '/DidInput', query: didInputQuery('후반') })
}

onMounted(async () => {
  let loaded: { squads: { home: MatchSquadPlayer[]; away: MatchSquadPlayer[] }; snapshot: MatchSnapshot } | undefined
  try {
    loaded = await loadSquads()
  } catch (error) {
    lifecycleError.value = error instanceof Error ? error.message : '선수 명단을 불러오지 못했습니다.'
  }
  const recovered = await recoverDraft(game.value)
  if (loaded) applyCurrentMatchSquads(loaded.squads, loaded.snapshot)
  // 현재 팀에 저장된 세션이 없는 신규 입력이라면, 이미 RAW가 있는 상대 팀의
  // 진영을 기준으로 자동 반대 진영을 지정한다. 저장된 세션의 값은 절대 덮지 않는다.
  if (!recovered && game.value.halfStatus === 'ready') applyOpponentFieldSideDefault(game.value.team)
  if (game.value.halfStatus === 'ready' && game.value.formationKey && !activeSlot.value) {
    activeSlot.value = firstEmptyLineupSlot()
  }
  lobbyClockTimer = setInterval(() => {
    if ((game.value.halfStatus === 'H1' || game.value.halfStatus === 'H2') && game.value.clockStartedAt) {
      game.value.seconds = Math.max(game.value.seconds, Math.floor((Date.now() - game.value.clockStartedAt) / 1000))
    }
  }, 250)
})

onUnmounted(() => {
  if (formationSaveTimer) clearTimeout(formationSaveTimer)
  if (lobbyClockTimer) clearInterval(lobbyClockTimer)
})

async function finishMatch() {
  if (!confirm('경기를 종료하시겠습니까?')) return
  lifecycleBusy.value = true
  lifecycleError.value = ''
  game.value.halfStatus = 'final'
  game.value.clockStartedAt = null
  try {
    if (game.value.recorderLevel === 'advanced') await finalizeAdvanced(game.value)
    else if (!await saveDraft(game.value)) throw new Error('네트워크 연결 후 다시 시도하세요. Draft는 이 기기에 저장되었습니다.')
  } catch (error) {
    game.value.halfStatus = 'H2_done'
    lifecycleError.value = error instanceof Error ? error.message : '최종 데이터 처리에 실패했습니다.'
    return
  } finally {
    lifecycleBusy.value = false
  }
}

async function editFinal() {
  lifecycleBusy.value = true
  lifecycleError.value = ''
  try {
    if (game.value.recorderLevel === 'advanced') await restoreFinalRaw(game.value)
    else game.value.halfStatus = 'H2_done'
    navigateTo({ path: '/DidInput', query: didInputQuery('후반', true, 'H2_done', true) })
  } catch (error) {
    lifecycleError.value = error instanceof Error ? error.message : '최종 RAW를 수정용 Draft로 복원하지 못했습니다.'
  } finally {
    lifecycleBusy.value = false
  }
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
  H3: '연장 전반 진행중',
  H3_done: '연장 전반 종료',
  H4: '연장 후반 진행중',
  H4_done: '연장 후반 종료',
  final: '경기 종료',
}[game.value.halfStatus]))

// KPI 는 저장하지 않고 기록(game.records)으로부터 항상 다시 계산한다.
// 현재는 한 번에 한 팀(game.team)의 기록만 입력하므로, 그 팀 쪽 칸에만 값을 채운다.
const kpiRecords = computed(() => {
  if (kpiHalf.value === 'all') return game.value.records
  return game.value.records.filter(r => (r.half ?? 'H1') === kpiHalf.value)
})
const kpiValues = computed(() => {
  const { paths, flags } = computeAttackPaths(kpiRecords.value, { closeTrailing: true })
  let tap = 0, dap = 0, dapSc = 0, sht = 0, gol = 0
  for (const f of flags.values()) {
    if (f.isTap) tap++
    if (f.isDap) { dap++; if (f.isDapS) dapSc++ }
    if (f.isSht) sht++
    if (f.isGol) gol++
  }
  const dtp = paths.filter(p => p.ptype === 'DTP' || p.ptype === 'STP').length
  const bap = computeBap(kpiRecords.value).length
  return {
    TAP: tap, DAP: dap, DTP: dtp, Shoot: sht, Goal: gol,
    ASR: dap ? Math.round((dapSc / dap) * 100) : 0,
    SSR: sht ? Math.round((gol / sht) * 100) : 0,
    BAP: bap,
  }
})

// ---- 입력 오류 감지 ----
// E-Time: 같은 half 안에서 같은 초(seconds)에 두 번 이상 입력된 레코드.
// E-Player: DAP 로 판정됐는데(=DidInput 표에서 선수 선택 버튼이 뜨는데) 선수를 아직 안 고른 레코드.
function fmtErrTime(sec: number) {
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
}
const kpiErrors = computed(() => {
  const { flags } = computeAttackPaths(kpiRecords.value, { closeTrailing: true })

  // DidInput 표의 No. 열과 같은 기준(그 half 레코드만 추린 순서)으로 번호를 매겨,
  // 오류 목록에 찍힌 No.가 수정 화면에서 보이는 행 번호와 그대로 일치하게 한다.
  const numberByHalf: Record<'H1' | 'H2', Map<string, number>> = { H1: new Map(), H2: new Map() }
  for (const halfKey of ['H1', 'H2'] as const) {
    game.value.records
      .filter(r => (r.half ?? 'H1') === halfKey)
      .forEach((r, i) => numberByHalf[halfKey].set(r.id, i + 1))
  }

  const timeGroups = new Map<string, { half: 'H1' | 'H2'; seconds: number; ids: string[] }>()
  for (const r of kpiRecords.value) {
    const halfKey = r.half ?? 'H1'
    const key = `${halfKey}_${r.seconds}`
    if (!timeGroups.has(key)) timeGroups.set(key, { half: halfKey, seconds: r.seconds, ids: [] })
    timeGroups.get(key)!.ids.push(r.id)
  }
  const timeList = [...timeGroups.values()]
    .filter(g => g.ids.length > 1)
    .map(g => ({
      half: g.half,
      time: fmtErrTime(g.seconds),
      nos: g.ids.map(id => numberByHalf[g.half].get(id) ?? 0).sort((a, b) => a - b),
    }))
    .sort((a, b) => (a.half === b.half ? 0 : a.half === 'H1' ? -1 : 1) || a.time.localeCompare(b.time))

  const playerList = kpiRecords.value
    .filter(r => flags.get(r.id)?.isDap && !r.playerId)
    .map(r => {
      const halfKey = r.half ?? 'H1'
      return { half: halfKey, no: numberByHalf[halfKey].get(r.id) ?? 0, time: fmtErrTime(r.seconds) }
    })
    .sort((a, b) => (a.half === b.half ? 0 : a.half === 'H1' ? -1 : 1) || a.no - b.no)

  return { eTime: timeList.length, ePlayer: playerList.length, timeList, playerList }
})
// 사이드바에서 E-Time/E-Player 행을 누르면 오류난 시간대/No.를 펼쳐 보여준다.
const errorDetailOpen = ref<'time' | 'player' | null>(null)
function toggleErrorDetail(kind: 'time' | 'player') {
  errorDetailOpen.value = errorDetailOpen.value === kind ? null : kind
}

// ---- 잔디선택 ----
// 레거시 APK 이미지 7종(p000/p1xx/p2xx)과 동일한 조합. 상세는 app/utils/grass.ts.
// 여기서 고른 값을 DidInput 으로 넘겨 경기장 배경에 그대로 적용한다.
const grassOpen = ref(false)
const grassPanelRef = ref<HTMLElement | null>(null)
const playerPanelRef = ref<HTMLElement | null>(null)
const grassBg = computed(() => grassBackground(game.value.grassPattern, game.value.grassLines))
function openGrass() {
  grassOpen.value = !grassOpen.value
}
// 잔디 팝업/선수교체 패널 바깥을 누르면 각각 닫는다. 선수교체 토글 버튼도 toolPanel
// 안에 있어서, 그 버튼을 눌러 직접 여닫을 때는 아래 취소 로직이 끼어들지 않는다.
function handleOutsideClick(e: PointerEvent) {
  const target = e.target as Node
  if (grassOpen.value && grassPanelRef.value && !grassPanelRef.value.contains(target)) grassOpen.value = false
  if (subOpen.value && playerPanelRef.value && !playerPanelRef.value.contains(target) && !grassPanelRef.value?.contains(target)) {
    cancelSub()
  }
}
onMounted(() => document.addEventListener('pointerdown', handleOutsideClick))
onUnmounted(() => document.removeEventListener('pointerdown', handleOutsideClick))

// ---- 선수교체 ----
// PPT 슬라이드 37: 좌측 = 교체 아웃 선수, 우측 = 교체 투입 선수, 선택 후 저장.
// Player List 자리에서 화면을 전환한다.
const subOpen = ref(false)
const cardForPlayer = (playerId: string) => game.value.cards.filter(c => c.player === playerId)
const subOut = ref<string | null>(null) // 빠질 선수의 슬롯 id (선발)
const subIn = ref<string | null>(null) //  들어올 선수의 슬롯 id (후보)
const subTimeMinute = ref(0)
const subTimeSecond = ref(0)
const subTimeTotal = computed(() => subTimeMinute.value * 60 + subTimeSecond.value)
function bumpSubMinute(delta: number) { subTimeMinute.value = Math.max(0, subTimeMinute.value + delta) }
function bumpSubSecond(delta: number) {
  let next = subTimeSecond.value + delta
  let minute = subTimeMinute.value
  if (next < 0) { next = 59; minute = Math.max(0, minute - 1) }
  if (next > 59) { next = 0; minute += 1 }
  subTimeSecond.value = next
  subTimeMinute.value = minute
}

const byNo = (a: { p: { no: string } | null }, b: { p: { no: string } | null }) => Number(a.p?.no ?? 0) - Number(b.p?.no ?? 0)
const starterSlots = computed(() =>
  [...outfieldSlots.value.map((_, i) => `o${i}`), 'gk']
    .filter(id => game.value.assigned[id] !== undefined)
    .map(id => ({ id, p: playerAt(id) }))
    .sort(byNo)
)
const benchSlots = computed(() =>
  benchIds
    .filter(id => game.value.assigned[id] !== undefined)
    .map(id => ({ id, p: playerAt(id) }))
    .sort(byNo)
)

// 교체는 고르는 즉시 반영해서 왼쪽 포메이션에 바로 보이게 한다.
// 취소를 누르면 열었을 때 상태로 되돌리기 위해 스냅샷을 떠둔다.
let subSnapshot: Record<string, string> | null = null
const subDragId = ref<string | null>(null)
/**
 * 저장 전, 지금 화면에서 진행 중인 교체를 일어난 순서 그대로 쌓아둔다. 스냅샷과의
 * 차이만 비교하면 같은 자리를 두 번 이상 재교체했을 때 중간 기록이 사라지므로,
 * swapSlots 에서 교체가 일어날 때마다 바로 한 건씩 추가한다.
 */
const pendingSubs = ref<SubRecord[]>([])

const isBenchSlot = (id: string) => id.startsWith('b')

function openSub() {
  if (subOpen.value) { closeSub(); return }
  // 다른 팝업/슬롯 선택 상태를 닫고 교체 패널을 최상위 입력 상태로 연다.
  grassOpen.value = false
  menuOpen.value = false
  activeSlot.value = null
  subSnapshot = { ...game.value.assigned }
  subOpen.value = true
  subOut.value = null
  subIn.value = null
  pendingSubs.value = []
  const moment = subMoment()
  subTimeMinute.value = moment ? Math.floor(moment.seconds / 60) : 0
  subTimeSecond.value = moment ? moment.seconds % 60 : 0
}
function closeSub() {
  subOpen.value = false
  subSnapshot = null
  subOut.value = null
  subIn.value = null
  subDragId.value = null
  pendingSubs.value = []
}
/**
 * 선발 ↔ 후보 자리를 맞바꾼다. 즉시 반영되므로 포메이션에 바로 보인다.
 * 교체 한 건을 그 즉시 pendingSubs 에 쌓는다 — 같은 자리를 다시 교체(재교체)해도
 * 이전 기록이 사라지지 않고 목록에 별도 항목으로 남는다.
 */
function swapSlots(a: string, b: string) {
  const starterSlot = isBenchSlot(a) ? b : a
  const benchSlot = isBenchSlot(a) ? a : b
  const outPlayer = game.value.assigned[starterSlot]!
  const inPlayer = game.value.assigned[benchSlot]!
  const tmp = game.value.assigned[a]!
  game.value.assigned[a] = game.value.assigned[b]!
  game.value.assigned[b] = tmp
  subOut.value = null
  subIn.value = null
  const moment = subMoment()
  if (moment) pendingSubs.value = [...pendingSubs.value, { half: moment.half, seconds: subTimeTotal.value, outPlayer, inPlayer }]
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
/**
 * 이 화면에서 교체를 "언제" 한 것으로 볼지.
 * 대기 화면이라 시계가 안 도니까 halfStatus 로 시점을 정한다.
 *  - ready        : 아직 경기 전 → 보통 후반 막판에 나올 교체이므로 후반 45:00을 기본값으로 미리 채운다.
 *  - final        : 경기가 끝났으니 교체가 아니다. 기록 안 남김(null)
 *  - H1/H2/H3/H4  : 그 half 를 정지시켜 두고 나온 상태 → 정지된 그 시각
 *  - H1_done      : 하프타임 교체 → 후반으로 들어오되, ready 와 같은 이유로 45:00을 기본값으로 채운다
 *  - H2_done      : 연장 전반 시작(0초)
 */
function subMoment(): { half: Half; seconds: number } | null {
  const st = game.value.halfStatus
  if (st === 'ready') return { half: 'H2', seconds: 45 * 60 }
  if (st === 'final') return null
  if (st === 'H1_done') return { half: 'H2', seconds: 45 * 60 }
  if (st === 'H2_done') return { half: 'H3', seconds: 0 }
  if (st === 'H3_done') return { half: 'H4', seconds: 0 }
  if (st === 'H4_done') return null
  return { half: st, seconds: game.value.seconds }
}

/** 저장: 미리보기로 떠 있던 교체를 실제 기록(game.subs)으로 확정하고 닫는다. */
function saveSub() {
  if (pendingSubs.value.length) {
    game.value.subs = [...game.value.subs, ...pendingSubs.value].sort(
      (a, b) => HALF_ORDER[a.half]! * 100000 + a.seconds - (HALF_ORDER[b.half]! * 100000 + b.seconds)
    )
  }
  closeSub()
}
/** 아직 저장 전인 교체 미리보기 한 건을 되돌린다 — 배치만 원래대로, game.subs 는 손대지 않는다. */
function cancelPendingSub(index: number) {
  const sub = pendingSubs.value[index]
  if (!sub) return
  const entries = Object.entries(game.value.assigned)
  const inSlot = entries.find(([, idx]) => idx === sub.inPlayer)?.[0]
  const outSlot = entries.find(([, idx]) => idx === sub.outPlayer)?.[0]
  if (inSlot && outSlot) {
    game.value.assigned = { ...game.value.assigned, [inSlot]: sub.outPlayer, [outSlot]: sub.inPlayer }
  }
  pendingSubs.value = pendingSubs.value.filter((_, i) => i !== index)
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
  const playerId = game.value.assigned[id]
  return playerId === undefined ? null : players.value.find(player => player.playerId === playerId) ?? null
}

// ---- 교체 이력 표시 — DidInput.vue 의 교체 패널과 같은 형식으로 보여준다 ----
const HALF_ORDER: Record<string, number> = { H1: 1, H2: 2, H3: 3, H4: 4 }
const subHalfLabel: Record<string, string> = { H1: '전반', H2: '후반', H3: '연장전반', H4: '연장후반' }
function fmtTime(sec: number) {
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
}
function playerLabel(playerId: string) {
  const player = players.value.find(item => item.playerId === playerId)
  return player ? `${player.no} ${player.name}` : '-'
}
/** 잘못 저장된 교체 되돌리기 — 자리도 원래대로 돌려놓는다(DidInput.vue 의 undoSub 와 동일 로직) */
function undoSub(index: number) {
  const s = game.value.subs[index]
  if (!s) return
  const entries = Object.entries(game.value.assigned)
  const inSlot = entries.find(([, idx]) => idx === s.inPlayer)?.[0]
  const outSlot = entries.find(([, idx]) => idx === s.outPlayer)?.[0]
  if (inSlot && outSlot) {
    game.value.assigned = { ...game.value.assigned, [inSlot]: s.outPlayer, [outSlot]: s.inPlayer }
  }
  game.value.subs = game.value.subs.filter((_, i) => i !== index)
}
</script>

<template>
  <div class="page">
    <div class="bg" />
    <button type="button" class="topBack" @click="navigateTo('/schedule')">← 경기 선택</button>
    <div class="frame">
      <aside class="sidebar">
        <div class="matchDate">{{ match.date.replaceAll('-', '.') }}</div>
        <div class="teamPick">입력할 팀 선택</div>
        <div class="teams">
          <button class="club" :class="{ active: game.team === 'home' }"
            :disabled="!canSwitchInputTeam || lifecycleBusy" @click="pickTeam('home')">
            <div class="crest homeCrest">V</div><span>{{ match.home }}</span>
          </button>
          <span class="versus">VS</span>
          <button class="club" :class="{ active: game.team === 'away' }"
            :disabled="!canSwitchInputTeam || lifecycleBusy" @click="pickTeam('away')">
            <div class="crest awayCrest">RM</div><span>{{ match.away }}</span>
          </button>
        </div>
        <div class="score">{{ game.homeScore }} : {{ game.awayScore }}</div>
        <div class="status">{{ statusLabel }}</div>
        <div class="matchMeta">{{ match.time }} | {{ match.league }} | {{ match.round }}</div>
        <div class="stadium">{{ match.stadium }}</div>
        <div class="kpiHalfToggle">
          <button :class="{ active: kpiHalf === 'all' }" @click="kpiHalf = 'all'">전체</button>
          <button :class="{ active: kpiHalf === 'H1' }" @click="kpiHalf = 'H1'">전반</button>
          <button :class="{ active: kpiHalf === 'H2' }" @click="kpiHalf = 'H2'">후반</button>
        </div>
        <div class="kpis">
          <div v-for="key in kpis" :key="key" class="kpiRow">
            <span>{{ game.team === 'home' ? kpiValues[key] : 0 }}</span><b>{{ key }}</b><span>{{ game.team === 'away' ?
              kpiValues[key] : 0 }}</span>
          </div>
        </div>
        <div class="kpiErrors">
          <div class="errRow" :class="{ has: kpiErrors.eTime > 0, open: errorDetailOpen === 'time' }"
            @click="toggleErrorDetail('time')"><span>E-Time</span><b>{{ kpiErrors.eTime }}개</b></div>
          <div v-if="errorDetailOpen === 'time'" class="errDetail">
            <div v-if="kpiErrors.timeList.length" class="errDetailGrid">
              <span v-for="(g, i) in kpiErrors.timeList" :key="i" class="errDetailItem">{{ g.half }} {{ g.time }}{{ i <
                kpiErrors.timeList.length - 1 ? ',' : '' }}</span>
            </div>
            <span v-else class="errDetailEmpty">중복된 시간 없음</span>
          </div>
          <div class="errRow" :class="{ has: kpiErrors.ePlayer > 0, open: errorDetailOpen === 'player' }"
            @click="toggleErrorDetail('player')"><span>E-Player</span><b>{{ kpiErrors.ePlayer }}개</b></div>
          <div v-if="errorDetailOpen === 'player'" class="errDetail">
            <div v-if="kpiErrors.playerList.length" class="errDetailGrid">
              <span v-for="(p, i) in kpiErrors.playerList" :key="i" class="errDetailItem">{{ p.half }} {{ p.time }}{{ i
                < kpiErrors.playerList.length - 1 ? ',' : '' }}</span>
            </div>
            <span v-else class="errDetailEmpty">선수 미입력 없음</span>
          </div>
        </div>
        <button class="testBtn" :disabled="!matchInfoEditable" @click="fillTestData">TEST</button>
        <!-- 개발용 등급 토글. 실제로는 recorders/{uid}.level 을 읽어와야 하지만
             그 연동 전까지 여기서 basic/advanced 화면을 바로 바꿔가며 확인한다. -->
        <button class="levelToggle" :class="{ basic: game.recorderLevel === 'basic' }" @click="toggleRecorderLevel">
          등급: {{ game.recorderLevel === 'basic' ? 'BASIC' : 'ADVANCED' }}
        </button>
        <button class="backBtn" @click="navigateTo('/schedule')">◀ 이전화면으로</button>
      </aside>
      <main class="content">
        <h1>Player List</h1>
        <div class="workspace">
          <div class="topRow">
            <section class="formationPanel">
              <div class="selectBar" :class="{ open: menuOpen, locked: !matchInfoEditable }"
                @click="matchInfoEditable && (menuOpen = !menuOpen)">
                <span>{{ game.formationKey ? `${formations[game.formationKey].label} 포메이션` : '포메이션을 선택하세요'
                }}</span><span>⌄</span>
                <div v-if="menuOpen" class="menu" @click.stop>
                  <div v-for="(f, key) in formations" :key="key" class="menuItem" @click="pickFormation(key)">{{ f.label
                  }}</div>
                </div>
              </div>
              <div class="pitch">
                <div class="halfway" />
                <div class="centerCircle" />
                <div class="penaltyArc" />
                <div class="penaltyBox" />
                <div class="penaltySpot" />
                <div class="goalBox" />
                <div class="goalPost" />
                <div class="cornerArc left" />
                <div class="cornerArc right" />
                <template v-if="game.formationKey">
                  <button v-for="(s, i) in outfieldSlots" :key="`o${i}`" class="slot"
                    :class="[{ active: activeSlot === `o${i}`, filled: game.assigned[`o${i}`] !== undefined }, playerAt(`o${i}`)?.pos?.toLowerCase()]"
                    :style="{ left: s.x + '%', top: s.y + '%' }" :disabled="!matchInfoEditable"
                    @click="clickSlot(`o${i}`)" @dblclick.prevent="removeFromSlot(`o${i}`)" @dragover.prevent
                    @drop="onDrop(`o${i}`)">{{ playerAt(`o${i}`)?.no ?? '' }}</button>
                  <button class="slot gk"
                    :class="{ active: activeSlot === 'gk', filled: game.assigned['gk'] !== undefined }"
                    :style="{ left: gkSlot.x + '%', top: gkSlot.y + '%' }" :disabled="!matchInfoEditable"
                    @click="clickSlot('gk')" @dblclick.prevent="removeFromSlot('gk')" @dragover.prevent
                    @drop="onDrop('gk')">{{ playerAt('gk')?.no ?? 'GK' }}</button>
                </template>
                <div v-else class="shirt" />
              </div>
              <div v-if="game.formationKey" class="benchHeader">
                <span>후보</span><b>{{ benchFilledCount }} / {{ BENCH_COUNT }}</b>
              </div>
              <div v-if="game.formationKey" class="bench">
                <button v-for="id in benchIds" :key="id" class="slot benchSlot"
                  :class="[{ active: activeSlot === id, filled: game.assigned[id] !== undefined }, playerAt(id)?.pos?.toLowerCase()]"
                  :disabled="!matchInfoEditable" @click="clickSlot(id)" @dblclick.prevent="removeFromSlot(id)"
                  @dragover.prevent @drop="onDrop(id)">{{ playerAt(id)?.no ?? '' }}</button>
              </div>
            </section>
            <section class="playerPanel" ref="playerPanelRef">
              <template v-if="!subOpen">
                <div class="tabs">
                  <button :class="{ off: sortKey !== 'position' }" @click="sortKey = 'position'">Position</button>
                  <button :class="{ off: sortKey !== 'number' }" @click="sortKey = 'number'">Number</button>
                  <button :class="{ off: sortKey !== 'name' }" @click="sortKey = 'name'">Name</button>
                </div>
                <div class="playerGrid">
                  <button v-for="{ p, id } in sortedPlayers" :key="id" class="player"
                    :class="[p.pos?.toLowerCase(), { used: usedPlayerIds.has(id), pickable: !usedPlayerIds.has(id) }]"
                    :draggable="matchInfoEditable && !usedPlayerIds.has(id)"
                    :disabled="!matchInfoEditable || usedPlayerIds.has(id)" @click="pickPlayer(id)"
                    @dragstart="onDragStart(id)"><strong>{{ p.no }}</strong><span>{{ p.name }}</span></button>
                  <div v-for="i in 13" :key="`e${i}`" class="player empty" />
                </div>
                <div class="legend"><span class="gk">GK</span><span class="fw">FW</span><span class="mf">MF</span><span
                    class="df">DF</span></div>
              </template>

              <!-- 선수 교체: 좌 = OUT(선발), 우 = IN(후보) -->
              <template v-else>
                <div class="tabs subTabs">
                  <button class="subTitle">선수 교체</button>
                </div>
                <div class="subTimeRow">
                  <span>교체 시각</span>
                  <button class="subTimeBtn" @click="bumpSubMinute(-1)">−</button>
                  <strong>{{ String(subTimeMinute).padStart(2, '0') }}</strong>
                  <button class="subTimeBtn" @click="bumpSubMinute(1)">＋</button>
                  <b>:</b>
                  <button class="subTimeBtn" @click="bumpSubSecond(-1)">−</button>
                  <strong>{{ String(subTimeSecond).padStart(2, '0') }}</strong>
                  <button class="subTimeBtn" @click="bumpSubSecond(1)">＋</button>
                </div>
                <div class="subCols">
                  <div class="subCol">
                    <div class="subColHead out">교체 OUT · 선발</div>
                    <div class="subList" @dragover.prevent>
                      <button v-for="s in starterSlots" :key="s.id" class="subItem"
                        :class="[s.p?.pos?.toLowerCase(), { on: subOut === s.id, dragging: subDragId === s.id }]"
                        draggable="true" @click="pickOut(s.id)" @dragstart="onSubDragStart(s.id)"
                        @dragend="subDragId = null" @dragover.prevent @drop="onSubDrop(s.id)"><strong>{{ s.p?.no }} <i
                            v-if="cardForPlayer(game.assigned[s.id]!).some(c => c.card === 'R')">🟥</i><i
                            v-else-if="cardForPlayer(game.assigned[s.id]!).length">🟨</i></strong><span>{{ s.p?.name
                            }}</span></button>
                    </div>
                  </div>
                  <div class="subCol">
                    <div class="subColHead in">교체 IN · 후보</div>
                    <div class="subList" @dragover.prevent>
                      <button v-for="s in benchSlots" :key="s.id" class="subItem"
                        :class="[s.p?.pos?.toLowerCase(), { on: subIn === s.id, dragging: subDragId === s.id }]"
                        draggable="true" @click="pickIn(s.id)" @dragstart="onSubDragStart(s.id)"
                        @dragend="subDragId = null" @dragover.prevent @drop="onSubDrop(s.id)"><strong>{{ s.p?.no
                        }}</strong><span>{{ s.p?.name }}</span></button>
                      <div v-if="!benchSlots.length" class="subEmpty">후보 선수가 없습니다</div>
                    </div>
                  </div>
                </div>

                <div class="subHistory">
                  <div class="subHistHead">
                    <span class="hHalf">Half</span>
                    <span class="hTime">Time</span>
                    <span class="hP">Out</span>
                    <span class="hP">In</span>
                    <span class="hAct"></span>
                  </div>
                  <div class="subHistBody">
                    <div v-if="!game.subs.length && !pendingSubs.length" class="subHistEmpty">교체 기록이 없습니다.</div>
                    <div v-for="(s, i) in game.subs" :key="`saved${i}`" class="subHistRow">
                      <span class="hHalf">{{ subHalfLabel[s.half] }}</span>
                      <span class="hTime">{{ fmtTime(s.seconds) }}</span>
                      <span class="hP outP">{{ playerLabel(s.outPlayer) }}</span>
                      <span class="hP inP">{{ playerLabel(s.inPlayer) }}</span>
                      <span class="hAct"><button class="subUndo" @click="undoSub(i)">취소</button></span>
                    </div>
                    <div v-for="(s, i) in pendingSubs" :key="`pending${i}`" class="subHistRow pending">
                      <span class="hHalf">{{ subHalfLabel[s.half] }}</span>
                      <span class="hTime">{{ fmtTime(s.seconds) }}</span>
                      <span class="hP outP">{{ playerLabel(s.outPlayer) }}</span>
                      <span class="hP inP">{{ playerLabel(s.inPlayer) }}</span>
                      <span class="hAct"><button class="subUndo" @click="cancelPendingSub(i)">취소</button></span>
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
                <div class="miniHalf" />
                <div class="miniCircle" />
                <div class="miniPenalty left" />
                <div class="miniPenalty right" />
                <div class="miniGoal left" />
                <div class="miniGoal right" />
                <div class="miniBox leftBox" :class="{ active: game.side === 'left', locked: !matchInfoEditable }"
                  @click="matchInfoEditable && (game.side = 'left')" />
                <div class="miniBox rightBox" :class="{ active: game.side === 'right', locked: !matchInfoEditable }"
                  @click="matchInfoEditable && (game.side = 'right')" />
              </div>
            </section>
            <section class="toolPanel" ref="grassPanelRef">
              <div class="toolGrid">
                <button class="toolBtn" :class="{ on: grassOpen }" @click="openGrass">
                  <span class="toolIcon grassIcon" :style="{ background: grassBg }" />
                  <span class="toolLabel">잔디선택</span>
                </button>
                <button type="button" class="toolBtn" :class="{ on: subOpen }" @click.stop="openSub" @pointerup.stop>
                  <span class="toolIcon subIcon">⇄</span>
                  <span class="toolLabel">선수교체</span>
                </button>
              </div>

              <div v-if="grassOpen" class="grassPop">
                <div class="popRow">
                  <span class="popLabel">잔디 패턴(중계화면 왼쪽에서부터)</span>
                  <div class="popOpts">
                    <button v-for="g in GRASS_PATTERNS" :key="g.value" class="popBtn"
                      :class="{ on: game.grassPattern === g.value }" @click="game.grassPattern = g.value">{{ g.label
                      }}</button>
                  </div>
                </div>
                <div class="popRow">
                  <span class="popLabel">잔디 라인(하프기준)</span>
                  <div class="popOpts">
                    <button v-for="n in GRASS_LINE_OPTIONS" :key="n" class="popBtn"
                      :class="{ on: game.grassLines === n }" :disabled="game.grassPattern === 0"
                      @click="game.grassLines = n">{{ n }}줄</button>
                  </div>
                </div>
                <div class="popPreview" :style="{ background: grassBg }" />
                <button class="popOk" @click="grassOpen = false">확인</button>
              </div>
            </section>
            <section class="startPanel">
              <template v-if="game.halfStatus === 'ready'">
                <div v-if="game.recorderLevel === 'advanced'" class="modeToggle">
                  <button class="modeBtn" :class="{ on: game.inputMode === '분석' }"
                    @click="game.inputMode = '분석'">분석<small>정지 가능</small></button>
                  <button class="modeBtn" :class="{ on: game.inputMode === '실시간' }"
                    @click="game.inputMode = '실시간'">실시간<small>정지 불가</small></button>
                </div>
                <p>아래의 버튼을 터치하시면<br><b>경기데이터 입력이 시작됩니다.</b></p>
                <button class="startBtn" :disabled="!canStart" @click="startFirstHalf">전반전 시작</button>
              </template>
              <template v-else-if="game.halfStatus === 'H1_done'">
                <p>전반 기록을 확인하세요<br><b>기록을 수정하거나 후반전을 시작할 수 있습니다.</b></p>
                <p v-if="game.recorderLevel === 'basic'" class="draftNotice">BASIC 기록은 관리자 승인 전까지 Draft에만 저장됩니다.</p>
                <div class="halfActions">
                  <button class="editBtn" :disabled="lifecycleBusy" @click="editHalf">수정</button>
                  <button class="startBtn" :disabled="lifecycleBusy" @click="startSecondHalf">후반전 시작</button>
                </div>
              </template>
              <template v-else-if="game.halfStatus === 'H2_done'">
                <p>후반 기록을 확인하세요<br><b>기록을 수정하거나 경기를 종료할 수 있습니다.</b></p>
                <p v-if="game.recorderLevel === 'basic'" class="draftNotice">종료하면 관리자 승인 대기 Draft로 제출됩니다.</p>
                <div class="halfActions">
                  <button class="editBtn" :disabled="lifecycleBusy" @click="editHalf">수정</button>
                  <button class="startBtn" :disabled="lifecycleBusy" @click="finishMatch">{{ game.recorderLevel ===
                    'basic' ? '승인 대기 제출 & 경기 종료' : '최종 데이터 갱신 & 경기 종료' }}</button>
                </div>
              </template>
              <template v-else-if="isPaused">
                <p>{{ pausedHalf }} 기록이 대기 중입니다<br><b>{{ pausedClock }} 시점부터 이어서 입력합니다.</b></p>
                <p v-if="matchInfoEditMode" class="draftNotice">라인업 · 포메이션 · 진영 선택 변경 중입니다.</p>
                <div class="halfActions">
                  <button class="editBtn" :disabled="lifecycleBusy" @click="toggleMatchInfoEdit">{{ matchInfoEditMode ?
                    '변경 완료' : '경기 정보 변경' }}</button>
                  <button class="startBtn" :disabled="matchInfoEditMode || lifecycleBusy" @click="reenterHalf">{{
                    pausedHalf }}전 입장</button>
                </div>
              </template>
              <template v-else>
                <p>{{ game.recorderLevel === 'basic' ? '관리자 승인 대기' : statusLabel }}</p>
                <p v-if="game.recorderLevel === 'basic'" class="draftNotice">관리자 페이지에서 RAW 승격 전까지 Draft만 유지됩니다.</p>
                <button class="editBtn" :disabled="lifecycleBusy" @click="editFinal">수정</button>
              </template>
              <p v-if="lifecycleError" class="lifecycleError">{{ lifecycleError }}</p>
              <small v-if="matchId">matchId: {{ matchId }}</small>
            </section>
          </div>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
* {
  box-sizing: border-box
}

button {
  font: inherit
}

.page {
  width: 1280px;
  height: 800px;
  padding: 0;
  display: grid;
  place-items: center;
  overflow: hidden;
  position: relative;
  background: #0b0f17;
  color: #fff;
  font-family: Arial, "Noto Sans KR", sans-serif
}

.bg {
  position: absolute;
  inset: 0;
  background: radial-gradient(1200px 500px at 50% 25%, rgba(255, 255, 255, .08), transparent 60%), radial-gradient(900px 400px at 20% 70%, rgba(111, 159, 186, .09), transparent 55%), radial-gradient(900px 400px at 80% 70%, rgba(241, 180, 0, .08), transparent 55%), linear-gradient(180deg, rgba(0, 0, 0, .55), rgba(0, 0, 0, .78))
}

.topBack {
  position: absolute;
  z-index: 2;
  top: 14px;
  right: 16px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid rgba(255, 255, 255, .22);
  border-radius: 4px;
  background: rgba(10, 14, 22, .88);
  color: rgba(255, 255, 255, .86);
  font-size: 12px;
  font-weight: 800;
  cursor: pointer
}

.topBack:hover {
  border-color: #f0b429;
  color: #f0b429
}

.frame {
  position: relative;
  width: 1280px;
  height: 800px;
  border-radius: 0;
  display: grid;
  grid-template-columns: 266px 1014px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, .08);
  background: rgba(10, 14, 22, .78);
  backdrop-filter: blur(8px);
  box-shadow: 0 18px 60px rgba(0, 0, 0, .55)
}

.sidebar {
  padding: 12px 12px 10px;
  border-right: 1px solid rgba(255, 255, 255, .06);
  display: flex;
  flex-direction: column
}

.matchDate {
  text-align: center;
  font-size: 16px;
  line-height: 22px;
  font-weight: 900;
  color: rgba(255, 255, 255, .85)
}

.teamPick {
  margin-top: 8px;
  text-align: center;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: .03em;
  color: rgba(255, 255, 255, .7)
}

.teams {
  height: 92px;
  display: grid;
  grid-template-columns: 1fr 22px 1fr;
  align-items: center;
  border-bottom: 1px solid rgba(255, 255, 255, .08)
}

.club {
  min-width: 0;
  display: grid;
  justify-items: center;
  gap: 7px;
  font-size: 10px;
  font-weight: 800;
  text-align: center;
  color: rgba(255, 255, 255, .85);
  background: none;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 6px 4px;
  cursor: pointer
}

.club:hover {
  background: rgba(255, 255, 255, .04)
}

.club.active {
  border-color: #f0b429;
  background: rgba(240, 180, 41, .1)
}

.crest {
  width: 30px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 45% 45% 55% 55%;
  font-size: 9px;
  font-weight: 900;
  color: white;
  border: 2px solid #e7d365
}

.homeCrest {
  background: linear-gradient(135deg, #fff 0 38%, #e43f3f 38% 53%, #fff 53%);
  color: #273246
}

.awayCrest {
  background: #fff;
  color: #2649a2;
  border-color: #e0bc42
}

.versus {
  width: 21px;
  height: 21px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, .9);
  color: #222;
  font-size: 8px;
  font-weight: 900
}

.score {
  margin-top: 12px;
  text-align: center;
  font-size: 20px;
  line-height: 24px;
  font-weight: 900;
  color: rgba(255, 255, 255, .9)
}

.status {
  color: rgba(241, 180, 0, .95);
  text-align: center;
  font-size: 12px;
  font-weight: 800
}

.matchMeta,
.stadium {
  margin-top: 8px;
  text-align: center;
  color: rgba(255, 255, 255, .45);
  font-size: 11px
}

.stadium {
  margin-top: 2px
}

.kpiHalfToggle {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px
}

.kpiHalfToggle button {
  height: 22px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .1);
  background: rgba(255, 255, 255, .03);
  color: rgba(255, 255, 255, .55);
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .03em
}

.kpiHalfToggle button.active {
  background: rgba(240, 180, 41, .12);
  border-color: rgba(240, 180, 41, .5);
  color: #f0b429
}

.kpis {
  margin-top: 6px;
  display: grid;
  gap: 4px
}

.kpiRow {
  height: 32px;
  display: grid;
  grid-template-columns: 1fr 1.15fr 1fr;
  place-items: center;
  background: rgba(255, 255, 255, .02);
  border: 1px solid rgba(255, 255, 255, .07);
  border-radius: 4px;
  color: rgba(255, 255, 255, .7);
  font-size: 14px
}

.kpiRow b {
  font-size: 13px;
  letter-spacing: .04em;
  color: rgba(255, 255, 255, .55)
}

.kpiErrors {
  margin-top: 6px;
  display: grid;
  gap: 4px
}

.errRow {
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  background: rgba(255, 255, 255, .02);
  border: 1px solid rgba(255, 255, 255, .07);
  border-radius: 4px;
  color: rgba(255, 255, 255, .45);
  font-size: 11px;
  font-weight: 700;
  cursor: pointer
}

.errRow b {
  font-size: 12px;
  color: rgba(255, 255, 255, .45)
}

.errRow.has {
  background: rgba(224, 62, 62, .12);
  border-color: rgba(224, 62, 62, .5);
  color: #e05c5c
}

.errRow.has b {
  color: #e05c5c
}

.errRow.open {
  border-color: rgba(240, 180, 41, .5)
}

.errDetail {
  margin: -2px 0 2px;
  padding: 4px 8px;
  background: rgba(0, 0, 0, .2);
  border: 1px solid rgba(255, 255, 255, .07);
  border-radius: 4px
}

.errDetailGrid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 2px 4px
}

.errDetailItem {
  font-size: 10px;
  color: rgba(255, 255, 255, .6);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis
}

.errDetailEmpty {
  display: block;
  font-size: 10px;
  color: rgba(255, 255, 255, .3);
  text-align: center;
  padding: 2px 0
}

.testBtn {
  margin-top: auto;
  height: 30px;
  border-radius: 4px;
  border: 1px dashed rgba(240, 180, 41, .5);
  background: rgba(240, 180, 41, .08);
  color: #f0b429;
  cursor: pointer;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .05em
}

.backBtn {
  margin-top: 8px;
  height: 34px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .1);
  background: rgba(255, 255, 255, .02);
  color: rgba(255, 255, 255, .75);
  cursor: pointer;
  font-size: 12px
}

.content {
  min-width: 0;
  padding: 14px 18px 18px
}

.content h1 {
  height: 28px;
  margin: 0;
  text-align: center;
  font-size: 18px;
  font-weight: 900;
  line-height: 28px;
  color: rgba(255, 255, 255, .85)
}

.workspace {
  height: calc(100% - 28px);
  display: flex;
  flex-direction: column;
  gap: 10px
}

.topRow {
  flex: 2.2;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px
}

.bottomRow {
  flex: 1;
  min-height: 0;
  max-height: 170px;
  display: grid;
  grid-template-columns: 0.7fr 1.3fr 1fr;
  gap: 10px
}

.formationPanel,
.playerPanel,
.fieldChoice,
.toolPanel,
.startPanel {
  min-width: 0;
  min-height: 0;
  padding: 12px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, .08);
  background: rgba(255, 255, 255, .03)
}

.formationPanel {
  display: grid;
  grid-template-rows: 28px minmax(0, 1fr) 16px 46px;
  gap: 8px
}

.benchHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: rgba(255, 255, 255, .6)
}

.benchHeader b {
  color: rgba(241, 180, 0, .95);
  font-size: 13px;
  font-weight: 800
}

.playerPanel {
  display: flex;
  flex-direction: column
}

.fieldChoice {
  display: flex;
  flex-direction: column
}

.toolPanel {
  display: flex;
  flex-direction: column;
  justify-content: center
}

.startPanel {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center
}

.selectBar {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  background: #f2f2f2;
  color: #252525;
  font-size: 11px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .15);
  cursor: pointer;
  user-select: none
}

.selectBar.open {
  border-radius: 4px 4px 0 0
}

.menu {
  position: absolute;
  left: 0;
  right: 0;
  top: 100%;
  z-index: 5;
  background: #fff;
  border: 1px solid rgba(255, 255, 255, .15);
  border-top: none;
  border-radius: 0 0 4px 4px;
  box-shadow: 0 8px 20px rgba(0, 0, 0, .4)
}

.menuItem {
  padding: 8px 10px;
  color: #252525;
  font-size: 11px;
  cursor: pointer
}

.menuItem:hover {
  background: #eef6f8
}

.pitch {
  position: relative;
  overflow: hidden;
  background: rgba(0, 0, 0, .18);
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, .1)
}

.halfway {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 43%;
  border-bottom: 2px solid rgba(255, 255, 255, .22)
}

.centerCircle {
  position: absolute;
  width: 104px;
  height: 104px;
  border: 2px solid rgba(255, 255, 255, .22);
  border-radius: 50%;
  left: 50%;
  top: -53px;
  transform: translateX(-50%)
}

/* 페널티 에어리어 — 실제 규격 비율로 그린다.
   페널티박스 40.32×16.5m, 골에어리어 18.32×5.5m, 페널티마크 11m,
   아크 반지름 9.15m(박스 위로 3.65m, 폭 14.6m), 골대 7.32m. 아래쪽이 골라인. */
.penaltyBox {
  position: absolute;
  left: 50%;
  bottom: 0;
  transform: translateX(-50%);
  width: 58%;
  height: 24%;
  border: 2px solid rgba(255, 255, 255, .22);
  border-bottom: 0
}

.goalBox {
  position: absolute;
  left: 50%;
  bottom: 0;
  transform: translateX(-50%);
  width: 26.3%;
  height: 8%;
  border: 2px solid rgba(255, 255, 255, .22);
  border-bottom: 0
}

.penaltySpot {
  position: absolute;
  left: 50%;
  bottom: 16%;
  width: 4px;
  height: 4px;
  margin: 0 0 -2px -2px;
  border-radius: 50%;
  background: rgba(255, 255, 255, .4)
}

.penaltyArc {
  position: absolute;
  left: 50%;
  bottom: 24%;
  transform: translateX(-50%);
  width: 21%;
  height: 5.3%;
  border: 2px solid rgba(255, 255, 255, .22);
  border-bottom: 0;
  border-radius: 50% 50% 0 0 / 100% 100% 0 0
}

.goalPost {
  position: absolute;
  left: 50%;
  bottom: 0;
  transform: translateX(-50%);
  width: 10.5%;
  height: 2.6%;
  border: 2px solid rgba(255, 255, 255, .45);
  border-bottom: 0;
  background: rgba(255, 255, 255, .05)
}

.cornerArc {
  position: absolute;
  bottom: -9px;
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, .18);
  border-radius: 50%
}

.cornerArc.left {
  left: -9px
}

.cornerArc.right {
  right: -9px
}

.shirt {
  position: absolute;
  left: 50%;
  bottom: 47px;
  width: 46px;
  height: 37px;
  transform: translateX(-50%);
  background: #e7ecf5;
  clip-path: polygon(22% 0, 38% 10%, 62% 10%, 78% 0, 100% 25%, 82% 42%, 75% 34%, 75% 100%, 25% 100%, 25% 34%, 18% 42%, 0 25%)
}

.bench {
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none
}

.bench::-webkit-scrollbar {
  display: none
}

.slot.benchSlot {
  position: static;
  flex: 0 0 40px;
  width: 40px;
  height: 40px;
  transform: none;
  font-size: 15px
}

.slot {
  position: absolute;
  transform: translate(-50%, -50%);
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: rgba(255, 255, 255, .14);
  border: 2px solid rgba(255, 255, 255, .25);
  color: #fff;
  font-size: 16px;
  font-weight: 800;
  cursor: pointer;
  display: grid;
  place-items: center;
  padding: 0
}

.selectBar.locked {
  opacity: .58;
  cursor: not-allowed
}

.slot:disabled {
  cursor: not-allowed;
  opacity: .78
}

.player:disabled {
  cursor: not-allowed
}

.miniBox.locked {
  cursor: not-allowed;
  opacity: .45
}

.slot:hover {
  background: rgba(255, 255, 255, .22)
}

.slot.filled {
  background: rgba(111, 159, 186, .22);
  border-color: #5fb8c9
}

.slot.active {
  outline: 2px solid #f0b429;
  outline-offset: 2px
}

.slot.gk {
  background: rgba(255, 255, 255, .2)
}

.slot.gk.filled {
  background: rgba(180, 190, 200, .4);
  border-color: #c9d2db
}

.slot.fw.filled {
  background: rgba(95, 184, 201, .3);
  border-color: #5fb8c9
}

.slot.mf.filled {
  background: rgba(217, 134, 113, .3);
  border-color: #d98671
}

.slot.df.filled {
  background: rgba(147, 181, 106, .3);
  border-color: #93b56a
}

.fieldChoice h2 {
  margin: 0 0 8px;
  font-size: 13px;
  text-align: center;
  color: rgba(255, 255, 255, .8);
  font-weight: 800
}

/* 잔디선택 / 선수교체 */
.toolPanel {
  position: relative
}

.toolGrid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px
}

.toolBtn.on {
  border-color: #f0b429;
  background: rgba(240, 180, 41, .12)
}

.grassPop {
  position: absolute;
  z-index: 30;
  left: 12px;
  right: 12px;
  bottom: calc(100% + 8px);
  padding: 10px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, .16);
  background: #1b1e22;
  box-shadow: 0 14px 40px rgba(0, 0, 0, .6);
  display: flex;
  flex-direction: column;
  gap: 9px
}

.popRow {
  display: flex;
  flex-direction: column;
  gap: 5px
}

.popLabel {
  font-size: 10px;
  font-weight: 800;
  color: rgba(255, 255, 255, .5)
}

.popOpts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 5px
}

.popBtn {
  height: 24px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .14);
  background: rgba(255, 255, 255, .05);
  color: #ddd;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
  padding: 0
}

.popBtn.on {
  border-color: #f0b429;
  background: rgba(240, 180, 41, .2);
  color: #f0b429
}

.popBtn:disabled {
  opacity: .35;
  cursor: not-allowed
}

.popPreview {
  height: 34px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .18)
}

.popOk {
  height: 26px;
  border-radius: 4px;
  border: none;
  background: #f0b429;
  color: #191919;
  font-weight: 800;
  font-size: 11px;
  cursor: pointer
}

.toolBtn {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, .12);
  background: rgba(255, 255, 255, .04);
  color: rgba(255, 255, 255, .85);
  cursor: pointer
}

.toolBtn:hover {
  border-color: #f0b429;
  background: rgba(240, 180, 41, .12)
}

.toolBtn:active {
  transform: scale(.98)
}

.toolIcon {
  width: 52px;
  height: 44px;
  border-radius: 4px;
  display: grid;
  place-items: center;
  font-size: 24px;
  color: #eee
}

.grassIcon {
  background: repeating-linear-gradient(90deg, #2f7d3c 0 8px, #256a31 8px 16px);
  border: 1px solid rgba(255, 255, 255, .25)
}

.subIcon {
  background: rgba(255, 255, 255, .08);
  border: 1px solid rgba(255, 255, 255, .2);
  color: #f0b429
}

.toolLabel {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: .02em
}

.miniPitch {
  flex: 1;
  min-height: 0;
  position: relative;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, .15);
  background: rgba(0, 0, 0, .14);
  overflow: hidden
}

.miniHalf {
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  border-left: 1px solid rgba(255, 255, 255, .25);
  z-index: 1;
  pointer-events: none
}

.miniCircle {
  position: absolute;
  width: 34%;
  aspect-ratio: 1;
  max-width: 60px;
  border: 1px solid rgba(255, 255, 255, .25);
  border-radius: 50%;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 1;
  pointer-events: none
}

.miniPenalty {
  position: absolute;
  top: 18%;
  bottom: 18%;
  width: 14%;
  border: 1px solid rgba(255, 255, 255, .25);
  z-index: 1;
  pointer-events: none
}

.miniPenalty.left {
  left: 0;
  border-left: none
}

.miniPenalty.right {
  right: 0;
  border-right: none
}

.miniGoal {
  position: absolute;
  top: 38%;
  bottom: 38%;
  width: 4%;
  border: 1px solid rgba(255, 255, 255, .25);
  z-index: 1;
  pointer-events: none
}

.miniGoal.left {
  left: 0;
  border-left: none
}

.miniGoal.right {
  right: 0;
  border-right: none
}

.miniBox {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 50%;
  cursor: pointer
}

.miniBox:hover {
  background: rgba(255, 255, 255, .08)
}

.miniBox.active {
  background: rgba(111, 159, 186, .22)
}

.leftBox {
  left: 0
}

.rightBox {
  right: 0
}

.playerPanel {}

.tabs {
  height: 30px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px
}

.tabs button {
  padding: 0;
  font: inherit;
  display: grid;
  place-items: center;
  background: #f2f2f2;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .15);
  color: #222;
  font-size: 10px;
  font-weight: 800;
  cursor: pointer
}

.tabs .off {
  background: rgba(255, 255, 255, .04);
  border-color: rgba(255, 255, 255, .1);
  color: rgba(255, 255, 255, .5)
}

.tabs .off:hover {
  background: rgba(255, 255, 255, .1);
  color: rgba(255, 255, 255, .8)
}

.playerGrid {
  flex: 1;
  margin-top: 8px;
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  grid-template-rows: repeat(6, 1fr);
  gap: 1px;
  background: rgba(255, 255, 255, .06);
  border-radius: 4px;
  overflow: hidden
}

.player {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255, 255, 255, .04);
  background: rgba(255, 255, 255, .03);
  overflow: hidden;
  padding: 0;
  cursor: default
}

.player.pickable {
  cursor: grab
}

.player.pickable:active {
  cursor: grabbing
}

.player.pickable:hover {
  background: rgba(255, 255, 255, .08);
  border-color: rgba(255, 255, 255, .15)
}

.player.used {
  opacity: .3;
  cursor: not-allowed
}

.player strong {
  font-size: 19px;
  line-height: 20px;
  color: rgba(255, 255, 255, .85)
}

.player span {
  max-width: 100%;
  padding: 0 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: rgba(255, 255, 255, .45);
  font-size: 8px
}

.player.gk strong {
  color: rgba(255, 255, 255, .55)
}

.player.fw strong {
  color: #5fb8c9
}

.player.mf strong {
  color: #d98671
}

.player.df strong {
  color: #93b56a
}

.player.empty {
  min-height: 20px
}

/* 선수 교체 */
.subTabs {
  grid-template-columns: 1fr
}

.subTitle {
  cursor: default
}

.subTimeRow {
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: rgba(255, 255, 255, .55);
  font-size: 10px
}

.subTimeRow strong {
  min-width: 22px;
  text-align: center;
  color: #f0b429;
  font: 700 14px ui-monospace, monospace
}

.subTimeRow b {
  color: #f0b429
}

.subTimeBtn {
  width: 22px;
  height: 22px;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, .18);
  border-radius: 4px;
  background: rgba(255, 255, 255, .05);
  color: #ddd;
  cursor: pointer
}

.subCols {
  flex: 1.6;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  padding: 8px 0
}

.subItem {
  cursor: grab
}

.subItem:active {
  cursor: grabbing
}

.subItem.dragging {
  opacity: .4
}

.subCol {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 6px
}

.subColHead {
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .02em
}

.subColHead.out {
  background: rgba(217, 134, 113, .18);
  color: #d98671;
  border: 1px solid rgba(217, 134, 113, .4)
}

.subColHead.in {
  background: rgba(147, 181, 106, .18);
  color: #93b56a;
  border: 1px solid rgba(147, 181, 106, .4)
}

/* 카드는 DidInput.vue 의 교체 카드와 같은 느낌으로 — 폭이 좁아 2줄까지만 조금 더 크게 */
.subList {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-auto-rows: 36px;
  gap: 4px;
  align-content: start;
  padding-right: 0
}

.subItem {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, .12);
  background: rgba(255, 255, 255, .04);
  cursor: pointer;
  padding: 0 4px
}

.subItem strong {
  font-size: 14px;
  font-weight: 900;
  line-height: 1
}

.subItem i {
  font-style: normal;
  font-size: 10px
}

.subItem span {
  font-size: 8px;
  color: rgba(255, 255, 255, .6);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%
}

.subItem.gk strong {
  color: #ddd
}

.subItem.fw strong {
  color: #5fb8c9
}

.subItem.mf strong {
  color: #d98671
}

.subItem.df strong {
  color: #93b56a
}

.subItem:hover {
  border-color: rgba(240, 180, 41, .6);
  background: rgba(240, 180, 41, .1)
}

.subItem.on {
  border-color: #f0b429;
  background: rgba(240, 180, 41, .24);
  box-shadow: 0 0 0 1px #f0b429 inset
}

.subEmpty {
  grid-column: 1/3;
  color: rgba(255, 255, 255, .35);
  font-size: 10px;
  text-align: center;
  padding-top: 14px
}

/* 교체 이력 — DidInput.vue 의 subHistory 와 같은 형식(칸 폭만 이 패널 너비에 맞춤) */
.subHistory {
  flex: 0 0 auto;
  max-height: 180px;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(255, 255, 255, .08);
  border-radius: 6px;
  overflow: hidden;
  margin-bottom: 8px
}

.subHistBody {
  flex: 1;
  overflow-y: auto
}

.subHistHead,
.subHistRow {
  display: grid;
  grid-template-columns: 44px 40px 1fr 1fr 34px;
  align-items: center;
  gap: 4px;
  padding: 8px 6px;
  font-size: 11px
}

.subHistHead {
  background: #1b2130;
  color: rgba(255, 255, 255, .45);
  font-weight: 600
}

.subHistRow {
  border-top: 1px solid rgba(255, 255, 255, .06);
  color: rgba(255, 255, 255, .8)
}

.subHistRow.pending {
  background: rgba(240, 180, 41, .08);
  border-left: 2px solid #f0b429
}

.subHistRow .hP {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis
}

.subHistRow .outP {
  color: #f87171
}

.subHistRow .inP {
  color: #f0b429
}

.subHistRow .hAct {
  display: flex;
  gap: 4px;
  justify-content: flex-end
}

.subHistEmpty {
  padding: 8px;
  text-align: center;
  color: rgba(255, 255, 255, .35);
  font-size: 10px
}

.subUndo {
  border: 1px solid rgba(255, 255, 255, .15);
  background: rgba(255, 255, 255, .05);
  color: rgba(255, 255, 255, .6);
  font-size: 9px;
  border-radius: 4px;
  padding: 1px 4px;
  cursor: pointer
}

.subUndo:hover {
  color: #fff;
  border-color: rgba(239, 68, 68, .5)
}

.subActions {
  height: 32px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px
}

.subCancel,
.subSave {
  border-radius: 4px;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
  border: 1px solid #f0b429;
  background: transparent;
  color: #f0b429
}

.subCancel:hover {
  background: rgba(240, 180, 41, .14)
}

.subSave {
  background: #f0b429;
  color: #191919
}

.subSave:disabled {
  background: transparent;
  border-color: rgba(255, 255, 255, .18);
  color: rgba(255, 255, 255, .35);
  cursor: not-allowed
}

.legend {
  height: 19px;
  display: flex;
  align-items: end;
  justify-content: flex-end;
  gap: 7px;
  font-size: 9px
}

.legend span {
  padding: 1px 8px;
  border-radius: 3px;
  color: white;
  font-weight: 700
}

.legend .gk {
  background: rgba(255, 255, 255, .25)
}

.legend .fw {
  background: #5fb8c9
}

.legend .mf {
  background: #d98671
}

.legend .df {
  background: #93b56a
}


.modeToggle {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  margin-bottom: 8px
}

.modeBtn {
  height: 22px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .12);
  background: rgba(255, 255, 255, .03);
  color: rgba(255, 255, 255, .45);
  font-size: 10px;
  font-weight: 800;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px
}

.modeBtn small {
  font-size: 8px;
  font-weight: 600;
  color: rgba(255, 255, 255, .3)
}

.modeBtn.on {
  background: rgba(240, 180, 41, .18);
  border-color: #f0b429;
  color: #f0b429
}

.modeBtn.on small {
  color: rgba(240, 180, 41, .65)
}

.startPanel p {
  margin: 0 0 10px;
  text-align: center;
  font-size: 11px;
  line-height: 1.4;
  color: rgba(255, 255, 255, .75)
}

.startPanel p b {
  color: rgba(241, 180, 0, .95)
}

.startBtn {
  height: 43px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .1);
  background: rgba(255, 255, 255, .04);
  color: rgba(255, 255, 255, .35);
  cursor: not-allowed
}

.startBtn:not(:disabled) {
  background: #f0b429;
  border-color: #f0b429;
  color: #161200;
  font-weight: 800;
  cursor: pointer
}

.startPanel small {
  position: absolute;
  right: 8px;
  bottom: 3px;
  color: rgba(255, 255, 255, .25);
  font-size: 8px
}

.halfActions {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 8px
}

.halfActions3 {
  grid-template-columns: 1fr 1fr 1.3fr
}

.editBtn {
  height: 43px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, .16);
  background: rgba(255, 255, 255, .04);
  color: #ddd;
  font-weight: 800;
  cursor: pointer
}

.editBtn:hover {
  background: rgba(255, 255, 255, .1)
}

.editBtn:disabled {
  opacity: .35;
  cursor: not-allowed
}

.halfActions .startBtn {
  background: #f0b429;
  border-color: #f0b429;
  color: #161200;
  cursor: pointer
}

.refreshBtn {
  height: 43px;
  border-radius: 4px;
  border: 1px solid rgba(240, 180, 41, .4);
  background: rgba(240, 180, 41, .08);
  color: #f0b429;
  font-weight: 800;
  cursor: pointer;
  font-size: 12px
}

.refreshBtn:hover:not(:disabled) {
  background: rgba(240, 180, 41, .18)
}

.refreshBtn:disabled {
  opacity: .35;
  cursor: not-allowed;
  color: rgba(255, 255, 255, .35);
  border-color: rgba(255, 255, 255, .16);
  background: transparent
}

.lockNotice {
  margin: 0 0 8px;
  font-size: 11px;
  color: #f0b429;
  text-align: center
}

.draftNotice {
  margin: 0 0 8px;
  font-size: 11px;
  color: #f0b429;
  text-align: center
}

.lifecycleError {
  margin: 8px 0 0;
  font-size: 11px;
  color: #ff8b8b;
  text-align: center;
  line-height: 1.4
}

.levelToggle {
  margin-top: 6px;
  height: 26px;
  border-radius: 4px;
  border: 1px dashed rgba(240, 180, 41, .5);
  background: rgba(240, 180, 41, .08);
  color: #f0b429;
  cursor: pointer;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .03em
}

.levelToggle.basic {
  background: rgba(99, 192, 162, .12);
  border-color: rgba(99, 192, 162, .5);
  color: #63c0a2
}
</style>
