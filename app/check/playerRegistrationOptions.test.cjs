const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')

const moduleState = { exports: {} }
vm.runInNewContext(transformSync(fs.readFileSync('app/utils/playerRegistrationOptions.ts', 'utf8'), { loader: 'ts', format: 'cjs' }).code,
  { module: moduleState, exports: moduleState.exports })
const helpers = moduleState.exports
const plain = value => JSON.parse(JSON.stringify(value))
const ref = value => ({ value })

test('season aliases and league-specific documents merge into one latest-first option', () => {
  const options = helpers.registrationSeasonOptions([
    { id: '20262027', leagueId: 'EPL' }, { id: '2026-2027', leagueId: 'LALIGA' },
    { id: '2026/27', leagueId: 'EPL' }, { id: '20252026' }, { id: '20262028' }, { id: 'bad' },
  ])
  assert.deepEqual(plain(options), [
    { id: '20262027', label: '2026-2027', leagueIds: ['EPL', 'LALIGA'] },
    { id: '20252026', label: '2025-2026', leagueIds: [] },
  ])
})

test('only explicit season D1 league membership qualifies; cup and current caches do not', () => {
  for (const entry of [undefined, { leagueId: 'EPL', division: 'D2' }, { leagueId: 'EPL' },
    { division: 'D1', leagueId: 'EFL', competitionIds: ['EPL'], cupIds: ['EPL'] },
    { division: 'D1', leagueId: 'EPL', leagueIds: ['EFL'] }]) {
    assert.equal(helpers.isRegistrationFirstDivision(entry, 'EPL'), false)
  }
  assert.equal(helpers.isRegistrationFirstDivision({ division: 'D1', leagueId: 'EPL' }, 'EPL'), true)
  assert.equal(helpers.isRegistrationFirstDivision({ division: 'D1', leagueIds: ['LALIGA'] }, 'LALIGA'), true)
})

function formEnvironment(initial = {}) {
  let mounted
  const model = ref({ leagueId: '', seasonId: '', teamId: '', no: '', ...initial })
  const entries = new Map()
  const teams = []
  for (const league of ['EPL', 'LALIGA']) {
    for (let i = 0; i < 20; i++) {
      const id = `${league}-${i}`
      teams.push({ id, name: id })
      entries.set(`${id}/20262027`, { leagueId: league, division: 'D1' })
    }
    teams.push({ id: `${league}-D2`, name: 'Second division', currentLeagueId: league })
    entries.set(`${league}-D2/20262027`, { leagueId: league, division: 'D2', competitionIds: [league] })
    teams.push({ id: `${league}-missing`, name: 'Missing season', currentLeagueId: league })
  }
  const source = fs.readFileSync('app/components/PlayerRegistrationForm.vue', 'utf8')
    .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import .*\r?\n/gm, '')
  const context = vm.createContext({ ...helpers, defineModel: () => model, defineProps: () => ({}),
    useNuxtApp: () => ({ $db: {} }), ref, computed: fn => ({ get value() { return fn() } }),
    watch: () => {}, onMounted: fn => { mounted = fn },
    collection: (_, name) => name, doc: (_, ...parts) => parts,
    getDocs: async name => ({ docs: (name === 'teams' ? teams : name === 'leagues' ?
      [{ id: 'EPL' }, { id: 'LALIGA' }] : [{ id: '20262027', leagueId: 'EPL' },
        { id: '2026-2027', leagueId: 'LALIGA' }, { id: '20252026' }])
      .map(row => ({ id: row.id, data: () => row })) }),
    getDoc: async parts => ({ data: () => entries.get(`${parts[1]}/${parts[3]}`) }),
  })
  vm.runInContext(transformSync(source + '\nglobalThis.formState = { model, seasons, seasonOptions, eligibleTeams, teamWarning, loadTeams };', { loader: 'ts' }).code, context)
  return { state: context.formState, mount: () => mounted(), entries }
}

test('new request defaults to latest season and each league shows exactly its 20 D1 teams', async () => {
  const { state, mount } = formEnvironment()
  await mount()
  assert.equal(state.model.value.seasonId, '20262027')
  assert.equal(state.seasonOptions.value.length, 2)
  assert.equal(state.eligibleTeams.value.length, 20)
  assert.ok(state.eligibleTeams.value.every(team => team.id.startsWith('EPL-')))
  state.model.value.leagueId = 'LALIGA'
  await state.loadTeams()
  assert.equal(state.eligibleTeams.value.length, 20)
  assert.ok(state.eligibleTeams.value.every(team => team.id.startsWith('LALIGA-')))
  assert.equal(state.teamWarning.value, '')
})

test('missing season data gives an explicit count warning instead of filling from current league', async () => {
  const { state, mount, entries } = formEnvironment({ leagueId: 'EPL', teamId: 'EPL-0' })
  entries.delete('EPL-0/20262027')
  await mount()
  assert.equal(state.eligibleTeams.value.length, 19)
  assert.equal(state.model.value.teamId, '')
  assert.match(state.teamWarning.value, /19팀/)
  state.model.value.seasonId = '20252026'
  await state.loadTeams()
  assert.equal(state.eligibleTeams.value.length, 0)
})

test('match or review supplied season is preserved instead of replaced by latest', async () => {
  const { state, mount } = formEnvironment({ leagueId: 'EPL', seasonId: '20252026', matchId: 'match' })
  await mount()
  assert.equal(state.model.value.seasonId, '20252026')
})
