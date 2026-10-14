import { reactive } from 'vue'
import { LiveCommandStore, type LiveCommand } from '~/utils/liveOutbox'

interface RoomStatus {
  connection: 'connecting' | 'online' | 'offline'
  phase: 'loading' | 'saved' | 'saving' | 'offline' | 'conflict' | 'error'
  revision: number
  roomEpoch: number
  savedCommandId: string | null
  pending: number
  error: string
  participants: Record<string, { role?: string; name?: string; online?: boolean }>
}
interface Room {
  status: RoomStatus
  running: boolean
  enqueue: Promise<unknown>
  timer?: ReturnType<typeof setTimeout>
  read: () => Promise<any>
  send: (command: LiveCommand) => Promise<any>
  listeners: Set<(response: any) => void>
  waiters: Map<string, (saved: boolean) => void>
  idle: Set<() => void>
  ownRevision: number
  completed: Set<string>
  cancelled: Set<string>
  initialized: boolean
  needsRecovery: boolean
  socket?: (command: LiveCommand) => Promise<any>
  socketBatch?: (commands: LiveCommand[]) => Promise<any>
  confirmedRecords: Map<string, string>
  recordVersions: Map<string, number>
  snapshotRevision: number
}
const rooms = new Map<string, Room>()
const store = new LiveCommandStore()
const fingerprint = (record: Record<string, unknown>) => JSON.stringify(Object.fromEntries(Object.entries({
  posX: null, posY: null, shootPosX: null, shootPosY: null, shootDspRange: null, isShot: null, playerId: null, ...record,
}).sort(([a], [b]) => a.localeCompare(b))))

function notifyRoom(room: Room, response: any) {
  for (const listener of room.listeners) {
    try { listener(response) } catch (error) { console.error('Live Room 화면 반영 실패', error) }
  }
}

function commandTargets(command: LiveCommand) {
  const body = command.request, scope = body.syncScope
  const targets = new Set<string>()
  if (command.kind === 'draft') {
    if (['state', 'state_records', 'checkpoint'].includes(scope)) targets.add('state')
    if (['records', 'state_records', 'checkpoint'].includes(scope)) {
      for (const row of body.payload?.records || []) targets.add(`record:${row.id}`)
      for (const id of body.deletedRecordIds || []) targets.add(`record:${id}`)
    }
    if (['cards', 'checkpoint'].includes(scope)) {
      for (const row of body.payload?.cards || []) targets.add(`card:${row.id}`)
      for (const id of body.deletedCardIds || []) targets.add(`card:${id}`)
    }
  } else if (command.kind === 'setup') {
    if (scope === 'setup') targets.add('setup')
    for (const row of body.clientState?.subs || []) targets.add(`sub:${row.id}`)
    for (const id of body.deletedSubIds || []) targets.add(`sub:${id}`)
  } else targets.add('*')
  return targets
}

function isEmptyState(command: LiveCommand) {
  const body = command.request
  return command.kind === 'draft' && body.syncScope === 'state' &&
    !body.payload?.records?.length && !body.payload?.cards?.length && !body.clientState?.subs?.length &&
    !body.deletedRecordIds?.length && !body.deletedCardIds?.length && !body.clearedRecordPlayerIds?.length
}

function isUnchangedSetup(command: LiveCommand, baseline: any) {
  const body = command.request
  if (baseline.status !== 'ok' || !baseline.inputSetup ||
      (body.roomEpoch != null && body.roomEpoch !== (baseline.roomEpoch || 0))) return false
  if (!((command.kind === 'setup' && body.syncScope === 'setup') ||
      (command.kind === 'draft' && body.syncScope === 'checkpoint'))) return false
  if (body.payload?.records?.length || body.payload?.cards?.length || body.deletedRecordIds?.length ||
      body.deletedCardIds?.length || body.deletedSubIds?.length || body.clearedRecordPlayerIds?.length) return false
  const rows = (values: any[], keys: string[]) => JSON.stringify((values || []).map(value =>
    keys.map(key => value[key] ?? null)).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))))
  const setup = baseline.inputSetup, payload = body.payload
  return payload.formationKey === setup.formationKey && payload.fieldSide === setup.fieldSide &&
    payload.inputMode === setup.inputMode &&
    rows(payload.lineup, ['playerId', 'slot', 'type', 'order', 'inHalf', 'inSeconds', 'outHalf', 'outSeconds']) ===
      rows(setup.lineup, ['playerId', 'slot', 'type', 'order', 'inHalf', 'inSeconds', 'outHalf', 'outSeconds']) &&
    rows(body.clientState?.subs, ['id', 'half', 'seconds', 'outPlayer', 'inPlayer']) ===
      rows(setup.subs, ['id', 'half', 'seconds', 'outPlayer', 'inPlayer'])
}

function selectCommands(commands: LiveCommand[]) {
  const blocked = new Set<string>(), selected: LiveCommand[] = []
  let cost = 1, bytes = 0
  for (const command of commands) {
    const targets = commandTargets(command)
    const forbiddenState = command.blocked === 'primary_required' && isEmptyState(command)
    if (!forbiddenState && (command.blocked || blocked.has('*') || [...targets].some(key => blocked.has(key)))) {
      if (command.blocked && !['draft_conflict', 'primary_required', 'command_too_large'].includes(command.blocked)) blocked.add('*')
      for (const key of targets) blocked.add(key)
      continue
    }
    if (command.kind === 'finalize' || command.kind === 'restore') { if (!selected.length && !blocked.size) selected.push(command); break }
    const size = JSON.stringify(command.request).length * 3
    if (selected.length && (selected.length >= 20 || cost + targets.size + 1 > 350 || bytes + size > 262144)) break
    selected.push(command); cost += targets.size + 1; bytes += size
  }
  return selected
}

export function useLiveRoom() {
  const { request } = useBackendApi()

  async function roomFor(gmId: string, side: 'H' | 'A') {
    const { $auth, $authReady } = useNuxtApp()
    await $authReady
    const uid = $auth.currentUser?.uid
    if (!uid) throw new Error('로그인이 필요합니다.')
    const key = `${uid}:${gmId}:${side}`
    let room = rooms.get(key)
    if (!room) {
      let reading: Promise<any> | undefined
      room = {
        status: reactive({ connection: 'connecting', phase: 'loading', revision: 0, roomEpoch: 0, savedCommandId: null, pending: 0, error: '', participants: {} }),
        running: false, initialized: false, needsRecovery: false, confirmedRecords: new Map(), recordVersions: new Map(), snapshotRevision: 0, ownRevision: 0, completed: new Set(), cancelled: new Set(), enqueue: Promise.resolve(), listeners: new Set(), waiters: new Map(), idle: new Set(),
        read: () => {
          if (!reading) reading = request<any>(`/api/v1/match-input/drafts/${encodeURIComponent(gmId)}/${side}`, {
            expectedUid: uid, signal: AbortSignal.timeout(10_000),
          }).finally(() => { reading = undefined })
          return reading
        },
        send: command => request<any>(`/api/v1/match-input/drafts/${encodeURIComponent(gmId)}/${side}${command.kind === 'setup' ? '/setup' : command.kind === 'finalize' ? '/finalize' : command.kind === 'restore' ? '/restore-raw' : ''}`, {
          expectedUid: uid, method: command.kind === 'finalize' || command.kind === 'restore' ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(command.request),
          signal: AbortSignal.timeout(15_000),
        }),
      }
      rooms.set(key, room)
    }
    return { room, key, uid }
  }

  async function pump(key: string, room: Room) {
    if (room.running) return
    room.running = true
    if (room.timer) clearTimeout(room.timer)
    room.timer = undefined
    let activeCommand: LiveCommand | undefined
    const confirmCommand = async (command: LiveCommand, response: any) => {
      const ack = response.inputSetup || response
      if (ack?.commandId !== command.commandId || typeof ack.savedRevision !== 'number') throw new Error('서버 저장 확인이 누락되었습니다.')
      await store.remove(command.order!)
      room.status.revision = Math.max(room.status.revision, ack.savedRevision, Number(response.revision || 0))
      room.ownRevision = Math.max(room.ownRevision, ack.savedRevision)
      room.status.savedCommandId = command.commandId
      if (response.event) rememberDelta(room, response.event)
      if (command.kind === 'finalize') room.status.roomEpoch += 1
      room.status.pending -= 1
      if (!room.status.pending) { room.status.phase = 'saved'; room.status.error = '' }
      notifyRoom(room, { ...response, ackCommand: command, finalized: command.kind === 'finalize' })
      room.waiters.get(command.commandId)?.(true)
      if (!room.waiters.has(command.commandId)) {
        room.completed.add(command.commandId)
        if (room.completed.size > 1000) room.completed.delete(room.completed.values().next().value!)
      }
      room.waiters.delete(command.commandId)
    }
    try {
      let commands = await store.list(key)
      room.status.pending = commands.length
      if (!commands.length) { room.status.phase = 'saved'; room.status.error = ''; return }
      // Prepared entry data is already a durable baseline. Read only when it
      // is unavailable or a transport failure requires recovery.
      if (!room.initialized || room.needsRecovery) {
        const baseline = await room.read()
        seedRoom(room, baseline)
        if (baseline.status === 'ok') notifyRoom(room, baseline)
      }
      while (true) {
        await room.enqueue
        commands = await store.list(key)
        const selected = selectCommands(commands)
        const command = selected[0] || commands[0]
        if (!command) break
        activeCommand = command
        const skipState = async (item = command) => {
          await store.remove(item.order!)
          room.status.pending -= 1
          room.waiters.get(item.commandId)?.(false)
          if (!room.waiters.has(item.commandId)) room.cancelled.add(item.commandId)
          room.waiters.delete(item.commandId)
          notifyRoom(room, { rejectedCommand: item, code: 'primary_required' })
        }
        const skipForbidden = async (item = command) => {
          if (isEmptyState(item)) { await skipState(item); return true }
          if (!['setup', 'checkpoint'].includes(item.request.syncScope)) return false
          try {
            const baseline = await room.read()
            if (!isUnchangedSetup(item, baseline)) return false
            await skipState(item)
            notifyRoom(room, baseline)
            return true
          } catch { return false }
        }
        if (command.blocked === 'primary_required' && await skipForbidden()) {
          continue
        }
        if (command.blocked) {
          room.status.phase = command.blocked === 'draft_conflict' ? 'conflict' : 'error'
          room.status.error = command.blocked
          break
        }
        room.status.phase = 'saving'
        if (selected.length > 1) {
          for (const item of selected) {
            if (item.request.baseRevision == null) { item.request.baseRevision = room.status.revision; item.request.roomEpoch = room.status.roomEpoch }
            if (!item.attempted) { item.request.baseRevision = Math.max(item.request.baseRevision, room.ownRevision); item.attempted = true; await store.update(item) }
          }
          const sendHttp = () => request<any>(`/api/v1/match-input/drafts/${encodeURIComponent(command.gmId)}/${command.side}/commands`, {
            expectedUid: command.uid, method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ commands: selected.map(item => ({ kind: item.kind, request: item.request })) }), signal: AbortSignal.timeout(15_000),
          })
          let response: any
          try { response = room.socketBatch ? await room.socketBatch(selected) : await sendHttp() }
          catch (error: any) { if (!error?.transportFailure) throw error; response = await sendHttp() }
          const results = response.results
          if (!Array.isArray(results) || results.length !== selected.length || results.some((result: any, index: number) => result.commandId !== selected[index]!.commandId || (result.status === 'ack' ? typeof result.savedRevision !== 'number' : result.status !== 'error' || !result.code))) throw new Error('배치 저장 확인이 누락되었습니다.')
          let transient: any, rejected = false
          for (let index = 0; index < selected.length; index++) {
            const item = selected[index]!, result = results[index]
            if (result.status === 'ack') await confirmCommand(item, result)
            else if (result.code === 'primary_required' && await skipForbidden(item)) { /* redundant request removed */ }
            else if (result.code !== 'dependency_blocked') {
              if ([403, 409, 422].includes(result.httpStatus)) { item.blocked = result.code; await store.update(item); room.status.error = `[${result.code}] ${result.message}`; rejected = true }
              else transient = { ...result, status: result.httpStatus, command: item }
            }
          }
          if (rejected) {
            try { const baseline = await room.read(); if (baseline.status === 'ok') notifyRoom(room, baseline) } catch { /* rejected commands remain durable */ }
          }
          if (transient) { activeCommand = transient.command; throw Object.assign(new Error(transient.message), transient) }
          continue
        }
        // Only unset revisions are initialized here. Never silently rebase an
        // attempted command; retries retain the original ID and base version.
        if (command.request.baseRevision == null) {
          command.request.baseRevision = room.status.revision
          command.request.roomEpoch = room.status.roomEpoch
          await store.update(command)
        }
        if (!command.attempted) {
          command.request.baseRevision = Math.max(command.request.baseRevision, room.ownRevision)
          command.attempted = true
          await store.update(command)
        }
        let response: any
        try {
        if (room.socket && (command.kind === 'draft' || command.kind === 'setup')) {
          try { response = await room.socket(command) }
          catch (error: any) {
            if (!error?.transportFailure) throw error
            // The original command ID survives the socket-to-HTTP retry.
            response = await room.send(command)
          }
        } else response = await room.send(command)
        } catch (error: any) {
          // Only a server-rejected, event-free state command is independent
          // of later ACTs. Event conflicts and all other failures stay blocked.
          if (error?.code === 'primary_required' && error?.status === 403 && await skipForbidden()) {
            continue
          }
          throw error
        }
        await confirmCommand(command, response)
      }
      commands = await store.list(key)
      if (!commands.length) { room.status.phase = 'saved'; room.status.error = '' }
    } catch (error: any) {
      const commands = await store.list(key)
      const code = error?.code
      const permanent = (error?.status === 403 && code !== 'account_changed') || error?.status === 422 || error?.status === 409
      room.status.phase = code === 'draft_conflict' ? 'conflict' : permanent || code === 'account_changed' ? 'error' : code === 'command_in_progress' ? 'saving' : 'offline'
      room.status.error = `${code ? `[${code}] ` : ''}${error instanceof Error ? error.message : String(error)}`
      if (!permanent && code !== 'account_changed') room.needsRecovery = true
      const failed = commands.find(item => item.commandId === activeCommand?.commandId)
      if (permanent && failed) {
        failed.blocked = code || 'command_rejected'
        await store.update(failed)
        try {
          const baseline = await room.read()
          if (baseline.status === 'ok') notifyRoom(room, baseline)
        } catch { /* The queue stays durable until the user resolves it. */ }
      }
    } finally {
      room.status.pending = (await store.list(key)).length
      for (const resolve of room.waiters.values()) resolve(false)
      room.waiters.clear()
      room.running = false
      for (const resolve of room.idle) resolve()
      room.idle.clear()
      if (room.status.pending && room.status.phase !== 'conflict' && room.status.phase !== 'error') {
        room.timer = setTimeout(() => { void pump(key, room) }, room.status.phase === 'saving' ? 0 : 2_000)
      }
    }
  }

  async function submit(gmId: string, side: 'H' | 'A', kind: LiveCommand['kind'], body: Record<string, unknown>) {
    const { room, key, uid } = await roomFor(gmId, side)
    const commandId = crypto.randomUUID()
    const command: LiveCommand = { commandId, key, uid, gmId, side, kind,
      request: JSON.parse(JSON.stringify({ ...body, commandId, baseRevision: room.status.phase === 'loading' ? null : room.status.revision,
        roomEpoch: room.status.phase === 'loading' ? null : room.status.roomEpoch })) }
    const added = room.enqueue.then(async () => {
      await store.add(command)
      room.status.pending += 1
      if (room.status.phase !== 'conflict' && room.status.phase !== 'error') room.status.phase = 'saving'
      notifyRoom(room, { pendingCommand: command })
    })
    room.enqueue = added.catch(() => undefined)
    await added
    if (room.running) await new Promise<void>(resolve => room.idle.add(resolve))
    if (room.cancelled.delete(commandId)) return false
    if (room.completed.delete(commandId)) return true
    const result = new Promise<boolean>(resolve => room.waiters.set(commandId, resolve))
    void pump(key, room)
    return await result
  }

  async function attach(gmId: string, side: 'H' | 'A', listener: (response: any) => void) {
    const { room, key } = await roomFor(gmId, side)
    room.listeners.add(listener)
    void pump(key, room)
    return { status: room.status, detach: () => room.listeners.delete(listener) }
  }

  async function observe(gmId: string, side: 'H' | 'A', revision: number, roomEpoch = 0) {
    const { room } = await roomFor(gmId, side)
    room.status.revision = Math.max(room.status.revision, revision)
    room.status.roomEpoch = roomEpoch
    room.initialized = true
    if (room.status.phase === 'loading' && !room.status.pending) room.status.phase = 'saved'
  }

  function seedRoom(room: Room, response: any) {
    if (Number(response.revision || 0) < room.status.revision && Number(response.roomEpoch || 0) === room.status.roomEpoch) return
    room.status.revision = Math.max(room.status.revision, Number(response.revision || 0))
    room.status.roomEpoch = Number(response.roomEpoch || 0)
    room.initialized = true
    room.needsRecovery = false
    if (room.status.phase === 'loading' && !room.status.pending) room.status.phase = 'saved'
    if (response.payload) {
      room.confirmedRecords = new Map(response.payload.records.map((record: any) => [record.id, fingerprint(record)]))
      room.snapshotRevision = Number(response.revision || 0)
      room.recordVersions.clear()
    }
  }

  function rememberDelta(room: Room, event: any) {
    const revision = Number(event.revision || 0)
    for (const record of event.records || []) {
      if (revision <= Math.max(room.snapshotRevision, room.recordVersions.get(record.id) || 0)) continue
      room.recordVersions.set(record.id, revision); room.confirmedRecords.set(record.id, fingerprint(record))
    }
    for (const id of event.deletedRecordIds || []) {
      if (revision <= Math.max(room.snapshotRevision, room.recordVersions.get(id) || 0)) continue
      room.recordVersions.set(id, revision); room.confirmedRecords.delete(id)
    }
  }

  async function seed(gmId: string, side: 'H' | 'A', response: any) {
    const { room } = await roomFor(gmId, side)
    seedRoom(room, response)
  }

  async function observeDelta(gmId: string, side: 'H' | 'A', event: any) {
    const { room } = await roomFor(gmId, side)
    rememberDelta(room, event)
    await observe(gmId, side, Math.max(room.status.revision, Number(event.revision || 0)), event.roomEpoch || 0)
  }

  async function changedRecords(gmId: string, side: 'H' | 'A', records: Array<Record<string, unknown>>) {
    const { room } = await roomFor(gmId, side)
    return records.filter(record => room.confirmedRecords.get(String(record.id)) !== fingerprint(record))
  }

  async function bindSocket(gmId: string, side: 'H' | 'A', send: (frame: unknown) => void) {
    const { room, key, uid } = await roomFor(gmId, side)
    const pending = new Map<string, { resolve: (ack: any) => void; reject: (error: any) => void; timer: ReturnType<typeof setTimeout> }>()
    const transmit = (commandId: string, frame: unknown) => new Promise<any>((resolve, reject) => {
      if (useNuxtApp().$auth.currentUser?.uid !== uid) { reject(Object.assign(new Error('로그인 계정이 변경되었습니다.'), { status: 403, code: 'account_changed' })); return }
      const timer = setTimeout(() => {
        pending.delete(commandId)
        reject(Object.assign(new Error('소켓 저장 확인 시간 초과'), { transportFailure: true }))
      }, 5_000)
      pending.set(commandId, { resolve, reject, timer })
      try { send(frame) }
      catch (error) { clearTimeout(timer); pending.delete(commandId); reject(Object.assign(new Error('소켓 전송 실패'), { transportFailure: true })) }
    })
    const transport = (command: LiveCommand) => transmit(command.commandId, { type: 'command', command: 'mutation', commandId: command.commandId, mutation: command.kind, request: command.request })
    const batchTransport = (commands: LiveCommand[]) => {
      const id = crypto.randomUUID()
      return transmit(id, { type: 'command', command: 'batch', commandId: id, commands: commands.map(command => ({ kind: command.kind, request: command.request })) })
    }
    room.socket = transport
    room.socketBatch = batchTransport
    void pump(key, room)
    return {
      receive(frame: any) {
        const waiter = pending.get(frame.commandId)
        if (!waiter) return false
        clearTimeout(waiter.timer)
        pending.delete(frame.commandId)
        if (frame.type === 'error') waiter.reject(Object.assign(new Error(frame.message), { code: frame.code, status: frame.status || 500 }))
        else waiter.resolve(frame)
        return true
      },
      detach(lost = false) {
        if (room.socket === transport) { room.socket = undefined; room.socketBatch = undefined }
        if (lost) room.needsRecovery = true
        for (const waiter of pending.values()) {
          clearTimeout(waiter.timer)
          waiter.reject(Object.assign(new Error('소켓 연결 종료'), { transportFailure: true }))
        }
        pending.clear()
      },
    }
  }

  // Explicit conflict resolution adopts the server and discards only the
  // rejected head command. Later commands keep their IDs and are revalidated.
  async function acceptServer(gmId: string, side: 'H' | 'A') {
    const { room, key } = await roomFor(gmId, side)
    if (room.running) return
    const commands = await store.list(key)
    if (!commands[0]?.blocked) return
    const baseline = await room.read()
    await store.remove(commands[0].order!)
    room.status.revision = Number(baseline.revision || 0)
    const remaining = await store.list(key)
    notifyRoom(room, { ...baseline, acceptServer: true, replayCommands: remaining })
    room.status.phase = remaining.length ? 'loading' : 'saved'
    room.status.error = ''
    await pump(key, room)
  }

  async function flush(gmId: string, side: 'H' | 'A') {
    const { room, key } = await roomFor(gmId, side)
    await room.enqueue
    if (room.running) await new Promise<void>(resolve => room.idle.add(resolve))
    if (!room.running) await pump(key, room)
    return (await store.list(key)).length === 0 && !room.running
  }

  async function pending(gmId: string, side: 'H' | 'A') {
    const { key } = await roomFor(gmId, side)
    return await store.list(key)
  }

  async function readSnapshot(gmId: string, side: 'H' | 'A') {
    const { room } = await roomFor(gmId, side)
    return await room.read()
  }

  return { submit, attach, observe, seed, observeDelta, changedRecords, bindSocket, acceptServer, flush, pending, readSnapshot }
}
