<script setup lang="ts">
type AnalystLevel = 'basic' | 'advanced'
type Lifecycle = 'ready' | 'H1' | 'H1_done' | 'H2' | 'H2_done' | 'final'
type Period = 'all' | 'month' | 'custom' | 'round'

type TeamCount = { teamId: string; teamName: string; count: number }
type AnalystInput = {
  gmId: string
  side: 'H' | 'A'
  date: string
  status: Lifecycle
  teamName: string
  opponentName: string
  leagueId: string
  round: number | null
  score: { home: number; away: number }
  dap: number
  recordCount: number
  updatedAt: string | null
  role: 'primary' | 'assistant'
}
type Analyst = {
  uid: string
  name: string
  level: AnalystLevel
  matches: number
  teamInputs: number
  primaryCount: number
  assistantCount: number
  dapTotal: number
  latestAt: string | null
  teams: TeamCount[]
  topDapInput: AnalystInput | null
  lowDapInput?: AnalystInput | null
  recentInputs: AnalystInput[]
}
type Dashboard = {
  summary: { analystCount: number; matchCount: number; partialMatchCount?: number; teamInputCount: number; dapTotal: number }
  analysts: Analyst[]
  rounds?: number[]
  leagues?: { id: string; name: string; count: number }[]
  generatedAt: string
}

const { request } = useBackendApi()
const data = ref<Dashboard | null>(null)
const loading = ref(false)
const error = ref('')
const selectedUid = ref<string | null>(null)
const search = ref('')
const period = ref<Period>('all')
const dateFrom = ref('')
const dateTo = ref('')
const level = ref<'all' | AnalystLevel>('all')
const league = ref<string>('all')
const round = ref<string>('all')

const lifecycleLabels: Record<Lifecycle, string> = {
  ready: '대기', H1: '전반 진행', H1_done: '전반 종료', H2: '후반 진행', H2_done: '후반 종료', final: '분석 종료',
}
const levelOptions: { value: 'all' | AnalystLevel; label: string }[] = [
  { value: 'all', label: '전체 등급' },
  { value: 'advanced', label: 'ADVANCED' },
  { value: 'basic', label: 'BASIC' },
]
// 선택된 리그는 목록이 바뀌어도 메뉴에 남겨 둔다.
const leagueOptions = computed(() => {
  const leagues = data.value?.leagues ?? []
  const options = [{ value: 'all', label: '전체 리그' }, ...leagues.map(item => ({ value: item.id, label: item.name }))]
  if (league.value !== 'all' && !leagues.some(item => item.id === league.value)) options.push({ value: league.value, label: leagueName(league.value) })
  return options
})

const leagueNames = ref<Record<string, string>>({})
function leagueName(id: string) {
  return leagueNames.value[id] ?? id
}
const periodOptions: { value: 'all' | 'month'; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 'month', label: '이번 달' },
]

// 선택된 라운드가 현재 목록에 없어도(데이터 갱신 등) 메뉴에서 사라지지 않게 유지한다.
const roundOptions = computed(() => {
  const rounds = new Set(data.value?.rounds ?? [])
  if (round.value !== 'all') rounds.add(Number(round.value))
  return [
    { value: 'all', label: '전체 라운드' },
    ...[...rounds].sort((a, b) => b - a).map(value => ({ value: String(value), label: `${value}R` })),
  ]
})

const analysts = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  const rows = data.value?.analysts ?? []
  if (!keyword) return rows
  return rows.filter(analyst => `${analyst.name} ${analyst.uid} ${analyst.teams.map(team => team.teamName).join(' ')}`.toLocaleLowerCase().includes(keyword))
})

const searchBox = ref<HTMLElement | null>(null)
const searchOpen = ref(false)

const analystSuggestions = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  const rows = data.value?.analysts ?? []
  return keyword ? rows.filter(row => `${row.name} ${row.uid}`.toLocaleLowerCase().includes(keyword)) : rows
})

const teamSuggestions = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  const teams = new Map<string, { name: string; analysts: number; count: number }>()
  for (const analyst of data.value?.analysts ?? []) {
    for (const team of analyst.teams) {
      const entry = teams.get(team.teamName) ?? { name: team.teamName, analysts: 0, count: 0 }
      entry.analysts += 1
      entry.count += team.count
      teams.set(team.teamName, entry)
    }
  }
  return [...teams.values()]
    .filter(team => !keyword || team.name.toLocaleLowerCase().includes(keyword))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'ko'))
})

function pickAnalyst(analyst: Analyst) {
  search.value = analyst.name
  selectedUid.value = analyst.uid
  searchOpen.value = false
}

function pickTeam(name: string) {
  search.value = name
  searchOpen.value = false
}

function clearSearch() {
  search.value = ''
  searchOpen.value = false
}

function onSearchOutside(event: PointerEvent) {
  if (searchOpen.value && searchBox.value && !searchBox.value.contains(event.target as Node)) searchOpen.value = false
}

onMounted(() => document.addEventListener('pointerdown', onSearchOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onSearchOutside))

const selectedAnalyst = computed(() => {
  const rows = analysts.value
  return rows.find(analyst => analyst.uid === selectedUid.value) ?? sortedAnalysts.value[0] ?? null
})

type SortKey = 'name' | 'teamInputs' | 'dap' | 'avgDap' | 'latest'
/** 기본 정렬: 팀 입력 많은 순 (같으면 DAP 순) */
const DEFAULT_SORT: SortKey = 'teamInputs'
const sortKey = ref<SortKey>(DEFAULT_SORT)
const sortDir = ref<'asc' | 'desc'>('desc')
const columns: { key: SortKey | null; label: string; align?: 'r' | 'c' }[] = [
  { key: 'name', label: '분석관' },
  { key: null, label: '주 / 부', align: 'c' },
  { key: 'teamInputs', label: '팀 입력', align: 'c' },
  { key: 'dap', label: '총 DAP', align: 'c' },
  { key: 'avgDap', label: 'DAP 평균', align: 'c' },
  { key: 'latest', label: '최근', align: 'c' },
]

function toggleSort(key: SortKey) {
  if (sortKey.value === key) sortDir.value = sortDir.value === 'desc' ? 'asc' : 'desc'
  else {
    sortKey.value = key
    sortDir.value = key === 'name' ? 'asc' : 'desc'
  }
}

function avgDap(analyst: Analyst) {
  return analyst.teamInputs ? analyst.dapTotal / analyst.teamInputs : 0
}

function sortValue(analyst: Analyst, key: SortKey): number | string {
  switch (key) {
    case 'name': return analyst.name.toLocaleLowerCase()
    case 'teamInputs': return analyst.teamInputs
    case 'dap': return analyst.dapTotal
    case 'avgDap': return avgDap(analyst)
    case 'latest': return analyst.latestAt ? new Date(analyst.latestAt).getTime() || 0 : 0
  }
}

const sortedAnalysts = computed(() => {
  const dir = sortDir.value === 'asc' ? 1 : -1
  return [...analysts.value].sort((a, b) => {
    const av = sortValue(a, sortKey.value)
    const bv = sortValue(b, sortKey.value)
    const diff = typeof av === 'string' ? av.localeCompare(String(bv), 'ko') : av - (bv as number)
    return diff * dir || b.dapTotal - a.dapTotal
  })
})

/** 이메일이면 아이디와 도메인을 나눠 아이디를 크게 보여준다 */
function splitName(name: string) {
  const at = name.indexOf('@')
  return at > 0 ? { main: name.slice(0, at), sub: name.slice(at) } : { main: name, sub: '' }
}

const IDLE_DAYS = 14
function daysSince(value: string | null) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  return Math.max(0, Math.round((start(new Date()) - start(date)) / 86400000))
}

function relativeDay(value: string | null) {
  const days = daysSince(value)
  if (days === null) return '-'
  if (days === 0) return '오늘'
  if (days === 1) return '어제'
  if (days < 30) return `${days}일 전`
  return formatDate(value)
}

function isIdle(value: string | null) {
  const days = daysSince(value)
  return days === null || days >= IDLE_DAYS
}

function statusTone(status: Lifecycle) {
  if (status === 'final') return 'done'
  if (status === 'ready') return 'idle'
  return 'live'
}

const showAllTeams = ref(false)
watch(selectedUid, () => { showAllTeams.value = false })


const ROUND_MATCHES = 10
const ROUND_TEAMS = 20

type Kpi = { label: string; value: number; unit: string; note: string; total?: number; progress?: number }
const kpis = computed<Kpi[]>(() => {
  const summary = data.value?.summary
  const rows = data.value?.analysts ?? []
  const analystCount = summary?.analystCount ?? 0
  const matchCount = summary?.matchCount ?? 0
  const teamInputCount = summary?.teamInputCount ?? 0
  const dapTotal = summary?.dapTotal ?? 0
  const advanced = rows.filter(row => row.level === 'advanced').length
  const recent = rows.filter(row => (daysSince(row.latestAt) ?? Infinity) < 7).length
  const partial = summary?.partialMatchCount ?? 0
  // 한 라운드 = 10경기 · 20개 팀. 라운드 선택 시 진행률로 보여준다.
  const inRound = appliedRound.value !== null
  const percent = (value: number, total: number) => Math.min(100, Math.round((value / total) * 100))
  return [
    { label: '활동 분석관', value: analystCount, unit: '명', note: `ADV ${advanced} · BASIC ${rows.length - advanced} · 7일 내 활동 ${recent}명` },
    inRound
      ? { label: '분석 경기', value: matchCount, total: ROUND_MATCHES, unit: '경기', progress: percent(matchCount, ROUND_MATCHES), note: `${appliedRound.value}R 진행률 ${percent(matchCount, ROUND_MATCHES)}% · 남은 경기 ${Math.max(0, ROUND_MATCHES - matchCount)}개` }
      : { label: '분석 경기', value: matchCount, unit: '경기', note: partial ? `양 팀 입력 완료 · 한 팀만 입력 ${partial}경기` : '양 팀 입력 완료 기준' },
    inRound
      ? { label: '팀 입력', value: teamInputCount, total: ROUND_TEAMS, unit: '팀', progress: percent(teamInputCount, ROUND_TEAMS), note: `${appliedRound.value}R 진행률 ${percent(teamInputCount, ROUND_TEAMS)}% · 남은 팀 ${Math.max(0, ROUND_TEAMS - teamInputCount)}개` }
      : { label: '팀 입력', value: teamInputCount, unit: '건', note: `1인당 평균 ${average(teamInputCount, analystCount)}건` },
    { label: '총 DAP', value: dapTotal, unit: '', note: `팀 입력당 평균 ${average(dapTotal, teamInputCount, 0)}` },
  ]
})

const selectedStats = computed(() => {
  const analyst = selectedAnalyst.value
  if (!analyst) return []
  return [
    { label: '팀 입력', value: formatNumber(analyst.teamInputs) },
    { label: '총 DAP', value: formatNumber(analyst.dapTotal), accent: true },
    { label: 'DAP 평균', value: average(analyst.dapTotal, analyst.teamInputs, 0) },
  ]
})

const selectedRoleShare = computed(() => {
  const analyst = selectedAnalyst.value
  if (!analyst) return 0
  const total = analyst.primaryCount + analyst.assistantCount
  return total ? Math.round((analyst.primaryCount / total) * 100) : 0
})

const selectedTeamMax = computed(() => Math.max(1, ...(selectedAnalyst.value?.teams.map(team => team.count) ?? [])))

const generatedLabel = computed(() => {
  if (!data.value?.generatedAt) return ''
  const date = new Date(data.value.generatedAt)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
})

function average(total: number, count: number, digits = 1) {
  if (!count) return '0'
  return new Intl.NumberFormat('ko-KR', { maximumFractionDigits: digits }).format(total / count)
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('ko-KR').format(value)
}

function formatDate(value: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

function inputDate(value: string) {
  return value.length === 8 ? `${value.slice(4, 6)}.${value.slice(6, 8)}` : '-'
}

function toDateInput(date: Date) {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function selectPeriod(value: 'all' | 'month') {
  period.value = value
  round.value = 'all'
  const today = new Date()
  if (value === 'all') {
    dateFrom.value = ''
    dateTo.value = ''
  } else {
    dateFrom.value = toDateInput(new Date(today.getFullYear(), today.getMonth(), 1))
    dateTo.value = toDateInput(today)
  }
  load()
}

/** 라운드와 날짜 기간은 함께 걸면 결과가 비기 쉬워 하나만 적용한다 */
function onRoundChange(value: string) {
  dateFrom.value = ''
  dateTo.value = ''
  period.value = value === 'all' ? 'all' : 'round'
  load()
}

function onCustomDate(range: { from: string; to: string }) {
  dateFrom.value = range.from
  dateTo.value = range.to
  round.value = 'all'
  period.value = range.from || range.to ? 'custom' : 'all'
  load()
}

function selectAnalyst(analyst: Analyst) {
  selectedUid.value = analyst.uid
}

/** 현재 선택된 기간/라운드 조건을 사람이 읽을 수 있게 */
function currentFilterLabel() {
  const leaguePart = league.value !== 'all' ? `${leagueName(league.value)} · ` : ''
  if (round.value !== 'all') return `${leaguePart}${round.value}R`
  if (period.value === 'month') return `${leaguePart}이번 달`
  if (period.value === 'custom') return leaguePart + ([dateFrom.value, dateTo.value].filter(Boolean).map(v => v.slice(5).replace('-', '.')).join(' ~ ') || '기간 지정')
  return league.value !== 'all' ? leagueName(league.value) : '전체 기간'
}
const pendingLabel = ref('')
const appliedLabel = ref('')
/** 화면에 표시 중인 데이터가 어느 라운드 기준인지 (전체면 null) */
const appliedRound = ref<string | null>(null)
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true
  pendingLabel.value = currentFilterLabel()
  const requestRound = round.value !== 'all' ? round.value : null
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (dateFrom.value.trim()) params.set('date_from', dateFrom.value.trim())
    if (dateTo.value.trim()) params.set('date_to', dateTo.value.trim())
    if (level.value !== 'all') params.set('level', level.value)
    if (league.value !== 'all') params.set('league', league.value)
    if (round.value !== 'all') {
      params.set('round_from', round.value)
      params.set('round_to', round.value)
    }
    const suffix = params.size ? `?${params}` : ''
    const result = await request<Dashboard>(`/api/v1/match-input/analyst-dashboard${suffix}`)
    // 연달아 바꾼 경우 마지막 요청의 결과만 반영한다.
    if (seq !== loadSeq) return
    data.value = result
    for (const item of result.leagues ?? []) leagueNames.value[item.id] = item.name
    appliedLabel.value = pendingLabel.value
    appliedRound.value = requestRound
    if (!data.value.analysts.some(analyst => analyst.uid === selectedUid.value)) {
      selectedUid.value = null
    }
  } catch (caught) {
    if (seq !== loadSeq) return
    data.value = null
    error.value = caught instanceof Error ? caught.message : '분석관 운영 데이터를 불러오지 못했습니다.'
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function resetFilters() {
  sortKey.value = DEFAULT_SORT
  sortDir.value = 'desc'
  period.value = 'all'
  dateFrom.value = ''
  dateTo.value = ''
  level.value = 'all'
  league.value = 'all'
  round.value = 'all'
  search.value = ''
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="frame">
      <header class="topBar">
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리</NuxtLink>
        <div class="titleBlock">
          <h1>분석관 운영 현황</h1>
          <p>분석관별 경기 참여와 DAP 실적</p>
        </div>
        <div class="headerActions">
          <span v-if="generatedLabel" class="stamp">{{ generatedLabel }} 기준</span>
          <button class="refreshBtn" type="button" :disabled="loading" @click="load">{{ loading ? '갱신 중…' : '새로고침' }}</button>
        </div>
      </header>

      <section class="toolbar" aria-label="대시보드 필터">
        <DateRangePicker :from="dateFrom" :to="dateTo" :active="period === 'custom'" @change="onCustomDate" />
        <div class="periodChips" role="group" aria-label="기간">
          <button
            v-for="option in periodOptions"
            :key="option.value"
            type="button"
            class="chip"
            :class="{ active: period === option.value }"
            @click="selectPeriod(option.value)"
          >{{ option.label }}</button>
        </div>
        <SelectMenu v-model="league" :options="leagueOptions" width="148px" aria-label="리그" :class="{ roundActive: league !== 'all' }" @change="load" />
        <SelectMenu v-model="round" :options="roundOptions" width="112px" aria-label="라운드" :class="{ roundActive: round !== 'all' }" @change="onRoundChange" />
        <SelectMenu v-model="level" :options="levelOptions" aria-label="등급" @change="load" />
        <div ref="searchBox" class="searchBox" :class="{ open: searchOpen }">
          <svg class="searchIcon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3 3" /></svg>
          <input
            v-model.trim="search"
            class="searchInput"
            type="text"
            placeholder="분석관·팀 검색"
            role="combobox"
            aria-autocomplete="list"
            :aria-expanded="searchOpen"
            @focus="searchOpen = true"
            @input="searchOpen = true"
            @keydown.esc="searchOpen = false"
          >
          <button v-if="search" type="button" class="clearBtn" aria-label="검색어 지우기" @click="clearSearch">×</button>
          <button type="button" class="caretBtn" aria-label="목록 열기" @click="searchOpen = !searchOpen">
            <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5" /></svg>
          </button>
          <div v-if="searchOpen" class="suggest" role="listbox">
            <template v-if="analystSuggestions.length">
              <p class="suggestGroup">분석관 <span>{{ analystSuggestions.length }}</span></p>
              <button v-for="analyst in analystSuggestions" :key="analyst.uid" type="button" role="option" class="suggestItem" @click="pickAnalyst(analyst)">
                <span class="suggestMain">{{ analyst.name }}</span>
                <span class="suggestSub">{{ analyst.teams[0]?.teamName ?? '-' }} · DAP {{ formatNumber(analyst.dapTotal) }}</span>
              </button>
            </template>
            <template v-if="teamSuggestions.length">
              <p class="suggestGroup">팀 <span>{{ teamSuggestions.length }}</span></p>
              <button v-for="team in teamSuggestions" :key="team.name" type="button" role="option" class="suggestItem" @click="pickTeam(team.name)">
                <span class="suggestMain">{{ team.name }}</span>
                <span class="suggestSub">분석관 {{ team.analysts }}명 · {{ team.count }}회</span>
              </button>
            </template>
            <p v-if="!analystSuggestions.length && !teamSuggestions.length" class="suggestEmpty">일치하는 분석관이나 팀이 없습니다.</p>
          </div>
        </div>
        <button class="resetBtn" type="button" @click="resetFilters">초기화</button>
      </section>

      <div class="loadBar" :class="{ on: loading }" aria-hidden="true"><i /></div>
      <p v-if="error" class="error">{{ error }}</p>

      <section class="kpiGrid" :class="{ busy: loading && data }" aria-label="운영 요약">
        <article v-for="kpi in kpis" :key="kpi.label" class="kpi">
          <span class="kpiLabel">{{ kpi.label }}</span>
          <strong>{{ formatNumber(kpi.value) }}<small v-if="kpi.total" class="kpiTotal">/ {{ kpi.total }}</small><small v-if="kpi.unit">{{ kpi.unit }}</small></strong>
          <i v-if="kpi.progress !== undefined" class="kpiProgress"><em :style="{ width: `${kpi.progress}%` }" /></i>
          <span class="kpiNote">{{ kpi.note }}</span>
        </article>
      </section>

      <section class="workspace" :class="{ busy: loading && data }">
        <p v-if="loading && data" class="busyPill" role="status"><i class="spinner" />{{ pendingLabel }} 기준으로 집계하는 중…</p>
        <div class="panel tablePanel">
          <div class="panelHeader">
            <h2>분석관<small v-if="appliedLabel" class="appliedTag">{{ appliedLabel }}</small></h2>
            <span>{{ analysts.length }}명</span>
          </div>
          <div v-if="loading && !data" class="empty">운영 데이터를 집계하고 있습니다.</div>
          <div v-else-if="analysts.length === 0" class="empty">선택한 조건에 참여 이력이 없습니다.</div>
          <div v-else class="analystTable" role="table">
            <div class="tableHead" role="row">
              <span class="c">#</span>
              <template v-for="col in columns" :key="col.label">
                <button
                  v-if="col.key"
                  type="button"
                  class="sortBtn"
                  :class="[col.align, { active: sortKey === col.key }]"
                  :aria-sort="sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'"
                  @click="toggleSort(col.key)"
                >{{ col.label }}<svg class="sortIcon" viewBox="0 0 8 12" aria-hidden="true"><path class="up" :class="{ on: sortKey === col.key && sortDir === 'asc' }" d="M4 1 7 5H1Z" /><path class="down" :class="{ on: sortKey === col.key && sortDir === 'desc' }" d="M4 11 1 7h6Z" /></svg></button>
                <span v-else :class="col.align">{{ col.label }}</span>
              </template>
            </div>
            <button
              v-for="(analyst, index) in sortedAnalysts"
              :key="analyst.uid"
              class="analystRow"
              :class="{ selected: analyst.uid === selectedAnalyst?.uid }"
              type="button"
              role="row"
              @click="selectAnalyst(analyst)"
            >
              <b class="rank" :class="{ top: index < 3 && sortKey === DEFAULT_SORT && sortDir === 'desc' }">{{ index + 1 }}</b>
              <span class="identity">
                <span class="nameStack">
                  <strong>{{ splitName(analyst.name).main }}</strong>
                  <small v-if="splitName(analyst.name).sub">{{ splitName(analyst.name).sub }}</small>
                </span>
                <em class="level" :class="analyst.level">{{ analyst.level === 'advanced' ? 'ADV' : 'BASIC' }}</em>
              </span>
              <span class="num roles">{{ analyst.primaryCount }}<i>/</i>{{ analyst.assistantCount }}</span>
              <span class="num">{{ analyst.teamInputs }}</span>
              <span class="dap">
                <b>{{ formatNumber(analyst.dapTotal) }}</b>
              </span>
              <span class="num soft">{{ average(analyst.dapTotal, analyst.teamInputs, 0) }}</span>
              <span class="lastSeen" :class="{ idle: isIdle(analyst.latestAt) }">{{ relativeDay(analyst.latestAt) }}</span>
            </button>
          </div>
        </div>

        <aside class="panel detailPanel" aria-label="선택 분석관 상세">
          <template v-if="selectedAnalyst">
            <div class="detailTitle">
              <div class="detailName">
                <h2>{{ splitName(selectedAnalyst.name).main }}<span v-if="splitName(selectedAnalyst.name).sub">{{ splitName(selectedAnalyst.name).sub }}</span></h2>
                <small :class="{ idle: isIdle(selectedAnalyst.latestAt) }">최근 참여 {{ formatDate(selectedAnalyst.latestAt) }} · {{ relativeDay(selectedAnalyst.latestAt) }}</small>
              </div>
              <em class="level" :class="selectedAnalyst.level">{{ selectedAnalyst.level === 'advanced' ? 'ADVANCED' : 'BASIC' }}</em>
            </div>

            <div class="statGrid">
              <div v-for="stat in selectedStats" :key="stat.label" class="stat" :class="{ accent: stat.accent }">
                <span>{{ stat.label }}</span><strong>{{ stat.value }}</strong>
              </div>
            </div>

            <div class="roleBlock">
              <div class="roleLabels">
                <span><i class="dot primary" />주 분석관 <b>{{ selectedAnalyst.primaryCount }}</b></span>
                <span><i class="dot assistant" />부 분석관 <b>{{ selectedAnalyst.assistantCount }}</b></span>
              </div>
              <div class="meter"><i class="primary" :style="{ width: `${selectedRoleShare}%` }" /><i class="assistant" :style="{ width: `${100 - selectedRoleShare}%` }" /></div>
            </div>

            <section v-if="selectedAnalyst.topDapInput" class="dapRange">
              <div class="dapExtreme high">
                <span>최고 DAP</span>
                <b>{{ selectedAnalyst.topDapInput.teamName }} <i>vs</i> {{ selectedAnalyst.topDapInput.opponentName }}<small>{{ inputDate(selectedAnalyst.topDapInput.date) }}</small></b>
                <strong>{{ formatNumber(selectedAnalyst.topDapInput.dap) }}</strong>
              </div>
              <div v-if="selectedAnalyst.lowDapInput" class="dapExtreme low">
                <span>최저 DAP</span>
                <b>{{ selectedAnalyst.lowDapInput.teamName }} <i>vs</i> {{ selectedAnalyst.lowDapInput.opponentName }}<small>{{ inputDate(selectedAnalyst.lowDapInput.date) }}</small></b>
                <strong>{{ formatNumber(selectedAnalyst.lowDapInput.dap) }}</strong>
              </div>
            </section>

            <section class="detailSection">
              <div class="detailHeading"><h3>담당 팀</h3><span>{{ selectedAnalyst.teams.length }}개 팀</span></div>
              <div class="teamList">
                <div v-for="team in (showAllTeams ? selectedAnalyst.teams : selectedAnalyst.teams.slice(0, 5))" :key="team.teamId" class="teamLine">
                  <span class="teamName">{{ team.teamName }}</span>
                  <i class="teamBar"><em :style="{ width: `${(team.count / selectedTeamMax) * 100}%` }" /></i>
                  <b>{{ team.count }}회</b>
                </div>
              </div>
              <button v-if="selectedAnalyst.teams.length > 5" type="button" class="moreBtn" @click="showAllTeams = !showAllTeams">
                {{ showAllTeams ? '접기' : `나머지 ${selectedAnalyst.teams.length - 5}개 팀 보기` }}
              </button>
            </section>

            <section class="detailSection">
              <div class="detailHeading"><h3>최근 참여 경기</h3><span>{{ selectedAnalyst.recentInputs.length }}건</span></div>
              <p v-if="!selectedAnalyst.recentInputs.length" class="recentEmpty">참여 기록이 없습니다.</p>
              <div class="recentList">
                <div v-for="input in selectedAnalyst.recentInputs" :key="`${input.gmId}_${input.side}_${input.role}`" class="recentRow">
                  <span class="recentDate">{{ inputDate(input.date) }}</span>
                  <span class="recentMatch">
                    <b>{{ input.teamName }} <i>vs</i> {{ input.opponentName }}</b>
                    <small>{{ input.round ?? '-' }}R · {{ input.score.home }}:{{ input.score.away }}<em class="status" :class="statusTone(input.status)">{{ lifecycleLabels[input.status] ?? input.status }}</em></small>
                  </span>
                  <span class="recentMeta">
                    <em :class="input.role">{{ input.role === 'primary' ? '주' : '부' }}</em>
                    <b>{{ formatNumber(input.dap) }}</b>
                  </span>
                </div>
              </div>
            </section>

            <p class="uid">계정 ID · {{ selectedAnalyst.uid }}</p>
          </template>
          <div v-else class="empty detailEmpty">분석관을 선택하세요.</div>
        </aside>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page {
  --bg: #0a0e14; --panel: #10161f; --panel2: #151c26; --line: rgba(255,255,255,.08); --line2: rgba(255,255,255,.14);
  --text: #eef2f6; --muted: rgba(238,242,246,.55); --faint: rgba(238,242,246,.36);
  --gold: #f0c04a; --goldSoft: rgba(240,192,74,.14); --cyan: #6fd3e6; --cyanSoft: rgba(111,211,230,.14);
  width: 1280px; height: 800px; box-sizing: border-box; overflow: hidden; background: var(--bg); color: var(--text); padding: 20px 28px 20px;
}
.frame { max-width: 1440px; height: 100%; margin: 0 auto; display: flex; flex-direction: column; min-height: 0; }

/* 헤더 */
.topBar { flex: none; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.titleBlock { text-align: center; }
.titleBlock h1 { margin: 0; font-size: 24px; letter-spacing: -.01em; }
.titleBlock p { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.backBtn { justify-self: start; color: #9da7b3; font-size: 14px; font-weight: 600; text-decoration: none; }
.headerActions { justify-self: end; display: flex; align-items: center; gap: 12px; }
.stamp { color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }
.refreshBtn, .resetBtn { height: 36px; padding: 0 14px; border: 1px solid var(--line2); border-radius: 6px; background: transparent; color: var(--text); font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; }
.refreshBtn { border-color: rgba(111,211,230,.5); color: var(--cyan); }
.refreshBtn:disabled { opacity: .5; cursor: wait; }

/* 필터 */
.toolbar { flex: none; display: flex; align-items: center; gap: 8px; padding: 12px 0; }
.periodChips { display: flex; padding: 3px; border: 1px solid var(--line2); border-radius: 8px; background: var(--panel); }
.chip { height: 30px; padding: 0 12px; border: 0; border-radius: 5px; background: transparent; color: var(--muted); font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; white-space: nowrap; }
.chip.active { background: var(--text); color: #0a0e14; }
.roundActive :deep(.trigger) { border-color: rgba(111,211,230,.5); color: var(--cyan); }
.searchInput { height: 36px; box-sizing: border-box; padding: 0 10px; border: 1px solid var(--line2); border-radius: 6px; background: var(--panel); color: var(--text); font: inherit; font-size: 13px; color-scheme: dark; }
.searchBox { position: relative; flex: 1; min-width: 180px; }
.searchIcon { position: absolute; top: 50%; left: 11px; width: 15px; height: 15px; transform: translateY(-50%); fill: none; stroke: rgba(238,242,246,.45); stroke-width: 1.5; stroke-linecap: round; pointer-events: none; }
.searchBox .searchInput { width: 100%; padding: 0 64px 0 34px; }
.searchInput:focus { outline: none; }
.searchBox.open .searchInput { border-color: #6fd3e6; box-shadow: 0 0 0 2px rgba(111,211,230,.18); }
.clearBtn, .caretBtn { position: absolute; top: 50%; width: 28px; height: 28px; transform: translateY(-50%); border: 0; border-radius: 5px; background: transparent; color: rgba(238,242,246,.55); font: inherit; font-size: 18px; line-height: 1; cursor: pointer; display: grid; place-items: center; }
.clearBtn { right: 34px; }
.caretBtn { right: 4px; }
.clearBtn:active, .caretBtn:active { background: rgba(255,255,255,.1); }
.caretBtn svg { width: 12px; height: 12px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; transition: transform .15s; }
.searchBox.open .caretBtn { color: #6fd3e6; }
.searchBox.open .caretBtn svg { transform: rotate(180deg); }
.suggest { position: absolute; top: calc(100% + 6px); left: 0; right: 0; z-index: 50; max-height: 360px; overflow-y: auto; padding: 6px; border: 1px solid rgba(255,255,255,.12); border-radius: 10px; background: #151c26; box-shadow: 0 18px 40px rgba(0,0,0,.55); }
.suggestGroup { display: flex; gap: 6px; margin: 6px 10px 4px; color: var(--faint); font-size: 11px; font-weight: 800; }
.suggestGroup span { color: var(--muted); font-weight: 600; }
.suggestItem { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; min-height: 40px; padding: 0 10px; border: 0; border-radius: 6px; background: transparent; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
.suggestItem:active { background: rgba(255,255,255,.12); }
@media (hover: hover) { .suggestItem:hover { background: rgba(255,255,255,.06); } .clearBtn:hover, .caretBtn:hover { color: var(--text); } }
.suggestMain { overflow: hidden; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.suggestSub { flex: none; color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }
.suggestEmpty { margin: 0; padding: 18px 10px; color: var(--muted); font-size: 13px; text-align: center; }
.loadBar { flex: none; position: relative; height: 2px; margin: -6px 0 10px; border-radius: 1px; overflow: hidden; background: transparent; }
.loadBar.on { background: rgba(111,211,230,.12); }
.loadBar i { position: absolute; top: 0; bottom: 0; left: -40%; width: 40%; background: var(--cyan); opacity: 0; }
.loadBar.on i { opacity: 1; animation: loadSlide 1s ease-in-out infinite; }
@keyframes loadSlide { to { left: 100%; } }
.kpiGrid, .workspace { transition: opacity .15s; }
.busy { opacity: .4; pointer-events: none; }
.workspace.busy { opacity: 1; }
.workspace.busy > .panel { opacity: .4; }
.busyPill { position: absolute; top: 72px; left: 50%; z-index: 5; transform: translateX(-50%); display: flex; align-items: center; gap: 8px; margin: 0; padding: 10px 16px; border: 1px solid rgba(111,211,230,.4); border-radius: 999px; background: #151c26; color: var(--cyan); font-size: 13px; font-weight: 700; white-space: nowrap; box-shadow: 0 12px 30px rgba(0,0,0,.5); }
.spinner { width: 14px; height: 14px; border: 2px solid rgba(111,211,230,.25); border-top-color: var(--cyan); border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.appliedTag { margin-left: 8px !important; padding: 2px 7px; border-radius: 4px; background: var(--cyanSoft); color: var(--cyan) !important; font-size: 11px !important; font-weight: 800 !important; }
.error { margin: 0 0 12px; padding: 10px 12px; border-radius: 6px; background: rgba(255,120,100,.1); color: #ff9d91; font-size: 13px; }

/* KPI */
.kpiGrid { flex: none; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 12px; }
.kpi { display: grid; gap: 4px; padding: 12px 18px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
.kpiLabel { color: var(--muted); font-size: 13px; font-weight: 700; }
.kpi strong { font-size: 28px; line-height: 1.1; font-weight: 800; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
.kpi strong small { margin-left: 4px; color: var(--muted); font-size: 15px; font-weight: 700; }
.kpiNote { color: var(--faint); font-size: 12px; }
.kpi strong small.kpiTotal { color: var(--faint); font-size: 18px; font-weight: 700; }
.kpiProgress { display: block; height: 4px; border-radius: 2px; overflow: hidden; background: rgba(255,255,255,.08); }
.kpiProgress em { display: block; height: 100%; border-radius: 2px; background: var(--cyan); transition: width .3s; }
.kpi:last-child strong { color: var(--gold); }

/* 패널 공통 */
.workspace { position: relative; flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1.85fr) minmax(340px, 1fr); gap: 14px; }
.panel { min-height: 0; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); overflow: hidden; }
.tablePanel { display: flex; flex-direction: column; }
.analystTable { flex: 1; min-height: 0; overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; }
.detailPanel { overflow-y: auto; overscroll-behavior: contain; }
.panelHeader { flex: none; display: flex; justify-content: space-between; align-items: center; padding: 16px 18px; border-bottom: 1px solid var(--line); }
.panelHeader h2 { margin: 0; font-size: 15px; }
.panelHeader h2 small { margin-left: 6px; color: var(--faint); font-size: 12px; font-weight: 600; }
.panelHeader > span { color: var(--muted); font-size: 12px; }
.empty { min-height: 240px; display: grid; place-items: center; color: var(--muted); font-size: 13px; }

/* 순위 표 */
.tableHead, .analystRow { display: grid; grid-template-columns: 24px minmax(150px, 1.4fr) minmax(56px, .7fr) minmax(52px, .7fr) minmax(72px, .7fr) minmax(76px, .7fr) minmax(72px, .8fr); gap: 12px; align-items: center; box-sizing: border-box; width: 100%; }
.tableHead { position: sticky; top: 0; z-index: 2; padding: 6px 18px; background: var(--panel); color: var(--faint); border-bottom: 1px solid var(--line); font-size: 12px; font-weight: 700; }
.tableHead .r { text-align: right; }
.tableHead .c { text-align: center; }
.sortBtn { position: relative; justify-self: start; display: inline-flex; align-items: center; gap: 3px; min-height: 32px; margin: 0 -6px; padding: 0 6px; border: 0; border-radius: 5px; background: transparent; color: inherit; font: inherit; cursor: pointer; white-space: nowrap; }
.sortBtn.r { justify-self: end; }
.sortBtn.c { justify-self: center; }
/* 화살표는 열 사이 간격에 띄워 글자 정렬이 숫자와 맞도록 한다 */
.sortIcon { position: absolute; top: 50%; left: calc(100% - 4px); width: 7px; height: 11px; transform: translateY(-50%); }
.sortIcon path { fill: currentColor; opacity: .3; }
.sortIcon path.on { fill: var(--cyan); opacity: 1; }
.sortBtn.active { color: var(--cyan); }
.sortBtn:active { background: rgba(255,255,255,.1); }
.analystRow { width: 100%; min-height: 52px; padding: 8px 18px; border: 0; border-bottom: 1px solid var(--line); background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.analystRow:last-child { border-bottom: 0; }
.analystRow:active { background: rgba(255,255,255,.06); }
@media (hover: hover) { .analystRow:hover { background: rgba(255,255,255,.035); } }
.analystRow.selected { background: var(--cyanSoft); box-shadow: inset 3px 0 0 var(--cyan); }
.rank { color: var(--faint); font-size: 13px; text-align: center; font-variant-numeric: tabular-nums; }
.rank.top { color: var(--gold); }
.identity { display: flex; align-items: center; gap: 8px; min-width: 0; }
.nameStack { display: grid; min-width: 0; }
.nameStack strong, .nameStack small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nameStack strong { color: var(--text); font-size: 14px; font-weight: 700; }
.nameStack small { margin-top: 1px; color: var(--faint); font-size: 11px; }
.num.soft { color: rgba(238,242,246,.72); font-weight: 600; }
.lastSeen { color: rgba(238,242,246,.72); font-size: 12px; text-align: center; white-space: nowrap; }
.lastSeen.idle { color: #ff9d91; }
.num { color: var(--text); font-size: 14px; font-weight: 700; text-align: center; font-variant-numeric: tabular-nums; }
.roles i { margin: 0 3px; color: var(--faint); font-style: normal; font-weight: 400; }
.dap { text-align: center; }
.dap b { color: var(--gold); font-size: 15px; font-variant-numeric: tabular-nums; }
.topTeam { overflow: hidden; color: rgba(238,242,246,.82); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.topTeam small { margin-left: 6px; color: var(--faint); font-size: 12px; }
.level { flex: none; padding: 3px 6px; border-radius: 4px; font-size: 10px; font-style: normal; font-weight: 800; letter-spacing: .04em; }
.level.advanced { background: var(--goldSoft); color: var(--gold); }
.level.basic { background: var(--cyanSoft); color: var(--cyan); }
.identity .level.advanced { background: transparent; color: var(--faint); box-shadow: inset 0 0 0 1px var(--line2); }

/* 상세 */
.detailTitle { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 18px; border-bottom: 1px solid var(--line); }
.detailName { min-width: 0; }
.detailName h2 { margin: 0; overflow: hidden; font-size: 17px; text-overflow: ellipsis; white-space: nowrap; }
.detailName h2 span { color: var(--faint); font-size: 13px; font-weight: 600; }
.detailName small { display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }
.detailName small.idle { color: #ff9d91; }
.moreBtn { width: 100%; min-height: 36px; margin-top: 8px; border: 1px dashed var(--line2); border-radius: 6px; background: transparent; color: var(--muted); font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; }
.moreBtn:active { background: rgba(255,255,255,.06); }
.recentEmpty { margin: 0; padding: 12px 0; color: var(--faint); font-size: 12px; }
.status { margin-left: 6px; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-style: normal; font-weight: 800; }
.status.done { background: rgba(255,255,255,.07); color: var(--muted); }
.status.live { background: rgba(120,220,140,.14); color: #86e09a; }
.status.idle { background: var(--cyanSoft); color: var(--cyan); }
.statGrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; margin: 16px 18px 0; border-radius: 8px; overflow: hidden; background: var(--line); }
.stat { display: grid; gap: 4px; padding: 12px 10px; background: var(--panel2); }
.stat span { color: var(--muted); font-size: 11px; }
.stat strong { font-size: 18px; font-variant-numeric: tabular-nums; }
.stat.accent strong { color: var(--gold); }
.roleBlock { padding: 14px 18px 0; }
.roleLabels { display: flex; justify-content: space-between; margin-bottom: 8px; color: var(--muted); font-size: 12px; }
.roleLabels b { margin-left: 4px; color: var(--text); font-variant-numeric: tabular-nums; }
.dot { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; vertical-align: 0; }
.dot.primary, .meter .primary { background: var(--gold); }
.dot.assistant, .meter .assistant { background: var(--cyan); }
.meter { display: flex; height: 6px; border-radius: 3px; overflow: hidden; background: rgba(255,255,255,.08); }
.meter i { height: 100%; }
.dapRange { display: grid; gap: 6px; margin: 16px 18px 0; }
.dapExtreme { display: grid; grid-template-columns: 52px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 11px 14px; border-radius: 8px; }
.dapExtreme.high { background: var(--goldSoft); }
.dapExtreme.low { background: rgba(255,255,255,.04); box-shadow: inset 0 0 0 1px var(--line); }
.dapExtreme span { font-size: 11px; font-weight: 800; white-space: nowrap; }
.dapExtreme.high span, .dapExtreme.high strong { color: var(--gold); }
.dapExtreme.low span, .dapExtreme.low strong { color: rgba(238,242,246,.7); }
.dapExtreme b { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.dapExtreme i { color: var(--faint); font-style: normal; font-weight: 400; }
.dapExtreme small { margin-left: 6px; color: var(--faint); font-size: 11px; font-weight: 600; }
.dapExtreme strong { font-size: 16px; font-variant-numeric: tabular-nums; }
.detailSection { margin-top: 18px; padding: 0 18px; }
.detailHeading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.detailHeading h3 { margin: 0; font-size: 13px; }
.detailHeading span { color: var(--faint); font-size: 12px; }
.teamList { display: grid; gap: 8px; }
.teamLine { display: grid; grid-template-columns: 96px minmax(0, 1fr) 36px; align-items: center; gap: 10px; font-size: 12px; }
.teamName { overflow: hidden; color: rgba(238,242,246,.85); text-overflow: ellipsis; white-space: nowrap; }
.teamBar { display: block; height: 6px; border-radius: 3px; overflow: hidden; background: rgba(255,255,255,.06); }
.teamBar em { display: block; height: 100%; border-radius: 3px; background: rgba(240,192,74,.7); }
.teamLine b { color: var(--text); text-align: right; font-variant-numeric: tabular-nums; }
.recentRow { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 10px 0; border-top: 1px solid var(--line); }
.recentRow:first-child { border-top: 0; }
.recentDate { color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }
.recentMatch { min-width: 0; }
.recentMatch b, .recentMatch small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recentMatch b { font-size: 13px; font-weight: 600; }
.recentMatch i { color: var(--faint); font-style: normal; font-weight: 400; }
.recentMatch small { margin-top: 3px; color: var(--faint); font-size: 11px; }
.recentMeta { display: flex; align-items: center; gap: 8px; }
.recentMeta em { padding: 2px 6px; border-radius: 4px; font-size: 11px; font-style: normal; font-weight: 800; }
.recentMeta em.primary { background: var(--goldSoft); color: var(--gold); }
.recentMeta em.assistant { background: var(--cyanSoft); color: var(--cyan); }
.recentMeta b { min-width: 34px; color: var(--text); font-size: 13px; text-align: right; font-variant-numeric: tabular-nums; }
.uid { margin: 14px 0 0; padding: 12px 18px; border-top: 1px solid var(--line); color: var(--faint); font-size: 11px; word-break: break-all; }
.detailEmpty { min-height: 480px; }

@media (max-width: 980px) {
  .page { padding: 18px; overflow-y: auto; }
  .frame { height: auto; }
  .analystTable, .detailPanel { overflow: visible; }
  .toolbar { flex-wrap: wrap; }
  .kpiGrid { grid-template-columns: repeat(2, 1fr); }
  .workspace { grid-template-columns: 1fr; }
}
</style>
