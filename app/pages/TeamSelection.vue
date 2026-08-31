<script setup lang="ts">
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
const players = computed(() => (selectedTeam.value === 'home' ? homePlayers : awayPlayers))
const kpis = ['TAP','DAP','DTP','Shoot','ASR','GSR','SSR','BAP']

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

// 한 사람이 한 팀씩 입력하므로, 어느 팀 라인업을 입력할지 먼저 선택
const selectedTeam = ref<'home' | 'away'>('home')

const formationKey = ref<string>('')
const menuOpen = ref(false)
const activeSlot = ref<string | null>(null)
const assigned = reactive<Record<string, number>>({}) // slotId -> players 배열 index
const side = ref<'left' | 'right' | null>(null)
const inputMode = ref<'분석' | '실시간'>('분석') // 디폴트 분석(정지 가능), 클릭하면 실시간(정지 불가)으로 전환

const outfieldSlots = computed(() => (formationKey.value ? formations[formationKey.value].slots : []))

function pickFormation(key: string) {
  formationKey.value = key
  menuOpen.value = false
  // 포메이션 바꾸면 배치 초기화
  Object.keys(assigned).forEach(k => delete assigned[k])
  activeSlot.value = 'o0'
}

// 테스트용: 포메이션/진영/전체 슬롯을 랜덤으로 한 번에 채움
function fillTestData() {
  const keys = Object.keys(formations)
  const key = keys[Math.floor(Math.random() * keys.length)]
  formationKey.value = key
  menuOpen.value = false
  Object.keys(assigned).forEach(k => delete assigned[k])

  const pool = Array.from({ length: players.value.length }, (_, i) => i)
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }

  const outfieldIds = outfieldSlots.value.map((_, i) => `o${i}`)
  const allIds = [...outfieldIds, 'gk', ...benchIds]
  allIds.forEach((id, i) => { assigned[id] = pool[i] })

  activeSlot.value = null
  side.value = Math.random() < 0.5 ? 'left' : 'right'
}

function pickTeam(team: 'home' | 'away') {
  if (selectedTeam.value === team) return
  selectedTeam.value = team
  // 팀이 바뀌면 선수 명단 자체가 달라지므로 기존 배치는 초기화
  Object.keys(assigned).forEach(k => delete assigned[k])
  activeSlot.value = formationKey.value ? 'o0' : null
}

function clickSlot(id: string) {
  if (id !== 'gk' && !formationKey.value) return
  activeSlot.value = id
}

const usedPlayerIndexes = computed(() => new Set(Object.values(assigned)))

function advanceAfter(slotId: string) {
  if (slotId.startsWith('o')) {
    const n = outfieldSlots.value.length
    const next = Array.from({ length: n }, (_, i) => `o${i}`).find(id => assigned[id] === undefined)
    activeSlot.value = next ?? null // 필드 10명 다 차면 GK로 자동 이동 안 함
  } else if (slotId.startsWith('b')) {
    const next = benchIds.find(id => assigned[id] === undefined)
    activeSlot.value = next ?? null
  } else {
    activeSlot.value = null
  }
}

function assignToSlot(slotId: string, index: number) {
  if (usedPlayerIndexes.value.has(index)) return
  assigned[slotId] = index
  advanceAfter(slotId)
}

function pickPlayer(index: number) {
  if (!activeSlot.value) return
  assignToSlot(activeSlot.value, index)
}

// ---- 드래그 앤 드롭으로 선수 배정 ----
const dragIndex = ref<number | null>(null)
function onDragStart(index: number) {
  dragIndex.value = index
}
function onDrop(slotId: string) {
  if (dragIndex.value === null) return
  if (slotId !== 'gk' && !slotId.startsWith('b') && !formationKey.value) return
  assignToSlot(slotId, dragIndex.value)
  dragIndex.value = null
}

const filledCount = computed(() => Object.keys(assigned).length)
const totalSlots = computed(() => outfieldSlots.value.length + 1 + BENCH_COUNT)
// 후보까지 전부(18/18) 채워야 전반전 시작 가능
const canStart = computed(() => !!formationKey.value && !!side.value && filledCount.value >= totalSlots.value)

function startFirstHalf() {
  if (!canStart.value) return
  if (!confirm('전반전을 시작하시겠습니까?\n시작 후에는 라인업을 수정할 수 없습니다.')) return
  navigateTo({
    path: '/DidInput',
    query: {
      matchId: matchId.value,
      home: match.value.home,
      away: match.value.away,
      side: side.value,
      mode: inputMode.value,
      team: selectedTeam.value,
    },
  })
}

// ---- 기록/수정 시작 시간 ----
const startMin = ref(0)
const startSec = ref(0)
const editingTime = ref(false)
function clamp(n: number, min: number, max: number) {
  return Math.min(max, Math.max(min, n))
}
function stepMin(delta: number) {
  startMin.value = clamp(startMin.value + delta, 0, 130)
}
function stepSec(delta: number) {
  startSec.value = clamp(startSec.value + delta, 0, 59)
}
function openTimeEdit() {
  editingTime.value = true
}
function commitTimeEdit() {
  startMin.value = clamp(Math.round(startMin.value) || 0, 0, 130)
  startSec.value = clamp(Math.round(startSec.value) || 0, 0, 59)
  editingTime.value = false
}
const timeLabel = computed(() => `${String(startMin.value).padStart(2, '0')} : ${String(startSec.value).padStart(2, '0')}`)
function playerAt(id: string) {
  const idx = assigned[id]
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
          <button class="club" :class="{ active: selectedTeam === 'home' }" @click="pickTeam('home')"><div class="crest homeCrest">V</div><span>{{ match.home }}</span></button>
          <span class="versus">VS</span>
          <button class="club" :class="{ active: selectedTeam === 'away' }" @click="pickTeam('away')"><div class="crest awayCrest">RM</div><span>{{ match.away }}</span></button>
        </div>
        <div class="score">0 : 0</div><div class="status">준비중</div>
        <div class="matchMeta">{{ match.time }} | {{ match.league }} | {{ match.round }}</div><div class="stadium">{{ match.stadium }}</div>
        <div class="kpis"><div v-for="key in kpis" :key="key" class="kpiRow"><span>0</span><b>{{ key }}</b><span>0</span></div></div>
        <button class="testBtn" @click="fillTestData">TEST</button>
        <button class="backBtn" @click="navigateTo('/schedule')">◀ 이전화면으로</button>
      </aside>
      <main class="content"><h1>Player List</h1>
        <div class="workspace">
        <div class="topRow">
          <section class="formationPanel">
            <div class="selectBar" :class="{ open: menuOpen }" @click="menuOpen = !menuOpen">
              <span>{{ formationKey ? `${formations[formationKey].label} 포메이션` : '포메이션을 선택하세요' }}</span><span>⌄</span>
              <div v-if="menuOpen" class="menu" @click.stop>
                <div v-for="(f, key) in formations" :key="key" class="menuItem" @click="pickFormation(key)">{{ f.label }}</div>
              </div>
            </div>
            <div class="pitch">
              <div class="halfway"/><div class="centerCircle"/><div class="penaltyBox"/><div class="penaltyArc"/><div class="goalBox"/>
              <template v-if="formationKey">
                <button
                  v-for="(s, i) in outfieldSlots" :key="`o${i}`"
                  class="slot" :class="{ active: activeSlot === `o${i}`, filled: assigned[`o${i}`] !== undefined }"
                  :style="{ left: s.x + '%', top: s.y + '%' }"
                  @click="clickSlot(`o${i}`)"
                  @dragover.prevent
                  @drop="onDrop(`o${i}`)"
                >{{ playerAt(`o${i}`)?.[0] ?? '' }}</button>
                <button
                  class="slot gk" :class="{ active: activeSlot === 'gk', filled: assigned['gk'] !== undefined }"
                  :style="{ left: gkSlot.x + '%', top: gkSlot.y + '%' }"
                  @click="clickSlot('gk')"
                  @dragover.prevent
                  @drop="onDrop('gk')"
                >{{ playerAt('gk')?.[0] ?? 'GK' }}</button>
              </template>
              <div v-else class="shirt"/>
              <b class="count">{{ filledCount }} / {{ formationKey ? totalSlots : 16 }}</b>
            </div>
            <div v-if="formationKey" class="bench">
              <button
                v-for="id in benchIds" :key="id" class="slot benchSlot"
                :class="{ active: activeSlot === id, filled: assigned[id] !== undefined }"
                @click="clickSlot(id)"
                @dragover.prevent
                @drop="onDrop(id)"
              >{{ playerAt(id)?.[0] ?? '' }}</button>
            </div>
          </section>
          <section class="playerPanel">
            <div class="tabs"><b>Position</b><b>Number</b><b class="off">Name</b></div>
            <div class="playerGrid">
              <button
                v-for="(p,i) in players" :key="i" class="player" :class="[p[2]?.toLowerCase(), { used: usedPlayerIndexes.has(i), pickable: !usedPlayerIndexes.has(i) }]"
                :draggable="!usedPlayerIndexes.has(i)"
                @click="pickPlayer(i)"
                @dragstart="onDragStart(i)"
              ><strong>{{ p[0] }}</strong><span>{{ p[1] }}</span></button>
              <div v-for="i in 13" :key="`e${i}`" class="player empty"/>
            </div>
            <div class="legend"><span class="gk">GK</span><span class="fw">FW</span><span class="mf">MF</span><span class="df">DF</span></div>
          </section>
        </div>
        <div class="bottomRow">
          <section class="fieldChoice">
            <h2>진영선택</h2>
            <div class="miniPitch">
              <div class="miniHalf"/><div class="miniCircle"/><div class="miniPenalty left"/><div class="miniPenalty right"/><div class="miniGoal left"/><div class="miniGoal right"/>
              <div class="miniBox leftBox" :class="{ active: side === 'left' }" @click="side = 'left'"/>
              <div class="miniBox rightBox" :class="{ active: side === 'right' }" @click="side = 'right'"/>
            </div>
          </section>
          <section class="timePanel">
            <h2>기록/수정 시작 시간</h2>
            <div class="timeBody">
              <p>설정 시간 이후 입력이 시작됩니다</p>
              <div class="timeControl">
                <div class="arrows"><button @click="stepMin(1)">▲</button><button @click="stepMin(-1)">▼</button></div>
                <div class="timeMid">
                  <template v-if="editingTime">
                    <input class="timeInput" type="number" v-model.number="startMin" min="0" max="130" @keyup.enter="commitTimeEdit" />
                    <span class="colon">:</span>
                    <input class="timeInput" type="number" v-model.number="startSec" min="0" max="59" @keyup.enter="commitTimeEdit" @blur="commitTimeEdit" />
                  </template>
                  <strong v-else @click="openTimeEdit">{{ timeLabel }}</strong>
                </div>
                <div class="arrows"><button @click="stepSec(1)">▲</button><button @click="stepSec(-1)">▼</button></div>
              </div>
            </div>
          </section>
          <section class="startPanel">
            <div class="modeToggle">
              <button class="modeBtn" :class="{ on: inputMode === '분석' }" @click="inputMode = '분석'">분석<small>정지 가능</small></button>
              <button class="modeBtn" :class="{ on: inputMode === '실시간' }" @click="inputMode = '실시간'">실시간<small>정지 불가</small></button>
            </div>
            <p>아래의 버튼을 터치하시면<br><b>경기데이터 입력이 시작됩니다.</b></p>
            <button class="startBtn" :disabled="!canStart" @click="startFirstHalf">전반전 시작</button>
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
.bg{position:absolute;inset:0;background:radial-gradient(1200px 500px at 50% 25%,rgba(255,255,255,.08),transparent 60%),radial-gradient(900px 400px at 20% 70%,rgba(0,217,255,.10),transparent 55%),radial-gradient(900px 400px at 80% 70%,rgba(241,180,0,.08),transparent 55%),linear-gradient(180deg,rgba(0,0,0,.55),rgba(0,0,0,.78))}

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

.formationPanel,.playerPanel,.fieldChoice,.timePanel,.startPanel{min-width:0;min-height:0;padding:12px;border-radius:6px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03)}
.formationPanel{display:grid;grid-template-rows:28px minmax(0,1fr) 46px;gap:8px}
.playerPanel{display:flex;flex-direction:column}
.fieldChoice{display:flex;flex-direction:column}
.timePanel{display:flex;flex-direction:column}
.startPanel{position:relative;display:flex;flex-direction:column;justify-content:center}

.selectBar{position:relative;display:flex;align-items:center;justify-content:space-between;padding:0 10px;background:#f2f2f2;color:#252525;font-size:11px;border-radius:4px;border:1px solid rgba(255,255,255,.15);cursor:pointer;user-select:none}
.selectBar.open{border-radius:4px 4px 0 0}
.menu{position:absolute;left:0;right:0;top:100%;z-index:5;background:#fff;border:1px solid rgba(255,255,255,.15);border-top:none;border-radius:0 0 4px 4px;box-shadow:0 8px 20px rgba(0,0,0,.4)}
.menuItem{padding:8px 10px;color:#252525;font-size:11px;cursor:pointer}
.menuItem:hover{background:#eef6f8}

.pitch{position:relative;overflow:hidden;background:rgba(0,0,0,.18);border-radius:6px;border:1px solid rgba(255,255,255,.1)}
.halfway{position:absolute;left:0;right:0;top:0;height:43%;border-bottom:2px solid rgba(255,255,255,.22)}
.centerCircle{position:absolute;width:104px;height:104px;border:2px solid rgba(255,255,255,.22);border-radius:50%;left:50%;top:-53px;transform:translateX(-50%)}
.penaltyBox{position:absolute;width:205px;height:112px;left:50%;bottom:0;transform:translateX(-50%);border:2px solid rgba(255,255,255,.22)}
.penaltyArc{position:absolute;width:104px;height:60px;left:50%;bottom:80px;transform:translateX(-50%);border:2px solid rgba(255,255,255,.22);border-radius:55px 55px 0 0;border-bottom:0}
.goalBox{position:absolute;width:78px;height:42px;left:50%;bottom:0;transform:translateX(-50%);border:2px solid rgba(255,255,255,.22)}
.shirt{position:absolute;left:50%;bottom:47px;width:46px;height:37px;transform:translateX(-50%);background:#e7ecf5;clip-path:polygon(22% 0,38% 10%,62% 10%,78% 0,100% 25%,82% 42%,75% 34%,75% 100%,25% 100%,25% 34%,18% 42%,0 25%)}
.bench{min-height:0;display:flex;align-items:center;justify-content:center;gap:10px;border-radius:6px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.02)}
.slot.benchSlot{position:static;width:32px;height:32px;transform:none}
.count{position:absolute;right:12px;bottom:8px;color:rgba(241,180,0,.95);font-size:13px;font-weight:800}
.slot{position:absolute;transform:translate(-50%,-50%);width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.25);color:#fff;font-size:11px;font-weight:800;cursor:pointer;display:grid;place-items:center;padding:0}
.slot:hover{background:rgba(255,255,255,.22)}
.slot.filled{background:rgba(0,217,255,.28);border-color:#35d0e0}
.slot.active{outline:2px solid #f0b429;outline-offset:2px}
.slot.gk{background:rgba(255,255,255,.2)}
.slot.gk.filled{background:rgba(180,190,200,.4);border-color:#c9d2db}

.fieldChoice h2,.timePanel h2{margin:0 0 8px;font-size:13px;text-align:center;color:rgba(255,255,255,.8);font-weight:800}
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
.miniBox.active{background:rgba(0,217,255,.28)}
.leftBox{left:0}.rightBox{right:0}

.playerPanel{}
.tabs{height:30px;display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.tabs b{display:grid;place-items:center;background:#f2f2f2;border-radius:4px;border:1px solid rgba(255,255,255,.15);color:#222;font-size:10px;font-weight:800}
.tabs .off{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.1);color:rgba(255,255,255,.5)}
.playerGrid{flex:1;margin-top:8px;display:grid;grid-template-columns:repeat(7,1fr);grid-template-rows:repeat(6,1fr);gap:1px;background:rgba(255,255,255,.06);border-radius:4px;overflow:hidden}
.player{min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;border:1px solid rgba(255,255,255,.04);background:rgba(255,255,255,.03);overflow:hidden;padding:0;cursor:default}
.player.pickable{cursor:grab}
.player.pickable:active{cursor:grabbing}
.player.pickable:hover{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.15)}
.player.used{opacity:.3;cursor:not-allowed}
.player strong{font-size:19px;line-height:20px;color:rgba(255,255,255,.85)}
.player span{max-width:100%;padding:0 2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:rgba(255,255,255,.45);font-size:8px}
.player.gk strong{color:rgba(255,255,255,.55)}
.player.fw strong{color:#35d0e0}
.player.mf strong{color:#ef6f5d}
.player.df strong{color:#a8dc30}
.player.empty{min-height:20px}
.legend{height:19px;display:flex;align-items:end;justify-content:flex-end;gap:7px;font-size:9px}
.legend span{padding:1px 8px;border-radius:3px;color:white;font-weight:700}
.legend .gk{background:rgba(255,255,255,.25)}
.legend .fw{background:#15cbd5}
.legend .mf{background:#ea6559}
.legend .df{background:#8dbc22}

.timeBody{flex:1;min-height:0;min-width:0;display:flex;flex-direction:column;align-items:stretch;justify-content:center;gap:14px}
.timeBody p{margin:0;color:rgba(255,255,255,.45);text-align:center;font-size:12px;line-height:1.4}
.timeControl{width:100%;max-width:100%;flex:1;min-height:0;min-width:0;max-height:140px;overflow:hidden;display:grid;grid-template-columns:40px minmax(0,1fr) 40px;align-items:stretch;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(0,0,0,.2);box-sizing:border-box}
.timeMid{min-width:0;display:flex;align-items:center;justify-content:center;gap:4px}
.timeControl strong{text-align:center;font-family:monospace;font-size:clamp(24px,5vw,44px);color:rgba(255,255,255,.92);white-space:nowrap;cursor:pointer;padding:2px 8px;border-radius:6px}
.timeControl strong:hover{background:rgba(255,255,255,.06)}
.timeInput{width:2.4em;min-width:0;font-family:monospace;font-size:clamp(20px,4.2vw,38px);text-align:center;background:rgba(255,255,255,.06);border:1px solid #f0b429;border-radius:6px;color:#fff;padding:2px 0}
.timeInput::-webkit-inner-spin-button,.timeInput::-webkit-outer-spin-button{opacity:1}
.timeMid .colon{font-family:monospace;font-size:clamp(20px,4vw,36px);color:rgba(255,255,255,.6)}
.arrows{align-self:stretch;display:grid;grid-template-rows:1fr 1fr;gap:2px;padding:4px}
.arrows button{border:1px solid rgba(255,255,255,.12);border-radius:4px;background:rgba(255,255,255,.05);color:rgba(255,255,255,.7);font-size:14px;cursor:pointer}
.arrows button:hover{background:rgba(255,255,255,.12);color:#fff}
.arrows button:active{background:rgba(240,180,41,.25);border-color:#f0b429}

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

</style>
