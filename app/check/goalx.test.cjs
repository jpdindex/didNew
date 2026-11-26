const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { ref, computed, nextTick } = require('vue')
const XLSX = require('xlsx')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')

test('manual stopwatch stops, permits correction, and clears when switching goals', () => {
  const utility = { exports: {} }
  vm.runInNewContext(transformSync(fs.readFileSync('app/utils/goalx.ts', 'utf8'), {
    loader: 'ts', format: 'cjs',
  }).code, { module: utility, exports: utility.exports })
  const source = fs.readFileSync('app/pages/manage/goalx/index.vue', 'utf8')
  const state = source.slice(source.indexOf('const curveDirection ='), source.indexOf("const search = ref('')"))
  let now = 0
  const ticks = new Map()
  const context = vm.createContext({
    ...utility.exports, ref, computed, draft: ref({ trajectory: 'medium' }),
    performance: { now: () => now },
    setInterval: callback => { ticks.set(1, callback); return 1 },
    clearInterval: id => ticks.delete(id),
  })
  const expose = 'globalThis.controls = { resetEstimate, toggleStopwatch, estimateDraft, estimatedPreview, timerRunning, estimateInvalid, curveDirection, curveDirectionValid };'
  const fields = source.slice(source.indexOf('const coordinateFields ='), source.indexOf('const categories:'))
  vm.runInContext(transformSync(fields + state + expose, { loader: 'ts' }).code, context)
  const c = context.controls
  c.resetEstimate({ rawCoordinates: { posX: 80, posY: 50, shootPosX: 273.5, shootPosY: 300 } })
  assert.equal(c.estimatedPreview.value, null)
  assert.equal(c.curveDirectionValid.value, false)
  c.curveDirection.value = 'left'
  assert.equal(c.estimatedPreview.value.averageSpeed, null)
  c.toggleStopwatch()
  assert.equal(c.timerRunning.value, true)
  assert.equal(c.estimateInvalid.value, true)
  now = 500
  ticks.get(1)()
  c.toggleStopwatch()
  assert.equal(c.estimateDraft.value.timeToGoal, .5)
  const initialSpeed = c.estimatedPreview.value.averageSpeed
  c.estimateDraft.value.timeToGoal = 1
  assert.equal(c.estimatedPreview.value.averageSpeed, initialSpeed / 2)
  context.draft.value.trajectory = 'low'
  c.curveDirection.value = null
  assert.equal(c.curveDirectionValid.value, true)
  assert.equal(c.estimatedPreview.value.input.curveDirection, null)
  context.draft.value.trajectory = 'medium'
  assert.equal(c.curveDirectionValid.value, false)
  assert.equal(c.estimatedPreview.value, null)
  c.curveDirection.value = 'right'
  assert.equal(c.curveDirectionValid.value, true)
  assert.equal(c.estimatedPreview.value.input.curveDirection, 'right')
  c.toggleStopwatch()
  c.resetEstimate()
  assert.equal(c.timerRunning.value, false)
  assert.equal(ticks.size, 0)
  assert.equal(c.estimateDraft.value.timeToGoal, null)
  assert.equal(c.estimatedPreview.value, null)
})

test('selecting a goal loads coordinates directly from RAW without ShotX', async () => {
  const source = fs.readFileSync('app/pages/manage/goalx/index.vue', 'utf8')
  const fields = source.slice(source.indexOf('const coordinateFields ='), source.indexOf('const categories:'))
  const state = source.slice(source.indexOf('const curveDirection ='), source.indexOf("const search = ref('')"))
  const coordinates = { posX: .10298661174047373, posY: 52.208201892744476, shootPosX: 211, shootPosY: 275, goalX: 105 }
  const keyOf = goal => goal.recordId
  const selectedKey = ref('goal-1')
  let resolveRequest
  let requestedPath
  const context = vm.createContext({
    ref, computed, AbortController, selectedKey, keyOf, draft: ref({ trajectory: 'medium' }),
    validEstimatedCoordinates: () => true, pathOf: goal => '/goals/' + goal.recordId,
    request: path => { requestedPath = path; return new Promise(resolve => { resolveRequest = resolve }) },
    clearInterval: () => {}, errorMessage: error => error.message,
  })
  vm.runInContext(transformSync(fields + state + 'globalThis.controls = { resetEstimate, loadRawCoordinates, estimateDraft, automaticGoalX };', { loader: 'ts' }).code, context)
  const c = context.controls
  const goal = { recordId: 'goal-1' }
  c.resetEstimate(goal)
  const pending = c.loadRawCoordinates(goal)
  assert.equal(requestedPath, '/goals/goal-1/raw-coordinates')
  // A manually edited value must survive the asynchronous fetch.
  c.estimateDraft.value.shootPosY = 250
  resolveRequest({ coordinates })
  await pending
  assert.equal(c.estimateDraft.value.posX, coordinates.posX)
  assert.equal(c.estimateDraft.value.posY, coordinates.posY)
  assert.equal(c.estimateDraft.value.shootPosX, 211)
  assert.equal(c.estimateDraft.value.shootPosY, 250)
  assert.equal(c.automaticGoalX.value, 105)
  c.estimateDraft.value.posX = 99
  assert.equal(c.automaticGoalX.value, 105)
  assert.deepEqual(goal.rawCoordinates, coordinates)
  c.resetEstimate(goal)
  const stale = c.loadRawCoordinates(goal)
  selectedKey.value = 'goal-2'
  c.resetEstimate()
  resolveRequest({ coordinates })
  await stale
  assert.equal(c.estimateDraft.value.posX, null)
})

test('export preserves every analyst, timing, numeric coordinates and approved representative in xlsx', () => {
  const module = { exports: {} }
  vm.runInNewContext(transformSync(fs.readFileSync('app/utils/goalx.ts', 'utf8'), {
    loader: 'ts', format: 'cjs',
  }).code, { module, exports: module.exports })
  const shot = { input: { posX: 80, posY: 50, shootPosX: 273.5, shootPosY: 300, goalX: 105, timeToGoal: .6, curveDirection: 'left' },
    distance: 22, averageSpeed: 132, curveWeight: .1, modelVersion: 1,
    trajectory: [{ x: 84, y: 34, z: 0 }, { x: 105, y: 34, z: 0 }] }
  const record = { gmId: 'match', side: 'A', recordId: 'goal',
    evaluations: [{ id: 'a', analystName: 'A', grades: { trajectory: 'medium' }, estimatedShot: shot },
      { id: 'b', analystName: 'B', grades: { trajectory: 'low' }, estimatedShot: { ...shot, input: { ...shot.input, timeToGoal: .7 } } }],
    representativeStatus: 'needs_review', representative: { approvedBy: 'manager', approvedAt: '2026-11-24',
      trajectory: 'medium', timeToGoal: .65, timeSampleCount: 2, score: 8,
      categoryScores: { artistry: 3, defense: 3, trajectory: 2 }, estimatedShot: shot, sourceVersion: 'v1', shotId: 'shotx-id' } }
  const labels = new Map([[JSON.stringify(['match', 'A', 'goal']), ['match', 'A', 'goal', '선수', '43:09']]])
  const sheets = module.exports.goalxEstimationSheets([record, { ...record, recordId: 'hidden' }], labels)
  const book = XLSX.utils.book_new()
  for (const sheet of sheets) XLSX.utils.book_append_sheet(book, XLSX.utils.aoa_to_sheet(sheet.rows), sheet.name)
  const reopened = XLSX.read(XLSX.write(book, { type: 'buffer', bookType: 'xlsx' }), { type: 'buffer' })
  const rows = XLSX.utils.sheet_to_json(reopened.Sheets['추정 3D 분석'])
  assert.equal(rows.length, 3)
  assert.equal(rows[0]['골라인 통과 소요시간 (초)'], .6)
  assert.equal(rows[1]['골라인 통과 소요시간 (초)'], .7)
  assert.equal(rows[0]['슈팅 X (%)'], 80)
  assert.equal(rows[0]['추정 평균 속도 (km/h)'], 132)
  assert.equal(rows[0]['커브 방향 (슈팅자 기준)'], '좌')
  assert.equal(rows[1]['커브 방향 (슈팅자 기준)'], '')
  assert.equal(XLSX.utils.sheet_to_json(reopened.Sheets['추정 궤적 좌표']).length, 6)
  assert.equal(XLSX.utils.sheet_to_json(reopened.Sheets['대표 승인'])[0]['승인 상태'], '재검토 필요')
  assert.equal(XLSX.utils.sheet_to_json(reopened.Sheets['대표 승인'])[0]['커브 방향 (슈팅자 기준)'], '좌')
})

function setupBulkApproval(request){
  const goals=Array.from({length:12},(_,i)=>({gmId:'game',side:'H',recordId:String(i)}))
  const patched=[];let shown=0,closed=0
  const state={canApproveRepresentative:ref(true),bulkApproving:ref(false),ranking:ref(goals),bulkTargets:ref([]),bulkProcessed:ref(0),bulkError:ref(''),bulkIssues:ref([]),bulkSummary:ref(),bulkDialog:ref({showModal(){shown++},close(){closed++}})}
  const context=vm.createContext({...state,nextTick,api:'/api/v1/goalx',request,patchGoal:(g,a)=>patched.push([g,a]),errorMessage:e=>e.message})
  const source=fs.readFileSync('app/pages/manage/goalx/index.vue','utf8')
  const functions=source.slice(source.indexOf('async function confirmBulkApproval()'),source.indexOf('async function openRepresentative('))
  vm.runInContext(transformSync(functions+'\nglobalThis.apiTest={confirmBulkApproval,approveBulk}',{loader:'ts'}).code,context)
  return {state,controls:context.apiTest,patched,shown:()=>shown,closed:()=>closed}
}
test('confirmation does not write; confirmed approval batches current ranking and reports exclusions',async()=>{
  const calls=[]
  const test=setupBulkApproval(async(path,opts)=>{const goals=JSON.parse(opts.body).goals;calls.push(goals);return{results:goals.map(g=>({...g,status:g.recordId==='1'?'skipped':'approved',reason:'좌표 확인',analysis:{score:90}}))}})
  await test.controls.confirmBulkApproval()
  assert.equal(test.shown(),1);assert.equal(calls.length,0)
  await test.controls.approveBulk()
  assert.deepEqual(calls.map(g=>g.length),[10,2]);assert.equal(test.patched.length,11)
  assert.equal(test.state.bulkSummary.value.approved,11);assert.equal(test.state.bulkSummary.value.skipped,1)
  assert.equal(test.state.bulkIssues.value.length,1);assert.equal(test.state.bulkProcessed.value,12);assert.equal(test.closed(),1)
})
test('non-manager cannot confirm or submit bulk approval',async()=>{
  let calls=0;const test=setupBulkApproval(async()=>{calls++;return{results:[]}})
  test.state.canApproveRepresentative.value=false
  await test.controls.confirmBulkApproval();await test.controls.approveBulk()
  assert.equal(calls,0);assert.equal(test.shown(),0)
})
test('network failure preserves completed count and shows an error without retrying writes',async()=>{
  let calls=0;const test=setupBulkApproval(async(path,opts)=>{calls++;if(calls===2)throw Error('network');return{results:JSON.parse(opts.body).goals.map(g=>({...g,status:'approved',analysis:{}}))}})
  await test.controls.confirmBulkApproval();await test.controls.approveBulk()
  assert.equal(calls,2);assert.equal(test.state.bulkSummary.value.approved,10)
  assert.match(test.state.bulkError.value,/network/);assert.equal(test.state.bulkApproving.value,false)
})
