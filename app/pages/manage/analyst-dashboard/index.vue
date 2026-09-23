<script setup lang="ts">
type AnalystLevel = 'basic' | 'advanced'
type Lifecycle = 'ready' | 'H1' | 'H1_done' | 'H2' | 'H2_done' | 'final'

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
  recentInputs: AnalystInput[]
}
type Dashboard = {
  summary: { analystCount: number; matchCount: number; teamInputCount: number; dapTotal: number }
  analysts: Analyst[]
  generatedAt: string
}

const { request } = useBackendApi()
const data = ref<Dashboard | null>(null)
const loading = ref(false)
const error = ref('')
const selectedUid = ref<string | null>(null)
const search = ref('')
const dateFrom = ref('')
const dateTo = ref('')
const level = ref<'all' | AnalystLevel>('all')
const lifecycle = ref<'all' | Lifecycle>('all')

const lifecycleLabels: Record<Lifecycle, string> = {
  ready: '대기', H1: '전반 진행', H1_done: '전반 종료', H2: '후반 진행', H2_done: '후반 종료', final: '분석 종료',
}

const analysts = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  const rows = data.value?.analysts ?? []
  if (!keyword) return rows
  return rows.filter(analyst => `${analyst.name} ${analyst.uid} ${analyst.teams.map(team => team.teamName).join(' ')}`.toLocaleLowerCase().includes(keyword))
})

const selectedAnalyst = computed(() => {
  const rows = analysts.value
  return rows.find(analyst => analyst.uid === selectedUid.value) ?? rows[0] ?? null
})

const maxDap = computed(() => Math.max(1, ...analysts.value.map(analyst => analyst.dapTotal)))
const selectedRoleTotal = computed(() => {
  const analyst = selectedAnalyst.value
  return analyst ? Math.max(1, analyst.primaryCount + analyst.assistantCount) : 1
})

function formatNumber(value: number) {
  return new Intl.NumberFormat('ko-KR').format(value)
}

function formatDate(value: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

function inputDate(value: string) {
  return value.length === 8 ? `${value.slice(0, 4)}.${value.slice(4, 6)}.${value.slice(6, 8)}` : '-'
}

function selectAnalyst(analyst: Analyst) {
  selectedUid.value = analyst.uid
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (dateFrom.value.trim()) params.set('date_from', dateFrom.value.trim())
    if (dateTo.value.trim()) params.set('date_to', dateTo.value.trim())
    if (level.value !== 'all') params.set('level', level.value)
    if (lifecycle.value !== 'all') params.set('lifecycle', lifecycle.value)
    const suffix = params.size ? `?${params}` : ''
    data.value = await request<Dashboard>(`/api/v1/match-input/analyst-dashboard${suffix}`)
    if (!data.value.analysts.some(analyst => analyst.uid === selectedUid.value)) {
      selectedUid.value = data.value.analysts[0]?.uid ?? null
    }
  } catch (caught) {
    data.value = null
    error.value = caught instanceof Error ? caught.message : '분석관 운영 데이터를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  dateFrom.value = ''
  dateTo.value = ''
  level.value = 'all'
  lifecycle.value = 'all'
  search.value = ''
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="frame">
      <header class="topBar">
        <div>
          <p class="eyebrow">OPERATIONS</p>
          <h1>분석관 운영 대시보드</h1>
          <p class="subtitle">참가한 팀 입력 세션과 담당 DAP 기준의 운영 현황</p>
        </div>
        <div class="headerActions">
          <button class="refreshBtn" :disabled="loading" @click="load">{{ loading ? '갱신 중' : '새로고침' }}</button>
          <NuxtLink class="backBtn" to="/manage">데이터 관리</NuxtLink>
        </div>
      </header>

      <section class="filters" aria-label="대시보드 필터">
        <label><span>시작일</span><input v-model.trim="dateFrom" inputmode="numeric" placeholder="YYYY-MM-DD" @keyup.enter="load"></label>
        <label><span>종료일</span><input v-model.trim="dateTo" inputmode="numeric" placeholder="YYYY-MM-DD" @keyup.enter="load"></label>
        <label><span>등급</span><select v-model="level" @change="load"><option value="all">전체 등급</option><option value="advanced">ADVANCED</option><option value="basic">BASIC</option></select></label>
        <label><span>입력 상태</span><select v-model="lifecycle" @change="load"><option value="all">전체 상태</option><option v-for="(label, value) in lifecycleLabels" :key="value" :value="value">{{ label }}</option></select></label>
        <label class="search"><span>분석관 또는 팀</span><input v-model.trim="search" placeholder="이름, 계정 ID, 팀 검색"></label>
        <button class="resetBtn" type="button" @click="resetFilters">초기화</button>
      </section>

      <p v-if="error" class="error">{{ error }}</p>

      <section class="summaryGrid" aria-label="운영 요약">
        <article class="summaryItem cyan"><span>분석관</span><strong>{{ formatNumber(data?.summary.analystCount ?? 0) }}</strong><small>활동 계정</small></article>
        <article class="summaryItem gold"><span>경기</span><strong>{{ formatNumber(data?.summary.matchCount ?? 0) }}</strong><small>고유 경기 수</small></article>
        <article class="summaryItem coral"><span>팀 입력</span><strong>{{ formatNumber(data?.summary.teamInputCount ?? 0) }}</strong><small>참가 세션</small></article>
        <article class="summaryItem green"><span>담당 DAP</span><strong>{{ formatNumber(data?.summary.dapTotal ?? 0) }}</strong><small>입력 팀 기준</small></article>
      </section>

      <section class="workspace">
        <div class="tablePanel">
          <div class="panelHeader"><h2>분석관 비교</h2><span>{{ analysts.length }}명</span></div>
          <div v-if="loading" class="empty">운영 데이터를 집계하고 있습니다.</div>
          <div v-else-if="analysts.length === 0" class="empty">선택한 조건에 참여 이력이 없습니다.</div>
          <div v-else class="analystTable" role="table">
            <div class="tableHead" role="row"><span>분석관</span><span>등급</span><span>경기</span><span>역할</span><span>DAP</span><span>주 담당 팀</span></div>
            <button
              v-for="(analyst, index) in analysts"
              :key="analyst.uid"
              class="analystRow"
              :class="{ selected: analyst.uid === selectedAnalyst?.uid }"
              type="button"
              @click="selectAnalyst(analyst)"
            >
              <span class="identity"><b class="rank">{{ index + 1 }}</b><span><strong>{{ analyst.name }}</strong><small>{{ analyst.uid }}</small></span></span>
              <span class="level" :class="analyst.level">{{ analyst.level === 'advanced' ? 'ADVANCED' : 'BASIC' }}</span>
              <span class="metric"><b>{{ analyst.matches }}</b><small>경기 / {{ analyst.teamInputs }}팀</small></span>
              <span class="roles"><b>주 {{ analyst.primaryCount }}</b><small>부 {{ analyst.assistantCount }}</small></span>
              <span class="dap"><b>{{ formatNumber(analyst.dapTotal) }}</b><i><em :style="{ width: `${(analyst.dapTotal / maxDap) * 100}%` }" /></i></span>
              <span class="topTeam">{{ analyst.teams[0]?.teamName ?? '-' }}<small v-if="analyst.teams[0]">{{ analyst.teams[0].count }}회</small></span>
            </button>
          </div>
        </div>

        <aside class="detailPanel" aria-label="선택 분석관 상세">
          <template v-if="selectedAnalyst">
            <div class="detailTitle">
              <div><p>ANALYST PROFILE</p><h2>{{ selectedAnalyst.name }}</h2><small>{{ selectedAnalyst.uid }}</small></div>
              <span class="level" :class="selectedAnalyst.level">{{ selectedAnalyst.level === 'advanced' ? 'ADVANCED' : 'BASIC' }}</span>
            </div>
            <div class="roleMeter">
              <div><span>주 분석관</span><strong>{{ selectedAnalyst.primaryCount }}</strong></div>
              <div><span>부 분석관</span><strong>{{ selectedAnalyst.assistantCount }}</strong></div>
              <div class="meter"><i class="primary" :style="{ width: `${(selectedAnalyst.primaryCount / selectedRoleTotal) * 100}%` }" /><i class="assistant" :style="{ width: `${(selectedAnalyst.assistantCount / selectedRoleTotal) * 100}%` }" /></div>
            </div>
            <section v-if="selectedAnalyst.topDapInput" class="dapFocus">
              <span>최고 DAP 경기</span>
              <b>{{ selectedAnalyst.topDapInput.teamName }} <i>vs</i> {{ selectedAnalyst.topDapInput.opponentName }}</b>
              <strong>DAP {{ selectedAnalyst.topDapInput.dap }}</strong>
            </section>
            <section class="detailSection"><div class="detailHeading"><h3>팀 입력 분포</h3><span>{{ selectedAnalyst.teamInputs }}팀 세션</span></div><div class="teamList"><div v-for="team in selectedAnalyst.teams.slice(0, 6)" :key="team.teamId" class="teamLine"><span>{{ team.teamName }}</span><b>{{ team.count }}회</b></div></div></section>
            <section class="detailSection recent"><div class="detailHeading"><h3>최근 참여 경기</h3><span>{{ formatDate(selectedAnalyst.latestAt) }}</span></div><div class="recentList"><div v-for="input in selectedAnalyst.recentInputs" :key="`${input.gmId}_${input.side}_${input.role}`" class="recentRow"><span class="recentDate">{{ inputDate(input.date) }}</span><span class="recentMatch"><b>{{ input.teamName }} <i>vs</i> {{ input.opponentName }}</b><small>{{ input.leagueId }} · {{ input.round ?? '-' }}R · {{ input.score.home }} : {{ input.score.away }}</small></span><span class="recentMeta"><em :class="input.role">{{ input.role === 'primary' ? '주' : '부' }}</em><b>DAP {{ input.dap }}</b></span></div></div></section>
          </template>
          <div v-else class="empty detailEmpty">분석관을 선택하세요.</div>
        </aside>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; background: #090d13; color: #f0f4f8; padding: 28px; }
.frame { max-width: 1440px; margin: 0 auto; }
.topBar { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; padding: 6px 0 22px; border-bottom: 1px solid rgba(255,255,255,.1); }
.eyebrow { margin: 0 0 6px; color: #67cce1; font-size: 11px; font-weight: 850; letter-spacing: .12em; }.topBar h1 { margin: 0; font-size: 28px; letter-spacing: 0; }.subtitle { margin: 8px 0 0; color: rgba(240,244,248,.54); font-size: 13px; }.headerActions { display: flex; gap: 8px; }.refreshBtn,.backBtn,.resetBtn { height: 34px; padding: 0 12px; border: 1px solid rgba(255,255,255,.18); border-radius: 4px; background: transparent; color: rgba(255,255,255,.82); font: inherit; font-size: 12px; font-weight: 750; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; }.refreshBtn { border-color: rgba(99,207,226,.65); color: #8ce6f5; }.refreshBtn:disabled { opacity: .5; cursor: wait; }
.filters { display: grid; grid-template-columns: 132px 132px 136px 142px minmax(180px,1fr) auto; gap: 10px; align-items: end; padding: 18px 0; border-bottom: 1px solid rgba(255,255,255,.08); }.filters label { display: grid; gap: 6px; min-width: 0; }.filters label span { color: rgba(255,255,255,.5); font-size: 11px; font-weight: 750; }.filters input,.filters select { width: 100%; height: 34px; box-sizing: border-box; padding: 0 9px; border: 1px solid rgba(255,255,255,.14); border-radius: 4px; background: #141a22; color: #edf3f8; font: inherit; font-size: 12px; }.filters input:focus,.filters select:focus { outline: 2px solid rgba(103,204,225,.65); outline-offset: 1px; }.resetBtn { margin-bottom: 0; }
.error { margin: 14px 0 0; color: #ff9d91; font-size: 13px; }.summaryGrid { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; padding: 18px 0; }.summaryItem { min-height: 112px; padding: 16px; border: 1px solid rgba(255,255,255,.12); border-top: 3px solid #7ed6e8; background: #101620; display: grid; align-content: space-between; }.summaryItem.gold { border-top-color: #e6b52b; }.summaryItem.coral { border-top-color: #ef927e; }.summaryItem.green { border-top-color: #91c56d; }.summaryItem span,.summaryItem small { color: rgba(255,255,255,.52); font-size: 12px; }.summaryItem strong { color: #fff; font-size: 30px; font-variant-numeric: tabular-nums; }
.workspace { display: grid; grid-template-columns: minmax(0,1.65fr) minmax(350px,.85fr); gap: 14px; align-items: start; }.tablePanel,.detailPanel { border: 1px solid rgba(255,255,255,.12); background: #0e141d; }.panelHeader,.detailTitle { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 16px; border-bottom: 1px solid rgba(255,255,255,.09); }.panelHeader h2,.detailTitle h2,.detailHeading h3 { margin: 0; font-size: 15px; }.panelHeader span,.detailHeading span { color: rgba(255,255,255,.48); font-size: 12px; }.empty { min-height: 230px; display: grid; place-items: center; color: rgba(255,255,255,.47); font-size: 13px; }
.analystTable { overflow: auto; }.tableHead,.analystRow { display: grid; grid-template-columns: minmax(190px,1.45fr) 98px 104px 92px minmax(110px,.85fr) minmax(120px,1fr); gap: 12px; align-items: center; min-width: 760px; }.tableHead { padding: 10px 16px; color: rgba(255,255,255,.44); border-bottom: 1px solid rgba(255,255,255,.08); font-size: 11px; font-weight: 800; }.analystRow { width: 100%; padding: 12px 16px; border: 0; border-bottom: 1px solid rgba(255,255,255,.07); background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }.analystRow:hover { background: rgba(255,255,255,.045); }.analystRow.selected { background: rgba(103,204,225,.10); box-shadow: inset 3px 0 0 #67cce1; }.identity { display: flex; align-items: center; gap: 10px; min-width: 0; }.rank { width: 20px; color: rgba(255,255,255,.38); font-size: 12px; text-align: center; }.identity strong,.identity small,.metric b,.metric small,.roles b,.roles small,.topTeam,.topTeam small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.identity strong { color: #f4f7fb; font-size: 13px; }.identity small,.metric small,.roles small,.topTeam small { margin-top: 3px; color: rgba(255,255,255,.43); font-size: 11px; }.level { justify-self: start; padding: 4px 6px; border-radius: 3px; font-size: 10px; font-weight: 900; letter-spacing: .04em; }.level.advanced { background: rgba(231,181,43,.14); color: #f3c64b; }.level.basic { background: rgba(103,204,225,.14); color: #8be4f3; }.metric b,.roles b { color: #fff; font-size: 13px; }.dap b { color: #f3c64b; font-size: 15px; font-variant-numeric: tabular-nums; }.dap i { display: block; width: 100%; height: 4px; margin-top: 6px; overflow: hidden; background: rgba(255,255,255,.10); }.dap em { display: block; height: 100%; background: #e6b52b; }.topTeam { color: rgba(255,255,255,.82); font-size: 12px; }
.detailTitle p { margin: 0 0 5px; color: #67cce1; font-size: 10px; font-weight: 850; letter-spacing: .1em; }.detailTitle small { color: rgba(255,255,255,.45); font-size: 11px; }.roleMeter { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 16px; }.roleMeter div:not(.meter) { display: grid; gap: 5px; padding: 10px; background: rgba(255,255,255,.035); }.roleMeter span { color: rgba(255,255,255,.5); font-size: 11px; }.roleMeter strong { font-size: 22px; font-variant-numeric: tabular-nums; }.meter { grid-column: 1 / -1; display: flex; height: 7px; overflow: hidden; background: rgba(255,255,255,.09); }.meter i { height: 100%; }.meter .primary { background: #e6b52b; }.meter .assistant { background: #67cce1; }.detailSection { padding: 0 16px 16px; }.detailHeading { display: flex; justify-content: space-between; align-items: center; padding: 8px 0 10px; }.detailHeading h3 { font-size: 13px; }.teamList { display: grid; gap: 6px; }.teamLine { display: flex; justify-content: space-between; gap: 12px; padding: 8px 10px; background: rgba(255,255,255,.035); color: rgba(255,255,255,.78); font-size: 12px; }.teamLine b { color: #f3c64b; }.recent { border-top: 1px solid rgba(255,255,255,.08); padding-top: 8px; }.recentList { display: grid; }.recentRow { display: grid; grid-template-columns: 76px minmax(0,1fr) 62px; gap: 8px; padding: 10px 0; border-top: 1px solid rgba(255,255,255,.07); }.recentDate { color: rgba(255,255,255,.45); font-size: 11px; }.recentMatch { min-width: 0; }.recentMatch b,.recentMatch small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.recentMatch b { color: rgba(255,255,255,.84); font-size: 12px; }.recentMatch i { color: rgba(255,255,255,.35); font-style: normal; }.recentMatch small { margin-top: 4px; color: rgba(255,255,255,.43); font-size: 10px; }.recentMeta { display: grid; justify-items: end; align-content: start; gap: 5px; }.recentMeta em { padding: 2px 5px; border-radius: 3px; font-size: 10px; font-style: normal; font-weight: 800; }.recentMeta em.primary { background: rgba(231,181,43,.15); color: #f3c64b; }.recentMeta em.assistant { background: rgba(103,204,225,.15); color: #8be4f3; }.recentMeta b { color: rgba(255,255,255,.7); font-size: 10px; white-space: nowrap; }.detailEmpty { min-height: 480px; }
.dapFocus { display: grid; grid-template-columns: auto minmax(0,1fr) auto; align-items: center; gap: 10px; margin: 0 16px 14px; padding: 11px; border: 1px solid rgba(231,181,43,.25); background: rgba(231,181,43,.07); }.dapFocus span { color: rgba(255,255,255,.53); font-size: 10px; font-weight: 800; }.dapFocus b { overflow: hidden; color: rgba(255,255,255,.88); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.dapFocus i { color: rgba(255,255,255,.38); font-style: normal; }.dapFocus strong { color: #f3c64b; font-size: 12px; white-space: nowrap; }
@media (max-width: 980px) { .page { padding: 18px; }.topBar { align-items: flex-start; flex-direction: column; }.filters { grid-template-columns: repeat(2,minmax(0,1fr)); }.filters .search { grid-column: 1 / -1; }.summaryGrid { grid-template-columns: repeat(2,1fr); }.workspace { grid-template-columns: 1fr; }.detailPanel { order: -1; } }
</style>
