<script setup lang="ts">
interface PendingDraft {
  gmId: string
  side: 'H' | 'A'
  updatedAt?: string
  payload: { homeScore: number; awayScore: number; records: unknown[]; status: string }
}

const { request } = useBackendApi()
const drafts = ref<PendingDraft[]>([])
const loading = ref(false)
const message = ref('')

async function load() {
  loading.value = true
  message.value = ''
  try {
    const response = await request<{ drafts: PendingDraft[] }>('/api/v1/match-input/approvals')
    drafts.value = response.drafts
  } catch (error) {
    message.value = error instanceof Error ? error.message : '승인 대기 Draft를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
}

async function approve(draft: PendingDraft) {
  if (!confirm(`${draft.gmId} / ${draft.side} 기록을 최종 RAW로 승격하시겠습니까?`)) return
  loading.value = true
  message.value = ''
  try {
    await request(`/api/v1/match-input/approvals/${encodeURIComponent(draft.gmId)}/${draft.side}/promote`, { method: 'POST' })
    await load()
  } catch (error) {
    message.value = error instanceof Error ? error.message : 'RAW 승격에 실패했습니다.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="frame">
      <div class="topBar">
        <div>
          <h1>BASIC 기록 승인</h1>
          <p>승인 대기 상태의 Draft만 최종 RAW로 승격합니다.</p>
        </div>
        <div class="tools">
          <button title="새로고침" :disabled="loading" @click="load">↻</button>
          <NuxtLink to="/manage">데이터 관리로</NuxtLink>
        </div>
      </div>

      <p v-if="message" class="message">{{ message }}</p>
      <p v-else-if="loading" class="muted">불러오는 중...</p>
      <p v-else-if="!drafts.length" class="muted">승인 대기 중인 BASIC Draft가 없습니다.</p>

      <div v-else class="rows">
        <article v-for="draft in drafts" :key="`${draft.gmId}_${draft.side}`" class="row">
          <div>
            <strong>{{ draft.gmId }} · {{ draft.side }}</strong>
            <span>{{ draft.payload.homeScore }} : {{ draft.payload.awayScore }} · 기록 {{ draft.payload.records.length }}건</span>
            <small>{{ draft.updatedAt ? new Date(draft.updatedAt).toLocaleString('ko-KR') : '' }}</small>
          </div>
          <button :disabled="loading" @click="approve(draft)">RAW 승격</button>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page{min-height:100vh;padding:48px;background:#0b0f17;color:#eef2f7;font-family:Arial,"Noto Sans KR",sans-serif}
.frame{max-width:1100px;margin:0 auto}
.topBar{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid #26303d;padding-bottom:20px;margin-bottom:20px}
h1{font-size:22px;margin:0 0 8px}p{margin:0;color:#9ca9b8;font-size:14px}.tools{display:flex;gap:12px;align-items:center}.tools a{color:#b9c8d8;text-decoration:none}.tools button{width:34px;height:34px}
.rows{display:grid;gap:10px}.row{display:flex;justify-content:space-between;align-items:center;border:1px solid #26303d;background:#111824;padding:18px}.row strong,.row span,.row small{display:block}.row span,.row small{color:#9ca9b8;font-size:13px;margin-top:5px}.row button{background:#e2ad1b;border:0;color:#1b1607;font-weight:700;padding:10px 14px;cursor:pointer}.message{color:#ff8b8b}.muted{padding:24px 0}
</style>
