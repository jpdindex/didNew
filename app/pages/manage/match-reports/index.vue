<script setup lang="ts">
type League = { id: string; name: string }
type Season = { id: string; leagueId: string; name: string; from: string | null; to: string | null }
type Analyst = { id: string; name: string }
type Team = { id: string; name: string; short: string; crestUrl: string | null; final: boolean; analysts: Analyst[] }
type Match = {
  gmId: string; date: string; kickoffTime: string | null; round: string
  score: { home: number | null; away: number | null }; home: Team; away: Team
}

// Reports are offered only for these leagues, in this order.
const REPORT_LEAGUES = ['EPL', 'LALIGA']
const roundLabels: Record<string, string> = { R32: '32강', R16: '16강', QF: '8강', SF: '준결승', F: '결승', unassigned: '라운드 미지정' }

const route = useRoute()
const router = useRouter()
const { request } = useBackendApi()
const leagues = ref<League[]>([])
const seasons = ref<Season[]>([])
const fixtureSeasons = ref<Record<string, string[]>>({})
const league = ref(String(route.query.league ?? ''))
const season = ref(String(route.query.season ?? ''))
const team = ref(String(route.query.team ?? ''))
const round = ref(String(route.query.round ?? ''))
const analyst = ref(String(route.query.analyst ?? ''))
const matches = ref<Match[]>([])
const loading = ref(false)
const error = ref('')
// Crest URLs that failed to load fall back to the empty circle.
const brokenCrests = reactive(new Set<string>())
let matchRequest: AbortController | undefined

function dateKey(value?: string | null) { return (value ?? '').replace(/[^0-9]/g, '') }
function seasonStart(item: Season) {
  const year = /(?:19|20)\d{2}/.exec(item.id)?.[0] ?? /(?:19|20)\d{2}/.exec(item.name)?.[0]
  return dateKey(item.from) || (year ? year + '0101' : '')
}
function roundLabel(id: string) { return roundLabels[id] ?? `${id}라운드` }
function dateLabel(date: string) { return date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1.$2.$3') }
function errorMessage(caught: unknown) { return caught instanceof Error ? caught.message : '요청을 처리하지 못했습니다.' }

const leagueSeasons = computed(() => {
  const registered = seasons.value.filter(item => item.leagueId === league.value)
  const knownIds = new Set(registered.map(item => item.id))
  const fixtureIds = fixtureSeasons.value[league.value]
  return [
    ...registered.filter(item => !fixtureIds || fixtureIds.includes(item.id)),
    ...(fixtureIds ?? []).filter(id => !knownIds.has(id)).map(id => ({
      id, leagueId: league.value, name: id.replace(/^(\d{4})(\d{4})$/, '$1–$2'), from: null, to: null,
    })),
  ].sort((a, b) => seasonStart(b).localeCompare(seasonStart(a)) || b.id.localeCompare(a.id))
})

const teams = computed(() => {
  const byId = new Map<string, string>()
  for (const match of matches.value) for (const side of [match.home, match.away]) byId.set(side.id, side.name)
  return [...byId].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name, 'ko'))
})

// Analyst names are often login emails; the list shows only the ID before '@'.
function analystId(name: string) { return name.split('@')[0] || name }

const analysts = computed(() => {
  const byId = new Map<string, string>()
  for (const match of matches.value) for (const side of [match.home, match.away]) for (const item of side.analysts ?? []) byId.set(item.id, analystId(item.name))
  return [...byId].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name, 'ko'))
})

function isAnalystSide(side: Team) {
  return Boolean(analyst.value) && Boolean(side.analysts?.some(item => item.id === analyst.value))
}
function hasAnalyst(match: Match) {
  return isAnalystSide(match.home) || isAnalystSide(match.away)
}
/** With an analyst selected, only the team that analyst recorded keeps its report buttons. */
function showReport(side: Team) {
  return side.final && (!analyst.value || isAnalystSide(side))
}

/** Rounds newest first, ordered by their latest fixture date. */
const groups = computed(() => {
  const byRound = new Map<string, Match[]>()
  for (const match of matches.value) {
    if (team.value && match.home.id !== team.value && match.away.id !== team.value) continue
    if (analyst.value && !hasAnalyst(match)) continue
    byRound.set(match.round, [...(byRound.get(match.round) ?? []), match])
  }
  return [...byRound]
    .map(([id, rows]) => ({ id, rows, latest: rows.reduce((max, row) => row.date > max ? row.date : max, '') }))
    .sort((a, b) => b.latest.localeCompare(a.latest) || (Number(b.id) || 0) - (Number(a.id) || 0))
})
const roundOptions = computed(() => groups.value.map(group => group.id))
const leagueOptions = computed(() => leagues.value.map(item => ({ value: item.id, label: item.name })))
const seasonOptions = computed(() => leagueSeasons.value.map(item => ({ value: item.id, label: item.name })))
const teamOptions = computed(() => [{ value: '', label: '전체 팀' }, ...teams.value.map(item => ({ value: item.id, label: item.name }))])
const roundSelectOptions = computed(() => [{ value: '', label: '전체 라운드' }, ...roundOptions.value.map(id => ({ value: id, label: roundLabel(id) }))])
const analystOptions = computed(() => [{ value: '', label: '전체 분석관' }, ...analysts.value.map(item => ({ value: item.id, label: item.name }))])
const visibleGroups = computed(() => groups.value.filter(group => !round.value || group.id === round.value))

function syncQuery() {
  const query = Object.fromEntries(Object.entries({ league: league.value, season: season.value, team: team.value, round: round.value, analyst: analyst.value })
    .filter(([, value]) => value))
  void router.replace({ query })
}

async function discoverSeasons() {
  const id = league.value
  if (!id || fixtureSeasons.value[id]) return
  const data = await request<{ ids: string[] }>('/api/v1/goalx/fixture-seasons?' + new URLSearchParams({ league_id: id }))
  fixtureSeasons.value = { ...fixtureSeasons.value, [id]: data.ids }
}

async function loadMatches() {
  matchRequest?.abort()
  syncQuery()
  matches.value = []
  error.value = ''
  if (!league.value || !season.value) return
  const controller = new AbortController()
  matchRequest = controller
  loading.value = true
  try {
    const params = new URLSearchParams({ league_id: league.value, season_id: season.value })
    const data = await request<{ matches: Match[] }>('/api/v1/match-reports/matches?' + params, { signal: controller.signal })
    if (controller.signal.aborted) return
    matches.value = data.matches
    if (team.value && !teams.value.some(item => item.id === team.value)) team.value = ''
    if (analyst.value && !analysts.value.some(item => item.id === analyst.value)) analyst.value = ''
    if (round.value && !roundOptions.value.includes(round.value)) round.value = ''
    syncQuery()
  } catch (caught) {
    if (!controller.signal.aborted) error.value = errorMessage(caught)
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}

async function changeLeague() {
  season.value = ''
  analyst.value = ''
  team.value = ''
  round.value = ''
  matches.value = []
  loading.value = true
  try {
    await discoverSeasons()
    season.value = leagueSeasons.value[0]?.id ?? ''
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
  await loadMatches()
}

function changeSeason() {
  analyst.value = ''
  team.value = ''
  round.value = ''
  void loadMatches()
}

/** `print` opens the report in print mode: it prints once loaded, then returns here. */
function openReport(match: Match, side: 'H' | 'A', print = false) {
  void router.push({ path: `/manage/match-reports/${encodeURIComponent(match.gmId)}/${side}`, query: print ? { print: '1' } : {} })
}

onMounted(async () => {
  loading.value = true
  try {
    const catalog = await request<{ leagues: League[]; seasons: Season[] }>('/api/v1/goalx/catalog')
    leagues.value = REPORT_LEAGUES.flatMap(id => catalog.leagues.filter(item => item.id === id))
    seasons.value = catalog.seasons.filter(item => REPORT_LEAGUES.includes(item.leagueId))
    if (!leagues.value.some(item => item.id === league.value)) league.value = leagues.value[0]?.id ?? ''
    await discoverSeasons()
    if (!leagueSeasons.value.some(item => item.id === season.value)) {
      season.value = leagueSeasons.value[0]?.id ?? ''
      team.value = ''
      round.value = ''
      analyst.value = ''
    }
  } catch (caught) {
    error.value = errorMessage(caught)
    loading.value = false
    return
  }
  await loadMatches()
})
onBeforeUnmount(() => matchRequest?.abort())
</script>

<template>
  <div class="page" role="region" aria-labelledby="reports-title" tabindex="0">
    <header class="topBar">
      <NuxtLink class="backBtn" to="/manage">← 데이터 관리</NuxtLink>
      <h1 id="reports-title">경기 리포트</h1>
      <button class="refreshBtn" type="button" :disabled="loading || !season" @click="loadMatches">새로고침</button>
    </header>

    <section class="filters" aria-label="리포트 필터">
      <div class="field"><span>리그</span>
        <FilterSelect v-model="league" label="리그" :options="leagueOptions" placeholder="리그 선택" @change="changeLeague" />
      </div>
      <div class="field"><span>시즌</span>
        <FilterSelect v-model="season" label="시즌" :options="seasonOptions" placeholder="등록된 시즌 없음" @change="changeSeason" />
      </div>
      <div class="field"><span>팀</span>
        <FilterSelect v-model="team" label="팀" :options="teamOptions" :disabled="!teams.length" @change="syncQuery" />
      </div>
      <div class="field"><span>라운드</span>
        <FilterSelect v-model="round" label="라운드" :options="roundSelectOptions" :disabled="!roundOptions.length" @change="syncQuery" />
      </div>
      <div class="field"><span>분석관</span>
        <FilterSelect v-model="analyst" label="분석관" :options="analystOptions" :disabled="!analysts.length" @change="syncQuery" />
      </div>
    </section>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div v-if="loading" class="empty" role="status">경기 목록을 불러오는 중…</div>
    <div v-else-if="!error && !visibleGroups.length" class="empty">선택한 조건에 경기가 없습니다.</div>

    <template v-else>
    <section v-for="group in visibleGroups" :key="group.id" class="roundGroup" :aria-label="roundLabel(group.id)">
      <h2>{{ roundLabel(group.id) }} <span>{{ group.rows.length }}경기</span></h2>
      <div v-for="match in group.rows" :key="match.gmId" class="matchRow">
        <span class="date">{{ dateLabel(match.date) }}<small v-if="match.kickoffTime">{{ match.kickoffTime }}</small></span>
        <span class="reportSlot">
          <template v-if="showReport(match.home)">
            <button class="reportBtn" type="button" :aria-label="`${match.home.name} 리포트`" @click="openReport(match, 'H')">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></svg>
              <b>리포트</b>
            </button>
            <button class="reportBtn printBtn" type="button" :aria-label="`${match.home.name} 리포트 인쇄`" title="인쇄" @click="openReport(match, 'H', true)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z" /></svg>
            </button>
          </template>
        </span>
        <span class="team home" :class="{ mine: isAnalystSide(match.home) }">
          <span class="name">{{ match.home.name }}</span>
          <img v-if="match.home.crestUrl && !brokenCrests.has(match.home.crestUrl)" :src="match.home.crestUrl" alt="" @error="brokenCrests.add(match.home.crestUrl)">
          <span v-else class="crestEmpty" aria-hidden="true" />
        </span>
        <span class="score">{{ match.score.home ?? '-' }} <i>:</i> {{ match.score.away ?? '-' }}</span>
        <span class="team away" :class="{ mine: isAnalystSide(match.away) }">
          <img v-if="match.away.crestUrl && !brokenCrests.has(match.away.crestUrl)" :src="match.away.crestUrl" alt="" @error="brokenCrests.add(match.away.crestUrl)">
          <span v-else class="crestEmpty" aria-hidden="true" />
          <span class="name">{{ match.away.name }}</span>
        </span>
        <span class="reportSlot">
          <template v-if="showReport(match.away)">
            <button class="reportBtn" type="button" :aria-label="`${match.away.name} 리포트`" @click="openReport(match, 'A')">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></svg>
              <b>리포트</b>
            </button>
            <button class="reportBtn printBtn" type="button" :aria-label="`${match.away.name} 리포트 인쇄`" title="인쇄" @click="openReport(match, 'A', true)">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z" /></svg>
            </button>
          </template>
        </span>
      </div>
    </section>
    </template>
  </div>
</template>

<style scoped>
.page { width: 1280px; height: 800px; box-sizing: border-box; overflow-y: auto; padding: 24px 28px 40px; background: #0b0f17; color: #f0f4f8; }
.topBar { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 20px; padding-bottom: 16px; border-bottom: 1px solid #29313b; }
.topBar h1 { margin: 0; font-size: 24px; color: #67cce1; }
.backBtn { justify-self: start; color: #9da7b3; font-size: 14px; font-weight: 600; text-decoration: none; }
.refreshBtn { justify-self: end; height: 36px; padding: 0 14px; border: 1px solid rgba(99,207,226,.65); border-radius: 4px; background: transparent; color: #8ce6f5; font: inherit; font-size: 13px; font-weight: 750; cursor: pointer; }
.refreshBtn:disabled { opacity: .5; cursor: default; }
.refreshBtn:active { background: rgba(99,207,226,.15); }
.filters { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; padding: 18px 0; border-bottom: 1px solid rgba(255,255,255,.08); }
.filters .field { display: grid; gap: 6px; min-width: 0; }
.filters .field > span { color: rgba(255,255,255,.5); font-size: 12px; font-weight: 750; }
.error { margin: 14px 0 0; color: #ff9d91; font-size: 13px; }
.empty { min-height: 260px; display: grid; place-items: center; color: rgba(255,255,255,.47); font-size: 14px; }
.roundGroup { margin-top: 20px; }
.roundGroup h2 { display: flex; align-items: baseline; gap: 10px; margin: 0 0 8px; font-size: 16px; }
.roundGroup h2 span { color: rgba(255,255,255,.45); font-size: 12px; font-weight: 600; }
.matchRow { display: grid; grid-template-columns: 110px 160px minmax(0, 1fr) 96px minmax(0, 1fr) 160px; align-items: center; gap: 12px; min-height: 60px; padding: 8px 14px; border: 1px solid rgba(255,255,255,.08); border-radius: 6px; background: #121821; }
.matchRow + .matchRow { margin-top: 6px; }
.date { display: grid; gap: 2px; color: rgba(255,255,255,.7); font-size: 13px; font-variant-numeric: tabular-nums; }
.date small { color: rgba(255,255,255,.4); font-size: 11px; }
.team { display: flex; align-items: center; gap: 10px; min-width: 0; font-size: 15px; font-weight: 750; }
.team.mine .name { color: #8ce6f5; }
.team.home { justify-content: flex-end; text-align: right; }
.team .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.team img { width: 28px; height: 28px; object-fit: contain; flex: none; }
.crestEmpty { width: 26px; height: 26px; flex: none; margin: 1px; box-sizing: border-box; border: 2px solid rgba(255,255,255,.22); border-radius: 50%; background: rgba(255,255,255,.04); }
.score { text-align: center; font-size: 20px; font-weight: 850; font-variant-numeric: tabular-nums; }
.score i { color: rgba(255,255,255,.35); font-style: normal; }
.reportSlot { display: flex; justify-content: center; gap: 6px; }
.reportBtn { display: inline-flex; align-items: center; gap: 6px; height: 44px; padding: 0 12px; border: 1px solid rgba(99,207,226,.5); border-radius: 6px; background: rgba(99,207,226,.08); color: #8ce6f5; font: inherit; cursor: pointer; }
.reportBtn:active { background: rgba(99,207,226,.24); }
.reportBtn svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linejoin: round; stroke-linecap: round; }
.reportBtn b { font-size: 13px; }
.reportBtn.printBtn { width: 44px; padding: 0; justify-content: center; }
</style>
