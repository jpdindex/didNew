<script setup lang="ts">
import { doc, getDoc } from 'firebase/firestore'
import { canAccessManagement } from '~/utils/managementAccess'
import { emptyPlayerRegistration, emptyPlayerNumberChange, type PlayerRegistration, type PlayerRegistrationRequest, type PlayerNumberChange } from '~/types/playerRegistration'

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
const numberForm = ref(emptyPlayerNumberChange())
const requestType = ref<'REGISTRATION' | 'NUMBER_CHANGE'>('REGISTRATION')
const newMenuOpen = ref(false)
const isNumberChange = computed(() => requestType.value === 'NUMBER_CHANGE')
const requestTitle = computed(() => isNumberChange.value ? '등번호 변경 요청' : '새 선수 등록 요청')
const reviewAction = ref<'approve' | 'edit'>('approve')
const existingPlayerId = ref('')
const duplicatePlayerId = ref('')
type Duplicate = { id: string; name: string; nameFull?: string; birth?: string; currentTeamId?: string }
const duplicates = ref<Duplicate[]>([])
const duplicateChecked = ref(false)
const statuses: Record<string, string> = { pending: '검토 대기', approved: '등록 완료' }
const visible = computed(() => items.value.filter(i => !filter.value || i.status === filter.value))
const editable = computed(() => !active.value || (manager.value && active.value.status === 'pending'))

watch(form, () => { duplicates.value = []; existingPlayerId.value = ''; duplicateChecked.value = false }, { deep: true })
watch(duplicatePlayerId, () => { duplicates.value = []; existingPlayerId.value = ''; duplicateChecked.value = false })
watch(() => [form.value.leagueId, form.value.seasonId, form.value.teamId], () => {
  numberForm.value.leagueId = form.value.leagueId
  numberForm.value.seasonId = form.value.seasonId
  numberForm.value.teamId = form.value.teamId
})

function requestLabel(item: PlayerRegistrationRequest) { return item.requestType === 'NUMBER_CHANGE' ? '등번호 변경' : '신규 선수 등록' }
function playerLabel(item: PlayerRegistrationRequest) {
  return item.requestType === 'NUMBER_CHANGE' ? item.playerName || (item.payload as PlayerNumberChange).playerId : (item.payload as PlayerRegistration).name
}
function requestDetail(item: PlayerRegistrationRequest) {
  if (item.requestType === 'NUMBER_CHANGE') {
    const payload = item.payload as PlayerNumberChange
    return `${payload.currentNo || '미등록'} → ${payload.newNo}번 · 변경 ${payload.changeDate || '날짜 미입력'}`
  }
  const payload = item.payload as PlayerRegistration
  return `${payload.nameFull} · ${payload.no}번 / ${payload.pos} · 적용 ${payload.fromDate || '미입력'}`
}

async function load() {
  loading.value = true
  error.value = ''
  try { items.value = (await request<{ requests: PlayerRegistrationRequest[] }>('/api/v1/player-requests')).requests }
  catch (e) { error.value = e instanceof Error ? e.message : '목록을 불러오지 못했습니다.' }
  finally { loading.value = false }
}

async function startNew(type: 'REGISTRATION' | 'NUMBER_CHANGE' = 'REGISTRATION') {
  newMenuOpen.value = false
  requestType.value = type
  active.value = null
  form.value = emptyPlayerRegistration()
  numberForm.value = emptyPlayerNumberChange()
  duplicatePlayerId.value = ''
  error.value = ''; message.value = ''
  const matchId = String(route.query.matchId || '')
  if (matchId && !matchId.includes('/')) {
    try {
      const match = (await getDoc(doc($db, 'matches', matchId))).data()
      if (!match) throw new Error('해당 경기를 찾을 수 없습니다.')
      form.value = { ...form.value, matchId, leagueId: match.leagueId, seasonId: match.seasonId,
        teamId: route.query.team === 'away' ? match.awayTeamId : match.homeTeamId }
    } catch (e) { error.value = e instanceof Error ? e.message : '경기 정보를 불러오지 못했습니다.'; return }
  }
  editing.value = true
}

function open(item: PlayerRegistrationRequest) {
  duplicatePlayerId.value = ''
  newMenuOpen.value = false
  active.value = item
  requestType.value = item.requestType || 'REGISTRATION'
  if (item.requestType === 'NUMBER_CHANGE') {
    const payload = item.payload as PlayerNumberChange
    form.value = { ...emptyPlayerRegistration(), leagueId: payload.leagueId, seasonId: payload.seasonId, teamId: payload.teamId }
    numberForm.value = { ...payload }
  } else form.value = { ...(item.payload as PlayerRegistration) }
  reviewAction.value = 'approve'
  error.value = ''; message.value = ''; editing.value = true
}

async function submit() {
  if (!isNumberChange.value && !form.value.nation) { error.value = '국적을 목록에서 선택하세요.'; return }
  if (isNumberChange.value && (!numberForm.value.playerId || !numberForm.value.contractId)) { error.value = '선수와 시즌 소속을 확인하세요.'; return }
  busy.value = true; error.value = ''; message.value = ''
  try {
    await request('/api/v1/player-requests', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(isNumberChange.value ? numberForm.value : form.value) })
    editing.value = false; filter.value = 'pending'; await load(); message.value = '요청을 제출했습니다. 관리자 승인 후 DB에 반영됩니다.'
  } catch (e) { error.value = e instanceof Error ? e.message : '요청에 실패했습니다.' }
  finally { busy.value = false }
}

async function checkDuplicates() {
  busy.value = true; error.value = ''
  duplicateChecked.value = false; duplicates.value = []; existingPlayerId.value = ''
  try {
    duplicates.value = (await request<{ players: Duplicate[] }>('/api/v1/player-requests/duplicates', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form.value, lookupPlayerId: duplicatePlayerId.value.trim() }),
    })).players
    duplicateChecked.value = true
  } catch (e) { error.value = e instanceof Error ? e.message : '중복 확인에 실패했습니다.' }
  finally { busy.value = false }
}

async function review(action: 'approve' | 'edit') {
  if (!active.value) return
  if (isNumberChange.value && action === 'approve' && !numberForm.value.changeDate) { error.value = '승인 전에 등번호 변경 날짜를 확인해 입력하세요.'; return }
  if (!isNumberChange.value && action === 'approve' && !duplicateChecked.value) { error.value = '승인 전에 기존 선수 중복 확인을 눌러주세요.'; return }
  if (!isNumberChange.value && action === 'approve' && duplicates.value.length && !existingPlayerId.value) { error.value = '영문 FULLNAME이 일치하는 기존 선수의 p_id를 선택하세요.'; return }
  busy.value = true; error.value = ''; message.value = ''
  try {
    await request(`/api/v1/player-requests/${active.value.id}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, version: active.value.version, payload: isNumberChange.value ? numberForm.value : form.value, existingPlayerId: isNumberChange.value ? '' : existingPlayerId.value }) })
    editing.value = false; await load(); message.value = action === 'approve' ? '승인하여 선수 DB에 반영했습니다.' : '수정 내용을 저장했습니다. 요청은 승인 대기로 유지됩니다.'
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
    <header><div><NuxtLink to="/manage">← 데이터 관리</NuxtLink><h1>선수 등록 요청하기</h1><p>선수 등록·등번호 변경을 요청하면 관리자가 검토 후 DB에 반영합니다.</p></div><div class="newRequest" @keydown.esc="newMenuOpen = false"><button :disabled="busy || loading" :aria-expanded="newMenuOpen" aria-controls="new-request-menu" @click="newMenuOpen = !newMenuOpen">+ 새 요청 ▾</button><div v-if="newMenuOpen" id="new-request-menu" class="newRequestMenu"><button @click="startNew('REGISTRATION')">새 선수 등록 요청</button><button @click="startNew('NUMBER_CHANGE')">등번호 변경 요청</button></div></div></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="message" class="success" role="status">{{ message }}</p>
    <section v-if="editing" class="editor">
      <div class="sectionHead"><h2>{{ requestTitle }}<template v-if="active"> · {{ statuses[active.status] }} · {{ active.requestedName }}</template></h2><button type="button" :disabled="busy" @click="editing = false">닫기</button></div>
      <form @submit.prevent="manager && active ? review(reviewAction) : submit()">
        <PlayerRegistrationForm :key="`${active?.id || 'new'}-${requestType}`" v-model="form" :selection-only="isNumberChange" :disabled="busy || !editable" />
        <PlayerNumberChangeForm v-if="isNumberChange" :key="active?.id || 'new-number'" v-model="numberForm" :disabled="busy || !editable" />
        <p v-if="!isNumberChange && /^https?:\/\//i.test(form.sourceUrl)"><a :href="form.sourceUrl" target="_blank" rel="noopener noreferrer">참고 자료 열기 ↗</a></p>
        <template v-if="manager && active?.status === 'pending'">
          <div v-if="!isNumberChange" class="reviewBox">
            <label>기존 p_id로 확인 (선택)<input v-model.trim="duplicatePlayerId" :disabled="busy" maxlength="100" placeholder="기존 p_id를 알면 입력하세요"></label>
            <button type="button" :disabled="busy" @click="checkDuplicates">영문 FULLNAME · p_id 중복 확인</button>
            <p v-if="duplicateChecked && !duplicates.length">영문 FULLNAME이 일치하는 기존 선수가 없습니다.</p>
            <label v-if="duplicateChecked">등록 방식<select v-model="existingPlayerId"><option value="" :disabled="!!duplicates.length">{{ duplicates.length ? '기존 선수의 p_id를 선택하세요' : '새 선수로 등록' }}</option><option v-for="p in duplicates" :key="p.id" :value="p.id">p_id: {{ p.id }} · {{ p.nameFull }} · {{ p.name }} · {{ p.birth || '생년월일 없음' }}</option></select></label>
            <p v-if="existingPlayerId">기존 선수의 기본 정보는 유지하고, 필요한 팀 소속 정보를 연결합니다.</p>
          </div>
          <div class="actions"><button type="submit" :disabled="busy" @click="reviewAction = 'edit'">수정 저장</button><button class="primary" type="submit" :disabled="busy || (!isNumberChange && (!duplicateChecked || (!!duplicates.length && !existingPlayerId)))" @click="reviewAction = 'approve'">승인 · DB 반영</button></div>
        </template>
        <div v-else-if="editable" class="actions"><button class="primary" :disabled="busy" type="submit">{{ isNumberChange ? '등번호 변경 요청 제출' : '등록 요청 제출' }}</button></div>
      </form>
    </section>
    <section class="list">
      <div class="sectionHead"><h2>{{ manager ? '전체 등록 요청' : '내 등록 요청' }}</h2><div class="filters"><select v-model="filter"><option value="">전체 상태</option><option v-for="(label, value) in statuses" :key="value" :value="value">{{ label }}</option></select><button :disabled="busy || loading" @click="load">목록 새로고침</button></div></div>
      <p v-if="loading">요청을 불러오는 중...</p><p v-else-if="!visible.length" class="empty">해당 상태의 요청이 없습니다.</p>
      <button v-for="item in visible" :key="item.id" class="requestRow" :disabled="busy" @click="open(item)"><span><small class="requestKind">{{ requestLabel(item) }}</small><strong>{{ playerLabel(item) }}</strong><small>{{ item.payload.leagueId === 'LALIGA' ? '라리가' : item.leagueName }} · {{ item.payload.seasonId.slice(0, 4) }}/{{ item.payload.seasonId.slice(6) }} · {{ item.teamName }}</small><small>{{ requestDetail(item) }}</small><small>{{ item.requestedName }}</small></span><span class="badge" :class="item.status">{{ statuses[item.status] }}</span></button>
    </section>
  </main>
</template>

<style scoped>
.reviewBox input{font:inherit;background:#202d3d;color:#edf3f9;border:1px solid #41516a;border-radius:6px;padding:9px 13px}.reviewBox>button{margin-top:12px}
.newRequest{position:relative;flex-shrink:0}.newRequestMenu{position:absolute;right:0;top:calc(100% + 6px);z-index:10;width:210px;padding:6px;background:#19222e;border:1px solid #41516a;border-radius:8px}.newRequestMenu button{display:block;width:100%;text-align:left;border:0;background:transparent}.newRequestMenu button:hover{background:#26374b}.requestRow .requestKind{display:inline-block;color:#7ed1df;margin:0 0 8px;border:1px solid #41516a;border-radius:4px;padding:3px 7px}
.requestsPage{box-sizing:border-box;height:800px;overflow-y:auto;background:#111820;color:#edf3f9;padding:28px;max-width:1280px;margin:auto;font-family:inherit}header,.sectionHead,.actions,.filters{display:flex;align-items:center;justify-content:space-between;gap:12px}header{align-items:flex-start}h1{font-size:24px;margin:12px 0}h2{font-size:17px}p{line-height:1.6;color:#a9b9cb}a{color:#76d4e6}button,select,textarea{font:inherit;background:#202d3d;color:#edf3f9;border:1px solid #41516a;border-radius:6px;padding:9px 13px}button{cursor:pointer}button:disabled{opacity:.5;cursor:default}.editor,.list{background:#19222e;border:1px solid #304052;border-radius:10px;padding:20px;margin-top:20px}.sectionHead{margin-bottom:16px}.actions{justify-content:flex-end;margin-top:18px}.primary{background:#167185;border-color:#48bbd2;font-weight:700}.error{color:#ffc4b9;background:#482c30;padding:12px}.success{color:#a5edc9;background:#163c33;padding:12px}.notice{color:#ffe0a4}.reviewBox{margin-top:16px;padding:16px;background:#111820;border-radius:8px}.reviewBox label{display:flex;flex-direction:column;gap:8px;margin-top:14px}.reviewBox textarea{box-sizing:border-box;width:100%}.requestRow{display:flex;justify-content:space-between;align-items:center;text-align:left;width:100%;margin-top:10px;padding:15px;gap:16px}.requestRow small{display:block;color:#b0bed0;margin-top:6px}.badge{white-space:nowrap;font-size:12px;color:#b6d6ff}.approved{color:#9ce7c4}.empty{padding:20px;text-align:center}@media(max-width:650px){.requestsPage{padding:16px}header,.sectionHead{align-items:stretch;flex-direction:column}.filters{flex-wrap:wrap}.requestRow{align-items:flex-start}.editor,.list{padding:14px}}
</style>
