<script setup lang="ts">
type BackendState = 'idle' | 'checking' | 'ready' | 'unavailable'
type ImportJobState = 'idle' | 'queued' | 'preparing' | 'preflight' | 'resuming' | 'replacing' | 'writing' | 'cleaning' | 'running' | 'complete' | 'failed'
interface ImportJob { status: ImportJobState; matchesTotal: number; matchesProcessed: number; documentsTotal: number; documentsWritten: number; recordsWritten: number; percent: number; error?: string | null }
interface PlayerUpdateJob { status: ImportJobState; rowsChecked: number; playersProcessed: number; playersTotal: number; playersCreated: number; playersUpdated: number; contractsCreated: number; contractsUpdated: number; squadsUpdated: number; playersSkipped: number; unchanged: number; issues: { playerId: string; reason: string }[]; percent: number; error?: string | null }
type Mode = 'full' | 'players'

const config = useRuntimeConfig()
const mode = ref<Mode>('full')
const paths = { full: '/api/v1/legacy-import', players: '/api/v1/legacy-import/player-update' }
const koreaNow = new Date(Date.now() + 9 * 60 * 60 * 1000)
const seasonYear = koreaNow.getUTCFullYear() - (koreaNow.getUTCMonth() < 7 ? 1 : 0)
const season = ref(`${seasonYear}-${seasonYear + 1}`)
const selectedFile = ref<File | null>(null)
const backendUrl = ref('')
const localApiKey = ref('')
const apiKeyVisible = ref(false)
const backendState = ref<BackendState>('idle')
const statusMessage = ref('백엔드 상태를 확인 중입니다.')
const importJob = ref<ImportJob | PlayerUpdateJob | null>(null)
const errorMessage = ref('')
const busy = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let mounted = true

function backendCandidates() {
  const configured = String(config.public.backendUrl || '').trim().replace(/\/$/, '')
  return [...new Set([backendUrl.value, configured, ...Array.from({ length: 100 }, (_, index) => `http://127.0.0.1:${8000 + index}`)].filter(Boolean))]
}
async function hasImportApi(url: string, endpoint: string) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 1500)
  try {
    if (!(await fetch(`${url}/health`, { signal: controller.signal })).ok) return false
    const response = await fetch(`${url}/openapi.json`, { signal: controller.signal })
    if (!response.ok) return false
    const schema = await response.json() as { paths?: Record<string, unknown> }
    return Boolean(schema.paths?.[endpoint] && schema.paths?.[`${endpoint}/status`])
  } catch { return false } finally { clearTimeout(timeout) }
}
async function checkBackend() {
  backendState.value = 'checking'; errorMessage.value = ''
  const endpoint = paths[mode.value]
  for (const candidate of backendCandidates()) {
    if (!mounted) return false
    if (await hasImportApi(candidate, endpoint)) {
      backendUrl.value = candidate; backendState.value = 'ready'
      statusMessage.value = `백엔드와 Firestore가 준비되었습니다. (${candidate})`
      return true
    }
  }
  backendState.value = 'unavailable'; statusMessage.value = '선택한 기능을 제공하는 백엔드가 연결되어 있는지 확인하세요.'
  return false
}
async function selectMode(next: Mode) {
  if (busy.value || backendState.value === 'checking' || mode.value === next) return
  mode.value = next
  selectedFile.value = null
  importJob.value = null
  await checkBackend()
}
function selectFile(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  importJob.value = null; errorMessage.value = ''
}
function progressText(job: ImportJob | PlayerUpdateJob) {
  if ('rowsChecked' in job) return `Player 문서 ${job.rowsChecked}건 · 선수 ${job.playersProcessed}/${job.playersTotal}명 · 신규 선수 ${job.playersCreated}명 · 선수 갱신 ${job.playersUpdated}명 · 신규 계약 ${job.contractsCreated}건 · 계약 갱신 ${job.contractsUpdated}건 · 팀 명단 ${job.squadsUpdated}건 · 변경 없음 ${job.unchanged}명 · 확인 필요 ${job.playersSkipped}건`
  return `경기 ${job.matchesProcessed} / ${job.matchesTotal} · 문서 ${job.documentsWritten} / ${job.documentsTotal}`
}
async function readStatus() {
  const response = await fetch(`${backendUrl.value}${paths[mode.value]}/status`, { headers: { 'X-Swagger-Key': localApiKey.value } })
  if (!response.ok) throw new Error(await response.text())
  return await response.json() as ImportJob | PlayerUpdateJob
}
async function pollStatus() {
  try {
    const job = await readStatus(); importJob.value = job
    if (job.status === 'complete') {
      busy.value = false
      statusMessage.value = 'rowsChecked' in job ? '선수·계약 업데이트 완료' : `경기 ${job.matchesProcessed}건 교체, 문서 ${job.documentsWritten}건, raw ${job.recordsWritten}건 저장 완료`
      return
    }
    if (job.status === 'failed') { busy.value = false; errorMessage.value = job.error || '이관 작업이 실패했습니다.'; return }
    if (job.status === 'idle') { busy.value = false; errorMessage.value = '서버에서 실행 상태를 찾지 못했습니다.'; return }
    if (mounted) pollTimer = setTimeout(() => void pollStatus(), 1200)
  } catch (error) { busy.value = false; errorMessage.value = error instanceof Error ? error.message : '진행 상태를 읽지 못했습니다.' }
}
async function startImport() {
  const file = selectedFile.value
  if (mode.value === 'full' && !file) { errorMessage.value = 'SQL 파일을 선택하세요.'; return }
  if (mode.value === 'players' && !/^\d{4}-\d{4}$/.test(season.value.trim())) { errorMessage.value = '시즌을 YYYY-YYYY 형식으로 입력하세요.'; return }
  if (!localApiKey.value) { errorMessage.value = 'Swagger API 키를 입력하세요.'; return }
  busy.value = true; importJob.value = null; errorMessage.value = ''
  if (backendState.value !== 'ready' && !(await checkBackend())) { busy.value = false; return }
  const headers = { 'X-Swagger-Key': localApiKey.value }
  const body = new FormData()
  if (mode.value === 'players') body.append('season', season.value.trim())
  else body.append('dump', file!)
  try {
    const response = await fetch(`${backendUrl.value}${paths[mode.value]}`, { method: 'POST', headers, body })
    if (!response.ok) throw new Error(await response.text())
    await response.json()
    await pollStatus()
  } catch (error) { busy.value = false; errorMessage.value = error instanceof Error ? error.message : '이관 요청을 시작하지 못했습니다.' }
}
onMounted(() => void checkBackend())
onUnmounted(() => { mounted = false; if (pollTimer) clearTimeout(pollTimer) })
</script>

<template>
  <main class="page-shell"><section class="import-panel">
    <header><NuxtLink to="/manage" class="back-link">&larr; 데이터 관리로</NuxtLink><h1>SQL 데이터 이관</h1></header>
    <div class="mode-options" role="group" aria-label="SQL 작업 유형">
      <button type="button" :aria-pressed="mode === 'full'" :disabled="busy || backendState === 'checking'" @click="selectMode('full')">전체 SQL 이관</button>
      <button type="button" :aria-pressed="mode === 'players'" :disabled="busy || backendState === 'checking'" @click="selectMode('players')">선수·계약 업데이트</button>
    </div>
    <p v-if="mode === 'full'">덤프에 포함된 경기와 기준 데이터를 이관합니다.</p>
    <p v-else>aifootballx Player의 선택 시즌 EPL 선수와 계약을 읽어 didNEW에 신규 및 변경분을 적용합니다.</p>
    <div class="form-row">
      <label v-if="mode === 'full'"><span>SQL 파일</span><input type="file" accept=".sql,.txt" :disabled="busy" @change="selectFile"></label>
      <label v-else><span>EPL 시즌</span><input v-model="season" placeholder="2026-2027" :disabled="busy" maxlength="9"></label>
      <label class="key-field"><span>Swagger API 키</span><div><input v-model="localApiKey" :type="apiKeyVisible ? 'text' : 'password'" autocomplete="off" :disabled="busy"><button type="button" class="icon-button" :aria-label="apiKeyVisible ? '키 숨기기' : '키 보기'" @click="apiKeyVisible = !apiKeyVisible">{{ apiKeyVisible ? '숨기기' : '보기' }}</button></div></label>
      <button class="run-button" :disabled="busy || (mode === 'full' && !selectedFile) || backendState === 'checking'" @click="startImport">{{ busy ? '실행 중...' : mode === 'players' ? '선수 업데이트 실행' : '이관 실행' }}</button>
    </div>
    <p v-if="selectedFile" class="file-name">선택 파일: {{ selectedFile.name }}</p>
    <p class="backend-status" :class="backendState">{{ statusMessage }}</p>
    <section v-if="importJob" class="progress-block"><div class="progress-head"><strong>{{ importJob.percent }}%</strong><span>{{ importJob.status }}</span></div><div class="progress-track"><div :style="{ width: `${importJob.percent}%` }" /></div><p>{{ progressText(importJob) }}</p></section>
    <details v-if="importJob && 'issues' in importJob && importJob.issues.length" class="progress-block">
      <summary>확인 필요 {{ importJob.playersSkipped }}건 중 {{ importJob.issues.length }}건 보기</summary>
      <ul><li v-for="(issue, index) in importJob.issues" :key="index">{{ issue.playerId }} · {{ issue.reason }}</li></ul>
    </details>
    <p v-if="errorMessage" class="error">오류: {{ errorMessage }}</p>
  </section></main>
</template>

<style scoped>
.page-shell { min-height: 100vh; padding: 76px 24px; background: #080d15; color: #e7edf5; }
.import-panel { max-width: 1280px; margin: 0 auto; border: 1px solid #202a39; padding: 28px; } header { border-bottom: 1px solid #202a39; padding-bottom: 18px; } h1 { margin: 0; font-size: 22px; } p { color: #aeb8c6; }
.mode-options { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
.mode-options button { padding: 10px 15px; border: 1px solid #344154; background: #111a2b; color: #e7edf5; cursor: pointer; }
.mode-options button[aria-pressed="true"] { background: #163429; border-color: #3aa675; color: #b6f6d2; }
.back-link { float: right; color: #aeb8c6; text-decoration: none; }.form-row { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(240px, .65fr) auto; gap: 16px; margin-top: 28px; align-items: end; } label { display: grid; gap: 8px; color: #cbd4df; font-size: 14px; }
input { width: 100%; box-sizing: border-box; min-height: 40px; border: 1px solid #344154; background: #111a2b; color: #edf3fb; padding: 8px 10px; }.key-field > div { display: flex; }.key-field input { border-right: 0; }.icon-button { min-width: 58px; border: 1px solid #344154; background: #172237; color: #cbd4df; cursor: pointer; }
.run-button { min-height: 40px; border: 0; padding: 0 18px; background: #13854a; color: white; font-weight: 700; cursor: pointer; }button:disabled, input:disabled { opacity: .55; cursor: not-allowed; }.file-name, .backend-status, .error, .progress-block { margin: 18px 0 0; padding: 14px; background: #111720; border: 1px solid #202a39; }.backend-status.ready { color: #66df9a; }.backend-status.unavailable, .error { color: #ffb4aa; }
.progress-head { display: flex; justify-content: space-between; }.progress-track { height: 8px; margin: 12px 0; background: #263244; overflow: hidden; }.progress-track div { height: 100%; background: #24b661; transition: width .2s ease; }@media (max-width: 760px) { .page-shell { padding: 24px 14px; }.form-row { grid-template-columns: 1fr; }.back-link { float: none; display: inline-block; margin-bottom: 18px; } }
</style>
