<script setup lang="ts">
import { computed, ref } from 'vue'
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
  inputStatus: { H: { rawStatus: string | null; completed: boolean }; A: { rawStatus: string | null; completed: boolean } }
}

const today = new Date()
const viewYear = ref(today.getFullYear())
const viewMonth = ref(today.getMonth()) // 0-11

const selectedDate = ref<string>('') // YYYY-MM-DD
const selectedMatchId = ref<string>('')
const matches = ref<MatchItem[]>([])
const loadingMatches = ref(false)
const loadError = ref('')
const { request } = useBackendApi()

const ymLabel = computed(() => {
  const y = viewYear.value
  const m = String(viewMonth.value + 1).padStart(2, '0')
  return `${y}.${m}`
})

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
  selectedDate.value = ''
  selectedMatchId.value = ''
  loadMatches()
}

function nextMonth() {
  if (viewMonth.value === 11) {
    viewMonth.value = 0
    viewYear.value += 1
  } else viewMonth.value += 1
  selectedDate.value = ''
  selectedMatchId.value = ''
  loadMatches()
}

function pickDate(ymd?: string) {
  if (!ymd) return
  selectedDate.value = ymd
  selectedMatchId.value = '' // 날짜 바꾸면 경기 선택 초기화
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
      inputStatus: { H: { rawStatus: string | null; completed: boolean }; A: { rawStatus: string | null; completed: boolean } }
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
      inputStatus: item.inputStatus,
    }))
  } catch (error) {
    matches.value = []
    loadError.value = error instanceof Error ? error.message : '경기 목록을 불러오지 못했습니다.'
  } finally {
    loadingMatches.value = false
  }
}

onMounted(loadMatches)

const matchList = computed(() => {
  if (!selectedDate.value) return []
  return matches.value.filter(match => match.date === selectedDate.value)
})

function selectMatch(id: string) {
  selectedMatchId.value = id
}

const selectedMatch = computed(() => {
  return matchList.value.find(m => m.id === selectedMatchId.value) || null
})

function teamAnalysisLabel(completed: boolean) {
  return completed ? '분석 종료' : '분석 대기'
}

function isMatchComplete(match: MatchItem) {
  return match.inputStatus.H.completed && match.inputStatus.A.completed
}

async function goTeamSelection(match?: MatchItem) {
  const m = match ?? selectedMatch.value
  if (!selectedDate.value || !m) return

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
    },
  })
}

function onSubmit() {
  if (!selectedDate.value) return
  if (!selectedMatch.value) return
  goTeamSelection()
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
            <div class="league">
              <span class="pill">전체</span>
              <span class="pill">▼</span>
            </div>

            <div class="monthNav">
              <button class="iconBtn" @click="prevMonth" aria-label="Prev month">‹</button>
              <div class="monthLabel">{{ ymLabel }}</div>
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
                hasMatch: c.ymd && matches.some(match => match.date === c.ymd),
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
              <button
                v-for="m in matchList"
                :key="m.id"
                class="matchRow"
                :class="{
                  selected: m.id === selectedMatchId,
                  'match-complete': isMatchComplete(m),
                  'match-pending': !isMatchComplete(m),
                }"
                @click="selectMatch(m.id)"
                @dblclick="goTeamSelection(m)"
                @keyup.enter="goTeamSelection(m)"
              >
              <div class="time">
                <div class="t">{{ m.time }}</div>
                <div class="sub">{{ m.league }}</div>
                <div class="sub">{{ m.stadium }}</div>
              </div>

              <div class="vs">
                <div class="teamCell teamHome">
                  <div class="team">{{ m.home.name }}</div>
                  <div class="teamStatus" :class="m.inputStatus.H.completed ? 'teamStatus-done' : 'teamStatus-waiting'">
                    {{ teamAnalysisLabel(m.inputStatus.H.completed) }}
                  </div>
                </div>
                <div class="mid">
                  <div class="vsTxt">VS</div>
                </div>
                <div class="teamCell teamAway">
                  <div class="team">{{ m.away.name }}</div>
                  <div class="teamStatus" :class="m.inputStatus.A.completed ? 'teamStatus-done' : 'teamStatus-waiting'">
                    {{ teamAnalysisLabel(m.inputStatus.A.completed) }}
                  </div>
                </div>
              </div>

              <div class="round">{{ m.round }}</div>
              </button>

              <div v-if="matchList.length === 0" class="hint">
                선택한 날짜에 경기가 없어.
              </div>
            </template>
          </div>
        </section>
      </div>

      <div class="bottomBar">
        <button class="btn ghost" @click="onCancel">Cancel</button>
        <button class="btn primary" :disabled="!selectedMatch" @click="onSubmit">Submit</button>
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

.body { grid-area: schedule-body; width: 100%; min-width: 0; min-height: 0; overflow: hidden; display: grid; grid-template-columns: 420px 860px; }

.left {
  border-right: 1px solid rgba(255,255,255,0.06);
  padding: 16px;
  display: grid;
  grid-template-rows: 44px 24px 1fr;
  gap: 10px;
}

.toolbar { display: flex; align-items: center; justify-content: space-between; }
.league { display: flex; gap: 6px; }
.pill {
  height: 26px; padding: 0 10px; border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.70);
  font-size: 12px;
  display: inline-flex; align-items: center;
}
.monthNav { display: flex; align-items: center; gap: 10px; }
.iconBtn {
  width: 28px; height: 28px; border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.8);
  cursor: pointer;
}
.monthLabel { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; min-width: 84px; text-align: center; }

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
  gap: 6px;
  align-content: start;
}
.cell {
  height: 44px;
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

.list { display: grid; gap: 8px; align-content: start; overflow: auto; padding-right: 6px; }

.matchRow {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr) 64px;
  gap: 14px;
  align-items: center;
  min-height: 78px;
  padding: 10px 12px;
  border-radius: 6px;
  background: rgba(255,255,255,0.03);
  cursor: pointer;
  text-align: left;
  transition: border-color 140ms ease, box-shadow 140ms ease, background 140ms ease;
}
/* 한 팀이라도 입력 대기면 경기 카드 자체를 살아있는 작업 대상으로 표시한다. */
.matchRow.match-pending {
  border: 1px solid rgba(0,217,255,0.44);
  box-shadow: inset 0 0 0 1px rgba(0,217,255,0.04);
}
/* 양 팀 모두 final이면 완료 경기의 테두리만 의도적으로 흐리게 한다. */
.matchRow.match-complete {
  border: 1px solid rgba(255,255,255,0.055);
  box-shadow: none;
}
.matchRow.selected {
  background: rgba(241,180,0,0.055);
  box-shadow: 0 0 0 2px rgba(241,180,0,0.46);
}
.time .t { color: rgba(255,255,255,0.85); font-weight: 700; }
.time .sub { color: rgba(255,255,255,0.45); font-size: 12px; margin-top: 2px; }
.vs {
  display: grid;
  grid-template-columns: minmax(150px, 1fr) 54px minmax(150px, 1fr);
  align-items: center;
  gap: 14px;
  min-width: 0;
}
.teamCell { min-width: 0; display: grid; gap: 5px; }
.teamHome { text-align: right; justify-items: end; }
.teamAway { text-align: left; justify-items: start; }
.team {
  max-width: 100%;
  color: rgba(255,255,255,0.88);
  font-weight: 750;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.teamStatus { font-size: 11px; line-height: 1; white-space: nowrap; font-weight: 700; }
.teamStatus-waiting { color: rgba(241,180,0,0.96); }
.teamStatus-done { color: rgba(92,200,255,0.88); }
.mid { display: grid; place-items: center; align-self: center; }
.vsTxt { color: rgba(255,255,255,0.64); font-weight: 900; letter-spacing: 0.06em; }
.round { color: rgba(255,255,255,0.6); text-align: right; font-weight: 700; }

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
