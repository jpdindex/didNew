<script setup lang="ts">
// 감독 관리 — teams 컬렉션에서 팀을 고르고, 그 팀의 coachContracts(재임기간 원장)를
// 조회·추가·수정·삭제한다. Firestore에 실제로 읽고 쓰는 화면이다(manage/teams.vue의
// "감독 재임 이력"과 같은 로직을 app/utils/coachContracts.ts로 공유한다).

import { collection, getDocs, orderBy, query, type Firestore } from 'firebase/firestore'
import type { TeamDoc } from '~/types/schema'
import {
  addCoachContract, deleteCoachContract, fetchCoachContracts, todayLabel, updateCoachContract,
  type CoachContract, type CoachStatus,
} from '~/utils/coachContracts'

const { $db } = useNuxtApp()
const db = $db as Firestore

type TeamRow = TeamDoc & { id: string }

const teams = ref<TeamRow[]>([])
const teamsLoading = ref(true)
const teamsError = ref('')
const search = ref('')
const selectedLeague = ref('EPL')

onMounted(async () => {
  try {
    const snap = await getDocs(query(collection(db, 'teams'), orderBy('name')))
    teams.value = snap.docs.map(d => ({ id: d.id, ...(d.data() as TeamDoc) }))
    if (filteredTeams.value.length) await selectTeam(filteredTeams.value[0]!)
  } catch (e) {
    teamsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    teamsLoading.value = false
  }
})

// 레거시엔 UCL/UEL/K1/각국 대표팀 대회 등 리그 값이 워낙 다양하지만(대부분 currentLeagueId
// 값 자체도 이관 과정에서 덮어써져 부정확하다 — 아래 참고), 지금 이 화면에서 다루는 건
// EPL/라리가뿐이라 드롭다운도 이 둘로 고정한다.
const LEAGUES = ['EPL', 'LALIGA']

const filteredTeams = computed(() => {
  const q = search.value.trim().toLowerCase()
  return teams.value.filter(t => {
    if (t.currentLeagueId !== selectedLeague.value) return false
    if (!q) return true
    return t.name?.toLowerCase().includes(q) || t.nameKr?.toLowerCase().includes(q) ||
      t.nameFull?.toLowerCase().includes(q) || t.id.toLowerCase().includes(q)
  })
})

// 리그를 바꾸면 그 리그의 첫 팀을 기본으로 띄운다 — 고른 값과 무관한 팀이 계속 남아있지 않게.
watch(selectedLeague, () => {
  if (filteredTeams.value.length) selectTeam(filteredTeams.value[0]!)
  else selectedTeam.value = null
})

// ---- 선택된 팀의 감독 재임 이력 ----
const selectedTeam = ref<TeamRow | null>(null)
const contracts = ref<CoachContract[]>([])
const contractsLoading = ref(false)
const contractsError = ref('')

async function selectTeam(team: TeamRow) {
  if (selectedTeam.value?.id === team.id) { selectedTeam.value = null; return }
  selectedTeam.value = team
  contractsLoading.value = true
  contractsError.value = ''
  try {
    contracts.value = await fetchCoachContracts(db, team.id)
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    contractsLoading.value = false
  }
}

// ---- 새 감독 추가 ----
const addOpen = ref(false)
const newName = ref('')
const newNameEn = ref('')
const newStatus = ref<CoachStatus>('MANAGER')
const saving = ref(false)

function openAdd() { addOpen.value = true; newName.value = ''; newNameEn.value = ''; newStatus.value = 'MANAGER' }

async function submitAdd() {
  if (!selectedTeam.value || !newName.value.trim()) return
  saving.value = true
  try {
    await addCoachContract(db, selectedTeam.value.id, contracts.value, {
      coachName: newName.value.trim(), coachNameEn: newNameEn.value.trim(), status: newStatus.value,
    })
    contracts.value = await fetchCoachContracts(db, selectedTeam.value.id)
    addOpen.value = false
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

// ---- 기존 행 수정 ----
const editingId = ref<string | null>(null)
const editName = ref('')
const editNameEn = ref('')
const editFrom = ref('')
const editTo = ref('')
const editStatus = ref<CoachStatus>('MANAGER')
const editCurrent = ref(false)

function openEdit(c: CoachContract) {
  editingId.value = c.id
  editName.value = c.coachName
  editNameEn.value = c.coachNameEn
  editFrom.value = c.from
  editTo.value = c.to ?? ''
  editStatus.value = c.status
  editCurrent.value = c.to === null
}
function cancelEdit() { editingId.value = null }

async function submitEdit() {
  if (!editingId.value || !selectedTeam.value) return
  saving.value = true
  try {
    await updateCoachContract(db, editingId.value, {
      coachName: editName.value.trim(), coachNameEn: editNameEn.value.trim(),
      from: editFrom.value.trim(), to: editCurrent.value ? null : (editTo.value.trim() || null),
      status: editStatus.value,
    })
    contracts.value = await fetchCoachContracts(db, selectedTeam.value.id)
    editingId.value = null
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function removeContract(c: CoachContract) {
  if (!selectedTeam.value) return
  if (!confirm(`${c.coachName} 재임기간을 삭제하시겠습니까?`)) return
  saving.value = true
  try {
    await deleteCoachContract(db, c.id)
    contracts.value = await fetchCoachContracts(db, selectedTeam.value.id)
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

function teamLabel(t: TeamRow) { return t.nameKr || t.name }
</script>

<template>
  <div class="page">
    <div class="bg" />
    <div class="frame">
      <div class="topBar">
        <div class="title">감독 관리</div>
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리로</NuxtLink>
      </div>

      <div class="body">
        <section class="teamPane">
          <select v-model="selectedLeague" class="leagueSelect">
            <option v-for="l in LEAGUES" :key="l" :value="l">{{ l }}</option>
          </select>
          <input v-model="search" class="search" placeholder="팀명 검색 (팀명 / 영문명 / 코드)" />
          <div v-if="teamsLoading" class="hint">불러오는 중...</div>
          <div v-else-if="teamsError" class="hint error">{{ teamsError }}</div>
          <div v-else class="teamList">
            <button
              v-for="t in filteredTeams" :key="t.id" class="teamItem"
              :class="{ on: selectedTeam?.id === t.id }" @click="selectTeam(t)"
            >
              <span class="tName">{{ teamLabel(t) }}</span>
              <span class="tCode">{{ t.id }}</span>
            </button>
            <div v-if="!filteredTeams.length" class="hint">일치하는 팀이 없습니다.</div>
          </div>
        </section>

        <section class="coachPane">
          <template v-if="!selectedTeam">
            <div class="hint big">왼쪽에서 팀을 선택하세요.</div>
          </template>
          <template v-else>
            <div class="coachHead">
              <h2>{{ teamLabel(selectedTeam) }} — 감독 재임 이력</h2>
              <button class="addBtn" @click="openAdd">+ 감독 추가</button>
            </div>

            <div v-if="addOpen" class="addForm">
              <input v-model="newName" placeholder="감독 이름(한글)" />
              <input v-model="newNameEn" placeholder="영문명" />
              <select v-model="newStatus">
                <option value="MANAGER">정식</option>
                <option value="CARETAKER">감독대행</option>
              </select>
              <span class="addFromHint">시작일: {{ todayLabel() }} (오늘) · 진행중으로 등록됩니다</span>
              <div class="addActions">
                <button :disabled="!newName.trim() || saving" class="applyBtn" @click="submitAdd">저장</button>
                <button @click="addOpen = false">취소</button>
              </div>
            </div>

            <div v-if="contractsLoading" class="hint">불러오는 중...</div>
            <div v-else-if="contractsError" class="hint error">{{ contractsError }}</div>
            <div v-else-if="!contracts.length" class="hint">등록된 감독 이력이 없습니다.</div>
            <div v-else class="contractList">
              <div class="contractHead">
                <span>감독</span><span>기간</span><span>상태</span><span></span>
              </div>
              <div v-for="c in contracts" :key="c.id" class="contractRow" :class="{ current: c.to === null }">
                <template v-if="editingId === c.id">
                  <div class="editRow">
                    <div class="editFields">
                      <input v-model="editName" placeholder="감독 이름">
                      <input v-model="editNameEn" placeholder="영문명">
                      <input v-model="editFrom" placeholder="시작일 (YYYY.MM.DD)">
                      <input v-model="editTo" placeholder="종료일" :disabled="editCurrent">
                      <label class="currentToggle"><input v-model="editCurrent" type="checkbox"> 진행중</label>
                      <select v-model="editStatus">
                        <option value="MANAGER">정식</option>
                        <option value="CARETAKER">감독대행</option>
                      </select>
                    </div>
                    <div class="editActions">
                      <button :disabled="saving" class="applyBtn" @click="submitEdit">적용</button>
                      <button @click="cancelEdit">취소</button>
                    </div>
                  </div>
                </template>
                <template v-else>
                  <span>{{ c.coachName }} <i class="en">({{ c.coachNameEn }})</i></span>
                  <span>{{ c.from }} ~ {{ c.to ?? '진행중' }}</span>
                  <span>{{ c.status === 'CARETAKER' ? '대행' : '정식' }}</span>
                  <span class="rowActions">
                    <button @click="openEdit(c)">수정</button>
                    <button class="deleteBtn" @click="removeContract(c)">삭제</button>
                  </span>
                </template>
              </div>
            </div>
          </template>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { box-sizing: border-box; width: 1280px; height: 800px; display: grid; place-items: center; position: relative; overflow: hidden; background: #0b0f17; }
.bg { position: absolute; inset: 0; background: radial-gradient(1200px 500px at 50% 35%, rgba(255,255,255,0.08), transparent 60%), linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.75)); }
.frame { box-sizing: border-box; position: relative; width: 1280px; height: 800px; background: rgba(10, 14, 22, 0.75); border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 18px 60px rgba(0,0,0,0.55); backdrop-filter: blur(8px); padding: 24px; display: flex; flex-direction: column; gap: 12px; }
.topBar { display: flex; align-items: center; justify-content: space-between; padding-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.06); }
.title { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; }
.backBtn { color: rgba(255,255,255,0.55); font-size: 13px; text-decoration: none; }
.backBtn:hover { color: #fff; }

.body { flex: 1; min-height: 0; display: grid; grid-template-columns: 260px 1fr; gap: 16px; }
.teamPane { min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.search, .leagueSelect { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12); border-radius: 4px; color: #fff; padding: 8px 10px; font-size: 12px; }
.teamList { flex: 1; min-height: 0; overflow-y: auto; border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; }
.teamItem { width: 100%; display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; background: transparent; border: 0; border-top: 1px solid rgba(255,255,255,0.05); color: rgba(255,255,255,0.8); font-size: 12px; cursor: pointer; text-align: left; }
.teamItem:first-child { border-top: 0; }
.teamItem:hover { background: rgba(255,255,255,0.04); }
.teamItem.on { background: rgba(240,180,41,0.14); color: #f0b429; }
.tCode { color: rgba(255,255,255,0.35); font-size: 10px; }

.coachPane { min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.coachHead { display: flex; justify-content: space-between; align-items: center; }
.coachHead h2 { margin: 0; font-size: 16px; color: #fff; }
.addBtn { background: rgba(0,217,255,0.12); border: 1px solid rgba(0,217,255,0.4); color: #0dc; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }

.hint { color: rgba(255,255,255,0.4); font-size: 12px; padding: 10px; }
.hint.big { padding: 40px 10px; text-align: center; }
.hint.error { color: #f16a6a; }

.addForm { display: grid; grid-template-columns: 1fr 1fr 120px; gap: 8px; padding: 12px; border: 1px solid rgba(0,217,255,0.3); border-radius: 6px; background: rgba(0,217,255,0.05); }
.addForm input, .addForm select { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12); border-radius: 4px; color: #fff; padding: 7px; font-size: 12px; }
.addFromHint { grid-column: 1 / -1; color: rgba(255,255,255,0.45); font-size: 11px; }
.addActions { grid-column: 1 / -1; display: flex; gap: 8px; }

.contractList { border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; overflow: hidden; }
.contractHead, .contractRow { display: grid; grid-template-columns: 1.6fr 1.4fr 0.6fr 0.9fr; gap: 8px; padding: 8px 10px; align-items: center; font-size: 12px; }
.contractHead { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.45); font-weight: 700; }
.contractRow { border-top: 1px solid rgba(255,255,255,0.06); color: rgba(255,255,255,0.8); }
.contractRow.current { color: #f0b429; }
.contractRow .en { color: rgba(255,255,255,0.4); font-style: italic; font-size: 11px; }
.rowActions { display: flex; gap: 4px; justify-self: end; }
.rowActions button { background: transparent; border: 1px solid rgba(255,255,255,0.15); color: rgba(255,255,255,0.7); border-radius: 3px; padding: 3px 8px; font-size: 10px; cursor: pointer; }
.deleteBtn { color: #f16a6a !important; border-color: rgba(241,106,106,0.4) !important; }

.editRow { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 8px; }
.editFields { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr auto 100px; gap: 6px; align-items: center; }
.editFields input, .editFields select { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12); border-radius: 4px; color: #fff; padding: 6px; font-size: 11px; }
.currentToggle { display: flex; align-items: center; gap: 4px; color: rgba(255,255,255,0.6); font-size: 11px; white-space: nowrap; }
.editActions { display: flex; gap: 8px; }
.editActions button, .addActions button { padding: 6px 14px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.15); background: transparent; color: #ddd; cursor: pointer; font-size: 12px; }
.applyBtn { background: #f0b429 !important; color: #191919 !important; border-color: #f0b429 !important; font-weight: 700; }
.applyBtn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
