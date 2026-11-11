
<script setup lang="ts">
type Values = {
  name: string; nameEn: string; nameFull: string; birth: string; nation: string
  foot: string; height: number | null; no: string; pos: string; effectiveDate: string; teamId: string
}
type Item = {
  id: string; jobId: string; runId: number; version: number; status: string; reason: string; playerId: string; registerNew: boolean
  target: { action: string; kind: string; team: string; fromTeam?: string; toTeam?: string }
  excel: Values; current: Partial<Values>; values: Values
  candidates: { id: string; name?: string; nameFull?: string; birth?: string }[]
}
type Job = { id: string; runId: number; status: string; targetCount: number; confirmedAt: string }

const { request, requestResponse } = useBackendApi()
const endpoint = '/api/v1/player-drafts'
const jobs = ref<Job[]>([])
const items = ref<Item[]>([])
const selected = ref('')
const memo = ref('')
const progress = ref<{ done: number; total: number; complete: boolean } | null>(null)
let compareController: AbortController | null = null
const busy = ref(false)
const message = ref('')
const error = ref('')
const showHistory = ref(false)
const selectedJobs = computed(() => selected.value === '*' ? jobs.value : jobs.value.filter(job => job.id === selected.value))
const targetTotal = computed(() => selectedJobs.value.reduce((total, job) => total + job.targetCount, 0))
const waiting = computed(() => selectedJobs.value.some(job => job.status === 'pending'))
const comparing = ref(false)
const progressTotal = computed(() => progress.value?.total ?? targetTotal.value)
const progressDone = computed(() => progress.value?.done ?? 0)
const progressPercent = computed(() => progressTotal.value ? Math.round(progressDone.value * 100 / progressTotal.value) : 0)
const editing = ref<Item | null>(null)
const form = ref<Values | null>(null)
const playerId = ref('')
const registerNew = ref(false)
const statusLabel: Record<string, string> = {
  pending: '대조 대기', compared: '대조 완료', deleting: '삭제 중', new: '신규', changed: '불일치',
  same: '동일', review: '확인 필요', stale: '재대조 필요', applied: '반영 완료',
}
const fields: { key: keyof Values; label: string; type?: string }[] = [
  { key: 'name', label: '한글명' }, { key: 'nameEn', label: '영문 약칭' }, { key: 'nameFull', label: '영문 전체 이름' },
  { key: 'birth', label: '생년월일', type: 'date' }, { key: 'nation', label: '국가 코드' },
  { key: 'foot', label: '주발 (L / R / B)' }, { key: 'height', label: '키 (cm)', type: 'number' },
  { key: 'no', label: '등번호' }, { key: 'pos', label: '포지션 (GK / DF / MF / FW)' },
  { key: 'teamId', label: 'JPD-DID 팀 ID (적용일 기준)' }, { key: 'effectiveDate', label: '적용일', type: 'date' },
]
const displayed = computed(() => items.value.filter(item => showHistory.value || !['applied', 'same'].includes(item.status)))
const json = { 'Content-Type': 'application/json' }
const applicable = computed(() => displayed.value.filter(item => ['new', 'changed'].includes(item.status)))

async function act(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  message.value = ''
  error.value = ''
  try { await action() }
  catch (err) { error.value = err instanceof Error ? err.message : String(err) }
  finally { busy.value = false }
}
async function readItems() {
  const results = await Promise.all(selectedJobs.value.map(async job => ({
    job,
    ...await request<{ items: Omit<Item, 'jobId' | 'runId'>[]; memo: string }>(`${endpoint}/${job.id}/items`),
  })))
  items.value = results.flatMap(({ job, items }) => items.map(item => ({ ...item, jobId: job.id, runId: job.runId })))
  memo.value = results.filter(result => result.memo).map(({ job, memo }) =>
    selected.value === '*' ? `#${job.runId}\n${memo}` : memo).join('\n\n')
}
async function refresh() {
  jobs.value = (await request<{ jobs: Job[] }>(endpoint)).jobs
  if ((selected.value !== '*' || !jobs.value.length) && !jobs.value.some(job => job.id === selected.value)) {
    selected.value = (jobs.value.find(job => job.status === 'pending') || jobs.value[0])?.id || ''
  }
  await readItems()
}
function selectJob() {
  editing.value = null
  items.value = []
  memo.value = ''
  progress.value = null
  return act(readItems)
}
async function compareJob(job: Job, signal: AbortSignal, onProgress: (done: number) => void) {
  const response = await requestResponse(`${endpoint}/${job.id}/compare-progress`, { method: 'POST', signal })
  const reader = response.body?.pipeThrough(new TextDecoderStream()).getReader()
  if (!reader) throw new Error('대조 진행률을 받을 수 없습니다.')
  let pending = ''
  let complete = false
  function consume(line: string) {
    if (!line.trim()) return
    const event = JSON.parse(line) as { done?: number; complete?: boolean; error?: string }
    if (event.error) throw new Error(event.error)
    if (typeof event.done === 'number') onProgress(event.done)
    if (event.complete) complete = true
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      const lines = (pending + value).split('\n')
      pending = lines.pop() || ''
      for (const line of lines) consume(line)
    }
    consume(pending)
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
  if (!complete) throw new Error('대조가 끝나기 전에 연결이 끊겼습니다.')
}
function compare() {
  return act(async () => {
    const batch = selectedJobs.value.filter(job => job.status !== 'deleting')
    const total = batch.reduce((sum, job) => sum + job.targetCount, 0)
    if (!total) return
    const controller = new AbortController()
    compareController = controller
    comparing.value = true
    editing.value = null
    progress.value = { done: 0, total, complete: false }
    const failures: string[] = []
    let processed = 0
    try {
      for (const job of batch) {
        let jobDone = 0
        try {
          await compareJob(job, controller.signal, done => {
            jobDone = done
            progress.value = { done: processed + done, total, complete: false }
          })
          job.status = 'compared'
        } catch (err) {
          if (controller.signal.aborted) return
          failures.push(`#${job.runId} · ${err instanceof Error ? err.message : String(err)}`)
        }
        processed += jobDone
      }
      progress.value = { done: processed, total, complete: failures.length === 0 }
      await readItems()
      if (failures.length) throw new Error(failures.join(' / '))
      message.value = '대조 결과를 불러왔습니다.'
    } finally {
      comparing.value = false
      compareController = null
    }
  })
}
function openEdit(item: Item) {
  editing.value = item
  form.value = { ...item.values }
  playerId.value = item.playerId
  registerNew.value = item.registerNew
}
function setValue(key: keyof Values, event: Event) {
  if (!form.value) return
  const value = (event.target as HTMLInputElement).value
  if (key === 'height') form.value.height = value === '' ? null : Number(value)
  else form.value[key] = value
}
function save() {
  return act(async () => {
    const item = editing.value
    if (!item || !form.value) return
    await request(`${endpoint}/${item.jobId}/items/${item.id}`, { method: 'PATCH', headers: json,
      body: JSON.stringify({ version: item.version, values: form.value, playerId: registerNew.value ? '' : playerId.value.trim(), registerNew: registerNew.value }) })
    editing.value = null
    await readItems()
    message.value = '수정값을 저장했습니다.'
  })
}
function apply(item: Item) {
  if (!window.confirm(`${item.values.name || item.values.nameFull} 선수의 변경을 반영할까요?`)) return
  return act(async () => {
    const result = await request<{ status: string; reason?: string }>(`${endpoint}/${item.jobId}/items/${item.id}/apply?version=${item.version}`, { method: 'POST' })
    await readItems()
    message.value = result.status === 'applied' ? '선수 관리에 반영했습니다.' : result.reason || statusLabel[result.status]
  })
}

function applyAll() {
  const reviewed = [...applicable.value].sort((left, right) => left.values.effectiveDate.localeCompare(right.values.effectiveDate))
  if (!reviewed.length || !window.confirm(`현재 목록에서 반영 가능한 ${reviewed.length}건을 선수 관리에 일괄 반영할까요?`)) return
  return act(async () => {
    const batches: [string, Item[]][] = []
    for (const item of reviewed) {
      const last = batches[batches.length - 1]
      if (last?.[0] === item.jobId) last[1].push(item)
      else batches.push([item.jobId, [item]])
    }
    let applied = 0
    const failures: string[] = []
    for (const [jobId, batch] of batches) {
      try {
        const { results } = await request<{ results: { id: string; status: string; reason?: string }[] }>(
          `${endpoint}/${jobId}/apply`, { method: 'POST', headers: json,
            body: JSON.stringify(batch.map(({ id, version }) => ({ id, version }))) })
        for (const result of results) {
          if (result.status === 'applied') applied++
          else {
            const item = batch.find(entry => entry.id === result.id)
            failures.push(`${item?.values.nameFull || item?.values.name || '선수'} · ${result.reason || statusLabel[result.status] || result.status}`)
          }
        }
      } catch (err) {
        failures.push(`전송 #${batch[0]?.runId} · ${err instanceof Error ? err.message : String(err)}`)
      }
    }
    editing.value = null
    await readItems()
    message.value = `일괄 반영 ${applied} / ${reviewed.length}건 완료`
    if (failures.length) error.value = failures.join(' / ')
  })
}

function removeItem(item: Item) {
  if (!window.confirm(`${item.values.name || item.values.nameFull} 선수의 수신 항목을 삭제할까요? 승인으로 반영한 선수·계약에는 영향이 없습니다.`)) return
  return act(async () => {
    await request(`${endpoint}/${item.jobId}/items/${item.id}`, { method: 'DELETE' })
    editing.value = null
    progress.value = null
    await refresh()
    message.value = '수신 항목을 삭제했습니다.'
  })
}
function removeJob() {
  const job = jobs.value.find(item => item.id === selected.value)
  if (!job || !window.confirm(`전송 #${job.runId}와 대조 항목 ${job.targetCount}건을 삭제할까요? 승인으로 반영한 선수·계약에는 영향이 없습니다.`)) return
  return act(async () => {
    await request(`${endpoint}/${job.id}`, { method: 'DELETE' })
    editing.value = null
    progress.value = null
    await refresh()
    message.value = '전송 건을 삭제했습니다.'
  })
}

onMounted(() => act(refresh))
onBeforeUnmount(() => compareController?.abort())
</script>

<template>
  <div class="page">
    <header class="top-bar">
      <NuxtLink class="back" to="/manage">← 데이터 관리</NuxtLink>
      <h1>이적 현황 업데이트</h1>
      <button type="button" class="refresh" :disabled="busy" @click="act(refresh)">수신함 새로고침</button>
    </header>

    <section class="inbox" aria-label="이적 수신함">
      <div class="toolbar">
        <label class="job-field">전송 건
          <select v-model="selected" :disabled="busy || !jobs.length" @change="selectJob">
            <option value="" disabled>{{ jobs.length ? '전송 건 선택' : '수신된 전송 건 없음' }}</option>
            <option v-if="jobs.length" value="*">전체 선택 · 전송 {{ jobs.length }}건</option>
            <option v-for="job in jobs" :key="job.id" :value="job.id">
              #{{ job.runId }} · {{ statusLabel[job.status] || job.status }} · {{ job.targetCount }}건 · {{ job.confirmedAt }}
            </option>
          </select>
        </label>
        <button type="button" class="primary" :disabled="busy || !selectedJobs.some(job => job.status !== 'deleting' && job.targetCount)" @click="compare">신규 이적 불러오기</button>
        <button v-if="selected && selected !== '*'" type="button" class="delete" :disabled="busy" @click="removeJob">전송 건 삭제</button>
        <button v-if="applicable.length" type="button" class="primary" :disabled="busy" @click="applyAll">전체 일괄 반영 · {{ applicable.length }}건</button>
        <label v-if="items.length" class="check"><input v-model="showHistory" type="checkbox"> 동일·반영 완료 표시</label>
      </div>
    </section>
    <section v-if="progressTotal && (progress || waiting)" class="empty" aria-label="대조 진행률">
      <h2>{{ comparing ? (progressDone ? '선수 DB 대조 중' : '선수 후보 조회 중') : progress?.complete ? '대조 완료' : progress ? '일부 대조가 완료되지 않았습니다' : '선택한 전송 건이 대조를 기다리고 있습니다' }}</h2>
      <div class="progress-line">
        <progress :value="progressDone" :max="progressTotal" aria-label="선수 DB 대조 진행률" />
        <span role="status">{{ progressDone }} / {{ progressTotal }}건 · {{ progressPercent }}%</span>
      </div>
      <p v-if="waiting && !progress"><strong>신규 이적 불러오기</strong>를 눌러 대조를 시작하세요.</p>
    </section>
    <p v-if="busy && !comparing" class="notice" role="status">처리 중입니다.</p>
    <p v-if="message" class="notice success" role="status">{{ message }}</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <details v-if="memo"><summary>Transfer Ops 작업 이력 메모</summary><pre class="memo">{{ memo }}</pre></details>
    <div v-if="displayed.length" class="table">
      <table>
        <thead><tr><th>선수</th><th>변경</th><th>DB 팀 / 등번호</th><th>엑셀 팀 / 등번호</th><th>대조 결과</th><th>처리</th></tr></thead>
        <tbody>
          <tr v-for="item in displayed" :key="item.jobId + ':' + item.id">
            <td>{{ item.values.name || item.values.nameFull }}<br>{{ item.values.nameFull }}<small v-if="selected === '*'"> · #{{ item.runId }}</small></td>
            <td>{{ item.target.kind }} · {{ item.values.effectiveDate }}<br>{{ item.target.fromTeam }} {{ item.target.toTeam ? '→ ' + item.target.toTeam : item.target.team }}</td>
            <td>{{ item.current.teamId || '—' }} / {{ item.current.no || '—' }}</td>
            <td>{{ item.target.team || '—' }} / {{ item.excel.no || '—' }}</td>
            <td>{{ statusLabel[item.status] || item.status }}<br>{{ item.reason }}</td>
            <td class="actions">
              <button v-if="item.status !== 'applied'" :disabled="busy" @click="openEdit(item)">상세·수정</button>
              <button v-if="['new', 'changed'].includes(item.status)" :disabled="busy" @click="apply(item)">선수 관리에 반영</button>
              <button type="button" class="delete" :disabled="busy" @click="removeItem(item)">삭제</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <section v-else-if="!busy && !error && !waiting" class="empty" aria-label="수신함 상태">
      <template v-if="!jobs.length">
        <h2>아직 받은 이적 작업이 없습니다</h2>
        <p>Transfer Ops의 작업 이력에서 <strong>JPD-DID로 전송하기</strong>를 누른 뒤 수신함을 새로고침하세요.</p>
      </template>
      <template v-else>
        <h2>반영할 이적 항목이 없습니다</h2>
        <p v-if="items.length && !showHistory">완료된 항목은 <strong>동일·반영 완료 표시</strong>에서 확인할 수 있습니다.</p>
      </template>
    </section>
    <form v-if="editing && form" class="editor" @submit.prevent="save">
      <h2>{{ editing.values.name || editing.values.nameFull }} · 반영할 값 수정</h2>
      <div class="toolbar">
        <label>기존 선수 ID
          <input v-model="playerId" :disabled="busy || registerNew" :list="'draft-player-options'" placeholder="후보 선택 또는 선수 ID 입력">
          <datalist id="draft-player-options">
            <option v-for="candidate in editing.candidates" :key="candidate.id" :value="candidate.id">
              {{ candidate.name }} {{ candidate.nameFull }} {{ candidate.birth }}
            </option>
          </datalist>
        </label>
        <label v-if="editing.target.action === 'team'"><input v-model="registerNew" :disabled="busy" type="checkbox"> 신규 선수로 등록</label>
      </div>
      <div class="table">
        <table>
          <thead><tr><th>항목</th><th>현재 DB</th><th>확정 엑셀</th><th>반영할 값</th></tr></thead>
          <tbody>
            <tr v-for="field in fields" :key="field.key">
              <th scope="row"><label :for="'transfer-' + field.key">{{ field.label }}</label></th>
              <td>{{ editing.current[field.key] ?? '—' }}</td>
              <td>{{ editing.excel[field.key] ?? '—' }}</td>
              <td><input :id="'transfer-' + field.key" :type="field.type || 'text'" :value="form[field.key] ?? ''" :disabled="busy"
                @input="setValue(field.key, $event)"></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="toolbar"><button type="submit" :disabled="busy">수정 저장</button><button type="button" :disabled="busy" @click="editing = null">닫기</button></div>
    </form>
  </div>
</template>

<style scoped>
.page {
  box-sizing: border-box;
  height: 800px;
  padding: 36px 48px 48px;
  overflow: auto;
  color-scheme: dark;
  color: #eef2f7;
  background: #0b0f17;
  font-family: Arial, "Noto Sans KR", sans-serif;
}
.top-bar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid #29313b;
}
h1 { margin: 0; font-size: 26px; }
h2 { margin: 0 0 8px; font-size: 18px; }
.back { justify-self: start; color: #aebdce; font-size: 14px; font-weight: 600; text-decoration: none; }
.back:hover { color: #8ce6f5; }
.refresh { justify-self: end; color: #8ce6f5; }
.inbox, .empty, .editor, details, .table {
  border: 1px solid #2d3847;
  border-radius: 8px;
  background: #111824;
}
.inbox, .editor, details { padding: 16px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
.job-field { display: grid; gap: 6px; flex: 1 1 300px; min-width: 0; }
.job-field, .check { color: #c1cddd; font-size: 14px; }
.check { display: inline-flex; align-items: center; gap: 6px; min-height: 38px; }
input:not([type=checkbox]), select, button {
  box-sizing: border-box;
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid #4a5a70;
  border-radius: 5px;
  color: inherit;
  background: #182233;
  font: inherit;
}
input::placeholder { color: #9ca9b8; }
input[type=checkbox] { accent-color: #8ce6f5; }
button { font-size: 14px; font-weight: 700; cursor: pointer; }
button:not(:disabled):hover { border-color: #8ce6f5; }
.primary { color: #0b1621; background: #8ce6f5; border-color: #8ce6f5; }
button:disabled, select:disabled, input:disabled {
  color: #a0adbe;
  background: #1b2533;
  border-color: #344255;
  cursor: not-allowed;
  opacity: 1;
}
:focus-visible { outline: 2px solid #8ce6f5; outline-offset: 2px; }
.empty { margin-top: 16px; padding: 28px; }
.progress-line { display: flex; align-items: center; gap: 12px; margin: 12px 0; }
.progress-line progress { flex: 1; min-width: 0; height: 12px; accent-color: #8ce6f5; }
.progress-line span { white-space: nowrap; font-size: 14px; font-variant-numeric: tabular-nums; }
.empty p { margin: 0; color: #aebdce; font-size: 14px; line-height: 1.6; }
.notice { padding: 12px 16px; border-radius: 6px; color: #c1cddd; background: #182233; }
.success { color: #a6f4cf; }
.error { color: #ffd0d0; background: #352027; overflow-wrap: anywhere; }
details, .table, .editor { margin-top: 16px; }
summary { cursor: pointer; font-weight: 700; }
.memo { margin-bottom: 0; color: #c1cddd; white-space: pre-wrap; overflow-wrap: anywhere; }
.table { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
thead { background: #182233; }
td, th { padding: 10px 12px; border-bottom: 1px solid #2d3847; text-align: left; vertical-align: top; }
tbody tr:last-child > * { border-bottom: 0; }
.actions { min-width: 12rem; }
.actions button { margin: 2px 4px 2px 0; }
.delete:not(:disabled) { color: #ffb8bd; }
.delete:not(:disabled):hover { border-color: #ffb8bd; }
.editor > .toolbar { margin-top: 12px; }
</style>
