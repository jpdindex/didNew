<script setup lang="ts">
import { collection, doc, getDoc, getDocs } from 'firebase/firestore'
import type { PlayerRegistration } from '~/types/playerRegistration'

const model = defineModel<PlayerRegistration>({ required: true })
const props = defineProps<{ disabled?: boolean }>()
const { $db } = useNuxtApp()
type Option = { id: string; label: string; leagueId?: string }
const leagues = ref<Option[]>([])
const seasons = ref<Option[]>([])
const teams = ref<(Option & { currentLeagueId?: string })[]>([])
const eligibleTeams = ref<Option[]>([])
const error = ref('')
const loading = ref(true)
const teamLoading = ref(false)
let teamLoadVersion = 0
const seasonOptions = computed(() => seasons.value.filter(s => !s.leagueId || s.leagueId === model.value.leagueId || s.id === model.value.seasonId))
const numbers = computed(() => [...new Set([...Array.from({ length: 100 }, (_, i) => String(i)), model.value.no].filter(Boolean))])
const seasonLabel = (id: string) => `${id.slice(0, 4)}/${id.slice(6)}`

async function loadTeams() {
  if (loading.value) return
  const version = ++teamLoadVersion
  const { leagueId, seasonId } = model.value
  if (!leagueId || !seasonId) { eligibleTeams.value = []; teamLoading.value = false; return }
  teamLoading.value = true
  error.value = ''
  try {
    const entries = await Promise.all(teams.value.map(async team => {
      const snapshot = await getDoc(doc($db, 'teams', team.id, 'seasons', seasonId))
      const entry = snapshot.data()
      const ids = entry ? [entry.leagueId, ...(entry.competitionIds || []), ...(entry.leagueIds || []), ...(entry.cupIds || [])] : [team.currentLeagueId]
      return ids.includes(leagueId) ? team : null
    }))
    if (version !== teamLoadVersion) return
    eligibleTeams.value = entries.filter((team): team is NonNullable<typeof team> => !!team)
    if (!eligibleTeams.value.some(team => team.id === model.value.teamId)) model.value.teamId = ''
  } catch (e) {
    if (version === teamLoadVersion) { eligibleTeams.value = []; error.value = e instanceof Error ? e.message : '팀 목록을 불러오지 못했습니다.' }
  } finally {
    if (version === teamLoadVersion) teamLoading.value = false
  }
}

watch(() => [model.value.leagueId, model.value.seasonId], loadTeams)
onMounted(async () => {
  try {
    const [leagueDocs, seasonDocs, teamDocs] = await Promise.all([
      getDocs(collection($db, 'leagues')), getDocs(collection($db, 'seasons')), getDocs(collection($db, 'teams')),
    ])
    leagues.value = leagueDocs.docs.map(d => ({ id: d.id, label: d.data().name || d.id })).sort((a, b) => a.label.localeCompare(b.label, 'ko'))
    seasons.value = seasonDocs.docs.map(d => ({ id: d.id.replace(/\D/g, ''), label: d.data().name || seasonLabel(d.id), leagueId: d.data().leagueId }))
      .filter(s => /^\d{8}$/.test(s.id)).sort((a, b) => b.id.localeCompare(a.id))
    if (model.value.seasonId && !seasons.value.some(s => s.id === model.value.seasonId)) seasons.value.unshift({ id: model.value.seasonId, label: seasonLabel(model.value.seasonId) })
    teams.value = teamDocs.docs.map(d => ({ id: d.id, label: d.data().nameKr || d.data().name || d.id, currentLeagueId: d.data().currentLeagueId }))
      .sort((a, b) => a.label.localeCompare(b.label, 'ko'))
    if (!model.value.leagueId) model.value.leagueId = leagues.value[0]?.id || ''
    if (!model.value.seasonId) model.value.seasonId = seasonOptions.value[0]?.id || ''
  } catch (e) { error.value = e instanceof Error ? e.message : '선택 항목을 불러오지 못했습니다.' }
  finally { loading.value = false }
  if (model.value.leagueId && model.value.seasonId) await loadTeams()
})
</script>

<template>
  <fieldset :disabled="props.disabled || loading" class="registrationFields">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading">DB 선택 항목을 불러오는 중...</p>
    <div class="fields">
      <label>리그 *<select v-model="model.leagueId" required :disabled="!!model.matchId"><option value="" disabled>리그 선택</option><option v-for="l in leagues" :key="l.id" :value="l.id">{{ l.label }}</option></select></label>
      <label>시즌 *<select v-model="model.seasonId" required :disabled="!!model.matchId"><option value="" disabled>시즌 선택</option><option v-for="s in seasonOptions" :key="s.id" :value="s.id">{{ s.label }}</option></select></label>
      <label>팀 *<select v-model="model.teamId" required :disabled="teamLoading || !!model.matchId"><option value="" disabled>{{ teamLoading ? '팀 불러오는 중...' : '팀 선택' }}</option><option v-for="t in eligibleTeams" :key="t.id" :value="t.id">{{ t.label }}</option></select></label>
      <label>요청 구분 *<select v-model="model.kind"><option value="NEW">명단에 없는 선수 등록</option><option value="YOUTH">2군·유스 콜업 선수 등록</option></select></label>
      <label>한글·표시 이름 *<input v-model.trim="model.name" required maxlength="100" placeholder="DB에 표시할 이름"></label>
      <label>영문 전체 이름 *<input v-model.trim="model.nameFull" required maxlength="160" placeholder="확인한 영문 전체 이름"></label>
      <label>영문 약칭<input v-model.trim="model.nameEn" maxlength="100" placeholder="예: J. Cruz"></label>
      <label>생년월일 *<input v-model="model.birth" type="date" required :max="model.fromDate"></label>
      <label>국적 *<FifaAssociationSelect v-model="model.nation" :disabled="props.disabled" /></label>
      <label>등번호 *<select v-model="model.no" required><option value="" disabled>등번호 선택</option><option v-for="n in numbers" :key="n" :value="n">{{ n }}</option></select></label>
      <label>포지션 *<select v-model="model.pos"><option value="GK">GK · 골키퍼</option><option value="DF">DF · 수비수</option><option value="MF">MF · 미드필더</option><option value="FW">FW · 공격수</option></select></label>
      <label>명단 적용 시작일 *<input v-model="model.fromDate" type="date" required></label>
      <label>주발<select v-model="model.foot"><option value="">미확인</option><option value="L">왼발</option><option value="R">오른발</option><option value="B">양발</option></select></label>
      <label>키<select v-model="model.height"><option :value="null">미확인</option><option v-for="h in 151" :key="h" :value="h + 99">{{ h + 99 }} cm</option></select></label>
      <label class="wide">참고 링크<input v-model.trim="model.sourceUrl" type="url" maxlength="2000" placeholder="직접 확인한 선수 정보 페이지 주소"></label>
      <label class="wide">추가 설명<textarea v-model.trim="model.note" rows="2" maxlength="2000" placeholder="확인이 필요한 내용이 있으면 적어주세요." /></label>
    </div>
    <p class="hint">* 필수 항목 · 명단 적용 시작일은 선수가 해당 팀 명단에 포함되어야 하는 날짜입니다. 모르는 주발·키는 미확인으로 제출하세요.</p>
  </fieldset>
</template>

<style scoped>
.registrationFields{border:0;padding:0;margin:0;min-width:0}.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}label{display:flex;flex-direction:column;gap:6px;color:#c5cfdb;font-size:13px}input,select,textarea{box-sizing:border-box;width:100%;min-height:39px;background:#111820;color:#eef4fa;border:1px solid #384659;border-radius:6px;padding:8px;font:inherit;color-scheme:dark}.wide{grid-column:1/-1}.hint{color:#9aaabb;font-size:12px;line-height:1.6}.error{color:#ffb4a9}fieldset:disabled{opacity:.7}@media(max-width:750px){.fields{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:440px){.fields{grid-template-columns:1fr}}
</style>
