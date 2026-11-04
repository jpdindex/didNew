const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const vue = require('vue')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')
const plain = value => JSON.parse(JSON.stringify(value))
const settle = async () => { await vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await vue.nextTick() }
function component(file, globals, exports) {
  const source = fs.readFileSync(file, 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
    .replace(/^import .*\r?\n/gm, '')
  const context = vm.createContext({ Error, ref: vue.ref, computed: vue.computed, watch: vue.watch, ...globals })
  vm.runInContext(transformSync(source + `\nglobalThis.state = { ${exports.join(',')} };`, { loader: 'ts' }).code, context)
  return context.state
}
const emptyNumber = () => ({ leagueId: '', seasonId: '', teamId: '', playerId: '', contractId: '', currentNo: '', newNo: '', changeDate: '' })

test('number form loads season roster and displays the number effective on the change date', async () => {
  const model = vue.ref({ ...emptyNumber(), leagueId: 'EPL', seasonId: '20262027', teamId: 'T' })
  const calls = []
  const state = component('app/components/PlayerNumberChangeForm.vue', {
    defineModel: () => model, defineProps: () => ({}), useNuxtApp: () => ({ $db: {} }),
    fetchPlayersByTeamsAndSeason: async (_, ids, season) => {
      calls.push({ ids: plain(ids), season })
      return [{ player: { id: 'P', name: 'Player' }, contracts: [
        { id: 'new', teamId: 'T', from: '2026.10.01', to: null, no: '20' },
        { id: 'old', teamId: 'T', from: '2026.08.01', to: '2026.09.30', no: '10' },
      ] }]
    },
  }, ['players', 'selectPlayer', 'updateCurrentNumber'])
  await settle()
  assert.deepEqual(calls, [{ ids: ['T'], season: '2026/27' }])
  assert.equal(state.players.value.length, 1)
  state.selectPlayer('P')
  assert.equal(model.value.currentNo, '20')
  model.value.changeDate = '2026-09-01'
  await settle()
  assert.equal(model.value.currentNo, '10')
  assert.equal(model.value.contractId, 'old')
  model.value.changeDate = '2026-07-01'
  await settle()
  assert.equal(model.value.contractId, '')
})

test('late roster response cannot replace another team or keep its selected player', async () => {
  const model = vue.ref({ ...emptyNumber(), leagueId: 'EPL', seasonId: '20262027', teamId: 'OLD', playerId: 'P', contractId: 'C', currentNo: '10' })
  let finishOld
  const state = component('app/components/PlayerNumberChangeForm.vue', {
    defineModel: () => model, defineProps: () => ({}), useNuxtApp: () => ({ $db: {} }),
    fetchPlayersByTeamsAndSeason: async (_, ids) => ids[0] === 'OLD' ? new Promise(resolve => { finishOld = resolve }) :
      [{ player: { id: 'new', name: 'New' }, contracts: [{ id: 'N', teamId: 'NEW' }] }],
  }, ['players'])
  model.value.teamId = 'NEW'
  await settle()
  assert.equal(model.value.playerId, '')
  finishOld([{ player: { id: 'P' }, contracts: [{ teamId: 'OLD' }] }])
  await settle()
  assert.equal(state.players.value[0].player.id, 'new')
})

function requestPage(duplicateResult = { players: [] }) {
  const requests = []
  const state = component('app/pages/manage/player-requests/index.vue', {
    emptyPlayerNumberChange: emptyNumber,
    emptyPlayerRegistration: () => ({ leagueId: '', seasonId: '', teamId: '', matchId: '', nation: '', sourceUrl: '' }),
    useNuxtApp: () => ({ $auth: { currentUser: { email: 'manager' } }, $db: {}, $authReady: Promise.resolve() }),
    canAccessManagement: () => true, useRoute: () => ({ query: {} }), onMounted: () => {},
    useBackendApi: () => ({ request: async (url, options) => {
      requests.push({ url, options })
      if (url.endsWith('/duplicates')) {
        if (duplicateResult instanceof Error) throw duplicateResult
        return duplicateResult
      }
      return { requests: [] }
    } }),
  }, ['startNew', 'open', 'submit', 'review', 'form', 'numberForm', 'requestType', 'requestLabel', 'requestDetail', 'error',
    'checkDuplicates', 'duplicates', 'duplicateChecked', 'duplicatePlayerId', 'existingPlayerId'])
  return { state, requests }
}

test('request type menu opens number form; submission sends only number fields and scope', async () => {
  const { state, requests } = requestPage()
  await state.startNew('NUMBER_CHANGE')
  state.form.value = { ...state.form.value, leagueId: 'EPL', seasonId: '20262027', teamId: 'T' }
  await settle()
  Object.assign(state.numberForm.value, { playerId: 'P', contractId: 'C', currentNo: '10', newNo: '20', changeDate: '2026-10-01' })
  await state.submit()
  const body = JSON.parse(requests[0].options.body)
  assert.deepEqual(body, { leagueId: 'EPL', seasonId: '20262027', teamId: 'T', playerId: 'P', contractId: 'C', currentNo: '10', newNo: '20', changeDate: '2026-10-01' })
  assert.equal(state.error.value, '')
})

test('undated number changes can be requested but approval requires the manager to enter a date', async () => {
  const { state, requests } = requestPage()
  await state.startNew('NUMBER_CHANGE')
  state.form.value = { ...state.form.value, leagueId: 'EPL', seasonId: '20262027', teamId: 'T' }
  await settle()
  Object.assign(state.numberForm.value, { playerId: 'P', contractId: 'C', currentNo: '10', newNo: '20' })
  await state.submit()
  assert.equal(JSON.parse(requests[0].options.body).changeDate, '')
  const item = { id: 'R', requestType: 'NUMBER_CHANGE', status: 'pending', version: 1,
    payload: { ...plain(state.numberForm.value) } }
  state.open(item)
  await settle()
  assert.match(state.requestDetail(item), /날짜 미입력/)
  await state.review('approve')
  assert.match(state.error.value, /변경 날짜/)
  assert.equal(requests.length, 2) // submission and list reload only
  state.numberForm.value.changeDate = '2026-10-01'
  await state.review('approve')
  assert.equal(JSON.parse(requests[2].options.body).payload.changeDate, '2026-10-01')
})

test('mixed request list keeps legacy registration readable and approves number change without duplicate search', async () => {
  const { state, requests } = requestPage()
  const item = { id: 'R', requestType: 'NUMBER_CHANGE', status: 'pending', version: 1, playerName: 'Player',
    payload: { ...emptyNumber(), leagueId: 'EPL', seasonId: '20262027', teamId: 'T', playerId: 'P', contractId: 'C', currentNo: '10', newNo: '20', changeDate: '2026-10-01' } }
  state.open(item)
  await settle()
  assert.equal(state.requestLabel(item), '등번호 변경')
  assert.match(state.requestDetail(item), /10 → 20번/)
  await state.review('approve')
  assert.equal(requests[0].url, '/api/v1/player-requests/R/review')
  assert.equal(JSON.parse(requests[0].options.body).payload.playerId, 'P')
  const legacy = { id: 'L', status: 'pending', payload: { name: 'Old', nameFull: 'Old Player', no: '9', pos: 'FW', fromDate: '' } }
  state.open(legacy)
  assert.equal(state.requestLabel(legacy), '신규 선수 등록')
  assert.equal(state.form.value.name, 'Old')
})

test('FULLNAME lookup sends optional p_id and approval requires the confirmed candidate p_id', async () => {
  const { state, requests } = requestPage({ players: [{ id: '0000000042', nameFull: 'Full Name', birth: '1990.01.01' }] })
  state.open({ id: 'R', status: 'pending', version: 1, payload: { name: 'Display', nameFull: 'Full Name' } })
  state.duplicatePlayerId.value = '42'
  await settle()
  await state.checkDuplicates()
  assert.equal(JSON.parse(requests[0].options.body).lookupPlayerId, '42')
  assert.equal(state.duplicateChecked.value, true)
  await state.review('approve')
  assert.equal(requests.length, 1)
  assert.match(state.error.value, /p_id/)
  state.existingPlayerId.value = '0000000042'
  await state.review('approve')
  assert.equal(JSON.parse(requests[1].options.body).existingPlayerId, '0000000042')
  state.form.value.nameFull = 'Changed Full Name'
  await settle()
  assert.equal(state.duplicateChecked.value, false)
  assert.equal(state.existingPlayerId.value, '')
})

test('failed p_id verification cannot retain a previously confirmed duplicate check', async () => {
  const { state } = requestPage(new Error('p_id FULLNAME mismatch'))
  state.duplicateChecked.value = true
  state.existingPlayerId.value = 'old'
  await state.checkDuplicates()
  assert.equal(state.duplicateChecked.value, false)
  assert.equal(state.existingPlayerId.value, '')
  assert.match(state.error.value, /mismatch/)
})
