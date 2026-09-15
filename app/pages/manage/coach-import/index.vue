<script setup lang="ts">
// 감독 재임 이력 엑셀("PL 감독 연혁-Admin.xlsx")을 업로드해서 Firestore로 옮기는 임시 도구.
// legacy-import(SQL 덤프 이관)와 같은 자리, 같은 패턴(파싱 → 미리보기 → 확인 후 쓰기)의 별도 도구다.
//
// 쓰는 시트 2개만 처리한다 — 나머지(Game/Captain/Coach/Coach_detail/Player_MST)는 이 작업과 무관.
//
//   1) `PL 감독` (재임기간 원장, ~400행) → `coachContracts` 컬렉션
//      - teamId는 팀 문서 ID와 맞춰 T_Code를 그대로 쓴다(매치 테이터 이관 시 conversion
//        도 T_Code를 그대로 homeTeamId/awayTeamId로 넣는 걸 이미 확인했다 — 따로 내부
//        약어(ars 등)로 바꾸지 않는다. teams.vue의 약어는 그 화면 전용 더미 데이터일 뿐).
//      - 같은 (팀, 감독, 정식/대행 상태)가 시즌 경계로 연속되는 행은 재임기간 하나로 합친다
//        (예: 코번트리 램파드 3시즌 행 → 문서 1개). 감독대행이 중간에 끼면 거기서 끊는다.
//      - "상태" 열이 "현재"인 행만 to를 null로 채운다(엑셀은 진행중 행도 시즌 종료 예정일이
//        박혀있어서 그대로 넣으면 "이미 물러났다"로 읽힌다).
//      - seasonId는 저장하지 않는다(날짜+시즌 캘린더로 언제든 역산 가능해서 중복 저장 안 함).
//
//   2) `감독 라운드` (라운드별 전개, ~6300행) → `coachRounds` 컬렉션 (가공 없이 그대로)
//      - 지금은 matches/recordings와 연결하지 않는다 — Firestore matches가 아직 일부
//        시즌(2023-2024 1~20R)만 들어와 있어서 지금 연결해봤자 대부분 못 붙는다.
//      - 대신 (teamId, plSeason, round)를 조인 키로 그대로 남겨서, 나중에 레거시 매치가
//        더 들어왔을 때 그 세 값으로 recordings.coachId를 채우는 별도 작업에서 쓴다.
//        레거시 SQL(ff_game/ff_game_info)엔 감독 컬럼이 아예 없어서, 이 라운드 데이터가
//        아니면 나중엔 이 정보를 구할 방법이 없다 — 그래서 지금 안 쓰더라도 저장은 해둔다.

import * as XLSX from 'xlsx'
import { Timestamp, writeBatch, doc, collection, type Firestore } from 'firebase/firestore'

const { $db } = useNuxtApp()
const db = $db as Firestore

const NOW = () => Timestamp.now()
function s(v: unknown): string { return v == null ? '' : String(v).trim() }

// 엑셀 날짜 칸이 텍스트("2018.08.01")로 들어있으면 그대로 쓰고, 혹시 엑셀 시리얼 숫자로
// 읽히면(셀 서식이 실제 날짜였던 경우) 같은 점(.) 구분 표기로 바꿔준다.
function normalizeDate(v: unknown): string {
  if (typeof v === 'number') {
    const d = XLSX.SSF.parse_date_code(v)
    return `${d.y}.${String(d.m).padStart(2, '0')}.${String(d.d).padStart(2, '0')}`
  }
  return s(v)
}

type Status = 'MANAGER' | 'CARETAKER'
function normalizeStatus(v: unknown): Status {
  return s(v).toLowerCase().startsWith('care') ? 'CARETAKER' : 'MANAGER'
}

interface ContractRow {
  coachId: string; coachName: string; coachNameEn: string
  teamId: string; from: string; to: string | null; status: Status
}
interface RoundRow extends ContractRow {
  round: number; plSeason: string
}

// ---------------------------------------------------------------------------
// 화면 상태
// ---------------------------------------------------------------------------

const fileName = ref('')
const log = ref<string[]>([])
const contracts = ref<ContractRow[] | null>(null)
const rounds = ref<RoundRow[] | null>(null)
const busy = ref(false)

function addLog(line: string) { log.value.push(line) }

function findSheet(wb: XLSX.WorkBook, name: string): XLSX.WorkSheet | null {
  const key = wb.SheetNames.find(n => n.trim() === name)
  return key ? wb.Sheets[key] : null
}

// `PL 감독` 행 하나 → ContractRow 하나(병합 전 원본 단위)
function toContractRow(r: Record<string, unknown>): ContractRow {
  const isCurrent = s(r['상태']) === '현재'
  return {
    coachId: s(r.m_id), coachName: s(r.Manager_KR), coachNameEn: s(r.Manager_EN),
    teamId: s(r.T_Code), from: normalizeDate(r.gm_date),
    to: isCurrent ? null : normalizeDate(r.e_date),
    status: normalizeStatus(r.Status_EN),
  }
}

/** 같은 (teamId, coachId, status)가 시즌 경계로 연속되면 재임기간 하나로 합친다. */
function mergeContracts(rows: ContractRow[]): ContractRow[] {
  const byTeam = new Map<string, ContractRow[]>()
  for (const r of rows) {
    if (!r.teamId || !r.coachId) continue
    const arr = byTeam.get(r.teamId) ?? []
    arr.push(r)
    byTeam.set(r.teamId, arr)
  }

  const merged: ContractRow[] = []
  for (const teamRows of byTeam.values()) {
    teamRows.sort((a, b) => a.from.localeCompare(b.from))
    let cur: ContractRow | null = null
    for (const r of teamRows) {
      if (cur && cur.coachId === r.coachId && cur.status === r.status) {
        cur.to = r.to // 계속 이어지는 재임 — 종료일만 최신 것으로 갱신
      } else {
        if (cur) merged.push(cur)
        cur = { ...r }
      }
    }
    if (cur) merged.push(cur)
  }
  return merged
}

function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  fileName.value = file.name
  log.value = []
  contracts.value = null
  rounds.value = null

  file.arrayBuffer().then(buf => {
    const wb = XLSX.read(buf, { type: 'array' })
    addLog(`시트 발견: ${wb.SheetNames.join(', ')}`)

    const plSheet = findSheet(wb, 'PL 감독')
    if (!plSheet) { addLog('❌ "PL 감독" 시트를 못 찾았습니다'); return }
    const plRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(plSheet)
    const rawContracts = plRows.map(toContractRow).filter(r => r.teamId && r.coachId)
    contracts.value = mergeContracts(rawContracts)
    addLog(`PL 감독: 원본 ${rawContracts.length}행 → 병합 후 ${contracts.value.length}건`)

    const roundSheet = findSheet(wb, '감독 라운드')
    if (!roundSheet) { addLog('❌ "감독 라운드" 시트를 못 찾았습니다'); return }
    const roundRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(roundSheet)
    rounds.value = roundRows
      .map(r => ({ ...toContractRow(r), round: Number(r.Round) || 0, plSeason: s(r.PlSeason) }))
      .filter(r => r.teamId && r.coachId && r.round > 0)
    addLog(`감독 라운드: ${rounds.value.length}건 (matches 연결 없이 원본 그대로 저장 예정)`)
  }).catch(err => {
    addLog(`❌ 파일을 읽는 중 오류: ${err instanceof Error ? err.message : String(err)}`)
  })
}

async function commitInChunks(items: { path: string; id: string; data: Record<string, unknown> }[]) {
  for (let i = 0; i < items.length; i += 450) {
    const chunk = items.slice(i, i + 450)
    const batch = writeBatch(db)
    for (const item of chunk) batch.set(doc(collection(db, item.path), item.id), item.data, { merge: true })
    await batch.commit()
    addLog(`  → ${Math.min(i + 450, items.length)}/${items.length} 저장`)
  }
}

async function runImport() {
  if (!contracts.value || !rounds.value) return
  if (!confirm('실제 Firestore(운영 프로젝트)에 씁니다. 계속할까요?')) return

  busy.value = true
  try {
    addLog(`coachContracts 저장 중... (${contracts.value.length}건)`)
    await commitInChunks(contracts.value.map(c => ({
      path: 'coachContracts', id: `${c.teamId}_${c.coachId}_${c.from}`,
      data: { ...c, createdAt: NOW(), createdBy: 'coach-import', updatedAt: NOW(), updatedBy: 'coach-import' }
    })))

    addLog(`coachRounds 저장 중... (${rounds.value.length}건)`)
    await commitInChunks(rounds.value.map(r => ({
      path: 'coachRounds', id: `${r.teamId}_${r.plSeason}_${r.round}`,
      data: { ...r, createdAt: NOW(), createdBy: 'coach-import' }
    })))

    addLog('완료.')
  } catch (e) {
    addLog(`❌ 오류: ${e instanceof Error ? e.message : String(e)}`)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="bg" />

    <div class="frame">
      <div class="topBar">
        <div class="title">감독 재임 이력 이관 (임시)</div>
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리로</NuxtLink>
      </div>

      <p class="note">
        "PL 감독 연혁" 엑셀을 올리면 <code>PL 감독</code> 시트(재임기간 원장 → <code>coachContracts</code>,
        같은 팀·같은 감독·같은 정식/대행 상태가 시즌 경계로 이어지면 하나로 합쳐서 저장)와
        <code>감독 라운드</code> 시트(라운드별 전개 → <code>coachRounds</code>, 가공 없이 원본 그대로)를
        함께 옮긴다. <code>matches</code>/<code>recordings.coachId</code> 연결은 지금 안 한다 — 아직
        일부 시즌만 이관돼 있어서 대부분 못 붙는다. 나머지 시트(Game/Captain/Coach 등)는 이 작업과
        무관해서 무시한다.
      </p>

      <input type="file" accept=".xlsx,.xls" @change="onFile" />
      <p v-if="fileName" class="fileName">{{ fileName }}</p>

      <div v-if="contracts" class="preview">
        <div class="previewTitle">coachContracts 미리보기 (앞 8건)</div>
        <div class="previewRow head"><span>감독</span><span>팀</span><span>기간</span><span>상태</span></div>
        <div v-for="(c, i) in contracts.slice(0, 8)" :key="i" class="previewRow">
          <span>{{ c.coachName }} ({{ c.coachNameEn }})</span>
          <span>{{ c.teamId }}</span>
          <span>{{ c.from }} ~ {{ c.to ?? '진행중' }}</span>
          <span>{{ c.status === 'CARETAKER' ? '대행' : '정식' }}</span>
        </div>
      </div>

      <div class="actions">
        <button :disabled="!contracts || !rounds || busy" @click="runImport">Firestore에 쓰기</button>
      </div>

      <pre class="log">{{ log.join('\n') }}</pre>
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
  gap: 12px;
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
.note { color: rgba(255,255,255,0.55); font-size: 12px; line-height: 1.6; }
.note code { color: rgba(0,217,255,0.85); }
.fileName { color: rgba(255,255,255,0.7); font-size: 12px; }
.preview { border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; overflow: hidden; }
.previewTitle { padding: 8px 10px; background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.7); font-size: 12px; font-weight: 700; }
.previewRow { display: grid; grid-template-columns: 2fr 1fr 1.6fr 0.8fr; gap: 8px; padding: 6px 10px; font-size: 12px; color: rgba(255,255,255,0.75); border-top: 1px solid rgba(255,255,255,0.06); }
.previewRow.head { color: rgba(255,255,255,0.45); font-weight: 700; border-top: none; }
.actions { display: flex; gap: 8px; }
.actions button {
  padding: 8px 16px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(0,217,255,0.1);
  color: #fff;
  cursor: pointer;
}
.actions button:disabled { opacity: 0.4; cursor: not-allowed; }
.log {
  height: 180px;
  overflow-y: auto;
  background: rgba(0,0,0,0.4);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px;
  padding: 10px;
  color: rgba(255,255,255,0.7);
  font-size: 12px;
  white-space: pre-wrap;
  margin: 0;
}
</style>
