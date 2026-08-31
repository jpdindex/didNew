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

const route = useRoute()
const home = computed(() => String(route.query.home ?? 'Home'))
const away = computed(() => String(route.query.away ?? 'Away'))
const gi_part = computed(() => (route.query.side === 'right' ? 'R' : 'L')) // TeamSelection의 진영선택 값
const inputMode = computed(() => (route.query.mode === '실시간' ? '실시간' : '분석')) // 실시간=정지불가, 분석(디폴트)=정지가능

const homeScore = ref(0)
const awayScore = ref(0)
const half = ref<'전반' | '후반'>('전반')
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
const records = ref<DidRecord[]>([])

// 입력 중인 팀. TeamSelection 에서 team 쿼리로 넘어온다.
const team = computed(() => (route.query.team === 'away' ? 'away' : 'home'))
const squad = computed<SquadPlayer[]>(() => (team.value === 'away' ? AWAY_SQUAD : HOME_SQUAD))

// 실시간 입력 중이므로 아직 안 끝난 루트는 마감하지 않는다.
// 루트가 X/B/슛 결과 또는 4초 규칙으로 끊겨야 DAP 가 확정되고 선수 입력 버튼이 뜬다.
const analysis = computed(() => computeAttackPaths(records.value, { closeTrailing: false }))
const bapCount = computed(() => computeBap(records.value).length)

function fmtTime(sec: number) {
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
}

// 표시용 행. 최신 레코드가 위로 오도록 뒤집는다.
const rows = computed(() => {
  const flags = analysis.value.flags
  return records.value
    .map((r, i) => ({
      id: r.id,
      no: i + 1,
      time: fmtTime(r.seconds),
      act: r.act,
      // 'O'(진행중)는 표에 빈칸으로 보여준다
      result: r.res === 'O' ? '' : r.res,
      area: String(r.area),
      // DAP 로 인정된 레코드에만 선수를 입력한다 (PPT 슬라이드 17·19).
      //
      // 추가로 X(실책)/B(블락)로 끝난 레코드는 제외한다. 운영 기준이며, 레거시에도
      // 결과적으로 같은 그림이 나온다 — 레거시 Select 조건은 `is_tap==1 && act 있음`
      // 뿐이지만(APK DPlayInputFragment:1661), 서버 판정에서 X/B 는 공격루트를
      // 끊지 않아 그런 공격은 대부분 UPP(무효)로 분류되어 is_tap=0 이 된다.
      isDap: (flags.get(r.id)?.isDap ?? false) && r.res !== 'X' && r.res !== 'B',
      playerName: squad.value.find(p => p.no === r.playerId)?.name ?? '',
    }))
    .reverse()
})

const pendingPos = ref<{ x: number; y: number } | null>(null)
const pendingShotId = ref<string | null>(null) // 골 존 결과를 기다리는 슛 레코드
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
const shootActs = [
  { k: 'S', label: 'Shooting' },
  { k: 'H', label: 'Heading' },
  { k: 'R', label: 'Free kick' },
]

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
  const nextCell = cellFromPos({ x, y })
  pendingPos.value = { x, y }
  pendingCell.value = nextCell
  flashCell.value = nextCell
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flashCell.value = null }, 420)
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
function clickAct(actKey: string, isShot: boolean) {
  if (!pendingPos.value) return
  const rec = createActRecord(
    actKey as Exclude<ActCode, ''>,
    seconds.value,
    Number(areaFromPos(pendingPos.value))
  )
  records.value.push(rec)
  pendingPos.value = null
  pendingCell.value = null
  flashCell.value = null
  if (isShot) pendingShotId.value = rec.id
}

// X/B: 새 레코드가 아니라 직전 레코드의 결과로 반영한다.
function clickResult(res: 'X' | 'B') {
  const last = records.value[records.value.length - 1]
  if (!last || last.res !== 'O') return
  applyResult(records.value, last.id, res)
  pendingShotId.value = null
}

// 골대 존: 결과를 기다리던 슛 레코드에 반영한다.
function clickGoalZone(zone: string) {
  if (!pendingShotId.value) return
  applyResult(records.value, pendingShotId.value, zone as Exclude<ResCode, 'O' | ''>)
  if (zone === 'GOAL') homeScore.value++
  pendingShotId.value = null
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

function finishHalf() {
  if (!confirm(`${half.value}을 종료하시겠습니까?`)) return
  if (half.value === '전반') {
    half.value = '후반'
    seconds.value = 0
  } else {
    if (confirm('경기를 종료하시겠습니까?')) {
      navigateTo('/schedule')
    }
  }
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
          <div class="grassIcon">▦</div>
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

        <div class="pitch" @click="clickPitch">
          <div class="stripes" />
          <div class="lineHalf" /><div class="lineCircle" />
          <div class="boxL" /><div class="arcL" /><div class="goalNetL" />
          <div class="boxR" /><div class="arcR" /><div class="goalNetR" />
          <div class="corner cornerTL" /><div class="corner cornerBL" />
          <div class="corner cornerTR" /><div class="corner cornerBR" />
          <div v-if="cellRect" class="zoneHighlight" :style="cellRect" />
          <div v-if="pendingPos" class="marker" :style="{ left: pendingPos.x + '%', top: pendingPos.y + '%' }" />
        </div>

        <div class="table">
          <div class="thead">
            <span>No.</span><span>Time</span><span>Act</span><span>Result</span><span>Area</span><span>Player</span>
          </div>
          <div class="tbody">
            <div v-for="r in rows" :key="r.id" class="trow" :class="{ pending: r.result === '' }">
              <span>{{ r.no }}</span><span>{{ r.time }}</span><span>{{ r.act }}</span><span>{{ r.result }}</span><span>{{ r.area }}</span>
              <span>
                <template v-if="r.playerName">{{ r.playerName }}</template>
                <button v-else-if="r.isDap" class="playerBtn" @click="openPlayerPick(r.id)">Select</button>
              </span>
            </div>
          </div>
        </div>

        <button class="finishBtn" @click="finishHalf">{{ half }} 종료</button>
      </section>

      <section class="right">
        <h1>{{ playerPickFor ? '선수선택' : 'DID-INPUT' }}</h1>

        <template v-if="!playerPickFor">
        <div class="group">
          <div class="groupTitle">Kick</div>
          <div class="kickGrid">
            <button v-for="a in kickActs" :key="a.k" class="actBtn kickPrimary" :class="`kick-${a.k.toLowerCase()}`" @click="clickAct(a.k, false)"><b>{{ a.k }}</b><span>{{ a.label }}</span></button>
            <div class="stacked">
              <button v-for="a in kickActs2" :key="a.k" class="actBtn small" @click="clickAct(a.k, false)"><b>{{ a.k }}</b><span>{{ a.label }}</span></button>
            </div>
            <button v-for="a in kickResultActs" :key="a.k" class="actBtn kickResult" :class="`kick-${a.k.toLowerCase()}`" @click="clickResult(a.k as 'X' | 'B')"><b>{{ a.k }}</b><span class="divider">|</span><span>{{ a.label }}</span></button>
          </div>
        </div>

        <div class="group">
          <div class="groupTitle">Shooting</div>
          <div class="shootGrid">
            <button v-for="a in shootActs" :key="a.k" class="actBtn" @click="clickAct(a.k, true)"><b>{{ a.k }}</b><span>{{ a.label }}</span></button>
          </div>

          <div class="goal" :class="{ active: pendingRecordIdx !== null }">
            <div class="goalZone hx" @click="clickGoalZone('HX')">HX</div>
            <div class="goalZone lx" @click="clickGoalZone('LX')">LX</div>
            <div class="goalZone rx" @click="clickGoalZone('RX')">RX</div>
            <div class="goalFrame">
              <div class="goalZone goalCenter" @click="clickGoalZone('GOAL')">GOAL</div>
            </div>
            <div class="goalBottom">
              <div class="goalZone b" @click="clickGoalZone('B')">B</div>
              <div class="goalZone x" @click="clickGoalZone('X')">X</div>
            </div>
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
.grassIcon,.swapIcon{width:26px;height:26px;display:grid;place-items:center;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.06);color:#ddd;font-size:13px}

.scoreBar{flex:0 0 auto;min-width:0;display:grid;grid-template-columns:minmax(0,1fr) auto auto auto minmax(0,1fr);align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid rgba(255,255,255,.08)}
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

.pitch{position:relative;flex:1;min-height:200px;overflow:hidden;cursor:crosshair;background:#204923}
.stripes{position:absolute;inset:0;background:repeating-linear-gradient(90deg,rgba(255,255,255,.075) 0 10%,rgba(0,0,0,.045) 10% 20%)}
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
@keyframes zoneFlash{0%{opacity:1}55%{opacity:.55}100%{opacity:0}}

.table{flex:0 0 auto;height:180px;overflow-y:auto;border-top:1px solid rgba(255,255,255,.08)}
.thead,.trow{display:grid;grid-template-columns:64px 96px 78px 86px 78px minmax(210px,1fr);gap:4px;padding:4px 10px}
.thead span,.trow span{min-width:0;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.thead span:last-child,.trow span:last-child{text-align:left;padding-left:18px}
.thead{background:#f0b429;color:#1a1a1a;font-weight:800;font-size:11px;position:sticky;top:0}
.trow{color:#ddd;font-size:11px;border-bottom:1px solid rgba(255,255,255,.06)}
.trow.pending{color:#f0b429}
.playerBtn{height:18px;padding:0 8px;border-radius:3px;border:1px dashed #f0b429;background:rgba(240,180,41,.1);color:#f0b429;font-size:10px;font-weight:700;cursor:pointer}
.playerBtn:hover{background:rgba(240,180,41,.25)}

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

.finishBtn{flex:0 0 auto;height:34px;border:none;border-top:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.06);color:#eee;font-weight:700;cursor:pointer}
.finishBtn:hover{background:rgba(255,255,255,.12)}

.right{width:567.3359px;min-width:567.3359px;min-height:0;background:#1e2126;display:flex;flex-direction:column;padding:10px;gap:10px;overflow:hidden}
.right h1{margin:0;text-align:center;color:#fff;font-size:20px}
.group{border:1px solid rgba(255,255,255,.1);padding:7px;background:rgba(255,255,255,.02)}
.group:last-of-type{flex:1;min-height:0;display:flex;flex-direction:column}
.groupTitle{color:#eee;font-weight:800;margin-bottom:8px;font-size:18px}

.kickGrid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));grid-template-rows:82px 54px;gap:5px}
.stacked{grid-column:5/7;grid-row:1;min-width:0;display:grid;grid-template-rows:1fr 1fr;gap:3px}
.actBtn{min-width:0;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;height:64px;border-radius:0;border:1px solid #f0b429;background:rgba(240,180,41,.08);color:#f0b429;cursor:pointer;transition:opacity .15s,background .15s,transform .1s}
.actBtn.small{height:auto;min-height:0;flex-direction:row;justify-content:flex-start;padding:0 14px;gap:12px}
.kickPrimary{height:82px}.kick-c{grid-column:1/3;grid-row:1}.kick-p{grid-column:3/5;grid-row:1}.kickResult{height:54px;flex-direction:row;gap:16px}.kick-x{grid-column:1/4;grid-row:2}.kick-b{grid-column:4/7;grid-row:2}.kickResult .divider{color:rgba(255,255,255,.2);font-size:24px}
.actBtn b{font-size:29px}
.actBtn span{font-size:14px;font-weight:700}
.stacked .actBtn span{font-size:12px}
.kickPrimary,.shootGrid .actBtn{flex-direction:row;gap:12px}
.kickPrimary b,.shootGrid .actBtn b{padding-right:12px;border-right:1px solid rgba(255,255,255,.22)}
.actBtn:disabled{opacity:.35;border-color:rgba(255,255,255,.15);color:rgba(255,255,255,.4);cursor:not-allowed}
.actBtn:not(:disabled):hover{background:rgba(240,180,41,.2)}
.actBtn:not(:disabled):active{transform:scale(.98)}
.kick-p:not(:disabled){background:#f0b429;color:#191919}
.kick-b:not(:disabled){background:#f1f1f1;color:#202020;border-color:#aaa}

.shootGrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:12px}

.goal{position:relative;flex:1;min-height:190px;opacity:.5;pointer-events:none;transition:opacity .2s,transform .2s;transform:scale(.985)}
.goal.active{opacity:1;pointer-events:auto;transform:scale(1)}
.goalFrame{
  position:absolute;left:9%;right:9%;top:48px;bottom:26px;width:auto;height:auto;margin:0;
  border:6px solid #e8e8e8;border-bottom:none;border-radius:2px 2px 0 0;
  background:
    repeating-linear-gradient(115deg, rgba(255,255,255,.14) 0 1px, transparent 1px 11px),
    repeating-linear-gradient(65deg,  rgba(255,255,255,.14) 0 1px, transparent 1px 11px),
    #12321a;
  box-shadow: inset 0 0 30px rgba(0,0,0,.5);
}
.goalZone{cursor:pointer;color:#f0b429;font-weight:800;font-size:11px;display:grid;place-items:center}
.hx{position:absolute;left:0;right:0;top:0;height:42px;text-align:center;color:rgba(255,255,255,.45);font-size:10px;letter-spacing:.08em}
.lx{position:absolute;left:0;top:48px;bottom:26px;width:9%;color:rgba(255,255,255,.55)}
.rx{position:absolute;right:0;top:48px;bottom:26px;width:9%;color:rgba(255,255,255,.55)}
.goalCenter{position:absolute;inset:0;color:#fff;font-size:20px;letter-spacing:.05em;text-shadow:0 2px 6px rgba(0,0,0,.6)}
.goalCenter:hover,.lx:hover,.rx:hover{background:rgba(240,180,41,.22)}
.goalBottom{
  position:absolute;left:9%;right:9%;bottom:0;height:26px;width:auto;margin:0;
  border-left:6px solid #e8e8e8;border-right:6px solid #e8e8e8;
  background:linear-gradient(180deg,#2f7d3c,#1f5c29);
  display:grid;grid-template-columns:1fr 1fr;
}
.goalBottom .goalZone{color:rgba(255,255,255,.85);font-size:11px}
.goalBottom .goalZone:hover{background:rgba(240,180,41,.3)}

</style>
