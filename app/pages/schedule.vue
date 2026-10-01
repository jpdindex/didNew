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

const selectedDate = ref<string>('') // YYYY-MM-DD
const selectedMatchId = ref<string>('')
const selectedTeam = ref<'home' | 'away' | null>(null)
const roleDialogOpen = ref(false)
const matches = ref<MatchItem[]>([])
const loadingMatches = ref(false)
const loadError = ref('')
const { request } = useBackendApi()

function toYMD(y: number, m0: number, d: number) {
  const mm = String(m0 + 1).padStart(2, '0')
  const dd = String(d).padStart(2, '0')
  return `${y}-${mm}-${dd}`
}

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

function applyCalendarView() {
  selectedDate.value = ''
  selectedMatchId.value = ''
  selectedTeam.value = null
  loadMatches()
}

function pickDate(ymd?: string) {
  if (!ymd) return
  selectedDate.value = ymd
  selectedMatchId.value = '' // 날짜 바꾸면 경기 선택 초기화
  selectedTeam.value = null
}

function normalizeDate(value: string) {
  const digits = value.replace(/\D/g, '')
  return digits.length >= 8 ? `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}` : value
}

async function loadMatches() {
  loadingMatches.value = true
  loadError.value = ''
  try {
    const payload = await request<{ matches: Array<{
      gmId: string; date: string; kickoffTime: string | null; leagueId: string; round: number | null
      stadiumName: string; home: { name: string }; away: { name: string }
      score: { home: number; away: number }
      inputStatus: { H: TeamInputStatus; A: TeamInputStatus }
      collaboration?: MatchItem['collaboration']
    }> }>(`/api/v1/match-input/matches?year=${viewYear.value}&month=${viewMonth.value + 1}`)
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
    matches.value = []
    loadError.value = error instanceof Error ? error.message : '경기 목록을 불러오지 못했습니다.'
  } finally {
    loadingMatches.value = false
  }
}

onMounted(loadMatches)

// 리그 필터: 월이 바뀌어도 선택은 유지하고, 그 달에 경기가 없어도 옵션은 남긴다.
const selectedLeague = ref<string>('all')
const leagueOptions = computed(() => {
  const leagues = new Set(matches.value.map(match => match.league).filter(Boolean))
  if (selectedLeague.value !== 'all') leagues.add(selectedLeague.value)
  return [...leagues].sort((a, b) => a.localeCompare(b))
})

const visibleMatches = computed(() => (
  selectedLeague.value === 'all' ? matches.value : matches.value.filter(match => match.league === selectedLeague.value)
))

function applyLeagueFilter() {
  selectedMatchId.value = ''
  selectedTeam.value = null
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

function teamCollaboration(match: MatchItem, team: 'home' | 'away') {
  return team === 'home' ? match.collaboration.H : match.collaboration.A
}

function orderedParticipants(summary: CollaborationSummary) {
  const order: Record<string, number> = { primary: 0, assistant: 1, manager: 2 }
  return [...(summary.participants || [])].sort((a, b) => (order[a.role || ''] ?? 1) - (order[b.role || ''] ?? 1))
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

// 팀 입력이 끝나면(RAW 확정 또는 BASIC 제출) 매니저만 들어갈 수 있다.
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
  // 끝난 경기의 주/부 기록은 명단으로만 남는다. 다시 들어오려면 매니저로 입장한다.
  if (selectedTeamFinished.value) return direct?.role === 'manager' ? 'manager' : null
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

  const primary = finished ? { disabled: true, label: '주 분석관으로 분석 진행' }
    : mine === 'primary' ? { disabled: false, label: '주 분석관으로 재입장' }
    : mine ? { disabled: true, label: '주 분석관으로 분석 진행' }
    : selectedTeamHasOtherPrimary.value ? { disabled: true, label: '주 분석관이 이미 입장했습니다' }
    : { disabled: false, label: '주 분석관으로 분석 진행' }

  const assistant = finished ? { disabled: true, label: '부 분석관으로 분석 진행' }
    : mine === 'assistant' ? { disabled: false, label: '부 분석관으로 재입장' }
    : mine ? { disabled: true, label: '부 분석관으로 분석 진행' }
    : !selectedTeamHasPrimary.value ? { disabled: true, label: '주 분석관 입장 후 참여할 수 있습니다' }
    : selectedTeamHasOtherAssistant.value ? { disabled: true, label: '부 분석관이 이미 입장했습니다' }
    : { disabled: false, label: '부 분석관으로 분석 진행' }

  const manager = mine === 'manager' ? { disabled: false, label: '매니저로 재입장' }
    : mine ? { disabled: true, label: '매니저 권한으로 입장' }
    : recorderLevel.value !== 'advanced' ? { disabled: true, label: '매니저 권한(ADVANCED)이 필요합니다' }
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
          <div class="meta">did.matchison.com · v0.8.17</div>
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
              <button class="iconBtn" @click="prevMonth" aria-label="Prev month">‹</button>
              <button class="iconBtn" @click="nextMonth" aria-label="Next month">›</button>
            </div>
          </div>

          <div class="dow">
            <span>일</span><span>월</span><span>화</span><span>수</span><span>목</span><span>금</span><span>토</span>
          </div>

          <div class="cal">
            <button
              v-for="(c, idx) in calendarDays"
              :key="idx"
              class="cell"
              :class="{
                empty: !c.day,
                active: c.ymd && c.ymd === selectedDate,
                hasMatch: c.ymd && visibleMatches.some(match => match.date === c.ymd),
              }"
              :disabled="!c.day"
              @click="pickDate(c.ymd)"
            >
              <span v-if="c.day">{{ c.day }}</span>
            </button>
          </div>
        </section>

        <!-- RIGHT: Match list -->
        <section class="right">
          <div class="rightTitle">경기선택</div>

          <div v-if="!selectedDate" class="hint">
            왼쪽에서 날짜를 선택해줘.
          </div>

          <div v-else class="list">
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
                <div class="stadium">{{ m.stadium }}</div>
              </div>

              <div class="vs">
                <button
                  v-for="side in (['home', 'away'] as const)"
                  :key="side"
                  class="teamChoice"
                  :class="[side === 'home' ? 'teamHome' : 'teamAway', { chosen: m.id === selectedMatchId && selectedTeam === side }]"
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
                    <span v-for="participant in orderedParticipants(teamCollaboration(m, side))" :key="participant.uid" class="analyst">
                      <span class="analystRole" :class="{ main: participant.role === 'primary' }">{{ participant.role === 'primary' ? 'Main' : participant.role === 'manager' ? 'Manager' : 'Sub' }}</span>
                      <span class="analystName">{{ (participant.name || participant.uid).split('@')[0] }}</span>
                    </span>
                    <span v-if="!teamCollaboration(m, side).participants?.length" class="analystEmpty">배정된 분석관 없음</span>
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
          <p v-if="selectedTeamFinished">분석이 종료된 경기입니다. 매니저 권한으로만 입장할 수 있습니다.</p>
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
.page { box-sizing: border-box; width: 1280px; height: 800px; margin: 0 auto; display: grid; place-items: center; position: relative; overflow: hidden; background: #0b0f17; }
.bg {
  position: absolute; inset: 0;
  background:
    radial-gradient(1200px 500px at 50% 25%, rgba(255,255,255,0.08), transparent 60%),
    radial-gradient(900px 400px at 20% 70%, rgba(0,217,255,0.10), transparent 55%),
    radial-gradient(900px 400px at 80% 70%, rgba(241,180,0,0.08), transparent 55%),
    linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.78));
}

.frame {
  box-sizing: border-box;
  position: relative;
  width: 1280px;
  height: 800px;
  border-radius: 0;
  background: rgba(10, 14, 22, 0.78);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
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
.meta { color: rgba(255,255,255,0.35); font-size: 12px; }
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
  height: 32px; padding: 0 28px 0 10px;
  border: 1px solid rgba(255,255,255,.14); border-radius: 4px;
  background: #171b22 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 4.5 6 8l3.5-3.5' fill='none' stroke='%2376d7e8' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right 9px center;
  color: rgba(255,255,255,.9);
  font: inherit; font-size: 13px; font-weight: 750; color-scheme: dark; cursor: pointer;
}
.calendarFilter select:focus { outline: none; border-color: rgba(93,204,229,.72); box-shadow: 0 0 0 2px rgba(93,204,229,.18); }
.calendarFilter select option { background: #171b22; color: rgba(255,255,255,.9); font-weight: 700; }
.calendarFilter select.monthSelect {
  flex: 0 0 76px; box-sizing: border-box;
  width: 76px; min-width: 76px; max-width: 76px;
  field-sizing: fixed; font-variant-numeric: tabular-nums;
}
/* 지원 브라우저(Chromium 135+)에서는 펼침 목록까지 다크 테마로 */
@supports (appearance: base-select) {
  .calendarFilter select, .calendarFilter select::picker(select) { appearance: base-select; }
  .calendarFilter select { display: inline-flex; align-items: center; }
  .calendarFilter select::picker-icon { display: none; }
  .calendarFilter select::picker(select) {
    max-height: 280px; margin-top: 4px; padding: 4px; overflow-y: auto;
    background: #171b22; border: 1px solid rgba(255,255,255,.14); border-radius: 6px;
    box-shadow: 0 12px 28px rgba(0,0,0,.5); scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.2) transparent;
  }
  .calendarFilter select option { min-height: 36px; padding: 0 10px; border-radius: 4px; font-size: 13px; }
  .calendarFilter select option::checkmark { display: none; }
  .calendarFilter select option:checked { background: rgba(241,180,0,.14); color: #f1b400; }
  .calendarFilter select option:focus-visible { outline: none; background: rgba(255,255,255,.08); }
  .calendarFilter select option:active { background: rgba(255,255,255,.12); }
}
.monthNav { display: flex; align-items: center; gap: 10px; }
.iconBtn {
  width: 28px; height: 28px; border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.8);
  cursor: pointer;
}
.dow {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  color: rgba(255,255,255,0.5);
  font-size: 12px;
  text-align: center;
}

.cal {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 12px 6px;
  align-content: start;
}
.cell {
  height: 56px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.03);
  color: rgba(255,255,255,0.75);
  cursor: pointer;
}
.cell.empty { border-color: transparent; background: transparent; cursor: default; }
.cell.active { border-color: rgba(241,180,0,0.7); box-shadow: 0 0 0 3px rgba(241,180,0,0.12); }
.cell.hasMatch:not(.active) { border-color: rgba(0,217,255,0.35); }

.right { padding: 16px; display: grid; grid-template-rows: 36px 1fr; gap: 10px; }
.rightTitle { color: rgba(255,255,255,0.85); font-weight: 800; font-size: 18px; text-align: center; }

.hint { color: rgba(255,255,255,0.45); display: grid; place-items: center; font-size: 14px; }
.roleOverlay { position: absolute; inset: 0; z-index: 5; display: grid; place-items: center; background: rgba(0,0,0,.58); }
.roleDialog { width: min(520px, calc(100% - 40px)); padding: 24px; border: 1px solid rgba(255,255,255,.16); border-radius: 6px; background: #151a22; box-shadow: 0 20px 60px rgba(0,0,0,.55); }
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

.list { display: grid; gap: 10px; align-content: start; overflow: auto; padding: 2px 6px 12px 2px; }

.matchRow {
  display: grid;
  grid-template-columns: 116px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
  min-height: 98px;
  padding: 12px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,.10);
  background: rgba(255,255,255,0.025);
  cursor: pointer;
  text-align: left;
  transition: border-color 140ms ease, box-shadow 140ms ease, background 140ms ease, transform 140ms ease;
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
.matchRow:active { background: rgba(255,255,255,.055); }
.matchRow.selected {
  background: rgba(241,180,0,0.055);
  box-shadow: 0 0 0 2px rgba(241,180,0,0.46);
}
.time { display: grid; gap: 5px; align-content: center; min-width: 0; }
.timeRow { display: flex; align-items: center; gap: 8px; }
.time .t { color: rgba(255,255,255,.94); font-size: 18px; font-weight: 800; font-variant-numeric: tabular-nums; }
.roundBadge { color: rgba(255,255,255,.58); font-size: 12px; font-weight: 750; }
.competition { color: #76d7e8; font-size: 12px; font-weight: 750; }
.stadium { color: rgba(255,255,255,.43); font-size: 12px; line-height: 1.35; word-break: keep-all; }
.vs {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 68px minmax(0, 1fr);
  align-items: stretch;
  gap: 8px;
  min-width: 0;
}
.mid { order: 1; display: grid; place-items: center; }
.score { display: flex; align-items: center; justify-content: center; gap: 7px; min-width: 64px; padding: 10px 4px; border-radius: 4px; background: rgba(255,255,255,.05); color: rgba(255,255,255,.92); font-size: 24px; font-weight: 850; line-height: 1; font-variant-numeric: tabular-nums; }
.scoreDivider { color: rgba(255,255,255,.35); font-size: 16px; }

/* 팀 카드 = 선택 버튼. 팀명·진행 상태·분석관을 한 덩어리로 묶는다. */
.teamChoice {
  min-width: 0;
  display: grid;
  align-content: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 6px;
  background: rgba(255,255,255,.03);
  cursor: pointer;
  font: inherit;
  transition: border-color 120ms ease, background 120ms ease;
}
.teamChoice:active { background: rgba(255,255,255,.09); }
/* 노란색은 '내가 고른 팀' 표시에만 쓴다. */
.teamChoice.chosen { border-color: rgba(241,180,0,.85); background: rgba(241,180,0,.10); box-shadow: inset 0 0 0 1px rgba(241,180,0,.35); }
.teamChoice:focus-visible { outline: 2px solid #5dcce5; outline-offset: 2px; }
.teamHome { text-align: right; justify-items: end; }
.teamAway { text-align: left; justify-items: start; }

.teamHead { display: flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%; }
.teamHome .teamHead { flex-direction: row-reverse; }
.team {
  min-width: 0;
  color: rgba(255,255,255,0.92);
  font-size: 17px;
  font-weight: 800;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.teamChoice.chosen .team { color: #fff; }
.teamStatus { flex: none; padding: 3px 6px; border-radius: 3px; font-size: 11px; line-height: 1.1; white-space: nowrap; font-weight: 800; }
.teamStatus-waiting { background: rgba(255,255,255,.07); color: rgba(255,255,255,.55); }
.teamStatus-live { background: rgba(0,217,255,.12); color: #76d7e8; }
.teamStatus-done { background: rgba(80,200,120,.13); color: #7fd99a; }

.analystLine { display: flex; flex-wrap: wrap; gap: 4px 10px; min-width: 0; max-width: 100%; min-height: 18px; align-items: center; }
.teamHome .analystLine { justify-content: flex-end; }
.analyst { display: inline-flex; align-items: center; gap: 5px; min-width: 0; max-width: 100%; font-size: 12px; line-height: 18px; }
.analystRole { flex: none; padding: 0 4px; border: 1px solid rgba(255,255,255,.16); border-radius: 3px; color: rgba(255,255,255,.5); font-size: 10px; font-weight: 800; line-height: 14px; }
.analystRole.main { border-color: rgba(255,255,255,.3); color: rgba(255,255,255,.8); }
.analystName { overflow: hidden; color: rgba(255,255,255,.62); text-overflow: ellipsis; white-space: nowrap; }
.analystEmpty { color: rgba(255,255,255,.28); font-size: 12px; line-height: 18px; }

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
