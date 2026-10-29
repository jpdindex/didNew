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
  const half = deferred(), profile = deferred(), events = [], intervals = []
  const context = functions('TeamSelection.vue', ['loadInitialKpis'], {
    game: ref({ halfStatus: 'H2_done', team: 'home', formationKey: '4-3-3' }),
    inputStatus: ref({ H: { rawStatus: null, lifecycleStatus: 'H2_done' } }),
    loadRecorderProfile: () => profile.promise,
    loadBootstrap: async () => ({}),
    applyBootstrap: () => { events.push('setup'); return true },
    isCurrentLobby: () => true,
    bootstrapApplied: ref(false), lobbyReady: ref(false), nextTick: async () => {},
    lobbyTeam: 'home', lobbyMatchId: 'm', initialKpisLoading: true,
    dashboardKpis: ref({ H: {}, A: {} }), dashboardKpiLoaded: {}, kpiHalf: ref('H2'),
    request: url => { events.push(url); return half.promise },
    loadDashboardKpis: async half => { events.push(half) },
    ensureLobbyHistory: async () => { events.push('history') },
    ensureLobbyParticipant: async () => {}, requestedRole: 'primary',
    refreshLobbyState: async () => {},
    setInterval: (callback, delay) => { intervals.push(delay); return 1 }, lifecycleError: ref(''),
    onMounted: callback => { context.mount = callback },
  })
  const mount = source('TeamSelection.vue').match(/onMounted\(async \(\) => \{[\s\S]*?\n}\)/)[0]
  vm.runInContext(transformSync(mount, { loader: 'ts', format: 'cjs' }).code, context)
  await context.mount()
  assert.equal(context.bootstrapApplied.value, true)
  assert.equal(events[0], 'setup')
  assert.match(events[1], /phase=preview&half=H2$/)
  assert.equal(events.length, 2)
  assert.ok(intervals.includes(5000))
  assert.ok(!intervals.includes(1200))
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
    joinedLobbyRole: 'primary', lobbyJoinPromise: undefined,
    startLobbyCollaboration: async () => true,
  })
  await assert.rejects(context.ensureLobbyHistory(), /기록을 불러오는 중/)
  context.lobbyHistoryLoaded = true
  await context.ensureLobbyHistory()
})

test('unjoined ready lobby does not start full history loading', async () => {
  let starts = 0
  const context = functions('TeamSelection.vue', ['ensureLobbyHistory'], {
    isCurrentLobby: () => true, lobbyTeam: 'home', lobbyHistoryPromise: undefined,
    lobbyHistoryLoaded: false, game: ref({ halfStatus: 'ready' }),
    inputStatus: ref({ H: { rawStatus: null } }),
    joinedLobbyRole: undefined, lobbyJoinPromise: undefined,
    startLobbyCollaboration: async () => { starts++; return true },
  })
  await context.ensureLobbyHistory()
  assert.equal(starts, 0)
  assert.equal(context.lobbyHistoryLoaded, false)
})

function lobbyRefreshEnvironment() {
  const urls = [], timers = [], kpiLoads = [], applied = []
  const state = (revision = 1, setupRevision = 0) => ({ revision, setupRevision, roomEpoch: 0, closed: false, sharedState: {} })
  let sides = { H: state(), A: state() }
  const context = functions('TeamSelection.vue', ['refreshLobbyState', 'queueLobbyKpiRefresh', 'lobbyVersionKey'], {
    isCurrentLobby: () => true, initialKpisLoading: false, lobbyRefreshBusy: false,
    lobbyMatchId: 'm', lobbyTeam: 'home', joinedLobbyRole: undefined,
    lobbyKpiRefreshTimer: undefined, lobbySetupRevisions: { H: 0, A: 0 },
    lastLobbyVersions: JSON.stringify([[1, 0, false], [1, 0, false]]),
    dashboardKpiLoaded: { all: true, H1: true, H2: true },
    game: ref({ formationKey: '4-3-3', halfStatus: 'ready' }),
    inputStatus: ref({ H: { rawStatus: null }, A: { rawStatus: null } }), kpiHalf: ref('all'),
    document: { hidden: false },
    setTimeout: (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer },
    loadDashboardKpis: async half => { kpiLoads.push(half) },
    applySetupFieldSides: setups => {
      for (const side of ['H', 'A']) if (setups[side]) context.lobbySetupRevisions[side] = setups[side].revision
    },
    applyInputSetup: setup => { applied.push(setup) },
    request: async url => {
      urls.push(url)
      if (url.endsWith('/lobby-state')) return { sides }
      if (url.endsWith('/input-setup')) return { inputSetup: { H: { revision: sides.H.setupRevision }, A: { revision: sides.A.setupRevision } } }
      throw new Error(`Full-history or unplanned read: ${url}`)
    },
  })
  return { context, urls, timers, kpiLoads, applied, state, setSides: value => { sides = value } }
}

test('unchanged lobby checks read only compact versions, with no KPI or setup reload', async () => {
  const env = lobbyRefreshEnvironment()
  for (let tick = 0; tick < 12; tick++) await env.context.refreshLobbyState()
  assert.equal(env.urls.length, 12)
  assert.ok(env.urls.every(url => url.endsWith('/lobby-state')))
  assert.equal(env.kpiLoads.length, 0)
  assert.equal(env.timers.length, 0)
  assert.equal(env.applied.length, 0)
})

test('peer ACT revisions coalesce KPI refresh while only setup revisions fetch a setup', async () => {
  const env = lobbyRefreshEnvironment()
  env.setSides({ H: env.state(1), A: env.state(2) })
  await env.context.refreshLobbyState()
  env.setSides({ H: env.state(1), A: env.state(3) })
  await env.context.refreshLobbyState()
  assert.equal(env.timers.length, 1)
  assert.equal(env.timers[0].delay, 1200)
  assert.equal(env.applied.length, 0)
  env.timers[0].callback()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(env.kpiLoads, ['all'])
  env.setSides({ H: { ...env.state(2, 1), sharedState: { halfStatus: 'H1', seconds: 90 } }, A: env.state(3) })
  await env.context.refreshLobbyState()
  assert.equal(env.urls.filter(url => url.endsWith('/input-setup')).length, 1)
  assert.equal(env.applied.length, 1)
  assert.equal(env.context.game.value.formationKey, '4-3-3')
  assert.equal(env.context.game.value.seconds, 90)
  await env.context.refreshLobbyState()
  assert.equal(env.urls.filter(url => url.endsWith('/input-setup')).length, 1)
})

test('hidden, loading or busy lobbies issue no background requests and late replies are ignored', async () => {
  const env = lobbyRefreshEnvironment()
  env.context.document.hidden = true
  await env.context.refreshLobbyState()
  env.context.document.hidden = false
  env.context.initialKpisLoading = true
  await env.context.refreshLobbyState()
  env.context.initialKpisLoading = false
  env.context.lobbyRefreshBusy = true
  await env.context.refreshLobbyState()
  assert.equal(env.urls.length, 0)
  env.context.lobbyRefreshBusy = false
  const gate = deferred()
  env.context.request = () => gate.promise
  const pending = env.context.refreshLobbyState()
  await env.context.refreshLobbyState()
  env.context.isCurrentLobby = () => false
  gate.resolve({ sides: { H: { ...env.state(10, 1), sharedState: { seconds: 999 } }, A: env.state() } })
  await pending
  assert.equal(env.context.game.value.seconds, undefined)
  assert.equal(env.applied.length, 0)
  assert.equal(env.timers.length, 0)
})

test('failed KPI updates retry on the next version check, and background tabs stay quiet', async () => {
  const env = lobbyRefreshEnvironment()
  env.context.loadDashboardKpis = async () => { throw new Error('temporary failure') }
  env.setSides({ H: env.state(2), A: env.state() })
  await env.context.refreshLobbyState()
  env.timers[0].callback()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(env.context.lastLobbyVersions, '')
  await env.context.refreshLobbyState()
  assert.equal(env.timers.length, 2)
  env.context.document.hidden = true
  env.context.loadDashboardKpis = async () => { assert.fail('hidden tab must not fetch KPI') }
  env.timers[1].callback()
  assert.equal(env.context.lastLobbyVersions, '')
})
