import type { DidRecord } from '~/utils/didLogic'
import type { CardRecord } from '~/utils/card'
import type { FormationChange, MatchSnapshot, MatchState, MatchSquadPlayer } from '~/composables/useMatchState'
import { assignmentsFromLineup, lineupOrderForSlot } from '~/utils/formationLayout'

type Side = 'H' | 'A'

export interface InputPayload {
  gmId: string
  side: Side
  inputMode: '분석' | '실시간'
  fieldSide: 'left' | 'right'
  formationKey: string
  homeScore: number
  awayScore: number
  status: 'ready' | 'H1' | 'H1_done' | 'H2' | 'H2_done' | 'final'
  halves: { H1: { seconds: number }; H2: { seconds: number } }
  lineup: Array<Record<string, unknown>>
  records: Array<Record<string, unknown>>
  cards: Array<Record<string, unknown>>
  recorderLevel: 'basic' | 'advanced'
  matchSnapshot: MatchSnapshot | null
  formationChanges: FormationChange[]
}

interface StoredDraft {
  key: string
  clientState: MatchState
  payload: InputPayload
  updatedAt: number
  finalized?: boolean
}

const DB_NAME = 'jpd-did-input'
const STORE_NAME = 'match-drafts'
// Setup mutations are infrequent but must retain their user-action order. A
// slow first substitution request must never arrive after and replace a later
// substitution's complete lineup snapshot.
let setupWriteChain: Promise<unknown> = Promise.resolve()

function inputSide(game: MatchState): Side {
  return game.team === 'away' ? 'A' : 'H'
}

function payloadMatchesGame(game: MatchState, payload: InputPayload | undefined | null) {
  return Boolean(payload && payload.gmId === game.matchId && payload.side === inputSide(game))
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) request.result.createObjectStore(STORE_NAME, { keyPath: 'key' })
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function readLocal(key: string): Promise<StoredDraft | null> {
  if (!import.meta.client) return null
  const db = await openDatabase()
  return await new Promise((resolve, reject) => {
    const request = db.transaction(STORE_NAME, 'readonly').objectStore(STORE_NAME).get(key)
    request.onsuccess = () => resolve((request.result as StoredDraft | undefined) ?? null)
    request.onerror = () => reject(request.error)
  }).finally(() => db.close())
}

function toPlain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

async function writeLocal(draft: StoredDraft): Promise<void> {
  if (!import.meta.client) return
  // IndexedDB structured-clone은 Vue reactive Proxy를 저장할 수 없다.
  // 저장 경계에서 항상 plain JSON 객체로 바꿔 Proxy가 섞여 들어오는 경로를 차단한다.
  const storable = toPlain(draft)
  const db = await openDatabase()
  await new Promise<void>((resolve, reject) => {
    const request = db.transaction(STORE_NAME, 'readwrite').objectStore(STORE_NAME).put(storable)
    request.onsuccess = () => resolve()
    request.onerror = () => reject(request.error)
  }).finally(() => db.close())
}

async function removeLocal(key: string): Promise<void> {
  if (!import.meta.client) return
  const db = await openDatabase()
  await new Promise<void>((resolve, reject) => {
    const request = db.transaction(STORE_NAME, 'readwrite').objectStore(STORE_NAME).delete(key)
    request.onsuccess = () => resolve()
    request.onerror = () => reject(request.error)
  }).finally(() => db.close())
}

export function cloneState(game: MatchState): MatchState {
  return toPlain(game)
}

function assignedLineup(game: MatchState, squad: MatchSquadPlayer[]) {
  const playerById = new Map(squad.map(player => [player.playerId, player]))
  return Object.entries(game.assigned)
    .map(([slot, playerId]) => ({ slot, player: playerById.get(playerId) }))
    .filter((entry): entry is { slot: string; player: MatchSquadPlayer } => Boolean(entry.player))
    .map(({ slot, player }) => {
      const subOut = game.subs.find(sub => sub.outPlayer === player.playerId)
      const subIn = game.subs.find(sub => sub.inPlayer === player.playerId)
      return {
        playerId: player.playerId, slot, order: lineupOrderForSlot(game.formationKey, slot),
        type: slot.startsWith('b') ? 'BENCH' : 'START', no: player.no, name: player.name, pos: player.pos,
        inHalf: subIn?.half ?? (slot.startsWith('b') ? null : 'H1'),
        inSeconds: subIn?.seconds ?? (slot.startsWith('b') ? null : 0),
        outHalf: subOut?.half ?? null, outSeconds: subOut?.seconds ?? null,
      }
    })
}

function cardsFromPayload(cards: Array<Record<string, unknown>>): CardRecord[] {
  return cards.flatMap((card, index) => {
    const player = card.playerId
    const half = card.half
    const seconds = card.halfSeconds
    const type = card.card
    if (typeof player !== 'string' || (half !== 'H1' && half !== 'H2') || typeof seconds !== 'number' || (type !== 'Y' && type !== 'R')) return []
    const id = typeof card.id === 'string' && card.id ? card.id : `legacy-card:${player}:${half}:${seconds}:${type}:${index}`
    return [{ id, player, half, seconds, card: type }]
  })
}

export function payloadFromState(game: MatchState): InputPayload {
  const squad = game.team === 'away' ? game.squads.away : game.squads.home
  // matchSnapshot은 화면 복원용 보조 스냅샷이다. gmId가 있는 입력/Draft 저장 자체를
  // 이 값의 유무로 막지 않는다. 새로고침·직접 URL 진입처럼 메모리 상태가 비어도
  // 서버는 gmId 기준의 경기 문서를 사용할 수 있고, 로컬 Draft 역시 계속 보존되어야 한다.
  return {
    gmId: game.matchId,
    side: inputSide(game),
    inputMode: game.inputMode,
    fieldSide: game.side ?? 'left',
    formationKey: game.formationKey,
    homeScore: game.homeScore,
    awayScore: game.awayScore,
    status: game.halfStatus,
    halves: { H1: { seconds: game.h1Seconds }, H2: { seconds: game.h2Seconds } },
    lineup: assignedLineup(game, squad),
    records: game.records.map((record: DidRecord, index) => ({
      id: record.id, half: record.half ?? 'H1', halfSeconds: record.seconds, seq: record.seq ?? index,
      act: record.act, res: record.res, area: record.area, posX: record.posX ?? null, posY: record.posY ?? null,
      shootPosX: record.shootPosX ?? null, shootPosY: record.shootPosY ?? null,
      shootDspRange: record.shootDspRange ?? null, isShot: record.isShot ?? null, playerId: record.playerId ?? null,
    })),
    cards: game.cards.map(card => ({
      id: card.id, playerId: card.player,
      half: card.half, halfSeconds: card.seconds, card: card.card,
    })).filter(card => Boolean(card.playerId)),
    recorderLevel: game.recorderLevel,
    matchSnapshot: game.matchSnapshot ? toPlain(game.matchSnapshot) : null,
    formationChanges: toPlain(game.formationChanges),
  }
}

const HALF_ORDER: Record<string, number> = { H1: 1, H2: 2, H3: 3, H4: 4 }

/**
 * 서버 라인업은 선발 기준으로 돌아올 수 있어(레거시 스냅샷 등) 교체가 배치에 반영돼 있지 않을 수 있다.
 * 교체를 시간순으로 다시 적용하되, OUT 선수가 필드에·IN 선수가 벤치에 있을 때만 맞바꾼다 —
 * 이미 교체가 반영된 배치라면 조건이 맞지 않아 그대로 두므로 두 번 적용되지 않는다.
 */
function applySubsToAssigned(game: MatchState) {
  const subs = [...game.subs].sort(
    (a, b) => (HALF_ORDER[a.half] ?? 0) * 100000 + a.seconds - ((HALF_ORDER[b.half] ?? 0) * 100000 + b.seconds),
  )
  for (const sub of subs) {
    const entries = Object.entries(game.assigned)
    const outSlot = entries.find(([, playerId]) => playerId === sub.outPlayer)?.[0]
    const inSlot = entries.find(([, playerId]) => playerId === sub.inPlayer)?.[0]
    if (!outSlot || !inSlot || outSlot.startsWith('b') || !inSlot.startsWith('b')) continue
    game.assigned = { ...game.assigned, [outSlot]: sub.inPlayer, [inSlot]: sub.outPlayer }
  }
}

function hydrateFromPayload(game: MatchState, payload: InputPayload, clientState?: MatchState) {
  // Never trust an IndexedDB value or a malformed server response merely
  // because it was stored under the current key. The route identity is the
  // hard boundary between matches and teams.
  if (!payloadMatchesGame(game, payload)) return
  // 역할과 계정 등급은 현재 로그인/일정 진입 세션의 값이다. 다른 기기나 이전
  // 입력자가 남긴 Draft clientState를 복원하면서 이 권한 맥락을 덮으면 안 된다.
  const sessionIdentity = {
    // Team choice belongs to this URL/session. The other analyst's stored
    // clientState must not move an away analyst onto the home-side workspace.
    team: game.team,
    participantRole: game.participantRole,
    participantName: game.participantName,
    recorderLevel: game.recorderLevel,
  }
  if (clientState?.matchId) {
    // 이미 메모리에 살아 있는 명단/배치는 빈 Draft 값으로 덮어쓰지 않는다.
    // 진행 중 화면 → 대기방 이동에서 선수 목록이 사라지는 것을 막는다.
    const liveSquads = game.squads
    const liveAssigned = game.assigned
    const sameMatch = game.matchId === clientState.matchId

    Object.assign(game, clientState)
    Object.assign(game, sessionIdentity)

    if (sameMatch) {
      if (!game.squads.home.length && liveSquads.home.length) game.squads.home = liveSquads.home
      if (!game.squads.away.length && liveSquads.away.length) game.squads.away = liveSquads.away
      if (!Object.keys(game.assigned).length && Object.keys(liveAssigned).length) game.assigned = liveAssigned
    }

    // Drafts made before the playerId migration stored squad array indexes.
    // Convert them once while their original squad snapshot is still present.
    const squad = game.team === 'away' ? game.squads.away : game.squads.home
    const legacy = game.assigned as unknown as Record<string, string | number>
    game.assigned = Object.fromEntries(Object.entries(legacy).flatMap(([slot, player]) => {
      const playerId = typeof player === 'number' ? squad[player]?.playerId : player
      return playerId ? [[slot, playerId]] : []
    }))
    game.cards = game.cards.flatMap(card => {
      const legacyPlayer = card.player as unknown as string | number
      const playerId = typeof legacyPlayer === 'number' ? squad[legacyPlayer]?.playerId : legacyPlayer
      return playerId ? [{ ...card, player: playerId }] : []
    })
    game.subs = game.subs.flatMap(sub => {
      const outPlayer = typeof sub.outPlayer === 'number' ? squad[sub.outPlayer]?.playerId : sub.outPlayer
      const inPlayer = typeof sub.inPlayer === 'number' ? squad[sub.inPlayer]?.playerId : sub.inPlayer
      const id = typeof sub.id === 'string' && sub.id
        ? sub.id
        : `legacy-sub:${sub.half}:${sub.seconds}:${outPlayer}:${inPlayer}`
      return outPlayer && inPlayer ? [{ ...sub, id, outPlayer, inPlayer }] : []
    })
    // clientState used to preserve o0…o9, whose coordinate-array order starts
    // at the defensive row. The durable lineup order is now the only authority
    // when it is present, so old Drafts also reopen in screen reading order.
    if (payload.formationKey && payload.lineup.length) {
      game.formationKey = payload.formationKey
      game.assigned = assignmentsFromLineup(payload.formationKey, payload.lineup)
    }
    applySubsToAssigned(game)
    // clientState is only a UI recovery shell. Records and match lifecycle are
    // server-merged payload data, otherwise another analyst's old checkpoint
    // can make recently assigned players disappear after refresh/re-entry.
    game.homeScore = payload.homeScore
    game.awayScore = payload.awayScore
    game.h1Seconds = payload.halves.H1?.seconds ?? game.h1Seconds
    game.h2Seconds = payload.halves.H2?.seconds ?? game.h2Seconds
    game.halfStatus = payload.status
    game.records = payload.records.map(record => ({
      id: String(record.id), half: record.half as 'H1' | 'H2', seconds: Number(record.halfSeconds), seq: Number(record.seq),
      act: record.act as DidRecord['act'], res: record.res as DidRecord['res'], area: Number(record.area),
      posX: record.posX as number | undefined, posY: record.posY as number | undefined,
      shootPosX: record.shootPosX as number | undefined, shootPosY: record.shootPosY as number | undefined,
      shootDspRange: record.shootDspRange as boolean | undefined, isShot: record.isShot as boolean | undefined,
      playerId: record.playerId as string | undefined,
    }))
    // Cards are server-owned Draft data, not a browser-only UI scratchpad.
    // Without this assignment a lobby re-entry restored an older clientState
    // and made persisted yellow/red cards look as if they had disappeared.
    game.cards = cardsFromPayload(payload.cards)
    return
  }
  // Direct entries also set the requested team before hydration. Preserve it
  // instead of inferring it from a Draft/RAW payload made by another analyst.
  game.team = sessionIdentity.team || (payload.side === 'A' ? 'away' : 'home')
  game.inputMode = payload.inputMode
  game.side = payload.fieldSide
  game.formationKey = payload.formationKey
  game.matchSnapshot = payload.matchSnapshot ?? null
  game.formationChanges = payload.formationChanges ?? []
  game.homeScore = payload.homeScore
  game.awayScore = payload.awayScore
  game.h1Seconds = payload.halves.H1?.seconds ?? 0
  game.h2Seconds = payload.halves.H2?.seconds ?? 0
  game.seconds = game.h2Seconds
  // Final RAW, especially SQL-migrated matches, has no browser clientState.
  // Rebuild slots in visible formation order before the board renders.
  game.assigned = assignmentsFromLineup(payload.formationKey, payload.lineup)
  game.records = payload.records.map(record => ({
    id: String(record.id), half: record.half as 'H1' | 'H2', seconds: Number(record.halfSeconds), seq: Number(record.seq),
    act: record.act as DidRecord['act'], res: record.res as DidRecord['res'], area: Number(record.area),
    posX: record.posX as number | undefined, posY: record.posY as number | undefined,
    shootPosX: record.shootPosX as number | undefined, shootPosY: record.shootPosY as number | undefined,
    shootDspRange: record.shootDspRange as boolean | undefined, isShot: record.isShot as boolean | undefined,
    playerId: record.playerId as string | undefined,
  }))
  game.cards = cardsFromPayload(payload.cards)
  game.halfStatus = payload.status
  Object.assign(game, sessionIdentity)
}

export function useMatchDraft() {
  const { request } = useBackendApi()
  const keyFor = (game: MatchState) => `${game.matchId}_${inputSide(game)}`

  async function saveLocal(game: MatchState, finalized = false): Promise<void> {
    if (!game.matchId) return
    const payload = payloadFromState(game)
    const draft = toPlain<StoredDraft>({
      key: keyFor(game), payload, clientState: cloneState(game), updatedAt: Date.now(),
      ...(finalized ? { finalized: true } : {}),
    })
    await writeLocal(draft)
  }

  async function save(game: MatchState): Promise<boolean> {
    if (!game.matchId) return false
    const payload = payloadFromState(game)
    const draft = toPlain<StoredDraft>({ key: keyFor(game), payload, clientState: cloneState(game), updatedAt: Date.now() })
    await writeLocal(draft)
    try {
      await request(`/api/v1/match-input/drafts/${encodeURIComponent(payload.gmId)}/${payload.side}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload, clientState: draft.clientState }),
      })
      return true
    } catch {
      // IndexedDB is the offline retry source. A later explicit lifecycle action retries sync.
      return false
    }
  }

  /** Persist only durable lobby setup after a formation/lineup action.
   * It must never be used for records, timer ticks or lifecycle changes. */
  async function saveSetup(game: MatchState): Promise<boolean> {
    if (!game.matchId) return false
    const payload = payloadFromState(game)
    const clientState = cloneState(game)
    const send = async () => {
      await request(`/api/v1/match-input/drafts/${encodeURIComponent(payload.gmId)}/${payload.side}/setup`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload, clientState }),
      })
    }
    const pending = setupWriteChain.then(send, send)
    setupWriteChain = pending.catch(() => false)
    try {
      await pending
      return true
    } catch {
      return false
    }
  }

  async function promoteH1(game: MatchState) {
    if (!await save(game)) throw new Error('네트워크 연결 후 다시 시도하세요. Draft는 이 기기에 안전하게 저장되었습니다.')
    const payload = payloadFromState(game)
    return await request(`/api/v1/match-input/drafts/${encodeURIComponent(payload.gmId)}/${payload.side}/promote-h1`, { method: 'POST' })
  }

  async function finalizeAdvanced(game: MatchState) {
    if (!await save(game)) throw new Error('네트워크 연결 후 다시 시도하세요. Draft는 이 기기에 안전하게 저장되었습니다.')
    const payload = payloadFromState(game)
    const result = await request(`/api/v1/match-input/drafts/${encodeURIComponent(payload.gmId)}/${payload.side}/finalize`, { method: 'POST' })
    // Final promotion clears its server Draft. A later explicit edit recreates
    // a short-lived Draft from RAW, so final RAW remains the only source.
    await saveLocal(game, true)
    return result
  }

  async function restoreFinalRaw(game: MatchState) {
    const side = inputSide(game)
    const response = await request<{ payload: InputPayload; clientState: MatchState }>(
      `/api/v1/match-input/drafts/${encodeURIComponent(game.matchId)}/${side}/restore-raw`, { method: 'POST' },
    )
    hydrateFromPayload(game, response.payload, response.clientState)
    await writeLocal({ key: keyFor(game), payload: response.payload, clientState: cloneState(game), updatedAt: Date.now() })
  }

  async function recoverFinalRaw(game: MatchState) {
    if (!game.matchId) return false
    try {
      const side = inputSide(game)
      const response = await request<{ payload: InputPayload; clientState?: MatchState }>(
        `/api/v1/match-input/matches/${encodeURIComponent(game.matchId)}/recordings/${side}/input-state`,
      )
      if (!response.payload) return false
      hydrateFromPayload(game, response.payload, response.clientState)
      await writeLocal({ key: keyFor(game), payload: response.payload, clientState: cloneState(game), updatedAt: Date.now(), finalized: true })
      return true
    } catch {
      return false
    }
  }

  async function recoverLocal(game: MatchState) {
    const draft = await readLocal(keyFor(game))
    if (!draft || !payloadMatchesGame(game, draft.payload)) return false
    hydrateFromPayload(game, draft.payload, draft.clientState)
    return true
  }

  async function recover(game: MatchState) {
    if (!game.matchId) return false
    let local = await readLocal(keyFor(game))
    if (local && !payloadMatchesGame(game, local.payload)) {
      await removeLocal(keyFor(game))
      local = null
    }
    try {
      const side = inputSide(game)
      const response = await request<{ status?: string; payload?: InputPayload; clientState?: MatchState; updatedAt?: string }>(
        `/api/v1/match-input/drafts/${encodeURIComponent(game.matchId)}/${side}`,
      )
      if (response.status === 'missing' || !response.payload) {
        if (local) {
          hydrateFromPayload(game, local.payload, local.clientState)
          // The page can reload before the debounced shared-Draft write has
          // completed. Restore the local checkpoint and immediately put it
          // back on the server so other analysts/re-entries see the same state.
          void save(game)
          return true
        }
        return false
      }
      if (!payloadMatchesGame(game, response.payload)) return false
      const serverUpdatedAt = response.updatedAt ? Date.parse(response.updatedAt) : 0
      // A page reload can happen inside the network debounce window. In that
      // case this device's immediately-written IndexedDB snapshot is newer than
      // the server Draft and must be restored + retried, never discarded.
      if (local && local.updatedAt > serverUpdatedAt) {
        hydrateFromPayload(game, local.payload, local.clientState)
        void save(game)
        return true
      }
      hydrateFromPayload(game, response.payload, response.clientState)
      await writeLocal({ key: keyFor(game), payload: response.payload, clientState: cloneState(game), updatedAt: Date.now() })
      return true
    } catch {
      // IndexedDB is an offline fallback only. When the shared Draft is
      // reachable, its state must win over an older device-local snapshot.
      return local ? (hydrateFromPayload(game, local.payload, local.clientState), true) : false
    }
  }

  return { saveLocal, save, saveSetup, promoteH1, finalizeAdvanced, restoreFinalRaw, recoverFinalRaw, recover, hydrate: hydrateFromPayload }
}
