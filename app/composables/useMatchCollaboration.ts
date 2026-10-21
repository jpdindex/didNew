import type { DidRecord } from '~/utils/didLogic'
import type { MatchState, MatchSquadPlayer } from '~/composables/useMatchState'
import type { CardRecord } from '~/utils/card'
import { payloadFromState, type InputPayload } from '~/composables/useMatchDraft'
import { assignmentsFromLineup } from '~/utils/formationLayout'

type ParticipantRole = 'primary' | 'assistant' | 'manager'

function sideFor(game: MatchState) {
  return game.team === 'away' ? 'A' : 'H'
}

function plain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function liveState(value: Partial<MatchState>): Partial<MatchState> {
  const keys = new Set(['halfStatus', 'seconds', 'h1Seconds', 'h2Seconds', 'clockStartedAt', 'homeScore', 'awayScore'])
  return Object.fromEntries(Object.entries(value).filter(([key]) => keys.has(key)))
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

function recordsToPayload(records: DidRecord[]) {
  return records.map((record, index) => ({
    id: record.id, half: record.half ?? 'H1', halfSeconds: record.seconds, seq: record.seq ?? index,
    act: record.act, res: record.res, area: record.area, posX: record.posX ?? null, posY: record.posY ?? null,
    shootPosX: record.shootPosX ?? null, shootPosY: record.shootPosY ?? null,
    shootDspRange: record.shootDspRange ?? null, isShot: record.isShot ?? null, playerId: record.playerId ?? null,
  }))
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

function setupFingerprint(setup: Pick<NonNullable<DraftResponse['inputSetup']>, 'formationKey' | 'fieldSide' | 'lineup' | 'subs' | 'inputMode'>) {
  return JSON.stringify({
    formationKey: setup.formationKey,
    fieldSide: setup.fieldSide,
    inputMode: setup.inputMode,
    lineup: [...setup.lineup].map(item => ({
      playerId: item.playerId, slot: item.slot, order: item.order, type: item.type,
      inHalf: item.inHalf ?? null, inSeconds: item.inSeconds ?? null,
      outHalf: item.outHalf ?? null, outSeconds: item.outSeconds ?? null,
    })).sort((a, b) => String(a.slot).localeCompare(String(b.slot))),
    subs: setup.subs.map(sub => ({ id: sub.id, half: sub.half, seconds: sub.seconds,
      outPlayer: sub.outPlayer, inPlayer: sub.inPlayer,
    })).sort((a, b) => `${a.half}:${a.seconds}:${a.outPlayer}:${a.inPlayer}:${a.id}`.localeCompare(`${b.half}:${b.seconds}:${b.outPlayer}:${b.inPlayer}:${b.id}`)),
  })
}

function setupFingerprintFromPayload(payload: InputPayload, subs: InputSub[]) {
  return setupFingerprint({
    formationKey: payload.formationKey,
    fieldSide: payload.fieldSide,
    inputMode: payload.inputMode,
    lineup: payload.lineup,
    subs,
  })
}

function sortRecords(records: DidRecord[]): DidRecord[] {
  return records.sort((a, b) =>
    ((a.half || 'H1').localeCompare(b.half || 'H1')) || a.seconds - b.seconds || (a.seq ?? 0) - (b.seq ?? 0),
  )
}

export interface DraftResponse {
  status: string
  gmId: string
  side: 'H' | 'A'
  payload: InputPayload
  clientState: Partial<MatchState>
  sharedState?: Partial<MatchState>
  revision: number
  roomEpoch?: number
  participantCount?: number
  inputSetup?: {
    formationKey: string
    fieldSide: 'left' | 'right' | null
    lineup: Array<Record<string, unknown>>
    lineupIsBaseline?: boolean
    subs: Array<{ id: string; half: 'H1' | 'H2'; seconds: number; outPlayer: string; inPlayer: string }>
    inputMode: '분석' | '실시간'
    revision: number
    forceApply?: boolean
  } | null
}

export interface DraftEnterResponse {
  status: 'ok'
  gmId: string
  side: 'H' | 'A'
  participant: {
    role?: ParticipantRole
    control?: boolean
    participants: Record<string, { name?: string; role?: ParticipantRole }>
    primaryUid?: string
  }
  draft: DraftResponse | { status: 'missing'; gmId: string; side: 'H' | 'A' }
  squads: { H: MatchSquadPlayer[]; A: MatchSquadPlayer[] }
  inputSetup: { H: DraftResponse['inputSetup']; A: DraftResponse['inputSetup'] }
}

type SyncScope = 'state' | 'records' | 'state_records' | 'cards'

/**
 * Browser code never writes Firestore. The backend owns the shared Draft and
 * record-level merge; IndexedDB remains the offline retry cache on each device.
 */
export function useMatchCollaboration() {
  const { request, openSocket } = useBackendApi()
  const liveRoom = useLiveRoom()
  const roomStatus = shallowRef<Awaited<ReturnType<typeof liveRoom.attach>>['status'] | null>(null)
  let detachRoom: (() => void) | undefined
  let socketBridge: Awaited<ReturnType<typeof liveRoom.bindSocket>> | undefined
  let pollTimer: ReturnType<typeof setInterval> | undefined
  let recoveryTimer: ReturnType<typeof setTimeout> | undefined
  let pollOnce: (() => Promise<void>) | undefined
  let closeSocket: (() => void) | undefined
  let sendSocket: ((data: unknown) => void) | undefined
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let heartbeatTimer: ReturnType<typeof setInterval> | undefined
  let reconnectAttempts = 0
  let lastSocketActivityAt = 0
  let lastHeartbeatTickAt = 0
  let stopped = true
  // stop() can run while start() is still awaiting auth or its first read
  // (the screen was left early). Each start owns one session number, and a
  // stale start must never arm a timer that nothing will clear again.
  let session = 0
  let polling = false
  let latestRevision = 0
  let latestSetupRevision = 0
  // A poll can return before this browser's queued write reaches the server.
  // These maps make the record stream an optimistic merge, never a whole-list
  // replacement that briefly removes a just-entered event from the screen.
  let confirmedRecordFingerprints = new Map<string, string>()
  let pendingRecordFingerprints = new Map<string, string>()
  let pendingDeletedRecordIds = new Set<string>()
  let recordCommands = new Map<string, string>()
  let deletedRecordCommands = new Map<string, string>()
  // Cards and substitutions are independent shared events. Never use a
  // whole-list fingerprint here: two analysts can legitimately save A and B
  // at the same time, and the server's [A, B] response must be accepted by
  // both browsers immediately.
  let confirmedCardFingerprints = new Map<string, string>()
  let pendingCardFingerprints = new Map<string, string>()
  let pendingDeletedCardIds = new Set<string>()
  let cardCommands = new Map<string, string>()
  let deletedCardCommands = new Map<string, string>()
  let confirmedSubFingerprints = new Map<string, string>()
  let pendingSubFingerprints = new Map<string, string>()
  let pendingDeletedSubIds = new Set<string>()
  let subCommands = new Map<string, string>()
  let deletedSubCommands = new Map<string, string>()
  let confirmedSetupFingerprint = ''
  let pendingSetupFingerprint = ''
  let setupCommandId = ''

  function markPendingRecords(records: DidRecord[]) {
    for (const record of records) {
      const fingerprint = recordFingerprint(record)
      if (confirmedRecordFingerprints.get(record.id) !== fingerprint) {
        if (pendingRecordFingerprints.get(record.id) !== fingerprint) recordCommands.delete(record.id)
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

  function mergeRemoteRecords(remoteRecords: DidRecord[], localRecords: DidRecord[]): DidRecord[] {
    const merged = new Map(remoteRecords.map(record => [record.id, record]))
    for (const recordId of pendingDeletedRecordIds) merged.delete(recordId)
    for (const record of localRecords) {
      const pendingFingerprint = pendingRecordFingerprints.get(record.id)
      if (pendingFingerprint !== recordFingerprint(record)) continue
      merged.set(record.id, record)
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
    const response = await request<{ role?: ParticipantRole; control?: boolean; revision?: number; roomEpoch?: number; participants: Record<string, { name?: string; role?: ParticipantRole }> }>(
      `/api/v1/match-input/drafts/${encodeURIComponent(game.matchId)}/${sideFor(game)}/participants`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, displayName: user?.displayName || user?.email || undefined }) },
    )
    game.participantRole = response.role ?? role
    game.lifecycleControl = response.control === true
    game.participantName = user?.displayName || user?.email || ''
    if (typeof response.revision === 'number') await liveRoom.observe(game.matchId, sideFor(game), response.revision, response.roomEpoch || 0)
    return response
  }

  async function enter(game: MatchState, role: ParticipantRole): Promise<DraftEnterResponse> {
    const user = await currentIdentity()
    const response = await request<DraftEnterResponse>(
      `/api/v1/match-input/drafts/${encodeURIComponent(game.matchId)}/${sideFor(game)}/enter`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, displayName: user?.displayName || user?.email || undefined }) },
    )
    game.participantRole = response.participant.role ?? role
    game.lifecycleControl = response.participant.control === true
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
    socketBridge?.detach()
    socketBridge = undefined
    session += 1
    detachRoom?.()
    detachRoom = undefined
    roomStatus.value = null
    pollOnce = undefined
    if (recoveryTimer) clearTimeout(recoveryTimer)
    recoveryTimer = undefined
    latestRevision = 0
    latestSetupRevision = 0
    confirmedSetupFingerprint = ''
    pendingSetupFingerprint = ''
    confirmedRecordFingerprints = new Map()
    pendingRecordFingerprints = new Map()
    pendingDeletedRecordIds = new Set()
    recordCommands.clear()
    deletedRecordCommands.clear()
    confirmedCardFingerprints = new Map()
    pendingCardFingerprints = new Map()
    pendingDeletedCardIds = new Set()
    cardCommands.clear()
    deletedCardCommands.clear()
    confirmedSubFingerprints = new Map()
    pendingSubFingerprints = new Map()
    pendingDeletedSubIds = new Set()
    subCommands.clear()
    deletedSubCommands.clear()
    setupCommandId = ''
    closeSocket?.()
    closeSocket = undefined
    sendSocket = undefined
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = undefined
    if (heartbeatTimer) clearInterval(heartbeatTimer)
    heartbeatTimer = undefined
    reconnectAttempts = 0
    lastSocketActivityAt = 0
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = undefined
  }

  async function start(game: MatchState, handlers: {
    applyState: (state: Partial<MatchState>) => void
    applyRecords: (records: DidRecord[]) => void
    applyCards?: (cards: CardRecord[]) => void
    applySetup?: (setup: NonNullable<DraftResponse['inputSetup']>) => void
    snapshotReady?: () => void
    resetPending?: () => void
    readOnly?: boolean
  }, initial?: DraftResponse | null) {
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
    // Only durable commands are pending. A device checkpoint must never make
    // already saved rows immune to legitimate remote edits/deletions.
    const queued = handlers.readOnly ? [] : await liveRoom.pending(gmId, side)
    if (current !== session || stopped) return false
    function restorePendingCommands(commands: typeof queued) {
      for (const command of commands) {
        if (initial && command.request.roomEpoch != null && command.request.roomEpoch !== (initial.roomEpoch || 0)) continue
        const body = command.request
        if (command.kind === 'draft') {
          if (['records', 'state_records', 'checkpoint'].includes(body.syncScope || 'checkpoint')) {
            const changed = body.payload.records.map(recordFromPayload)
            const merged = new Map(game.records.map(record => [record.id, record]))
            for (const record of changed) merged.set(record.id, record)
            for (const id of body.deletedRecordIds || []) { merged.delete(id); pendingDeletedRecordIds.add(id) }
            game.records = sortRecords([...merged.values()])
            markPendingRecords(changed)
            for (const record of changed) recordCommands.set(record.id, command.commandId)
            for (const id of body.deletedRecordIds || []) deletedRecordCommands.set(id, command.commandId)
          }
          if (body.syncScope === 'cards') {
            const cards = cardsFromPayload(body.payload.cards)
            const merged = new Map(game.cards.map(card => [card.id, card]))
            for (const card of cards) { merged.set(card.id, card); pendingCardFingerprints.set(card.id, cardFingerprint(card)) }
            for (const id of body.deletedCardIds || []) { merged.delete(id); pendingDeletedCardIds.add(id) }
            game.cards = sortCards([...merged.values()])
            for (const card of cards) cardCommands.set(card.id, command.commandId)
            for (const id of body.deletedCardIds || []) deletedCardCommands.set(id, command.commandId)
          }
        } else if (command.kind === 'setup') {
          if (body.syncScope === 'setup') pendingSetupFingerprint = setupFingerprintFromPayload(body.payload, body.clientState.subs || [])
          const merged = new Map(game.subs.map(sub => [sub.id, sub]))
          for (const sub of body.clientState.subs || []) { merged.set(sub.id, sub); pendingSubFingerprints.set(sub.id, subFingerprint(sub)) }
          for (const id of body.deletedSubIds || []) { merged.delete(id); pendingDeletedSubIds.add(id) }
          game.subs = sortSubs([...merged.values()])
          for (const sub of body.clientState.subs || []) subCommands.set(sub.id, command.commandId)
          for (const id of body.deletedSubIds || []) deletedSubCommands.set(id, command.commandId)
          if (body.syncScope === 'setup') {
            setupCommandId = command.commandId
            handlers.applySetup?.({ formationKey: body.payload.formationKey, fieldSide: body.payload.fieldSide,
              inputMode: body.payload.inputMode, lineup: body.payload.lineup, subs: game.subs,
              revision: latestSetupRevision, forceApply: true })
          }
        }
      }
      handlers.applyRecords(game.records)
      handlers.applyCards?.(game.cards)
    }
    restorePendingCommands(queued)

    let serverRecords = new Map<string, DidRecord>()
    let serverCards = new Map<string, CardRecord>()
    let serverSetup: NonNullable<DraftResponse['inputSetup']> | undefined
    let snapshotRevision = 0
    let stateRevision = 0
    const recordVersions = new Map<string, number>()
    const cardVersions = new Map<string, number>()
    const subVersions = new Map<string, number>()

    const observeServerCards = (cards: CardRecord[]) => {
      const server = new Map(cards.map(card => [card.id, cardFingerprint(card)]))
      for (const [id, fingerprint] of server) {
        if (pendingCardFingerprints.get(id) !== fingerprint) confirmedCardFingerprints.set(id, fingerprint)
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
      void liveRoom.seed(gmId, side, response)
      snapshotRevision = response.revision
      stateRevision = response.revision
      followParticipants(response.participantCount)
      if (response.sharedState && Object.keys(response.sharedState).length) handlers.applyState(liveState(response.sharedState))
      const setup = response.inputSetup
      if (setup && Object.keys(assignmentsFromLineup(setup.formationKey, setup.lineup)).length) {
        applySetup(setup)
      } else if (!Object.keys(game.assigned || {}).length && Object.keys(assignmentsFromLineup(response.payload.formationKey, response.payload.lineup)).length) {
        // Repair a screen already cleared by an empty setup using the same
        // response's durable lineup; no additional fetch or inferred starters.
        applySetup({ ...setup, formationKey: response.payload.formationKey, fieldSide: response.payload.fieldSide,
          lineup: response.payload.lineup, lineupIsBaseline: false, inputMode: response.payload.inputMode,
          subs: setup?.subs || [], revision: setup?.revision ?? latestSetupRevision, forceApply: true })
      }
      const records = sortRecords(response.payload.records.map(recordFromPayload))
      serverRecords = new Map(records.map(record => [record.id, record]))
      serverCards = new Map(cardsFromPayload(response.payload.cards).map(card => [card.id, card]))
      observeServerRecords(records)
      handlers.applyRecords(records)
      applyCards(cardsFromPayload(response.payload.cards))
      handlers.snapshotReady?.()
    }

    const applySetup = (setup: NonNullable<DraftResponse['inputSetup']>) => {
      if (!Object.keys(assignmentsFromLineup(setup.formationKey, setup.lineup)).length) return
      const revision = Number(setup.revision || 0)
      const fingerprint = setupFingerprint(setup)
      if (revision < latestSetupRevision) return
      // A local formation/lineup snapshot is already visible and
      // on its way to Firestore. Older poll/socket echoes must not replace it
      // with the previous lineup while the ACK is still in flight.
      if (pendingSetupFingerprint && fingerprint !== pendingSetupFingerprint && !pendingSubFingerprints.size && !pendingDeletedSubIds.size) return
      latestSetupRevision = Math.max(latestSetupRevision, revision)
      serverSetup = plain(setup)
      confirmedSetupFingerprint = fingerprint
      const server = new Map(setup.subs.map(sub => [sub.id, subFingerprint(sub)]))
      for (const [id, fingerprint] of server) {
        if (pendingSubFingerprints.get(id) !== fingerprint) confirmedSubFingerprints.set(id, fingerprint)
      }
      const merged = new Map(setup.subs.map(sub => [sub.id, sub]))
      for (const id of pendingDeletedSubIds) merged.delete(id)
      for (const sub of game.subs) {
        if (pendingSubFingerprints.get(sub.id) === subFingerprint(sub)) merged.set(sub.id, sub)
      }
      handlers.applySetup?.({ ...setup, subs: sortSubs([...merged.values()]) })
    }

    const applyDelta = (event: any, force = false) => {
      if (event.gmId !== gmId || event.side !== side || stopped) return
      if (roomStatus.value && event.roomEpoch != null && event.roomEpoch !== roomStatus.value.roomEpoch) {
        if (event.roomEpoch > roomStatus.value.roomEpoch) void poll()
        return
      }
      const revision = Number(event.revision || 0)
      latestRevision = Math.max(latestRevision, revision)
      void liveRoom.observeDelta(gmId, side, event)
      let recordsChanged = false
      for (const raw of event.records || []) {
        const record = recordFromPayload(raw)
        if (revision <= Math.max(snapshotRevision, recordVersions.get(record.id) || 0)) continue
        recordVersions.set(record.id, revision); serverRecords.set(record.id, record); recordsChanged = true
      }
      for (const id of event.deletedRecordIds || []) {
        if (revision <= Math.max(snapshotRevision, recordVersions.get(id) || 0)) continue
        recordVersions.set(id, revision); serverRecords.delete(id); recordsChanged = true
      }
      if (recordsChanged || (force && (event.records || event.deletedRecordIds))) {
        const records = sortRecords([...serverRecords.values()])
        observeServerRecords(records)
        handlers.applyRecords(records)
      }
      let cardsChanged = false
      for (const card of cardsFromPayload(event.cards || [])) {
        if (revision <= Math.max(snapshotRevision, cardVersions.get(card.id) || 0)) continue
        cardVersions.set(card.id, revision); serverCards.set(card.id, card); cardsChanged = true
      }
      for (const id of event.deletedCardIds || []) {
        if (revision <= Math.max(snapshotRevision, cardVersions.get(id) || 0)) continue
        cardVersions.set(id, revision); serverCards.delete(id); cardsChanged = true
      }
      if (cardsChanged || (force && (event.cards || event.deletedCardIds))) applyCards([...serverCards.values()])
      if (event.sharedState && revision >= stateRevision) {
        stateRevision = revision
        handlers.applyState(liveState(event.sharedState))
      }
      const header = event.setup && (!serverSetup || event.setup.revision >= serverSetup.revision) ? event.setup : serverSetup
      if (header && (event.setup || event.subs || event.deletedSubIds)) {
        const subs = new Map((serverSetup?.subs || []).map(sub => [sub.id, sub]))
        for (const sub of event.subs || []) {
          if (revision <= Math.max(snapshotRevision, subVersions.get(sub.id) || 0)) continue
          subVersions.set(sub.id, revision); subs.set(sub.id, sub)
        }
        for (const id of event.deletedSubIds || []) {
          if (revision <= Math.max(snapshotRevision, subVersions.get(id) || 0)) continue
          subVersions.set(id, revision); subs.delete(id)
        }
        applySetup({ ...header, subs: sortSubs([...subs.values()]), forceApply: true })
      }
    }

    let recoveryAttempts = 0, nextRecoveryAt = 0
    const poll = async () => {
      if (stopped || polling || current !== session || Date.now() < nextRecoveryAt) return
      polling = true
      try {
        const response = await liveRoom.readSnapshot(gmId, side) as DraftResponse
        if (current !== session) return
        recoveryAttempts = 0; nextRecoveryAt = 0
        if (recoveryTimer) clearTimeout(recoveryTimer)
        recoveryTimer = undefined
        if (response.status === 'missing') {
          void liveRoom.seed(gmId, side, response)
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
      } catch (error: any) {
        if (stopped || current !== session || error?.status === 403) return
        // Keep the visible state and outbox. Retry gaps even while the socket
        // remains online, where the ordinary fallback poll is disabled.
        const delay = Math.min(4_000, 500 * 2 ** recoveryAttempts++)
        nextRecoveryAt = Date.now() + delay
        if (recoveryTimer) clearTimeout(recoveryTimer)
        recoveryTimer = setTimeout(() => { recoveryTimer = undefined; void poll() }, delay)
      } finally {
        polling = false
      }
    }
    pollOnce = poll
    const connectSocket = async () => {
      try {
        const socket = await openSocket<{
        type: 'draft' | 'state' | 'cards' | 'setup' | 'mutation' | 'error' | 'pong' | 'room' | 'closed' | 'resync'
        room?: { participants: Record<string, { role?: ParticipantRole; name?: string; online?: boolean }>; primaryUid?: string }
        gmId?: string
        side?: 'H' | 'A'
        commandId?: string
        event?: {
          type: 'draft' | 'state' | 'cards' | 'setup' | 'delta' | 'resync'
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
        lastSocketActivityAt = Date.now()
        if (socketBridge?.receive(message)) return
        if (message.type === 'pong') return
        if (message.type === 'resync') { void poll(); return }
        if (message.type === 'closed' && message.gmId === gmId && message.side === side) {
          handlers.applyState({ halfStatus: 'final', clockStartedAt: null })
          return
        }
        if (message.type === 'room' && message.room && roomStatus.value) {
          roomStatus.value.participants = message.room.participants
          const participant = message.room.participants[$auth.currentUser!.uid]
          game.lifecycleControl = Boolean(participant?.role) && (message.room.primaryUid === $auth.currentUser!.uid || (participant?.role === 'manager' && !message.room.primaryUid))
          if (participant?.role) {
            game.participantRole = participant.role
          }
          return
        }
        const event = message.type === 'mutation' ? message.event : message
        if (event?.type === 'batch') { for (const delta of event.events || []) applyDelta(delta); return }
        if (event?.type === 'delta') { applyDelta(event); return }
        if (event?.type === 'resync') { void poll(); return }
        if (event?.type === 'draft' && event.response) {
          applyDraftResponse(event.response)
        }
        if (event?.type === 'state' && event.gmId === gmId && event.side === side && event.sharedState) {
          const revision = event.revision ?? 0
          if (revision >= latestRevision) {
            latestRevision = revision
            handlers.applyState(liveState(event.sharedState))
          }
        }
        if (event?.type === 'cards' && event.gmId === gmId && event.side === side && event.cards) {
          // Card broadcasts may arrive behind an ACT/state response. Never
          // let that older list temporarily erase cards that a newer revision
          // already confirmed.
          const revision = event.revision ?? 0
          if (revision >= latestRevision) {
            latestRevision = revision
            applyCards(cardsFromPayload(event.cards))
          }
        }
        if (event?.type === 'setup' && event.gmId === gmId && event.side === side && event.setup) applySetup(event.setup)
        }, () => {
          if (stopped || current !== session) return
          socketBridge?.detach(true)
          socketBridge = undefined
          if (roomStatus.value) {
            roomStatus.value.connection = 'offline'
            for (const participant of Object.values(roomStatus.value.participants)) participant.online = undefined
          }
          closeSocket = undefined
          sendSocket = undefined
          if (heartbeatTimer) clearInterval(heartbeatTimer)
          heartbeatTimer = undefined
          followParticipants(2)
          const delay = Math.min(4_000, 250 * 2 ** reconnectAttempts++)
          reconnectTimer = setTimeout(() => { void connectSocket() }, delay)
        })
        if (current === session && !stopped) {
          if (roomStatus.value) roomStatus.value.connection = 'online'
          closeSocket = socket.close
          sendSocket = socket.send
          const bridge = await liveRoom.bindSocket(gmId, side, socket.send)
          if (current !== session || stopped) { bridge.detach(); socket.close(); return }
          socketBridge = bridge
          socket.send({ type: 'subscribe', revision: latestRevision, roomEpoch: roomStatus.value?.roomEpoch || 0 })
          reconnectAttempts = 0
          lastSocketActivityAt = Date.now()
          lastHeartbeatTickAt = lastSocketActivityAt
          if (heartbeatTimer) clearInterval(heartbeatTimer)
          heartbeatTimer = setInterval(() => {
            if (stopped || current !== session || !sendSocket) return
            // A half-open browser socket does not always emit onclose when a
            // network device drops it. Force a normal reconnect instead of
            // letting subscriptions silently stop forever.
            const now = Date.now()
            // Background tabs may suspend timers. Give the connection a fresh
            // heartbeat opportunity after resuming instead of closing it
            // solely because the browser stopped scheduling callbacks.
            if (now - lastHeartbeatTickAt > 10_000) lastSocketActivityAt = now
            lastHeartbeatTickAt = now
            if (now - lastSocketActivityAt > 30_000) {
              closeSocket?.()
              return
            }
            try { sendSocket({ type: 'heartbeat' }) }
            catch { closeSocket?.() }
          }, 5_000)
          if (pollTimer) clearInterval(pollTimer)
          pollTimer = undefined
          // The subscription reports a gap when recovery needs a full read.
        } else socket.close()
      } catch (error: any) {
        if (stopped || current !== session) return
        if (roomStatus.value) roomStatus.value.connection = 'offline'
        followParticipants(2)
        if (error?.code === 'participant_required') return
        const delay = Math.min(4_000, 250 * 2 ** reconnectAttempts++)
        reconnectTimer = setTimeout(() => { void connectSocket() }, delay)
      }
    }
    // The enter response is already a durable Draft read. Reusing it avoids a
    // second complete Firestore read during normal screen entry. Fallback and
    // legacy callers still establish the boundary through poll().
    const hasInitialDraft = Boolean(initial && initial.status === 'ok')
    if (initial) applyDraftResponse(initial)
    if (handlers.readOnly) {
      await poll()
      if (current !== session || stopped) return false
      followParticipants(2)
      return true
    }
    const attached = await liveRoom.attach(gmId, side, (response) => {
      if (stopped || current !== session) return
      if (response.finalized) {
        handlers.applyState({ halfStatus: 'final', clockStartedAt: null })
        return
      }
      if (response.pendingCommand) {
        const command = response.pendingCommand
        const body = command.request
        if (command.kind === 'draft') {
          if (['records', 'state_records', 'checkpoint'].includes(body.syncScope || 'checkpoint')) {
            for (const record of body.payload.records.map(recordFromPayload)) {
              const local = game.records.find(item => item.id === record.id)
              if (local && recordFingerprint(local) === recordFingerprint(record)) {
                markPendingRecords([local])
                recordCommands.set(record.id, command.commandId)
              }
            }
            for (const id of body.deletedRecordIds || []) { pendingDeletedRecordIds.add(id); deletedRecordCommands.set(id, command.commandId) }
          }
          if (body.syncScope === 'cards') {
            for (const card of cardsFromPayload(body.payload.cards)) {
              const local = game.cards.find(item => item.id === card.id)
              if (local && cardFingerprint(local) === cardFingerprint(card)) {
                pendingCardFingerprints.set(card.id, cardFingerprint(card))
                cardCommands.set(card.id, command.commandId)
              }
            }
            for (const id of body.deletedCardIds || []) { pendingDeletedCardIds.add(id); deletedCardCommands.set(id, command.commandId) }
          }
        } else if (command.kind === 'setup') {
          if (body.syncScope === 'setup') {
            const fingerprint = setupFingerprintFromPayload(body.payload, body.clientState.subs || [])
            if (fingerprint === pendingSetupFingerprint) setupCommandId = command.commandId
          }
          for (const sub of body.clientState.subs || []) {
            const local = game.subs.find(item => item.id === sub.id)
            if (local && subFingerprint(local) === subFingerprint(sub)) {
              pendingSubFingerprints.set(sub.id, subFingerprint(sub))
              subCommands.set(sub.id, command.commandId)
            }
          }
          for (const id of body.deletedSubIds || []) { pendingDeletedSubIds.add(id); deletedSubCommands.set(id, command.commandId) }
        }
        return
      }
      if (response.acceptServer) {
        handlers.resetPending?.()
        pendingRecordFingerprints.clear()
        pendingDeletedRecordIds.clear()
        pendingCardFingerprints.clear()
        pendingDeletedCardIds.clear()
        pendingSubFingerprints.clear()
        pendingDeletedSubIds.clear()
        pendingSetupFingerprint = ''
        recordCommands.clear(); deletedRecordCommands.clear()
        cardCommands.clear(); deletedCardCommands.clear()
        subCommands.clear(); deletedSubCommands.clear()
        setupCommandId = ''
      }
      const ack = response.ackCommand
      const settledCommand = ack || response.rejectedCommand
      if (settledCommand) {
        for (const [commands, fingerprints] of [[recordCommands, pendingRecordFingerprints], [cardCommands, pendingCardFingerprints], [subCommands, pendingSubFingerprints]]) {
          for (const [id, commandId] of commands!) {
            if (commandId === settledCommand.commandId) { commands!.delete(id); fingerprints!.delete(id) }
          }
        }
        for (const [commands, deleted] of [[deletedRecordCommands, pendingDeletedRecordIds], [deletedCardCommands, pendingDeletedCardIds], [deletedSubCommands, pendingDeletedSubIds]] as const) {
          for (const [id, commandId] of commands) {
            if (commandId === settledCommand.commandId) { commands.delete(id); deleted.delete(id) }
          }
        }
        if (setupCommandId === settledCommand.commandId) { pendingSetupFingerprint = ''; setupCommandId = '' }
      }
      if (response.payload) {
        applyDraftResponse(response.acceptServer && response.inputSetup ? { ...response, inputSetup: { ...response.inputSetup, forceApply: true } } : response)
      } else if (response.inputSetup) applySetup({ ...response.inputSetup, forceApply: Boolean(ack) })
      else if (response.event?.type === 'delta') applyDelta(response.event, Boolean(ack))
      else if (response.event?.type === 'resync') void poll()
      if (ack && response.payload && response.revision < latestRevision) void poll()
      if (response.acceptServer && response.replayCommands) restorePendingCommands(response.replayCommands)
    })
    if (current !== session || stopped) { attached.detach(); return false }
    detachRoom = attached.detach
    roomStatus.value = attached.status
    roomStatus.value.connection = 'connecting'
    if (!hasInitialDraft) await poll()
    void connectSocket()
    return current === session
  }

  async function sync(
    game: MatchState,
    syncScope: SyncScope,
    deletedRecordIds: string[] = [],
    clearedRecordPlayerIds: string[] = [],
    changedRecords?: DidRecord[],
  ) {
    const { $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    // Capture each edit at call time, then send mutations in that exact order.
    // State and records used to race each other and a late whole-Draft write
    // could replace a freshly assigned player after navigation/refresh.
    const writesRecords = syncScope === 'records' || syncScope === 'state_records'
    const payload = plain(payloadFromState({ ...game,
      matchSnapshot: null,
      records: writesRecords ? (changedRecords || (deletedRecordIds.length ? [] : game.records)) : [],
      cards: syncScope === 'cards' ? game.cards : [],
    }))
    const clientState = plain({ seconds: game.seconds, h1Seconds: game.h1Seconds, h2Seconds: game.h2Seconds,
      clockStartedAt: game.clockStartedAt ?? null, halfStatus: game.halfStatus, homeScore: game.homeScore, awayScore: game.awayScore,
      records: [], cards: [], subs: [] })
    const sentRecords = writesRecords
      ? sortRecords(changedRecords ? plain(changedRecords) : deletedRecordIds.length ? [] : payload.records.map(recordFromPayload))
      : []
    if (writesRecords) {
      markPendingRecords(sentRecords)
      for (const recordId of deletedRecordIds) {
        pendingRecordFingerprints.delete(recordId)
        pendingDeletedRecordIds.add(recordId)
      }
    }
    else payload.records = []
    clientState.records = []
    clientState.cards = []
    clientState.subs = []
    // A live edit sends only its changed records. The previous implementation
    // resent the complete table for every ACT and made timer/card commands wait
    // behind an ever-growing Firestore batch.
    if (writesRecords) {
      payload.records = recordsToPayload(sentRecords)
      for (const record of payload.records) {
        const recordId = String(record.id)
        if (record.playerId === null && !clearedRecordPlayerIds.includes(recordId)) delete record.playerId
      }
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
    if (syncScope === 'cards') {
      payload.cards = payload.cards.filter(card => pendingCardFingerprints.has(String(card.id)))
    } else payload.cards = []
    return await liveRoom.submit(payload.gmId, payload.side, 'draft', {
      payload, clientState, syncScope, deletedRecordIds, deletedCardIds, clearedRecordPlayerIds,
    })
  }

  async function syncState(game: MatchState) {
    // Assistants own event edits, never the shared clock/lifecycle. An empty
    // debounced event save must not enqueue a forbidden state command.
    if (!game.lifecycleControl) return true
    return await sync(game, 'state')
  }

  async function syncRecords(game: MatchState, records: DidRecord[]) {
    game.records = records
    return await sync(game, 'records')
  }

  async function syncStateRecords(game: MatchState, records: DidRecord[], clearedRecordPlayerIds: string[] = [], includeState = true) {
    if (!records.length && !clearedRecordPlayerIds.length) return includeState ? await syncState(game) : true
    return await sync(game, includeState && game.lifecycleControl ? 'state_records' : 'records', [], clearedRecordPlayerIds, records)
  }

  async function removeRecord(game: MatchState, recordId: string, includeState = false) {
    return await sync(game, includeState && game.lifecycleControl ? 'state_records' : 'records', [recordId])
  }

  async function syncCards(game: MatchState) {
    return await sync(game, 'cards')
  }

  async function syncSetup(game: MatchState, syncScope: 'setup' | 'substitutions' = 'setup') {
    const { $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    if (syncScope === 'setup' && !game.lifecycleControl) return false
    const payload = plain(payloadFromState({ ...game, matchSnapshot: null, records: [], cards: [] }))
    const clientState = plain({ side: game.side, subs: game.subs, records: [], cards: [] })
    clientState.records = []
    clientState.cards = []
    payload.records = []
    payload.cards = []
    const setupPendingFingerprint = setupFingerprintFromPayload(payload, game.subs)
    if (syncScope === 'setup' && setupPendingFingerprint !== confirmedSetupFingerprint) pendingSetupFingerprint = setupPendingFingerprint
    const deletedSubIds = [...confirmedSubFingerprints.keys()].filter(id => !game.subs.some(sub => sub.id === id))
    if (syncScope === 'substitutions') {
      for (const sub of game.subs) {
        const fingerprint = subFingerprint(sub)
        if (confirmedSubFingerprints.get(sub.id) !== fingerprint) {
          if (pendingSubFingerprints.get(sub.id) !== fingerprint) subCommands.delete(sub.id)
          pendingSubFingerprints.set(sub.id, fingerprint)
        }
        pendingDeletedSubIds.delete(sub.id)
      }
      for (const id of deletedSubIds) {
        pendingSubFingerprints.delete(id)
        pendingDeletedSubIds.add(id)
      }
    }
    if (syncScope === 'substitutions') {
      clientState.subs = clientState.subs.filter(sub => pendingSubFingerprints.has(sub.id))
    }
    return await liveRoom.submit(payload.gmId, payload.side, 'setup', {
      payload, clientState, deletedSubIds, syncScope,
    })
  }

  // Protect edits before the debounced network write enters the durable outbox.
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
      if (confirmedCardFingerprints.get(card.id) !== fingerprint) {
        if (pendingCardFingerprints.get(card.id) !== fingerprint) cardCommands.delete(card.id)
        pendingCardFingerprints.set(card.id, fingerprint)
      }
      pendingDeletedCardIds.delete(card.id)
    }
  }

  async function acceptServer() {
    if (gameForRoom) await liveRoom.acceptServer(gameForRoom.matchId, sideFor(gameForRoom))
  }
  async function keepLocal() {
    if (gameForRoom) await liveRoom.keepLocal(gameForRoom.matchId, sideFor(gameForRoom))
  }
  let gameForRoom: MatchState | undefined
  return { join, enter, start: async (...args: Parameters<typeof start>) => { gameForRoom = args[0]; return await start(...args) }, stop, syncState, syncRecords, syncStateRecords, syncCards, syncSetup, removeRecord, mergeRemoteRecords, markLocalRecords, markLocalCards, roomStatus, acceptServer, keepLocal }
}
