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

function cardFingerprint(card: CardRecord) {
  return JSON.stringify({ id: card.id, player: card.player, half: card.half, seconds: card.seconds, card: card.card })
}

function sortCards(cards: CardRecord[]) {
  return cards.sort((a, b) =>
    a.half.localeCompare(b.half) || a.seconds - b.seconds || a.id.localeCompare(b.id),
  )
}

type InputSub = NonNullable<DraftResponse['inputSetup']>['subs'][number]

function subFingerprint(sub: InputSub) {
  return JSON.stringify({
    id: sub.id, half: sub.half, seconds: sub.seconds,
    outPlayer: sub.outPlayer, inPlayer: sub.inPlayer,
  })
}

function sortSubs(subs: InputSub[]) {
  return subs.sort((a, b) =>
    a.half.localeCompare(b.half) || a.seconds - b.seconds || a.id.localeCompare(b.id),
  )
}

function sortRecords(records: DidRecord[]): DidRecord[] {
  return records.sort((a, b) =>
    ((a.half || 'H1').localeCompare(b.half || 'H1')) || a.seconds - b.seconds || (a.seq ?? 0) - (b.seq ?? 0),
  )
}

interface DraftResponse {
  status: string
  gmId: string
  side: 'H' | 'A'
  payload: InputPayload
  clientState: Partial<MatchState>
  sharedState?: Partial<MatchState>
  revision: number
  participantCount?: number
  inputSetup?: {
    formationKey: string
    fieldSide: 'left' | 'right' | null
    lineup: Array<Record<string, unknown>>
    lineupIsBaseline?: boolean
    subs: Array<{ id: string; half: 'H1' | 'H2'; seconds: number; outPlayer: string; inPlayer: string }>
    inputMode: '분석' | '실시간'
    revision: number
  } | null
}

type SyncScope = 'state' | 'records' | 'state_records' | 'cards'

/**
 * Browser code never writes Firestore. The backend owns the shared Draft and
 * record-level merge; IndexedDB remains the offline retry cache on each device.
 */
export function useMatchCollaboration() {
  const { request, openSocket } = useBackendApi()
  let pollTimer: ReturnType<typeof setInterval> | undefined
  let pollOnce: (() => Promise<void>) | undefined
  let closeSocket: (() => void) | undefined
  let sendSocket: ((data: unknown) => void) | undefined
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let reconnectAttempts = 0
  let consecutiveSocketAckTimeouts = 0
  let pendingSocketCommands = new Map<string, (saved: boolean) => void>()
  let stopped = true
  // stop() can run while start() is still awaiting auth or its first read
  // (the screen was left early). Each start owns one session number, and a
  // stale start must never arm a timer that nothing will clear again.
  let session = 0
  let polling = false
  let latestRevision = 0
  // A poll can return before this browser's queued write reaches the server.
  // These maps make the record stream an optimistic merge, never a whole-list
  // replacement that briefly removes a just-entered event from the screen.
  let confirmedRecordFingerprints = new Map<string, string>()
  let pendingRecordFingerprints = new Map<string, string>()
  let pendingDeletedRecordIds = new Set<string>()
  // Cards and substitutions are independent shared events. Never use a
  // whole-list fingerprint here: two analysts can legitimately save A and B
  // at the same time, and the server's [A, B] response must be accepted by
  // both browsers immediately.
  let confirmedCardFingerprints = new Map<string, string>()
  let pendingCardFingerprints = new Map<string, string>()
  let pendingDeletedCardIds = new Set<string>()
  let confirmedSubFingerprints = new Map<string, string>()
  let pendingSubFingerprints = new Map<string, string>()
  let pendingDeletedSubIds = new Set<string>()
  let setupWriteChain: Promise<unknown> = Promise.resolve()

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

  // Polling is a recovery read only. A healthy socket is the collaboration
  // transport; this timer starts only while the socket is unavailable.
  function followParticipants(count: number | undefined) {
    if (stopped || sendSocket || pollTimer || !pollOnce || (count ?? 0) < 2) return
    const run = pollOnce
    pollTimer = setInterval(() => { void run() }, 2_000)
  }

  function stop() {
    stopped = true
    session += 1
    pollOnce = undefined
    latestRevision = 0
    confirmedRecordFingerprints = new Map()
    pendingRecordFingerprints = new Map()
    pendingDeletedRecordIds = new Set()
    confirmedCardFingerprints = new Map()
    pendingCardFingerprints = new Map()
    pendingDeletedCardIds = new Set()
    confirmedSubFingerprints = new Map()
    pendingSubFingerprints = new Map()
    pendingDeletedSubIds = new Set()
    setupWriteChain = Promise.resolve()
    closeSocket?.()
    closeSocket = undefined
    sendSocket = undefined
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = undefined
    reconnectAttempts = 0
    consecutiveSocketAckTimeouts = 0
    for (const resolve of pendingSocketCommands.values()) resolve(false)
    pendingSocketCommands = new Map()
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

    const observeServerCards = (cards: CardRecord[]) => {
      const server = new Map(cards.map(card => [card.id, cardFingerprint(card)]))
      for (const [id, fingerprint] of server) {
        if (pendingCardFingerprints.get(id) !== fingerprint) confirmedCardFingerprints.set(id, fingerprint)
      }
      for (const [id, fingerprint] of pendingCardFingerprints) {
        if (server.get(id) === fingerprint) {
          pendingCardFingerprints.delete(id)
          confirmedCardFingerprints.set(id, fingerprint)
        }
      }
      for (const id of pendingDeletedCardIds) {
        if (!server.has(id)) {
          pendingDeletedCardIds.delete(id)
          confirmedCardFingerprints.delete(id)
        }
      }
    }

    const applyCards = (cards: CardRecord[]) => {
      observeServerCards(cards)
      const merged = new Map(cards.map(card => [card.id, card]))
      for (const id of pendingDeletedCardIds) merged.delete(id)
      for (const card of game.cards) {
        if (pendingCardFingerprints.get(card.id) === cardFingerprint(card)) merged.set(card.id, card)
      }
      handlers.applyCards?.(sortCards([...merged.values()]))
    }

    const applyDraftResponse = (response: DraftResponse) => {
      // Socket fan-out is keyed by these values, but assert them again here so
      // no stale/corrupt payload can ever cross a browser session boundary.
      if (response.gmId !== gmId || response.side !== side || response.payload.gmId !== gmId || response.payload.side !== side) return
      if (response.status !== 'ok' || stopped || response.revision < latestRevision) return
      latestRevision = response.revision
      followParticipants(response.participantCount)
      if (response.sharedState && Object.keys(response.sharedState).length) handlers.applyState(plain(response.sharedState))
      if (response.inputSetup) applySetup(response.inputSetup)
      const records = sortRecords(response.payload.records.map(recordFromPayload))
      observeServerRecords(records)
      handlers.applyRecords(records)
      applyCards(cardsFromPayload(response.payload.cards))
    }

    const applySetup = (setup: NonNullable<DraftResponse['inputSetup']>) => {
      const server = new Map(setup.subs.map(sub => [sub.id, subFingerprint(sub)]))
      for (const [id, fingerprint] of server) {
        if (pendingSubFingerprints.get(id) !== fingerprint) confirmedSubFingerprints.set(id, fingerprint)
      }
      for (const [id, fingerprint] of pendingSubFingerprints) {
        if (server.get(id) === fingerprint) {
          pendingSubFingerprints.delete(id)
          confirmedSubFingerprints.set(id, fingerprint)
        }
      }
      for (const id of pendingDeletedSubIds) {
        if (!server.has(id)) {
          pendingDeletedSubIds.delete(id)
          confirmedSubFingerprints.delete(id)
        }
      }
      const merged = new Map(setup.subs.map(sub => [sub.id, sub]))
      for (const id of pendingDeletedSubIds) merged.delete(id)
      for (const sub of game.subs) {
        if (pendingSubFingerprints.get(sub.id) === subFingerprint(sub)) merged.set(sub.id, sub)
      }
      handlers.applySetup?.({ ...setup, subs: sortSubs([...merged.values()]) })
    }

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
        applyDraftResponse(response)
      } catch {
        // Offline is normal. IndexedDB preserves the next retry checkpoint.
      } finally {
        polling = false
      }
    }
    pollOnce = poll
    const connectSocket = async () => {
      try {
        const socket = await openSocket<{
        type: 'draft' | 'state' | 'cards' | 'setup' | 'mutation' | 'error'
        gmId?: string
        side?: 'H' | 'A'
        commandId?: string
        event?: {
          type: 'draft' | 'state' | 'cards' | 'setup'
          gmId?: string
          side?: 'H' | 'A'
          response?: DraftResponse
          setup?: NonNullable<DraftResponse['inputSetup']>
          sharedState?: Partial<MatchState>
          cards?: Array<Record<string, unknown>>
          revision?: number
        }
        response?: DraftResponse
        setup?: NonNullable<DraftResponse['inputSetup']>
        sharedState?: Partial<MatchState>
        cards?: Array<Record<string, unknown>>
        revision?: number
        }>(`/api/v1/match-input/drafts/${encodeURIComponent(gmId)}/${side}/live`, (message) => {
        if (stopped || current !== session) return
        const event = message.type === 'mutation' ? message.event : message
        if (event?.type === 'draft' && event.response) {
          applyDraftResponse(event.response)
          acknowledgeServerRecords(sortRecords(event.response.payload.records.map(recordFromPayload)))
        }
        if (event?.type === 'state' && event.gmId === gmId && event.side === side && event.sharedState) {
          latestRevision = Math.max(latestRevision, event.revision ?? 0)
          handlers.applyState(plain(event.sharedState))
        }
        if (event?.type === 'cards' && event.gmId === gmId && event.side === side && event.cards) {
          latestRevision = Math.max(latestRevision, event.revision ?? 0)
          applyCards(cardsFromPayload(event.cards))
        }
        if (event?.type === 'setup' && event.gmId === gmId && event.side === side && event.setup) applySetup(event.setup)
        if (message.commandId) {
          const resolve = pendingSocketCommands.get(message.commandId)
          if (resolve) {
            pendingSocketCommands.delete(message.commandId)
            resolve(message.type !== 'error')
          }
        }
        }, () => {
          if (stopped || current !== session) return
          closeSocket = undefined
          sendSocket = undefined
          followParticipants(2)
          const delay = Math.min(4_000, 250 * 2 ** reconnectAttempts++)
          reconnectTimer = setTimeout(() => { void connectSocket() }, delay)
        })
        if (current === session && !stopped) {
          closeSocket = socket.close
          sendSocket = socket.send
          reconnectAttempts = 0
          consecutiveSocketAckTimeouts = 0
          if (pollTimer) clearInterval(pollTimer)
          pollTimer = undefined
        } else socket.close()
      } catch {
        if (stopped || current !== session) return
        followParticipants(2)
        const delay = Math.min(4_000, 250 * 2 ** reconnectAttempts++)
        reconnectTimer = setTimeout(() => { void connectSocket() }, delay)
      }
    }
    // Start the live channel immediately, but do not report this screen ready
    // until one durable Draft read has applied. This prevents a refresh from
    // enabling a primary's timer against an empty local state while the old
    // session is still being recovered.
    void connectSocket()
    await poll()
    return current === session
  }

  async function sync(game: MatchState, syncScope: SyncScope, deletedRecordIds: string[] = []) {
    const { $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    // Capture each edit at call time, then send mutations in that exact order.
    // State and records used to race each other and a late whole-Draft write
    // could replace a freshly assigned player after navigation/refresh.
    const payload = plain(payloadFromState(game))
    const clientState = cloneState(game)
    const writesRecords = syncScope === 'records' || syncScope === 'state_records'
    const sentRecords = writesRecords
      ? sortRecords(payload.records.map(recordFromPayload))
      : []
    if (writesRecords) {
      markPendingRecords(sentRecords)
      for (const recordId of deletedRecordIds) {
        pendingRecordFingerprints.delete(recordId)
        pendingDeletedRecordIds.add(recordId)
      }
    }
    // A live edit sends only its changed records. The previous implementation
    // resent the complete table for every ACT and made timer/card commands wait
    // behind an ever-growing Firestore batch.
    if (syncScope === 'state_records') {
      payload.records = payload.records.filter(record => pendingRecordFingerprints.has(String(record.id)))
    }
    const deletedCardIds = syncScope === 'cards'
      ? [...confirmedCardFingerprints.keys()].filter(id => !game.cards.some(card => card.id === id))
      : []
    if (syncScope === 'cards') {
      for (const card of game.cards) {
        const fingerprint = cardFingerprint(card)
        if (confirmedCardFingerprints.get(card.id) !== fingerprint) pendingCardFingerprints.set(card.id, fingerprint)
        pendingDeletedCardIds.delete(card.id)
      }
      for (const id of deletedCardIds) {
        pendingCardFingerprints.delete(id)
        pendingDeletedCardIds.add(id)
      }
    }
    const send = async () => {
      if (sendSocket) {
        const saved = await sendSocketMutation('draft', { payload, clientState, syncScope, deletedRecordIds, deletedCardIds })
        if (saved) return true
      }
      const response = await request<DraftResponse>(`/api/v1/match-input/drafts/${encodeURIComponent(payload.gmId)}/${payload.side}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload, clientState, syncScope, deletedRecordIds, deletedCardIds }),
      })
      latestRevision = Math.max(latestRevision, response.revision)
      // A solo primary does not poll; its own saves reveal a newly joined analyst.
      followParticipants(response.participantCount)
      if (writesRecords) acknowledgeServerRecords(sortRecords(response.payload.records.map(recordFromPayload)))
      if (syncScope === 'cards') {
        const savedCards = cardsFromPayload(response.payload.cards)
        for (const card of savedCards) {
          const fingerprint = cardFingerprint(card)
          if (pendingCardFingerprints.get(card.id) === fingerprint) pendingCardFingerprints.delete(card.id)
          confirmedCardFingerprints.set(card.id, fingerprint)
        }
        for (const id of pendingDeletedCardIds) {
          if (!savedCards.some(card => card.id === id)) {
            pendingDeletedCardIds.delete(id)
            confirmedCardFingerprints.delete(id)
          }
        }
      }
      return true
    }
    // Server-side per-Draft locking establishes revision order. Do not keep a
    // browser-wide serial queue here: an urgent pause command must never wait
    // for an older record/card request to finish.
    return await send()
  }

  async function syncState(game: MatchState) {
    return await sync(game, 'state')
  }

  async function syncRecords(game: MatchState, records: DidRecord[]) {
    game.records = records
    return await sync(game, 'records')
  }

  async function syncStateRecords(game: MatchState, records: DidRecord[]) {
    game.records = records
    return await sync(game, 'state_records')
  }

  async function removeRecord(game: MatchState, recordId: string) {
    return await sync(game, 'records', [recordId])
  }

  async function syncCards(game: MatchState) {
    return await sync(game, 'cards')
  }

  async function syncSetup(game: MatchState, syncScope: 'setup' | 'substitutions' = 'setup') {
    const { $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    const payload = plain(payloadFromState(game))
    const clientState = cloneState(game)
    const deletedSubIds = [...confirmedSubFingerprints.keys()].filter(id => !game.subs.some(sub => sub.id === id))
    if (syncScope === 'substitutions') {
      for (const sub of game.subs) {
        const fingerprint = subFingerprint(sub)
        if (confirmedSubFingerprints.get(sub.id) !== fingerprint) pendingSubFingerprints.set(sub.id, fingerprint)
        pendingDeletedSubIds.delete(sub.id)
      }
      for (const id of deletedSubIds) {
        pendingSubFingerprints.delete(id)
        pendingDeletedSubIds.add(id)
      }
    }
    const send = async () => {
      if (sendSocket) {
        const saved = await sendSocketMutation('setup', { payload, clientState, deletedSubIds, syncScope })
        if (saved) return true
      }
      const response = await request<{ inputSetup?: NonNullable<DraftResponse['inputSetup']> }>(`/api/v1/match-input/drafts/${encodeURIComponent(payload.gmId)}/${payload.side}/setup`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload, clientState, deletedSubIds, syncScope }),
      })
      if (response.inputSetup) {
        for (const sub of response.inputSetup.subs) {
          const fingerprint = subFingerprint(sub)
          if (pendingSubFingerprints.get(sub.id) === fingerprint) pendingSubFingerprints.delete(sub.id)
          confirmedSubFingerprints.set(sub.id, fingerprint)
        }
        for (const id of pendingDeletedSubIds) {
          if (!response.inputSetup.subs.some(sub => sub.id === id)) {
            pendingDeletedSubIds.delete(id)
            confirmedSubFingerprints.delete(id)
          }
        }
      }
      return true
    }
    // Setup is primary-owned and snapshots the whole lineup/substitution
    // configuration. Keep only setup mutations ordered so an older snapshot
    // cannot arrive after a newer substitution and erase it.
    const pending = setupWriteChain.then(send, send)
    setupWriteChain = pending.catch(() => false)
    return await pending
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
    for (const card of game.cards) {
      const fingerprint = cardFingerprint(card)
      if (confirmedCardFingerprints.get(card.id) !== fingerprint) pendingCardFingerprints.set(card.id, fingerprint)
      pendingDeletedCardIds.delete(card.id)
    }
  }

  async function sendSocketMutation(kind: 'draft' | 'setup', request: Record<string, unknown>) {
    const send = sendSocket
    const close = closeSocket
    if (!send) return false
    const commandId = crypto.randomUUID()
    return await new Promise<boolean>((resolve) => {
      const fail = () => {
        pendingSocketCommands.delete(commandId)
        // Firestore writes for cards/setup can legitimately take longer than
        // a record write. One late ACK is not a dead socket. The old 1-second
        // rule forcibly closed a healthy connection during card input, then
        // made every following ACT wait for reconnection.
        consecutiveSocketAckTimeouts += 1
        if (consecutiveSocketAckTimeouts >= 2 && sendSocket === send) {
          sendSocket = undefined
          closeSocket = undefined
          close?.()
        }
        resolve(false)
      }
      const timeout = window.setTimeout(fail, 4_000)
      pendingSocketCommands.set(commandId, (result) => {
        window.clearTimeout(timeout)
        pendingSocketCommands.delete(commandId)
        if (result) consecutiveSocketAckTimeouts = 0
        resolve(result)
      })
      try {
        send({ type: 'command', commandId, command: 'mutation', mutation: kind, request })
      } catch {
        window.clearTimeout(timeout)
        fail()
      }
    })
  }

  return { join, start, stop, syncState, syncRecords, syncStateRecords, syncCards, syncSetup, removeRecord, mergeRemoteRecords, markLocalRecords, markLocalCards }
}
