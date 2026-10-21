<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { signOut } from 'firebase/auth'

const { $auth } = useNuxtApp()

async function logout() {
  await signOut($auth)
  await navigateTo('/login')
}



type MatchItem = {
  id: string
  date: string
  time: string
  league: string
  round: string
  stadium: string
  home: { name: string; short?: string }
  away: { name: string; short?: string }
  score: { home: number; away: number }
  inputStatus: { H: TeamInputStatus; A: TeamInputStatus }
  collaboration: { H: CollaborationSummary; A: CollaborationSummary }
}

type TeamInputStatus = { rawStatus: string | null; completed: boolean; lifecycleStatus: string }
type CollaborationSummary = { status?: string; primaryUid?: string; participants?: Array<{ uid: string; role?: string; name?: string }> }

const today = new Date()
const viewYear = ref(today.getFullYear())
const viewMonth = ref(today.getMonth()) // 0-11
const yearOptions = computed(() => Array.from({ length: 101 }, (_, index) => 2000 + index))
const monthOptions = Array.from({ length: 12 }, (_, index) => index)

function toYMD(y: number, m0: number, d: number) {
  const mm = String(m0 + 1).padStart(2, '0')
  const dd = String(d).padStart(2, '0')
  return `${y}-${mm}-${dd}`
}

const selectedDate = ref<string>('')
const selectedMatchId = ref<string>('')
const selectedTeam = ref<'home' | 'away' | null>(null)
const roleDialogOpen = ref(false)
const matches = ref<MatchItem[]>([])
type CalendarFixture = { date: string; leagueId: string }
const calendarFixtures = ref<CalendarFixture[]>([])
const calendarCache = useState<Record<string, CalendarFixture[]>>('schedule-calendar-dates', () => ({}))
let calendarGeneration = 0
let detailGeneration = 0
let scheduleActive = true
onUnmounted(() => { scheduleActive = false; calendarGeneration++; detailGeneration++ })
const loadingMatches = ref(false)
const loadError = ref('')
const { request } = useBackendApi()

const calendarDays = computed(() => {
  const y = viewYear.value
  const m0 = viewMonth.value
  const first = new Date(y, m0, 1)
  const last = new Date(y, m0 + 1, 0)
  const startDow = first.getDay() // 0 Sun ~ 6 Sat
  const total = last.getDate()

  // 우리 화면은 "일 월 화 수 목 금 토"로 보이니까
  // startDow 그대로 사용(일요일 시작)
  const cells: Array<{ day: number | null; ymd?: string }> = []
  for (let i = 0; i < startDow; i++) cells.push({ day: null })

  for (let d = 1; d <= total; d++) {
    cells.push({ day: d, ymd: toYMD(y, m0, d) })
  }

  while (cells.length % 7 !== 0) cells.push({ day: null })
  return cells
})

const todayYmd = toYMD(today.getFullYear(), today.getMonth(), today.getDate())

// 날짜별 경기 수(선택한 리그 기준). 달력 칸에 "N경기"로 표시한다.
const matchCountByDate = computed(() => {
  const counts: Record<string, number> = {}
  for (const fixture of calendarFixtures.value) {
    if (selectedLeague.value !== 'all' && fixture.leagueId !== selectedLeague.value) continue
    counts[fixture.date] = (counts[fixture.date] ?? 0) + 1
  }
  return counts
})

const DOW_NAMES = ['일', '월', '화', '수', '목', '금', '토']

// 달력 아래 요약: 선택한 날짜와 그날의 경기 수.
const selectedDateLabel = computed(() => {
  const ymd = selectedDate.value
  if (!ymd) return '날짜를 선택하세요'
  const [y, m, d] = ymd.split('-').map(Number)
  const dow = DOW_NAMES[new Date(y!, m! - 1, d!).getDay()]
  const count = matchCountByDate.value[ymd] ?? 0
  return `${y}.${String(m).padStart(2, '0')}.${String(d).padStart(2, '0')} (${dow}) · ${count ? `${count}경기` : '경기 없음'}`
})

// 오늘이 속한 달로 이동하고 오늘 날짜를 바로 선택한다.
function goToday() {
  const monthChanged = viewYear.value !== today.getFullYear() || viewMonth.value !== today.getMonth()
  viewYear.value = today.getFullYear()
  viewMonth.value = today.getMonth()
  if (monthChanged) applyCalendarView()
  pickDate(todayYmd)
}

function prevMonth() {
  if (viewMonth.value === 0) {
    viewMonth.value = 11
    viewYear.value -= 1
  } else viewMonth.value -= 1
  applyCalendarView()
}

function nextMonth() {
  if (viewMonth.value === 11) {
    viewMonth.value = 0
    viewYear.value += 1
  } else viewMonth.value += 1
  applyCalendarView()
}

// 달력 좌우 스와이프로 월 이동 (왼쪽 → 다음 달, 오른쪽 → 이전 달)
const SWIPE_THRESHOLD = 50
const SLIDE_DISTANCE = 140
const SLIDE_MS = 180
let swipeStart: { x: number; y: number; id: number; dragging: boolean } | null = null
let suppressNextClick = false
let sliding = false

// 손가락을 따라 달력이 움직이고, 월이 바뀔 때 밀려나갔다 들어오는 효과
const calOffset = ref(0)
const calOpacity = ref(1)
const calTransition = ref(false)
const calStyle = computed(() => ({
  transform: `translateX(${calOffset.value}px)`,
  opacity: calOpacity.value,
  transition: calTransition.value ? `transform ${SLIDE_MS}ms ease-out, opacity ${SLIDE_MS}ms ease-out` : 'none',
}))

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function nextFrame() {
  return new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
}

function wait(ms: number) {
  return new Promise<void>(resolve => setTimeout(resolve, ms))
}

// dir: 1 → 다음 달(왼쪽으로 밀려남), -1 → 이전 달
async function slideMonth(dir: 1 | -1) {
  if (sliding) return
  if (prefersReducedMotion()) {
    calOffset.value = 0
    dir === 1 ? nextMonth() : prevMonth()
    return
  }
  sliding = true
  calTransition.value = true
  calOffset.value = -dir * SLIDE_DISTANCE
  calOpacity.value = 0
  await wait(SLIDE_MS)
  dir === 1 ? nextMonth() : prevMonth()
  calTransition.value = false
  calOffset.value = dir * SLIDE_DISTANCE
  await nextFrame()
  calTransition.value = true
  calOffset.value = 0
  calOpacity.value = 1
  await wait(SLIDE_MS)
  calTransition.value = false
  sliding = false
}

function onCalendarPointerDown(event: PointerEvent) {
  if (event.pointerType === 'mouse' || sliding) return
  swipeStart = { x: event.clientX, y: event.clientY, id: event.pointerId, dragging: false }
}

function onCalendarPointerMove(event: PointerEvent) {
  if (!swipeStart || swipeStart.id !== event.pointerId) return
  const dx = event.clientX - swipeStart.x
  const dy = event.clientY - swipeStart.y
  if (!swipeStart.dragging) {
    if (Math.abs(dx) < 10 || Math.abs(dx) < Math.abs(dy)) return
    swipeStart.dragging = true
    // 가로 드래그가 확정된 뒤에만 캡처 (탭 클릭 대상은 그대로 유지)
    ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  }
  calTransition.value = false
  calOffset.value = dx * 0.6
  calOpacity.value = 1 - Math.min(Math.abs(dx) / 600, 0.35)
}

function onCalendarPointerUp(event: PointerEvent) {
  if (!swipeStart || swipeStart.id !== event.pointerId) return
  const dx = event.clientX - swipeStart.x
  const dy = event.clientY - swipeStart.y
  const dragging = swipeStart.dragging
  swipeStart = null
  if (dragging) {
    // 스와이프 끝에 손가락 아래 셀이 클릭되는 것 방지
    suppressNextClick = true
    setTimeout(() => { suppressNextClick = false }, 300)
  }
  if (Math.abs(dx) < SWIPE_THRESHOLD || Math.abs(dx) < Math.abs(dy) * 1.5) {
    snapBack()
    return
  }
  slideMonth(dx < 0 ? 1 : -1)
}

function snapBack() {
  calTransition.value = true
  calOffset.value = 0
  calOpacity.value = 1
}

function onCalendarPointerCancel() {
  swipeStart = null
  snapBack()
}

function onCalendarClickCapture(event: MouseEvent) {
  if (!suppressNextClick) return
  suppressNextClick = false
  event.stopPropagation()
  event.preventDefault()
}

function applyCalendarView() {
  selectedDate.value = ''
  matches.value = []
  selectedMatchId.value = ''
  selectedTeam.value = null
  void loadCalendar()
}

function pickDate(ymd?: string) {
  selectedDate.value = ymd || ''
  selectedMatchId.value = ''
  selectedTeam.value = null
  void loadMatches()
}

async function loadCalendar() {
  const generation = ++calendarGeneration
  ++detailGeneration
  const key = `${viewYear.value}-${viewMonth.value + 1}`
  const current = () => scheduleActive && generation === calendarGeneration
  const apply = (fixtures: CalendarFixture[]) => {
    calendarFixtures.value = fixtures
    const dates = fixtures.filter(item => selectedLeague.value === 'all' || item.leagueId === selectedLeague.value)
      .map(item => normalizeDate(item.date)).sort()
    // Start with the latest fixture day, then preserve a user's chosen day.
    const selected = selectedDate.value || dates[dates.length - 1] || ''
    if (selected !== selectedDate.value) pickDate(selected)
  }
  calendarFixtures.value = []
  loadError.value = ''
  loadingMatches.value = false
  const cached = calendarCache.value[key]
  if (cached) apply(cached)
  try {
    const payload = await request<{ fixtures: CalendarFixture[] }>(`/api/v1/match-input/calendar?year=${viewYear.value}&month=${viewMonth.value + 1}`)
    if (!current()) return
    calendarCache.value[key] = payload.fixtures
    apply(payload.fixtures)
  } catch (error) {
    if (!current()) return
    loadError.value = error instanceof Error ? error.message : '경기 날짜를 불러오지 못했습니다.'
  }
}

function normalizeDate(value: string) {
  const digits = value.replace(/\D/g, '')
  return digits.length >= 8 ? `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}` : value
}

async function loadMatches() {
  const generation = ++detailGeneration
  const day = selectedDate.value
  const current = () => scheduleActive && generation === detailGeneration && day === selectedDate.value
  matches.value = []
  if (!day) { loadingMatches.value = false; return }
  loadingMatches.value = true
  loadError.value = ''
  try {
    const payload = await request<{ matches: Array<{
      gmId: string; date: string; kickoffTime: string | null; leagueId: string; round: number | null
      stadiumName: string; home: { name: string }; away: { name: string }
      score: { home: number; away: number }
      inputStatus: { H: TeamInputStatus; A: TeamInputStatus }
      collaboration?: MatchItem['collaboration']
    }> }>(`/api/v1/match-input/matches?year=${viewYear.value}&month=${viewMonth.value + 1}&date=${day}`)
    if (!current()) return
    matches.value = payload.matches.map((item) => ({
      id: item.gmId,
      date: normalizeDate(item.date),
      time: item.kickoffTime ?? '',
      league: item.leagueId,
      round: item.round === null ? '-' : `${item.round}R`,
      stadium: item.stadiumName,
      home: item.home,
      away: item.away,
      score: item.score,
      inputStatus: item.inputStatus,
      collaboration: item.collaboration ?? { H: {}, A: {} },
    }))
  } catch (error) {
    if (!current()) return
    matches.value = []
    loadError.value = error instanceof Error ? error.message : '경기 목록을 불러오지 못했습니다.'
  } finally {
    if (current()) loadingMatches.value = false
  }
}

onMounted(loadCalendar)

// 리그 필터: 월이 바뀌어도 선택은 유지하고, 그 달에 경기가 없어도 옵션은 남긴다.
const selectedLeague = ref<string>('all')
const leagueOptions = computed(() => {
  const leagues = new Set(calendarFixtures.value.map(match => match.leagueId).filter(Boolean))
  if (selectedLeague.value !== 'all') leagues.add(selectedLeague.value)
  return [...leagues].sort((a, b) => a.localeCompare(b))
})

const visibleMatches = computed(() => (
  selectedLeague.value === 'all' ? matches.value : matches.value.filter(match => match.league === selectedLeague.value)
))

function applyLeagueFilter() {
  selectedMatchId.value = ''
  selectedTeam.value = null
  const dates = calendarFixtures.value.filter(item => selectedLeague.value === 'all' || item.leagueId === selectedLeague.value)
    .map(item => normalizeDate(item.date)).sort()
  pickDate(dates.includes(selectedDate.value) ? selectedDate.value : dates[dates.length - 1] || '')
}

const matchList = computed(() => {
  if (!selectedDate.value) return []
  return visibleMatches.value.filter(match => match.date === selectedDate.value)
})

function selectMatch(id: string) {
  if (selectedMatchId.value !== id) selectedTeam.value = null
  selectedMatchId.value = id
}

function selectTeam(matchId: string, team: 'home' | 'away') {
  // 이미 선택한 팀을 다시 누르면 선택을 해제한다(경기 선택도 함께 풀어 다른 카드의 흐림도 사라진다).
  if (selectedMatchId.value === matchId && selectedTeam.value === team) {
    selectedMatchId.value = ''
    selectedTeam.value = null
    return
  }
  selectedMatchId.value = matchId
  selectedTeam.value = team
}

const selectedMatch = computed(() => {
  return matchList.value.find(m => m.id === selectedMatchId.value) || null
})

function teamAnalysisLabel(status: TeamInputStatus) {
  const labels: Record<string, string> = {
    ready: '분석 대기', H1: '전반 진행중', H1_done: '전반 종료',
    H2: '후반 진행중', H2_done: '후반 종료', final: '분석 종료',
  }
  return labels[status.lifecycleStatus] || '분석 대기'
}

function teamStatusTone(status: TeamInputStatus) {
  if (status.lifecycleStatus === 'final') return 'done'
  if (!status.lifecycleStatus || status.lifecycleStatus === 'ready') return 'waiting'
  return 'live'
}

function isMatchComplete(match: MatchItem) {
  return match.inputStatus.H.completed && match.inputStatus.A.completed
}

// 경기장 이름 끝의 '스타디움'은 항상 둘째 줄로 내린다. 이름 길이와 상관없이 같은 모양으로 보이게 한다.
function stadiumLines(name: string) {
  const match = name.trim().match(/^(.+?)\s*(스타디움|Stadium)$/i)
  return match ? [match[1]!, match[2]!] : [name]
}

function teamCollaboration(match: MatchItem, team: 'home' | 'away') {
  return team === 'home' ? match.collaboration.H : match.collaboration.A
}

// 내 계정이 참여한 팀인지. 입장 로직(selectedParticipantRole)과 같은 기준으로 판별한다.
function isMe(participant: { uid: string; name?: string }) {
  const user = $auth.currentUser
  if (!user) return false
  if (participant.uid === user.uid) return true
  // 예전 로컬 Draft는 공용 UID를 썼다. 저장된 이름이 내 계정과 같으면 내 참여로 본다.
  const names = [user.displayName, user.email].filter(Boolean)
  return participant.uid === 'local-did-input' && names.includes(participant.name || '')
}

function teamIsMine(match: MatchItem, team: 'home' | 'away') {
  return Boolean(teamCollaboration(match, team).participants?.some(isMe))
}

function matchHasMine(match: MatchItem) {
  return teamIsMine(match, 'home') || teamIsMine(match, 'away')
}

// 분석관은 실제로 있는 사람만 Main → Sub → Manager 순서로 위에서부터 쌓는다. 자리를 미리 잡아 두지 않는다.
// 한 카드의 두 팀 상자는 사람이 더 많은 쪽 높이에 맞춰지고, 적은 쪽은 아래가 비어 보인다.
type AnalystRow = { key: string; label: 'Main' | 'Sub' | 'Manager'; participant: { uid: string; role?: string; name?: string } }
function analystRows(summary: CollaborationSummary): AnalystRow[] {
  const participants = summary.participants || []
  const rows: AnalystRow[] = []
  const main = participants.find(item => item.role === 'primary')
  const sub = participants.find(item => item.role === 'assistant')
  if (main) rows.push({ key: 'main', label: 'Main', participant: main })
  if (sub) rows.push({ key: 'sub', label: 'Sub', participant: sub })
  for (const manager of participants.filter(item => item.role === 'manager')) {
    rows.push({ key: `manager-${manager.uid}`, label: 'Manager', participant: manager })
  }
  return rows
}

type ParticipantRole = 'primary' | 'assistant' | 'manager'

const ROLE_NAMES: Record<ParticipantRole, string> = { primary: '주 분석관', assistant: '부 분석관', manager: '매니저' }

// 매니저 버튼은 ADVANCED 분석관에게만 열린다. 최종 판단은 서버 join이 다시 한다.
const recorderLevel = ref<'basic' | 'advanced' | null>(null)
async function loadRecorderLevel() {
  try {
    const profile = await request<{ level?: 'basic' | 'advanced' }>('/api/v1/match-input/recorder-profile')
    recorderLevel.value = profile.level === 'basic' ? 'basic' : 'advanced'
  } catch {
    recorderLevel.value = null
  }
}
onMounted(loadRecorderLevel)

const selectedSummary = computed(() => (
  selectedMatch.value && selectedTeam.value ? teamCollaboration(selectedMatch.value, selectedTeam.value) : null
))

// 팀 입력이 끝나도 기존 참여자는 자기 역할로 다시 들어갈 수 있고,
// 매니저 입장은 별도로 유지한다.
const selectedTeamFinished = computed(() => {
  if (!selectedMatch.value || !selectedTeam.value) return false
  return selectedMatch.value.inputStatus[selectedTeam.value === 'home' ? 'H' : 'A'].lifecycleStatus === 'final'
})

const selectedTeamHasOtherPrimary = computed(() => {
  const summary = selectedSummary.value
  if (!summary) return false
  const primary = summary.primaryUid || summary.participants?.find(item => item.role === 'primary')?.uid
  const primaryName = summary.participants?.find(item => item.uid === primary)?.name
  const currentNames = [$auth.currentUser?.displayName, $auth.currentUser?.email].filter(Boolean)
  // Older local Drafts used one shared backend UID. Match their stored display
  // name once so the original primary can re-enter and the server can migrate it.
  if (primary === 'local-did-input' && primaryName && currentNames.includes(primaryName)) return false
  return Boolean(primary && primary !== $auth.currentUser?.uid)
})

const selectedParticipantRole = computed<ParticipantRole | null>(() => {
  const summary = selectedSummary.value
  if (!summary || !$auth.currentUser) return null
  const isRole = (role?: string): role is ParticipantRole => role === 'primary' || role === 'assistant' || role === 'manager'

  const direct = summary.participants?.find(item => item.uid === $auth.currentUser?.uid)
  if (isRole(direct?.role)) return direct.role

  // Legacy local Drafts used a shared UID. Until the backend migrates one,
  // recognize its stored name so the original participant can re-enter only
  // with the role already assigned to them.
  const currentNames = [$auth.currentUser.displayName, $auth.currentUser.email].filter(Boolean)
  const legacy = summary.participants?.find(item => (
    item.uid === 'local-did-input' && currentNames.includes(item.name || '')
  ))
  return isRole(legacy?.role) ? legacy.role : null
})

const selectedTeamHasPrimary = computed(() => {
  const summary = selectedSummary.value
  return Boolean(summary && (summary.primaryUid || summary.participants?.some(item => item.role === 'primary')))
})

const selectedTeamHasOtherAssistant = computed(() => Boolean(
  selectedSummary.value?.participants?.some(item => item.role === 'assistant' && item.uid !== $auth.currentUser?.uid)
))

const roleState = computed(() => {
  const mine = selectedParticipantRole.value
  const finished = selectedTeamFinished.value

  const primary = mine === 'primary' ? { disabled: false, label: '주 분석관으로 재입장' }
    : finished ? { disabled: true, label: '주 분석관으로 분석 진행' }
    : mine ? { disabled: true, label: '주 분석관으로 분석 진행' }
    : selectedTeamHasOtherPrimary.value ? { disabled: true, label: '주 분석관이 이미 입장했습니다' }
    : { disabled: false, label: '주 분석관으로 분석 진행' }

  const assistant = mine === 'assistant' ? { disabled: false, label: '부 분석관으로 재입장' }
    : finished ? { disabled: true, label: '부 분석관으로 분석 진행' }
    : mine ? { disabled: true, label: '부 분석관으로 분석 진행' }
    : !selectedTeamHasPrimary.value ? { disabled: true, label: '주 분석관 입장 후 참여할 수 있습니다' }
    : selectedTeamHasOtherAssistant.value ? { disabled: true, label: '부 분석관이 이미 입장했습니다' }
    : { disabled: false, label: '부 분석관으로 분석 진행' }

  const manager = mine === 'manager' ? { disabled: false, label: '매니저로 재입장' }
    : recorderLevel.value !== 'advanced' ? { disabled: true, label: '매니저 권한(ADVANCED)이 필요합니다' }
    : finished ? { disabled: false, label: '매니저 권한으로 입장' }
    : mine ? { disabled: true, label: '매니저 권한으로 입장' }
    : !finished && !selectedTeamHasPrimary.value ? { disabled: true, label: '주 분석관 입장 후 참여할 수 있습니다' }
    : { disabled: false, label: '매니저 권한으로 입장' }

  return { primary, assistant, manager }
})

async function goTeamSelection(role: ParticipantRole) {
  const m = selectedMatch.value
  if (!selectedDate.value || !m || !selectedTeam.value) return

  await navigateTo({
    path: '/TeamSelection',
    query: {
      date: selectedDate.value,
      matchId: m.id,
      league: m.league,
      round: m.round,
      stadium: m.stadium,
      time: m.time,
      home: m.home.name,
      away: m.away.name,
      team: selectedTeam.value,
      role,
    },
  })
}

function onSubmit() {
  if (!selectedDate.value) return
  if (!selectedMatch.value || !selectedTeam.value) return
  roleDialogOpen.value = true
}

function chooseRole(role: ParticipantRole) {
  if (roleState.value[role].disabled) return
  roleDialogOpen.value = false
  goTeamSelection(role)
}

function onCancel() {
  navigateTo('/login')
}
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <div class="topBar">
        <div class="title">경기일자와 경기를 선택해주세요</div>
        <div class="topRight">
          <NuxtLink class="manageBtn" to="/manage">데이터 관리</NuxtLink>
          <button class="logoutBtn" @click="logout">로그아웃</button>
        </div>
      </div>

      <div class="body">
        <!-- LEFT: Calendar -->
        <section class="left">
          <div class="toolbar">
            <div class="calendarFilter" aria-label="조회 조건">
              <select v-model="selectedLeague" aria-label="리그" @change="applyLeagueFilter">
                <option value="all">전체 리그</option>
                <option v-for="league in leagueOptions" :key="league" :value="league">{{ league }}</option>
              </select>
              <select v-model.number="viewYear" aria-label="연도" @change="applyCalendarView">
                <option v-for="year in yearOptions" :key="year" :value="year">{{ year }}년</option>
              </select>
              <select v-model.number="viewMonth" class="monthSelect" aria-label="월" @change="applyCalendarView">
                <option v-for="month in monthOptions" :key="month" :value="month">{{ month + 1 }}월</option>
              </select>
            </div>
            <div class="monthNav" aria-label="월 이동">
              <button class="iconBtn" @click="slideMonth(-1)" aria-label="Prev month">‹</button>
              <button class="iconBtn" @click="slideMonth(1)" aria-label="Next month">›</button>
            </div>
          </div>

          <div class="dow">
            <span>일</span><span>월</span><span>화</span><span>수</span><span>목</span><span>금</span><span>토</span>
          </div>

          <div class="calViewport">
          <div
            class="cal"
            :style="calStyle"
            @pointerdown="onCalendarPointerDown"
            @pointermove="onCalendarPointerMove"
            @pointerup="onCalendarPointerUp"
            @pointercancel="onCalendarPointerCancel"
            @click.capture="onCalendarClickCapture"
          >
            <button
              v-for="(c, idx) in calendarDays"
              :key="idx"
              class="cell"
              :class="{
                empty: !c.day,
                active: c.ymd && c.ymd === selectedDate,
                today: c.ymd === todayYmd,
                hasMatch: c.ymd && (matchCountByDate[c.ymd] ?? 0) > 0,
              }"
              :disabled="!c.day"
              @click="pickDate(c.ymd)"
            >
              <template v-if="c.day">
                <span class="num">{{ c.day }}</span>
                <span v-if="c.ymd && matchCountByDate[c.ymd]" class="cnt">{{ matchCountByDate[c.ymd] }}경기</span>
              </template>
            </button>
          </div>

          <div class="calFoot">
            <div class="selectedInfo">{{ selectedDateLabel }}</div>
            <button class="todayBtn" type="button" @click="goToday">오늘</button>
          </div>
          </div>
        </section>

        <!-- RIGHT: Match list -->
        <section class="right">
          <div class="rightTitle">경기선택</div>

          <div v-if="!selectedDate" class="hint">
            {{ loadError || '왼쪽에서 날짜를 선택해줘.' }}
          </div>

          <div v-else class="list" :class="{ hasSelection: Boolean(selectedMatchId) }">
            <div v-if="loadingMatches" class="hint">경기 목록을 불러오는 중입니다.</div>
            <div v-else-if="loadError" class="hint">{{ loadError }}</div>
            <template v-else>
              <div
                v-for="m in matchList"
                :key="m.id"
                class="matchRow"
                :class="{
                  selected: m.id === selectedMatchId,
                  'match-complete': isMatchComplete(m),
                  'match-pending': !isMatchComplete(m),
                  'has-mine': matchHasMine(m),
                }"
                @click="selectMatch(m.id)"
                role="button"
                tabindex="0"
                @keydown.enter="selectMatch(m.id)"
              >
              <div class="time">
                <div class="t">{{ m.time || '시간 미정' }}</div>
                <div class="timeRow">
                  <div class="competition">{{ m.league }}</div>
                  <div class="roundBadge">{{ m.round }}</div>
                </div>
                <div class="stadium"><span v-for="(line, lineIndex) in stadiumLines(m.stadium)" :key="lineIndex">{{ line }}</span></div>
                <div v-if="matchHasMine(m)" class="mineTag">내 경기</div>
              </div>

              <div class="vs">
                <button
                  v-for="side in (['home', 'away'] as const)"
                  :key="side"
                  class="teamChoice"
                  :class="[side === 'home' ? 'teamHome' : 'teamAway', { chosen: m.id === selectedMatchId && selectedTeam === side, mine: teamIsMine(m, side) }]"
                  :style="{ order: side === 'home' ? 0 : 2 }"
                  type="button"
                  @click.stop="selectTeam(m.id, side)"
                >
                  <div class="teamHead">
                    <div class="team">{{ m[side].name }}</div>
                    <div class="teamStatus" :class="`teamStatus-${teamStatusTone(m.inputStatus[side === 'home' ? 'H' : 'A'])}`">
                      {{ teamAnalysisLabel(m.inputStatus[side === 'home' ? 'H' : 'A']) }}
                    </div>
                  </div>
                  <div class="analystLine" :aria-label="side === 'home' ? '홈팀 분석관' : '원정팀 분석관'">
                    <span
                      v-for="row in analystRows(teamCollaboration(m, side))"
                      :key="row.key"
                      class="analyst"
                      :class="{ me: isMe(row.participant), isMain: row.label === 'Main' }"
                    >
                      <span class="analystRole" :class="`role-${row.label.toLowerCase()}`"><span class="roleBadge">{{ row.label }}</span></span>
                      <span class="analystName">{{ (row.participant.name || row.participant.uid).split('@')[0] }}</span>
                    </span>
                  </div>
                </button>
                <div class="mid">
                  <div class="score" :aria-label="`스코어 ${m.score.home} 대 ${m.score.away}`">
                    <span>{{ m.score.home }}</span><span class="scoreDivider">:</span><span>{{ m.score.away }}</span>
                  </div>
                </div>
              </div>
              </div>

              <div v-if="matchList.length === 0" class="hint">
                선택한 날짜에 경기가 없어.
              </div>
            </template>
          </div>
        </section>
      </div>

      <div class="bottomBar">
        <button class="btn ghost" @click="onCancel">Cancel</button>
        <button class="btn primary" :disabled="!selectedMatch || !selectedTeam" @click="onSubmit">Submit</button>
      </div>

      <div v-if="roleDialogOpen" class="roleOverlay" @click.self="roleDialogOpen = false">
        <section class="roleDialog" role="dialog" aria-modal="true" aria-label="분석 역할 선택">
          <div class="roleHeader">
            <h2>분석 역할 선택</h2>
            <button class="roleClose" type="button" aria-label="닫기" @click="roleDialogOpen = false">
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" /></svg>
            </button>
          </div>
          <p v-if="selectedTeamFinished && selectedParticipantRole">분석이 종료된 경기입니다. 기존 참여 역할 또는 매니저 권한으로 입장할 수 있습니다.</p>
          <p v-else-if="selectedTeamFinished">분석이 종료된 경기입니다. 매니저 권한으로 입장할 수 있습니다.</p>
          <p v-else-if="selectedParticipantRole">이미 {{ ROLE_NAMES[selectedParticipantRole] }}로 참여 중이라 같은 역할로만 재입장할 수 있습니다.</p>
          <p v-else>{{ selectedTeam === 'home' ? selectedMatch?.home.name : selectedMatch?.away.name }} 입력 역할을 선택하세요.</p>
          <button
            v-for="role in (['primary', 'assistant', 'manager'] as const)"
            :key="role"
            class="roleChoice"
            :class="role"
            :disabled="roleState[role].disabled"
            @click="chooseRole(role)"
          >
            {{ roleState[role].label }}
          </button>
          <button class="roleCancel" @click="roleDialogOpen = false">취소</button>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { box-sizing: border-box; width: 1280px; height: 800px; margin: 0 auto; display: grid; place-items: center; position: relative; overflow: hidden; background: #303134; }
.bg {
  position: absolute; inset: 0;
  background:
    radial-gradient(1200px 500px at 50% 25%, rgba(255,255,255,0.08), transparent 60%),
    radial-gradient(900px 400px at 20% 70%, rgba(0,217,255,0.10), transparent 55%),
    radial-gradient(900px 400px at 80% 70%, rgba(241,180,0,0.08), transparent 55%),
    linear-gradient(180deg, rgba(0,0,0,0.04), rgba(0,0,0,0.12));
}

.frame {
  box-sizing: border-box;
  position: relative;
  width: 1280px;
  height: 800px;
  border-radius: 0;
  background: #303134;
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.3);
  backdrop-filter: blur(8px);
  display: grid;
  grid-template-columns: 1280px;
  grid-template-rows: 48px 1fr 60px;
  grid-template-areas: "schedule-top" "schedule-body" "schedule-bottom";
  overflow: hidden;
}

.topBar {
  grid-area: schedule-top;
  width: 100%; min-width: 0;
  display: flex; align-items: center; justify-content: center;
  position: relative;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.title { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; }
.topRight { position: absolute; right: 16px; display: flex; align-items: center; gap: 12px; }
.logoutBtn {
  height: 26px;
  padding: 0 10px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.15);
  background: transparent;
  color: rgba(255,255,255,0.65);
  font-size: 12px;
  cursor: pointer;
}
.logoutBtn:hover { background: rgba(255,255,255,0.08); color: #fff; }

.manageBtn {
  height: 26px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  border-radius: 4px;
  border: 1px solid rgba(0,217,255,0.35);
  background: rgba(0,217,255,0.08);
  color: rgba(200,245,255,0.9);
  font-size: 12px;
  text-decoration: none;
  cursor: pointer;
}
.manageBtn:hover { background: rgba(0,217,255,0.16); }

.body { grid-area: schedule-body; width: 100%; min-width: 0; min-height: 0; overflow: hidden; display: grid; grid-template-columns: 460px 820px; }

.left {
  border-right: 1px solid rgba(255,255,255,0.06);
  padding: 16px;
  display: grid;
  grid-template-rows: 44px 24px 1fr;
  gap: 10px;
}

.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.calendarFilter { display: flex; align-items: center; gap: 8px; }
.calendarFilter select {
  appearance: none; -webkit-appearance: none;
  height: 40px; padding: 0 32px 0 12px;
  border: 1px solid rgba(255,255,255,.14); border-radius: 4px;
  background: #252629 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 4.5 6 8l3.5-3.5' fill='none' stroke='%2376d7e8' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right 11px center;
  color: rgba(255,255,255,.9);
  font: inherit; font-size: 15px; font-weight: 750; color-scheme: dark; cursor: pointer;
}
.calendarFilter select:focus { outline: none; border-color: rgba(93,204,229,.72); box-shadow: 0 0 0 2px rgba(93,204,229,.18); }
.calendarFilter select option { background: #252629; color: rgba(255,255,255,.9); font-weight: 700; }
.calendarFilter select.monthSelect {
  flex: 0 0 84px; box-sizing: border-box;
  width: 84px; min-width: 84px; max-width: 84px;
  field-sizing: fixed; font-variant-numeric: tabular-nums;
}
/* 지원 브라우저(Chromium 135+)에서는 펼침 목록까지 다크 테마로 */
@supports (appearance: base-select) {
  .calendarFilter select, .calendarFilter select::picker(select) { appearance: base-select; }
  .calendarFilter select { display: inline-flex; align-items: center; }
  .calendarFilter select::picker-icon { display: none; }
  .calendarFilter select::picker(select) {
    max-height: 280px; margin-top: 4px; padding: 4px; overflow-y: auto;
    background: #252629; border: 1px solid rgba(255,255,255,.14); border-radius: 6px;
    box-shadow: 0 12px 28px rgba(0,0,0,.5); scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.2) transparent;
  }
  .calendarFilter select option { min-height: 40px; padding: 0 12px; border-radius: 4px; font-size: 15px; }
  .calendarFilter select option::checkmark { display: none; }
  .calendarFilter select option:checked { background: rgba(241,180,0,.14); color: #f1b400; }
  .calendarFilter select option:focus-visible { outline: none; background: rgba(255,255,255,.08); }
  .calendarFilter select option:active { background: rgba(255,255,255,.12); }
}
.monthNav { display: flex; align-items: center; gap: 8px; }
.iconBtn {
  width: 40px; height: 40px; border-radius: 4px; font-size: 18px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.8);
  cursor: pointer;
}
.dow {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  column-gap: 6px;
  color: rgba(255,255,255,0.75);
  font-size: 14px;
  font-weight: 700;
  text-align: center;
  align-items: center;
}

.calViewport { min-height: 0; overflow: hidden; }
.cal {
  will-change: transform;
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 8px 6px;
  grid-auto-rows: 76px;
  align-content: start;
  touch-action: pan-y; /* 가로 스와이프는 월 이동으로 처리 */
  user-select: none;
}
.cell {
  position: relative;
  box-sizing: border-box;
  height: 76px;
  padding: 10px 4px 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  border-radius: 4px;
  border: 1px solid rgba(0,0,0,0.25);
  background: #404145;
  color: rgba(255,255,255,0.6);
  text-align: center;
  cursor: pointer;
  transition: background .12s, border-color .12s;
}
.cell .num { font-size: 19px; font-weight: 500; line-height: 1; font-variant-numeric: tabular-nums; }
.cell .cnt { font-size: 13px; font-weight: 700; line-height: 1; color: #fff; white-space: nowrap; }
.cell:not(.empty):active { filter: brightness(1.12); }
.cell.empty { border-color: transparent; background: transparent; cursor: default; }
/* 경기가 있는 날: 숫자를 밝게 하고 왼쪽에 가는 선만 둔다 */
.cell.hasMatch { color: #fff; background: #65676c; border-color: rgba(0,0,0,0.25); }
.cell.hasMatch .num { font-weight: 700; }
.cell.hasMatch::before { content: ''; position: absolute; left: 10px; right: 10px; top: -1px; height: 2px; border-radius: 0 0 2px 2px; background: #3fb3cc; }
/* 오늘: 숫자에만 얇은 고리 */
.cell.today .num { color: rgba(255,255,255,0.85); display: inline-grid; place-items: center; min-width: 28px; height: 28px; margin: -5px 0 0; padding: 0 6px; border-radius: 14px; border: 1px solid rgba(255,255,255,0.6); box-sizing: border-box; }
.cell.active { border-color: #f1b400; background: #e4e5e7; color: #1c1d20; }
.cell.active .num, .cell.active.today .num { color: #1c1d20; border-color: rgba(0,0,0,0.5); }
.cell.active .cnt { color: #9a6c00; }
.cell.active.hasMatch::before { background: #f1b400; }

.calFoot { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.06); }
.selectedInfo { color: rgba(255,255,255,0.92); font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; }
.todayBtn {
  height: 30px; padding: 0 14px; border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.18); background: transparent;
  color: rgba(255,255,255,0.9); font-size: 14px; font-weight: 600; cursor: pointer;
}
.todayBtn:hover { background: rgba(255,255,255,0.06); }
.todayBtn:active { background: rgba(255,255,255,0.12); }

/* 1fr 은 min 이 auto 라 경기가 많으면 목록 높이만큼 늘어나 스크롤이 생기지 않는다 → minmax(0, 1fr) 로 고정.
   하단 버튼바가 translateY(-22px) 로 본문 위에 22px 겹치므로 그만큼 아래 여백을 더 둔다. */
.right { min-height: 0; overflow: hidden; padding: 16px 16px 38px; display: grid; grid-template-rows: 36px minmax(0, 1fr); gap: 10px; }
.rightTitle { color: rgba(255,255,255,0.85); font-weight: 800; font-size: 18px; text-align: center; }

.hint { color: rgba(255,255,255,0.45); display: grid; place-items: center; font-size: 14px; }
.roleOverlay { position: absolute; inset: 0; z-index: 5; display: grid; place-items: center; background: rgba(0,0,0,.58); }
.roleDialog { width: min(520px, calc(100% - 40px)); padding: 24px; border: 1px solid rgba(255,255,255,.16); border-radius: 6px; background: #2a2b2e; box-shadow: 0 20px 60px rgba(0,0,0,.55); }
.roleHeader { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.roleDialog h2 { margin: 0; color: #fff; font-size: 20px; }
.roleClose { flex: none; display: grid; place-items: center; width: 40px; height: 40px; align-self: flex-start; margin: -14px -12px -8px 0; border: 0; border-radius: 4px; background: transparent; color: rgba(255,255,255,.6); cursor: pointer; }
.roleClose:active { background: rgba(255,255,255,.1); color: #fff; }
.roleDialog p { margin: 8px 0 20px; color: rgba(255,255,255,.55); font-size: 13px; }
.roleChoice, .roleCancel { width: 100%; height: 46px; margin-top: 8px; border-radius: 4px; cursor: pointer; font-weight: 800; }
.roleChoice.primary { border: 1px solid #eab529; background: #eab529; color: #16120a; }
.roleChoice.assistant { border: 1px solid #42b8d2; background: rgba(66,184,210,.12); color: #8ce6f5; }
.roleChoice.manager { border: 1px solid #7fd99a; background: rgba(80,200,120,.12); color: #a6ecbb; }
.roleChoice:disabled { border-color: rgba(255,255,255,.16); background: rgba(255,255,255,.08); color: rgba(255,255,255,.4); cursor: not-allowed; }
.roleCancel { border: 1px solid rgba(255,255,255,.15); background: transparent; color: rgba(255,255,255,.7); }

/* 높이가 고정된 스크롤 영역 안에서 카드가 눌려 찌그러지지 않도록 flex 세로 배치 + 카드 flex-shrink: 0. */
.list { display: flex; flex-direction: column; gap: 12px; overflow-y: auto; padding: 2px 6px 12px 2px; }
.list > * { flex-shrink: 0; }

.matchRow {
  display: grid;
  grid-template-columns: 116px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
  min-height: 120px;
  padding: 14px 18px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,.10);
  background: #252629;
  cursor: pointer;
  text-align: left;
  transition: border-color 140ms ease, box-shadow 140ms ease, background 140ms ease, transform 140ms ease, filter 160ms ease, opacity 160ms ease;
}
/* 한 팀이라도 입력 대기면 경기 카드 자체를 살아있는 작업 대상으로 표시한다. */
.matchRow.match-pending {
  border: 1px solid rgba(0,217,255,0.44);
  box-shadow: inset 0 0 0 1px rgba(0,217,255,0.04);
}
/* 양 팀 모두 final이면 완료 경기의 테두리만 의도적으로 흐리게 한다. */
.matchRow.match-complete {
  border-color: rgba(255,255,255,0.10);
  box-shadow: none;
}
.matchRow:active { background: #2c2d30; }
/* 경기를 하나 선택하면 나머지 카드는 채도·밝기를 낮춰 선택한 카드가 돋보이게 한다. 눌러서 다시 고를 수는 있다. */
.list.hasSelection .matchRow:not(.selected) { filter: saturate(.25) brightness(.88); opacity: .62; }
.matchRow.selected {
  background: rgba(241,180,0,0.055);
  box-shadow: 0 0 0 2px rgba(241,180,0,0.46);
}
.time { display: grid; gap: 7px; align-content: center; min-width: 0; }
.timeRow { display: flex; align-items: center; gap: 8px; }
.time .t { color: rgba(255,255,255,.94); font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; }
.roundBadge { color: rgba(255,255,255,.62); font-size: 13px; font-weight: 750; }
.competition { color: #76d7e8; font-size: 13px; font-weight: 750; }
.stadium { color: rgba(255,255,255,.5); font-size: 11.5px; line-height: 1.35; word-break: keep-all; }
.stadium span { display: block; }
.vs {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 84px minmax(0, 1fr);
  align-items: stretch;
  gap: 10px;
  min-width: 0;
}
.mid { order: 1; display: grid; place-items: center; }
.score { display: flex; align-items: center; justify-content: center; gap: 7px; min-width: 76px; padding: 14px 4px; border-radius: 6px; background: rgba(255,255,255,.06); color: rgba(255,255,255,.95); font-size: 30px; font-weight: 850; line-height: 1; font-variant-numeric: tabular-nums; }
.scoreDivider { color: rgba(255,255,255,.35); font-size: 20px; }

/* 팀 카드 = 선택 버튼. 팀명·진행 상태·분석관을 한 덩어리로 묶는다. */
.teamChoice {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 10px;
  min-height: 96px;
  padding: 14px 16px;
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 8px;
  background: rgba(255,255,255,.03);
  cursor: pointer;
  font: inherit;
  transition: border-color 120ms ease, background 120ms ease;
}
.teamChoice:active { background: rgba(255,255,255,.09); }
/* 노란색은 '내가 고른 팀' 표시에만 쓴다. */
.teamChoice.chosen, .teamChoice.teamHome.chosen, .teamChoice.teamAway.chosen { border-color: rgba(241,180,0,.85); background: rgba(241,180,0,.12); box-shadow: inset 0 0 0 1px rgba(241,180,0,.35); }
.teamChoice:focus-visible { outline: 2px solid #5dcce5; outline-offset: 2px; }
/* 내가 참여 중인 팀: 보라색 계열. 노란색(선택)·청록(진행)·초록(종료)과 겹치지 않게 한다. */
.teamChoice.mine:not(.chosen) { border-color: rgba(150,130,255,.42); background: linear-gradient(90deg, rgba(150,130,255,.04), rgba(150,130,255,.11)); }
.teamChoice.teamAway.mine:not(.chosen) { background: linear-gradient(270deg, rgba(150,130,255,.04), rgba(150,130,255,.11)); }
.matchRow.has-mine { border-left: 3px solid #9682ff; }
.mineTag { justify-self: start; margin-top: 2px; padding: 3px 8px; border-radius: 4px; background: rgba(150,130,255,.13); color: #bdb2fb; font-size: 12px; font-weight: 700; }
.analyst.me { background: rgba(150,130,255,.08); }
.analyst.me .analystName { color: #9e97c4; font-weight: 600; }
.analyst.me .role-main .roleBadge { background: rgba(150,130,255,.14); color: #a49dcf; }
.analyst.me .role-sub .roleBadge { color: #9890c4; box-shadow: inset 0 0 0 1px rgba(150,130,255,.26); }
.teamHome { text-align: right; justify-items: end; background: linear-gradient(90deg, rgba(255,255,255,.02), rgba(96,150,200,.12)); }
.teamAway { text-align: left; justify-items: start; background: linear-gradient(270deg, rgba(255,255,255,.02), rgba(96,150,200,.12)); }

.teamHead { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; min-width: 0; max-width: 100%; }
.teamHome .teamHead { flex-direction: row-reverse; }
.team {
  min-width: 0;
  color: #fff;
  font-size: 21px;
  font-weight: 800;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.teamChoice.chosen .team { color: #fff; }
.teamStatus { flex: none; padding: 5px 9px; border-radius: 4px; font-size: 13px; line-height: 1.1; white-space: nowrap; font-weight: 800; }
.teamStatus-waiting { background: rgba(255,255,255,.06); color: rgba(255,255,255,.5); }
.teamStatus-live { background: rgba(110,170,185,.12); color: #7eadb8; }
.teamStatus-done { background: rgba(120,175,140,.12); color: #84ab92; }

.analystLine { display: flex; flex-direction: column; flex-wrap: nowrap; gap: 3px; width: 100%; min-width: 0; align-items: stretch; }
.analyst { box-sizing: border-box; display: flex; align-items: center; gap: 12px; min-width: 0; padding: 7px 12px; border-radius: 4px; background: rgba(255,255,255,.03); font-size: 14px; line-height: 20px; }
.teamHome .analyst { flex-direction: row-reverse; }
.teamHome .analystName { text-align: right; }
.teamHome .analystRole { justify-content: flex-end; }
.analystRole { flex: none; width: 84px; display: flex; align-items: center; }
/* 역할 뱃지: 모양으로 구분한다 — Main은 채움, Sub는 테두리, Manager는 회청색 채움. 색은 늘리지 않는다. */
.roleBadge { padding: 2px 9px; border-radius: 3px; font-size: 11px; font-weight: 800; letter-spacing: .06em; line-height: 16px; text-transform: uppercase; }
.role-main .roleBadge { background: rgba(255,255,255,.07); color: rgba(255,255,255,.52); }
.role-sub .roleBadge { background: transparent; color: rgba(255,255,255,.36); box-shadow: inset 0 0 0 1px rgba(255,255,255,.12); }
.role-manager .roleBadge { background: rgba(255,255,255,.06); color: #8b95a1; }
.analyst.isMain { background: rgba(255,255,255,.055); }
.analyst.isMain .analystName { color: rgba(255,255,255,.6); font-weight: 600; }
.analystName { flex: 1 1 auto; min-width: 0; overflow: hidden; color: rgba(255,255,255,.46); text-overflow: ellipsis; white-space: nowrap; }
.analystEmpty { color: rgba(255,255,255,.35); font-size: 14px; line-height: 22px; }

.bottomBar {
  grid-area: schedule-bottom;
  width: 100%; min-width: 0;
  border-top: 1px solid rgba(255,255,255,0.06);
  display: flex; align-items: center; justify-content: center;
  gap: 14px;
  /* Keep the actions clear of the tablet/browser bottom edge. */
  transform: translateY(-22px);
}
.btn {
  height: 36px; min-width: 120px; padding: 0 16px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  cursor: pointer;
}
.btn.ghost { background: rgba(255,255,255,0.02); color: rgba(255,255,255,0.75); }
.btn.primary { background: #f1b400; border-color: #f1b400; color: #111; font-weight: 800; }
.btn:disabled { opacity: 0.35; cursor: not-allowed; }
</style>
