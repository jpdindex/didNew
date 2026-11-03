const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')

function contractsEnvironment(contracts = [], players = [], { missingIndexes = false } = {}) {
  const requests = []
  let active = 0, peak = 0
  const api = {
    collection: (db, name) => ({ kind: 'players', name }),
    collectionGroup: (db, name) => ({ kind: 'contracts', name }),
    documentId: () => '__name__', where: (field, op, value) => ({ field, op, value }),
    query: (source, ...filters) => ({ ...source, filters }),
    getDocs: async query => {
      if (missingIndexes && query.kind === 'contracts' && query.filters.some(filter => filter.field === 'from')) {
        throw Object.assign(new Error('query requires an index'), { code: 'failed-precondition' })
      }
      requests.push(query); active++; peak = Math.max(peak, active)
      await new Promise(resolve => setImmediate(resolve))
      active--
      const source = query.kind === 'contracts' ? contracts : players
      const found = source.filter(row => query.filters.every(({ field, op, value }) => {
        const actual = field === '__name__' ? row.id : row[field]
        if (op === 'in') return value.includes(actual)
        if (op === '==') return actual === value
        if (op === '>=') return actual != null && actual >= value
        if (op === '<=') return actual != null && actual <= value
        throw new Error(op)
      }))
      return { docs: found.map(row => ({ id: row.id, data: () => row,
        ref: { parent: { parent: { id: row.playerId, parent: { id: row.owner || 'players' } } } } })) }
    },
    getDoc: () => { throw new Error('One-player-at-a-time reads are forbidden') },
  }
  const module = { exports: {} }
  const source = fs.readFileSync(path.join(__dirname, '../utils/playerContracts.ts'), 'utf8')
  vm.runInNewContext(transformSync(source, { loader: 'ts', format: 'cjs' }).code, {
    module, exports: module.exports, require: name => { assert.equal(name, 'firebase/firestore'); return api },
  })
  return { ...module.exports, requests, peak: () => peak }
}

test('season listing requests only overlapping contracts and defers all profile reads', async () => {
  const make = (id, from, to, extra = {}) => ({ id, playerId: id, teamId: 'T', from, to, ...extra })
  const env = contractsEnvironment([
    make('3', '2025.08.01', null), make('2', '2026.01.01', '2026.08.01'),
    make('4', '2024.01.01', '2026.07.31'), make('5', '2027.06.01', null),
    make('6', '2026.01.01', null, { owner: 'coaches' }),
  ])
  const rows = await env.fetchPlayersByTeamsAndSeason({}, ['T'], '2026/27', { deferProfiles: true })
  assert.equal(rows.map(row => row.player.id).join(','), '2,3')
  assert.equal(env.requests.length, 2)
  assert.ok(env.requests.every(request => request.kind === 'contracts'))
  assert.ok(env.requests.every(request => request.filters.some(filter => filter.field === 'from' && filter.value === '2027.05.31')))
})

test('50 visible profiles use two batched reads and deduplicate p_id', async () => {
  const players = Array.from({ length: 50 }, (_, i) => ({ id: String(i).padStart(10, '0'), name: `Player ${i}` }))
  const env = contractsEnvironment([], players)
  const result = await env.fetchPlayerProfiles({}, [...players.map(player => player.id), players[0].id])
  assert.equal(result.length, 50)
  assert.equal(env.requests.length, 2)
  assert.ok(env.requests.every(request => request.filters[0].value.length <= 30))
})

test('missing composite index preserves the season-filtered list using legacy contract lookup', async () => {
  const env = contractsEnvironment([
    { id: 'open', playerId: '1', teamId: 'T', from: '2026.01.01', to: null },
    { id: 'old', playerId: '2', teamId: 'T', from: '2024.01.01', to: '2025.01.01' },
  ], [], { missingIndexes: true })
  const rows = await env.fetchPlayersByTeamsAndSeason({}, ['T'], '2026/27', { deferProfiles: true })
  assert.equal(rows.map(row => row.player.id).join(','), '1')
})

test('profile requests stay bounded instead of flooding Firestore', async () => {
  const env = contractsEnvironment()
  await env.fetchPlayerProfiles({}, Array.from({ length: 310 }, (_, i) => String(i)))
  assert.equal(env.requests.length, 11)
  assert.equal(env.peak(), 4)
})

function pageFunctions(names, globals) {
  const source = fs.readFileSync(path.join(__dirname, '../pages/manage/players/index.vue'), 'utf8')
  const code = names.map(name => source.match(new RegExp(`(?:async )?function ${name}\\([\\s\\S]*?\\n}`))[0]).join('\n')
  const context = vm.createContext(globals)
  vm.runInContext(transformSync(code, { loader: 'ts', format: 'cjs' }).code, context)
  return context
}
const ref = value => ({ value })

test('manual player registration uses the server numeric allocator with a stable retry key', async () => {
  const requests = []
  const context = pageFunctions(['submitAddPlayer'], {
    newPlayer: ref({ name: 'Player', nameEn: '', nameFull: '', birth: '', height: '', foot: '', nation: '',
      teamId: 'T', no: '7', pos: 'MF', from: '2026.08.01', transferType: 'TRANSFER', fee: '', currency: 'EUR' }),
    selectedSeason: ref('2026/27'), selectedLeague: ref('LALIGA'),
    newPlayerRequestId: 'same-idempotency-key', newPlayerError: ref(''), savingNewPlayer: ref(false),
    findDuplicatePlayers: async () => [], backendRequest: async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return { playerId: '0000000101' } },
    allPlayersCache: null, addOpen: ref(true), loadPlayers: async () => {},
  })
  await context.submitAddPlayer()
  await context.submitAddPlayer()
  assert.equal(requests.length, 2)
  assert.equal(requests[0].url, '/api/v1/player-requests/create-player')
  assert.equal(requests[0].body.requestId, requests[1].body.requestId)
  assert.equal(requests[0].body.seasonId, '20262027')
  assert.equal(requests[0].body.fromDate, '2026-08-01')
  assert.equal(context.newPlayerError.value, '')
})

test('late filter response cannot replace the latest season/team list', async () => {
  let resolveOld
  const old = new Promise(resolve => { resolveOld = resolve })
  let calls = 0
  const context = pageFunctions(['loadPlayers'], {
    listRequestId: 0, profileRequestId: 0, profileCache: new Map(), profileLoading: ref(false), loading: ref(false), loadError: ref(''),
    selectedTeamFilter: ref('T'), leagueTeams: ref([]), selectedSeason: ref('2026/27'), db: {}, rows: ref([]),
    fetchPlayersByTeamsAndSeason: async () => ++calls === 1 ? old : [{ player: { id: 'new' }, contracts: [] }],
  })
  const first = context.loadPlayers()
  await context.loadPlayers()
  resolveOld([{ player: { id: 'old' }, contracts: [] }])
  await first
  assert.equal(context.rows.value[0].player.id, 'new')
  assert.equal(context.loading.value, false)
})

test('page navigation loads only visible uncached profiles', async () => {
  const all = Array.from({ length: 100 }, (_, i) => ({ player: { id: String(i) }, contracts: [] }))
  const requested = []
  const context = pageFunctions(['loadVisibleProfiles'], {
    listRequestId: 1, profileRequestId: 0, profileCache: new Map(), profileLoading: ref(false), loading: ref(false), loadError: ref(''),
    nameSearch: ref(''), pagedRows: ref(all.slice(0, 50)), rows: ref(all), db: {},
    fetchPlayerProfiles: async (db, ids) => { requested.push([...ids]); return ids.map(id => ({ id, name: `Player ${id}` })) },
  })
  await context.loadVisibleProfiles()
  assert.equal(requested[0].length, 50)
  assert.equal(context.rows.value[50].player.name, undefined)
  context.pagedRows.value = context.rows.value.slice(50)
  await context.loadVisibleProfiles()
  assert.equal(requested[1].length, 50)
  context.pagedRows.value = context.rows.value.slice(0, 50)
  await context.loadVisibleProfiles()
  assert.equal(requested[2].length, 0)
})

test('p_id is the default numeric order while explicit position sorting stays available', () => {
  const env = contractsEnvironment()
  const context = pageFunctions(['sortTableRows'], {
    tableSort: ref('id'), tableSortDirection: ref('asc'), comparePlayerIds: env.comparePlayerIds,
    contractForFilter: row => row.contracts[0], shirtNumber: row => Number(row.contracts[0].no),
  })
  const rows = [
    { player: { id: '0000000100' }, contracts: [{ pos: 'GK', no: 1 }] },
    { player: { id: '0000000002' }, contracts: [{ pos: 'FW', no: 9 }] },
    { player: { id: '0000000020' }, contracts: [{ pos: 'DF', no: 5 }] },
  ]
  assert.equal(context.sortTableRows(rows).map(row => row.player.id).join(','), '0000000002,0000000020,0000000100')
  context.tableSort.value = 'position'
  assert.equal(context.sortTableRows(rows).map(row => row.contracts[0].pos).join(','), 'GK,DF,FW')
  context.tableSort.value = 'number'
  assert.equal(context.sortTableRows(rows).map(row => row.contracts[0].no).join(','), '1,5,9')
})

test('profile response from a previous filter cannot enter the new list or cache', async () => {
  let resolveOld
  const pending = new Promise(resolve => { resolveOld = resolve })
  let requests = 0
  const oldRow = { player: { id: 'old' }, contracts: [] }
  const newRow = { player: { id: 'new' }, contracts: [] }
  const context = pageFunctions(['loadVisibleProfiles'], {
    listRequestId: 1, profileRequestId: 0, profileCache: new Map(), profileLoading: ref(false), loading: ref(false), loadError: ref(''),
    nameSearch: ref(''), pagedRows: ref([oldRow]), rows: ref([oldRow]), db: {},
    fetchPlayerProfiles: async () => ++requests === 1 ? pending : [{ id: 'new', name: 'New player' }],
  })
  const oldRequest = context.loadVisibleProfiles()
  context.listRequestId++
  context.pagedRows.value = [newRow]
  context.rows.value = [newRow]
  await context.loadVisibleProfiles()
  resolveOld([{ id: 'old', name: 'Old player' }])
  await oldRequest
  assert.equal(context.rows.value[0].player.name, 'New player')
  assert.equal(context.profileCache.has('old'), false)
})
