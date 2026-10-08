<script setup lang="ts">
interface PendingDraft {
  gmId: string
  side: 'H' | 'A'
  teamId: string
  teamName: string
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
  if (!confirm(`${draft.gmId} / ${draft.side} / ${draft.teamName} 기록을 최종 RAW로 승격하시겠습니까?`)) return
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
      <header class="topBar">
        <NuxtLink to="/manage" class="back">← 데이터 관리</NuxtLink>
        <h1>BASIC 기록 승인</h1>
        <button class="refreshBtn" title="새로고침" :disabled="loading" @click="load">↻ 새로고침</button>
      </header>
      <p class="pageDescription">승인 대기 상태의 Draft만 최종 RAW로 승격합니다.</p>

      <p v-if="message" class="message">{{ message }}</p>
      <p v-else-if="loading" class="muted">불러오는 중...</p>
      <p v-else-if="!drafts.length" class="muted">승인 대기 중인 BASIC Draft가 없습니다.</p>

      <div v-else class="rows">
        <article v-for="draft in drafts" :key="`${draft.gmId}_${draft.side}`" class="row">
          <div>
            <strong>{{ draft.gmId }}</strong>
            <div class="teamInfo">
              <span>{{ draft.side === 'H' ? '홈팀 입력' : '원정팀 입력' }}</span>
              <b>{{ draft.teamName }}</b>
              <code v-if="draft.teamId">{{ draft.teamId }}</code>
            </div>
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
.page{width:1280px;height:800px;box-sizing:border-box;overflow-y:auto;padding:48px;background:#0b0f17;color:#eef2f7;font-family:Arial,"Noto Sans KR",sans-serif}
.frame{max-width:1100px;margin:0 auto}
.topBar{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:20px;width:calc(100% + 76px);margin:0 0 16px -38px;border-bottom:1px solid #29313b;padding:0 0 18px}
h1{font-size:26px;text-align:center;margin:0}.back{justify-self:start;height:34px;display:inline-flex;align-items:center;color:#9da7b3;font-size:14px;font-weight:600;text-decoration:none}.refreshBtn{justify-self:end;height:34px;padding:0 12px;border:1px solid rgba(140,230,245,.65);border-radius:4px;background:transparent;color:#8ce6f5;font-weight:700;cursor:pointer}.refreshBtn:disabled{opacity:.5;cursor:wait}.pageDescription{margin:0 0 20px;color:#9ca9b8;font-size:13px}p{margin:0;color:#9ca9b8;font-size:14px}
.rows{display:grid;gap:10px}.row{display:flex;justify-content:space-between;align-items:center;border:1px solid #26303d;background:#111824;padding:18px}.row strong,.row>div>span,.row small{display:block}.row>div>span,.row small{color:#9ca9b8;font-size:13px;margin-top:5px}.teamInfo{display:flex;align-items:center;gap:8px;margin-top:9px}.teamInfo span{padding:3px 7px;border:1px solid rgba(140,230,245,.35);border-radius:3px;color:#8ce6f5;font-size:11px;font-weight:700}.teamInfo b{color:#f3f6fa;font-size:15px}.teamInfo code{color:#8290a0;font-size:11px}.row button{background:#e2ad1b;border:0;color:#1b1607;font-weight:700;padding:10px 14px;cursor:pointer}.message{color:#ff8b8b}.muted{padding:24px 0}
</style>
