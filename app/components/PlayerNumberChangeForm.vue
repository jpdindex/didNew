<script setup lang="ts">
import type { PlayerNumberChange } from '~/types/playerRegistration'
import { fetchPlayersByTeamsAndSeason, type PlayerContract, type PlayerRow } from '~/utils/playerContracts'

const model = defineModel<PlayerNumberChange>({ required: true })
const props = defineProps<{ disabled?: boolean }>()
const { $db } = useNuxtApp()
const players = ref<{ player: PlayerRow; contracts: PlayerContract[] }[]>([])
const loading = ref(false)
const error = ref('')
let loadVersion = 0
const selected = computed(() => players.value.find(row => row.player.id === model.value.playerId))
const today = new Date()
const maxDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`

function selectPlayer(playerId: string) {
  model.value.playerId = playerId
  updateCurrentNumber()
}

function updateCurrentNumber() {
  if (!selected.value) return
  const target = model.value.changeDate.replaceAll('-', '.')
  const contracts = selected.value.contracts.filter(contract => contract.teamId === model.value.teamId && contract.competitionType !== 'national')
  const source = target ? contracts.find(contract => contract.from <= target && (!contract.to || contract.to >= target)) : contracts[0]
  model.value.contractId = source?.id || ''
  model.value.currentNo = source?.no == null ? '' : String(source.no)
}

async function loadPlayers() {
  const version = ++loadVersion
  players.value = []
  error.value = ''
  if (!model.value.leagueId || !model.value.seasonId || !model.value.teamId) { loading.value = false; return }
  loading.value = true
  try {
    const rows = await fetchPlayersByTeamsAndSeason($db, [model.value.teamId], `${model.value.seasonId.slice(0, 4)}/${model.value.seasonId.slice(6)}`)
    if (version !== loadVersion) return
    players.value = rows.filter(row => row.contracts.some(contract => contract.teamId === model.value.teamId && contract.competitionType !== 'national'))
    // Reviewing a request keeps its original number so a stale request is visible.
    if (!model.value.playerId) return
    if (!selected.value) error.value = '선택한 선수의 시즌 소속을 찾을 수 없습니다. 선수와 변경 날짜를 확인하세요.'
  } catch (e) {
    if (version === loadVersion) error.value = e instanceof Error ? e.message : '선수 목록을 불러오지 못했습니다.'
  } finally { if (version === loadVersion) loading.value = false }
}

watch(() => [model.value.leagueId, model.value.seasonId, model.value.teamId], (_, previous) => {
  if (previous) {
    model.value.playerId = ''; model.value.contractId = ''; model.value.currentNo = ''
  }
  void loadPlayers()
}, { immediate: true })
watch(() => model.value.changeDate, updateCurrentNumber)
</script>

<template>
  <fieldset :disabled="props.disabled || loading" class="numberForm">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading">선수 목록을 불러오는 중...</p>
    <p v-else-if="!model.teamId">리그·시즌·팀을 먼저 선택하세요.</p>
    <template v-else>
      <label>선수 *<select :value="model.playerId" required @change="selectPlayer(($event.target as HTMLSelectElement).value)"><option value="" disabled>선수 선택</option><option v-for="row in players" :key="row.player.id" :value="row.player.id">{{ row.player.name }} · {{ row.contracts[0]?.no ?? '등번호 없음' }}번</option></select></label>
      <p v-if="!loading && !players.length">해당 시즌·팀 소속 선수가 없습니다.</p>
      <div v-if="model.playerId" class="numberFields">
        <label>현재 등번호<input :value="model.currentNo" readonly placeholder="미등록"></label>
        <label>변경할 등번호 *<input v-model.trim="model.newNo" required inputmode="numeric" pattern="[0-9]{1,3}" maxlength="3"></label>
        <label>변경 날짜 *<input v-model="model.changeDate" required type="date" :max="maxDate"></label>
      </div>
      <p v-if="model.playerId && model.changeDate && !model.contractId" class="error">변경 날짜에 해당 팀 소속 이력이 없습니다.</p>
    </template>
  </fieldset>
</template>

<style scoped>
.numberForm{border:0;padding:16px 0 0;margin:0;min-width:0}.numberFields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:14px}label{display:flex;flex-direction:column;gap:6px;color:#c5cfdb;font-size:13px}input,select{box-sizing:border-box;width:100%;min-height:39px;background:#111820;color:#eef4fa;border:1px solid #384659;border-radius:6px;padding:8px;font:inherit;color-scheme:dark}input[readonly]{opacity:.7}.error{color:#ffb4a9}@media(max-width:650px){.numberFields{grid-template-columns:1fr}}
</style>
