<script setup lang="ts">
import { ref, computed } from 'vue'

type MatchItem = {
  time: string
  stadium: string
  home: string
  away: string
  round: string
}

const league = ref<'ALL' | 'LALIGA'>('ALL')

const today = new Date()
const viewYear = ref(today.getFullYear())
const viewMonth = ref(today.getMonth()) // 0~11
const selectedDate = ref<Date>(new Date(viewYear.value, viewMonth.value, today.getDate()))

const monthLabel = computed(() => {
  const y = viewYear.value
  const m = String(viewMonth.value + 1).padStart(2, '0')
  return `${y}.${m}`
})

const firstDayOfMonth = computed(() => new Date(viewYear.value, viewMonth.value, 1))
const lastDayOfMonth = computed(() => new Date(viewYear.value, viewMonth.value + 1, 0))

// 0(일)~6(토). 화면은 월~일로 보여줄 거라 변환 필요
const firstWeekdayMonBase = computed(() => {
  const d = firstDayOfMonth.value.getDay() // 0~6
  // 월(0),화(1)...일(6)
  return (d + 6) % 7
})

const daysInMonth = computed(() => lastDayOfMonth.value.getDate())

const calendarCells = computed(() => {
  const blanks = firstWeekdayMonBase.value
  const total = blanks + daysInMonth.value
  const rows = Math.ceil(total / 7)
  const cells: Array<{ day: number | null; date: Date | null }> = []

  for (let i = 0; i < rows * 7; i++) {
    const dayNum = i - blanks + 1
    if (dayNum >= 1 && dayNum <= daysInMonth.value) {
      const dt = new Date(viewYear.value, viewMonth.value, dayNum)
      cells.push({ day: dayNum, date: dt })
    } else {
      cells.push({ day: null, date: null })
    }
  }
  return cells
})

function isSameDay(a: Date, b: Date) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

function pickDate(dt: Date | null) {
  if (!dt) return
  selectedDate.value = dt
}

// ✅ 더미 경기 데이터 (나중에 DB 붙이면 여기만 교체)

const matchList = computed<MatchItem[]>(() => {
  const dt = selectedDate.value
  const key = `${dt.getFullYear()}-${dt.getMonth() + 1}-${dt.getDate()}`
  // 날짜별로 조금 다르게 보이게만
  const base: MatchItem[] = [
    { time: '12:00', stadium: 'Metropolitano', home: 'AT Madrid', away: 'Getafe', round: '17R' },
    { time: '12:00', stadium: 'Estadi Olímpic Lluís', home: 'Barcelona', away: 'Leganés', round: '17R' },
    { time: '12:00', stadium: 'Vallecas', home: 'Rayo', away: 'Real Madrid', round: '17R' },
    { time: '12:00', stadium: 'Mendizorrotza', home: 'Alavés', away: 'Athletic', round: '17R' },
    { time: '12:00', stadium: 'RCDE Stadium', home: 'Espanyol', away: 'Osasuna', round: '17R' },
  ]

  // 예: 홀수 날짜면 3개만, 짝수면 5개
  return (dt.getDate() % 2 === 1) ? base.slice(0, 3) : base
})

function prevMonth() {
  const m = viewMonth.value - 1
  if (m < 0) {
    viewMonth.value = 11
    viewYear.value -= 1
  } else viewMonth.value = m
  // 선택 날짜도 월 첫날로 자연스럽게 이동
  selectedDate.value = new Date(viewYear.value, viewMonth.value, 1)
}

function nextMonth() {
  const m = viewMonth.value + 1
  if (m > 11) {
    viewMonth.value = 0
    viewYear.value += 1
  } else viewMonth.value = m
  selectedDate.value = new Date(viewYear.value, viewMonth.value, 1)
}

function onCancel() {
  // 일단 로그인으로 되돌리기 (원하면 다른 화면으로)
  navigateTo('/login')
}

function onSubmit() {
  // 지금은 “선택 확인”만. (나중에 팀 화면/경기 입력 화면으로)
  // 예: 선택한 날짜+경기ID를 store에 저장하고 이동
  alert(`선택 날짜: ${monthLabel.value} / ${selectedDate.value.getDate()}일\n(다음 단계에서 경기 선택 연결)`)
}
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="wrap">
      <header class="topbar">
        <div class="hint">경기일자와 경기를 선택해주세요</div>
        <div class="ver">didNew &nbsp; v0.1</div>
      </header>

      <section class="panel">
        <!-- LEFT -->
        <div class="left">
          <div class="leftHeader">
            <div class="league">
              <select v-model="league" class="select">
                <option value="ALL">전체</option>
                <option value="LALIGA">LALIGA</option>
              </select>
            </div>

            <div class="monthNav">
              <button class="navBtn" @click="prevMonth" aria-label="Prev month">‹</button>
              <div class="month">{{ monthLabel }}</div>
              <button class="navBtn" @click="nextMonth" aria-label="Next month">›</button>
            </div>
          </div>

          <div class="dow">
            <div v-for="d in ['일','월','화','수','목','금','토']" :key="d" class="dowCell">{{ d }}</div>
          </div>

          <div class="grid">
            <button
              v-for="(c, i) in calendarCells"
              :key="i"
              class="day"
              :class="{
                empty: !c.day,
                selected: c.date && isSameDay(c.date, selectedDate),
              }"
              :disabled="!c.day"
              @click="pickDate(c.date)"
            >
              <span v-if="c.day">{{ c.day }}</span>
            </button>
          </div>
        </div>

        <!-- DIVIDER -->
        <div class="divider" />

        <!-- RIGHT -->
        <div class="right">
          <div class="rightTitle">경기선택</div>

          <div class="matchList">
            <button
              v-for="(m, idx) in matchList"
              :key="idx"
              class="matchRow"
            >
              <div class="mLeft">
                <div class="time">{{ m.time }}</div>
                <div class="stadium">{{ m.stadium }}</div>
              </div>

              <div class="mMid">
                <div class="teams">
                  <span class="team">{{ m.home }}</span>
                  <span class="vs">VS</span>
                  <span class="team">{{ m.away }}</span>
                </div>
                <div class="status">시작전</div>
              </div>

              <div class="mRight">{{ m.round }}</div>
            </button>

            <div v-if="matchList.length === 0" class="emptyBox">
              선택한 날짜에 경기가 없습니다.
            </div>
          </div>
        </div>
      </section>

      <footer class="footer">
        <button class="btn cancel" @click="onCancel">Cancel</button>
        <button class="btn submit" @click="onSubmit">Submit</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
  background: #0b0f17;
}

.bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(1200px 520px at 50% 28%, rgba(255,255,255,0.07), transparent 60%),
    radial-gradient(900px 420px at 20% 80%, rgba(0,217,255,0.10), transparent 55%),
    radial-gradient(900px 420px at 80% 80%, rgba(241,180,0,0.08), transparent 55%),
    linear-gradient(180deg, rgba(0,0,0,0.50), rgba(0,0,0,0.78));
  filter: saturate(1.1);
}

.wrap {
  position: relative;
  width: min(1100px, 96vw);
  color: rgba(255,255,255,0.9);
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(10, 14, 22, 0.55);
  border: 1px solid rgba(255,255,255,0.08);
  backdrop-filter: blur(8px);
}
.hint {
  font-weight: 700;
  letter-spacing: 0.02em;
  color: rgba(255,255,255,0.88);
}
.ver {
  font-size: 12px;
  color: rgba(255,255,255,0.55);
}

.panel {
  display: grid;
  grid-template-columns: 1fr 1px 1.2fr;
  gap: 18px;
  padding: 18px;
  border-radius: 10px;
  background: rgba(10, 14, 22, 0.75);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
  min-height: 520px;
}

.divider {
  background: rgba(255,255,255,0.08);
}

/* LEFT */
.leftHeader {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.select {
  height: 34px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.85);
  outline: none;
}
.monthNav {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.navBtn {
  width: 34px;
  height: 34px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.85);
  cursor: pointer;
}
.navBtn:active { transform: translateY(1px); }
.month {
  min-width: 90px;
  text-align: center;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: rgba(255,255,255,0.9);
}

.dow {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  margin-top: 12px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(255,255,255,0.08);
}
.dowCell {
  text-align: center;
  font-size: 12px;
  color: rgba(255,255,255,0.55);
}

.grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 8px;
}

.day {
  height: 46px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.86);
  cursor: pointer;
}
.day:hover {
  border-color: rgba(0,217,255,0.35);
}
.day.selected {
  border-color: rgba(241,180,0,0.75);
  box-shadow: 0 0 0 3px rgba(241,180,0,0.12);
  background: rgba(241,180,0,0.08);
}
.day.empty {
  opacity: 0;
  cursor: default;
  border: none;
  background: transparent;
}

/* RIGHT */
.rightTitle {
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 0.02em;
  margin-bottom: 12px;
}

.matchList {
  display: grid;
  gap: 10px;
}

.matchRow {
  width: 100%;
  display: grid;
  grid-template-columns: 170px 1fr 64px;
  gap: 14px;
  padding: 12px 12px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.9);
  cursor: pointer;
  text-align: left;
}
.matchRow:hover {
  border-color: rgba(241,180,0,0.35);
  background: rgba(241,180,0,0.06);
}

.mLeft .time {
  font-weight: 800;
  letter-spacing: 0.02em;
}
.mLeft .stadium {
  margin-top: 4px;
  font-size: 12px;
  color: rgba(255,255,255,0.55);
}

.teams {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
}
.vs {
  color: rgba(255,255,255,0.5);
  font-weight: 800;
}
.status {
  margin-top: 6px;
  font-size: 12px;
  color: rgba(241,180,0,0.85);
  font-weight: 700;
}
.mRight {
  display: grid;
  place-items: center;
  font-weight: 800;
  color: rgba(255,255,255,0.65);
}

.emptyBox {
  padding: 18px;
  border-radius: 8px;
  border: 1px dashed rgba(255,255,255,0.18);
  color: rgba(255,255,255,0.55);
}

/* FOOTER */
.footer {
  display: flex;
  justify-content: center;
  gap: 14px;
  margin-top: 14px;
}

.btn {
  min-width: 160px;
  height: 40px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.78);
  cursor: pointer;
}
.btn.cancel {
  border-color: rgba(241,180,0,0.35);
}
.btn.submit {
  background: rgba(241,180,0,0.22);
  border-color: rgba(241,180,0,0.55);
  color: rgba(255,255,255,0.9);
}
.btn:active { transform: translateY(1px); }

@media (max-width: 980px) {
  .panel {
    grid-template-columns: 1fr;
  }
  .divider { display: none; }
}
</style>
