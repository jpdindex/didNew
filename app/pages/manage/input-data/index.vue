<script setup lang="ts">
// "입력 데이터"(경기마다 실시간으로 쌓이는 것 — matches/recordings/records/playerStats/
// cards/paths) 전용 조회+수정 화면. 팀/선수/리그 같은 "저장 관리 데이터"는 여기서 안 다룬다
// (그건 /manage/data-viewer의 최상위 버튼들로 본다). 경로를 직접 타이핑하는 대신 버튼으로
// 경기 → 팀(H/A) → 세부 항목 순서로 눌러 내려가는 방식이라 Firestore 경로 문법을 몰라도 된다.

import { collection, doc, getDoc, getDocs, query, updateDoc, where, type Firestore, Timestamp } from 'firebase/firestore'

const { $db } = useNuxtApp()
const db = $db as Firestore

// 지금 어디까지 내려왔는지 — 이 배열 하나가 곧 Firestore 경로다.
const crumbs = ref<string[]>([])
const path = computed(() => crumbs.value.join('/'))
const depth = computed(() => crumbs.value.length)

const rows = ref<Record<string, unknown>[]>([])
const columns = ref<string[]>([])
const busy = ref(false)
const errorMsg = ref('')
const isSingleDoc = ref(false)

const roundFilter = ref('')
const leagueFilter = ref('')
const seasonFilter = ref('')

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (v instanceof Timestamp) return v.toDate().toLocaleString('ko-KR')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
function toEditString(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (v instanceof Timestamp) return v.toDate().toISOString()
  if (typeof v === 'object') return JSON.stringify(v, null, 2)
  return String(v)
}
function fromEditString(str: string, original: unknown): unknown {
  if (original instanceof Timestamp) {
    const d = new Date(str)
    if (isNaN(d.getTime())) throw new Error(`날짜 형식이 아니다: ${str}`)
    return Timestamp.fromDate(d)
  }
  if (typeof original === 'number') {
    if (str.trim() === '') return null
    const num = Number(str)
    if (isNaN(num)) throw new Error(`숫자가 아니다: ${str}`)
    return num
  }
  if (typeof original === 'boolean') return str === 'true'
  if (original !== null && typeof original === 'object') return str.trim() === '' ? null : JSON.parse(str)
  return str === '' ? null : str
}

async function load() {
  if (crumbs.value.length === 0) { rows.value = []; columns.value = []; return }
  busy.value = true
  errorMsg.value = ''
  try {
    if (depth.value % 2 === 0) {
      isSingleDoc.value = true
      const snap = await getDoc(doc(db, path.value))
      if (!snap.exists()) { rows.value = []; columns.value = []; errorMsg.value = '문서가 없다'; return }
      const data = snap.data()
      columns.value = ['id', ...Object.keys(data)]
      rows.value = [{ id: snap.id, ...data }]
    } else {
      isSingleDoc.value = false
      let q = query(collection(db, path.value))
      if (depth.value === 1) {
        if (roundFilter.value.trim()) q = query(q, where('round', '==', Number(roundFilter.value)))
        if (leagueFilter.value.trim()) q = query(q, where('leagueId', '==', leagueFilter.value.trim()))
        if (seasonFilter.value.trim()) q = query(q, where('seasonId', '==', seasonFilter.value.trim()))
      }
      const snap = await getDocs(q)
      const docs: Record<string, unknown>[] = []
      const colSet = new Set<string>(['id'])
      snap.forEach(d => {
        const data = d.data()
        Object.keys(data).forEach(k => colSet.add(k))
        docs.push({ id: d.id, ...data })
      })
      columns.value = [...colSet]
      rows.value = docs
    }
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e)
    rows.value = []
    columns.value = []
  } finally {
    busy.value = false
  }
}

function start() {
  crumbs.value = ['matches']
  load()
}

// 표에서 문서 ID를 누르면 그 문서로 한 단계 내려간다.
function enterDoc(id: string) {
  crumbs.value = [...crumbs.value, id]
  load()
}

// "레코드 보기"류 버튼 — 정해진 서브컬렉션 이름으로 한 단계 내려간다.
function enterSub(name: string) {
  crumbs.value = [...crumbs.value, name]
  load()
}

// 브레드크럼 중간을 누르면 그 지점까지 되돌아간다.
function jumpTo(i: number) {
  crumbs.value = crumbs.value.slice(0, i + 1)
  load()
}

const editingId = ref<string | null>(null)
const editBuffer = ref<Record<string, string>>({})
const saving = ref(false)
const saveError = ref('')

function startEdit(row: Record<string, unknown>) {
  editingId.value = row.id as string
  saveError.value = ''
  const buf: Record<string, string> = {}
  for (const col of columns.value) { if (col !== 'id') buf[col] = toEditString(row[col]) }
  editBuffer.value = buf
}
function cancelEdit() { editingId.value = null; saveError.value = '' }

async function saveEdit(row: Record<string, unknown>) {
  saving.value = true
  saveError.value = ''
  try {
    const updates: Record<string, unknown> = {}
    for (const col of columns.value) {
      if (col === 'id') continue
      const original = row[col]
      const edited = editBuffer.value[col] ?? ''
      if (edited === toEditString(original)) continue
      updates[col] = fromEditString(edited, original)
    }
    if (Object.keys(updates).length === 0) { editingId.value = null; return }
    const docRef = isSingleDoc.value ? doc(db, path.value) : doc(collection(db, path.value), row.id as string)
    await updateDoc(docRef, updates)
    Object.assign(row, updates)
    editingId.value = null
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

// 지금 depth에서 다음으로 뭘 보여줄지 — matches → 경기 문서 → recordings → H/A 문서 → 서브4개
const subButtons = ['records', 'playerStats', 'cards', 'paths']
const subLabel: Record<string, string> = { records: '레코드', playerStats: '선수 KPI', cards: '카드', paths: '공격경로' }

onMounted(start)
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <div class="topBar">
        <div class="title">입력 데이터 (경기 기록)</div>
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리로</NuxtLink>
      </div>

      <div class="crumbs">
        <button class="crumb" @click="start">경기 목록</button>
        <template v-for="(c, i) in crumbs.slice(1)" :key="i">
          <span class="sep">›</span>
          <button class="crumb" @click="jumpTo(i + 1)">{{ c }}</button>
        </template>
      </div>

      <div v-if="depth === 1" class="filterBar">
        <input v-model="seasonFilter" class="filterValue" placeholder="시즌 (예: 26270219)" @keyup.enter="load">
        <input v-model="leagueFilter" class="filterValue" placeholder="리그 ID (예: PRE)" @keyup.enter="load">
        <input v-model="roundFilter" class="filterValue" placeholder="라운드 (예: 17)" @keyup.enter="load">
        <button class="loadBtn" :disabled="busy" @click="load">{{ busy ? '조회 중...' : '조회' }}</button>
      </div>

      <div v-if="depth === 2" class="quickRow">
        <button class="quickBtn" @click="enterSub('recordings')">팀 기록 보기 (H/A)</button>
      </div>
      <div v-if="depth === 4" class="quickRow">
        <button v-for="name in subButtons" :key="name" class="quickBtn" @click="enterSub(name)">{{ subLabel[name] }}</button>
      </div>

      <p v-if="errorMsg" class="error">{{ errorMsg }}</p>
      <p v-if="saveError" class="error">저장 실패: {{ saveError }}</p>

      <div class="tableWrap">
        <table v-if="rows.length">
          <thead>
            <tr>
              <th v-for="col in columns" :key="col">{{ col }}</th>
              <th>작업</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id as string">
              <td v-for="col in columns" :key="col">
                <button v-if="col === 'id'" class="idBtn" @click="enterDoc(row.id as string)">{{ row.id }}</button>
                <textarea v-else-if="editingId === row.id" v-model="editBuffer[col]" class="editInput" rows="1" />
                <span v-else>{{ formatValue(row[col]) }}</span>
              </td>
              <td>
                <template v-if="editingId === row.id">
                  <button class="actBtn" :disabled="saving" @click="saveEdit(row)">{{ saving ? '저장 중...' : '저장' }}</button>
                  <button class="actBtn" :disabled="saving" @click="cancelEdit">취소</button>
                </template>
                <button v-else class="actBtn" @click="startEdit(row)">수정</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else-if="!busy && !errorMsg" class="empty">문서가 없다.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  box-sizing: border-box;
  width: 1280px;
  height: 800px;
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
  background: #0b0f17;
}
.bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(1200px 500px at 50% 35%, rgba(255,255,255,0.08), transparent 60%),
    linear-gradient(180deg, rgba(0,0,0,0.55), rgba(0,0,0,0.75));
}
.frame {
  box-sizing: border-box;
  position: relative;
  width: 1280px;
  height: 800px;
  background: rgba(10, 14, 22, 0.75);
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 18px 60px rgba(0,0,0,0.55);
  backdrop-filter: blur(8px);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.topBar { display: flex; align-items: center; justify-content: space-between; padding-bottom: 16px; border-bottom: 1px solid rgba(255,255,255,0.06); }
.title { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; }
.backBtn { color: rgba(255,255,255,0.55); font-size: 13px; text-decoration: none; }
.backBtn:hover { color: #fff; }

.crumbs { display: flex; align-items: center; gap: 4px; }
.crumb {
  background: none; border: none; cursor: pointer;
  color: rgba(0,217,255,0.85); font-size: 13px; padding: 2px 4px;
}
.crumb:hover { text-decoration: underline; }
.sep { color: rgba(255,255,255,0.3); font-size: 13px; }

.filterBar, .quickRow { display: flex; gap: 8px; flex-wrap: wrap; }
.filterValue {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 4px;
  color: #fff;
  font-size: 12px;
  padding: 6px 8px;
  width: 140px;
}
.loadBtn {
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(0,217,255,0.12);
  color: #fff;
  cursor: pointer;
}
.loadBtn:disabled { opacity: 0.5; cursor: not-allowed; }

.quickBtn {
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid rgba(0,217,255,0.3);
  background: rgba(0,217,255,0.08);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}
.quickBtn:hover { background: rgba(0,217,255,0.16); }

.error { color: #ff6b6b; font-size: 12px; }

.tableWrap { flex: 1; overflow: auto; border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; }
table { border-collapse: collapse; width: max-content; min-width: 100%; }
th, td {
  padding: 6px 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  font-size: 12px;
  color: rgba(255,255,255,0.8);
  text-align: left;
  white-space: nowrap;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
}
th { position: sticky; top: 0; background: #12161f; color: rgba(255,255,255,0.5); font-weight: 600; }
tbody tr:hover { background: rgba(255,255,255,0.03); }
.idBtn { background: none; border: none; color: rgba(0,217,255,0.85); cursor: pointer; font-size: 12px; text-decoration: underline; padding: 0; }
.empty { padding: 20px; color: rgba(255,255,255,0.4); font-size: 13px; }

.editInput {
  width: 160px; min-height: 24px;
  background: rgba(0,217,255,0.08);
  border: 1px solid rgba(0,217,255,0.4);
  border-radius: 4px;
  color: #fff;
  font-size: 12px;
  font-family: monospace;
  padding: 4px 6px;
  resize: vertical;
  white-space: pre;
}
.actBtn {
  padding: 3px 8px;
  margin-right: 4px;
  border-radius: 4px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: rgba(255,255,255,0.8);
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
}
.actBtn:hover { border-color: rgba(0,217,255,0.4); color: #fff; }
.actBtn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
