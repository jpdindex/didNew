<script setup lang="ts">
// 선수 관리 — 리그/시즌/팀 필터로 좁힌 선수 목록을 한 표에 보여주고, 신원 수정 +
// 이적 이력(계약) 조회·추가를 지원한다. "전체 선수"가 이미 Firestore에 다 들어가 있어서
// (해외/타 리그 포함) 팀 필터 없이 리그+시즌만으로도 조회되지만, 그 리그 소속 팀들의
// 계약만 모아서 찾는 방식이라 결과는 항상 그 리그로 좁혀진다.

import { collection, doc, getDocs, limit, orderBy, query, setDoc, Timestamp, where, type Firestore } from 'firebase/firestore'
import type { PlayerDoc, TeamDoc } from '~/types/schema'
import {
  fetchPlayerContracts, fetchPlayersByTeamsAndSeason, todayLabel, transferPlayer, retirePlayer, departToOtherLeague,
  updatePlayerContract, deletePlayerContract, type PlayerContract, type PlayerRow,
} from '~/utils/playerContracts'

// "이적할 팀" 드롭다운의 특수 옵션(선수 추가 쪽 팀 선택엔 안 씀 — 신규 등록은 항상 실제
// 소속팀이 있어야 하니까). 우리가 안 쫓는 팀/리그로 나가는 경우에만 고른다.
const OTHER_LEAGUE = '__OTHER_LEAGUE__'

const { $db } = useNuxtApp()
const db = $db as Firestore

type TeamRow = TeamDoc & { id: string }

const LEAGUES = [
  { id: 'EPL', label: '프리미어리그' },
  { id: 'LALIGA', label: '라리가' },
]
const seasons = ['2026/27', '2025/26', '2024/25', '2023/24', '2022/23', '2021/22', '2020/21']

const teams = ref<TeamRow[]>([])
const selectedLeague = ref('EPL')
const selectedSeason = ref(seasons[0])
const selectedTeamFilter = ref('') // '' = 전체

const rows = ref<{ player: PlayerRow; contracts: PlayerContract[] }[]>([])
const loading = ref(true)
const loadError = ref('')
const nameSearch = ref('')

// 선수명 검색은 리그/시즌/팀 필터를 무시하고 전체 players 컬렉션을 직접 뒤진다 —
// "전체 선수 다 들어가 있다"는 그 방대한 데이터를 시즌 필터 없이도 찾을 수 있어야 해서다.
// 계약을 안 갖고 오므로(contracts:[]) 표시는 PlayerDoc에 캐시된 currentTeamId 등을 쓴다.
const searchResults = ref<PlayerRow[]>([])
const searching = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(nameSearch, q => {
  clearTimeout(searchTimer)
  const trimmed = q.trim()
  if (!trimmed) { searchResults.value = []; return }
  searchTimer = setTimeout(() => runNameSearch(trimmed), 300)
})
function prefixQuery(field: string, q: string) {
  return query(collection(db, 'players'), orderBy(field), where(field, '>=', q), where(field, '<=', q + ''), limit(50))
}
// name(국문)만 뒤지면 "Son"처럼 영어로 쳤을 때 안 걸린다 — nameEn도 같이 뒤진다.
// Firestore 범위 검색은 대소문자를 구분해서 정확히 일치해야 하니, 입력 그대로/첫 글자만 대문자
// 두 가지 대소문자 형태로 nameEn을 같이 질의해 웬만한 표기(Son, son 등)를 다 잡는다.
async function runNameSearch(q: string) {
  searching.value = true
  try {
    const capitalized = q.charAt(0).toUpperCase() + q.slice(1)
    const snaps = await Promise.all([
      getDocs(prefixQuery('name', q)),
      getDocs(prefixQuery('nameEn', q)),
      ...(capitalized !== q ? [getDocs(prefixQuery('nameEn', capitalized))] : []),
    ])
    const byId = new Map<string, PlayerRow>()
    for (const snap of snaps) {
      for (const d of snap.docs) byId.set(d.id, { id: d.id, ...(d.data() as PlayerDoc) })
    }
    searchResults.value = [...byId.values()]
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    searching.value = false
  }
}

const leagueTeams = computed(() => teams.value.filter(t => t.currentLeagueId === selectedLeague.value))
const filteredRows = computed(() => {
  if (nameSearch.value.trim()) return searchResults.value.map(p => ({ player: p, contracts: [] as PlayerContract[] }))
  return rows.value
})

function teamLabel(id: string) {
  const t = teams.value.find(t => t.id === id)
  return t ? (t.nameKr || t.name) : id
}
// 필터로 보고 있는 시즌 기준 "그 팀 계약"을 그 선수의 계약 목록에서 골라 표시용으로 쓴다 —
// 이적 이력이 있으면 한 선수가 여러 계약을 가질 수 있어서, 지금 필터에 맞는 것 하나를 고른다.
// 검색 결과(contracts가 비어있음)는 계약 대신 PlayerDoc의 currentTeamId 등 캐시를 쓴다.
function contractForFilter(r: { player: PlayerRow; contracts: PlayerContract[] }) {
  if (!r.contracts.length) {
    return r.player.currentTeamId ? { teamId: r.player.currentTeamId, no: r.player.currentNo, pos: r.player.currentPos } : undefined
  }
  if (selectedTeamFilter.value) return r.contracts.find(c => c.teamId === selectedTeamFilter.value) ?? r.contracts[0]
  return r.contracts.find(c => c.to === null) ?? r.contracts[0]
}

async function loadPlayers() {
  loading.value = true
  loadError.value = ''
  try {
    const teamIds = selectedTeamFilter.value ? [selectedTeamFilter.value] : leagueTeams.value.map(t => t.id)
    rows.value = await fetchPlayersByTeamsAndSeason(db, teamIds, selectedSeason.value)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const snap = await getDocs(query(collection(db, 'teams'), orderBy('name')))
    teams.value = snap.docs.map(d => ({ id: d.id, ...(d.data() as TeamDoc) }))
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  }
  await loadPlayers()
})
watch([selectedLeague, selectedSeason, selectedTeamFilter], () => { closeDetail(); loadPlayers() })
// 리그를 바꾸면 그 리그에 없는 팀이 필터에 남아있을 수 있으니 초기화한다.
watch(selectedLeague, () => { selectedTeamFilter.value = '' })

// ---- 상세(행 클릭) ----
const selectedPlayerId = ref<string | null>(null)
const isEditingDetail = ref(false)
const editFields = ref({ name: '', nameEn: '', nameFull: '', birth: '', height: '', foot: '' as '' | 'L' | 'R' | 'B', nation: '' })
const savingPlayer = ref(false)
const detailContracts = ref<PlayerContract[]>([])
const detailError = ref('')

function selectedRow() {
  return filteredRows.value.find(r => r.player.id === selectedPlayerId.value) ?? null
}

async function toggleDetail(r: { player: PlayerRow; contracts: PlayerContract[] }) {
  if (selectedPlayerId.value === r.player.id) { closeDetail(); return }
  isEditingDetail.value = false
  selectedPlayerId.value = r.player.id
  detailContracts.value = []
  await loadDetailContracts()
}
function closeDetail() { selectedPlayerId.value = null; isEditingDetail.value = false }

async function loadDetailContracts() {
  if (!selectedPlayerId.value) return
  try {
    detailContracts.value = await fetchPlayerContracts(db, selectedPlayerId.value)
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  }
}

function startEditDetail() {
  const r = selectedRow()
  if (!r) return
  editFields.value = {
    name: r.player.name ?? '', nameEn: r.player.nameEn ?? '', nameFull: r.player.nameFull ?? '',
    birth: r.player.birth ?? '', height: r.player.height != null ? String(r.player.height) : '',
    foot: (r.player.foot as '' | 'L' | 'R' | 'B') ?? '', nation: r.player.nation ?? '',
  }
  isEditingDetail.value = true
}
function cancelEditDetail() { isEditingDetail.value = false }

async function applyEditDetail() {
  const r = selectedRow()
  if (!r) return
  savingPlayer.value = true
  try {
    const patch = {
      name: editFields.value.name.trim(), nameEn: editFields.value.nameEn.trim() || null,
      nameFull: editFields.value.nameFull.trim() || null, birth: editFields.value.birth.trim() || null,
      height: editFields.value.height ? Number(editFields.value.height) : null,
      foot: editFields.value.foot || null, nation: editFields.value.nation.trim() || null,
      updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
    }
    await setDoc(doc(db, 'players', r.player.id), patch, { merge: true })
    Object.assign(r.player, patch)
    isEditingDetail.value = false
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingPlayer.value = false
  }
}

// ---- 이적/계약 추가 ----
const transferOpen = ref(false)
const transferForm = ref({
  teamId: '', no: '', pos: '', transferType: 'TRANSFER' as NonNullable<PlayerContract['transferType']>,
  from: '', fee: '', currency: 'EUR',
})
function openTransfer() {
  const r = selectedRow()
  transferForm.value = {
    teamId: '', no: '', pos: r ? (contractForFilter(r)?.pos ?? '') : '', transferType: 'TRANSFER',
    from: todayLabel(), fee: '', currency: 'EUR',
  }
  transferOpen.value = true
}
async function submitTransfer() {
  const r = selectedRow()
  if (!r || !transferForm.value.teamId || !transferForm.value.from.trim()) return
  savingPlayer.value = true
  detailError.value = ''
  try {
    if (transferForm.value.teamId === OTHER_LEAGUE) {
      await departToOtherLeague(db, r.player.id, transferForm.value.from.trim())
    } else {
      await transferPlayer(db, r.player.id, r.player.name, {
        teamId: transferForm.value.teamId, no: transferForm.value.no || undefined, pos: transferForm.value.pos || undefined,
        leagueId: selectedLeague.value, transferType: transferForm.value.transferType, from: transferForm.value.from.trim(),
        fee: transferForm.value.fee ? Number(transferForm.value.fee) : undefined,
        currency: transferForm.value.fee ? transferForm.value.currency : undefined,
      })
    }
    await loadDetailContracts()
    await loadPlayers()
    transferOpen.value = false
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingPlayer.value = false
  }
}

// ---- 은퇴 처리 — transferPlayer와 달리 옮겨갈 팀이 없어서 별도 버튼으로 뺀다 ----
async function submitRetire() {
  const r = selectedRow()
  if (!r) return
  if (!confirm(`${r.player.name} 선수를 은퇴 처리하시겠습니까? (모든 팀 스쿼드에서 빠집니다)`)) return
  savingPlayer.value = true
  detailError.value = ''
  try {
    await retirePlayer(db, r.player.id)
    await loadDetailContracts()
    await loadPlayers()
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingPlayer.value = false
  }
}

// ---- 계약 한 건 수정/삭제(오타 등 교정용) ----
const editingContractId = ref<string | null>(null)
const editContract = ref({ from: '', to: '', no: '', pos: '', current: false })
function openEditContract(c: PlayerContract) {
  editingContractId.value = c.id
  editContract.value = { from: c.from, to: c.to ?? '', no: c.no ?? '', pos: c.pos ?? '', current: c.to === null }
}
function cancelEditContract() { editingContractId.value = null }
async function submitEditContract() {
  const r = selectedRow()
  if (!r || !editingContractId.value) return
  try {
    await updatePlayerContract(db, r.player.id, editingContractId.value, {
      from: editContract.value.from.trim(), to: editContract.value.current ? null : (editContract.value.to.trim() || null),
      no: editContract.value.no.trim() || null, pos: editContract.value.pos.trim() || null,
    })
    await loadDetailContracts()
    await loadPlayers()
    editingContractId.value = null
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  }
}
async function removeContract(c: PlayerContract) {
  const r = selectedRow()
  if (!r) return
  if (!confirm(`${teamLabel(c.teamId)} 계약(${c.from} ~ ${c.to ?? '진행중'})을 삭제하시겠습니까?`)) return
  try {
    await deletePlayerContract(db, r.player.id, c.id)
    await loadDetailContracts()
  } catch (e) {
    detailError.value = e instanceof Error ? e.message : String(e)
  }
}

// ---- 선수 추가 ----
const addOpen = ref(false)
const savingNewPlayer = ref(false)
const newPlayerError = ref('')
const newPlayer = ref({
  name: '', nameEn: '', nameFull: '', birth: '', nation: '', teamId: '', no: '', pos: '', from: '',
  transferType: 'TRANSFER' as 'YOUTH' | 'FREE' | 'TRANSFER', fee: '', currency: 'EUR',
})
function openAddPlayer() {
  addOpen.value = true
  newPlayerError.value = ''
  newPlayer.value = {
    name: '', nameEn: '', nameFull: '', birth: '', nation: '', teamId: selectedTeamFilter.value, no: '', pos: '',
    from: todayLabel(), transferType: 'TRANSFER', fee: '', currency: 'EUR',
  }
}
function cancelAddPlayer() { addOpen.value = false }
async function submitAddPlayer() {
  const name = newPlayer.value.name.trim()
  if (!name) { newPlayerError.value = '선수 이름은 필수입니다.'; return }
  if (!newPlayer.value.teamId) { newPlayerError.value = '소속팀을 선택하세요.'; return }
  if (!newPlayer.value.from.trim()) { newPlayerError.value = '입단일을 입력하세요.'; return }
  savingNewPlayer.value = true
  newPlayerError.value = ''
  try {
    const id = doc(collection(db, 'players')).id
    await setDoc(doc(db, 'players', id), {
      name, nameEn: newPlayer.value.nameEn.trim() || null, nameFull: newPlayer.value.nameFull.trim() || null,
      birth: newPlayer.value.birth.trim() || null,
      nation: newPlayer.value.nation.trim() || null, active: true,
      createdAt: Timestamp.now(), createdBy: 'manage-ui', updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
    })
    await transferPlayer(db, id, name, {
      teamId: newPlayer.value.teamId, no: newPlayer.value.no || undefined, pos: newPlayer.value.pos || undefined,
      leagueId: selectedLeague.value, transferType: newPlayer.value.transferType, from: newPlayer.value.from.trim(),
      fee: newPlayer.value.fee ? Number(newPlayer.value.fee) : undefined,
      currency: newPlayer.value.fee ? newPlayer.value.currency : undefined,
    })
    addOpen.value = false
    await loadPlayers()
  } catch (e) {
    newPlayerError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingNewPlayer.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="frame">
      <header>
        <NuxtLink to="/manage" class="back">← 데이터 관리</NuxtLink>
        <h1>선수 관리</h1>
        <button class="add" @click="openAddPlayer">+ 선수 추가</button>
      </header>

      <p v-if="loadError" class="errBanner">{{ loadError }}</p>

      <div v-if="addOpen" class="addForm">
        <label>이름<input v-model="newPlayer.name" placeholder="예: 부카요 사카"></label>
        <label>영문명<input v-model="newPlayer.nameEn"></label>
        <label>정식 영문명<input v-model="newPlayer.nameFull"></label>
        <label>생년월일<input v-model="newPlayer.birth" placeholder="YYYY.MM.DD"></label>
        <label>국적<input v-model="newPlayer.nation"></label>
        <label>소속팀
          <select v-model="newPlayer.teamId">
            <option value="" disabled>선택</option>
            <option v-for="t in leagueTeams" :key="t.id" :value="t.id">{{ t.nameKr || t.name }}</option>
          </select>
        </label>
        <label>등번호<input v-model="newPlayer.no"></label>
        <label>포지션<input v-model="newPlayer.pos" placeholder="예: FW/MF/DF/GK"></label>
        <label>입단일<input v-model="newPlayer.from" placeholder="YYYY.MM.DD"></label>
        <label>영입 유형
          <select v-model="newPlayer.transferType">
            <option value="TRANSFER">이적(영입)</option>
            <option value="FREE">자유계약</option>
            <option value="YOUTH">유스 승격</option>
          </select>
        </label>
        <label>이적료(선택)<input v-model="newPlayer.fee" type="number" placeholder="예: 5000000"></label>
        <label>통화
          <select v-model="newPlayer.currency" :disabled="!newPlayer.fee">
            <option value="EUR">EUR</option>
            <option value="GBP">GBP</option>
            <option value="USD">USD</option>
            <option value="KRW">KRW</option>
          </select>
        </label>
        <p v-if="newPlayerError" class="addFormError">{{ newPlayerError }}</p>
        <div class="addFormActions">
          <button :disabled="savingNewPlayer" class="applyBtn" @click="submitAddPlayer">저장</button>
          <button @click="cancelAddPlayer">취소</button>
        </div>
      </div>

      <div class="searchRow">
        <label class="searchLabel">검색
          <input v-model="nameSearch" placeholder="선수명 검색 — 전체 선수 대상, 리그·시즌·팀 필터 무시" class="nameSearchInput">
        </label>
      </div>
      <div class="toolbar">
        <div class="filters">
          <label>리그
            <select v-model="selectedLeague" :disabled="!!nameSearch.trim()">
              <option v-for="l in LEAGUES" :key="l.id" :value="l.id">{{ l.label }}</option>
            </select>
          </label>
          <label>시즌
            <select v-model="selectedSeason" :disabled="!!nameSearch.trim()">
              <option v-for="s in seasons" :key="s">{{ s }}</option>
            </select>
          </label>
          <label>팀
            <select v-model="selectedTeamFilter" :disabled="!!nameSearch.trim()">
              <option value="">전체</option>
              <option v-for="t in leagueTeams" :key="t.id" :value="t.id">{{ t.nameKr || t.name }}</option>
            </select>
          </label>
        </div>
        <span>{{ (loading || searching) ? '불러오는 중...' : `${filteredRows.length}명` }}</span>
      </div>

      <div class="playerTable">
        <div class="playerHead"><span>선수명</span><span>영문명</span><span>정식 영문명</span><span>포지션</span><span>등번호</span><span>소속팀</span><span>국적</span><span>생년월일</span></div>
        <div v-if="!loading && !searching && !filteredRows.length" class="playerEmpty">조건에 맞는 선수가 없습니다.</div>
        <template v-for="r in filteredRows" :key="r.player.id">
          <div class="playerRow" :class="{ open: selectedPlayerId === r.player.id }" @click="toggleDetail(r)">
            <span class="nameCell">{{ r.player.name }}</span>
            <span>{{ r.player.nameEn || '-' }}</span>
            <span>{{ r.player.nameFull || '-' }}</span>
            <span>{{ contractForFilter(r)?.pos || '-' }}</span>
            <span>{{ contractForFilter(r)?.no || '-' }}</span>
            <span>{{ teamLabel(contractForFilter(r)?.teamId ?? '') }}</span>
            <span>{{ r.player.nation || '-' }}</span>
            <span>{{ r.player.birth || '-' }}</span>
          </div>
          <div v-if="selectedPlayerId === r.player.id" class="detail">
            <div class="detailHead">
              <h2>{{ r.player.name }} 상세 정보</h2>
              <div class="detailActions">
                <template v-if="isEditingDetail">
                  <button class="applyBtn" :disabled="savingPlayer" @click="applyEditDetail">적용</button>
                  <button @click="cancelEditDetail">취소</button>
                </template>
                <template v-else>
                  <button @click="startEditDetail">수정</button>
                  <button class="transferBtn" @click="openTransfer">이적/등록번호 변경</button>
                  <button class="retireBtn" @click="submitRetire">은퇴 처리</button>
                  <button @click="closeDetail">닫기</button>
                </template>
              </div>
            </div>

            <p v-if="detailError" class="addFormError">{{ detailError }}</p>

            <div class="fields">
              <label>이름<input v-model="editFields.name" :disabled="!isEditingDetail"></label>
              <label>영문명<input v-model="editFields.nameEn" :disabled="!isEditingDetail"></label>
              <label>정식 영문명<input v-model="editFields.nameFull" :disabled="!isEditingDetail"></label>
              <label>생년월일<input v-model="editFields.birth" :disabled="!isEditingDetail" placeholder="YYYY.MM.DD"></label>
              <label>키(cm)<input v-model="editFields.height" :disabled="!isEditingDetail" type="number"></label>
              <label>주발
                <select v-model="editFields.foot" :disabled="!isEditingDetail">
                  <option value="">-</option>
                  <option value="L">왼발</option>
                  <option value="R">오른발</option>
                  <option value="B">양발</option>
                </select>
              </label>
              <label>국적<input v-model="editFields.nation" :disabled="!isEditingDetail"></label>
            </div>

            <div v-if="transferOpen" class="transferForm">
              <label>이적할 팀
                <select v-model="transferForm.teamId">
                  <option value="" disabled>선택</option>
                  <option v-for="t in leagueTeams" :key="t.id" :value="t.id">{{ t.nameKr || t.name }}</option>
                  <option :value="OTHER_LEAGUE">타 리그</option>
                </select>
              </label>
              <label>이적일<input v-model="transferForm.from" placeholder="YYYY.MM.DD"></label>
              <template v-if="transferForm.teamId !== OTHER_LEAGUE">
                <label>등번호<input v-model="transferForm.no"></label>
                <label>포지션<input v-model="transferForm.pos"></label>
                <label>이적 유형
                  <select v-model="transferForm.transferType">
                    <option value="TRANSFER">이적</option>
                    <option value="LOAN">임대</option>
                    <option value="LOAN_RETURN">임대복귀</option>
                    <option value="FREE">자유계약</option>
                  </select>
                </label>
                <label>이적료(선택)<input v-model="transferForm.fee" type="number" placeholder="예: 5000000"></label>
                <label>통화
                  <select v-model="transferForm.currency" :disabled="!transferForm.fee">
                    <option value="EUR">EUR</option>
                    <option value="GBP">GBP</option>
                    <option value="USD">USD</option>
                    <option value="KRW">KRW</option>
                  </select>
                </label>
              </template>
              <span v-if="transferForm.teamId === OTHER_LEAGUE" class="addFromHint">우리가 추적하지 않는 팀/리그로 나가는 경우입니다 — 새 계약은 안 만들고, 지금 계약만 닫고 스쿼드에서 뺍니다. (은퇴가 아니므로 계속 활성 선수로 남습니다)</span>
              <span v-else class="addFromHint">오늘({{ todayLabel() }})부터 새 팀 소속으로 등록되고, 이전 팀 스쿼드에선 자동으로 빠집니다.</span>
              <div class="addActions">
                <button :disabled="!transferForm.teamId || savingPlayer" class="applyBtn" @click="submitTransfer">확정</button>
                <button @click="transferOpen = false">취소</button>
              </div>
            </div>

            <div class="contractHistory">
              <b>이적/계약 이력</b>
              <div v-if="!detailContracts.length" class="contractEmpty">등록된 계약이 없습니다.</div>
              <div v-for="c in detailContracts" :key="c.id" class="contractRow" :class="{ current: c.to === null }">
                <template v-if="editingContractId === c.id">
                  <div class="editContractFields">
                    <input v-model="editContract.from" placeholder="시작일">
                    <input v-model="editContract.to" placeholder="종료일" :disabled="editContract.current">
                    <label class="currentToggle"><input v-model="editContract.current" type="checkbox"> 진행중</label>
                    <input v-model="editContract.no" placeholder="등번호">
                    <input v-model="editContract.pos" placeholder="포지션">
                    <span class="contractRowActions">
                      <button class="contractActionBtn" @click="submitEditContract">적용</button>
                      <button class="contractActionBtn" @click="cancelEditContract">취소</button>
                    </span>
                  </div>
                </template>
                <template v-else>
                  <span class="teamNameGroup">{{ teamLabel(c.teamId) }}</span>
                  <span>{{ c.from }} ~ {{ c.to ?? '진행중' }}</span>
                  <span>{{ c.no ? `#${c.no}` : '-' }} {{ c.pos || '' }}</span>
                  <span class="contractRowActions">
                    <button class="contractActionBtn" @click="openEditContract(c)">수정</button>
                    <button class="contractActionBtn contractRemoveBtn" @click="removeContract(c)">삭제</button>
                  </span>
                </template>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page{width:1280px;height:800px;background:#0b0f17;color:#eee;padding:28px;box-sizing:border-box;overflow-y:auto}.frame{min-height:100%;border:1px solid #29313b;background:#151a21;padding:24px;box-sizing:border-box}header{display:flex;align-items:center;gap:20px;border-bottom:1px solid #29313b;padding-bottom:18px}h1{flex:1;text-align:center;font-size:26px;margin:0}.back{color:#9da7b3;text-decoration:none}.add{background:#f0b429;border:0;padding:10px 16px;font-weight:800;border-radius:4px;cursor:pointer}
.errBanner{margin:12px 0 0;padding:8px 12px;background:rgba(241,106,106,.12);border:1px solid rgba(241,106,106,.4);border-radius:4px;color:#f16a6a;font-size:12px}
.addForm{margin-top:12px;padding:14px;display:grid;grid-template-columns:repeat(4,1fr);gap:10px;border:1px solid rgba(240,180,41,.4);border-radius:6px;background:rgba(240,180,41,.05)}
.addForm label{display:flex;flex-direction:column;gap:4px;color:rgba(255,255,255,.5);font-size:11px}
.addForm input,.addForm select{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#fff;padding:7px;font-size:12px}
.addFormError{grid-column:1/-1;color:#f16a6a;font-size:12px;margin:0}
.addFormActions{grid-column:1/-1;display:flex;gap:8px}
.addFormActions button{padding:8px 16px;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:transparent;color:#ddd;cursor:pointer;font-size:12px}
.toolbar{display:flex;justify-content:space-between;align-items:center;margin:20px 0;color:#aab3be}.filters{display:flex;gap:18px;align-items:center}select{margin-left:10px;background:#202731;color:#fff;border:1px solid #4a5563;padding:8px 28px 8px 10px;border-radius:4px}
.searchRow{margin-top:16px;padding:12px 14px;background:rgba(240,180,41,.1);border:1px solid rgba(240,180,41,.4);border-radius:6px}
.searchLabel{display:flex;align-items:center;gap:10px;color:#f0b429;font-weight:800;font-size:13px}
.nameSearchInput{flex:1;background:#11161d;color:#fff;border:1px solid rgba(240,180,41,.5);padding:9px 12px;border-radius:4px;width:100%;max-width:420px;box-sizing:border-box;font-size:13px}
.playerTable{border:1px solid #303a48;border-radius:5px;overflow:hidden}
.playerHead,.playerRow{display:grid;grid-template-columns:1.1fr 1fr 1.3fr .5fr .4fr 1.1fr .5fr .8fr;align-items:center;gap:14px;padding:10px 14px}
.playerHead{background:#202731;color:#8f9baa;font-size:12px;font-weight:800}
.playerRow{border-top:1px solid #2a3340;background:#171d25;color:#eee;font-size:14px;cursor:pointer}
.playerRow:hover{background:#242d38}
.playerRow.open{background:#242d38;box-shadow:inset 3px 0 0 #f0b429}
.nameCell{font-weight:700}
.playerEmpty{padding:20px;text-align:center;color:#77818d;font-size:13px}
.detail{padding:18px;border-top:2px solid #f0b429;background:#1c222b}.detailHead{display:flex;justify-content:space-between;align-items:center}.detailHead h2{margin:0;font-size:16px}.detailActions{display:flex;gap:8px}
.detailHead button{background:transparent;color:#ddd;border:1px solid #596474;padding:6px 12px;border-radius:3px;cursor:pointer;font-size:12px}
.detailHead .applyBtn{background:#f0b429;color:#191919;border-color:#f0b429;font-weight:800}
.detailHead .applyBtn:disabled{opacity:.5;cursor:not-allowed}
.detailHead .transferBtn{color:#7bd3ff;border-color:#2f6a86}
.detailHead .retireBtn{color:#f16a6a;border-color:#7a3a3a}
.fields{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}.fields label{display:flex;flex-direction:column;gap:4px;color:#aab3be;font-size:11px}.fields input,.fields select{margin:0;width:100%;box-sizing:border-box;background:#11161d;color:#fff;border:1px solid #3b4654;padding:7px;border-radius:3px}.fields input:disabled,.fields select:disabled{color:#7f8996;background:#171c23;border-color:#2a323d;cursor:not-allowed}
.transferForm{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;padding:12px;margin-top:14px;border:1px solid rgba(123,211,255,.35);border-radius:6px;background:rgba(123,211,255,.05)}
.transferForm label{display:flex;flex-direction:column;gap:4px;color:rgba(255,255,255,.55);font-size:11px}
.transferForm input,.transferForm select{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#fff;padding:7px;font-size:12px}
.addFromHint{grid-column:1/-1;color:rgba(255,255,255,.45);font-size:11px}
.addActions{grid-column:1/-1;display:flex;gap:8px}
.addActions button{padding:6px 14px;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:transparent;color:#ddd;cursor:pointer;font-size:12px}
.contractHistory{margin-top:16px;border-top:1px solid #303a48;padding-top:12px}
.contractEmpty{margin-top:8px;color:#77818d;font-size:12px}
.contractRow{display:grid;grid-template-columns:1.6fr 1.6fr 1.2fr auto;align-items:center;gap:8px;padding:8px 0;border-top:1px solid #232b36;font-size:12px;color:rgba(255,255,255,.8)}.contractRow.current{color:#f0b429}
.teamNameGroup{font-weight:700}
.contractRowActions{display:flex;gap:4px;justify-self:end}.contractActionBtn{background:transparent;color:#9da7b3;border:1px solid #596474;border-radius:3px;padding:4px 8px;font-size:10px;cursor:pointer}.contractRemoveBtn{color:#f16a6a;border-color:#7a3a3a}
.editContractFields{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr auto 1fr 1fr auto;gap:6px;align-items:center}.editContractFields input{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#fff;padding:6px;font-size:11px}.currentToggle{display:flex;align-items:center;gap:4px;color:rgba(255,255,255,.6);font-size:11px;white-space:nowrap}
.applyBtn{background:#f0b429!important;color:#191919!important;border-color:#f0b429!important;font-weight:700}
</style>
