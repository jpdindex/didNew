<script setup lang="ts">
type Grade = 'high' | 'medium' | 'low'
type Category = 'artistry' | 'defense' | 'trajectory'
type Grades = Record<Category, Grade>
type Metric = 'distance' | 'speed'
type RankingSortKey = Metric | 'finalSpeed' | Category | 'score'
type Point = { x: number; y: number; z: number }
type League = { id: string; name: string }
type Season = { id: string; leagueId: string; name: string; from: string | null; to: string | null }
type Round = { id: string; latestDate: string; earliestDate: string }
type Team = { name: string; aliases: string[] }
type Analysis = {
  shotId: string | null; myGrades: Grades | null; evaluationCount: number
  evaluators: { id: string; name: string }[]
  score: number | null; categoryScores: Partial<Record<Category, number>>; autoGrades: Partial<Record<Metric, Grade>>
  metrics: Partial<Record<string, number | null>>
}
type Goal = Analysis & {
  gmId: string; side: 'H' | 'A'; recordId: string; teamId: string; homeTeamId: string; awayTeamId: string
  date: string; half: string; clock: string; playerName: string | null; seasonId: string; round: string
  manual?: boolean
}
type Goals = { goals: Goal[]; points: Record<Grade, number>; maxScore: number; teams: Record<string, Team> }
type Candidate = { id: string; title: string; home: string | null; away: string | null; clock: string | null }
type Shot = {
  id: string; title: string; metrics: Record<string, number | null>; trajectory: Point[]; measuredTrajectory: Point[]
  goalPoint: Point | null; goalX: number | null; goalCenterY: number | null; videoUrl: string | null
  scoreboard: { home: string | null; away: string | null; clock: string | null }
}

const api = '/api/v1/goalx'
const supportedLeagueIds = new Set(['EPL', 'LALIGA', 'TESTGOAL'])
const { request } = useBackendApi()
const grades: Grade[] = ['high', 'medium', 'low']
const labels = { high: '상', medium: '중', low: '하' }
const categories: { key: Category; label: string }[] = [
  { key: 'artistry', label: '예술성' }, { key: 'defense', label: '수비 환경' }, { key: 'trajectory', label: '궤적' },
]
const rankingColumns: { label: string; sortKey?: RankingSortKey; numeric?: boolean }[] = [
  { label: '순위' }, { label: '득점자' }, { label: '경기' }, { label: '득점 시각' },
  { label: '거리', sortKey: 'distance' }, { label: '초기 속도', sortKey: 'speed' },
  { label: '종료 속도', sortKey: 'finalSpeed' }, { label: '평가 인원' },
  ...categories.map(({ label, key }) => ({ label, sortKey: key, numeric: true })),
  { label: '평균 점수', sortKey: 'score', numeric: true },
]
const metrics = [
  { key: 'distance', label: '슈팅 거리', unit: 'm' }, { key: 'speed', label: '초기 속도', unit: 'km/h' },
  { key: 'launchAngle', label: '발사각', unit: '°' }, { key: 'spin', label: '회전수', unit: 'rpm' },
  { key: 'timeToGoal', label: '골라인 도달 시간', unit: '초' }, { key: 'flightTime', label: '비행시간', unit: '초' },
  { key: 'finalSpeed', label: '궤적 종료 속도', unit: 'km/h' },
]
const roundLabels: Record<string, string> = { R32: '32강', R16: '16강', QF: '8강', SF: '준결승', F: '결승', unassigned: '라운드 미지정' }
const knockoutStages = ['F', 'SF', 'QF', 'R16', 'R32']
const cardsPerPage = 5

const tab = ref<'analysis' | 'ranking'>('analysis')
const leagues = ref<League[]>([])
const seasons = ref<Season[]>([])
const fixtureSeasons = ref<Record<string, string[]>>({})
const rounds = ref<Round[]>([])
const league = ref('')
const season = ref('')
const round = ref('')
const team = ref('')
const result = ref<Goals | null>(null)
const isTestLeague = computed(() => league.value === 'TESTGOAL')
const testEditor = ref<{ id?: string; clock: string; playerName: string; home: string; away: string; scoringSide: 'H' | 'A'; date: string; shotId: string | null }>()
const exporting = ref(false)
const exportError = ref('')
const rankingSeason = ref('')
const rankingRound = ref('all')
const rankingTeam = ref('')
const rankingRounds = ref<Round[]>([])
const rankingResult = ref<Goals | null>(null)
const rankingSort = ref<{ key: RankingSortKey; direction: 'ascending' | 'descending' }>({ key: 'score', direction: 'descending' })
const rankingError = ref('')
const rankingRoundsError = ref('')
const loadingRanking = ref(false)
const loadingRankingRounds = ref(false)
const participantsDialog = ref<HTMLDialogElement>()
const participantsGoal = ref<Goal>()
const leagueSeasons = computed(() => {
  const registered = seasons.value.filter(item => item.leagueId === league.value)
  const knownIds = new Set(registered.map(item => item.id))
  const fixtureIds = fixtureSeasons.value[league.value] ?? []
  return [
    ...registered.filter(item => {
      const yearAlias = /_(\d{4})-(\d{4})$/.exec(item.id)
      return !yearAlias || !knownIds.has(yearAlias[1] + yearAlias[2]) || fixtureIds.includes(item.id)
    }),
    ...fixtureIds.filter(id => !knownIds.has(id)).map(id => ({
      id, leagueId: league.value, name: id.replace(/^(\d{4})(\d{4})$/, '$1–$2'), from: null, to: null,
    })),
  ].sort((a, b) => seasonStart(b).localeCompare(seasonStart(a)) || b.id.localeCompare(a.id))
})
const availableTeams = computed(() => Object.keys(result.value?.teams ?? {}).sort((a, b) => teamName(a).localeCompare(teamName(b), 'ko')))
const rankingTeams = computed(() => [...new Set(rankingResult.value?.goals.map(goal => goal.teamId))]
  .sort((a, b) => rankingTeamName(a).localeCompare(rankingTeamName(b), 'ko')))
const goals = computed(() => (result.value?.goals ?? []).filter(goal => !team.value || goal.teamId === team.value))
const completedGoals = computed(() => goals.value.filter(goal => goal.evaluationCount > 0))
const ranking = computed(() => {
  const rows = (rankingResult.value?.goals ?? [])
    .filter(goal => goal.score !== null && (!rankingTeam.value || goal.teamId === rankingTeam.value))
    .sort((a, b) => b.score! - a.score!)
  let rank = 0
  const ranked = rows.map((goal, index) => {
    if (index === 0 || goal.score !== rows[index - 1]?.score) rank = index + 1
    return { ...goal, rank }
  })
  const { key, direction } = rankingSort.value
  return ranked.sort((a, b) => {
    const left = rankingValue(a, key)
    const right = rankingValue(b, key)
    if (left == null) return right == null ? 0 : 1
    if (right == null) return -1
    return direction === 'ascending' ? left - right : right - left
  })
})

function rankingValue(goal: Goal, key: RankingSortKey) {
  if (key === 'score') return goal.score
  if (key === 'distance' || key === 'speed' || key === 'finalSpeed') return goal.metrics[key]
  return goal.categoryScores?.[key]
}

function setRankingSort(key: RankingSortKey) {
  const direction = rankingSort.value.key === key && rankingSort.value.direction === 'descending' ? 'ascending' : 'descending'
  rankingSort.value = { key, direction }
}

const selectedKey = ref('')
const selected = computed(() => goals.value.find(goal => keyOf(goal) === selectedKey.value))
const pages = computed(() => {
  const slots: (Goal | null)[] = [...goals.value]
  if (isTestLeague.value) {
    const capacity = Math.ceil((slots.length + 1) / cardsPerPage) * cardsPerPage
    while (slots.length < capacity) slots.push(null)
  }
  const chunks: (Goal | null)[][] = []
  for (let index = 0; index < slots.length; index += cardsPerPage) chunks.push(slots.slice(index, index + cardsPerPage))
  return chunks
})
const rail = ref<HTMLElement>()
const cardPage = ref(0)
const draft = ref<Partial<Grades>>({})
const editing = ref(false)
const loadError = ref('')
const detailError = ref('')
const searchError = ref('')
const actionError = ref('')
const notice = ref('')
const loadingCatalog = ref(false)
const loadingRounds = ref(false)
const loadingGoals = ref(false)
const loadingShot = ref(false)
const loadingCandidates = ref(false)
const saving = ref(false)
const linking = ref(false)
const shot = ref<Shot | null>(null)
const showShot = ref(false)
const search = ref('')
const candidates = ref<Candidate[] | null>(null)
const candidateId = ref('')
const shotCache = new Map<string, { shot: Shot; expires: number }>()
const seasonLookups = new Set<string>()
let roundRequest: AbortController | undefined
let listRequest: AbortController | undefined
let shotRequest: AbortController | undefined
let candidateRequest: AbortController | undefined
let rankingRequest: AbortController | undefined
let rankingRoundsRequest: AbortController | undefined

function teamName(id: string) { return result.value?.teams[id]?.name || id }
function rankingTeamName(id: string) { return rankingResult.value?.teams[id]?.name || id }
function roundLabel(id: string) { return isTestLeague.value && id === 'unassigned' ? '테스트 목록' : roundLabels[id] || id + '라운드' }
function seasonLabel(id: string) { return leagueSeasons.value.find(item => item.id === id)?.name || id.replace(/^(\d{4})(\d{4})$/, '$1–$2') }
function rankingMatch(goal: Goal) { return rankingTeamName(goal.homeTeamId) + ' vs ' + rankingTeamName(goal.awayTeamId) }
function keyOf(goal: Goal) { return JSON.stringify([goal.gmId, goal.side, goal.recordId]) }
function pathOf(goal: Goal) {
  return api + '/goals/' + [goal.gmId, goal.side, goal.recordId].map(encodeURIComponent).join('/')
}
function dateLabel(date: string) { return date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1.$2.$3') }
function dateKey(date?: string | null) { return (date ?? '').replace(/[^0-9]/g, '') }
function seasonStart(item: Season) {
  const year = /(?:19|20)\d{2}/.exec(item.id)?.[0] ?? /(?:19|20)\d{2}/.exec(item.name)?.[0]
  return dateKey(item.from) || (year ? year + '0101' : '')
}
function compareRounds(a: Round, b: Round) {
  const stageA = knockoutStages.indexOf(a.id)
  const stageB = knockoutStages.indexOf(b.id)
  if (stageA >= 0 || stageB >= 0) {
    return (stageA < 0 ? knockoutStages.length : stageA) - (stageB < 0 ? knockoutStages.length : stageB)
  }
  return (Number(b.id) || 0) - (Number(a.id) || 0) || a.id.localeCompare(b.id)
}
function errorMessage(error: unknown) { return error instanceof Error ? error.message : '요청을 처리하지 못했습니다.' }
function formatMetric(value: number | null | undefined, unit: string) {
  return value == null ? '—' : value.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) + ' ' + unit
}
function normalize(value: string) { return value.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '') }

function openTestEditor(goal?: Goal) {
  testEditor.value = {
    id: goal?.gmId, clock: goal?.clock ?? '', playerName: goal?.playerName ?? '',
    home: goal ? teamName(goal.homeTeamId) : '', away: goal ? teamName(goal.awayTeamId) : '',
    scoringSide: goal && goal.teamId === goal.awayTeamId ? 'A' : 'H',
    date: goal?.date ?? '', shotId: goal?.shotId ?? null,
  }
}

async function afterTestGoalSave(id: string) {
  testEditor.value = undefined
  team.value = ''
  await loadGoals(true)
  const index = goals.value.findIndex(goal => goal.gmId === id)
  if (index < 0) return
  selectGoal(goals.value[index])
  await nextTick()
  scrollCards(Math.floor(index / cardsPerPage), 'instant')
}

async function downloadCompletedGoals() {
  if (exporting.value || !completedGoals.value.length) return
  exporting.value = true
  exportError.value = ''
  const leagueLabel = leagues.value.find(item => item.id === league.value)?.name || league.value
  const seasonName = seasonLabel(season.value)
  const filename = 'GOALX-' + [league.value, season.value, round.value].join('-').replace(/[^\w-]/g, '_') + '.xlsx'
  const headers = ['대회', '시즌', '라운드', '경기 날짜', '득점 시각', '득점자', '득점 팀', '홈팀', '원정팀', '평가 인원',
    ...categories.map(category => category.label + ' 평균'), '평균 총점', '분석관 이름', '분석관 ID', 'ShotX ID',
    ...metrics.map(metric => metric.label + ' (' + metric.unit + ')')]
  const rows = completedGoals.value.map(goal => [
    leagueLabel, seasonName, roundLabel(goal.round), dateLabel(goal.date), goal.clock,
    goal.playerName, teamName(goal.teamId), teamName(goal.homeTeamId), teamName(goal.awayTeamId),
    goal.evaluationCount, ...categories.map(category => goal.categoryScores?.[category.key] ?? null), goal.score,
    goal.evaluators.map(analyst => analyst.name).join(', '), goal.evaluators.map(analyst => analyst.id).join(', '),
    goal.shotId, ...metrics.map(metric => goal.metrics[metric.key] ?? null),
  ])
  try {
    const XLSX = await import('xlsx')
    const sheet = XLSX.utils.aoa_to_sheet([headers, ...rows])
    sheet['!cols'] = headers.map((_, i) => ({ wch: i < 5 ? 15 : 22 }))
    const workbook = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(workbook, sheet, '분석 완료')
    XLSX.writeFile(workbook, filename)
  } catch (error) {
    exportError.value = errorMessage(error)
  } finally {
    exporting.value = false
  }
}

const scoreboardDiffers = computed(() => {
  const goal = selected.value
  const board = shot.value?.scoreboard
  if (!goal || !board?.home || !board.away) return false
  const aliases = (id: string) => (result.value?.teams[id]?.aliases ?? [id]).map(normalize)
  const home = aliases(goal.homeTeamId)
  const away = aliases(goal.awayTeamId)
  const first = normalize(board.home)
  const second = normalize(board.away)
  if (!first || !second) return false
  return !(home.includes(first) && away.includes(second)) && !(home.includes(second) && away.includes(first))
})

function scrollCards(page: number, behavior: ScrollBehavior = 'smooth') {
  const track = rail.value
  if (!track) return
  const next = Math.max(0, Math.min(page, pages.value.length - 1))
  cardPage.value = next
  track.scrollTo({ left: next * track.clientWidth, behavior })
}
function syncCardPage() {
  const track = rail.value
  if (track?.clientWidth) cardPage.value = Math.round(track.scrollLeft / track.clientWidth)
}
async function showGoalCard(goal: Goal) {
  tab.value = 'analysis'
  if (goal.seasonId !== season.value) {
    season.value = goal.seasonId
    if (!leagueSeasons.value.some(item => item.id === goal.seasonId)) void discoverSeasons()
    await loadRounds(goal.round)
  } else if (goal.round !== round.value || !result.value) {
    round.value = goal.round
    await loadGoals()
  }
  if (tab.value !== 'analysis' || season.value !== goal.seasonId || round.value !== goal.round) return
  if (team.value && team.value !== goal.teamId) team.value = ''
  const current = goals.value.find(item => keyOf(item) === keyOf(goal))
  if (!current) return
  selectGoal(current)
  void nextTick(() => scrollCards(Math.floor(goals.value.findIndex(item => keyOf(item) === keyOf(goal)) / cardsPerPage), 'instant'))
}
function selectGoal(goal: Goal | undefined) {
  selectedKey.value = goal ? keyOf(goal) : ''
  draft.value = { ...goal?.myGrades }
  editing.value = !!goal && !goal.myGrades
  search.value = ''
  candidates.value = null
  candidateId.value = ''
  searchError.value = ''
  actionError.value = ''
  notice.value = ''
  candidateRequest?.abort()
  loadingCandidates.value = false
  showShot.value = false
  void loadShot()
}
function startEditing() {
  draft.value = { ...selected.value?.myGrades }
  notice.value = ''
  actionError.value = ''
  editing.value = true
}
function cancelEditing() {
  draft.value = { ...selected.value?.myGrades }
  actionError.value = ''
  editing.value = false
}

function toggleShot() {
  showShot.value = !showShot.value
  void loadShot(showShot.value ? selected.value : undefined)
}
async function loadShot(goal?: Goal) {
  shotRequest?.abort()
  shot.value = null
  detailError.value = ''
  loadingShot.value = false
  if (!goal?.shotId) return
  const key = keyOf(goal) + goal.shotId
  const cached = shotCache.get(key)
  if (cached && cached.expires > Date.now()) {
    shot.value = cached.shot
    return
  }
  const controller = new AbortController()
  shotRequest = controller
  loadingShot.value = true
  try {
    const data = await request<{ shot: Shot | null }>(pathOf(goal) + '/shotx', { signal: controller.signal })
    if (controller.signal.aborted) return
    if (data.shot) shotCache.set(key, { shot: data.shot, expires: Date.now() + 5 * 60_000 })
    shot.value = data.shot
  } catch (error) {
    if (!controller.signal.aborted) detailError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingShot.value = false
  }
}

function changeTeam() {
  selectGoal(goals.value[0])
  cardPage.value = 0
  void nextTick(() => scrollCards(0, 'instant'))
}

async function loadGoals(preserveSelection = false) {
  listRequest?.abort()
  const controller = new AbortController()
  listRequest = controller
  loadError.value = ''
  exportError.value = ''
  if (!league.value || !season.value || !round.value) return
  const params = new URLSearchParams({ league_id: league.value, season_id: season.value, round_id: round.value })
  loadingGoals.value = true
  if (!preserveSelection) {
    result.value = null
    team.value = ''
    selectGoal(undefined)
    cardPage.value = 0
  }
  try {
    const next = await request<Goals>(api + '/goals?' + params.toString(), { signal: controller.signal })
    if (controller.signal.aborted) return
    const previous = selected.value
    result.value = next
    if (team.value && !next.teams[team.value]) team.value = ''
    if (!selected.value || previous?.shotId !== selected.value.shotId) selectGoal(selected.value ?? goals.value[0])
    else if (!editing.value) draft.value = { ...selected.value.myGrades }
  } catch (error) {
    if (!controller.signal.aborted) loadError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingGoals.value = false
  }
}

async function discoverSeasons() {
  const id = league.value
  if (!id || fixtureSeasons.value[id] || seasonLookups.has(id)) return
  seasonLookups.add(id)
  try {
    const data = await request<{ ids: string[] }>(api + '/fixture-seasons?' + new URLSearchParams({ league_id: id }))
    fixtureSeasons.value[id] = data.ids
    if (league.value === id && !season.value && leagueSeasons.value.length) {
      season.value = leagueSeasons.value[0]!.id
      void loadRounds()
    }
  } catch (error) {
    if (league.value === id && !season.value) loadError.value = errorMessage(error)
  } finally {
    seasonLookups.delete(id)
  }
}

function changeLeague() {
  const today = dateKey(new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(new Date()))
  const options = leagueSeasons.value
  season.value = (options.find(item => dateKey(item.from) <= today && dateKey(item.to) >= today)
    ?? options.find(item => seasonStart(item) && seasonStart(item) <= today) ?? options[0])?.id ?? ''
  rankingSeason.value = ''
  rankingTeam.value = ''
  rankingResult.value = null
  rankingRequest?.abort()
  rankingRoundsRequest?.abort()
  void loadRounds()
  if (tab.value === 'ranking') changeTab('ranking')
  if (!season.value) void discoverSeasons()
}

async function loadRounds(preferredRound?: string) {
  roundRequest?.abort()
  listRequest?.abort()
  loadingGoals.value = false
  result.value = null
  selectGoal(undefined)
  rounds.value = []
  round.value = ''
  team.value = ''
  loadError.value = ''
  loadingRounds.value = false
  if (!league.value || !season.value) return
  const controller = new AbortController()
  roundRequest = controller
  loadingRounds.value = true
  const params = new URLSearchParams({ league_id: league.value, season_id: season.value })
  try {
    const data = await request<{ rounds: Round[] }>(api + '/rounds?' + params.toString(), { signal: controller.signal })
    if (controller.signal.aborted) return
    rounds.value = [...data.rounds].sort(compareRounds)
    const today = dateKey(new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(new Date()))
    round.value = (data.rounds.find(item => item.id === preferredRound)
      ?? data.rounds.find(item => item.earliestDate && item.earliestDate <= today) ?? data.rounds.at(-1))?.id ?? ''
    if (round.value && tab.value === 'analysis') await loadGoals()
  } catch (error) {
    if (!controller.signal.aborted) loadError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingRounds.value = false
  }
}

function changeTab(next: 'analysis' | 'ranking') {
  const firstRankingLoad = !rankingSeason.value
  if (next === tab.value && (next === 'analysis' || !firstRankingLoad)) return
  tab.value = next
  if (next === 'analysis') {
    if (round.value && !result.value && !loadingGoals.value) void loadGoals()
    return
  }
  if (firstRankingLoad) {
    rankingSeason.value = season.value || 'all'
    changeRankingSeason()
  } else {
    void loadRanking()
  }
}

function changeRankingSeason() {
  rankingRound.value = 'all'
  void loadRankingRounds()
  void loadRanking()
}

async function loadRankingRounds() {
  rankingRoundsRequest?.abort()
  rankingRounds.value = []
  rankingRoundsError.value = ''
  loadingRankingRounds.value = false
  if (!league.value || rankingSeason.value === 'all') return
  if (rankingSeason.value === season.value && rounds.value.length) {
    rankingRounds.value = rounds.value
    return
  }
  const controller = new AbortController()
  rankingRoundsRequest = controller
  loadingRankingRounds.value = true
  try {
    const params = new URLSearchParams({ league_id: league.value, season_id: rankingSeason.value })
    const data = await request<{ rounds: Round[] }>(api + '/rounds?' + params, { signal: controller.signal })
    if (!controller.signal.aborted) rankingRounds.value = data.rounds.sort(compareRounds)
  } catch (error) {
    if (!controller.signal.aborted) rankingRoundsError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingRankingRounds.value = false
  }
}

async function loadRanking() {
  rankingRequest?.abort()
  rankingError.value = ''
  rankingResult.value = null
  loadingRanking.value = false
  if (!league.value || !rankingSeason.value) return
  const controller = new AbortController()
  rankingRequest = controller
  loadingRanking.value = true
  try {
    const params = new URLSearchParams({ league_id: league.value, season_id: rankingSeason.value, round_id: rankingRound.value })
    const data = await request<Goals>(api + '/ranking?' + params, { signal: controller.signal })
    if (controller.signal.aborted) return
    rankingResult.value = data
    if (!rankingTeams.value.includes(rankingTeam.value)) rankingTeam.value = ''
  } catch (error) {
    if (!controller.signal.aborted) rankingError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingRanking.value = false
  }
}

async function showParticipants(goal: Goal) {
  participantsGoal.value = goal
  await nextTick()
  participantsDialog.value?.showModal()
}

async function loadCatalog() {
  loadingCatalog.value = true
  loadError.value = ''
  try {
    const data = await request<{ leagues: League[]; seasons: Season[] }>(api + '/catalog')
    leagues.value = data.leagues
      .filter(item => supportedLeagueIds.has(item.id))
      .sort((a, b) => a.name.localeCompare(b.name, 'ko'))
    seasons.value = data.seasons
    league.value = (leagues.value.find(item => item.id === 'EPL') ?? leagues.value[0])?.id ?? ''
    changeLeague()
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loadingCatalog.value = false
  }
}

async function findCandidates() {
  const goal = selected.value
  if (!goal) return
  candidateRequest?.abort()
  const controller = new AbortController()
  candidateRequest = controller
  loadingCandidates.value = true
  searchError.value = ''
  candidates.value = null
  candidateId.value = ''
  try {
    const data = await request<{ candidates: Candidate[] }>(
      pathOf(goal) + '/candidates?' + new URLSearchParams({ search: search.value }).toString(), { signal: controller.signal },
    )
    if (!controller.signal.aborted) candidates.value = data.candidates
  } catch (error) {
    if (!controller.signal.aborted) searchError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingCandidates.value = false
  }
}

function patchGoal(goal: Goal, analysis: Analysis) {
  const current = result.value?.goals.find(item => keyOf(item) === keyOf(goal))
  if (current) Object.assign(current, analysis)
}

async function saveGrades() {
  const goal = selected.value
  if (!goal || !editing.value || saving.value || linking.value || categories.some(({ key }) => !draft.value[key])) return
  saving.value = true
  actionError.value = ''
  notice.value = ''
  try {
    const { analysis } = await request<{ analysis: Analysis }>(pathOf(goal) + '/evaluation', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft.value),
    })
    patchGoal(goal, analysis)
    if (selectedKey.value === keyOf(goal)) {
      draft.value = { ...analysis.myGrades }
      editing.value = false
      notice.value = '내 평가를 저장했습니다.'
    }
  } catch (error) {
    if (selectedKey.value === keyOf(goal)) actionError.value = errorMessage(error)
  } finally {
    saving.value = false
  }
}

async function updateLink(id: string | null) {
  const goal = selected.value
  if (!goal || linking.value || saving.value) return
  linking.value = true
  actionError.value = ''
  notice.value = ''
  try {
    const { analysis } = await request<{ analysis: Analysis }>(pathOf(goal) + '/shotx', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shotId: id }),
    })
    shotCache.delete(keyOf(goal) + goal.shotId)
    shotCache.delete(keyOf(goal) + analysis.shotId)
    patchGoal(goal, analysis)
    if (selectedKey.value === keyOf(goal)) {
      notice.value = id ? 'ShotX 결과를 연결했습니다.' : 'ShotX 연결을 해제했습니다.'
      if (showShot.value) void loadShot({ ...goal, ...analysis })
    }
  } catch (error) {
    if (selectedKey.value === keyOf(goal)) actionError.value = errorMessage(error)
  } finally {
    linking.value = false
  }
}

onMounted(loadCatalog)
onBeforeUnmount(() => {
  roundRequest?.abort()
  listRequest?.abort()
  shotRequest?.abort()
  candidateRequest?.abort()
  rankingRequest?.abort()
  rankingRoundsRequest?.abort()
})
</script>

<template>
  <div class="goalx-page" role="region" aria-labelledby="goalx-title" tabindex="0">
    <header class="masthead">
      <div>
        <NuxtLink class="back" to="/manage">← 데이터 관리</NuxtLink>
        <h1 id="goalx-title">GOALX</h1>
      </div>
      <nav class="tabs" aria-label="GOALX 메뉴">
        <button type="button" :aria-pressed="tab === 'analysis'" @click="changeTab('analysis')">분석</button>
        <button type="button" :aria-pressed="tab === 'ranking'" @click="changeTab('ranking')">랭킹</button>
      </nav>
    </header>

    <div class="filters">
      <label>대회
        <select v-model="league" :disabled="loadingCatalog || !leagues.length" @change="changeLeague">
          <option v-if="!leagues.length" value="">대회 선택</option>
          <option v-for="item in leagues" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select>
      </label>
      <label v-if="tab === 'analysis'">시즌
        <select v-model="season" :disabled="!leagueSeasons.length" @focus="discoverSeasons" @change="loadRounds()">
          <option v-if="!leagueSeasons.length" value="">등록된 시즌 없음</option>
          <option v-for="item in leagueSeasons" :key="item.id" :value="item.id">{{ item.name }}</option>
        </select>
      </label>
      <label v-if="tab === 'analysis'">라운드
        <select v-model="round" :disabled="loadingRounds || !rounds.length" @change="loadGoals()">
          <option v-if="!rounds.length" value="">라운드 선택</option>
          <option v-for="item in rounds" :key="item.id" :value="item.id">{{ roundLabel(item.id) }}</option>
        </select>
      </label>
      <label v-if="tab === 'analysis'">득점 팀
        <select v-model="team" :disabled="!availableTeams.length" @change="changeTeam">
          <option value="">전체 팀</option>
          <option v-for="id in availableTeams" :key="id" :value="id">{{ teamName(id) }}</option>
        </select>
      </label>
      <button v-if="tab === 'analysis'" class="quiet" type="button" :disabled="loadingCatalog || loadingRounds || loadingGoals || saving || linking" @click="round ? loadGoals(true) : league ? loadRounds() : loadCatalog()">새로고침</button>
      <template v-else>
        <label>시즌
          <select v-model="rankingSeason" :disabled="!league" @focus="discoverSeasons" @change="changeRankingSeason">
            <option value="all">시즌 전체</option>
            <option v-for="item in leagueSeasons" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>라운드
          <select v-model="rankingRound" :disabled="loadingRankingRounds || !rankingRounds.length" @change="loadRanking">
            <option value="all">전체 라운드 (누적)</option>
            <option v-for="item in rankingRounds" :key="item.id" :value="item.id">{{ roundLabel(item.id) }}</option>
          </select>
        </label>
        <label>득점 팀
          <select v-model="rankingTeam" :disabled="!rankingTeams.length">
            <option value="">전체 팀</option>
            <option v-for="id in rankingTeams" :key="id" :value="id">{{ rankingTeamName(id) }}</option>
          </select>
        </label>
        <button class="quiet" type="button" :disabled="loadingCatalog || loadingRanking" @click="league ? loadRanking() : loadCatalog()">새로고침</button>
      </template>
    </div>

    <template v-if="tab === 'analysis'">
      <p v-if="loadError" class="error message" role="alert">{{ loadError }}</p>
      <div v-if="loadingCatalog || loadingRounds || (loadingGoals && !result)" class="empty" role="status">득점 기록을 불러오는 중…</div>
      <div v-else-if="!loadError && !goals.length && !isTestLeague" class="empty">
        {{ !season ? '이 대회에 등록된 시즌이 없습니다.' : !round ? '이 시즌에 등록된 경기 기록이 없습니다.' : '선택한 범위에 득점 기록이 없습니다.' }}
      </div>
    </template>

    <section v-show="tab === 'analysis'" v-if="goals.length || (isTestLeague && result)" class="analysis">
      <div class="rail-header">
        <h2>득점 목록 <span>{{ goals.length }}</span></h2>
        <div class="rail-controls">
          <button class="quiet export-button" type="button" :disabled="!completedGoals.length || exporting" @click="downloadCompletedGoals">
            {{ exporting ? '파일 만드는 중…' : '분석 완료 값 내려받기' }}
          </button>
          <span v-if="pages.length > 1 && isTestLeague">{{ cardPage + 1 }} / {{ pages.length }}</span>
          <span v-else-if="pages.length > 1">{{ cardPage * cardsPerPage + 1 }}–{{ Math.min((cardPage + 1) * cardsPerPage, goals.length) }} / {{ goals.length }}</span>
          <button type="button" aria-label="이전 득점 카드" :disabled="cardPage === 0" @click="scrollCards(cardPage - 1)">‹</button>
          <button type="button" aria-label="다음 득점 카드" :disabled="cardPage >= pages.length - 1" @click="scrollCards(cardPage + 1)">›</button>
        </div>
      </div>
      <p v-if="exportError" class="error message" role="alert">{{ exportError }}</p>
      <div ref="rail" class="card-rail" aria-label="득점 카드" @scroll.passive="syncCardPage">
        <div v-for="(page, index) in pages" :key="index" class="card-page">
          <template v-for="(goal, slot) in page" :key="goal ? keyOf(goal) : 'empty-' + slot">
            <button v-if="goal" type="button" class="goal-card" :class="{ completed: goal.myGrades }"
              :aria-pressed="selectedKey === keyOf(goal)" @click="selectedKey !== keyOf(goal) && selectGoal(goal)">
              <span class="card-top"><span class="clock">{{ goal.clock }}</span><span class="state">{{ goal.myGrades ? '평가 완료' : '평가 전' }}</span></span>
              <strong>{{ goal.playerName || '득점자 미기록' }}</strong>
              <span class="card-team">{{ teamName(goal.teamId) }} <span>·</span> {{ dateLabel(goal.date) }}</span>
            </button>
            <button v-else type="button" class="goal-card empty-card" aria-label="TESTGOAL 득점 추가" @click="openTestEditor()">＋</button>
          </template>
        </div>
      </div>

      <article v-if="selected" :key="selectedKey" class="detail">
        <header class="detail-header">
          <div>
            <p>{{ teamName(selected.homeTeamId) }} <span class="versus">vs</span> {{ teamName(selected.awayTeamId) }} · {{ dateLabel(selected.date) }}</p>
            <h2>{{ selected.playerName || '득점자 미기록' }} <span class="clock">{{ selected.clock }}</span></h2>
          </div>
          <div class="goal-actions">
            <button v-if="selected.manual" class="quiet" type="button" :disabled="saving || linking" @click="openTestEditor(selected)">득점 정보 수정</button>
            <button class="quiet shot-toggle" type="button" :aria-expanded="showShot" aria-controls="goalx-shotx" @click="toggleShot">
              {{ showShot ? 'ShotX 분석 접기' : 'ShotX 분석 보기' }}
            </button>
          </div>
        </header>

        <div class="review-grid" :class="{ 'with-shot': showShot }">
          <form class="grading" @submit.prevent="saveGrades">
            <div class="grading-heading">
              <h3>내 평가</h3>
              <button v-if="selected.myGrades && !editing" type="button" class="quiet" :disabled="saving || linking" @click="startEditing">수정하기</button>
            </div>
            <div class="rubric">
              <fieldset v-for="category in categories" :key="category.key" :disabled="saving || linking">
                <legend>{{ category.label }}</legend>
                <div v-if="editing" class="choices">
                  <label v-for="grade in grades" :key="grade" :class="{ chosen: draft[category.key] === grade }">
                    <input v-model="draft[category.key]" type="radio" :name="category.key" :value="grade" required>
                    <span>{{ labels[grade] }}</span><small>{{ result?.points[grade] }}점</small>
                  </label>
                </div>
                <div v-else-if="selected.myGrades" class="saved-grade">
                  <strong>{{ labels[selected.myGrades[category.key]] }}</strong>
                  <span>{{ result?.points[selected.myGrades[category.key]] }}점</span>
                </div>
              </fieldset>
            </div>
            <div v-if="editing" class="form-actions">
              <button type="submit" class="primary" :disabled="saving || linking || categories.some(({ key }) => !draft[key])">{{ saving ? '저장 중…' : '평가 저장' }}</button>
              <button v-if="selected.myGrades" type="button" class="quiet" :disabled="saving" @click="cancelEditing">취소</button>
            </div>
            <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
            <p v-if="notice" class="notice" role="status">{{ notice }}</p>
            <div v-if="selected.evaluators.length" class="evaluators">
              <h3>평가 분석관 <span>{{ selected.evaluationCount }}명</span></h3>
              <ul>
                <li v-for="analyst in selected.evaluators" :key="analyst.id">
                  <strong v-if="analyst.name !== analyst.id">{{ analyst.name }}</strong>
                  <span>{{ analyst.id }}</span>
                </li>
              </ul>
            </div>
          </form>

          <section v-if="showShot" id="goalx-shotx" class="shot-panel">
            <h3>ShotX 영상과 측정값</h3>
            <p v-if="loadingShot" role="status">ShotX 데이터를 불러오는 중…</p>
            <p v-if="detailError" class="error" role="alert">{{ detailError }}</p>
            <p v-if="scoreboardDiffers" class="mismatch" role="status">
              DID 경기와 ShotX 영상의 팀 표기가 다릅니다. 연결을 확인하세요.
              <span>{{ shot?.scoreboard.home }} vs {{ shot?.scoreboard.away }}</span>
            </p>
            <video v-if="shot?.videoUrl" :key="shot.videoUrl" :src="shot.videoUrl" controls playsinline preload="metadata" />
            <p v-else-if="!selected.shotId" class="muted">연결된 ShotX 분석이 없습니다.</p>
            <p v-else-if="shot" class="muted">재생할 수 있는 클립이 없습니다.</p>
            <div v-if="shot && (shot.trajectory.length >= 2 || shot.measuredTrajectory.length >= 2)" class="shot-trajectory">
              <LazyGoalxGoalTrajectory :points="shot.trajectory" :measured-points="shot.measuredTrajectory"
                :goal-point="shot.goalPoint" :goal-x="shot.goalX" :goal-center-y="shot.goalCenterY"
                :flight-time="shot.metrics.flightTime ?? null" />
              <div class="trajectory-key"><span v-if="shot.trajectory.length >= 2">복원 궤적</span><span v-if="shot.measuredTrajectory.length" class="measured-key">측정점</span><span v-if="shot.goalPoint" class="crossing-key">골라인 통과점</span></div>
            </div>
            <p v-else-if="shot && !loadingShot" class="muted">저장된 궤적 좌표가 없습니다.</p>
            <dl v-if="selected.shotId" class="measurements">
              <div v-for="metric in metrics.filter(item => ['distance', 'speed'].includes(item.key) || shot?.metrics[item.key] != null)" :key="metric.key">
                <dt>{{ metric.label }}</dt>
                <dd>{{ formatMetric(shot?.metrics[metric.key] ?? selected.metrics[metric.key as Metric], metric.unit) }}
                  <small v-if="selected.autoGrades[metric.key as Metric]">{{ labels[selected.autoGrades[metric.key as Metric]!] }}</small>
                </dd>
              </div>
            </dl>
            <details v-if="!selected.manual">
              <summary>{{ selected.shotId ? 'ShotX 연결 변경' : 'ShotX 결과 연결' }}</summary>
              <p v-if="shot" class="muted">현재 연결 · {{ shot.title }}</p>
              <form class="search" @submit.prevent="findCandidates">
                <input v-model.trim="search" maxlength="150" aria-label="ShotX 결과 검색" placeholder="팀, 경기 시각, 파일명으로 검색">
                <button type="submit" :disabled="loadingCandidates || linking">{{ loadingCandidates ? '검색 중…' : '후보 찾기' }}</button>
              </form>
              <p v-if="searchError" class="error" role="alert">{{ searchError }}</p>
              <p v-if="candidates && !candidates.length" class="muted">표시할 결과가 없습니다.</p>
              <div v-if="candidates?.length" class="candidate-list">
                <label v-for="candidate in candidates" :key="candidate.id">
                  <input v-model="candidateId" type="radio" name="shot-candidate" :value="candidate.id">
                  <span><strong>{{ candidate.title }}</strong><small>{{ [candidate.home, candidate.away, candidate.clock].filter(Boolean).join(' · ') || '경기 정보 없음' }}</small></span>
                </label>
              </div>
              <div class="form-actions">
                <button v-if="candidateId" type="button" :disabled="linking || saving" @click="updateLink(candidateId)">선택한 결과 연결</button>
                <button v-if="selected.shotId" type="button" :disabled="linking || saving" @click="updateLink(null)">연결 해제</button>
              </div>
            </details>
          </section>
        </div>
      </article>
    </section>

    <section v-if="tab === 'ranking'" class="detail ranking">
      <header class="detail-header"><h2>골 랭킹</h2><span v-if="rankingResult">{{ ranking.length }}골 · {{ rankingResult.maxScore }}점 만점</span></header>
      <p v-if="rankingRoundsError" class="error" role="alert">{{ rankingRoundsError }}</p>
      <p v-if="rankingError" class="error" role="alert">{{ rankingError }}</p>
      <p v-else-if="loadingRanking" class="muted" role="status">랭킹을 불러오는 중…</p>
      <p v-else-if="!ranking.length" class="empty">선택한 범위에 평가된 골이 없습니다.</p>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th v-for="column in rankingColumns" :key="column.label" scope="col" :class="{ 'ranking-score': column.numeric }"
                :aria-sort="column.sortKey === rankingSort.key ? rankingSort.direction : undefined">
                <button v-if="column.sortKey" type="button" class="ranking-sort" @click="setRankingSort(column.sortKey)">
                  {{ column.label }}
                  <span aria-hidden="true">{{ column.sortKey !== rankingSort.key ? '↕' : rankingSort.direction === 'ascending' ? '↑' : '↓' }}</span>
                </button>
                <template v-else>{{ column.label }}</template>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="goal in ranking" :key="keyOf(goal)">
              <td class="clock">{{ goal.rank }}</td>
              <td><button type="button" class="text-button" @click="showGoalCard(goal)">{{ goal.playerName || '득점자 미기록' }}</button></td>
              <td class="ranking-match">
                <div class="ranking-teams">
                  <template v-for="(teamId, index) in [goal.homeTeamId, goal.awayTeamId]" :key="index">
                    <span v-if="index" class="ranking-versus">vs</span>
                    <span class="ranking-team" :class="{ 'scoring-team': teamId === goal.teamId }">
                      <span v-if="teamId === goal.teamId" class="ranking-scoring-label">득점 팀 </span>{{ rankingTeamName(teamId) }}
                    </span>
                  </template>
                </div>
                <span class="ranking-round">{{ seasonLabel(goal.seasonId) }} · {{ roundLabel(goal.round) }}</span>
              </td>
              <td>{{ goal.clock }}</td>
              <td>{{ formatMetric(goal.metrics.distance, 'm') }}</td>
              <td>{{ formatMetric(goal.metrics.speed, 'km/h') }}</td>
              <td>{{ formatMetric(goal.metrics.finalSpeed, 'km/h') }}</td>
              <td><button type="button" class="text-button participants-button" aria-haspopup="dialog" @click="showParticipants(goal)">{{ goal.evaluationCount }}명</button></td>
              <td v-for="category in categories" :key="category.key" class="ranking-score">
                {{ goal.categoryScores?.[category.key]?.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) ?? '—' }}
              </td>
              <td class="ranking-score"><strong>{{ goal.score?.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) }}</strong></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <LazyGoalxTestGoalEditor v-if="testEditor" :initial="testEditor" @close="testEditor = undefined" @saved="afterTestGoalSave" />
    <dialog ref="participantsDialog" class="participants-dialog" aria-labelledby="participants-title" @close="participantsGoal = undefined">
      <template v-if="participantsGoal">
        <header class="detail-header">
          <div>
            <h2 id="participants-title">평가 참여자 {{ participantsGoal.evaluationCount }}명</h2>
            <p>{{ roundLabel(participantsGoal.round) }} · {{ rankingMatch(participantsGoal) }} · {{ participantsGoal.clock }}</p>
          </div>
          <form method="dialog"><button type="submit" class="quiet" autofocus>닫기</button></form>
        </header>
        <ul>
          <li v-for="analyst in participantsGoal.evaluators" :key="analyst.id">
            <strong v-if="analyst.name !== analyst.id">{{ analyst.name }}</strong>
            <span>{{ analyst.id }}</span>
          </li>
        </ul>
      </template>
    </dialog>
  </div>
</template>

<style scoped>
.goalx-page {
  --page: #0c141a;
  --surface: #141f26;
  --surface-hover: #192a31;
  --line: #293a43;
  --muted: #9bafb7;
  --text: #eef5f3;
  --accent: #a4e7c9;
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  overflow: auto;
  background: radial-gradient(ellipse at 77% -25%, #173b36 0, transparent 53%), var(--page);
  color: var(--text);
  padding: 26px 46px 42px;
}
.masthead, .detail-header, .rail-header, .grading-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.masthead { align-items: end; margin: 0 0 22px; }
.back { color: var(--muted); text-decoration: none; font-size: 13px; }
.back:hover { color: var(--accent); }
h1, h2, h3, p { margin: 0; }
h1 { margin-top: 7px; font-size: 38px; letter-spacing: -.058em; line-height: 1.16; }
h2 { font-size: 21px; letter-spacing: -.035em; }
h3 { font-size: 15px; letter-spacing: -.02em; }
button, select, input { font: inherit; }
button, select, input:not([type="radio"]) {
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 8px 12px;
}
button { cursor: pointer; }
button:hover:not(:disabled) { background: var(--surface-hover); border-color: #52746b; }
button:disabled { cursor: default; opacity: .46; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tabs { display: flex; gap: 3px; background: #0d191d; border: 1px solid var(--line); padding: 4px; border-radius: 12px; }
.tabs button { min-width: 77px; border-color: transparent; background: transparent; color: var(--muted); font-size: 14px; font-weight: 700; }
.tabs button[aria-pressed="true"] { color: #102720; background: var(--accent); }
.filters { display: grid; grid-template-columns: 1.12fr 1fr .8fr 1fr auto; align-items: end; gap: 10px; border-block: 1px solid var(--line); padding: 13px 0; margin-bottom: 20px; }
.filters label { display: grid; min-width: 0; gap: 5px; color: var(--muted); font-size: 12px; }
.filters select { min-width: 0; width: 100%; font-size: 14px; }
.quiet { background: transparent; font-size: 13px; }
.rail-header { margin-bottom: 8px; }
.rail-header h2 { font-size: 17px; }
.rail-header h2 span, .evaluators h3 span { margin-left: 5px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.rail-controls { display: flex; align-items: center; gap: 6px; }
.rail-controls > span { margin-right: 5px; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.rail-controls button { padding: 0; width: 32px; height: 30px; font-size: 22px; line-height: 1; background: transparent; }
.rail-controls .export-button { width: auto; height: auto; padding: 7px 10px; margin-right: 5px; font-size: 12px; line-height: 1.25; color: var(--accent); }
.card-rail { display: flex; overflow: auto hidden; scroll-snap-type: x mandatory; scrollbar-width: none; overscroll-behavior-inline: contain; }
.card-rail::-webkit-scrollbar { display: none; }
.card-page { min-width: 0; flex: 0 0 100%; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 9px; scroll-snap-align: start; }
.goal-card { min-width: 0; display: grid; gap: 7px; text-align: left; padding: 10px 12px; background: #152129; min-height: 92px; }
.goal-card:focus-visible { outline-offset: -3px; }
.goal-card.empty-card { place-items: center; border-style: dashed; color: var(--accent); background: #111e24; font-size: 28px; }
.goal-card[aria-pressed="true"] { border-color: #79b29f; background: #1b3532; box-shadow: inset 0 2px #9fe7c4; }
.goal-card.completed { color: #a8b6bb; background: #111b21; border-color: #29343b; }
.goal-card.completed[aria-pressed="true"] { background: #1c292f; border-color: #6b8387; box-shadow: inset 0 2px #839b9e; }
.goal-card strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 16px; }
.card-top { display: flex; align-items: center; justify-content: space-between; gap: 4px; }
.clock { color: var(--accent); font-weight: 750; font-variant-numeric: tabular-nums; }
.completed .clock { color: #9aafae; }
.state { border-radius: 5px; padding: 3px 5px; background: #25343c; color: #bfd1d8; font-size: 10px; white-space: nowrap; }
.completed .state { background: #222e32; color: #99adae; }
.card-team { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.card-team > span { color: #647d85; margin: 0 2px; }
.detail { margin-top: 20px; border: 1px solid var(--line); border-radius: 15px; background: var(--surface); overflow: hidden; }
.detail-header { padding: 15px 20px; border-bottom: 1px solid var(--line); }
.detail-header p, .detail-header > span, .muted { color: var(--muted); font-size: 13px; line-height: 1.5; }
.detail-header h2 { margin-top: 2px; }
.detail-header .clock { font-size: 14px; margin-left: 7px; }
.versus { color: #73938d; margin: 0 3px; }
.shot-toggle { color: var(--accent); border-color: #3e5a54; white-space: nowrap; }
.goal-actions { display: flex; flex-wrap: wrap; justify-content: end; gap: 8px; }
.review-grid { display: grid; gap: 18px; padding: 16px 20px 20px; }
.review-grid.with-shot { grid-template-columns: minmax(0, 1.03fr) minmax(0, 1fr); }
.grading { min-width: 0; }
.grading-heading { min-height: 31px; margin-bottom: 12px; }
.rubric { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.with-shot .rubric { grid-template-columns: 1fr; gap: 13px; }
fieldset { padding: 0; margin: 0; border: 0; min-width: 0; }
legend { color: var(--muted); padding: 0; margin-bottom: 7px; font-size: 13px; }
.choices { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; }
.choices label { position: relative; display: flex; flex-wrap: wrap; align-items: baseline; justify-content: center; gap: 5px; padding: 10px 4px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; }
.choices label:hover { border-color: #708e87; }
.choices label:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.choices input { position: absolute; opacity: 0; width: 1px; height: 1px; }
.choices .chosen { color: var(--accent); border-color: #79b29f; background: #243c38; }
.choices small, .saved-grade span { color: var(--muted); font-size: 11px; }
.saved-grade { display: flex; align-items: baseline; gap: 7px; border: 1px solid var(--line); border-radius: 8px; padding: 9px 11px; background: #17252b; }
.form-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 13px; }
.primary { background: var(--accent); color: #112b22; border-color: var(--accent); font-weight: 750; }
.primary:hover:not(:disabled) { background: #b5f5d8; color: #112b22; }
.evaluators { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 12px; }
.evaluators ul { display: flex; flex-wrap: wrap; gap: 6px; list-style: none; padding: 0; margin: 8px 0 0; }
.evaluators li { overflow-wrap: anywhere; display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px; background: #1a2a30; color: var(--muted); border-radius: 6px; padding: 5px 7px; font-size: 11px; }
.evaluators strong { color: var(--text); }
.shot-panel { min-width: 0; border-left: 1px solid var(--line); padding-left: 18px; }
.shot-panel h3 { margin-bottom: 9px; }
video { width: 100%; max-height: 250px; background: #071015; border-radius: 8px; display: block; }
.shot-trajectory { margin-top: 10px; }
.trajectory-key { display: flex; gap: 10px; color: var(--muted); font-size: 11px; margin-top: 5px; }
.trajectory-key span::before { content: ''; display: inline-block; background: #70f0bd; width: 6px; height: 6px; border-radius: 50%; margin-right: 4px; }
.trajectory-key .measured-key::before { background: #d6e4e8; }
.trajectory-key .crossing-key::before { background: #ffca77; }
.measurements { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin: 9px 0 12px; }
.measurements > div { border: 1px solid var(--line); padding: 7px; border-radius: 7px; }
.measurements dt { color: var(--muted); font-size: 11px; }
.measurements dd { margin: 3px 0 0; font-weight: 700; font-size: 14px; }
.measurements small, .notice { color: var(--accent); }
details { margin-top: 9px; border-top: 1px solid var(--line); padding-top: 9px; }
summary { cursor: pointer; width: fit-content; color: var(--accent); font-size: 13px; }
.search { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.search input { flex: 1; min-width: 110px; }
.candidate-list { max-height: 210px; overflow: auto; margin-top: 6px; }
.candidate-list label { display: flex; gap: 7px; padding: 7px 0; border-bottom: 1px solid var(--line); cursor: pointer; overflow-wrap: anywhere; }
.candidate-list small { display: block; color: var(--muted); }
.mismatch { margin-bottom: 9px; border-left: 2px solid #e4b471; background: #342d24; color: #f3d19d; padding: 7px 9px; font-size: 12px; }
.mismatch span { display: block; margin-top: 3px; }
.error, .notice { margin-top: 8px; font-size: 13px; }
.error { color: #ffadb2; }
.message { margin-bottom: 8px; }
.empty { padding: 26px 16px; border: 1px dashed var(--line); border-radius: 10px; color: var(--muted); }
.ranking > p { margin: 12px 20px; }
.table-wrap { overflow-x: auto; padding: 0 20px 15px; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-size: 12px; }
.text-button { background: none; border: 0; padding: 0; text-decoration: underline; text-underline-offset: 3px; }
.ranking-teams { display: flex; align-items: center; gap: 6px; }
.ranking-team { color: var(--muted); font-weight: 650; border-radius: 5px; padding: 3px 6px; }
.ranking-versus { color: var(--muted); font-size: 11px; }
.ranking-team.scoring-team { color: var(--accent); background: #203d35; box-shadow: inset 0 0 0 1px #406b59; }
.ranking-scoring-label { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.ranking-round { display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }
.ranking-score { text-align: right; font-variant-numeric: tabular-nums; }
.ranking-sort { display: inline-flex; gap: 4px; align-items: center; padding: 3px 0; border: 0; background: none; color: inherit; font: inherit; }
.ranking-sort:hover, th[aria-sort] .ranking-sort { color: var(--accent); }
.ranking-sort:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.participants-button { color: var(--accent); }
.participants-dialog { width: min(440px, calc(100vw - 32px)); max-height: 80vh; box-sizing: border-box; padding: 0; color: var(--text); background: var(--surface); border: 1px solid var(--line); border-radius: 14px; }
.participants-dialog::backdrop { background: #071014b8; }
.participants-dialog h2 { margin: 0; }
.participants-dialog ul { list-style: none; margin: 0; padding: 6px 20px 16px; }
.participants-dialog li { display: grid; gap: 3px; padding: 10px 0; overflow-wrap: anywhere; }
.participants-dialog li + li { border-top: 1px solid var(--line); }
.participants-dialog li span { color: var(--muted); font-size: 13px; }
@media (prefers-reduced-motion: reduce) { .card-rail { scroll-behavior: auto; } }
</style>
