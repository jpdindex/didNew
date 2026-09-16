<script setup lang="ts">
// 경기 일정 관리 — matches 컬렉션을 보여주고 새 경기를 등록한다.
// gm_id 는 우리가 채번하지 않는다 — 담당자가 여기서 직접 입력한 문자열이 그대로 문서 ID가
// 된다(docs/04_firestore_schema.html §03). 형식: 시즌(8, 예 20262027) + 리그코드(3) +
// 리그 고정 상수(가변, 같은 리그면 항상 동일) + 일련번호(4). 오타 하나가 JaionX 매칭을
// 통째로 깨뜨리고, 같은 gm_id로 다시 저장하면 Firestore가 말없이 덮어쓰므로
// (1) 형식 검증 (2) 같은 리그 기존 gm_id와의 패턴 비교 (3) 중복 확인을 반드시 거친다.

import { collection, deleteDoc, deleteField, doc, getDoc, getDocs, orderBy, query, setDoc, Timestamp, writeBatch, type Firestore } from 'firebase/firestore'
import * as XLSX from 'xlsx'
import type { MatchDoc, StadiumDoc, TeamDoc } from '~/types/schema'

const { $db } = useNuxtApp()
const db = $db as Firestore

type MatchRow = MatchDoc & { id: string }
type TeamRow = TeamDoc & { id: string }

const LEAGUES = [
  { id: 'EPL', label: '프리미어리그' },
  { id: 'LALIGA', label: '라리가' },
]

const matches = ref<MatchRow[]>([])
const teams = ref<TeamRow[]>([])
const stadiums = ref<{ id: string; label: string }[]>([])
const loading = ref(true)
const loadError = ref('')

const selectedLeague = ref('EPL')

onMounted(async () => {
  try {
    const [matchesSnap, teamsSnap, stadiumsSnap] = await Promise.all([
      getDocs(query(collection(db, 'matches'), orderBy('date'))),
      getDocs(query(collection(db, 'teams'), orderBy('name'))),
      getDocs(collection(db, 'stadiums')),
    ])
    matches.value = matchesSnap.docs.map(d => ({ id: d.id, ...(d.data() as MatchDoc) }))
    teams.value = teamsSnap.docs.map(d => ({ id: d.id, ...(d.data() as TeamDoc) }))
    stadiums.value = stadiumsSnap.docs.map(d => {
      const s = d.data() as StadiumDoc
      return { id: d.id, label: s.nameKr || s.name }
    })
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})

function teamLabel(id: string) {
  const t = teams.value.find(t => t.id === id)
  return t ? (t.nameKr || t.name) : id
}
function stadiumLabel(id: string) {
  const s = stadiums.value.find(s => s.id === id)
  return s ? `${id} (${s.label})` : id
}
function teamStadiumId(teamId: string) {
  return teams.value.find(t => t.id === teamId)?.stadiumId
}
function leagueLabel(id: string) {
  return LEAGUES.find(l => l.id === id)?.label ?? id
}
function roundLabel(m: MatchRow) {
  if (m.matchType === 'tournament') {
    return [m.stage, m.group, m.leg ? `${m.leg}차전` : ''].filter(Boolean).join(' ')
  }
  return m.round !== undefined ? `R${m.round}` : '-'
}

const deletingId = ref<string | null>(null)
async function deleteMatch(m: MatchRow) {
  if (!confirm(`gm_id "${m.id}" (${teamLabel(m.homeTeamId)} vs ${teamLabel(m.awayTeamId)})를 삭제하시겠습니까?`)) return
  deletingId.value = m.id
  try {
    await deleteDoc(doc(db, 'matches', m.id))
    matches.value = matches.value.filter(x => x.id !== m.id)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    deletingId.value = null
  }
}

// ---- 경기 수정 — gm_id(문서 ID)는 안 바꾼다. 나머지 필드만 그 자리에서 고친다. ----
const editingMatchId = ref<string | null>(null)
const savingEdit = ref(false)
const editForm = ref({
  date: '', kickoffTime: '', round: '', stadiumId: '', homeTeamId: '', awayTeamId: '',
})

function openEditMatch(m: MatchRow) {
  editingMatchId.value = m.id
  formError.value = ''
  editForm.value = {
    date: m.date, kickoffTime: m.kickoffTime ?? '', round: m.round !== undefined ? String(m.round) : '',
    stadiumId: m.stadiumId, homeTeamId: m.homeTeamId, awayTeamId: m.awayTeamId,
  }
}
function cancelEditMatch() { editingMatchId.value = null }

async function applyEditMatch(m: MatchRow) {
  if (!editForm.value.date) { formError.value = '날짜를 입력하세요.'; return }
  if (!editForm.value.stadiumId) { formError.value = '경기장을 선택하세요.'; return }
  if (!editForm.value.homeTeamId || !editForm.value.awayTeamId) { formError.value = '홈/원정 팀을 선택하세요.'; return }
  if (editForm.value.homeTeamId === editForm.value.awayTeamId) { formError.value = '홈팀과 원정팀이 같을 수 없습니다.'; return }

  savingEdit.value = true
  formError.value = ''
  try {
    const roundNum = editForm.value.round ? Number(editForm.value.round) : undefined
    const kickoff = editForm.value.kickoffTime || undefined
    // 옵셔널 필드를 지우는 경우(빈칸으로 비움)는 merge 로 표현이 안 되므로(머지는 없는 키를
    // 그냥 건드리지 않을 뿐 지우지 않는다) deleteField() 로 명시적으로 지운다.
    // score는 여기서 다루지 않는다(경기 결과 입력은 이 화면 책임이 아니다).
    await setDoc(doc(db, 'matches', m.id), {
      date: editForm.value.date,
      stadiumId: editForm.value.stadiumId,
      homeTeamId: editForm.value.homeTeamId,
      awayTeamId: editForm.value.awayTeamId,
      updatedAt: Timestamp.now(),
      kickoffTime: kickoff ?? deleteField(),
      round: roundNum ?? deleteField(),
    }, { merge: true })
    Object.assign(m, {
      date: editForm.value.date, stadiumId: editForm.value.stadiumId,
      homeTeamId: editForm.value.homeTeamId, awayTeamId: editForm.value.awayTeamId,
      kickoffTime: kickoff, round: roundNum,
    })
    editingMatchId.value = null
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    savingEdit.value = false
  }
}

// 시즌 목록은 하드코딩하지 않는다 — gm_id 앞 8자리(seasonId)가 실제로 들어와 있는 값 기준.
// 리그를 바꾸면 그 리그에 있는 시즌만 남도록 다시 계산한다.
const seasonsInLeague = computed(() => {
  const set = new Set(matches.value.filter(m => m.leagueId === selectedLeague.value).map(m => m.seasonId))
  return [...set].sort().reverse()
})
function seasonLabel(seasonId: string) {
  return `${seasonId.slice(0, 4)}-${seasonId.slice(6, 8)}`
}
const selectedSeason = ref('')
watch(seasonsInLeague, list => {
  if (!list.includes(selectedSeason.value)) selectedSeason.value = list[0] ?? ''
}, { immediate: true })

const filteredMatches = computed(() => matches.value
  .filter(m => m.leagueId === selectedLeague.value && m.seasonId === selectedSeason.value)
  // 최신 라운드가 맨 위로 — 경기 추가할 때 항상 "다음 라운드" 번호를 넣으니, 라운드
  // 내림차순이 곧 최신순이다(날짜는 일정이 밀리거나 당겨질 수 있어 기준으로 못 쓴다).
  // 라운드가 없는 토너먼트 경기는 날짜로만 정렬한다.
  .sort((a, b) => {
    const ra = a.round ?? -1
    const rb = b.round ?? -1
    if (ra !== rb) return rb - ra
    return (b.date + (b.kickoffTime ?? '')).localeCompare(a.date + (a.kickoffTime ?? ''))
  }))
const leagueTeams = computed(() => teams.value.filter(t => t.currentLeagueId === selectedLeague.value))

// 킥오프는 30분 단위(00/30)로만 잡는다 — 자유 입력 대신 목록에서 고르게 해서 오전/오후
// 표기가 있는 브라우저 기본 time input의 불편함도 같이 없앤다.
const TIME_SLOTS = Array.from({ length: 48 }, (_, i) => {
  const h = String(Math.floor(i / 2)).padStart(2, '0')
  const m = i % 2 === 0 ? '00' : '30'
  return `${h}:${m}`
})

// 대부분 홈팀 구장에서 열리니 홈팀을 고르면 경기장을 자동으로 맞춰준다 — 중립 경기장 등
// 다른 경우는 그 다음에 경기장 드롭다운에서 직접 바꾸면 된다. watch가 아니라 select의
// change 이벤트로 건다 — watch를 쓰면 기존 경기를 "수정"으로 열 때(editForm을 그 경기의
// 실제 저장값으로 채울 때)도 homeTeamId 변경으로 감지돼서, 그 경기가 원래 다른(예: 중립)
// 경기장이었어도 홈팀 기본 구장으로 덮어써버린다.
function onEditHomeTeamChange() {
  const stadiumId = teamStadiumId(editForm.value.homeTeamId)
  if (stadiumId) editForm.value.stadiumId = stadiumId
}

// "리그 고정 상수"(시즌 8자리와 일련번호 4자리 사이, 리그코드 포함)는 그 리그의 어느 경기를
// 봐도 항상 동일하다(§03 문서: 3시즌 780건 전부 동일) — 그 리그에 이미 등록된 경기가
// 하나라도 있으면 거기서 그대로 뽑아 쓴다. 하나도 없으면(그 리그의 첫 경기) 뽑을 데가
// 없으므로 그때만 담당자가 직접 입력한다.
const leagueConstant = computed(() => {
  const existing = matches.value.find(m => m.leagueId === selectedLeague.value)
  return existing ? existing.id.slice(8, -4) : ''
})

// gm_id의 시즌은 날짜에서 역산하지 않고, 지금 목록에서 보고 있는 시즌(selectedSeason)을
// 그대로 쓴다 — 이미 그 시즌 화면에 들어와 있으니 다시 물을 필요가 없다. 순연경기처럼
// 날짜와 시즌이 어긋나는 경우는 애초에 이 값과 무관하다.
const autoPrefix = computed(() => leagueConstant.value ? `${selectedSeason.value}${leagueConstant.value}` : '')

// 같은 리그·같은 시즌 안에서 마지막 일련번호 다음 값을 자동으로 채운다 — 벌크 추가를 열 때
// 10칸의 시작 번호로만 쓴다(칸마다 그 뒤로 1씩 늘려서 기본값을 준다).
const nextSerial = computed(() => {
  if (!selectedSeason.value) return ''
  const sameSeason = matches.value.filter(m => m.leagueId === selectedLeague.value && m.seasonId === selectedSeason.value)
  const maxSerial = sameSeason.reduce((max, m) => {
    const s = Number(m.id.slice(-4))
    return Number.isFinite(s) ? Math.max(max, s) : max
  }, 0)
  return maxSerial + 1
})

// gm_id 형식: 시즌(8, 숫자) + 리그 고정 상수(가변, 리그코드 포함) + 일련번호(4, 숫자).
// 자동 조립된 값은 이미 이 형식을 만족하므로, 이 검증은 사실상 수동 입력(첫 경기) 때만 걸린다.
function validateGmIdFormat(raw: string): string {
  const id = raw.trim()
  if (!id) return 'gm_id가 없습니다.'
  if (!/^[A-Za-z0-9]+$/.test(id)) return 'gm_id는 영문자와 숫자만 사용할 수 있습니다.'
  if (id.length < 15) return 'gm_id가 너무 짧습니다 (시즌 8자리 + 리그코드 3자리 + 일련번호 4자리, 최소 15자 이상).'
  const seasonPart = id.slice(0, 8)
  if (!/^\d{8}$/.test(seasonPart)) return `gm_id 앞 8자리는 시즌(숫자)이어야 합니다. 지금: "${seasonPart}"`
  const y1 = Number(seasonPart.slice(0, 4))
  const y2 = Number(seasonPart.slice(4, 8))
  if (y2 !== y1 + 1) return `시즌 앞 4자리(${y1})와 뒤 4자리(${y2})는 연속된 연도여야 합니다 (예: 20262027).`
  const serialPart = id.slice(-4)
  if (!/^\d{4}$/.test(serialPart)) return `gm_id 마지막 4자리는 일련번호(숫자)여야 합니다. 지금: "${serialPart}"`
  return ''
}

// ---- 경기 추가 — "+ 경기 추가"를 누르면 기본 10칸이 뜬다. 한 라운드에 보통 여러 경기가
// 한꺼번에 잡히는(스크린샷의 SQL 이관용 엑셀과 같은) 흐름이라 하나씩 여닫는 게 더 느리다.
// 다 안 쓸 칸은 X로 지우고, 남은 칸만 한 번에 저장한다.
const addOpen = ref(false)
const saving = ref(false)
const formError = ref('')

interface BulkRow {
  key: number
  date: string
  kickoffTime: string
  round: string
  stadiumId: string
  homeTeamId: string
  awayTeamId: string
  manualGmId: string // 이 리그의 첫 경기라 자동 조립 재료(리그 고정 상수)가 없을 때만 쓴다
  serial: string
  editableGmId: string // bulkGmIdOverride가 켜졌을 때만 쓰는 전체 수동 입력값
  error: string
}
let bulkKeySeq = 0
function makeBulkRow(serial: number): BulkRow {
  return {
    key: bulkKeySeq++, date: '', kickoffTime: '', round: '', stadiumId: '', homeTeamId: '', awayTeamId: '',
    manualGmId: '', serial: String(serial).padStart(4, '0'), editableGmId: '', error: '',
  }
}
const bulkRows = ref<BulkRow[]>([])

// 새 시즌 등으로 gm_id를 처음부터 끝까지 직접 쳐야 할 때 켜는 "수정" — 칸마다 따로 켜는 게
// 아니라 GM_ID 헤더 옆 버튼 하나로 전체 칸을 한 번에 수동 입력 모드로 바꾼다(10칸을 하나씩
// 누르지 않아도 되게).
const bulkGmIdOverride = ref(false)
function toggleBulkGmIdOverride() {
  if (bulkGmIdOverride.value) {
    bulkGmIdOverride.value = false
  } else {
    for (const row of bulkRows.value) row.editableGmId = gmIdFor(row)
    bulkGmIdOverride.value = true
  }
}

function openAdd() {
  addOpen.value = true
  formError.value = ''
  bulkGmIdOverride.value = false
  const base = Number(nextSerial.value || 1)
  bulkRows.value = Array.from({ length: 10 }, (_, i) => makeBulkRow(base + i))
}
function cancelAdd() { addOpen.value = false; bulkRows.value = [] }
function removeBulkRow(key: number) { bulkRows.value = bulkRows.value.filter(r => r.key !== key) }
function addBulkRow() {
  const last = bulkRows.value.at(-1)
  const base = last ? Number(last.serial) + 1 : Number(nextSerial.value || 1)
  const row = makeBulkRow(base)
  if (bulkGmIdOverride.value) row.editableGmId = gmIdFor(row)
  bulkRows.value.push(row)
}

// ---- 엑셀로 추가 — "경기일정_입력양식.xlsx" 의 "경기입력" 시트(A~H열)를 그대로 읽어
// 지금 카드 화면에 채워 넣는다. 저장 전 검증·중복확인은 기존 submitAdd 를 그대로 재사용한다
// (엑셀은 "타이핑을 대신 해주는 것"일 뿐, 검증/저장 로직은 손대지 않는다).
const excelInputEl = ref<HTMLInputElement | null>(null)
function openExcelPicker() { excelInputEl.value?.click() }

function formatExcelDate(v: unknown): string {
  if (v instanceof Date) {
    const y = v.getFullYear(), m = String(v.getMonth() + 1).padStart(2, '0'), d = String(v.getDate()).padStart(2, '0')
    return `${y}-${m}-${d}`
  }
  const s = String(v ?? '').trim()
  const m = /^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})/.exec(s)
  return m ? `${m[1]}-${m[2]!.padStart(2, '0')}-${m[3]!.padStart(2, '0')}` : s
}
function formatExcelTime(v: unknown): string {
  if (v instanceof Date) {
    return `${String(v.getHours()).padStart(2, '0')}:${String(v.getMinutes()).padStart(2, '0')}`
  }
  const s = String(v ?? '').trim()
  const m = /^(\d{1,2}):(\d{2})/.exec(s)
  return m ? `${m[1]!.padStart(2, '0')}:${m[2]}` : ''
}
// 템플릿 2행에 남아있는 형식 예시 그대로면(지우는 걸 깜빡했을 때) 자동으로 건너뛴다.
function isTemplateExampleRow(r: unknown[]): boolean {
  return String(r[0] ?? '').trim() === '2' && String(r[4] ?? '').trim() === 'E-AR' && String(r[5] ?? '').trim() === 'E-CO'
}

function importExcelRows(rows: unknown[][]) {
  // H열(gm_id 전체)을 하나라도 쓴 행이 있으면 이 배치 전체를 "수정"(전체 수동입력) 모드로
  // 켠다 — gm_id 오버라이드는 칸별이 아니라 배치 전체 토글이라서, 섞어 쓸 수 없다.
  const hasFullOverride = rows.some(r => String(r[7] ?? '').trim())
  bulkGmIdOverride.value = hasFullOverride

  bulkRows.value = rows.map(r => {
    const row = makeBulkRow(0)
    const serialRaw = String(r[0] ?? '').trim()
    row.serial = serialRaw ? serialRaw.padStart(4, '0').slice(-4) : ''
    row.date = formatExcelDate(r[1])
    row.kickoffTime = formatExcelTime(r[2])
    const roundRaw = String(r[3] ?? '').trim()
    row.round = roundRaw
    row.homeTeamId = String(r[4] ?? '').trim()
    row.awayTeamId = String(r[5] ?? '').trim()
    const stadiumRaw = String(r[6] ?? '').trim()
    row.stadiumId = stadiumRaw || teamStadiumId(row.homeTeamId) || ''
    const fullGmId = String(r[7] ?? '').trim()
    if (hasFullOverride) row.editableGmId = fullGmId || (leagueConstant.value ? `${autoPrefix.value}${row.serial}` : '')
    else row.manualGmId = fullGmId || row.serial
    return row
  })
}

async function handleExcelFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  formError.value = ''
  try {
    const buf = await file.arrayBuffer()
    const workbook = XLSX.read(buf, { type: 'array', cellDates: true })
    const sheetName = workbook.SheetNames.includes('경기입력') ? '경기입력' : workbook.SheetNames[0]!
    const sheet = workbook.Sheets[sheetName]!
    const rows = XLSX.utils.sheet_to_json<unknown[]>(sheet, { header: 1, defval: '' })
    let dataRows = rows.slice(1) // 1행(헤더) 제외
    if (dataRows.length && isTemplateExampleRow(dataRows[0]!)) dataRows = dataRows.slice(1)
    dataRows = dataRows.filter(r => r.some(cell => String(cell ?? '').trim() !== ''))
    if (!dataRows.length) { formError.value = '엑셀에서 읽은 데이터가 없습니다 — 3행부터 채워서 올려주세요.'; return }
    addOpen.value = true
    importExcelRows(dataRows)
  } catch (err) {
    formError.value = err instanceof Error ? `엑셀을 읽는 중 오류가 발생했습니다: ${err.message}` : String(err)
  } finally {
    input.value = '' // 같은 파일을 다시 선택해도 change 이벤트가 나도록
  }
}
function onBulkHomeTeamChange(row: BulkRow) {
  const stadiumId = teamStadiumId(row.homeTeamId)
  if (stadiumId) row.stadiumId = stadiumId
}
// 한 라운드 여러 경기가 같은 날짜/라운드인 경우가 많은데, 자동으로 아래 전부를 한꺼번에
// 채우면(값이 없는 칸 전부) 원치 않는 칸까지 같이 채워진다 — 그래서 자동 전파 대신, 버튼을
// 누른 "그 다음 한 칸"에만 값을 복사한다. 여러 칸에 이어 쓰고 싶으면 그만큼 눌러서 쓴다.
function fillNextRow(key: number, field: 'date' | 'kickoffTime' | 'round') {
  const idx = bulkRows.value.findIndex(r => r.key === key)
  if (idx < 0 || idx + 1 >= bulkRows.value.length) return
  const value = bulkRows.value[idx]![field]
  if (!value) return
  bulkRows.value[idx + 1]![field] = value
}
function gmIdFor(row: BulkRow): string {
  if (bulkGmIdOverride.value) return row.editableGmId.trim()
  if (leagueConstant.value) return `${autoPrefix.value}${row.serial.trim().padStart(4, '0').slice(-4)}`
  return row.manualGmId.trim()
}

async function submitAdd() {
  formError.value = ''
  const rows = bulkRows.value
  if (!rows.length) { formError.value = '추가할 경기가 없습니다 — 전부 닫혀 있어요.'; return }

  const gmIds: string[] = []
  let anyError = false
  for (const row of rows) {
    row.error = ''
    const gmId = gmIdFor(row)
    const fmtErr = validateGmIdFormat(gmId)
    if (fmtErr) row.error = fmtErr
    else if (!row.date) row.error = '날짜를 입력하세요.'
    else if (!row.stadiumId) row.error = '경기장을 선택하세요.'
    else if (!row.homeTeamId || !row.awayTeamId) row.error = '홈/원정 팀을 선택하세요.'
    else if (!leagueTeams.value.some(t => t.id === row.homeTeamId)) row.error = `홈팀 코드를 찾을 수 없습니다(이 리그 소속 아님): "${row.homeTeamId}"`
    else if (!leagueTeams.value.some(t => t.id === row.awayTeamId)) row.error = `원정팀 코드를 찾을 수 없습니다(이 리그 소속 아님): "${row.awayTeamId}"`
    else if (row.homeTeamId === row.awayTeamId) row.error = '홈팀과 원정팀이 같을 수 없습니다.'
    else if (!stadiums.value.some(s => s.id === row.stadiumId)) row.error = `경기장 코드를 찾을 수 없습니다: "${row.stadiumId}"`
    else if (gmIds.includes(gmId)) row.error = `이 배치 안에서 gm_id가 중복됩니다: "${gmId}"`
    if (row.error) anyError = true
    gmIds.push(gmId)
  }
  if (anyError) { formError.value = '빨간 표시된 칸을 확인하세요.'; return }

  saving.value = true
  try {
    const existingChecks = await Promise.all(gmIds.map(id => getDoc(doc(db, 'matches', id))))
    existingChecks.forEach((snap, i) => {
      if (snap.exists()) { rows[i]!.error = `이미 등록된 gm_id입니다: "${gmIds[i]}"`; anyError = true }
    })
    if (anyError) { formError.value = '이미 등록된 gm_id가 있습니다. 빨간 표시된 칸을 확인하세요.'; return }

    const batch = writeBatch(db)
    const newRows: MatchRow[] = []
    rows.forEach((row, i) => {
      const gmId = gmIds[i]!
      const data: MatchDoc = {
        date: row.date,
        leagueId: selectedLeague.value,
        seasonId: /^\d{8}/.test(gmId) ? gmId.slice(0, 8) : selectedSeason.value,
        matchType: 'league',
        stadiumId: row.stadiumId,
        homeTeamId: row.homeTeamId,
        awayTeamId: row.awayTeamId,
        score: { home: 0, away: 0 },
        createdAt: Timestamp.now(),
        updatedAt: Timestamp.now(),
      }
      if (row.kickoffTime) data.kickoffTime = row.kickoffTime
      if (row.round) data.round = Number(row.round)
      batch.set(doc(db, 'matches', gmId), data)
      newRows.push({ id: gmId, ...data })
    })
    await batch.commit()
    matches.value = [...matches.value, ...newRows]
    // 방금 추가한 경기들이 바로 보이도록 — 지금 보고 있던 리그/시즌 필터가 다르면 맞춰준다.
    if (newRows[0]) {
      selectedLeague.value = newRows[0].leagueId
      selectedSeason.value = newRows[0].seasonId
    }
    addOpen.value = false
    bulkRows.value = []
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="frame">
      <header>
        <NuxtLink to="/manage" class="back">← 데이터 관리</NuxtLink>
        <h1>경기 일정 관리</h1>
        <div class="headerActions">
          <button class="add excelAdd" @click="openExcelPicker">엑셀로 추가</button>
          <input ref="excelInputEl" type="file" accept=".xlsx,.xls,.csv" class="hiddenFileInput" @change="handleExcelFile">
          <button class="add" @click="openAdd">+ 경기 추가</button>
        </div>
      </header>

      <p v-if="loadError" class="errBanner">{{ loadError }}</p>

      <div v-if="addOpen" class="bulkAddPanel">
        <div class="bulkHeader">
          <span class="leagueFixed">리그: {{ leagueLabel(selectedLeague) }} · 시즌: {{ seasonLabel(selectedSeason) }} (현재 목록 기준) · {{ bulkRows.length }}칸</span>
          <span v-if="!leagueConstant" class="warnBanner">이 리그의 첫 경기라 gm_id를 자동 조립할 수 없습니다 — 칸마다 gm_id를 직접 입력하세요.</span>
        </div>

        <div class="bulkGridHead">
          <span class="gmIdHeadLabel">GM_ID
            <button v-if="leagueConstant" type="button" class="gmIdEditBtn" @click="toggleBulkGmIdOverride">{{ bulkGmIdOverride ? '자동' : '수정' }}</button>
          </span>
          <span>날짜</span><span>킥오프</span><span>라운드</span><span>홈팀</span><span>원정팀</span><span>경기장</span>
        </div>
        <div class="bulkGrid">
          <div v-for="row in bulkRows" :key="row.key" class="bulkCard">
            <button type="button" class="bulkRemoveBtn" title="이 칸 삭제" @click="removeBulkRow(row.key)">✕</button>

            <div class="bulkField bulkFieldLg gmIdRow" :class="{ gmIdExpanded: bulkGmIdOverride }">
              <input v-if="bulkGmIdOverride" v-model="row.editableGmId" class="mono gmIdFull" placeholder="예: 20272028eplj2rgn13na28rd2b8q0001">
              <input v-else-if="leagueConstant" v-model="row.serial" class="mono serialInput" maxlength="4" placeholder="0001" :title="gmIdFor(row)">
              <input v-else v-model="row.manualGmId" class="mono" placeholder="예: 20262027eplj2rgn13na28rd2b8q0001">
            </div>

            <div class="bulkField fillableField">
              <input v-model="row.date" type="date">
              <button type="button" class="fillDownBtn" title="다음 칸에 같은 날짜 복사" @click="fillNextRow(row.key, 'date')">↓</button>
            </div>
            <div class="bulkField fillableField">
              <select v-model="row.kickoffTime">
                <option value="">-</option>
                <option v-for="t in TIME_SLOTS" :key="t" :value="t">{{ t }}</option>
              </select>
              <button type="button" class="fillDownBtn" title="다음 칸에 같은 킥오프 복사" @click="fillNextRow(row.key, 'kickoffTime')">↓</button>
            </div>
            <div class="bulkField fillableField">
              <input v-model="row.round" type="number" min="1">
              <button type="button" class="fillDownBtn" title="다음 칸에 같은 라운드 복사" @click="fillNextRow(row.key, 'round')">↓</button>
            </div>
            <div class="bulkField bulkFieldLg teamField">
              <TeamCombo v-model="row.homeTeamId" :options="leagueTeams.map(t => ({ id: t.id, label: t.nameKr || t.name }))" @select="onBulkHomeTeamChange(row)" />
            </div>
            <div class="bulkField bulkFieldLg teamField">
              <TeamCombo v-model="row.awayTeamId" :options="leagueTeams.map(t => ({ id: t.id, label: t.nameKr || t.name }))" />
            </div>
            <div class="bulkField bulkFieldLg">
              <select v-model="row.stadiumId">
                <option value="" disabled>선택</option>
                <option v-for="s in stadiums" :key="s.id" :value="s.id">{{ s.label }}</option>
              </select>
            </div>

            <p v-if="row.error" class="rowErrorMsg">{{ row.error }}</p>
          </div>
        </div>

        <button type="button" class="addBulkRowBtn" @click="addBulkRow">+ 칸 추가</button>

        <p v-if="formError" class="formErrorMsg">{{ formError }}</p>
        <div class="addActionsRow">
          <button :disabled="saving" class="applyBtn" @click="submitAdd">{{ bulkRows.length }}개 저장</button>
          <button @click="cancelAdd">취소</button>
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
              <option v-for="s in seasonsInLeague" :key="s" :value="s">{{ seasonLabel(s) }}</option>
            </select>
          </label>
        </div>
        <span>{{ loading ? '불러오는 중...' : `${filteredMatches.length}개 경기` }}</span>
      </div>

      <div class="matchTable">
        <div class="matchHead">
          <span>gm_id</span><span>날짜</span><span>라운드</span><span>대진</span><span>경기장</span><span>스코어</span><span></span>
        </div>
        <div v-if="!loading && !filteredMatches.length" class="matchEmpty">등록된 경기가 없습니다.</div>
        <div v-for="m in filteredMatches" :key="m.id" class="matchRow" :class="{ editingRow: editingMatchId === m.id }">
          <template v-if="editingMatchId === m.id">
            <span class="mono gmIdCell" :title="m.id">{{ m.id }}</span>
            <span class="editCell">
              <input v-model="editForm.date" type="date">
              <select v-model="editForm.kickoffTime">
                <option value="">-</option>
                <option v-for="t in TIME_SLOTS" :key="t" :value="t">{{ t }}</option>
              </select>
            </span>
            <span class="editCell"><input v-model="editForm.round" type="number" min="1" placeholder="라운드"></span>
            <span class="editCell">
              <TeamCombo v-model="editForm.homeTeamId" :options="leagueTeams.map(t => ({ id: t.id, label: t.nameKr || t.name }))" @select="onEditHomeTeamChange" />
              <TeamCombo v-model="editForm.awayTeamId" :options="leagueTeams.map(t => ({ id: t.id, label: t.nameKr || t.name }))" />
            </span>
            <span class="editCell">
              <select v-model="editForm.stadiumId">
                <option v-for="s in stadiums" :key="s.id" :value="s.id">{{ s.label }}</option>
              </select>
            </span>
            <span>{{ m.score.home }} - {{ m.score.away }}</span>
            <span class="matchRowActions">
              <button class="applyRowBtn" :disabled="savingEdit" @click="applyEditMatch(m)">적용</button>
              <button class="cancelRowBtn" @click="cancelEditMatch">취소</button>
            </span>
          </template>
          <template v-else>
            <span class="mono gmIdCell" :title="m.id">{{ m.id }}</span>
            <span>{{ m.date }}<i v-if="m.kickoffTime"> {{ m.kickoffTime }}</i></span>
            <span>{{ roundLabel(m) }}</span>
            <span>{{ teamLabel(m.homeTeamId) }} vs {{ teamLabel(m.awayTeamId) }}</span>
            <span>{{ stadiumLabel(m.stadiumId) }}</span>
            <span>{{ m.score.home }} - {{ m.score.away }}</span>
            <span class="matchRowActions">
              <button class="editRowBtn" @click="openEditMatch(m)">수정</button>
              <button class="deleteRowBtn" :disabled="deletingId === m.id" @click="deleteMatch(m)">삭제</button>
            </span>
          </template>
        </div>
      </div>
      <p v-if="editingMatchId && formError" class="formErrorMsg">{{ formError }}</p>
    </div>
  </div>
</template>

<style scoped>
.page{width:1280px;height:800px;background:#0b0f17;color:#eee;padding:28px;box-sizing:border-box;overflow-y:auto}.frame{min-height:100%;border:1px solid #29313b;background:#151a21;padding:24px;box-sizing:border-box}header{display:flex;align-items:center;gap:20px;border-bottom:1px solid #29313b;padding-bottom:18px}h1{flex:1;text-align:center;font-size:26px;margin:0}.back{color:#9da7b3;text-decoration:none}.add{background:#f0b429;border:0;padding:10px 16px;font-weight:800;border-radius:4px;cursor:pointer}
.headerActions{display:flex;gap:8px}
.excelAdd{background:transparent!important;border:1px solid #f0b429!important;color:#f0b429!important}
.hiddenFileInput{display:none}
.errBanner{margin:12px 0 0;padding:8px 12px;background:rgba(241,106,106,.12);border:1px solid rgba(241,106,106,.4);border-radius:4px;color:#f16a6a;font-size:12px}
.bulkAddPanel{margin-top:12px;padding:14px;border:1px solid rgba(240,180,41,.4);border-radius:6px;background:rgba(240,180,41,.05)}
.bulkHeader{display:flex;flex-direction:column;gap:6px;margin-bottom:12px}
.leagueFixed{color:rgba(255,255,255,.55);font-size:12px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
.warnBanner{margin:0;padding:8px 12px;background:rgba(240,180,41,.12);border:1px solid rgba(240,180,41,.5);border-radius:4px;color:#f0b429;font-size:12px}
.formErrorMsg{color:#f16a6a;font-size:12px;margin:8px 0 0}
.addActionsRow{display:flex;gap:8px;margin-top:12px}
.addActionsRow button{padding:8px 16px;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:transparent;color:#ddd;cursor:pointer;font-size:12px}
.addActionsRow .applyBtn:disabled{opacity:.4;cursor:not-allowed}
.applyBtn{background:#f0b429!important;color:#191919!important;border-color:#f0b429!important;font-weight:700}
.bulkGridHead{display:grid;grid-template-columns:112px 1fr .85fr .55fr 1.3fr 1.3fr 1.45fr;gap:10px;padding:0 40px 0 10px;margin-bottom:6px;color:rgba(255,255,255,.4);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.02em}
.gmIdHeadLabel{display:flex;align-items:center;gap:6px;white-space:nowrap}
.gmIdHeadLabel .gmIdEditBtn{text-transform:none;font-weight:700}
.bulkGrid{display:flex;flex-direction:column;gap:10px}
.bulkCard{position:relative;padding:10px 40px 10px 10px;border:1px solid rgba(255,255,255,.12);border-radius:6px;background:rgba(255,255,255,.03);display:grid;grid-template-columns:112px 1fr .85fr .55fr 1.3fr 1.3fr 1.45fr;gap:10px;align-items:start}
.bulkRemoveBtn{position:absolute;top:6px;right:6px;width:20px;height:20px;line-height:18px;padding:0;border-radius:50%;border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.06);color:#ddd;font-size:11px;cursor:pointer}
.bulkRemoveBtn:hover{background:rgba(241,106,106,.25);border-color:#f16a6a;color:#fff}
.bulkField{display:flex;flex-direction:column;gap:3px;color:rgba(255,255,255,.5);font-size:10px}
.bulkField input,.bulkField select{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:4px;color:#fff;padding:6px;font-size:11px;width:100%;box-sizing:border-box;margin:0;display:block}
.bulkFieldLg input,.bulkFieldLg select{padding:12px 8px;font-size:14px}
.teamField :deep(.teamCombo input){padding:12px 8px;font-size:14px}
.bulkField select{appearance:none;-webkit-appearance:none;-moz-appearance:none;background-image:linear-gradient(45deg,transparent 50%,rgba(255,255,255,.5) 50%),linear-gradient(135deg,rgba(255,255,255,.5) 50%,transparent 50%);background-position:calc(100% - 14px) 55%,calc(100% - 9px) 55%;background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:22px}
.fillableField{gap:2px}
.fillDownBtn{width:100%;height:13px;padding:0;margin:0;line-height:11px;border-radius:3px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.05);color:rgba(255,255,255,.6);font-size:8px;cursor:pointer;box-sizing:border-box;display:block}
.fillDownBtn:hover{background:rgba(240,180,41,.2);border-color:#f0b429;color:#f0b429}
.gmIdRow{display:flex;flex-direction:row;gap:6px;align-items:center;transition:grid-column .15s ease}
.gmIdRow input{flex:1;min-width:0}
.serialInput{color:#f0b429!important;font-weight:800;text-align:center;border-color:rgba(240,180,41,.5)!important}
.gmIdRow.gmIdExpanded{grid-column:1/-1}
.gmIdFull{padding:9px 10px!important;font-size:12px!important;letter-spacing:.02em}
.gmIdEditBtn{flex:0 0 auto;padding:0 8px;border-radius:4px;border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.06);color:#ddd;font-size:10px;cursor:pointer}
.rowErrorMsg{grid-column:1/-1;color:#f16a6a;font-size:10px;margin:0}
.addBulkRowBtn{margin-top:12px;padding:8px 14px;border-radius:4px;border:1px dashed rgba(255,255,255,.3);background:transparent;color:#ddd;font-size:12px;cursor:pointer}
.addBulkRowBtn:hover{background:rgba(255,255,255,.06)}
.toolbar{display:flex;justify-content:space-between;align-items:center;margin:20px 0;color:#aab3be}.filters{display:flex;gap:18px;align-items:center}select{margin-left:10px;background:#202731;color:#fff;border:1px solid #4a5563;padding:8px 28px 8px 10px;border-radius:4px}
.matchTable{border:1px solid #303a48;border-radius:5px;overflow:hidden}
.matchHead,.matchRow{display:grid;grid-template-columns:280px .8fr .6fr 1.2fr 1fr .55fr 115px;align-items:center;gap:12px;padding:10px 14px}
.matchHead{background:#202731;color:#8f9baa;font-size:12px;font-weight:800}
.matchRow{border-top:1px solid #2a3340;background:#171d25;color:#eee;font-size:13px}
.matchRow:hover{background:#1c232c}
.matchRow.editingRow{background:#1c232c;box-shadow:inset 3px 0 0 #f0b429}
.gmIdCell{white-space:nowrap}
.matchEmpty{padding:20px;text-align:center;color:#77818d;font-size:13px}
.matchRowActions{justify-self:end;display:flex;gap:6px}
.editCell{display:flex;flex-direction:column;gap:4px}
.editCell select,.editCell input{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);border-radius:4px;color:#fff;padding:5px;font-size:11px;width:100%;box-sizing:border-box}
.editRowBtn{padding:5px 10px;border-radius:3px;border:1px solid #596474;background:transparent;color:#ddd;font-size:11px;cursor:pointer}
.editRowBtn:hover{background:rgba(255,255,255,.08)}
.deleteRowBtn{padding:5px 10px;border-radius:3px;border:1px solid #7a3a3a;background:transparent;color:#f16a6a;font-size:11px;cursor:pointer}
.deleteRowBtn:hover{background:rgba(241,106,106,.12)}
.deleteRowBtn:disabled{opacity:.4;cursor:not-allowed}
.applyRowBtn{padding:5px 10px;border-radius:3px;border:1px solid #f0b429;background:rgba(240,180,41,.15);color:#f0b429;font-size:11px;font-weight:700;cursor:pointer}
.applyRowBtn:disabled{opacity:.4;cursor:not-allowed}
.cancelRowBtn{padding:5px 10px;border-radius:3px;border:1px solid #596474;background:transparent;color:#ddd;font-size:11px;cursor:pointer}
</style>
