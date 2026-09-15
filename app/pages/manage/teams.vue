<script setup lang="ts">
// 팀 관리 — 원래 UI(표 + 1부리그/1부아님 두 구역 + 행 안에서 펼쳐지는 상세)를 그대로 쓰되,
// 데이터는 전부 실제 Firestore다(더미 아님): teams 컬렉션 + teams/{id}/seasons(시즌별
// 1부/1부아님 — ff_team의 t_begin/t_end 행이 그 시즌[8월~이듬해 5월] 구간을 실제로
// 덮는지로 수기 반영해둠, coach-import 때와 같은 방식) + coachContracts(감독 재임 이력,
// manage/coaches/index.vue와 app/utils/coachContracts.ts를 그대로 공유 — 두 화면 중
// 어디서 고쳐도 같이 갱신된다).
//
// TeamDoc.currentDivision은 "지금" 시즌(seasons[0])의 캐시일 뿐이다 — 실제 정본은
// teams/{id}/seasons/{seasonId}. 그래서 시즌 배지를 클릭하면 그 시즌 서브문서를 갱신하고,
// 지금 보고 있는 게 "현재" 시즌일 때만 currentDivision 캐시도 같이 맞춘다.

import {
  collection, deleteDoc, doc, getDoc, getDocs, orderBy, query, setDoc, Timestamp, type Firestore,
} from 'firebase/firestore'
import type { StadiumDoc, TeamDoc } from '~/types/schema'
import {
  addCoachContract, deleteCoachContract, fetchCoachContracts, todayLabel, updateCoachContract,
  type CoachContract, type CoachStatus,
} from '~/utils/coachContracts'

const { $db } = useNuxtApp()
const db = $db as Firestore

type Division = 'D1' | 'D2'
type TeamRow = TeamDoc & { id: string }

const LEAGUES = [
  { id: 'EPL', label: '프리미어리그' },
  { id: 'LALIGA', label: '라리가' },
]
const seasons = ['2026/27', '2025/26', '2024/25', '2023/24', '2022/23', '2021/22', '2020/21']
const selectedSeason = ref(seasons[0])
const selectedLeague = ref('EPL')

const teams = ref<TeamRow[]>([])
const teamsLoading = ref(true)
const teamsError = ref('')
const stadiumNames = ref<Map<string, string>>(new Map())
// 목록의 "현 감독" 칸용 — 팀마다 따로 조회하면 N+1이라, coachContracts를 한 번에 다 불러와서
// teamId별 "to === null"인 것만 골라 맵으로 만든다.
const currentCoachByTeam = ref<Map<string, string>>(new Map())

onMounted(async () => {
  try {
    const [teamsSnap, stadiumsSnap, coachSnap] = await Promise.all([
      getDocs(query(collection(db, 'teams'), orderBy('name'))),
      getDocs(collection(db, 'stadiums')),
      getDocs(collection(db, 'coachContracts')),
    ])
    teams.value = teamsSnap.docs.map(d => ({ id: d.id, ...(d.data() as TeamDoc) }))
    stadiumNames.value = new Map(stadiumsSnap.docs.map(d => {
      const s = d.data() as StadiumDoc
      return [d.id, s.nameKr || s.name]
    }))
    const coachMap = new Map<string, string>()
    for (const d of coachSnap.docs) {
      const c = d.data() as CoachContract
      if (c.to === null) coachMap.set(c.teamId, c.coachName)
    }
    currentCoachByTeam.value = coachMap
    await loadSeasonDivisions() // teams.value가 채워진 다음에 호출해야 한다 — 팀 목록으로 조회 대상을 정하므로
  } catch (e) {
    teamsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    teamsLoading.value = false
  }
})

function currentCoachLabel(team: TeamRow) { return currentCoachByTeam.value.get(team.id) ?? '-' }

// ---- 시즌별 1부/1부아님 (teams/{id}/seasons가 정본) ----
const seasonDivisionByTeam = ref<Map<string, Division>>(new Map())
const seasonDocId = (season: string) => season.replace('/', '-')
const isCurrentSeason = computed(() => selectedSeason.value === seasons[0])

async function loadSeasonDivisions() {
  // collectionGroup 쿼리는 필터가 하나만 있어도 Firestore 색인을 미리 만들어둬야 해서
  // (색인 관리 인프라를 새로 안 건드리려고) 대신 이미 알고 있는 팀 목록으로 각 팀의 그
  // 시즌 문서를 경로 그대로 직접 읽는다 — 정확한 경로 단건 조회라 색인이 필요 없다.
  const seasonId = seasonDocId(selectedSeason.value)
  const targets = teams.value.filter(t => t.currentLeagueId === selectedLeague.value)
  try {
    const results = await Promise.all(targets.map(async t => {
      const snap = await getDoc(doc(db, 'teams', t.id, 'seasons', seasonId))
      const division = snap.exists() ? (snap.data() as { division?: Division }).division : undefined
      return [t.id, division] as const
    }))
    const map = new Map<string, Division>()
    for (const [teamId, division] of results) if (division) map.set(teamId, division)
    seasonDivisionByTeam.value = map
  } catch (e) {
    teamsError.value = e instanceof Error ? e.message : String(e)
  }
}

function divisionOf(team: TeamRow): Division | undefined {
  return seasonDivisionByTeam.value.get(team.id)
}

const leagueTeams = computed(() => teams.value.filter(t => t.currentLeagueId === selectedLeague.value))
// 반드시 명시적으로 D1/D2인 것만 각 구역에 넣는다. 그 시즌 서브문서가 아예 없는 팀(아직
// 분류 안 함, 또는 이 시즌 데이터를 아직 안 채워넣음)을 "D2가 아니니까 1부"로 잘못
// 취급했던 게 실제 버그였다 — 그런 팀은 미분류 구역으로 뺀다.
const firstDivision = computed(() => leagueTeams.value.filter(t => divisionOf(t) === 'D1'))
const otherTeams = computed(() => leagueTeams.value.filter(t => divisionOf(t) === 'D2'))
const unclassifiedTeams = computed(() => leagueTeams.value.filter(t => !divisionOf(t)))

function teamLabel(t: TeamRow) { return t.nameKr || t.name }
function stadiumLabel(id?: string | null) {
  if (!id) return '-'
  return stadiumNames.value.get(id) ? `${id} (${stadiumNames.value.get(id)})` : id
}
function divisionLabel(d?: Division) { return d === 'D2' ? '1부 아님' : '1부 리그' }

/** 리그 배지를 클릭하면 1부/1부 아님 사이를 바로 오간다 — 지금 보고 있는 시즌 서브문서에 반영. */
async function toggleDivision(team: TeamRow) {
  await setDivision(team, divisionOf(team) === 'D2' ? 'D1' : 'D2')
}
/** 미분류 팀을 1부/1부아님으로 처음 확정할 때도 쓴다(toggleDivision과 달리 목표값을 직접 받는다). */
async function setDivision(team: TeamRow, next: Division) {
  const prevMap = seasonDivisionByTeam.value
  const nextMap = new Map(prevMap)
  nextMap.set(team.id, next)
  seasonDivisionByTeam.value = nextMap // 낙관적 갱신 — 목록이 바로 재분류되게
  const prevCurrent = team.currentDivision
  try {
    await setDoc(doc(db, 'teams', team.id, 'seasons', seasonDocId(selectedSeason.value)), {
      leagueId: selectedLeague.value, division: next, seasonId: selectedSeason.value,
      updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
    }, { merge: true })
    // "현재" 시즌을 보고 있을 때만 팀 문서의 currentDivision 캐시도 같이 맞춘다 — 과거
    // 시즌을 고치는 중에 "지금" 값을 덮어쓰면 안 되니까.
    if (isCurrentSeason.value) {
      team.currentDivision = next
      await setDoc(doc(db, 'teams', team.id), { currentDivision: next, updatedAt: Timestamp.now(), updatedBy: 'manage-ui' }, { merge: true })
    }
  } catch (e) {
    seasonDivisionByTeam.value = prevMap // 실패하면 되돌림
    team.currentDivision = prevCurrent
    teamsError.value = e instanceof Error ? e.message : String(e)
  }
}

// ---- 상세정보(행 클릭하면 그 아래로 펼쳐짐) ----
const selectedTeam = ref<TeamRow | null>(null)
const isEditingDetail = ref(false)
const editFields = ref({ nameKr: '', nameFull: '', nameShort: '', stadiumId: '', crestUrl: '' })
const savingTeam = ref(false)

function toggleDetail(team: TeamRow) {
  if (selectedTeam.value?.id === team.id) { closeDetail(); return }
  isEditingDetail.value = false
  selectedTeam.value = team
  contracts.value = []
  loadContracts()
}
function closeDetail() { selectedTeam.value = null; isEditingDetail.value = false }

watch([selectedSeason, selectedLeague], () => { closeDetail(); loadSeasonDivisions() })

function startEditDetail() {
  if (!selectedTeam.value) return
  const t = selectedTeam.value
  editFields.value = {
    nameKr: t.nameKr ?? '', nameFull: t.nameFull ?? '', nameShort: t.nameShort ?? '',
    stadiumId: t.stadiumId ?? '', crestUrl: t.crestUrl ?? '',
  }
  isEditingDetail.value = true
}
function cancelEditDetail() { isEditingDetail.value = false }

async function applyEditDetail() {
  if (!selectedTeam.value) return
  savingTeam.value = true
  try {
    const patch = { ...editFields.value, updatedAt: Timestamp.now(), updatedBy: 'manage-ui' }
    await setDoc(doc(db, 'teams', selectedTeam.value.id), patch, { merge: true })
    Object.assign(selectedTeam.value, patch)
    isEditingDetail.value = false
  } catch (e) {
    teamsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingTeam.value = false
  }
}

async function deleteTeam(team: TeamRow) {
  if (!confirm(`${teamLabel(team)} 팀을 삭제하시겠습니까? (감독 이력은 남습니다)`)) return
  savingTeam.value = true
  try {
    await deleteDoc(doc(db, 'teams', team.id))
    teams.value = teams.value.filter(t => t.id !== team.id)
    if (selectedTeam.value?.id === team.id) closeDetail()
  } catch (e) {
    teamsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingTeam.value = false
  }
}

// ---- 팀 추가 ----
// 새 팀은 레거시 T_Code가 없으니 코드를 직접 입력받는다 — 다른 컬렉션(matches 등)이
// 전부 이 코드를 팀 참조로 쓰므로, 나중에 매치를 붙일 걸 생각하면 여기서 한 번 정하고
// 넘어가는 게 맞다. 1부/1부아님은 여기서 안 정한다 — 만들고 나면 "미분류"로 뜨니, 목록에서
// 배지 눌러 지금 보고 있는 시즌 기준으로 확정한다(기존 팀들과 같은 흐름).
const addTeamOpen = ref(false)
const savingNewTeam = ref(false)
const newTeamError = ref('')
const newTeam = ref({ id: '', nameKr: '', nameFull: '', nameShort: '', stadiumId: '', crestUrl: '', leagueId: 'EPL' })

function openAddTeam() {
  addTeamOpen.value = true
  newTeamError.value = ''
  newTeam.value = { id: '', nameKr: '', nameFull: '', nameShort: '', stadiumId: '', crestUrl: '', leagueId: selectedLeague.value }
}
function cancelAddTeam() { addTeamOpen.value = false }

async function submitAddTeam() {
  const id = newTeam.value.id.trim()
  const nameKr = newTeam.value.nameKr.trim()
  if (!id || !nameKr) { newTeamError.value = '팀 코드와 팀명은 필수입니다.'; return }
  savingNewTeam.value = true
  newTeamError.value = ''
  try {
    const existing = await getDoc(doc(db, 'teams', id))
    if (existing.exists()) { newTeamError.value = `팀 코드 "${id}"는 이미 있습니다.`; return }
    const data = {
      name: nameKr, nameKr, nameFull: newTeam.value.nameFull.trim(), nameShort: newTeam.value.nameShort.trim(),
      stadiumId: newTeam.value.stadiumId.trim() || null, crestUrl: newTeam.value.crestUrl.trim() || null,
      currentLeagueId: newTeam.value.leagueId,
      createdAt: Timestamp.now(), createdBy: 'manage-ui', updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
    }
    await setDoc(doc(db, 'teams', id), data)
    teams.value = [...teams.value, { id, ...data } as TeamRow].sort((a, b) => (a.name ?? '').localeCompare(b.name ?? ''))
    addTeamOpen.value = false
  } catch (e) {
    newTeamError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingNewTeam.value = false
  }
}

// ---- 감독 재임 이력 (manage/coaches/index.vue와 app/utils/coachContracts.ts 공유) ----
const contracts = ref<CoachContract[]>([])
const contractsLoading = ref(false)
const contractsError = ref('')

async function loadContracts() {
  if (!selectedTeam.value) return
  contractsLoading.value = true
  contractsError.value = ''
  try {
    contracts.value = await fetchCoachContracts(db, selectedTeam.value.id)
    // 목록의 "현 감독" 칸도 같이 갱신 — 추가/수정/삭제 후 페이지를 새로고침 안 해도 맞게 보이도록.
    const current = contracts.value.find(c => c.to === null)
    const map = new Map(currentCoachByTeam.value)
    if (current) map.set(selectedTeam.value.id, current.coachName)
    else map.delete(selectedTeam.value.id)
    currentCoachByTeam.value = map
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    contractsLoading.value = false
  }
}

const addOpen = ref(false)
const newName = ref('')
const newNameEn = ref('')
const newStatus = ref<CoachStatus>('MANAGER')

function openAdd() { addOpen.value = true; newName.value = ''; newNameEn.value = ''; newStatus.value = 'MANAGER' }
async function submitAdd() {
  if (!selectedTeam.value || !newName.value.trim()) return
  contractsLoading.value = true
  try {
    await addCoachContract(db, selectedTeam.value.id, contracts.value, {
      coachName: newName.value.trim(), coachNameEn: newNameEn.value.trim(), status: newStatus.value,
    })
    await loadContracts()
    addOpen.value = false
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    contractsLoading.value = false
  }
}

const editingCoachId = ref<string | null>(null)
const editCoach = ref({ name: '', nameEn: '', from: '', to: '', status: 'MANAGER' as CoachStatus, current: false })

function openEditCoach(c: CoachContract) {
  editingCoachId.value = c.id
  editCoach.value = { name: c.coachName, nameEn: c.coachNameEn, from: c.from, to: c.to ?? '', status: c.status, current: c.to === null }
}
function cancelEditCoach() { editingCoachId.value = null }
async function submitEditCoach() {
  if (!editingCoachId.value) return
  contractsLoading.value = true
  try {
    await updateCoachContract(db, editingCoachId.value, {
      coachName: editCoach.value.name.trim(), coachNameEn: editCoach.value.nameEn.trim(),
      from: editCoach.value.from.trim(), to: editCoach.value.current ? null : (editCoach.value.to.trim() || null),
      status: editCoach.value.status,
    })
    await loadContracts()
    editingCoachId.value = null
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    contractsLoading.value = false
  }
}
async function removeCoach(c: CoachContract) {
  if (!confirm(`${c.coachName} 재임기간을 삭제하시겠습니까?`)) return
  contractsLoading.value = true
  try {
    await deleteCoachContract(db, c.id)
    await loadContracts()
  } catch (e) {
    contractsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    contractsLoading.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="frame">
      <header>
        <NuxtLink to="/manage" class="back">← 데이터 관리</NuxtLink>
        <h1>팀 관리</h1>
        <button class="add" @click="openAddTeam">+ 팀 추가</button>
      </header>

      <p v-if="teamsError" class="errBanner">{{ teamsError }}</p>

      <div v-if="addTeamOpen" class="addTeamForm">
        <label>팀 코드(ID)<input v-model="newTeam.id" placeholder="예: E-XX (레거시 T_Code 규칙과 맞추는 걸 권장)"></label>
        <label>팀명<input v-model="newTeam.nameKr"></label>
        <label>영문 정식명<input v-model="newTeam.nameFull"></label>
        <label>약칭<input v-model="newTeam.nameShort"></label>
        <label>리그
          <select v-model="newTeam.leagueId">
            <option v-for="l in LEAGUES" :key="l.id" :value="l.id">{{ l.label }}</option>
          </select>
        </label>
        <label>스타디움 ID<input v-model="newTeam.stadiumId" placeholder="예: UK_Emirates Stadium"></label>
        <label>엠블럼 URL<input v-model="newTeam.crestUrl"></label>
        <p v-if="newTeamError" class="addTeamError">{{ newTeamError }}</p>
        <div class="addTeamActions">
          <button :disabled="!newTeam.id.trim() || !newTeam.nameKr.trim() || savingNewTeam" class="applyBtn" @click="submitAddTeam">저장</button>
          <button @click="cancelAddTeam">취소</button>
        </div>
      </div>

      <div class="toolbar">
        <div class="filters">
          <label>리그
            <select v-model="selectedLeague">
              <option v-for="l in LEAGUES" :key="l.id" :value="l.id">{{ l.label }}</option>
            </select>
          </label>
          <label>시즌
            <select v-model="selectedSeason">
              <option v-for="s in seasons" :key="s">{{ s }}</option>
            </select>
          </label>
        </div>
        <span>{{ teamsLoading ? '불러오는 중...' : `${leagueTeams.length}개 팀` }}</span>
      </div>

      <section>
        <h2>1부 리그 <small>{{ firstDivision.length }}</small></h2>
        <div class="teamTable">
          <div class="teamHead"><span>팀명</span><span>영어 이름</span><span>스타디움</span><span>현 감독</span><span>현 상태</span></div>
          <template v-for="team in firstDivision" :key="team.id">
            <div class="teamRow" :class="{ open: selectedTeam?.id === team.id }" @click="toggleDetail(team)">
              <span class="teamNameCell"><img class="crest" :src="team.crestUrl ?? ''" alt="" @error="($event.target as HTMLImageElement).style.visibility = 'hidden'">{{ teamLabel(team) }}</span>
              <span>{{ team.nameFull || team.name }}</span>
              <span>{{ stadiumLabel(team.stadiumId) }}</span>
              <span>{{ currentCoachLabel(team) }}</span>
              <button class="leagueBadge" title="클릭하면 1부 아님으로 내려갑니다" @click.stop="toggleDivision(team)">{{ divisionLabel(divisionOf(team)) }}</button>
            </div>
            <div v-if="selectedTeam?.id === team.id" class="detail">
              <div class="detailHead">
                <h2><img class="crestLg" :src="team.crestUrl ?? ''" alt="" @error="($event.target as HTMLImageElement).style.visibility = 'hidden'">{{ teamLabel(team) }} 상세 정보</h2>
                <div class="detailActions">
                  <template v-if="isEditingDetail">
                    <button class="applyBtn" :disabled="savingTeam" @click="applyEditDetail">적용</button>
                    <button @click="cancelEditDetail">취소</button>
                  </template>
                  <template v-else>
                    <button @click="startEditDetail">수정</button>
                    <button class="deleteBtn" @click="deleteTeam(team)">삭제</button>
                    <button @click="closeDetail">닫기</button>
                  </template>
                </div>
              </div>
              <div class="fields">
                <label>팀명<input v-model="editFields.nameKr" :disabled="!isEditingDetail"></label>
                <label>영문 정식명<input v-model="editFields.nameFull" :disabled="!isEditingDetail"></label>
                <label>약칭<input v-model="editFields.nameShort" :disabled="!isEditingDetail"></label>
                <label>스타디움 ID<input v-model="editFields.stadiumId" :disabled="!isEditingDetail" placeholder="예: UK_Emirates Stadium"></label>
                <label>엠블럼 URL<input v-model="editFields.crestUrl" :disabled="!isEditingDetail"></label>
              </div>

              <div class="coachHistory">
                <div class="coachHistHead">
                  <b>감독 재임 이력</b>
                  <button class="addCoachBtn" @click="openAdd">+ 감독 추가</button>
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
                    <button :disabled="!newName.trim()" class="applyBtn" @click="submitAdd">저장</button>
                    <button @click="addOpen = false">취소</button>
                  </div>
                </div>
                <div v-if="contractsLoading" class="coachEmpty">불러오는 중...</div>
                <div v-else-if="contractsError" class="coachEmpty">{{ contractsError }}</div>
                <div v-else-if="!contracts.length" class="coachEmpty">등록된 감독 이력이 없습니다.</div>
                <div v-for="c in contracts" :key="c.id" class="coachRow" :class="{ current: c.to === null }">
                  <template v-if="editingCoachId === c.id">
                    <div class="editCoachFields">
                      <input v-model="editCoach.name" placeholder="감독 이름">
                      <input v-model="editCoach.nameEn" placeholder="영문명">
                      <input v-model="editCoach.from" placeholder="시작일">
                      <input v-model="editCoach.to" placeholder="종료일" :disabled="editCoach.current">
                      <label class="currentToggle"><input v-model="editCoach.current" type="checkbox"> 진행중</label>
                      <select v-model="editCoach.status">
                        <option value="MANAGER">정식</option>
                        <option value="CARETAKER">감독대행</option>
                      </select>
                      <span class="coachRowActions">
                        <button class="coachActionBtn" @click="submitEditCoach">적용</button>
                        <button class="coachActionBtn" @click="cancelEditCoach">취소</button>
                      </span>
                    </div>
                  </template>
                  <template v-else>
                    <span class="coachNameGroup">{{ c.coachName }} <i>({{ c.coachNameEn }})</i></span>
                    <span>{{ c.from }} ~ {{ c.to ?? '진행중' }}</span>
                    <span class="coachDash"></span>
                    <span>{{ c.status === 'CARETAKER' ? '대행' : '정식' }}</span>
                    <span class="coachRowActions">
                      <button class="coachActionBtn" @click="openEditCoach(c)">수정</button>
                      <button class="coachActionBtn coachRemoveBtn" @click="removeCoach(c)">삭제</button>
                    </span>
                  </template>
                </div>
              </div>
            </div>
          </template>
        </div>
      </section>

      <section v-if="otherTeams.length">
        <h2>1부 아님 <small>{{ otherTeams.length }}</small></h2>
        <div class="teamTable">
          <template v-for="team in otherTeams" :key="team.id">
            <div class="teamRow muted" :class="{ open: selectedTeam?.id === team.id }" @click="toggleDetail(team)">
              <span class="teamNameCell"><img class="crest" :src="team.crestUrl ?? ''" alt="" @error="($event.target as HTMLImageElement).style.visibility = 'hidden'">{{ teamLabel(team) }}</span>
              <span>{{ team.nameFull || team.name }}</span>
              <span>{{ stadiumLabel(team.stadiumId) }}</span>
              <span>{{ currentCoachLabel(team) }}</span>
              <button class="leagueBadge muted" title="클릭하면 1부 리그로 올라갑니다" @click.stop="toggleDivision(team)">{{ divisionLabel(divisionOf(team)) }}</button>
            </div>
            <div v-if="selectedTeam?.id === team.id" class="detail">
              <div class="detailHead">
                <h2><img class="crestLg" :src="team.crestUrl ?? ''" alt="" @error="($event.target as HTMLImageElement).style.visibility = 'hidden'">{{ teamLabel(team) }} 상세 정보</h2>
                <div class="detailActions">
                  <template v-if="isEditingDetail">
                    <button class="applyBtn" :disabled="savingTeam" @click="applyEditDetail">적용</button>
                    <button @click="cancelEditDetail">취소</button>
                  </template>
                  <template v-else>
                    <button @click="startEditDetail">수정</button>
                    <button class="deleteBtn" @click="deleteTeam(team)">삭제</button>
                    <button @click="closeDetail">닫기</button>
                  </template>
                </div>
              </div>
              <div class="fields">
                <label>팀명<input v-model="editFields.nameKr" :disabled="!isEditingDetail"></label>
                <label>영문 정식명<input v-model="editFields.nameFull" :disabled="!isEditingDetail"></label>
                <label>약칭<input v-model="editFields.nameShort" :disabled="!isEditingDetail"></label>
                <label>스타디움 ID<input v-model="editFields.stadiumId" :disabled="!isEditingDetail" placeholder="예: UK_Emirates Stadium"></label>
                <label>엠블럼 URL<input v-model="editFields.crestUrl" :disabled="!isEditingDetail"></label>
              </div>

              <div class="coachHistory">
                <div class="coachHistHead">
                  <b>감독 재임 이력</b>
                  <button class="addCoachBtn" @click="openAdd">+ 감독 추가</button>
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
                    <button :disabled="!newName.trim()" class="applyBtn" @click="submitAdd">저장</button>
                    <button @click="addOpen = false">취소</button>
                  </div>
                </div>
                <div v-if="contractsLoading" class="coachEmpty">불러오는 중...</div>
                <div v-else-if="contractsError" class="coachEmpty">{{ contractsError }}</div>
                <div v-else-if="!contracts.length" class="coachEmpty">등록된 감독 이력이 없습니다.</div>
                <div v-for="c in contracts" :key="c.id" class="coachRow" :class="{ current: c.to === null }">
                  <template v-if="editingCoachId === c.id">
                    <div class="editCoachFields">
                      <input v-model="editCoach.name" placeholder="감독 이름">
                      <input v-model="editCoach.nameEn" placeholder="영문명">
                      <input v-model="editCoach.from" placeholder="시작일">
                      <input v-model="editCoach.to" placeholder="종료일" :disabled="editCoach.current">
                      <label class="currentToggle"><input v-model="editCoach.current" type="checkbox"> 진행중</label>
                      <select v-model="editCoach.status">
                        <option value="MANAGER">정식</option>
                        <option value="CARETAKER">감독대행</option>
                      </select>
                      <span class="coachRowActions">
                        <button class="coachActionBtn" @click="submitEditCoach">적용</button>
                        <button class="coachActionBtn" @click="cancelEditCoach">취소</button>
                      </span>
                    </div>
                  </template>
                  <template v-else>
                    <span class="coachNameGroup">{{ c.coachName }} <i>({{ c.coachNameEn }})</i></span>
                    <span>{{ c.from }} ~ {{ c.to ?? '진행중' }}</span>
                    <span class="coachDash"></span>
                    <span>{{ c.status === 'CARETAKER' ? '대행' : '정식' }}</span>
                    <span class="coachRowActions">
                      <button class="coachActionBtn" @click="openEditCoach(c)">수정</button>
                      <button class="coachActionBtn coachRemoveBtn" @click="removeCoach(c)">삭제</button>
                    </span>
                  </template>
                </div>
              </div>
            </div>
          </template>
        </div>
      </section>

      <!-- 1부/1부아님이 한 번도 지정된 적 없는 팀. 레거시 이관이 currentLeagueId만 EPL로
           잘못 찍어놓고 승강 상태는 모르는 경우 — 여기 나오면 배지를 눌러 1부/1부아님으로
           확정해야 위 두 구역 중 하나로 들어간다. 잘못 확정해도 그 구역에서 다시 클릭하면 된다. -->
      <section v-if="unclassifiedTeams.length">
        <h2>미분류 <small>{{ unclassifiedTeams.length }}</small></h2>
        <div class="teamTable">
          <div v-for="team in unclassifiedTeams" :key="team.id" class="teamRow unclassified">
            <span class="teamNameCell"><img class="crest" :src="team.crestUrl ?? ''" alt="" @error="($event.target as HTMLImageElement).style.visibility = 'hidden'">{{ teamLabel(team) }}</span>
            <span>{{ team.nameFull || team.name }}</span>
            <span>{{ stadiumLabel(team.stadiumId) }}</span>
            <span>{{ currentCoachLabel(team) }}</span>
            <span class="classifyActions">
              <button class="leagueBadge" @click="setDivision(team, 'D1')">1부로 지정</button>
              <button class="leagueBadge muted" @click="setDivision(team, 'D2')">1부아님으로 지정</button>
            </span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page{width:1280px;height:800px;background:#0b0f17;color:#eee;padding:28px;box-sizing:border-box;overflow-y:auto}.frame{min-height:100%;border:1px solid #29313b;background:#151a21;padding:24px;box-sizing:border-box}header{display:flex;align-items:center;gap:20px;border-bottom:1px solid #29313b;padding-bottom:18px}h1{flex:1;text-align:center;font-size:26px;margin:0}.back{color:#9da7b3;text-decoration:none}.add{background:#f0b429;border:0;padding:10px 16px;font-weight:800;border-radius:4px;cursor:pointer}
.errBanner{margin:12px 0 0;padding:8px 12px;background:rgba(241,106,106,.12);border:1px solid rgba(241,106,106,.4);border-radius:4px;color:#f16a6a;font-size:12px}
.addTeamForm{margin-top:12px;padding:14px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;border:1px solid rgba(240,180,41,.4);border-radius:6px;background:rgba(240,180,41,.05)}
.addTeamForm label{display:flex;flex-direction:column;gap:4px;color:rgba(255,255,255,.5);font-size:11px}
.addTeamForm input,.addTeamForm select{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#fff;padding:7px;font-size:12px}
.addTeamError{grid-column:1/-1;color:#f16a6a;font-size:12px;margin:0}
.addTeamActions{grid-column:1/-1;display:flex;gap:8px}
.addTeamActions button{padding:8px 16px;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:transparent;color:#ddd;cursor:pointer;font-size:12px}
.addTeamActions .applyBtn:disabled{opacity:.4;cursor:not-allowed}
.toolbar{display:flex;justify-content:space-between;align-items:center;margin:20px 0;color:#aab3be}.filters{display:flex;gap:18px;align-items:center}select{margin-left:10px;background:#202731;color:#fff;border:1px solid #4a5563;padding:8px 28px 8px 10px;border-radius:4px}
section{margin-top:24px}h2{font-size:18px;margin:0 0 12px;border-left:4px solid #f0b429;padding-left:10px;display:flex;align-items:center;gap:10px}h2 small{color:#9da7b3;font-size:12px}
.teamTable{border:1px solid #303a48;border-radius:5px;overflow:hidden}.teamHead,.teamRow{display:grid;grid-template-columns:1fr 1.2fr 1.2fr 1.2fr 180px;align-items:center;gap:16px;padding:10px 14px}.teamHead{background:#202731;color:#8f9baa;font-size:12px;font-weight:800}.teamRow{border-top:1px solid #2a3340;background:#171d25;color:#eee;font-size:14px;cursor:pointer}.teamRow:hover{background:#242d38}.teamRow.open{background:#242d38;box-shadow:inset 3px 0 0 #f0b429}.teamRow span:first-child{font-weight:700}
.teamNameCell{display:flex;align-items:center;gap:10px}.crest{width:22px;height:22px;object-fit:contain;flex:0 0 auto}.crestLg{width:30px;height:30px;object-fit:contain}
.leagueBadge{justify-self:start;font:inherit;color:#f0b429;background:transparent;border:1px solid #f0b429;border-radius:3px;padding:4px 9px;font-size:11px;cursor:pointer}.leagueBadge:hover{background:rgba(240,180,41,.14)}.leagueBadge.muted{color:#9aa4b0;border-color:#596474}.leagueBadge.muted:hover{background:rgba(154,164,176,.14)}
.teamRow.unclassified{cursor:default}.classifyActions{display:flex;gap:6px;justify-self:end}
.detail{padding:18px;border-top:2px solid #f0b429;background:#1c222b}.detailHead{display:flex;justify-content:space-between;align-items:center}.detailHead h2{border:0;margin:0}.detailActions{display:flex;gap:8px}
.detailHead button{background:transparent;color:#ddd;border:1px solid #596474;padding:6px 12px;border-radius:3px;cursor:pointer}.detailHead .applyBtn{background:#f0b429;color:#191919;border-color:#f0b429;font-weight:800}.detailHead .applyBtn:disabled{opacity:.5;cursor:not-allowed}.detailHead .deleteBtn{color:#f16a6a;border-color:#7a3a3a}.detailHead .deleteBtn:hover{background:rgba(241,106,106,.12)}
.fields{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}.fields label{display:flex;flex-direction:column;gap:4px;color:#aab3be;font-size:11px}.fields input{margin:0;width:100%;box-sizing:border-box;background:#11161d;color:#fff;border:1px solid #3b4654;padding:7px;border-radius:3px}.fields input:disabled{color:#7f8996;background:#171c23;border-color:#2a323d;cursor:not-allowed}
.coachHistory{margin-top:16px;border-top:1px solid #303a48;padding-top:12px}.coachHistHead{display:flex;justify-content:space-between;align-items:center}.addCoachBtn{background:transparent;color:#f0b429;border:1px dashed #f0b429;border-radius:3px;padding:4px 10px;font-size:11px;cursor:pointer}.coachEmpty{margin-top:8px;color:#77818d;font-size:12px}
.addForm{display:grid;grid-template-columns:1fr 1fr 120px;gap:8px;padding:12px;margin-top:8px;border:1px solid rgba(0,217,255,.3);border-radius:6px;background:rgba(0,217,255,.05)}.addForm input,.addForm select{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#fff;padding:7px;font-size:12px}.addFromHint{grid-column:1/-1;color:rgba(255,255,255,.45);font-size:11px}.addActions{grid-column:1/-1;display:flex;gap:8px}.addActions button{padding:6px 14px;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:transparent;color:#ddd;cursor:pointer;font-size:12px}
.coachRow{display:grid;grid-template-columns:2.6fr 1fr auto 1fr 1fr;align-items:center;gap:8px;padding:8px 0;border-top:1px solid #232b36;font-size:12px;color:rgba(255,255,255,.8)}.coachRow.current{color:#f0b429}.coachDash{color:#7f8996;text-align:center}.coachNameGroup i{color:#9da7b3;font-style:italic;font-size:11px}
.coachRowActions{display:flex;gap:4px;justify-self:end}.coachActionBtn{background:transparent;color:#9da7b3;border:1px solid #596474;border-radius:3px;padding:4px 8px;font-size:10px;cursor:pointer}.coachRemoveBtn{color:#f16a6a;border-color:#7a3a3a}
.editCoachFields{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto 100px auto;gap:6px;align-items:center}.editCoachFields input,.editCoachFields select{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#fff;padding:6px;font-size:11px}.currentToggle{display:flex;align-items:center;gap:4px;color:rgba(255,255,255,.6);font-size:11px;white-space:nowrap}
.applyBtn{background:#f0b429!important;color:#191919!important;border-color:#f0b429!important;font-weight:700}
</style>
