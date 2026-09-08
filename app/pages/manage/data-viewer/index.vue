<script setup lang="ts">
// Firestore 조회 + 수정 뷰어 — footballX의 admin(dMST.vue 등) 같은 "컬렉션 = 표" 화면.
// jpd-rating의 web/index.html(기준값 편집 페이지)과 같은 패턴 — 프론트에서 값 고치면
// 그대로 Firestore에 반영된다. 삭제 기능은 없다(요청받은 건 수정뿐).

import {
  collection, doc, getDoc, getDocs, query, updateDoc, where,
  type Firestore, type WhereFilterOp, Timestamp
} from 'firebase/firestore'

const { $db } = useNuxtApp()
const db = $db as Firestore

const editingId = ref<string | null>(null)
const editBuffer = ref<Record<string, string>>({})
const saving = ref(false)
const saveError = ref('')

// Firestore 값 -> 편집창에 넣을 문자열. Timestamp는 ISO로, object/array는 JSON으로 펼친다.
function toEditString(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (v instanceof Timestamp) return v.toDate().toISOString()
  if (typeof v === 'object') return JSON.stringify(v, null, 2)
  return String(v)
}

// 편집창 문자열 -> Firestore에 쓸 값. 원래 값의 타입을 보고 되돌린다.
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
  if (original !== null && typeof original === 'object') {
    if (str.trim() === '') return null
    return JSON.parse(str)
  }
  // 원래 null이었거나 문자열이었던 필드 — 빈 값이면 null, 아니면 문자열 그대로
  return str === '' ? null : str
}

function startEdit(row: Record<string, unknown>) {
  editingId.value = row.id as string
  saveError.value = ''
  const buf: Record<string, string> = {}
  for (const col of columns.value) {
    if (col === 'id') continue
    buf[col] = toEditString(row[col])
  }
  editBuffer.value = buf
}

function cancelEdit() {
  editingId.value = null
  saveError.value = ''
}

async function saveEdit(row: Record<string, unknown>) {
  saving.value = true
  saveError.value = ''
  try {
    const updates: Record<string, unknown> = {}
    for (const col of columns.value) {
      if (col === 'id') continue
      const original = row[col]
      const edited = editBuffer.value[col] ?? ''
      if (edited === toEditString(original)) continue // 안 바뀐 필드는 안 보낸다
      updates[col] = fromEditString(edited, original)
    }
    if (Object.keys(updates).length === 0) { editingId.value = null; return }
    const p = path.value.trim().replace(/^\/+|\/+$/g, '')
    const docRef = singleDoc.value ? doc(db, p) : doc(collection(db, p), row.id as string)
    await updateDoc(docRef, updates)
    Object.assign(row, updates)
    editingId.value = null
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

const SHORTCUTS = ['teams', 'players', 'leagues', 'seasons', 'stadiums', 'coaches', 'matches']
const OPS: WhereFilterOp[] = ['==', '!=', '<', '<=', '>', '>=']

interface FilterRow { field: string; op: WhereFilterOp; value: string }

const path = ref('teams')
const filters = ref<FilterRow[]>([])
const rows = ref<Record<string, unknown>[]>([])
const columns = ref<string[]>([])
const busy = ref(false)
const errorMsg = ref('')
const count = ref(0)
const singleDoc = ref(false)

// 자주 쓰는 하위 컬렉션은 타이핑 안 해도 버튼으로 바로 갈 수 있게 미리 알려준다.
const SUB_MAP: Record<string, string[]> = {
  teams: ['squad', 'seasons'],
  players: ['contracts'],
  coaches: ['contracts'],
  matches: ['recordings']
}
const subSuggestions = computed<string[]>(() => {
  const segs = path.value.trim().replace(/^\/+|\/+$/g, '').split('/').filter(Boolean)
  if (segs.length === 2) return SUB_MAP[segs[0]] ?? []
  if (segs.length === 4 && segs[0] === 'matches' && segs[2] === 'recordings') {
    return ['records', 'playerStats', 'cards', 'paths']
  }
  return []
})
function goSub(name: string) {
  const p = path.value.trim().replace(/\/+$/g, '')
  path.value = `${p}/${name}`
  load()
}

function addFilter() { filters.value.push({ field: '', op: '==', value: '' }) }
function removeFilter(i: number) { filters.value.splice(i, 1) }

// 필터 값 입력칸은 문자열이지만, 숫자/불리언처럼 보이면 그 타입으로 바꿔서 비교한다 —
// 안 그러면 round(숫자) 필드에 문자열 "5"로 == 걸어봐야 하나도 안 걸린다.
function parseFilterValue(v: string): unknown {
  const t = v.trim()
  if (t === 'true') return true
  if (t === 'false') return false
  if (t === 'null') return null
  if (t !== '' && !isNaN(Number(t))) return Number(t)
  return v
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (v instanceof Timestamp) return v.toDate().toLocaleString('ko-KR')
  if (Array.isArray(v)) return JSON.stringify(v)
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

async function load() {
  const p = path.value.trim().replace(/^\/+|\/+$/g, '')
  if (!p) return
  const segs = p.split('/').filter(Boolean)
  busy.value = true
  errorMsg.value = ''
  try {
    if (segs.length % 2 === 0) {
      // 짝수 세그먼트 = 문서 경로 하나(예: teams/ARS) — 문서 한 건만 조회
      singleDoc.value = true
      const snap = await getDoc(doc(db, p))
      if (!snap.exists()) {
        rows.value = []
        columns.value = []
        count.value = 0
        errorMsg.value = '해당 문서가 없다'
        return
      }
      const data = snap.data()
      columns.value = ['id', ...Object.keys(data)]
      rows.value = [{ id: snap.id, ...data }]
      count.value = 1
    } else {
      // 홀수 세그먼트 = 컬렉션 — 필터가 있으면 where()로 걸어서 조회
      singleDoc.value = false
      let q = query(collection(db, p))
      for (const f of filters.value) {
        if (!f.field.trim()) continue
        q = query(q, where(f.field.trim(), f.op, parseFilterValue(f.value)))
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
      count.value = docs.length
    }
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e)
    rows.value = []
    columns.value = []
  } finally {
    busy.value = false
  }
}

function goShortcut(name: string) {
  path.value = name
  load()
}

// 문서 ID를 눌러서 경로를 이어붙인다 — teams 조회 후 특정 팀 ID를 누르면
// path가 "teams/{id}"가 되고, 거기에 "/squad" 등을 직접 이어 타이핑해서 파고들 수 있다.
function drillInto(id: string) {
  const p = path.value.trim().replace(/\/+$/g, '')
  path.value = `${p}/${id}/`
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <div class="topBar">
        <div class="title">Firestore 데이터 뷰어</div>
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리로</NuxtLink>
      </div>

      <div class="shortcuts">
        <button v-for="name in SHORTCUTS" :key="name" class="chip" @click="goShortcut(name)">{{ name }}</button>
      </div>

      <div class="pathBar">
        <input v-model="path" class="pathInput" placeholder="예: matches/2627ENG17H0001/recordings/H/records" @keyup.enter="load">
        <button class="loadBtn" :disabled="busy" @click="load">{{ busy ? '조회 중...' : '조회' }}</button>
      </div>

      <div v-if="subSuggestions.length" class="quickRow">
        <span class="quickLabel">하위 보기:</span>
        <button v-for="name in subSuggestions" :key="name" class="quickBtn" @click="goSub(name)">{{ name }}</button>
      </div>

      <div class="filterBar">
        <div v-for="(f, i) in filters" :key="i" class="filterRow">
          <input v-model="f.field" class="filterField" placeholder="필드명 (예: round)">
          <select v-model="f.op" class="filterOp">
            <option v-for="op in OPS" :key="op" :value="op">{{ op }}</option>
          </select>
          <input v-model="f.value" class="filterValue" placeholder="값 (예: 5 / ARS / true)" @keyup.enter="load">
          <button class="actBtn" @click="removeFilter(i)">✕</button>
        </div>
        <button class="chip" @click="addFilter">+ 필터 추가</button>
      </div>

      <div class="fieldRef">
        <div class="fieldRefRow">선수: <code>players</code> + 필터 <code>name</code>=<code>손흥민</code></div>
        <div class="fieldRefRow">팀: <code>teams</code> + 필터 <code>nameKr</code>=<code>아스날</code></div>
        <div class="fieldRefRow">경기: <code>matches</code> + 필터 <code>leagueId</code>, <code>seasonId</code>, <code>round</code></div>
        <div class="fieldRefRow">ID 알면 바로: <code>teams/ARS</code> (이름은 안 됨, 필터 써야 함)</div>
        <div class="fieldRefRow">더보기: 표의 <span class="idBtn">파란 ID</span> 클릭</div>
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
                <button v-if="col === 'id'" class="idBtn" @click="drillInto(row.id as string)">{{ row.id }}</button>
                <textarea
                  v-else-if="editingId === row.id"
                  v-model="editBuffer[col]"
                  class="editInput"
                  rows="1"
                />
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

      <div class="footer">{{ count }}건 (전체 조회)</div>
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
.topBar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.title { color: rgba(255,255,255,0.85); font-weight: 700; letter-spacing: 0.02em; }
.backBtn { color: rgba(255,255,255,0.55); font-size: 13px; text-decoration: none; }
.backBtn:hover { color: #fff; }

.shortcuts { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.7);
  font-size: 12px;
  cursor: pointer;
}
.chip:hover { border-color: rgba(0,217,255,0.4); color: #fff; }

.pathBar { display: flex; gap: 8px; }
.pathInput {
  flex: 1;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 6px;
  color: #fff;
  font-family: monospace;
  font-size: 13px;
  padding: 8px 10px;
}
.loadBtn {
  padding: 8px 16px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(0,217,255,0.12);
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
}
.loadBtn:disabled { opacity: 0.5; cursor: not-allowed; }

.hint { color: rgba(255,255,255,0.4); font-size: 11px; line-height: 1.5; }
.hint code { color: rgba(0,217,255,0.8); }

.quickRow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.quickLabel { color: rgba(255,255,255,0.4); font-size: 12px; }
.quickBtn {
  padding: 5px 12px;
  border-radius: 6px;
  border: 1px solid rgba(0,217,255,0.3);
  background: rgba(0,217,255,0.08);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.quickBtn:hover { background: rgba(0,217,255,0.16); }

.filterBar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.filterRow { display: flex; gap: 4px; align-items: center; }
.filterField, .filterValue {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 4px;
  color: #fff;
  font-size: 12px;
  padding: 5px 8px;
  width: 130px;
}
.filterOp {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 4px;
  color: #fff;
  font-size: 12px;
  padding: 5px 4px;
}

.guide {
  color: rgba(255,255,255,0.55);
  font-size: 12px;
  line-height: 1.8;
  margin: 0;
  padding-left: 18px;
}
.guide code { color: rgba(0,217,255,0.8); }

.fieldRef {
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  color: rgba(255,255,255,0.6);
}
.fieldRefTitle { color: rgba(255,255,255,0.8); font-weight: 700; margin-bottom: 6px; }
.fieldRefRow { line-height: 1.8; }
.fieldRef code { color: rgba(0,217,255,0.8); }

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
.idBtn {
  background: none;
  border: none;
  color: rgba(0,217,255,0.85);
  cursor: pointer;
  font-size: 12px;
  text-decoration: underline;
  padding: 0;
}
.empty { padding: 20px; color: rgba(255,255,255,0.4); font-size: 13px; }

.editInput {
  width: 160px;
  min-height: 24px;
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

.footer { color: rgba(255,255,255,0.4); font-size: 11px; }
</style>
