const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')

const source = file => fs.readFileSync(path.resolve(__dirname, '../pages', file), 'utf8')
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const ref = value => ({ value })
function functions(file, names, globals) {
  const text = source(file)
  const code = names.map(name => text.match(new RegExp(`(?:async )?function ${name}\\([\\s\\S]*?\\n}`))[0]).join('\n')
  const context = vm.createContext(globals)
  vm.runInContext(transformSync(code, { loader: 'ts', format: 'cjs' }).code, context)
  return context
}

test('saved setup paints without waiting for profile, then only the current half precedes full KPI', async () => {
  const half = deferred(), profile = deferred(), events = []
  const context = functions('TeamSelection.vue', ['loadInitialKpis'], {
    game: ref({ halfStatus: 'H2_done', team: 'home', formationKey: '4-3-3' }),
    inputStatus: ref({ H: { rawStatus: null, lifecycleStatus: 'H2_done' } }),
    loadRecorderProfile: () => profile.promise,
    loadBootstrap: async () => ({}),
    applyBootstrap: () => { events.push('setup'); return true },
    isCurrentLobby: () => true,
    bootstrapApplied: ref(false), nextTick: async () => {},
    lobbyTeam: 'home', lobbyMatchId: 'm', initialKpisLoading: true,
    dashboardKpis: ref({ H: {}, A: {} }), dashboardKpiLoaded: {}, kpiHalf: ref('H2'),
    request: url => { events.push(url); return half.promise },
    loadDashboardKpis: async half => { events.push(half) },
    ensureLobbyHistory: async () => { events.push('history') },
    ensureLobbyParticipant: async () => {}, requestedRole: 'primary',
    setInterval: () => 1, lifecycleError: ref(''),
    onMounted: callback => { context.mount = callback },
  })
  const mount = source('TeamSelection.vue').match(/onMounted\(async \(\) => \{[\s\S]*?\n}\)/)[0]
  vm.runInContext(transformSync(mount, { loader: 'ts', format: 'cjs' }).code, context)
  await context.mount()
  assert.equal(context.bootstrapApplied.value, true)
  assert.equal(events[0], 'setup')
  assert.match(events[1], /phase=preview&half=H2$/)
  assert.equal(events.length, 2)
  half.resolve({ dashboardKpis: { H: { H2: { Goal: 2 } }, A: { H2: { Goal: 0 } } } })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(context.dashboardKpiLoaded.H2, true)
  assert.equal(context.dashboardKpiLoaded.H1, undefined)
  assert.deepEqual(events.slice(2), ['all', 'history'])
})

test('KPI refresh cannot replace saved lineup, and late replies cannot reach another lobby', async () => {
  const responses = [deferred(), deferred()]
  let active = true, requests = 0
  const assigned = { gk: 'keeper', o0: 'starter' }
  const context = functions('TeamSelection.vue', ['loadDashboardKpis'], {
    isCurrentLobby: () => active, matchId: ref('m'), lobbyMatchId: 'm',
    dashboardKpiLoading: new Set(), dashboardKpiLoaded: {},
    dashboardKpis: ref({ H: {}, A: {} }),
    game: ref({ assigned, formationKey: '4-3-3' }),
    request: () => responses[requests++].promise,
  })
  const first = context.loadDashboardKpis('H2')
  responses[0].resolve({ kpis: { H: { Goal: 1 }, A: { Goal: 0 } }, inputSetup: { H: { formationKey: '4-4-2', lineup: [] } } })
  await first
  assert.equal(context.game.value.assigned, assigned)
  assert.equal(context.game.value.formationKey, '4-3-3')
  const second = context.loadDashboardKpis('all')
  active = false
  responses[1].resolve({ kpis: { H: { Goal: 9 }, A: { Goal: 0 } } })
  await second
  assert.equal(context.dashboardKpiLoaded.all, undefined)
})

test('calendar dots appear before day details and stale day replies are ignored', async () => {
  const calendar = deferred(), firstDay = deferred(), secondDay = deferred(), urls = []
  const context = functions('schedule.vue', ['loadCalendar', 'loadMatches', 'pickDate', 'normalizeDate'], {
    calendarGeneration: 0, detailGeneration: 0, scheduleActive: true,
    viewYear: ref(2026), viewMonth: ref(9), calendarCache: ref({}),
    calendarFixtures: ref([]), selectedDate: ref(''), selectedLeague: ref('all'),
    selectedMatchId: ref(''), selectedTeam: ref(null), matches: ref([]),
    loadingMatches: ref(false), loadError: ref(''),
    request: url => {
      urls.push(url)
      return url.includes('/calendar?') ? calendar.promise : urls.length === 2 ? firstDay.promise : secondDay.promise
    },
  })
  const loaded = context.loadCalendar()
  calendar.resolve({ fixtures: [{ date: '2026-10-01', leagueId: 'EPL' }, { date: '2026-10-14', leagueId: 'EPL' }] })
  await loaded
  assert.equal(context.calendarFixtures.value.length, 2)
  assert.equal(context.selectedDate.value, '2026-10-14')
  assert.equal(context.loadingMatches.value, true)
  assert.match(urls[1], /date=2026-10-14$/)
  context.pickDate('2026-10-01')
  const item = (id, date) => ({ gmId: id, date, round: 1, home: {}, away: {} })
  secondDay.resolve({ matches: [item('selected', '2026-10-01')] })
  await new Promise(resolve => setImmediate(resolve))
  firstDay.resolve({ matches: [item('stale', '2026-10-14')] })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(context.matches.value[0].id, 'selected')
  assert.equal(context.loadingMatches.value, false)
})

test('existing match cannot be submitted before its full collaboration snapshot is ready', async () => {
  const context = functions('TeamSelection.vue', ['ensureLobbyHistory'], {
    isCurrentLobby: () => true, lobbyTeam: 'home', lobbyHistoryPromise: undefined,
    lobbyHistoryLoaded: false, game: ref({ halfStatus: 'H2_done' }),
    inputStatus: ref({ H: { rawStatus: null } }),
    startLobbyCollaboration: async () => true,
  })
  await assert.rejects(context.ensureLobbyHistory(), /기록을 불러오는 중/)
  context.lobbyHistoryLoaded = true
  await context.ensureLobbyHistory()
})
