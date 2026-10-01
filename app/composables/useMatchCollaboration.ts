import type { DidRecord } from '~/utils/didLogic'
import type { MatchState } from '~/composables/useMatchState'
import type { CardRecord } from '~/utils/card'
import { cloneState, payloadFromState, type InputPayload } from '~/composables/useMatchDraft'

type ParticipantRole = 'primary' | 'assistant' | 'manager'

function sideFor(game: MatchState) {
  return game.team === 'away' ? 'A' : 'H'
}

function plain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function numberOrUndefined(value: unknown) {
  return typeof value === 'number' ? value : undefined
}

function booleanOrUndefined(value: unknown) {
  return typeof value === 'boolean' ? value : undefined
}

function recordFromPayload(record: Record<string, unknown>): DidRecord {
  return {
    id: String(record.id), half: record.half as 'H1' | 'H2', seconds: Number(record.halfSeconds), seq: Number(record.seq),
    act: record.act as DidRecord['act'], res: record.res as DidRecord['res'], area: Number(record.area),
    posX: numberOrUndefined(record.posX), posY: numberOrUndefined(record.posY),
    shootPosX: numberOrUndefined(record.shootPosX), shootPosY: numberOrUndefined(record.shootPosY),
    shootDspRange: booleanOrUndefined(record.shootDspRange), isShot: booleanOrUndefined(record.isShot),
    playerId: typeof record.playerId === 'string' ? record.playerId : undefined,
  }
}

function recordFingerprint(record: DidRecord): string {
  // Keep this limited to the persisted record shape. UI-only reactive fields
  // must never make an already acknowledged event look pending forever.
  return JSON.stringify({
    id: record.id, half: record.half, seconds: record.seconds, seq: record.seq,
    act: record.act, res: record.res, area: record.area,
    posX: record.posX, posY: record.posY,
    shootPosX: record.shootPosX, shootPosY: record.shootPosY,
    shootDspRange: record.shootDspRange, isShot: record.isShot,
    playerId: record.playerId,
  })
}

function cardsFromPayload(cards: Array<Record<string, unknown>>): CardRecord[] {
  return cards.flatMap(card => {
    const player = card.playerId
    const half = card.half
    const seconds = card.halfSeconds
    const type = card.card
    if (typeof player !== 'string' || (half !== 'H1' && half !== 'H2') || typeof seconds !== 'number' || (type !== 'Y' && type !== 'R')) return []
    return [{ player, half, seconds, card: type }]
  })
}

function cardsFingerprint(cards: CardRecord[]) {
  return JSON.stringify([...cards]
    .map(card => ({ player: card.player, half: card.half, seconds: card.seconds, card: card.card }))
    .sort((a, b) => `${a.half}:${a.seconds}:${a.player}:${a.card}`.localeCompare(`${b.half}:${b.seconds}:${b.player}:${b.card}`)))
}

function sortRecords(records: DidRecord[]): DidRecord[] {
  return records.sort((a, b) =>
    ((a.half || 'H1').localeCompare(b.half || 'H1')) || a.seconds - b.seconds || (a.seq ?? 0) - (b.seq ?? 0),
  )
}

interface DraftResponse {
  status: string
  payload: InputPayload
  clientState: Partial<MatchState>
  sharedState?: Partial<MatchState>
  revision: number
  participantCount?: number
  inputSetup?: {
    formationKey: string
    fieldSide: 'left' | 'right' | null
    lineup: Array<Record<string, unknown>>
    subs: Array<{ half: 'H1' | 'H2'; seconds: number; outPlayer: string; inPlayer: string }>
    inputMode: '분석' | '실시간'
    revision: number
  } | null
}

/**
 * Browser code never writes Firestore. The backend owns the shared Draft and
 * record-level merge; IndexedDB remains the offline retry cache on each device.
 */
export function useMatchCollaboration() {
  const { request } = useBackendApi()
  let pollTimer: ReturnType<typeof setInterval> | undefined
  let pollOnce: (() => Promise<void>) | undefined
  let stopped = true
  // stop() can run while start() is still awaiting auth or its first read
  // (the screen was left early). Each start owns one session number, and a
  // stale start must never arm a timer that nothing will clear again.
  let session = 0
  let polling = false
  let latestRevision = 0
  let writeChain: Promise<unknown> = Promise.resolve()
  // A poll can return before this browser's queued write reaches the server.
  // These maps make the record stream an optimistic merge, never a whole-list
  // replacement that briefly removes a just-entered event from the screen.
  let confirmedRecordFingerprints = new Map<string, string>()
  let pendingRecordFingerprints = new Map<string, string>()
  let pendingDeletedRecordIds = new Set<string>()
  let pendingCardsFingerprint: string | undefined

  function markPendingRecords(records: DidRecord[]) {
    for (const record of records) {
      const fingerprint = recordFingerprint(record)
      if (confirmedRecordFingerprints.get(record.id) !== fingerprint) {
        pendingRecordFingerprints.set(record.id, fingerprint)
      }
      pendingDeletedRecordIds.delete(record.id)
    }
  }

  function observeServerRecords(records: DidRecord[]) {
    for (const record of records) {
      const fingerprint = recordFingerprint(record)
      // An older poll must not acknowledge a newer local edit for this ID.
      if (pendingRecordFingerprints.get(record.id) !== fingerprint) {
        confirmedRecordFingerprints.set(record.id, fingerprint)
      }
    }
  }

  function acknowledgeServerRecords(records: DidRecord[]) {
    const responseById = new Map(records.map(record => [record.id, recordFingerprint(record)]))
    confirmedRecordFingerprints = responseById
    for (const [recordId, fingerprint] of pendingRecordFingerprints) {
      if (responseById.get(recordId) === fingerprint) pendingRecordFingerprints.delete(recordId)
    }
    for (const recordId of pendingDeletedRecordIds) {
      if (!responseById.has(recordId)) pendingDeletedRecordIds.delete(recordId)
    }
  }

  function mergeRemoteRecords(remoteRecords: DidRecord[], localRecords: DidRecord[]): DidRecord[] {
    const merged = new Map(remoteRecords.map(record => [record.id, record]))
    for (const recordId of pendingDeletedRecordIds) merged.delete(recordId)
    for (const record of localRecords) {
      if (pendingRecordFingerprints.get(record.id) === recordFingerprint(record)) {
        merged.set(record.id, record)
      }
    }
    return sortRecords([...merged.values()])
  }

  async function currentIdentity() {
    const { $auth, $authReady } = useNuxtApp()
    await $authReady
    return $auth.currentUser
  }

  async function join(game: MatchState, role: ParticipantRole) {
    const user = await currentIdentity()
    const response = await request<{ role?: ParticipantRole; control?: boolean; participants: Record<string, { name?: string; role?: ParticipantRole }> }>(
      `/api/v1/match-input/drafts/${encodeURIComponent(game.matchId)}/${sideFor(game)}/participants`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, displayName: user?.displayName || user?.email || undefined }) },
    )
    game.participantRole = response.role ?? role
    game.lifecycleControl = response.control ?? game.participantRole === 'primary'
    game.participantName = user?.displayName || user?.email || ''
    return response
  }

  // A solo analyst has nothing to receive. Repeated reads start only once the
  // server reports another participant on this Draft.
  function followParticipants(count: number | undefined) {
    if (stopped || pollTimer || !pollOnce || (count ?? 0) < 2) return
    const run = pollOnce
    pollTimer = setInterval(() => { void run() }, 400)
  }

  function stop() {
    stopped = true
    session += 1
    pollOnce = undefined
    latestRevision = 0
    writeChain = Promise.resolve()
    confirmedRecordFingerprints = new Map()
    pendingRecordFingerprints = new Map()
    pendingDeletedRecordIds = new Set()
    pendingCardsFingerprint = undefined
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = undefined
  }

  async function start(game: MatchState, handlers: {
    applyState: (state: Partial<MatchState>) => void
    applyRecords: (records: DidRecord[]) => void
    applyCards?: (cards: CardRecord[]) => void
    applySetup?: (setup: NonNullable<DraftResponse['inputSetup']>) => void
  }) {
    stop()
    const current = session
    const { $auth, $authReady } = useNuxtApp()
    await $authReady
    if (current !== session || !$auth.currentUser || !game.matchId) return false
    stopped = false
    // Fix the target at start. The shared game object can later hold another
    // match; this session must keep reading only the Draft it was started for.
    const gmId = game.matchId
    const side = sideFor(game)
    // Direct re-entry can begin with an IndexedDB checkpoint before the first
    // server poll. Protect it until its idempotent record sync is acknowledged.
    markPendingRecords(game.records)

    const poll = async () => {
      if (stopped || polling || current !== session) return
      polling = true
      try {
        const response = await request<DraftResponse>(
          `/api/v1/match-input/drafts/${encodeURIComponent(gmId)}/${side}`,
        )
        if (current !== session) return
        if (response.status === 'missing') {
          // Final promotion deletes the Draft. Check final RAW once so an
          // assistant that polls just after deletion still leaves this screen.
          try {
            const raw = await request<{ payload?: InputPayload }>(
              `/api/v1/match-input/matches/${encodeURIComponent(gmId)}/recordings/${side}/input-state`,
            )
            if (current === session && raw.payload?.status === 'final') handlers.applyState({ halfStatus: 'final', clockStartedAt: null })
          } catch {
            // A missing Draft before the first input session is normal.
          }
          return
        }
        if (response.status !== 'ok' || stopped || response.revision < latestRevision) return
        latestRevision = response.revision
        followParticipants(response.participantCount)
        if (response.sharedState && Object.keys(response.sharedState).length) handlers.applyState(plain(response.sharedState))
        if (response.inputSetup) handlers.applySetup?.(response.inputSetup)
        const records = sortRecords(response.payload.records.map(recordFromPayload))
        observeServerRecords(records)
        handlers.applyRecords(records)
        const cards = cardsFromPayload(response.payload.cards)
        const fingerprint = cardsFingerprint(cards)
        if (pendingCardsFingerprint === undefined || pendingCardsFingerprint === fingerprint) {
          pendingCardsFingerprint = undefined
          handlers.applyCards?.(cards)
        }
      } catch {
        // Offline is normal. IndexedDB preserves the next retry checkpoint.
      } finally {
        polling = false
      }
    }
    pollOnce = poll
    // One read on entry restores the shared Draft. The 400ms loop (single-flight,
    // so a slower backend never stacks reads) starts only once another analyst
    // shares it — reported by this read or by a later save response.
    await poll()
    return current === session
  }

  async function sync(game: MatchState, syncScope: 'state' | 'records' | 'cards', deletedRecordIds: string[] = []) {
    const { $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    // Capture each edit at call time, then send mutations in that exact order.
    // State and records used to race each other and a late whole-Draft write
    // could replace a freshly assigned player after navigation/refresh.
    const payload = plain(payloadFromState(game))
    const clientState = cloneState(game)
    const sentRecords = syncScope === 'records'
      ? sortRecords(payload.records.map(recordFromPayload))
      : []
    if (syncScope === 'records') {
      markPendingRecords(sentRecords)
      for (const recordId of deletedRecordIds) {
        pendingRecordFingerprints.delete(recordId)
        pendingDeletedRecordIds.add(recordId)
      }
    }
    if (syncScope === 'cards') pendingCardsFingerprint = cardsFingerprint(game.cards)
    const send = async () => {
      const response = await request<DraftResponse>(`/api/v1/match-input/drafts/${encodeURIComponent(payload.gmId)}/${payload.side}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload, clientState, syncScope, deletedRecordIds }),
      })
      latestRevision = Math.max(latestRevision, response.revision)
      // A solo primary does not poll; its own saves reveal a newly joined analyst.
      followParticipants(response.participantCount)
      if (syncScope === 'records') acknowledgeServerRecords(sortRecords(response.payload.records.map(recordFromPayload)))
      if (syncScope === 'cards' && cardsFingerprint(cardsFromPayload(response.payload.cards)) === pendingCardsFingerprint) pendingCardsFingerprint = undefined
      return true
    }
    const pending = writeChain.then(send, send)
    writeChain = pending.catch(() => false)
    return await pending
  }

  async function syncState(game: MatchState) {
    return await sync(game, 'state')
  }

  async function syncRecords(game: MatchState, records: DidRecord[]) {
    game.records = records
    return await sync(game, 'records')
  }

  async function removeRecord(game: MatchState, recordId: string) {
    return await sync(game, 'records', [recordId])
  }

  async function syncCards(game: MatchState) {
    return await sync(game, 'cards')
  }

  // The screen debounces its network write, but polling runs every 400ms.
  // Protect a local edit from the moment it is made; otherwise a poll inside
  // the debounce window replaces it with the older server list.
  function markLocalRecords(records: DidRecord[]) {
    markPendingRecords(records)
  }

  // Call only when this device actually changed its cards. Re-marking on every
  // record save made a stale screen ignore the server and keep rewriting its
  // old card list, so two screens overwrote each other every second.
  function markLocalCards(game: MatchState) {
    pendingCardsFingerprint = cardsFingerprint(game.cards)
  }

  return { join, start, stop, syncState, syncRecords, syncCards, removeRecord, mergeRemoteRecords, markLocalRecords, markLocalCards }
}
