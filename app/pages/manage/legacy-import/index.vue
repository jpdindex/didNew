<script setup lang="ts">
// SQL 덤프(mysqldump 출력)를 붙여넣어 레거시 데이터를 Firestore로 이관하는 임시 도구.
// docs/06_backend_integration.md 4단계, docs/05_legacy_field_mapping.md 매핑표 기준.
//
// 옮기는 것 — 레거시 19개 표 중 didNew가 실제로 쓰는 전부:
//   ff_team/ff_player/ff_league/ff_season/ff_stadium → 참조 데이터
//   ff_team.t_coach_kr → coaches (감독은 이름만, 아래 주의 참고)
//   ff_player_team → 이적 계약 이력 + teams/{id}/squad(현재 계약만 골라 만드는 파생 뷰)
//   ff_game → 경기 일정 + teams/{id}/seasons(그 시즌에 실제로 뛴 경기들로 역산한 파생 데이터)
//   ff_game_info → 팀별 기록 세션(레거시 gi_* KPI 값을 그대로 kpi 맵에 채움 — 이미 끝난 경기라 재계산 불필요)
//   ff_game_record → 원본 레코드(공격 판정 로직 재검증용)
//   ff_game_player + ff_game_player_log → RecordingDoc.lineup (출전+교체 정보 병합)
//   ff_game_player → playerStats (선수 개인 KPI. 평점 필드는 전부 null — jpd-rating 호출 후 채워짐)
//   ff_game_card → cards 서브컬렉션
//   ff_game_path → paths 서브컬렉션(gt_id로 묶인 records를 recordIds에 채움)
//   ff_game_bap → 해당 RecordDoc.bapReason에 병합
// 의도적으로 안 옮기는 것 3개 — 옮기면 오히려 잘못된 데이터가 되는 경우들:
//   - ff_formation: 포메이션은 팀 소유물이 아니라 공용 상수(TeamSelection.vue)라 컬렉션 자체가 없음
//   - ff_user/ff_session/ff_member_session: RecorderProfileDoc의 문서 ID는 반드시 "실제 로그인할
//     Firebase Auth UID"여야 하는데, 레거시 u_id는 그 UID가 아니다. 옛 u_id로 문서를 만들어봐야
//     실제 로그인한 사람의 uid와 안 맞아서 앱이 절대 못 찾는 죽은 문서가 될 뿐이다 — 이건 사람이
//     "이 레거시 계정 = 이 Firebase 계정"을 직접 매칭해줘야 하는 작업이라 자동화 대상이 아니다
//   - ff_game_state: 레거시 gm_state 강제수정용 테이블. 관리자가 recordings.status를 직접 고치는
//     것으로 대체됨(docs/05_legacy_field_mapping.md 참고), 옮길 대상 데이터가 없음
//   - exportJobs: 갱신 파이프라인 "진행 상태" 문서라 옮길 과거 데이터 자체가 없다(이미 끝난
//     경기는 파이프라인을 다시 돌릴 필요가 없음) — CoachContractDoc과 마찬가지로 만들지 않는다
//
// 컬럼 순서는 덤프 안의 CREATE TABLE 정의 또는 INSERT 문의 명시적 컬럼 목록에서만 읽는다 —
// 둘 다 없으면 그 표는 컬럼 순서를 추측하지 않고 건너뛴다(잘못된 컬럼 매핑으로 조용히
// 틀린 데이터가 들어가는 것을 막기 위해). mysqldump는 기본적으로 CREATE TABLE을 포함하니
// 보통은 문제없다.
//
// 주의(이 페이지가 이 앱 최초의 실제 Firestore 쓰기 코드다):
//   - firestore.rules가 아직 없다 — 프로젝트가 "테스트 모드"가 아니면 쓰기가 permission-denied로
//     막힐 수 있다. 그러면 Firebase 콘솔에서 규칙을 잠시 열어야 한다.
//   - lineup 맵의 키는 새 스키마에서 원래 포메이션 슬롯 코드(예: 'gk','o0')인데, 레거시엔 그
//     개념이 없다. 이 도구는 대신 playerId를 키로 쓴다 — 과거 경기를 새 포메이션 UI로 다시
//     열어 편집할 일은 없으므로(검증·조회용) 실용적인 타협이다.
//   - LineupEntry.no/pos는 그 경기 날짜에 유효했던 ff_player_team 계약에서 채운다. 겹치는
//     계약이 없으면 가장 가까운 과거 계약으로 대체한다(완벽하진 않음, 로그에 표시).

import { Timestamp, writeBatch, doc, collection, type Firestore } from 'firebase/firestore'

const { $db } = useNuxtApp()
const db = $db as Firestore

// ---------------------------------------------------------------------------
// 1. SQL 덤프 파서 — CREATE TABLE / INSERT INTO 만 읽는다
// ---------------------------------------------------------------------------

interface ParsedTable { columns: string[] | null; rows: unknown[][] }

function parseLiteral(tok: string): unknown {
  const t = tok.trim()
  if (t === '' || /^NULL$/i.test(t)) return null
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t)
  return t.replace(/\\'/g, "'").replace(/\\"/g, '"').replace(/\\\\/g, '\\').replace(/''/g, "'")
}

function parseTuple(s: string): unknown[] {
  const vals: unknown[] = []
  let cur = ''
  let inStr = false
  let strCh = ''
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (inStr) {
      if (c === '\\') { cur += c; i++; if (i < s.length) cur += s[i]; continue }
      if (c === strCh) { inStr = false; continue }
      cur += c
      continue
    }
    if (c === "'" || c === '"') { inStr = true; strCh = c; continue }
    if (c === ',') { vals.push(parseLiteral(cur)); cur = ''; continue }
    cur += c
  }
  vals.push(parseLiteral(cur))
  return vals
}

function splitTuples(blob: string): unknown[][] {
  const tuples: unknown[][] = []
  let depth = 0
  let cur = ''
  let inStr = false
  let strCh = ''
  for (let i = 0; i < blob.length; i++) {
    const c = blob[i]
    if (inStr) {
      cur += c
      if (c === '\\') { i++; if (i < blob.length) cur += blob[i]; continue }
      if (c === strCh) inStr = false
      continue
    }
    if (c === "'" || c === '"') { inStr = true; strCh = c; cur += c; continue }
    if (c === '(') { depth++; if (depth === 1) { cur = ''; continue } }
    if (c === ')') {
      depth--
      if (depth === 0) { tuples.push(parseTuple(cur)); continue }
    }
    if (depth >= 1) cur += c
  }
  return tuples
}

function parseDump(sql: string): Record<string, ParsedTable> {
  const tables: Record<string, ParsedTable> = {}

  const createRe = /CREATE TABLE\s+`?(\w+)`?\s*\(([\s\S]*?)\)\s*ENGINE/gi
  let m: RegExpExecArray | null
  while ((m = createRe.exec(sql))) {
    const table = m[1]
    const cols: string[] = []
    for (const line of m[2].split(/,\r?\n/)) {
      const cm = line.trim().match(/^`([^`]+)`/)
      if (cm) cols.push(cm[1])
    }
    tables[table] = { columns: cols, rows: [] }
  }

  const insertRe = /INSERT INTO\s+`?(\w+)`?\s*(\(([^)]+)\))?\s*VALUES\s*([\s\S]*?);/gi
  while ((m = insertRe.exec(sql))) {
    const table = m[1]
    const explicitCols = m[3] ? m[3].split(',').map(c => c.trim().replace(/`/g, '')) : null
    if (!tables[table]) tables[table] = { columns: null, rows: [] }
    if (explicitCols) tables[table].columns = explicitCols
    tables[table].rows.push(...splitTuples(m[4]))
  }

  return tables
}

function rowsToObjects(t: ParsedTable): Record<string, unknown>[] {
  if (!t.columns) return []
  return t.rows.map(row => {
    const obj: Record<string, unknown> = {}
    t.columns!.forEach((col, i) => { obj[col] = row[i] })
    return obj
  })
}

// ---------------------------------------------------------------------------
// 2. 변환 헬퍼
// ---------------------------------------------------------------------------

const NOW = () => Timestamp.now()
function toTs(v: unknown): Timestamp | null {
  if (!v || v === '0000-00-00' || v === '0000-00-00 00:00:00') return null
  const d = new Date(String(v).replace(' ', 'T'))
  return isNaN(d.getTime()) ? null : Timestamp.fromDate(d)
}
function n(v: unknown): number { const x = Number(v); return isNaN(x) ? 0 : x }
function s(v: unknown): string { return v == null ? '' : String(v) }
function findContractNoPos(
  contracts: { from: string; to: string | null; no: string; pos: string }[] | undefined,
  matchDate: string
): { no: string; pos: string } {
  if (!contracts || contracts.length === 0) return { no: '', pos: '' }
  const active = contracts.find(c => c.from <= matchDate && (!c.to || matchDate <= c.to))
  if (active) return { no: active.no, pos: active.pos }
  // 정확히 겹치는 계약이 없으면 가장 가까운(이전) 계약으로 대체한다 — 완벽하진 않지만 빈 값보다 낫다
  const sorted = [...contracts].sort((a, b) => a.from.localeCompare(b.from))
  const prior = [...sorted].reverse().find(c => c.from <= matchDate)
  return prior ? { no: prior.no, pos: prior.pos } : { no: sorted[0].no, pos: sorted[0].pos }
}

function normalizeHalf(v: unknown): 'H1' | 'H2' | 'H3' | 'H4' {
  const x = String(v)
  if (x === '1' || x === 'H1') return 'H1'
  if (x === '2' || x === 'H2') return 'H2'
  if (x === '3' || x === 'H3') return 'H3'
  return 'H4'
}

// ---------------------------------------------------------------------------
// 3. 화면 상태
// ---------------------------------------------------------------------------

const dumpText = ref('')
const log = ref<string[]>([])
const parsed = ref<Record<string, ParsedTable> | null>(null)
const busy = ref(false)

const SUPPORTED = [
  'ff_team', 'ff_player', 'ff_league', 'ff_season', 'ff_stadium', 'ff_player_team',
  'ff_game', 'ff_game_info', 'ff_game_record',
  'ff_game_player', 'ff_game_player_log', 'ff_game_card', 'ff_game_path', 'ff_game_bap'
]

function addLog(line: string) { log.value.push(line) }

function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  file.text().then(text => { dumpText.value = text })
}

function doParse() {
  log.value = []
  parsed.value = parseDump(dumpText.value)
  const found = Object.keys(parsed.value)
  addLog(`덤프에서 ${found.length}개 표 발견: ${found.join(', ') || '(없음)'}`)
  for (const name of SUPPORTED) {
    const t = parsed.value[name]
    if (!t) { addLog(`⚠️ ${name} — 덤프에 없음, 건너뜀`); continue }
    if (!t.columns) { addLog(`⚠️ ${name} — 컬럼 정보를 못 찾음(CREATE TABLE도, INSERT의 명시적 컬럼 목록도 없음), 건너뜀`); continue }
    addLog(`✅ ${name} — ${t.rows.length}행, 컬럼 ${t.columns.length}개`)
  }
}

// ---------------------------------------------------------------------------
// 4. Firestore 쓰기 — 500개씩 배치
// ---------------------------------------------------------------------------

async function commitInChunks(items: { path: string; id: string; data: Record<string, unknown> }[]) {
  for (let i = 0; i < items.length; i += 450) {
    const chunk = items.slice(i, i + 450)
    const batch = writeBatch(db)
    for (const item of chunk) {
      batch.set(doc(collection(db, item.path), item.id), item.data, { merge: true })
    }
    await batch.commit()
    addLog(`  → ${Math.min(i + 450, items.length)}/${items.length} 저장`)
  }
}

async function runImport() {
  if (!parsed.value) return
  if (!confirm('실제 Firestore(운영 프로젝트)에 씁니다. 계속할까요?')) return

  busy.value = true
  try {
    const p = parsed.value
    const playerNameById = new Map<string, string>()
    const contractsByPlayerTeam = new Map<string, { from: string; to: string | null; no: string; pos: string }[]>()

    // 참조 데이터
    const coachesByName = new Map<string, { nameKr: string }>()
    function coachId(nameKr: string): string {
      return nameKr.trim().replace(/\s+/g, '-') || 'unknown'
    }
    if (p.ff_team?.columns) {
      const rows = rowsToObjects(p.ff_team)
      addLog(`teams 저장 중... (${rows.length}건)`)
      for (const r of rows) {
        if (r.t_coach_kr) coachesByName.set(coachId(s(r.t_coach_kr)), { nameKr: s(r.t_coach_kr) })
      }
      await commitInChunks(rows.map(r => ({
        path: 'teams', id: s(r.t_code),
        data: {
          name: s(r.t_name), nameKr: s(r.t_name_kr), nameFull: s(r.t_name_full), nameShort: s(r.t_name_short),
          textColor: r.u_text_color_code ? s(r.u_text_color_code) : null,
          stadiumId: r.s_code ? s(r.s_code) : null,
          currentCoachId: r.t_coach_kr ? coachId(s(r.t_coach_kr)) : null,
          currentLeagueId: r.l_code ? s(r.l_code) : null,
          foundedAt: r.t_begin ? s(r.t_begin) : null,
          dissolvedAt: r.t_end ? s(r.t_end) : null,
          createdAt: NOW(), createdBy: 'legacy-import', updatedAt: NOW(), updatedBy: 'legacy-import'
        }
      })))
      if (coachesByName.size) {
        addLog(`coaches 저장 중... (${coachesByName.size}명)`)
        await commitInChunks([...coachesByName.entries()].map(([id, c]) => ({
          path: 'coaches', id,
          data: {
            name: c.nameKr, nameKr: c.nameKr, active: true,
            createdAt: NOW(), createdBy: 'legacy-import', updatedAt: NOW(), updatedBy: 'legacy-import'
          }
        })))
        addLog('  ⚠️ 레거시엔 감독 재임 기간 이력이 없다(t_coach_kr 문자열 하나뿐) — CoachContractDoc(이력)은 못 만든다. 현재 감독만 반영됨, 과거 감독 이력은 수동 입력 필요')
      }
    }

    if (p.ff_player?.columns) {
      const rows = rowsToObjects(p.ff_player)
      addLog(`players 저장 중... (${rows.length}건)`)
      for (const r of rows) playerNameById.set(s(r.p_id), s(r.p_name))
      await commitInChunks(rows.map(r => ({
        path: 'players', id: s(r.p_id),
        data: {
          name: s(r.p_name), nameEn: r.p_name_en ? s(r.p_name_en) : null, nameFull: r.p_name_full ? s(r.p_name_full) : null,
          foot: r.p_foot ? s(r.p_foot) : null, birth: r.p_birth ? s(r.p_birth) : null,
          height: r.p_height != null ? n(r.p_height) : null, nation: r.p_nation ? s(r.p_nation) : null,
          legacyPlayerId: r.p_id_old != null ? n(r.p_id_old) : null, active: true,
          createdAt: NOW(), createdBy: 'legacy-import', updatedAt: NOW(), updatedBy: 'legacy-import'
        }
      })))
    }

    if (p.ff_league?.columns) {
      const rows = rowsToObjects(p.ff_league)
      addLog(`leagues 저장 중... (${rows.length}건)`)
      await commitInChunks(rows.map(r => ({
        path: 'leagues', id: s(r.league_code),
        data: { name: s(r.league_name), nameEn: r.league_name_en ? s(r.league_name_en) : null, createdAt: NOW(), createdBy: 'legacy-import', updatedAt: NOW(), updatedBy: 'legacy-import' }
      })))
    }

    if (p.ff_season?.columns) {
      const rows = rowsToObjects(p.ff_season)
      addLog(`seasons 저장 중... (${rows.length}건)`)
      await commitInChunks(rows.map(r => ({
        path: 'seasons', id: `${s(r.l_code)}_${s(r.s_name).replace(/\s+/g, '')}`,
        data: {
          leagueId: s(r.l_code), name: s(r.s_name), from: s(r.s_fr_date), to: s(r.s_to_date),
          alias: r.s_alias ? s(r.s_alias) : null,
          createdAt: NOW(), createdBy: 'legacy-import', updatedAt: NOW(), updatedBy: 'legacy-import'
        }
      })))
    }

    if (p.ff_stadium?.columns) {
      const rows = rowsToObjects(p.ff_stadium)
      addLog(`stadiums 저장 중... (${rows.length}건)`)
      await commitInChunks(rows.map(r => ({
        path: 'stadiums', id: s(r.s_code),
        data: {
          name: s(r.s_name), nameKr: r.s_name_kr ? s(r.s_name_kr) : null, seats: r.s_seats != null ? n(r.s_seats) : null,
          country: r.s_country ? s(r.s_country) : null, city: r.s_city ? s(r.s_city) : null,
          homeTeamId: r.s_hometeam ? s(r.s_hometeam) : null, surface: r.s_ground ? s(r.s_ground) : null,
          createdAt: NOW(), createdBy: 'legacy-import', updatedAt: NOW(), updatedBy: 'legacy-import'
        }
      })))
    }

    if (p.ff_player_team?.columns) {
      const rows = rowsToObjects(p.ff_player_team)
      addLog(`선수 계약 이력 저장 중... (${rows.length}건)`)
      for (const r of rows) {
        const key = `${s(r.p_id)}_${s(r.t_code)}`
        const arr = contractsByPlayerTeam.get(key) ?? []
        arr.push({ from: s(r.pt_begin), to: r.pt_end ? s(r.pt_end) : null, no: r.pt_num ? s(r.pt_num) : '', pos: r.pt_pos ? s(r.pt_pos) : '' })
        contractsByPlayerTeam.set(key, arr)
      }
      await commitInChunks(rows.map(r => ({
        path: `players/${s(r.p_id)}/contracts`, id: `${s(r.t_code)}_${s(r.pt_begin)}`,
        data: {
          teamId: s(r.t_code), leagueId: r.l_code ? s(r.l_code) : null,
          from: s(r.pt_begin), to: r.pt_end ? s(r.pt_end) : null,
          no: r.pt_num ? s(r.pt_num) : null, pos: r.pt_pos ? s(r.pt_pos) : null,
          createdAt: NOW(), createdBy: 'legacy-import', updatedAt: NOW(), updatedBy: 'legacy-import'
        }
      })))

      // 현재 스쿼드(SquadEntry)는 원본이 아니라 파생 뷰다 — to가 없는(현재 유효) 계약에서 만든다
      const currentContracts = rows.filter(r => !r.pt_end)
      addLog(`squad(파생 뷰) 저장 중... (${currentContracts.length}건)`)
      await commitInChunks(currentContracts.map(r => ({
        path: `teams/${s(r.t_code)}/squad`, id: s(r.p_id),
        data: {
          name: playerNameById.get(s(r.p_id)) ?? '',
          no: r.pt_num ? s(r.pt_num) : '', pos: r.pt_pos ? s(r.pt_pos) : '',
          contractId: `${s(r.t_code)}_${s(r.pt_begin)}`, since: s(r.pt_begin)
        }
      })))
    }

    // 경기 일정 — 이후 단계(recordings/records/lineup)가 이 맵을 참조한다.
    // is_realtime은 ff_game 컬럼이지만 목적지는 RecordingDoc.inputMode라(§05 매핑표),
    // 여기서 gm_id별로 뽑아뒀다가 ff_game_info 처리할 때 꺼내 쓴다.
    const matchesById = new Map<string, { homeTeamId: string; awayTeamId: string; date: string; isRealtime: boolean }>()
    if (p.ff_game?.columns) {
      const rows = rowsToObjects(p.ff_game)
      addLog(`matches 저장 중... (${rows.length}건)`)
      for (const r of rows) {
        matchesById.set(s(r.gm_id), {
          homeTeamId: s(r.gm_h_t_code), awayTeamId: s(r.gm_a_t_code), date: s(r.gm_date),
          isRealtime: n(r.is_realtime) === 1 || s(r.is_realtime).toUpperCase() === 'Y'
        })
      }
      await commitInChunks(rows.map(r => ({
        path: 'matches', id: s(r.gm_id),
        data: {
          date: s(r.gm_date), kickoffTime: r.gm_time ? s(r.gm_time) : null,
          leagueId: s(r.gm_league), seasonId: s(r.gm_id).slice(0, 8),
          matchType: 'league', round: r.gm_round != null ? n(r.gm_round) : null,
          group: r.gm_sub_league ? s(r.gm_sub_league) : null,
          stadiumId: s(r.gm_s_code), homeTeamId: s(r.gm_h_t_code), awayTeamId: s(r.gm_a_t_code),
          score: { home: n(r.gi_goal_home), away: n(r.gi_goal_away) },
          createdAt: NOW(), updatedAt: NOW()
        }
      })))
      addLog('  ※ matchType은 전부 league로 채웠다 — 토너먼트 경기는 이관 후 수동으로 stage/matchType 보정 필요')

      // teams/{teamId}/seasons/{seasonId} — 승강제 대응 파생 데이터. 레거시 ff_team.l_code는
      // "현재" 소속 리그 하나뿐이라 시즌별 이력이 없다 — 대신 그 시즌에 실제로 뛴 경기들의
      // leagueId로 역산한다.
      const teamSeasonLeague = new Map<string, string>()
      for (const r of rows) {
        const seasonId = s(r.gm_id).slice(0, 8)
        const leagueId = s(r.gm_league)
        teamSeasonLeague.set(`${s(r.gm_h_t_code)}_${seasonId}`, leagueId)
        teamSeasonLeague.set(`${s(r.gm_a_t_code)}_${seasonId}`, leagueId)
      }
      addLog(`team-seasons(파생) 저장 중... (${teamSeasonLeague.size}건)`)
      await commitInChunks([...teamSeasonLeague.entries()].map(([key, leagueId]) => {
        const [teamId, seasonId] = key.split('_')
        return { path: `teams/${teamId}/seasons`, id: seasonId, data: { leagueId } }
      }))
    }

    // 팀별 기록 세션 — ff_game_record가 참조할 (gi_id → {gmId, side}) 맵을 여기서 만든다
    const recordingByGiId = new Map<string, { gmId: string; side: 'H' | 'A' }>()
    if (p.ff_game_info?.columns) {
      const rows = rowsToObjects(p.ff_game_info)
      addLog(`recordings 저장 중... (${rows.length}건)`)
      const items: { path: string; id: string; data: Record<string, unknown> }[] = []
      for (const r of rows) {
        const gmId = s(r.gm_id)
        const match = matchesById.get(gmId)
        const side: 'H' | 'A' = match && match.homeTeamId === s(r.gi_h_t_code) ? 'H' : 'A'
        recordingByGiId.set(s(r.gi_id), { gmId, side })
        items.push({
          path: `matches/${gmId}/recordings`, id: side,
          data: {
            side, teamId: s(r.gi_h_t_code), opponentTeamId: s(r.gi_a_t_code),
            recorders: r.gi_user_id ? { [s(r.gi_user_id)]: { rank: 'main', joinedAt: NOW() } } : {},
            recorderIds: r.gi_user_id ? [s(r.gi_user_id)] : [],
            status: 'final', inputMode: match?.isRealtime ? '실시간' : '분석',
            fieldSide: r.gi_part === 'R' ? 'right' : 'left',
            fieldSideEx: r.gi_part_ex ? (r.gi_part_ex === 'R' ? 'right' : 'left') : null,
            formationKey: r.gi_formation ? s(r.gi_formation) : '',
            lineup: {},
            halves: {
              H1: r.gi_h1_begin ? { startedAt: toTs(r.gi_h1_begin), seconds: n(r.gi_h1_seconds) } : null,
              H2: r.gi_h2_begin ? { startedAt: toTs(r.gi_h2_begin), seconds: n(r.gi_h2_seconds) } : null
            },
            h1Locked: true, h2Locked: true, maxSeq: 0,
            kpi: {
              TAP: n(r.gi_tmp), DAP: n(r.gi_tap), TTP: n(r.gi_ttp), DTP: n(r.gi_ctp), BAP: n(r.gi_bap),
              DTB: n(r.gi_ctb), DTM: n(r.gi_ctm), DTA: n(r.gi_cta), DTS: n(r.gi_cts),
              SHOT: n(r.gi_sht), ASR: n(r.gi_asr), SSR: n(r.gi_ssr), GOAL: n(r.gi_gol), OG: 0
            },
            kpiComputedAt: NOW(), kpiVersion: 1, teamRating: null, ratingBasedOn: null,
            legacyGiId: n(r.gi_id), syncedAt: null,
            createdAt: toTs(r.gi_regdt) ?? NOW(), updatedAt: toTs(r.gi_moddt) ?? NOW()
          }
        })
      }
      await commitInChunks(items)
      addLog('  ※ kpi.OG(자책골 수)는 레거시에 없는 필드라 전부 0으로 채웠다 — 자책골 있는 경기는 수동 보정 필요')
    }

    // 원본 레코드 — half/halfSeconds 그룹 안에서 seq를 새로 채번한다
    // gt_id별로 레코드 ID를 모아뒀다가(pathRecordIds) ff_game_path 저장할 때 recordIds로 쓴다 —
    // 새 RecordDoc 자체엔 gt_id를 저장하지 않는다(§05: export 시 computeAttackPaths()가 계산).
    const pathRecordIds = new Map<string, string[]>()
    if (p.ff_game_record?.columns) {
      const rows = rowsToObjects(p.ff_game_record)
      addLog(`records 저장 중... (${rows.length}건)`)
      const seqCounter = new Map<string, number>()
      const items: { path: string; id: string; data: Record<string, unknown> }[] = []
      let skipped = 0
      for (const r of rows) {
        const rec = recordingByGiId.get(s(r.gi_id))
        if (!rec) { skipped++; continue }
        const half = normalizeHalf(r.gr_half)
        const halfSeconds = n(r.gr_half_seconds)
        const key = `${rec.gmId}_${rec.side}_${half}_${halfSeconds}`
        const seq = seqCounter.get(key) ?? 0
        seqCounter.set(key, seq + 1)
        if (r.gt_id != null) {
          const pathKey = `${rec.gmId}_${rec.side}_${s(r.gt_id)}`
          const arr = pathRecordIds.get(pathKey) ?? []
          arr.push(s(r.gr_id))
          pathRecordIds.set(pathKey, arr)
        }
        items.push({
          path: `matches/${rec.gmId}/recordings/${rec.side}/records`, id: s(r.gr_id),
          data: {
            half, halfSeconds, seq,
            act: r.gr_act_code ? s(r.gr_act_code) : '',
            res: r.gr_res_code ? s(r.gr_res_code) : '',
            area: n(r.gr_area_code),
            posX: r.gr_pos_x != null ? n(r.gr_pos_x) / 971 * 100 : null,
            posY: r.gr_pos_y != null ? n(r.gr_pos_y) / 634 * 100 : null,
            shootPosX: r.gr_shoot_pos_x != null ? n(r.gr_shoot_pos_x) : null,
            shootPosY: r.gr_shoot_pos_y != null ? n(r.gr_shoot_pos_y) : null,
            playerId: r.p_id ? s(r.p_id) : null,
            createdBy: 'legacy-import', source: 'did',
            createdAt: toTs(r.gr_regdt) ?? NOW()
          }
        })
      }
      await commitInChunks(items)
      if (skipped) addLog(`  ⚠️ recordings를 못 찾아 건너뛴 레코드 ${skipped}건 (ff_game_info에 해당 gi_id 없음)`)
    }

    // 라인업 — ff_game_player(출전) + ff_game_player_log(교체)를 합쳐 RecordingDoc.lineup 맵으로
    if (p.ff_game_player?.columns) {
      const rows = rowsToObjects(p.ff_game_player)
      addLog(`라인업 반영 중... (${rows.length}건)`)
      const lineupByRecording = new Map<string, Record<string, Record<string, unknown>>>()
      let missingContract = 0
      for (const r of rows) {
        const rec = recordingByGiId.get(s(r.gi_id))
        if (!rec) continue
        const playerId = s(r.p_id)
        const teamId = s(r.t_code)
        const matchDate = matchesById.get(rec.gmId)?.date ?? ''
        const { no, pos } = findContractNoPos(contractsByPlayerTeam.get(`${playerId}_${teamId}`), matchDate)
        if (!no && !pos) missingContract++
        const key = `${rec.gmId}_${rec.side}`
        const map = lineupByRecording.get(key) ?? {}
        map[playerId] = {
          // 레거시엔 포메이션 슬롯 개념이 없어 playerId를 그대로 슬롯 값으로 쓴다(v1 타협)
          slot: playerId,
          order: n(r.gp_order),
          type: r.gp_type === 'BENCH' ? 'BENCH' : 'START',
          no, pos,
          name: playerNameById.get(playerId) ?? '',
          inHalf: r.gp_in_half ? normalizeHalf(r.gp_in_half) : null,
          inSeconds: r.gp_in_half_seconds != null ? n(r.gp_in_half_seconds) : null,
          outHalf: r.gp_out_half ? normalizeHalf(r.gp_out_half) : null,
          outSeconds: r.gp_out_half_seconds != null ? n(r.gp_out_half_seconds) : null
        }
        lineupByRecording.set(key, map)
      }

      if (p.ff_game_player_log?.columns) {
        const logRows = rowsToObjects(p.ff_game_player_log)
        for (const r of logRows) {
          const rec = recordingByGiId.get(s(r.gi_id))
          if (!rec) continue
          const map = lineupByRecording.get(`${rec.gmId}_${rec.side}`)
          if (!map) continue
          const half = r.pl_in_half ? normalizeHalf(r.pl_in_half) : null
          const seconds = r.pl_in_seconds != null ? n(r.pl_in_seconds) : null
          const inId = r.pl_in_p_id ? s(r.pl_in_p_id) : null
          const outId = r.pl_out_p_id ? s(r.pl_out_p_id) : null
          if (inId && map[inId]) {
            map[inId].inHalf = half
            map[inId].inSeconds = seconds
            if (r.pl_in_position) map[inId].pos = s(r.pl_in_position)
          }
          if (outId && map[outId]) { map[outId].outHalf = half; map[outId].outSeconds = seconds }
        }
      }

      const items: { path: string; id: string; data: Record<string, unknown> }[] = []
      for (const [key, lineup] of lineupByRecording) {
        const [gmId, side] = key.split('_')
        items.push({ path: `matches/${gmId}/recordings`, id: side, data: { lineup } })
      }
      await commitInChunks(items)
      if (missingContract) addLog(`  ⚠️ 그 시점 계약 이력을 못 찾아 no/pos를 비워둔 라인업 항목 ${missingContract}건`)

      // 같은 ff_game_player 표에 선수 개인 KPI도 같이 있다 — PlayerStatsDoc으로 별도 저장.
      // 평점(score*/jmx/apx/tpx/fpx 등)은 레거시에 없으니 null — jpd-rating 호출 후 채워진다.
      addLog(`playerStats 저장 중... (${rows.length}건)`)
      const statItems: { path: string; id: string; data: Record<string, unknown> }[] = []
      let statSkipped = 0
      for (const r of rows) {
        const rec = recordingByGiId.get(s(r.gi_id))
        if (!rec) { statSkipped++; continue }
        statItems.push({
          path: `matches/${rec.gmId}/recordings/${rec.side}/playerStats`, id: s(r.p_id),
          data: {
            playerId: s(r.p_id),
            TAP: n(r.gp_tmp), DAP: n(r.gp_tap), UTP: n(r.gp_utp), DTP: n(r.gp_ctp), TTP: n(r.gp_ttp),
            SHOT: n(r.gp_sht), AST: n(r.gp_ast), GOAL: n(r.gp_gol),
            DTB: n(r.gp_ctb), DTM: n(r.gp_ctm), DTA: n(r.gp_cta), DTS: n(r.gp_cts),
            GTB: n(r.gp_gtb), GTM: n(r.gp_gtm), ASR: n(r.gp_asr), SSR: n(r.gp_ssr),
            scoreRel: null, scoreAbs: null, score: null,
            jmx: null, apx: null, apxGrade: null, tpx: null, tpxGrade: null, fpx: null, fpxGrade: null,
            ratingBasedOn: null
          }
        })
      }
      await commitInChunks(statItems)
      if (statSkipped) addLog(`  ⚠️ recordings를 못 찾아 건너뛴 playerStats ${statSkipped}건`)
    }

    // 카드
    if (p.ff_game_card?.columns) {
      const rows = rowsToObjects(p.ff_game_card)
      addLog(`cards 저장 중... (${rows.length}건)`)
      const items: { path: string; id: string; data: Record<string, unknown> }[] = []
      let skipped = 0
      for (const r of rows) {
        const rec = recordingByGiId.get(s(r.gi_id))
        if (!rec) { skipped++; continue }
        items.push({
          path: `matches/${rec.gmId}/recordings/${rec.side}/cards`, id: s(r.c_id),
          data: {
            playerId: s(r.gp_id), half: normalizeHalf(r.gp_card_half), halfSeconds: n(r.gp_card_time),
            card: r.gp_card_card === 'R' ? 'R' : 'Y', createdBy: 'legacy-import', createdAt: NOW()
          }
        })
      }
      await commitInChunks(items)
      if (skipped) addLog(`  ⚠️ recordings를 못 찾아 건너뛴 카드 ${skipped}건`)
    }

    // 공격경로 — gt_id로 묶인 레코드 ID 목록(pathRecordIds)은 위 records 처리 때 이미 모아뒀다
    if (p.ff_game_path?.columns) {
      const rows = rowsToObjects(p.ff_game_path)
      addLog(`paths 저장 중... (${rows.length}건)`)
      const items: { path: string; id: string; data: Record<string, unknown> }[] = []
      let skipped = 0
      for (const r of rows) {
        const rec = recordingByGiId.get(s(r.gi_id))
        if (!rec) { skipped++; continue }
        let ptype: 'UPP' | 'UTP' | 'DTP' | 'STP' = 'UPP'
        if (n(r.gt_ctp)) ptype = 'DTP'
        else if (n(r.gt_utp)) ptype = 'UTP'
        else if (n(r.gt_stp)) ptype = 'STP'
        const pathKey = `${rec.gmId}_${rec.side}_${s(r.gt_id)}`
        items.push({
          path: `matches/${rec.gmId}/recordings/${rec.side}/paths`, id: s(r.gt_id),
          data: {
            gtId: s(r.gt_id), resCode: r.gt_res_code ? s(r.gt_res_code) : '',
            dsp: !!n(r.gt_csp), ttp: !!n(r.gt_ttp), ptype,
            recordIds: pathRecordIds.get(pathKey) ?? []
          }
        })
      }
      await commitInChunks(items)
      if (skipped) addLog(`  ⚠️ recordings를 못 찾아 건너뛴 공격경로 ${skipped}건`)
    }

    // BAP — 이미 써놓은 RecordDoc에 bapReason만 병합한다
    if (p.ff_game_bap?.columns) {
      const rows = rowsToObjects(p.ff_game_bap)
      addLog(`BAP 표시 반영 중... (${rows.length}건)`)
      const items: { path: string; id: string; data: Record<string, unknown> }[] = []
      let skipped = 0
      for (const r of rows) {
        const rec = recordingByGiId.get(s(r.gi_id))
        if (!rec) { skipped++; continue }
        items.push({
          path: `matches/${rec.gmId}/recordings/${rec.side}/records`, id: s(r.gr_id),
          data: { bapReason: 'legacy-import' }
        })
      }
      await commitInChunks(items)
      addLog('  ※ bapReason엔 실제 사유(BapEvent.reason) 대신 "legacy-import"만 채웠다 — 레거시엔 BAP 여부만 있고 사유 분류가 없었다')
      if (skipped) addLog(`  ⚠️ recordings를 못 찾아 건너뛴 BAP ${skipped}건`)
    }

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
        <div class="title">SQL 데이터 이관 (임시)</div>
        <NuxtLink class="backBtn" to="/manage">← 데이터 관리로</NuxtLink>
      </div>

      <p class="note">
        mysqldump로 뜬 SQL 덤프를 붙여넣거나 파일로 올리면, 참조 데이터(팀/선수/리그/시즌/경기장/
        감독/스쿼드/팀별 시즌-리그) + 이적 계약 이력 + 경기 일정 + 팀별 기록 세션(KPI) + 선수별
        KPI + 원본 레코드 + 라인업(교체 포함) + 카드 + 공격경로 + BAP까지, didNew가 쓰는 레거시
        표 전부를 옮긴다. 평점 관련 필드(score/jmx/apx/tpx/fpx 등)는 레거시에 없으므로 전부
        null로 들어가고, jpd-rating API를 호출해야 채워진다. 감독 재임 이력과 로그인 계정
        (ff_user)은 구조상 옮길 수 없다 — 자세한 이유는 파일 상단 주석 참고. 컬럼 순서는 덤프 안
        CREATE TABLE 또는 INSERT의 명시 컬럼 목록에서만 읽으므로, <code>mysqldump</code>는 기본
        옵션(스키마 포함)으로 뜨면 된다.
      </p>

      <input type="file" accept=".sql,.txt" @change="onFile" />
      <textarea v-model="dumpText" class="dump" placeholder="여기에 SQL 덤프를 붙여넣으세요" />

      <div class="actions">
        <button :disabled="!dumpText || busy" @click="doParse">1. 파싱</button>
        <button :disabled="!parsed || busy" @click="runImport">2. Firestore에 쓰기</button>
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
.dump {
  flex: 1;
  min-height: 220px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 6px;
  color: #fff;
  font-family: monospace;
  font-size: 12px;
  padding: 10px;
  resize: none;
}
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
