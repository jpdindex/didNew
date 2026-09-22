<script setup lang="ts">
type BackendState = 'idle' | 'checking' | 'ready' | 'unavailable'
type ImportJobState = 'queued' | 'preparing' | 'replacing' | 'writing' | 'complete' | 'failed'
interface ImportJob { status: ImportJobState; jobId: string; matchesTotal: number; matchesProcessed: number; documentsTotal: number; documentsWritten: number; recordsWritten: number; percent: number; error?: string | null }

const selectedFile = ref<File | null>(null)
const backendUrl = ref('')
const localApiKey = ref('')
const apiKeyVisible = ref(false)
const backendState = ref<BackendState>('idle')
const statusMessage = ref('백엔드 상태를 확인 중입니다.')
const importJob = ref<ImportJob | null>(null)
const errorMessage = ref('')
const busy = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null

function backendCandidates() { return Array.from({ length: 100 }, (_, index) => `http://127.0.0.1:${8000 + index}`) }
async function hasImportApi(url: string) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 1500)
  try {
    if (!(await fetch(`${url}/health`, { signal: controller.signal })).ok) return false
    const response = await fetch(`${url}/openapi.json`, { signal: controller.signal })
    if (!response.ok) return false
    const schema = await response.json() as { paths?: Record<string, unknown> }
    return Boolean(schema.paths?.['/api/v1/legacy-import'])
  } catch { return false } finally { clearTimeout(timeout) }
}
async function checkBackend() {
  backendState.value = 'checking'; errorMessage.value = ''
  const candidates = backendUrl.value ? [backendUrl.value.replace(/\/$/, '')] : backendCandidates()
  for (const candidate of candidates) {
    if (await hasImportApi(candidate)) {
      backendUrl.value = candidate; backendState.value = 'ready'
      statusMessage.value = `백엔드와 Firestore가 준비되었습니다. (${candidate})`
      return true
    }
  }
  backendState.value = 'unavailable'; statusMessage.value = '백엔드를 실행하세요: python -m backend.app'
  return false
}
function selectFile(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  importJob.value = null; errorMessage.value = ''
}
function progressText(job: ImportJob) { return `경기 ${job.matchesProcessed} / ${job.matchesTotal} · 문서 ${job.documentsWritten} / ${job.documentsTotal}` }
async function readJob(jobId: string) {
  const response = await fetch(`${backendUrl.value}/api/v1/legacy-import/jobs/${jobId}`, { headers: { 'X-Swagger-Key': localApiKey.value } })
  if (!response.ok) throw new Error(await response.text())
  return await response.json() as ImportJob
}
async function pollJob(jobId: string) {
  try {
    const job = await readJob(jobId); importJob.value = job
    if (job.status === 'complete') {
      busy.value = false
      statusMessage.value = `완료: 경기 ${job.matchesProcessed}건 교체, 문서 ${job.documentsWritten}건, raw ${job.recordsWritten}건 저장`
      return
    }
    if (job.status === 'failed') { busy.value = false; errorMessage.value = job.error || '이관 작업이 실패했습니다.'; return }
    pollTimer = setTimeout(() => void pollJob(jobId), 1200)
  } catch (error) { busy.value = false; errorMessage.value = error instanceof Error ? error.message : '진행 상태를 읽지 못했습니다.' }
}
async function startImport() {
  if (!selectedFile.value) { errorMessage.value = 'SQL 파일을 선택하세요.'; return }
  if (!localApiKey.value) { errorMessage.value = '로컬 API 키를 입력하세요.'; return }
  if (!(await checkBackend())) return
  busy.value = true; importJob.value = null; errorMessage.value = ''
  const form = new FormData(); form.append('dump', selectedFile.value)
  try {
    const response = await fetch(`${backendUrl.value}/api/v1/legacy-import`, { method: 'POST', headers: { 'X-Swagger-Key': localApiKey.value }, body: form })
    if (!response.ok) throw new Error(await response.text())
    await pollJob((await response.json() as { jobId: string }).jobId)
  } catch (error) { busy.value = false; errorMessage.value = error instanceof Error ? error.message : '이관 요청을 시작하지 못했습니다.' }
}
onMounted(() => void checkBackend())
onUnmounted(() => { if (pollTimer) clearTimeout(pollTimer) })
</script>

<template>
  <main class="page-shell"><section class="import-panel">
    <header><NuxtLink to="/manage" class="back-link">&larr; 데이터 관리로</NuxtLink><h1>SQL 데이터 이관</h1><p>덤프에 포함된 경기만 기존 데이터를 교체합니다.</p></header>
    <div class="form-row">
      <label><span>SQL 파일</span><input type="file" accept=".sql,.txt" :disabled="busy" @change="selectFile"></label>
      <label class="key-field"><span>로컬 API 키</span><div><input v-model="localApiKey" :type="apiKeyVisible ? 'text' : 'password'" autocomplete="off" :disabled="busy"><button type="button" class="icon-button" :aria-label="apiKeyVisible ? '키 숨기기' : '키 보기'" @click="apiKeyVisible = !apiKeyVisible">{{ apiKeyVisible ? 'Hide' : 'Show' }}</button></div></label>
      <button class="run-button" :disabled="busy || !selectedFile" @click="startImport">{{ busy ? '이관 중...' : '이관 실행' }}</button>
    </div>
    <p v-if="selectedFile" class="file-name">선택 파일: {{ selectedFile.name }}</p>
    <p class="backend-status" :class="backendState">{{ statusMessage }}</p>
    <section v-if="importJob" class="progress-block"><div class="progress-head"><strong>{{ importJob.percent }}%</strong><span>{{ importJob.status }}</span></div><div class="progress-track"><div :style="{ width: `${importJob.percent}%` }" /></div><p>{{ progressText(importJob) }}</p></section>
    <p v-if="errorMessage" class="error">오류: {{ errorMessage }}</p>
  </section></main>
</template>

<style scoped>
.page-shell { min-height: 100vh; padding: 76px 24px; background: #080d15; color: #e7edf5; }
.import-panel { max-width: 1280px; margin: 0 auto; border: 1px solid #202a39; padding: 28px; } header { border-bottom: 1px solid #202a39; padding-bottom: 18px; } h1 { margin: 0; font-size: 22px; } p { color: #aeb8c6; }
.back-link { float: right; color: #aeb8c6; text-decoration: none; }.form-row { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(240px, .65fr) auto; gap: 16px; margin-top: 28px; align-items: end; } label { display: grid; gap: 8px; color: #cbd4df; font-size: 14px; }
input { width: 100%; box-sizing: border-box; min-height: 40px; border: 1px solid #344154; background: #111a2b; color: #edf3fb; padding: 8px 10px; }.key-field > div { display: flex; }.key-field input { border-right: 0; }.icon-button { min-width: 58px; border: 1px solid #344154; background: #172237; color: #cbd4df; cursor: pointer; }
.run-button { min-height: 40px; border: 0; padding: 0 18px; background: #13854a; color: white; font-weight: 700; cursor: pointer; }button:disabled, input:disabled { opacity: .55; cursor: not-allowed; }.file-name, .backend-status, .error, .progress-block { margin: 18px 0 0; padding: 14px; background: #111720; border: 1px solid #202a39; }.backend-status.ready { color: #66df9a; }.backend-status.unavailable, .error { color: #ffb4aa; }
.progress-head { display: flex; justify-content: space-between; }.progress-track { height: 8px; margin: 12px 0; background: #263244; overflow: hidden; }.progress-track div { height: 100%; background: #24b661; transition: width .2s ease; }@media (max-width: 760px) { .page-shell { padding: 24px 14px; }.form-row { grid-template-columns: 1fr; }.back-link { float: none; display: inline-block; margin-bottom: 18px; } }
</style>
