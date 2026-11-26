<script setup lang="ts">
import { calculateGoalxGeometry, estimateGoalShot, validEstimatedCoordinates, goalxEstimationSheets, type CurveDirection, type ShotType, type EstimatedShot, type EstimatedShotInput, type GoalxExportRecord } from '~/utils/goalx'
type Grade = 'high' | 'medium' | 'low'
type Category = 'artistry' | 'defense' | 'trajectory'
type Grades = Record<Category, Grade>
type Metric = 'distance' | 'speed'
type RankingSortKey = 'distance' | 'averageSpeed' | 'flightTime' | 'analystScore' | Category | 'score'
type Point = { x: number; y: number; z: number }
type ShotContext = { shotType?: ShotType | null; playerHeightCm?: number | null; jumpHeightM?: number; startHeightM?: number | null }
type RawCoordinates = Partial<Record<'posX' | 'posY' | 'shootPosX' | 'shootPosY' | 'goalX', number | null>> & ShotContext
type League = { id: string; name: string }
type Season = { id: string; leagueId: string; name: string; from: string | null; to: string | null }
type Round = { id: string; latestDate: string; earliestDate: string }
type Team = { name: string; aliases: string[] }
type Analysis = {
  myCurveDirection: CurveDirection | null
  representative: { status: 'approved' | 'needs_review'; approvedBy: string; approvedAt: string } | null
  myEstimatedShot: EstimatedShot | null
  shotId: string | null; myGrades: Grades | null; evaluationCount: number
  evaluators: { id: string; name: string }[]
  score: number | null; analystScore: number | null; analystCategoryScores: Partial<Record<Category, number>>
  ratingMetrics: { distance: number | null; averageSpeed: number | null; flightTime: number | null }
  categoryScores: Partial<Record<Category, number>>; autoGrades: Partial<Record<Metric, Grade>>
  metrics: Partial<Record<string, number | null>>
}
type Goal = Analysis & {
  rawCoordinates?: RawCoordinates
  gmId: string; side: 'H' | 'A'; recordId: string; teamId: string; homeTeamId: string; awayTeamId: string
  date: string; half: string; clock: string; playerName: string | null; seasonId: string; round: string
  manual?: boolean
}
type Goals = { goals: Goal[]; points: Record<Grade, number>; maxScore: number; teams: Record<string, Team>
  ratingRules: { categoryWeights: Record<Category, number>; gradeFactors: Record<Grade, number> } }
type Candidate = { id: string; title: string; home: string | null; away: string | null; clock: string | null }
type Shot = {
  id: string; title: string; metrics: Record<string, number | null>; trajectory: Point[]; measuredTrajectory: Point[]
  goalPoint: Point | null; goalX: number | null; goalCenterY: number | null; videoUrl: string | null
  scoreboard: { home: string | null; away: string | null; clock: string | null }
}
type RepresentativeProposal = ShotContext & {
  sourceVersion: string; evaluationCount: number; categoryScores: Record<Category, number>; score: number | null
  trajectoryCounts: Partial<Record<Grade, number>>; trajectoryCandidates: Grade[]
  curveDirectionCounts: Record<'high' | 'medium', Partial<Record<CurveDirection, number>>>
  curveDirectionCandidates: Record<'high' | 'medium', CurveDirection[]>
  timeToGoal: number | null; timeSampleCount: number; coordinates: RawCoordinates; goalX: 0 | 105; directionGoalX: 0 | 105 | null; shotId: string | null
  coordinateSource?: 'evaluations' | 'raw'; coordinateSampleCount?: number
}

const api = '/api/v1/goalx'
const supportedLeagueIds = new Set(['EPL', 'LALIGA', 'TESTGOAL'])
const { request } = useBackendApi()
function categoryPoints(category: Category, grade: Grade) {
  const rules = result.value?.ratingRules
  return rules ? rules.categoryWeights[category] * rules.gradeFactors[grade] : '—'
}
const grades: Grade[] = ['high', 'medium', 'low']
const labels = { high: '상', medium: '중', low: '하' }
const trajectoryLabels = { high: '딥 커브', medium: '커브', low: '스트레이트' }
const curveDirectionLabels = { left: '좌', right: '우' }
const coordinateFields = [
  { key: 'posX', label: '슈팅 X (%)', min: 0, max: 100 },
  { key: 'posY', label: '슈팅 Y (%)', min: 0, max: 100 },
  { key: 'shootPosX', label: '골문 결과 X', min: 83, max: 464 },
  { key: 'shootPosY', label: '골문 결과 Y', min: 83, max: 300 },
] as const
const categories: { key: Category; label: string }[] = [
  { key: 'artistry', label: '예술성' }, { key: 'defense', label: '수비 환경' }, { key: 'trajectory', label: '궤적' },
]
const rankingColumns: { label: string; sortKey?: RankingSortKey; numeric?: boolean }[] = [
  { label: '순위' }, { label: '득점자' }, { label: '경기' }, { label: '득점 시각' },
  { label: '거리', sortKey: 'distance' }, { label: '시속 (km/h)', sortKey: 'averageSpeed' },
  { label: '평가 인원' },
  ...categories.map(({ label, key }) => ({ label, sortKey: key, numeric: true })),
  { label: '분석관 평가 점수', sortKey: 'analystScore', numeric: true },
  { label: '총점', sortKey: 'score', numeric: true },
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
const canApproveRepresentative = ref(false)
const bulkDialog = ref<HTMLDialogElement>()
const bulkTargets = ref<Goal[]>([])
const bulkApproving = ref(false)
const bulkProcessed = ref(0)
const bulkError = ref('')
const bulkSummary = ref<{ approved: number; skipped: number; failed: number }>()
const bulkIssues = ref<{ goal: Goal; reason: string }[]>([])
const representativeDialog = ref<HTMLDialogElement>()
const representativeGoal = ref<Goal>()
const representativeProposal = ref<RepresentativeProposal>()
const representativeTrajectory = ref<Grade>()
const representativeCurveDirection = ref<CurveDirection | null>(null)
const representativeDirectionValid = computed(() => representativeTrajectory.value === 'low' || representativeCurveDirection.value != null)
const representativeCoordinates = ref<Record<string, number | null | ''>>({})
const loadingRepresentative = ref(false)
const approvingRepresentative = ref(false)
const representativeError = ref('')
let representativeRequest: AbortController | undefined
const representativeCoordinatesValid = computed(() => validEstimatedCoordinates({ ...representativeCoordinates.value, goalX: representativeProposal.value?.goalX }))
const representativeHasCoordinates = computed(() => coordinateFields.some(({ key }) => representativeCoordinates.value[key] != null && representativeCoordinates.value[key] !== ''))
const representativeEstimate = computed(() => {
  const proposal = representativeProposal.value
  if (!proposal || (proposal.shotType === 'H' && proposal.playerHeightCm == null) || !representativeTrajectory.value || !representativeCoordinatesValid.value || !representativeDirectionValid.value) return null
  const goalX = proposal.directionGoalX ?? (Number(representativeCoordinates.value.posX) < 50 ? 0 : 105)
  return estimateGoalShot({ ...representativeCoordinates.value, shotType: proposal.shotType, playerHeightCm: proposal.playerHeightCm, goalX, timeToGoal: proposal.timeToGoal,
    curveDirection: representativeTrajectory.value === 'low' ? null : representativeCurveDirection.value } as EstimatedShotInput, representativeTrajectory.value)
})
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
    .filter(goal => goal.evaluationCount > 0 && (!rankingTeam.value || goal.teamId === rankingTeam.value))
    .sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity))
  let rank = 0
  const ranked = rows.map((goal, index) => {
    if (index === 0 || goal.score !== rows[index - 1]?.score) rank = index + 1
    return { ...goal, rank: goal.score == null ? null : rank }
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
  if (key === 'analystScore') return goal.analystScore
  if (key === 'distance' || key === 'averageSpeed' || key === 'flightTime') return goal.ratingMetrics?.[key]
  return goal.analystCategoryScores?.[key]
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
const curveDirection = ref<CurveDirection | null>(null)
const curveDirectionRequired = computed(() => draft.value.trajectory === 'high' || draft.value.trajectory === 'medium')
const curveDirectionValid = computed(() => !curveDirectionRequired.value || curveDirection.value != null)
const loadingRawCoordinates = ref(false)
const rawCoordinatesError = ref('')
let rawCoordinatesRequest: AbortController | undefined
const estimateDraft = ref<Record<string, number | null | ''>>({})
const shotContext = ref<ShotContext>({})
const headingHeightValid = computed(() => shotContext.value.shotType !== 'H' || (typeof shotContext.value.playerHeightCm === 'number' && shotContext.value.playerHeightCm >= 100 && shotContext.value.playerHeightCm <= 250))
const rawGoalX = ref<0 | 105 | null>(null)
const automaticGoalX = computed(() => rawGoalX.value ?? (typeof estimateDraft.value.posX === 'number' && estimateDraft.value.posX < 50 ? 0 : 105))
const coordinateMeasurements = computed(() => calculateGoalxGeometry({ ...estimateDraft.value, goalX: automaticGoalX.value }))
const timerRunning = ref(false)
const stopwatchElapsed = ref(0)
const stopwatchDisplay = computed(() => {
  const value = timerRunning.value ? stopwatchElapsed.value : estimateDraft.value.timeToGoal
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '0.00'
})
let stopwatch: ReturnType<typeof setInterval> | undefined
let stopwatchStarted = 0
const estimatedCoordinatesValid = computed(() => validEstimatedCoordinates({ ...estimateDraft.value, goalX: automaticGoalX.value }))
const estimatedTimeValid = computed(() => estimateDraft.value.timeToGoal == null
  || (typeof estimateDraft.value.timeToGoal === 'number' && Number.isFinite(estimateDraft.value.timeToGoal)
    && estimateDraft.value.timeToGoal > 0 && estimateDraft.value.timeToGoal <= 600))
const estimatedPreview = computed(() => {
  if (!headingHeightValid.value || !estimatedCoordinatesValid.value || !estimatedTimeValid.value || !draft.value.trajectory || !curveDirectionValid.value) return null
  return estimateGoalShot({ ...estimateDraft.value, ...shotContext.value, goalX: automaticGoalX.value,
    curveDirection: draft.value.trajectory === 'low' ? null : curveDirection.value } as EstimatedShotInput, draft.value.trajectory)
})
const hasEstimateCoordinates = computed(() => coordinateFields.some(field => estimateDraft.value[field.key] != null && estimateDraft.value[field.key] !== ''))
const estimateInvalid = computed(() => loadingRawCoordinates.value || !headingHeightValid.value || timerRunning.value || !estimatedTimeValid.value
  || ((hasEstimateCoordinates.value || estimateDraft.value.timeToGoal != null) && !estimatedCoordinatesValid.value))

function stopStopwatch() {
  if (stopwatch !== undefined) clearInterval(stopwatch)
  stopwatch = undefined
  timerRunning.value = false
}
function resetEstimate(goal?: Goal) {
  stopStopwatch()
  const raw = goal?.rawCoordinates ?? {}
  const saved = goal?.myEstimatedShot?.input
  shotContext.value = { shotType: raw.shotType ?? saved?.shotType, playerHeightCm: raw.playerHeightCm ?? saved?.playerHeightCm,
    jumpHeightM: raw.jumpHeightM ?? saved?.jumpHeightM, startHeightM: raw.startHeightM ?? saved?.startHeightM }
  curveDirection.value = goal?.myGrades?.trajectory === 'low' ? null : goal?.myCurveDirection ?? saved?.curveDirection ?? null
  rawGoalX.value = raw.goalX === 0 || raw.goalX === 105 ? raw.goalX : null
  estimateDraft.value = {
    ...Object.fromEntries(coordinateFields.map(field => [field.key, saved?.[field.key] ?? raw[field.key] ?? null])),
    timeToGoal: saved?.timeToGoal ?? null,
  }
}
async function loadRawCoordinates(goal?: Goal) {
  rawCoordinatesRequest?.abort()
  loadingRawCoordinates.value = false
  rawCoordinatesError.value = ''
  if (!goal || goal.manual) return
  const controller = new AbortController()
  rawCoordinatesRequest = controller
  const initial = { ...estimateDraft.value }
  loadingRawCoordinates.value = true
  try {
    const { coordinates } = await request<{ coordinates: RawCoordinates }>(pathOf(goal) + '/raw-coordinates', { signal: controller.signal })
    if (controller.signal.aborted || selectedKey.value !== keyOf(goal)) return
    goal.rawCoordinates = coordinates
    shotContext.value = { shotType: coordinates.shotType, playerHeightCm: coordinates.playerHeightCm,
      jumpHeightM: coordinates.jumpHeightM, startHeightM: coordinates.startHeightM }
    rawGoalX.value = coordinates.goalX === 0 || coordinates.goalX === 105 ? coordinates.goalX : null
    for (const { key } of coordinateFields) {
      if (initial[key] == null && estimateDraft.value[key] == null) estimateDraft.value[key] = coordinates[key] ?? null
    }
  } catch (error) {
    if (!controller.signal.aborted && selectedKey.value === keyOf(goal)) rawCoordinatesError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingRawCoordinates.value = false
  }
}
function toggleStopwatch() {
  if (timerRunning.value) {
    estimateDraft.value.timeToGoal = Math.max(.001, Math.round((performance.now() - stopwatchStarted) / 10) / 100)
    stopStopwatch()
    return
  }
  stopwatchStarted = performance.now()
  stopwatchElapsed.value = 0
  timerRunning.value = true
  stopwatch = setInterval(() => {
    stopwatchElapsed.value = Math.min(600, Math.round((performance.now() - stopwatchStarted) / 10) / 100)
    if (stopwatchElapsed.value >= 600) {
      estimateDraft.value.timeToGoal = 600
      stopStopwatch()
    }
  }, 30)
}
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
  return value == null ? '—' : value.toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' ' + unit
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
  const completed = [...completedGoals.value]
  exporting.value = true
  exportError.value = ''
  const leagueLabel = leagues.value.find(item => item.id === league.value)?.name || league.value
  const seasonName = seasonLabel(season.value)
  const filename = 'GOALX-' + [league.value, season.value, round.value].join('-').replace(/[^\w-]/g, '_') + '.xlsx'
  const headers = ['대회', '시즌', '라운드', '경기 날짜', '득점 시각', '득점자', '득점 팀', '홈팀', '원정팀', '평가 인원',
    ...categories.map(category => category.label + ' 평균'), '분석관 평가 점수', '총점',
    '평가 거리 (m)', '평가 시속 (km/h)', '골라인 통과 소요시간 (초)', '분석관 이름', '분석관 ID', 'ShotX ID',
    ...metrics.map(metric => 'ShotX 참고 ' + metric.label + ' (' + metric.unit + ')')]
  const rows = completed.map(goal => [
    leagueLabel, seasonName, roundLabel(goal.round), dateLabel(goal.date), goal.clock,
    goal.playerName, teamName(goal.teamId), teamName(goal.homeTeamId), teamName(goal.awayTeamId),
    goal.evaluationCount, ...categories.map(category => goal.analystCategoryScores?.[category.key] ?? null), goal.analystScore, goal.score,
    goal.ratingMetrics?.distance ?? null, goal.ratingMetrics?.averageSpeed ?? null, goal.ratingMetrics?.flightTime ?? null,
    goal.evaluators.map(analyst => analyst.name).join(', '), goal.evaluators.map(analyst => analyst.id).join(', '),
    goal.shotId, ...metrics.map(metric => goal.metrics[metric.key] ?? null),
  ])
  try {
    const exported = await request<{ goals: GoalxExportRecord[] }>(api + '/export?' + new URLSearchParams({ league_id: league.value, season_id: season.value, round_id: round.value }))
    const XLSX = await import('xlsx')
    const sheet = XLSX.utils.aoa_to_sheet([headers, ...rows])
    sheet['!cols'] = headers.map((_, i) => ({ wch: i < 5 ? 15 : 22 }))
    const workbook = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(workbook, sheet, '분석 완료')
    const labels = new Map(completed.map(goal => [keyOf(goal), [goal.gmId, goal.side, goal.recordId, goal.playerName, goal.clock]]))
    for (const extra of goalxEstimationSheets(exported.goals, labels)) {
      const extraSheet = XLSX.utils.aoa_to_sheet(extra.rows)
      extraSheet['!cols'] = extra.rows[0]!.map(() => ({ wch: 20 }))
      XLSX.utils.book_append_sheet(workbook, extraSheet, extra.name)
    }
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
  resetEstimate(goal)
  selectedKey.value = goal ? keyOf(goal) : ''
  void loadRawCoordinates(goal)
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
  resetEstimate(selected.value)
  draft.value = { ...selected.value?.myGrades }
  notice.value = ''
  actionError.value = ''
  editing.value = true
}
function cancelEditing() {
  resetEstimate(selected.value)
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
    else if (!editing.value) {
      draft.value = { ...selected.value.myGrades }
      resetEstimate(selected.value)
    }
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

async function confirmBulkApproval() {
  if (!canApproveRepresentative.value || bulkApproving.value || !ranking.value.length) return
  bulkTargets.value = [...ranking.value]
  bulkProcessed.value = 0
  await nextTick()
  bulkDialog.value?.showModal()
}
async function approveBulk() {
  if (bulkApproving.value || !canApproveRepresentative.value || !bulkTargets.value.length) return
  bulkApproving.value = true
  bulkError.value = ''
  bulkIssues.value = []
  bulkSummary.value = { approved: 0, skipped: 0, failed: 0 }
  try {
    for (let offset = 0; offset < bulkTargets.value.length; offset += 10) {
      const goals = bulkTargets.value.slice(offset, offset + 10)
      const data = await request<{ results: { gmId: string; side: 'H' | 'A'; recordId: string; status: 'approved' | 'skipped' | 'failed'; analysis?: Analysis; reason?: string }[] }>(api + '/representative/bulk', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goals: goals.map(({ gmId, side, recordId }) => ({ gmId, side, recordId })) }),
      })
      for (const item of data.results) {
        const goal = goals.find(goal => goal.gmId === item.gmId && goal.side === item.side && goal.recordId === item.recordId)
        if (!goal) continue
        bulkSummary.value[item.status]++
        if (item.status === 'approved' && item.analysis) patchGoal(goal, item.analysis)
        else bulkIssues.value.push({ goal, reason: item.reason || '처리 결과를 확인해 주세요.' })
      }
      bulkProcessed.value += goals.length
    }
  } catch (error) {
    bulkError.value = errorMessage(error) + ' 일부 골은 이미 처리되었을 수 있습니다. 랭킹을 다시 조회해 확인해 주세요.'
  } finally {
    bulkApproving.value = false
    bulkDialog.value?.close()
  }
}

async function openRepresentative(goal: Goal) {
  representativeRequest?.abort()
  const controller = new AbortController()
  representativeRequest = controller
  representativeGoal.value = goal
  representativeProposal.value = undefined
  representativeTrajectory.value = undefined
  representativeCurveDirection.value = null
  representativeError.value = ''
  loadingRepresentative.value = true
  await nextTick()
  representativeDialog.value?.showModal()
  try {
    const { proposal } = await request<{ proposal: RepresentativeProposal }>(pathOf(goal) + '/representative', { signal: controller.signal })
    if (controller.signal.aborted) return
    representativeProposal.value = proposal
    representativeCoordinates.value = Object.fromEntries(coordinateFields.map(({ key }) => [key, proposal.coordinates[key] ?? null]))
    representativeTrajectory.value = proposal.trajectoryCandidates.length === 1 ? proposal.trajectoryCandidates[0] : undefined
    chooseRepresentativeDirection()
  } catch (error) {
    if (!controller.signal.aborted) representativeError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loadingRepresentative.value = false
  }
}
function chooseRepresentativeDirection() {
  const grade = representativeTrajectory.value
  const candidates = grade && grade !== 'low' ? representativeProposal.value?.curveDirectionCandidates[grade] ?? [] : []
  representativeCurveDirection.value = candidates.length === 1 ? candidates[0]! : null
}
function closeRepresentative() {
  representativeRequest?.abort()
  representativeGoal.value = undefined
}
async function approveRepresentative() {
  const goal = representativeGoal.value
  const proposal = representativeProposal.value
  if (!goal || !proposal || !representativeTrajectory.value || !representativeDirectionValid.value || approvingRepresentative.value
    || (representativeHasCoordinates.value && !representativeCoordinatesValid.value)) return
  approvingRepresentative.value = true
  representativeError.value = ''
  try {
    const { analysis } = await request<{ analysis: Analysis }>(pathOf(goal) + '/representative', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceVersion: proposal.sourceVersion, trajectory: representativeTrajectory.value,
        curveDirection: representativeTrajectory.value === 'low' ? null : representativeCurveDirection.value,
        coordinates: representativeEstimate.value?.input ?? null }),
    })
    patchGoal(goal, analysis)
    representativeDialog.value?.close()
  } catch (error) {
    representativeError.value = errorMessage(error)
  } finally {
    approvingRepresentative.value = false
  }
}

async function loadCatalog() {
  loadingCatalog.value = true
  loadError.value = ''
  try {
    const data = await request<{ leagues: League[]; seasons: Season[]; canApproveRepresentative: boolean }>(api + '/catalog')
    canApproveRepresentative.value = data.canApproveRepresentative
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
  const ranked = rankingResult.value?.goals.find(item => keyOf(item) === keyOf(goal))
  if (ranked) Object.assign(ranked, analysis)
}

async function saveGrades() {
  const goal = selected.value
  if (!goal || !editing.value || saving.value || linking.value || loadingRawCoordinates.value || estimateInvalid.value || !curveDirectionValid.value || categories.some(({ key }) => !draft.value[key])) return
  saving.value = true
  actionError.value = ''
  notice.value = ''
  try {
    const { analysis } = await request<{ analysis: Analysis }>(pathOf(goal) + '/evaluation', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...draft.value, curveDirection: draft.value.trajectory === 'low' ? null : curveDirection.value,
        estimatedShot: estimatedPreview.value?.input ?? null }),
    })
    patchGoal(goal, analysis)
    if (selectedKey.value === keyOf(goal)) {
      draft.value = { ...analysis.myGrades }
      resetEstimate({ ...goal, ...analysis })
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
  representativeRequest?.abort()
  rawCoordinatesRequest?.abort()
  stopStopwatch()
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
            <button v-if="goal" type="button" class="goal-card" :class="{ completed: goal.myGrades, approved: goal.representative?.status === 'approved' }"
              :aria-pressed="selectedKey === keyOf(goal)" @click="selectedKey !== keyOf(goal) && selectGoal(goal)">
              <span class="card-top"><span class="clock">{{ goal.clock }}</span><span class="state">{{ goal.representative?.status === 'approved' ? '✓ 대표 승인 완료' : goal.myGrades ? '평가 완료' : '평가 전' }}</span></span>
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
            <button class="quiet hint-toggle" type="button" :aria-expanded="showShot" aria-controls="goalx-shotx"
              title="평가 전 참고하는 ShotX 분석입니다. 공식 총점 계산에는 사용하지 않습니다." @click="toggleShot">
              ShotX 분석
            </button>
          </div>
        </header>

        <div class="review-grid with-shot">
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
                    <input v-model="draft[category.key]" type="radio" :name="category.key" :value="grade" required @change="category.key === 'trajectory' && (curveDirection = null)">
                    <span>{{ category.key === 'trajectory' ? trajectoryLabels[grade] : labels[grade] }}</span><small>{{ categoryPoints(category.key, grade) }}점</small>
                  </label>
                </div>
                <div v-if="category.key === 'trajectory' && editing && curveDirectionRequired" class="curve-direction">
                  <p>어느 방향으로 휘었나요? <small>슈팅자가 골대를 바라보는 기준 · 점수 미반영</small></p>
                  <div class="choices">
                    <label v-for="direction in (['left', 'right'] as const)" :key="direction" :class="{ chosen: curveDirection === direction }">
                      <input v-model="curveDirection" type="radio" name="curve-direction" :value="direction" required>
                      <span>{{ direction === 'left' ? '↶ 좌' : '우 ↷' }}</span>
                    </label>
                  </div>
                </div>
                <p v-else-if="category.key === 'trajectory' && !editing && selected.myGrades?.trajectory !== 'low' && selected.myGrades" class="curve-direction-saved">
                  커브 방향 · {{ curveDirection ? curveDirectionLabels[curveDirection] : '미선택' }} <small>점수 미반영</small>
                </p>
                <div v-if="!editing && selected.myGrades" class="saved-grade">
                  <strong>{{ category.key === 'trajectory' ? trajectoryLabels[selected.myGrades[category.key]] : labels[selected.myGrades[category.key]] }}</strong>
                  <span>{{ categoryPoints(category.key, selected.myGrades[category.key]) }}점</span>
                </div>
              </fieldset>
            </div>
            <div v-if="editing" class="form-actions">
              <button type="submit" class="primary" :disabled="saving || linking || loadingRawCoordinates || estimateInvalid || !curveDirectionValid || categories.some(({ key }) => !draft[key])">{{ saving ? '저장 중…' : '평가 저장' }}</button>
              <button v-if="selected.myGrades" type="button" class="quiet" :disabled="saving" @click="cancelEditing">취소</button>
            </div>
            <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
            <p v-if="notice" class="notice" role="status">{{ notice }}</p>
            <div v-if="selected.evaluators.length" class="evaluators">
              <h3>평가 분석관 <span>{{ selected.evaluationCount }}명</span></h3>
              <ul>
                <li v-for="analyst in selected.evaluators" :key="analyst.id">
                  <strong>{{ analyst.name && analyst.name !== analyst.id ? analyst.name : '닉네임 미등록' }}</strong>
                </li>
              </ul>
            </div>
          </form>

          <div class="render-panels">
          <section v-if="!showShot" id="goalx-estimated" class="shot-panel">
            <h3>추정 3D렌더링</h3>
            <p class="muted shot-context">슈팅 유형 <strong>{{ shotContext.shotType || '미확인' }}</strong>
              <template v-if="shotContext.playerHeightCm != null"> · 선수 신장 {{ shotContext.playerHeightCm }}cm</template>
              <template v-if="shotContext.shotType === 'H' && headingHeightValid"> · 헤딩 출발 {{ ((shotContext.playerHeightCm ?? 0) / 100 + .5).toFixed(1) }}m (점프 0.5m)</template>
            </p>
            <p v-if="!headingHeightValid" class="error">선수관리에서 신장을 입력한 후 좌표를 다시 불러와 주세요.</p>
            <fieldset class="time-card" :class="{ 'is-running': timerRunning }" :disabled="!editing || saving || linking">
              <legend class="time-legend">골라인 통과 소요시간</legend>
              <div class="time-card-heading">
                <span class="time-title">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M9 2h6M12 2v3m6 1 2-2" /><circle cx="12" cy="14" r="8" /><path d="M12 9v5l3 2" /></svg>
                  골라인 통과 소요시간
                </span>
                <span class="time-status" role="status"><span aria-hidden="true"></span>{{ timerRunning ? '측정 중' : estimateDraft.timeToGoal != null ? '측정 완료' : '측정 대기' }}</span>
              </div>
              <div class="time-main">
                <div class="time-readout" aria-label="경과 시간">
                  <span class="time-digits">{{ stopwatchDisplay }}</span><span class="time-unit">초</span>
                </div>
                <button type="button" class="time-toggle" :aria-pressed="timerRunning" @click="toggleStopwatch">
                  <span class="time-button-icon" :class="{ stop: timerRunning }" aria-hidden="true"></span>
                  {{ timerRunning ? '측정 정지' : estimateDraft.timeToGoal != null ? '다시 측정' : '측정 시작' }}
                </button>
              </div>
              <div class="time-footer">
                <label class="time-adjust">시간 미세조정
                  <span class="time-input-wrap"><input :value="timerRunning ? stopwatchElapsed : estimateDraft.timeToGoal ?? ''" type="number" min="0.001" max="600" step="0.01" :disabled="timerRunning" placeholder="0.00" aria-label="골라인 통과 소요시간 미세조정 (초)"
                    @input="estimateDraft.timeToGoal = ($event.target as HTMLInputElement).value === '' ? null : ($event.target as HTMLInputElement).valueAsNumber">
                    <span>초</span></span>
                </label>
                <button type="button" class="time-reset" :disabled="!timerRunning && estimateDraft.timeToGoal == null" @click="stopStopwatch(); estimateDraft.timeToGoal = null">
                  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 8a7 7 0 1 1 0 5M3 3v5h5" /></svg>초기화
                </button>
              </div>
              <p class="time-hint">{{ timerRunning ? '골라인을 통과하는 순간 정지를 눌러 주세요.' : '슈팅 순간 시작하고, 골라인 통과 순간 정지하세요.' }}</p>
            </fieldset>
            <p v-if="!estimatedTimeValid && !timerRunning" class="error">시간은 0초 초과 600초 이하로 입력해 주세요.</p>
            <p v-else-if="rawCoordinatesError || (estimateInvalid && !timerRunning)" class="error">RAW 좌표를 확인해 주세요.</p>
            <p v-if="loadingRawCoordinates" class="muted" role="status">RAW 좌표를 불러오는 중…</p>
            <p v-else-if="rawCoordinatesError" class="error" role="alert">RAW 좌표를 불러오지 못했습니다. {{ rawCoordinatesError }}</p>
            <button v-if="rawCoordinatesError" type="button" :disabled="saving || linking" @click="loadRawCoordinates(selected)">RAW 좌표 다시 불러오기</button>
            <fieldset class="estimate-inputs" :disabled="!editing || saving || linking">
              <legend>슈팅·골문 좌표</legend>
              <p class="muted">RAW 좌표를 기본값으로 사용하며, 공격 골대는 자동으로 결정됩니다.</p>
              <div class="coordinate-inputs">
                <label v-for="field in coordinateFields" :key="field.key">{{ field.label }}
                  <input v-model.number="estimateDraft[field.key]" type="number" :min="field.min" :max="field.max" step="any" placeholder="좌표 없음">
                </label>
              </div>
              <p class="muted">슈팅 X·Y: 경기장 0–100%. 골문 결과 X: 83–464, Y: 83(상단)–300(지면).</p>
              <dl class="measurements" aria-label="좌표 계산 데이터">
                <div><dt>골라인 통과 높이</dt><dd>{{ formatMetric(coordinateMeasurements.height, 'm') }}</dd></div>
                <div><dt>발사각</dt><dd>{{ formatMetric(coordinateMeasurements.launchAngle, '°') }}</dd></div>
              </dl>
              <p class="muted">JAIONX AI가 계산한 데이터입니다. 높이는 지면 기준이며, 발사각은 골대 중앙 방향과 실제 골라인 통과 방향 사이의 평면 각도입니다.</p>
            </fieldset>
            <p v-if="!estimatedCoordinatesValid && hasEstimateCoordinates" class="error">좌표를 범위 안에서 모두 입력해 주세요.</p>
            <p class="muted">{{ draft.trajectory ? trajectoryLabels[draft.trajectory] : '궤적 유형 미선택' }} · 평가 기반 추정 궤적</p>
            <LazyGoalxGoalTrajectory v-if="estimatedPreview" :points="estimatedPreview.trajectory" :measured-points="[]"
              :goal-point="estimatedPreview.goalPoint" :goal-x="estimatedPreview.input.goalX" :goal-center-y="34"
              :flight-time="estimatedPreview.input.timeToGoal" />
            <p v-else class="muted">슈팅·골문 결과 좌표와 궤적 유형을 입력하면 3D 그래픽이 표시됩니다.</p>
            <dl v-if="estimatedPreview" class="measurements">
              <div><dt>추정 궤적 거리</dt><dd>{{ formatMetric(estimatedPreview.distance, 'm') }}</dd></div>
              <div><dt>추정 평균 속도</dt><dd>{{ formatMetric(estimatedPreview.averageSpeed, 'km/h') }}</dd></div>
            </dl>
            <p class="muted">선택한 궤적 유형과 {{ curveDirection ? curveDirectionLabels[curveDirection] + ' 방향' : '방향' }}을 반영한 추정 궤적입니다. 좌·우는 슈팅자가 골대를 바라보는 기준이며, 궤적 길이로 평균 속도를 계산합니다.</p>
          </section>
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
        </div>
      </article>
    </section>

    <section v-if="tab === 'ranking'" class="detail ranking">
      <header class="detail-header"><h2>골 랭킹</h2><span v-if="rankingResult">{{ ranking.length }}골 · {{ rankingResult.maxScore }}점 만점</span>
        <button v-if="canApproveRepresentative" type="button" class="primary" :disabled="loadingRanking || bulkApproving || !ranking.length" aria-haspopup="dialog" @click="confirmBulkApproval">일괄 처리</button>
      </header>
      <p v-if="bulkSummary" class="notice" role="status">일괄 처리 결과 · 승인 {{ bulkSummary.approved }}건 · 제외 {{ bulkSummary.skipped }}건 · 오류 {{ bulkSummary.failed }}건</p>
      <p v-if="bulkError" class="error" role="alert">{{ bulkError }}</p>
      <ul v-if="bulkIssues.length" class="bulk-issues"><li v-for="item in bulkIssues" :key="keyOf(item.goal)">
        <button type="button" class="text-button" @click="openRepresentative(item.goal)">{{ item.goal.playerName || '득점자 미기록' }} · {{ item.goal.clock }} 검토</button> — {{ item.reason }}
      </li></ul>
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
              <td class="clock">{{ goal.rank ?? '—' }}</td>
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
              <td>{{ formatMetric(goal.ratingMetrics?.distance, 'm') }}</td>
              <td>{{ formatMetric(goal.ratingMetrics?.averageSpeed, 'km/h') }}</td>
              <td><button type="button" class="text-button participants-button" aria-haspopup="dialog" @click="showParticipants(goal)">{{ goal.evaluationCount }}명</button></td>
              <td v-for="category in categories" :key="category.key" class="ranking-score">
                {{ goal.analystCategoryScores?.[category.key]?.toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) ?? '—' }}
              </td>
              <td class="ranking-score">{{ goal.analystScore?.toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) ?? '—' }}</td>
              <td class="ranking-score"><strong>{{ goal.score == null ? '—' : goal.score.toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) }}</strong></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <dialog ref="bulkDialog" class="representative-dialog" aria-labelledby="bulk-approval-title" @cancel="bulkApproving && $event.preventDefault()">
      <h2 id="bulk-approval-title">대표 승인 일괄 처리</h2>
      <p>현재 조회 목록의 {{ bulkTargets.length }}골을 최신 저장 평가·좌표로 대표 승인합니다. 이미 승인된 골도 최신 값으로 다시 검토합니다.</p>
      <p>좌표·시간·평점이 부족하거나 궤적·커브 방향이 동률인 골은 제외하고 사유를 표시합니다. 처리하시겠습니까?</p>
      <p v-if="bulkApproving" role="status">처리 중… {{ bulkProcessed }} / {{ bulkTargets.length }}골</p>
      <div class="form-actions"><button type="button" class="quiet" :disabled="bulkApproving" @click="bulkDialog?.close()">취소</button>
        <button type="button" class="primary" :disabled="bulkApproving" @click="approveBulk">{{ bulkApproving ? '처리 중…' : '확인하고 처리' }}</button></div>
    </dialog>
    <LazyGoalxTestGoalEditor v-if="testEditor" :initial="testEditor" @close="testEditor = undefined" @saved="afterTestGoalSave" />
    <dialog ref="representativeDialog" class="representative-dialog" aria-labelledby="representative-title" @close="closeRepresentative"
      @cancel="approvingRepresentative && $event.preventDefault()">
      <template v-if="representativeGoal">
        <header class="detail-header">
          <div><h2 id="representative-title">대표 분석 승인</h2><p>{{ representativeGoal.playerName }} · {{ rankingMatch(representativeGoal) }} · {{ representativeGoal.clock }}</p></div>
          <button type="button" class="quiet" :disabled="approvingRepresentative" @click="representativeDialog?.close()">닫기</button>
        </header>
        <div class="representative-content">
          <p v-if="loadingRepresentative" class="muted" role="status">분석관 평가를 취합하는 중…</p>
          <form v-if="representativeProposal" @submit.prevent="approveRepresentative">
            <p class="muted">평가 {{ representativeProposal.evaluationCount }}건을 기준으로 대표 결과를 확정합니다. 이후 평가가 바뀌면 재검토가 필요합니다.</p>
            <dl class="measurements">
              <div v-for="category in categories" :key="category.key"><dt>{{ category.label }} 평균</dt><dd>{{ formatMetric(representativeProposal.categoryScores[category.key], '점') }}</dd></div>
              <div><dt>대표 평균 총점</dt><dd>{{ formatMetric(representativeProposal.score, '점') }}</dd></div>
              <div><dt>소요시간 중앙값</dt><dd>{{ formatMetric(representativeProposal.timeToGoal, '초') }} <small>측정 {{ representativeProposal.timeSampleCount }}건</small></dd></div>
            </dl>
            <fieldset :disabled="approvingRepresentative" class="representative-field">
              <legend>대표 궤적</legend>
              <p class="muted">{{ representativeProposal.trajectoryCandidates.length > 1 ? '최다 선택이 동률입니다. 대표 유형을 선택해 주세요.' : '분석관들이 가장 많이 선택한 유형입니다.' }}</p>
              <div class="choices">
                <label v-for="grade in representativeProposal.trajectoryCandidates" :key="grade" :class="{ chosen: representativeTrajectory === grade }">
                  <input v-model="representativeTrajectory" type="radio" name="representative-trajectory" :value="grade" required @change="chooseRepresentativeDirection">
                  <span>{{ trajectoryLabels[grade] }}</span><small>{{ representativeProposal.trajectoryCounts[grade] }}명</small>
                </label>
              </div>
            </fieldset>
            <fieldset v-if="representativeTrajectory && representativeTrajectory !== 'low'" class="representative-field curve-direction" :disabled="approvingRepresentative">
              <legend>대표 커브 방향 · 점수 미반영</legend>
              <p class="muted">선택한 궤적의 최다 방향입니다. 동률이거나 기존 방향 정보가 없으면 관리자가 선택하세요.</p>
              <div class="choices"><label v-for="direction in representativeProposal.curveDirectionCandidates[representativeTrajectory]" :key="direction" :class="{ chosen: representativeCurveDirection === direction }">
                <input v-model="representativeCurveDirection" type="radio" name="representative-direction" :value="direction" required>
                <span>{{ curveDirectionLabels[direction] }}</span><small>{{ representativeProposal.curveDirectionCounts[representativeTrajectory][direction] ?? 0 }}명</small>
              </label></div>
            </fieldset>
            <fieldset :disabled="approvingRepresentative" class="representative-field">
              <legend>대표 좌표 확인</legend><p class="muted">{{ representativeProposal.coordinateSource === 'evaluations' ? `분석에서 저장한 좌표를 자동 반영했습니다. 분석관 ${representativeProposal.coordinateSampleCount}명의 항목별 중앙값입니다.` : '저장된 분석 좌표가 없어 RAW 좌표를 사용합니다.' }} 필요한 경우 수정 후 확정하세요.</p>
              <div class="coordinate-inputs"><label v-for="field in coordinateFields" :key="field.key">{{ field.label }}
                <input v-model.number="representativeCoordinates[field.key]" type="number" :min="field.min" :max="field.max" step="any" placeholder="좌표 없음">
              </label></div>
              <dl class="measurements" aria-label="대표 좌표 계산 데이터">
                <div><dt>골라인 통과 높이</dt><dd>{{ formatMetric(calculateGoalxGeometry({ ...representativeCoordinates, goalX: representativeProposal.directionGoalX }).height, 'm') }}</dd></div>
                <div><dt>발사각</dt><dd>{{ formatMetric(calculateGoalxGeometry({ ...representativeCoordinates, goalX: representativeProposal.directionGoalX }).launchAngle, '°') }}</dd></div>
              </dl>
              <p class="muted">JAIONX AI가 계산한 데이터 · 발사각은 골대 중앙 방향 대비 평면 각도</p>
              <p v-if="representativeHasCoordinates && !representativeCoordinatesValid" class="error">좌표를 범위 안에서 모두 입력해 주세요.</p>
            </fieldset>
            <template v-if="representativeEstimate">
              <LazyGoalxGoalTrajectory :points="representativeEstimate.trajectory" :measured-points="[]" :goal-point="representativeEstimate.goalPoint"
                :goal-x="representativeEstimate.input.goalX" :goal-center-y="34" :flight-time="representativeEstimate.input.timeToGoal" />
              <dl class="measurements"><div><dt>대표 추정 거리</dt><dd>{{ formatMetric(representativeEstimate.distance, 'm') }}</dd></div>
                <div><dt>대표 추정 평균 속도</dt><dd>{{ formatMetric(representativeEstimate.averageSpeed, 'km/h') }}</dd></div></dl>
            </template>
            <p v-else class="muted">좌표 또는 대표 궤적이 없으면 추정 3D는 포함되지 않습니다.</p>
            <p v-if="representativeProposal.timeToGoal == null" class="muted">측정된 소요시간이 없어 추정 평균 속도는 포함되지 않습니다.</p>
            <div class="form-actions"><button type="submit" class="primary" :disabled="approvingRepresentative || !representativeTrajectory || !representativeDirectionValid || (representativeHasCoordinates && !representativeCoordinatesValid)">
              {{ approvingRepresentative ? '확정 중…' : '대표 분석 확정' }}</button></div>
          </form>
          <p v-if="representativeError" class="error" role="alert">{{ representativeError }}</p>
        </div>
      </template>
    </dialog>
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
            <strong>{{ analyst.name && analyst.name !== analyst.id ? analyst.name : '닉네임 미등록' }}</strong>
          </li>
        </ul>
      </template>
    </dialog>
  </div>
</template>

<style scoped>
.shot-context { margin: 10px 0 16px; }
.curve-direction { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
.curve-direction p, .curve-direction-saved { margin-bottom: 8px; color: var(--muted); font-size: 12px; line-height: 1.5; }
.curve-direction p small { display: block; margin-top: 3px; font-size: 11px; }
.curve-direction .choices { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.curve-direction-saved { margin-top: 8px; }
.curve-direction-saved small { margin-left: 6px; }
.representative-button { white-space: nowrap; color: var(--accent); font-size: 12px; }
.representative-dialog { width: min(720px, calc(100vw - 32px)); max-height: 90vh; box-sizing: border-box; padding: 0; color: var(--text); background: var(--surface); border: 1px solid var(--line); border-radius: 14px; }
.representative-dialog::backdrop { background: #071014b8; }
.representative-content { padding: 18px 20px; }
.representative-field { margin: 18px 0; }
.representative-field legend { margin-bottom: 8px; font-weight: 650; }
.representative-field .muted { margin-bottom: 10px; }
.render-panels { min-width: 0; display: grid; align-content: start; gap: 16px; }
.estimate-inputs { margin: 16px 0; padding: 12px; border: 1px solid var(--line); border-radius: 10px; min-width: 0; }
.coordinate-inputs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.coordinate-inputs label { display: grid; gap: 5px; font-size: 12px; }
.coordinate-inputs input, .coordinate-inputs select { box-sizing: border-box; min-width: 0; width: 100%; }
.time-card { min-width: 0; margin: 20px 0 16px; padding: 18px 20px; border: 1px solid #314b46; border-radius: 14px; background: linear-gradient(120deg, #182d2b 0%, #101e24 75%); transition: border-color .2s, box-shadow .2s; }
.time-card.is-running { border-color: #92dcb7; box-shadow: inset 0 0 0 1px #a4e7c91a; }
.time-legend { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.time-card-heading, .time-title, .time-status, .time-main, .time-readout, .time-footer, .time-adjust { display: flex; align-items: center; }
.time-card-heading, .time-main, .time-footer { justify-content: space-between; gap: 12px; }
.time-title { gap: 8px; color: #d7e8e1; font-size: 13px; font-weight: 650; }
.time-title svg { width: 20px; height: 20px; color: var(--accent); flex: none; }
.time-status { gap: 6px; color: var(--muted); font-size: 11px; white-space: nowrap; }
.time-status > span { width: 5px; height: 5px; border-radius: 50%; background: #6d8582; }
.is-running .time-status { color: var(--accent); }
.is-running .time-status > span { background: var(--accent); box-shadow: 0 0 0 4px #a4e7c914; }
.time-main { padding: 17px 0 19px; flex-wrap: wrap; }
.time-readout { gap: 9px; align-items: baseline; }
.time-digits { font-size: clamp(38px, 4vw, 52px); line-height: 1.1; font-weight: 650; font-variant-numeric: tabular-nums; letter-spacing: -.045em; color: #effbf4; }
.time-unit { color: #8eaaa0; font-size: 15px; }
.time-card .time-toggle { min-height: 46px; min-width: 132px; display: inline-flex; justify-content: center; align-items: center; gap: 9px; padding: 11px 18px; color: #10291e; background: #a4e7c9; border-color: #a4e7c9; font-size: 14px; font-weight: 750; border-radius: 10px; }
.time-card .time-toggle:hover:not(:disabled) { background: #baf3d9; border-color: #baf3d9; }
.time-card.is-running .time-toggle { color: #ffe4cf; background: #4a3028; border-color: #a47759; }
.time-card.is-running .time-toggle:hover:not(:disabled) { background: #5c3b2e; border-color: #d49c71; }
.time-button-icon { width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-left: 8px solid currentColor; }
.time-button-icon.stop { width: 9px; height: 9px; border: 0; border-radius: 2px; background: currentColor; }
.time-footer { border-top: 1px solid #30453f; padding-top: 13px; flex-wrap: wrap; }
.time-adjust { gap: 10px; color: var(--muted); font-size: 12px; flex-wrap: wrap; }
.time-input-wrap { display: flex; align-items: center; gap: 6px; }
.time-input-wrap input { box-sizing: border-box; width: 108px; min-height: 36px; padding: 7px 9px; background: #101e22; border-color: #38524a; font-size: 13px; font-variant-numeric: tabular-nums; }
.time-input-wrap input:disabled { opacity: .5; }
.time-card .time-reset { display: inline-flex; align-items: center; gap: 5px; padding: 7px 2px; background: transparent; border-color: transparent; color: var(--muted); font-size: 12px; }
.time-reset svg { width: 15px; height: 15px; }
.time-hint { margin-top: 11px; color: #8ca39d; font-size: 11px; line-height: 1.6; }
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
.goal-actions { display: flex; flex-wrap: wrap; justify-content: end; gap: 8px; }
.hint-toggle { display: inline-flex; align-items: center; justify-content: center; gap: 7px; color: #aab9c1; border-style: dashed; border-color: #46545e; white-space: nowrap; font-size: 12px; }
.hint-toggle[aria-expanded="true"] { background: #253039; color: #d0d9de; border-style: solid; }
.goal-card.approved { background: #121b20; color: #9da9ad; border-color: #303d43; }
.goal-card.approved .clock, .goal-card.approved .card-team { color: #8d9ca2; }
.goal-card.approved .state { color: #acb8ba; background: #293438; }
.goal-card.approved[aria-pressed="true"] { background: #202b30; border-color: #687b81; box-shadow: inset 0 2px #87979c; }
.representative-button.approved { color: #a5b3b7; background: #202c32; border-color: #39484e; }
.representative-button.approved:hover:not(:disabled) { background: #29363b; border-color: #697c83; }

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
