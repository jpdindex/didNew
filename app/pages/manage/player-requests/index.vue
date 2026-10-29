<script setup lang="ts">
import { doc, getDoc } from 'firebase/firestore'
import { canAccessManagement } from '~/utils/managementAccess'
import { emptyPlayerRegistration, type PlayerRegistrationRequest } from '~/types/playerRegistration'

const { $auth, $db, $authReady } = useNuxtApp()
const { request } = useBackendApi()
const route = useRoute()
const manager = computed(() => canAccessManagement($auth.currentUser?.email))
const items = ref<PlayerRegistrationRequest[]>([])
const filter = ref('pending')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const message = ref('')
const editing = ref(false)
const active = ref<PlayerRegistrationRequest | null>(null)
const form = ref(emptyPlayerRegistration())
const reviewAction = ref<'approve' | 'edit'>('approve')
const existingPlayerId = ref('')
type Duplicate = { id: string; name: string; nameFull?: string; birth?: string; currentTeamId?: string; sameBirth: boolean }
const duplicates = ref<Duplicate[]>([])
const duplicateChecked = ref(false)
const statuses: Record<string, string> = { pending: '검토 대기', approved: '등록 완료' }
const visible = computed(() => items.value.filter(i => !filter.value || i.status === filter.value))
const editable = computed(() => !active.value || (manager.value && active.value.status === 'pending'))

watch(form, () => { duplicates.value = []; existingPlayerId.value = ''; duplicateChecked.value = false }, { deep: true })

async function load() {
  loading.value = true
  error.value = ''
  try { items.value = (await request<{ requests: PlayerRegistrationRequest[] }>('/api/v1/player-requests')).requests }
  catch (e) { error.value = e instanceof Error ? e.message : '목록을 불러오지 못했습니다.' }
  finally { loading.value = false }
}

async function startNew() {
  active.value = null
  form.value = emptyPlayerRegistration()
  error.value = ''; message.value = ''
  const matchId = String(route.query.matchId || '')
  if (matchId && !matchId.includes('/')) {
    try {
      const match = (await getDoc(doc($db, 'matches', matchId))).data()
      if (!match) throw new Error('해당 경기를 찾을 수 없습니다.')
      form.value = { ...form.value, matchId, leagueId: match.leagueId, seasonId: match.seasonId,
        teamId: route.query.team === 'away' ? match.awayTeamId : match.homeTeamId,
        fromDate: String(match.date).replaceAll('.', '-') }
    } catch (e) { error.value = e instanceof Error ? e.message : '경기 정보를 불러오지 못했습니다.'; return }
  }
  editing.value = true
}

function open(item: PlayerRegistrationRequest) {
  active.value = item
  form.value = { ...item.payload }
  reviewAction.value = 'approve'
  error.value = ''; message.value = ''; editing.value = true
}

async function submit() {
  if (!form.value.nation) { error.value = '국적을 목록에서 선택하세요.'; return }
  busy.value = true; error.value = ''; message.value = ''
  try {
    await request('/api/v1/player-requests', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value) })
    editing.value = false; filter.value = 'pending'; await load(); message.value = '등록 요청을 제출했습니다. 관리자 승인 후 DB에 등록됩니다.'
  } catch (e) { error.value = e instanceof Error ? e.message : '요청에 실패했습니다.' }
  finally { busy.value = false }
}

async function checkDuplicates() {
  busy.value = true; error.value = ''
  try {
    duplicates.value = (await request<{ players: Duplicate[] }>('/api/v1/player-requests/duplicates', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form.value),
    })).players
    duplicateChecked.value = true
  } catch (e) { error.value = e instanceof Error ? e.message : '중복 확인에 실패했습니다.' }
  finally { busy.value = false }
}

async function review(action: 'approve' | 'edit') {
  if (!active.value) return
  if (action === 'approve' && !duplicateChecked.value) { error.value = '승인 전에 기존 선수 중복 확인을 눌러주세요.'; return }
  busy.value = true; error.value = ''; message.value = ''
  try {
    await request(`/api/v1/player-requests/${active.value.id}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, version: active.value.version, payload: form.value, existingPlayerId: existingPlayerId.value }) })
    editing.value = false; await load(); message.value = action === 'approve' ? '승인하여 선수 DB에 등록했습니다.' : '수정 내용을 저장했습니다. 요청은 승인 대기로 유지됩니다.'
  } catch (e) { error.value = e instanceof Error ? e.message : '검토 처리에 실패했습니다.' }
  finally { busy.value = false }
}

onMounted(async () => {
  await $authReady
  await load()
  if (route.query.matchId) await startNew()
})
</script>

<template>
  <main class="requestsPage">
    <header><div><NuxtLink to="/manage">← 데이터 관리</NuxtLink><h1>선수 등록 요청하기</h1><p>직접 확인한 선수 정보를 제출하면 관리자가 검토 후 DB에 등록합니다.</p></div><button :disabled="busy || loading" @click="startNew">+ 새 요청</button></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="message" class="success" role="status">{{ message }}</p>
    <section v-if="editing" class="editor">
      <div class="sectionHead"><h2>{{ active ? `${statuses[active.status]} · ${active.requestedName}` : '새 선수 등록 요청' }}</h2><button type="button" :disabled="busy" @click="editing = false">닫기</button></div>
      <form @submit.prevent="manager && active ? review(reviewAction) : submit()">
        <PlayerRegistrationForm :key="active?.id || 'new'" v-model="form" :disabled="busy || !editable" />
        <p v-if="/^https?:\/\//i.test(form.sourceUrl)"><a :href="form.sourceUrl" target="_blank" rel="noopener noreferrer">참고 자료 열기 ↗</a></p>
        <template v-if="manager && active?.status === 'pending'">
          <div class="reviewBox">
            <button type="button" :disabled="busy" @click="checkDuplicates">기존 선수 중복 확인</button>
            <p v-if="duplicateChecked && !duplicates.length">이름이 일치하는 기존 선수가 없습니다.</p>
            <label v-if="duplicateChecked">등록 방식<select v-model="existingPlayerId"><option value="">새 선수로 등록</option><option v-for="p in duplicates" :key="p.id" :value="p.id">기존 선수 연결 · {{ p.name }} / {{ p.nameFull }} / {{ p.birth || '생년월일 없음' }} {{ p.sameBirth ? '(생년월일 일치)' : '' }}</option></select></label>
            <p v-if="existingPlayerId">기존 선수의 기본 정보는 유지하고, 필요한 팀 소속 정보를 연결합니다.</p>
          </div>
          <div class="actions"><button type="submit" :disabled="busy" @click="reviewAction = 'edit'">수정 저장</button><button class="primary" type="submit" :disabled="busy || !duplicateChecked" @click="reviewAction = 'approve'">승인 · DB 등록</button></div>
        </template>
        <div v-else-if="editable" class="actions"><button class="primary" :disabled="busy" type="submit">등록 요청 제출</button></div>
      </form>
    </section>
    <section class="list">
      <div class="sectionHead"><h2>{{ manager ? '전체 등록 요청' : '내 등록 요청' }}</h2><div class="filters"><select v-model="filter"><option value="">전체 상태</option><option v-for="(label, value) in statuses" :key="value" :value="value">{{ label }}</option></select><button :disabled="busy || loading" @click="load">목록 새로고침</button></div></div>
      <p v-if="loading">요청을 불러오는 중...</p><p v-else-if="!visible.length" class="empty">해당 상태의 요청이 없습니다.</p>
      <button v-for="item in visible" :key="item.id" class="requestRow" :disabled="busy" @click="open(item)"><span><strong>{{ item.payload.name }}</strong> · {{ item.payload.nameFull }}<small>{{ item.leagueName }} · {{ item.payload.seasonId.slice(0, 4) }}/{{ item.payload.seasonId.slice(6) }} · {{ item.teamName }} · {{ item.payload.no }}번 / {{ item.payload.pos }}</small><small>{{ item.requestedName }} · 생년월일 {{ item.payload.birth }} · 적용 {{ item.payload.fromDate }}</small></span><span class="badge" :class="item.status">{{ statuses[item.status] }}</span></button>
    </section>
  </main>
</template>

<style scoped>
.requestsPage{box-sizing:border-box;height:800px;overflow-y:auto;background:#111820;color:#edf3f9;padding:28px;max-width:1280px;margin:auto;font-family:inherit}header,.sectionHead,.actions,.filters{display:flex;align-items:center;justify-content:space-between;gap:12px}header{align-items:flex-start}h1{font-size:24px;margin:12px 0}h2{font-size:17px}p{line-height:1.6;color:#a9b9cb}a{color:#76d4e6}button,select,textarea{font:inherit;background:#202d3d;color:#edf3f9;border:1px solid #41516a;border-radius:6px;padding:9px 13px}button{cursor:pointer}button:disabled{opacity:.5;cursor:default}.editor,.list{background:#19222e;border:1px solid #304052;border-radius:10px;padding:20px;margin-top:20px}.sectionHead{margin-bottom:16px}.actions{justify-content:flex-end;margin-top:18px}.primary{background:#167185;border-color:#48bbd2;font-weight:700}.error{color:#ffc4b9;background:#482c30;padding:12px}.success{color:#a5edc9;background:#163c33;padding:12px}.notice{color:#ffe0a4}.reviewBox{margin-top:16px;padding:16px;background:#111820;border-radius:8px}.reviewBox label{display:flex;flex-direction:column;gap:8px;margin-top:14px}.reviewBox textarea{box-sizing:border-box;width:100%}.requestRow{display:flex;justify-content:space-between;align-items:center;text-align:left;width:100%;margin-top:10px;padding:15px;gap:16px}.requestRow small{display:block;color:#b0bed0;margin-top:6px}.badge{white-space:nowrap;font-size:12px;color:#b6d6ff}.approved{color:#9ce7c4}.empty{padding:20px;text-align:center}@media(max-width:650px){.requestsPage{padding:16px}header,.sectionHead{align-items:stretch;flex-direction:column}.filters{flex-wrap:wrap}.requestRow{align-items:flex-start}.editor,.list{padding:14px}}
</style>
