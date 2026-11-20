<script setup lang="ts">
import { FORMATIONS, GK_SLOT, orderedOutfieldSlotIds } from '~/utils/formationLayout'

type Half = 'H1' | 'H2' | 'H3' | 'H4'
type EventTime = { half: Half; clock: string } | null
type Team = { id: string; name: string; nameEn?: string; short: string; crestUrl: string | null }
type PlayerRef = { name: string; no: string; pos?: string }
type Report = {
  gmId: string; side: 'H' | 'A'
  league: { id: string; name: string }; season: { id: string; name: string }; round: string
  date: string; kickoffTime: string | null; stadium: string | null; score: { home: number; away: number }
  home: Team; away: Team; team: Team
  recorders: { name: string; rank: 'main' | 'sub' | 'manager' }[]
  completedAt: string | null
  formationKey: string
  cards: (PlayerRef & { card: 'Y' | 'R'; time: EventTime })[]
  substitutions: { time: EventTime; out: PlayerRef | null; in: PlayerRef | null }[]
  startingEleven: (PlayerRef & { slot: string; out: string | null })[] | null
  bench: PlayerRef[] | null
  halves: Half[]
  kpi: Record<Half | 'total', Record<string, number>>
}

// ALL = TAP + X + B (standalone X/B records without an act).
const kpiFields = ['ALL', 'TAP', 'X', 'B', 'DAP', 'DTP', 'BAP', 'SHOT', 'GOAL', 'ASR', 'SSR']
const ratioFields = new Set(['ASR', 'SSR'])
const rankLabels = { main: 'Main', sub: 'Sub', manager: 'Manager' }
const roundLabels: Record<string, string> = { R32: 'Round of 32', R16: 'Round of 16', QF: 'Quarter-final', SF: 'Semi-final', F: 'Final', unassigned: 'Unassigned' }

const route = useRoute()
const router = useRouter()
const { request } = useBackendApi()
const report = ref<Report | null>(null)
const loading = ref(true)
const error = ref('')
// Crest URLs that failed to load fall back to the empty circle.
const brokenCrests = reactive(new Set<string>())

const columns = computed(() => [
  ...(report.value?.halves ?? []).map(half => ({ key: half, label: half })),
  { key: 'total' as const, label: 'Total' },
])
const recorderGroups = computed(() => (['main', 'sub', 'manager'] as const)
  .map(rank => ({ rank, label: rankLabels[rank], names: (report.value?.recorders ?? []).filter(item => item.rank === rank).map(item => analystId(item.name)) }))
  .filter(group => group.names.length))

// Analyst names are often login emails; the report shows only the ID before '@'.
function analystId(name: string) { return name.split('@')[0] || name }
function roundLabel(id: string) { return roundLabels[id] ?? `Round ${id}` }
function dateLabel(date: string) {
  const match = /^(\d{4})(\d{2})(\d{2})$/.exec(date)
  if (!match) return date || '-'
  const weekday = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])).getDay()]
  return `${match[1]}.${match[2]}.${match[3]} (${weekday})`
}
function dateTimeLabel(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
function timeLabel(time: EventTime) { return time ? `${time.half} ${time.clock}` : '-' }
function playerLabel(player: PlayerRef | null) { return player ? (player.no ? `${player.no}. ${player.name}` : player.name) : '-' }
/**
 * Starting XI on a vertical pitch, attacking upward — the same orientation as the
 * stored formation slots (y=94 is the goalkeeper).
 */
const pitchPlayers = computed(() => {
  const data = report.value
  const slots = data ? FORMATIONS[data.formationKey]?.slots : undefined
  if (!data?.startingEleven?.length || !slots) return []
  const readingOrder = orderedOutfieldSlotIds(data.formationKey)
  return data.startingEleven.flatMap(player => {
    // Legacy rows arrive as "order:N" (formation reading order) instead of an "oN" slot.
    const slotId = player.slot.startsWith('order:') ? readingOrder[Number(player.slot.slice(6))] ?? '' : player.slot
    const point = slotId === 'gk' ? GK_SLOT : slotId.startsWith('o') ? slots[Number(slotId.slice(1))] : undefined
    // Squeeze the depth a little so the keeper's name stays inside the pitch.
    return point ? [{ ...player, left: point.x, top: 4 + point.y * 0.9 }] : []
  })
})

// Prefer breaking right after an initial ("J.브랜트와이트" → "J." / "브랜트와이트").
function pitchName(name: string) { return name.replace(/\.(?=\S)/, '.\u200B') }

const teamLabel = computed(() => report.value?.team.nameEn || report.value?.team.name || '')

function formationLabel(key: string) { return FORMATIONS[key]?.label ?? (key || '-') }
function kpiValue(field: string, value: number | undefined) {
  if (value == null) return '-'
  if (ratioFields.has(field)) return `${(value <= 1 ? value * 100 : value).toFixed(1)}%`
  return value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })
}

function goBack() {
  if (window.history.state?.back) router.back()
  else void router.push('/manage/match-reports')
}

function printReport() { window.print() }

// Opened from the list's print button: print once the sheet and crests are ready, then go back.
const autoPrint = route.query.print === '1'

function waitForImages() {
  const pending = [...document.querySelectorAll<HTMLImageElement>('.sheet img')]
    .filter(image => !image.complete)
    .map(image => new Promise(resolve => {
      image.addEventListener('load', resolve, { once: true })
      image.addEventListener('error', resolve, { once: true })
    }))
  return Promise.race([Promise.all(pending), new Promise(resolve => setTimeout(resolve, 2000))])
}

async function printAndReturn() {
  await nextTick()
  await waitForImages()
  window.addEventListener('afterprint', goBack, { once: true })
  window.print()
}

onMounted(async () => {
  document.documentElement.classList.add('matchReportPrint')
  try {
    const gmId = encodeURIComponent(String(route.params.gmId))
    const side = encodeURIComponent(String(route.params.side))
    report.value = await request<Report>(`/api/v1/match-reports/${gmId}/${side}`)
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '리포트를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
  if (autoPrint && report.value) void printAndReturn()
})
onBeforeUnmount(() => {
  document.documentElement.classList.remove('matchReportPrint')
  window.removeEventListener('afterprint', goBack)
})
</script>

<template>
  <div class="page" role="region" aria-labelledby="report-title" tabindex="0">
    <header class="toolbar">
      <button class="backBtn" type="button" @click="goBack">← 경기 리포트</button>
      <button class="printBtn" type="button" :disabled="!report" @click="printReport">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z" /></svg>
        인쇄
      </button>
    </header>

    <div v-if="loading" class="state" role="status">리포트를 불러오는 중…</div>
    <div v-else-if="error" class="state error" role="alert">{{ error }}</div>

    <article v-else-if="report" class="sheet">
      <header class="sheetHead">
        <p class="eyebrow">{{ report.league.name }} · {{ report.season.name }} · {{ roundLabel(report.round) }}</p>
        <h1 id="report-title">{{ report.team.nameEn || report.team.name }} Match Report</h1>
      </header>

      <section class="scoreboard" aria-label="Match result">
        <div class="club home" :class="{ mine: report.side === 'H' }">
          <span class="clubText"><b>{{ report.home.nameEn || report.home.name }}</b><small>HOME</small></span>
          <img v-if="report.home.crestUrl && !brokenCrests.has(report.home.crestUrl)" :src="report.home.crestUrl" alt="" @error="brokenCrests.add(report.home.crestUrl)">
          <span v-else class="crestEmpty" aria-hidden="true" />
        </div>
        <div class="result">
          <strong>{{ report.score.home }}<i>:</i>{{ report.score.away }}</strong>
          <span>{{ dateLabel(report.date) }}<template v-if="report.kickoffTime">&nbsp;&nbsp;{{ report.kickoffTime }}</template></span>
          <span v-if="report.stadium">{{ report.stadium }}</span>
        </div>
        <div class="club away" :class="{ mine: report.side === 'A' }">
          <img v-if="report.away.crestUrl && !brokenCrests.has(report.away.crestUrl)" :src="report.away.crestUrl" alt="" @error="brokenCrests.add(report.away.crestUrl)">
          <span v-else class="crestEmpty" aria-hidden="true" />
          <span class="clubText"><b>{{ report.away.nameEn || report.away.name }}</b><small>AWAY</small></span>
        </div>
      </section>

      <div class="facts" :class="{ withPitch: pitchPlayers.length }">
        <div class="factColumn">
          <dl class="factList">
            <div><dt>Analyst</dt>
              <dd>
                <span v-for="group in recorderGroups" :key="group.rank" class="recorderGroup"><em>{{ group.label }}</em>{{ group.names.join(', ') }}</span>
                <template v-if="!recorderGroups.length">-</template>
              </dd>
            </div>
            <div v-if="report.completedAt" class="completedRow"><dt>Completed</dt><dd>{{ dateTimeLabel(report.completedAt) }}</dd></div>
            <div v-if="!pitchPlayers.length"><dt>Formation</dt><dd>{{ formationLabel(report.formationKey) }}</dd></div>
          </dl>
          <section class="block kpiBlock">
            <h2>{{ teamLabel }} Team KPI</h2>
            <table class="kpiTable">
              <thead><tr><th scope="col">Metric</th><th v-for="column in columns" :key="column.key" scope="col">{{ column.label }}</th></tr></thead>
              <tbody>
                <tr v-for="field in kpiFields" :key="field">
                  <th scope="row">{{ field }}</th>
                  <td v-for="column in columns" :key="column.key" :class="{ total: column.key === 'total' }">{{ kpiValue(field, report.kpi[column.key]?.[field]) }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
        <section v-if="pitchPlayers.length" class="formation">
          <h2>{{ teamLabel }} Formation <span>{{ formationLabel(report.formationKey) }}</span></h2>
          <div class="pitch" role="img" :aria-label="`${report.team.nameEn || report.team.name} starting XI ${formationLabel(report.formationKey)}`">
            <i class="line half" /><i class="line circle" /><i class="line box top" /><i class="line box bottom" />
            <div v-for="player in pitchPlayers" :key="player.slot" class="pitchPlayer" :style="{ left: `${player.left}%`, top: `${player.top}%` }">
              <b>{{ player.no || '-' }}</b>
              <span>{{ pitchName(player.name) }}</span>
            </div>
          </div>
          <div v-if="report.bench?.length" class="bench">
            <h3>Bench</h3>
            <ul>
              <li v-for="(player, index) in report.bench" :key="index"><b>{{ player.no || '-' }}</b>{{ player.name }}</li>
            </ul>
          </div>
        </section>
      </div>

      <div class="eventGrid">
        <section class="block">
          <h2>Cards <span>{{ report.cards.length }}</span></h2>
          <table v-if="report.cards.length" class="eventTable">
            <thead><tr><th scope="col">Time</th><th scope="col">Player</th><th scope="col">Card</th></tr></thead>
            <tbody>
              <tr v-for="(card, index) in report.cards" :key="index">
                <td>{{ timeLabel(card.time) }}</td>
                <td>{{ playerLabel(card) }}</td>
                <td><span class="cardChip" :class="card.card"><i aria-hidden="true" />{{ card.card === 'R' ? 'Red' : 'Yellow' }}</span></td>
              </tr>
            </tbody>
          </table>
          <p v-else class="none">No cards</p>
        </section>

        <section class="block">
          <h2>Substitutions <span>{{ report.substitutions.length }}</span></h2>
          <table v-if="report.substitutions.length" class="eventTable">
            <thead><tr><th scope="col">Time</th><th scope="col">OUT</th><th scope="col">IN</th></tr></thead>
            <tbody>
              <tr v-for="(sub, index) in report.substitutions" :key="index">
                <td>{{ timeLabel(sub.time) }}</td>
                <td class="out"><i aria-hidden="true">▼</i>{{ playerLabel(sub.out) }}</td>
                <td class="in"><i aria-hidden="true">▲</i>{{ playerLabel(sub.in) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="none">No substitutions</p>
        </section>
      </div>
    </article>
  </div>
</template>

<style scoped>
.page { width: 1280px; height: 800px; box-sizing: border-box; overflow-y: auto; padding: 16px 28px 40px; background: #0b0f17; color: #f0f4f8; }
.toolbar { display: flex; justify-content: space-between; align-items: center; width: 794px; margin: 0 auto 14px; }
.backBtn, .printBtn { display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 16px; border-radius: 6px; font: inherit; font-size: 14px; font-weight: 750; cursor: pointer; }
.backBtn { padding: 0; border: 0; background: transparent; color: #9da7b3; }
.printBtn { border: 1px solid rgba(99,207,226,.65); background: rgba(99,207,226,.08); color: #8ce6f5; }
.printBtn:active { background: rgba(99,207,226,.24); }
.printBtn:disabled { opacity: .5; cursor: default; }
.printBtn svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linejoin: round; }
.state { min-height: 400px; display: grid; place-items: center; color: rgba(255,255,255,.55); font-size: 14px; }
.state.error { color: #ff9d91; }

/* The report itself is a white A4 sheet so screen and print look the same. */
.sheet { box-sizing: border-box; width: 794px; margin: 0 auto; padding: 36px 40px 40px; background: #fff; color: #1b2430; border-radius: 4px; font-size: 13px; }
.sheetHead { padding-bottom: 12px; border-bottom: 2px solid #1b2430; }
.eyebrow { margin: 0 0 6px; color: #5b6878; font-size: 12px; font-weight: 700; }
.sheetHead h1 { margin: 0; font-size: 22px; }
.scoreboard { display: grid; grid-template-columns: minmax(0, 1fr) 210px minmax(0, 1fr); align-items: center; gap: 16px; padding: 18px 0; border-bottom: 1px solid #d7dde4; }
.club { display: flex; align-items: center; gap: 12px; min-width: 0; color: #5b6878; }
.club.home { justify-content: flex-end; text-align: right; }
.club img { width: 48px; height: 48px; flex: none; object-fit: contain; filter: grayscale(1); }
.crestEmpty { width: 44px; height: 44px; flex: none; margin: 2px; box-sizing: border-box; border: 2px solid #c9d1d9; border-radius: 50%; background: #f3f6f8; }
.clubText { display: grid; gap: 3px; min-width: 0; }
.clubText b { font-size: 17px; line-height: 1.2; overflow-wrap: break-word; }
.clubText small { color: #8a95a2; font-size: 10px; font-weight: 800; letter-spacing: .08em; }
.club b { color: #6b7684; font-weight: 650; }
.club.mine b { color: #111; font-weight: 850; }
.result { display: grid; justify-items: center; gap: 3px; text-align: center; }
.result strong { display: flex; align-items: center; gap: 12px; font-size: 34px; line-height: 1.1; font-variant-numeric: tabular-nums; }
.result i { color: #9aa5b1; font-size: 26px; font-style: normal; }
.result span { color: #5b6878; font-size: 12px; font-weight: 650; }
.facts { display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; align-items: start; padding: 10px 0 16px; border-bottom: 1px solid #d7dde4; }
.facts.withPitch { grid-template-columns: minmax(0, 1fr) 340px; }
.factColumn { min-width: 0; }
.factList { margin: 0; }
.kpiBlock { margin-top: 10px; }
.factList div { display: grid; grid-template-columns: 76px 1fr; align-items: baseline; padding: 8px 0; }
.factList dt { color: #5b6878; font-size: 12px; font-weight: 700; }
.factList dd { margin: 0; font-weight: 650; }
.factList .completedRow { padding-top: 0; }
.formation h2 { display: flex; align-items: baseline; gap: 6px; margin: 8px 0; color: #5b6878; font-size: 12px; }
.formation h2 span { color: #1b2430; font-size: 14px; }
.pitch { position: relative; height: 380px; border: 1.5px solid #8a939d; border-radius: 4px; background: #fff; overflow: hidden; }
.pitch .line { position: absolute; border: 1.5px solid #c8cdd2; }
.pitch .half { left: 0; right: 0; top: 50%; height: 0; border-width: 1.5px 0 0; }
.pitch .circle { top: 50%; left: 50%; width: 64px; height: 64px; border-radius: 50%; transform: translate(-50%, -50%); }
.pitch .box { left: 22%; right: 22%; height: 13%; }
.pitch .box.top { top: -1.5px; }
.pitch .box.bottom { bottom: -1.5px; }
.pitchPlayer { position: absolute; display: grid; justify-items: center; gap: 1px; width: 66px; transform: translate(-50%, -50%); text-align: center; }
.pitchPlayer b { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: #1b2430; color: #fff; font-size: 11px; font-variant-numeric: tabular-nums; }
.bench { margin-top: 8px; }
.bench h3 { margin: 0 0 4px; color: #5b6878; font-size: 12px; }
.bench ul { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 3px 10px; margin: 0; padding: 0; list-style: none; }
.bench li { display: flex; align-items: baseline; gap: 5px; min-width: 0; overflow: hidden; font-size: 11px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.bench li b { flex: none; min-width: 16px; color: #1b2430; font-size: 10.5px; text-align: right; font-variant-numeric: tabular-nums; }
.pitchPlayer span { display: -webkit-box; max-width: 100%; overflow: hidden; color: #1b2430; font-size: 10px; font-weight: 750; line-height: 1.2; overflow-wrap: anywhere; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; }
.recorderGroup { display: inline-block; margin: 0 16px 4px 0; }
.recorderGroup em { margin-right: 6px; padding: 1px 6px; border-radius: 3px; background: #e8eef3; color: #3d4a59; font-size: 11px; font-style: normal; font-weight: 800; }
.block { margin-top: 18px; break-inside: avoid; }
.block h2 { display: flex; align-items: baseline; gap: 6px; margin: 0 0 8px; font-size: 14px; }
.block h2 span { color: #8a95a2; font-size: 12px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 5px 8px; border-bottom: 1px solid #e3e8ed; text-align: left; }
thead th { border-bottom: 1.5px solid #1b2430; color: #3d4a59; font-size: 12px; }
.kpiTable th:not(:first-child), .kpiTable td { text-align: right; }
.kpiTable tbody th { font-weight: 750; }
.kpiTable td.total { font-weight: 800; background: #f3f6f8; }
.kpiTable th, .kpiTable td { padding: 5px 6px; }
.eventGrid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 12px; }
.eventTable td:first-child { white-space: nowrap; color: #5b6878; }
.eventTable .out, .eventTable .in { color: #1b2430; }
.eventTable .out i, .eventTable .in i { display: inline-block; width: 14px; font-size: 9px; font-style: normal; }
.eventTable .out i { color: #6b7684; }
.eventTable .in i { color: #111; }
/* Card colour is carried by fill, not hue: yellow = outlined card, red = solid black card. */
.cardChip { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 750; }
.cardChip i { display: inline-block; width: 9px; height: 12px; box-sizing: border-box; border: 1.5px solid #1b2430; border-radius: 1.5px; background: #fff; }
.cardChip.R i { background: #1b2430; }
.none { margin: 0; padding: 10px 0; color: #8a95a2; }

@media print {
  .page { width: auto; height: auto; overflow: visible; padding: 0; background: #fff; }
  .toolbar { display: none; }
  /* The page itself has no margin (see @page), so the 12mm margin lives inside the sheet. */
  .sheet { width: auto; padding: 12mm; border-radius: 0; }
  /* Fit the whole report on one A4 page (KPI table has 12 rows plus cards/substitutions). */
  .sheet { font-size: 13px; }
  .sheetHead { padding-bottom: 10px; }
  .sheetHead h1 { font-size: 24px; }
  .scoreboard { padding: 14px 0; }
  .result strong { font-size: 34px; }
  .facts { gap: 28px; padding: 10px 0 14px; }
  .facts.withPitch { grid-template-columns: minmax(0, 1fr) 340px; }
  .factList div { padding: 5px 0; }
  .kpiBlock { margin-top: 8px; }
  .kpiTable th, .kpiTable td { padding: 6px 6px; }
  .pitch { height: 390px; }
  .block { margin-top: 16px; }
  .block h2 { margin-bottom: 6px; }
  .eventTable th, .eventTable td { padding: 4px 8px; }
}
</style>

<style>
/* Release the fixed 1280×800 tablet canvas only while a report page is open. */
@media print {
  /* Zero page margin leaves the browser no room for its date/URL header and footer. */
  @page { size: A4 portrait; margin: 0; }
  html.matchReportPrint, html.matchReportPrint body, html.matchReportPrint #__nuxt { height: auto; overflow: visible; background: #fff; }
  html.matchReportPrint .canvasHost { display: block; width: auto; height: auto; overflow: visible; }
  html.matchReportPrint .apkCanvas { width: auto; height: auto; margin: 0 !important; transform: none; }
  html.matchReportPrint * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
</style>
