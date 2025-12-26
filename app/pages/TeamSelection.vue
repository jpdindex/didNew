<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from '#imports'

const route = useRoute()

const date = computed(() => String(route.query.date ?? ''))
const matchId = computed(() => String(route.query.matchId ?? ''))
const league = computed(() => String(route.query.league ?? ''))

// 더미(나중에 API로 교체)
const match = computed(() => ({
  date: date.value || '2024-12-15',
  time: '12:00',
  league: league.value || 'LALIGA',
  round: '17R',
  stadium: 'Fubol de Vallecas',
  home: 'Vallecano',
  away: 'Real Madrid',
  scoreHome: 0,
  scoreAway: 0,
  statusKo: '준비중',
}))
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <!-- LEFT PANEL (① 영역) -->
      <aside class="left">
        <div class="date">{{ match.date }}</div>

        <div class="matchBox">
          <div class="teams">
            <div class="team">{{ match.home }}</div>
            <div class="vs">VS</div>
            <div class="team">{{ match.away }}</div>
          </div>

          <div class="score">
            <span>{{ match.scoreHome }}</span>
            <span class="colon">:</span>
            <span>{{ match.scoreAway }}</span>
          </div>

          <div class="status">{{ match.statusKo }}</div>

          <div class="meta">
            <div>{{ match.time }} | {{ match.league }} | {{ match.round }}</div>
            <div>{{ match.stadium }}</div>
          </div>
        </div>

        <div class="kpiList">
          <div class="kpiRow" v-for="k in ['TMP','TAP','CTP','Shoot','ASR','GSR','SSR','BAP']" :key="k">
            <div class="kpiVal">0</div>
            <div class="kpiKey">{{ k }}</div>
            <div class="kpiVal">0</div>
          </div>
        </div>

        <button class="backBtn" @click="navigateTo('/schedule')">← 이전화면으로</button>
      </aside>

      <!-- MAIN -->
      <main class="main">
        <div class="header">Player List</div>

        <div class="grid">
          <section class="formation">
            <div class="selectBar">
              <div class="selectFake">포메이션을 선택하세요</div>
              <div class="caret">▼</div>
            </div>

            <div class="pitch">
              <div class="shirt">👕</div>
              <div class="dots">
                <span v-for="i in 7" :key="i" class="dot" />
              </div>
              <div class="count">0 / 16</div>
            </div>

            <div class="mini">
              <div class="miniTitle">진영선택</div>
              <div class="miniBox"></div>
            </div>
          </section>

          <section class="players">
            <div class="tabs">
              <div class="tab on">Position</div>
              <div class="tab">Number</div>
              <div class="tab">Name</div>
            </div>

            <div class="table">
              <div v-for="i in 40" :key="i" class="cell">
                <div class="num" v-if="i <= 30">{{ i }}</div>
              </div>
            </div>

            <div class="legend">
              <span class="pill">GK</span>
              <span class="pill">FW</span>
              <span class="pill">MF</span>
              <span class="pill">DF</span>
            </div>
          </section>

          <section class="time">
            <div class="timeTitle">기록/수정 시작 시간</div>
            <div class="timeBox">
              <div class="hint">선택하신 경기 입력 시간이 설정 시간 이후부터 시작됩니다</div>
              <div class="clock">00 : 00</div>
            </div>
          </section>

          <section class="start">
            <div class="startHint">
              아래의 버튼을 터치하시면<br />
              <b>경기데이터 입력이 시작됩니다.</b>
            </div>
            <button class="startBtn" disabled>전반전 시작</button>
            <div class="small">matchId: {{ matchId }}</div>
          </section>
        </div>
      </main>
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
  width: min(1260px, 96vw);
  height: min(660px, 86vh);
  border-radius: 10px;
  background: rgba(10, 14, 22, 0.78);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
  display: grid;
  grid-template-columns: 280px 1fr;
  overflow: hidden;
}

.left {
  border-right: 1px solid rgba(255,255,255,0.06);
  padding: 14px;
  display: grid;
  grid-template-rows: 32px auto auto 1fr;
  gap: 10px;
}

.date { color: rgba(255,255,255,0.85); font-weight: 900; font-size: 18px; }

.matchBox {
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.03);
  border-radius: 6px;
  padding: 12px;
}
.teams { display: grid; grid-template-columns: 1fr 44px 1fr; gap: 6px; align-items: center; }
.team { color: rgba(255,255,255,0.85); font-weight: 800; }
.vs { color: rgba(255,255,255,0.55); text-align: center; font-weight: 900; }
.score { margin-top: 10px; color: rgba(255,255,255,0.9); font-weight: 900; font-size: 26px; text-align: center; }
.colon { margin: 0 8px; color: rgba(255,255,255,0.55); }
.status { margin-top: 6px; color: rgba(241,180,0,0.95); text-align: center; font-weight: 800; }
.meta { margin-top: 10px; color: rgba(255,255,255,0.45); font-size: 12px; line-height: 1.35; text-align: center; }

.kpiList {
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.02);
  border-radius: 6px;
  overflow: hidden;
}
.kpiRow {
  display: grid;
  grid-template-columns: 52px 1fr 52px;
  border-top: 1px solid rgba(255,255,255,0.06);
  height: 34px;
  align-items: center;
}
.kpiRow:first-child { border-top: none; }
.kpiVal { color: rgba(255,255,255,0.7); text-align: center; font-weight: 800; }
.kpiKey { color: rgba(255,255,255,0.55); text-align: center; font-weight: 800; font-size: 12px; letter-spacing: 0.06em; }

.backBtn {
  align-self: end;
  height: 36px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.02);
  color: rgba(255,255,255,0.75);
  cursor: pointer;
}

.main { padding: 14px; display: grid; grid-template-rows: 40px 1fr; gap: 10px; }
.header { display: grid; place-items: center; color: rgba(255,255,255,0.85); font-weight: 900; font-size: 20px; }

.grid {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  grid-template-rows: 1fr 170px;
  gap: 12px;
}

.formation, .players, .time, .start {
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.03);
  border-radius: 6px;
  padding: 12px;
}

.selectBar {
  height: 34px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  display: grid;
  grid-template-columns: 1fr 36px;
  align-items: center;
  padding: 0 10px;
  color: rgba(255,255,255,0.7);
  font-weight: 700;
}
.caret { text-align: center; color: rgba(255,255,255,0.55); }

.pitch {
  margin-top: 12px;
  height: 260px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(0,0,0,0.12);
  position: relative;
  overflow: hidden;
}
.shirt { position: absolute; left: 50%; top: 46%; transform: translate(-50%,-50%); font-size: 32px; opacity: 0.9; }
.dots { position: absolute; left: 50%; bottom: 18px; transform: translateX(-50%); display: flex; gap: 10px; }
.dot { width: 28px; height: 28px; border-radius: 50%; background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.10); }
.count { position: absolute; right: 12px; bottom: 12px; color: rgba(241,180,0,0.9); font-weight: 900; }

.mini { margin-top: 12px; }
.miniTitle { color: rgba(255,255,255,0.7); font-weight: 800; margin-bottom: 8px; }
.miniBox { height: 72px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.08); background: rgba(0,0,0,0.10); }

.tabs { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.tab {
  height: 30px; border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.02);
  color: rgba(255,255,255,0.6);
  display: grid; place-items: center;
  font-weight: 800; font-size: 12px;
}
.tab.on { background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.85); }

.table {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
}
.cell {
  height: 46px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(0,0,0,0.10);
  position: relative;
}
.num {
  position: absolute;
  left: 10px; top: 10px;
  color: rgba(255,255,255,0.75);
  font-weight: 900;
}
.legend { margin-top: 10px; display: flex; justify-content: flex-end; gap: 8px; }
.legend .pill {
  height: 22px; padding: 0 10px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.7);
  font-size: 12px;
  display: inline-flex; align-items: center;
}

.timeTitle { color: rgba(255,255,255,0.8); font-weight: 900; margin-bottom: 10px; }
.timeBox {
  height: 110px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(0,0,0,0.10);
  display: grid;
  align-content: center;
  justify-items: center;
  gap: 10px;
}
.timeBox .hint { color: rgba(255,255,255,0.45); font-size: 12px; }
.clock { color: rgba(255,255,255,0.85); font-weight: 900; font-size: 42px; letter-spacing: 0.06em; }

.startHint { color: rgba(255,255,255,0.75); text-align: center; line-height: 1.5; }
.startHint b { color: rgba(241,180,0,0.95); }
.startBtn {
  margin-top: 12px;
  width: 100%;
  height: 44px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.35);
}
.small { margin-top: 10px; color: rgba(255,255,255,0.35); font-size: 12px; text-align: center; }
</style>
