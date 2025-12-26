<script setup lang="ts">
import { computed, ref } from 'vue'

필요한 데이터 코드들 

fm_formation (포메이션 문자열 ex.4-3-3)
gi_part (경기장 진영 선택 L/R)
gi_part_ex (연장 들어갈 경우 진영 재선택)
gp_id (선수 아이디)
gp_tap / gp_ttp / gp_sht / gp_gol / gp_asr / gp_ssr / gp_tmp / gp_ctp (왼쪽 양팀 스탯란에 필요한 리스트들)



type MatchItem = {
  id: string
  time: string
  league: string
  round: string
  stadium: string
  home: { name: string; short?: string }
  away: { name: string; short?: string }
  status: 'READY' | 'LIVE' | 'DONE'
}

const today = new Date()
const viewYear = ref(today.getFullYear())
const viewMonth = ref(today.getMonth()) // 0-11

const selectedDate = ref<string>('') // YYYY-MM-DD
const selectedMatchId = ref<string>('')

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
}

function nextMonth() {
  if (viewMonth.value === 11) {
    viewMonth.value = 0
    viewYear.value += 1
  } else viewMonth.value += 1
  selectedDate.value = ''
  selectedMatchId.value = ''
}

function pickDate(ymd?: string) {
  if (!ymd) return
  selectedDate.value = ymd
  selectedMatchId.value = '' // 날짜 바꾸면 경기 선택 초기화
}

// ---- 더미 경기 데이터 (나중에 API로 교체될 자리) ----
const dummyByDate = ref<Record<string, MatchItem[]>>({
  [toYMD(viewYear.value, viewMonth.value, 14)]: [
    {
      id: 'M20241214-01',
      time: '12:00',
      league: 'LALIGA',
      round: '17R',
      stadium: 'Estadio de Vallecas',
      home: { name: 'Vallecano' },
      away: { name: 'Real Madrid' },
      status: 'READY',
    },
    {
      id: 'M20241214-02',
      time: '15:00',
      league: 'LALIGA',
      round: '17R',
      stadium: 'Camp de Futbol',
      home: { name: 'Alaves' },
      away: { name: 'Athletic' },
      status: 'READY',
    },
  ],
})

const matchList = computed(() => {
  if (!selectedDate.value) return []
  return dummyByDate.value[selectedDate.value] ?? []
})

function selectMatch(id: string) {
  selectedMatchId.value = id
}

const selectedMatch = computed(() => {
  return matchList.value.find(m => m.id === selectedMatchId.value) || null
})

async function goTeamSelection(match?: MatchItem) {
  const m = match ?? selectedMatch.value
  if (!selectedDate.value || !m) return

  await navigateTo({
    path: '/TeamSelection',
    query: {
      date: selectedDate.value,
      matchId: m.id,
      league: m.league,
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
        <div class="meta">did.matchison.com · v0.8.17</div>
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
                hasMatch: c.ymd && (dummyByDate[c.ymd]?.length ?? 0) > 0,
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
            <button
              v-for="m in matchList"
              :key="m.id"
              class="matchRow"
              :class="{ selected: m.id === selectedMatchId }"
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
                <div class="team">{{ m.home.name }}</div>
                <div class="mid">
                  <div class="vsTxt">VS</div>
                  <div class="status">시작전</div>
                </div>
                <div class="team">{{ m.away.name }}</div>
              </div>

              <div class="round">{{ m.round }}</div>
            </button>

            <div v-if="matchList.length === 0" class="hint">
              선택한 날짜에 경기가 없어.
            </div>
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
.page { min-height: 100vh; display: grid; place-items: center; position: relative; overflow: hidden; background: #0b0f17; }
.bg {
  position: absolute; inset: 0;
  background:
    radial-gradient(1200px 500px at 50% 25%, rgba(255,255,255,0.08), transparent 60%),
    radial-gradient(900px 400px at 20% 70%, rgba(0,217,255,0.10), transparent 55%),
    radial-gradient(900px 400px at 80% 70%, rgba(241,180,0,0.08), transparent 55%),
    linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.78));
}

.frame {
  position: relative;
  width: min(1180px, 96vw);
  height: min(560px, 80vh);
  border-radius: 10px;
  background: rgba(10, 14, 22, 0.78);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
  display: grid;
  grid-template-rows: 48px 1fr 60px;
  overflow: hidden;
}

.topBar {
  display: flex; align-items: center; justify-content: center;
  position: relative;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.title { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; }
.meta { position: absolute; right: 16px; color: rgba(255,255,255,0.35); font-size: 12px; }

.body { display: grid; grid-template-columns: 420px 1fr; }

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
  grid-template-columns: 220px 1fr 64px;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.03);
  cursor: pointer;
  text-align: left;
}
.matchRow.selected { border-color: rgba(241,180,0,0.7); background: rgba(241,180,0,0.06); }
.time .t { color: rgba(255,255,255,0.85); font-weight: 700; }
.time .sub { color: rgba(255,255,255,0.45); font-size: 12px; margin-top: 2px; }
.vs { display: grid; grid-template-columns: 1fr 80px 1fr; align-items: center; gap: 8px; }
.team { color: rgba(255,255,255,0.85); font-weight: 700; }
.mid { display: grid; place-items: center; }
.vsTxt { color: rgba(255,255,255,0.7); font-weight: 900; letter-spacing: 0.06em; }
.status { color: rgba(241,180,0,0.9); font-size: 12px; margin-top: 2px; }
.round { color: rgba(255,255,255,0.6); text-align: right; font-weight: 700; }

.bottomBar {
  border-top: 1px solid rgba(255,255,255,0.06);
  display: flex; align-items: center; justify-content: center;
  gap: 14px;
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
