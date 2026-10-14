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
  conflict?: { commandId: string; local: string; server: string; revision: number }
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
  completed: Set<string>
  cancelled: Set<string>
  initialized: boolean
  needsRecovery: boolean
  socket?: (command: LiveCommand) => Promise<any>
  socketBatch?: (commands: LiveCommand[]) => Promise<any>
  confirmedRecords: Map<string, string>
  recordVersions: Map<string, number>
  snapshotRevision: number
  acknowledgedTargets: Map<string, { commandId: string; revision: number }>
  inputs: Map<string, any>
  inputVersions: Map<string, number>
  clockOffset: number
  lastEditAt: number
  pendingInputs: Map<string, { commandId: string; value: any }>
}
const rooms = new Map<string, Room>()
const store = new LiveCommandStore()
let deviceId: string | undefined
function editingDevice() {
  if (deviceId) return deviceId
  try {
    deviceId = localStorage.getItem('did-live-edit-device') || crypto.randomUUID()
    localStorage.setItem('did-live-edit-device', deviceId)
  } catch { deviceId = crypto.randomUUID() }
  return deviceId
}

function commandInputs(command: LiveCommand) {
  const body = command.request, values = new Map<string, any>()
  for (const target of commandTargets(command)) {
    if (target === 'state') values.set(target, { ...body.clientState, halfStatus: body.payload?.status, homeScore: body.payload?.homeScore, awayScore: body.payload?.awayScore })
    else if (target === 'setup') values.set(target, { formationKey: body.payload?.formationKey, fieldSide: body.payload?.fieldSide, inputMode: body.payload?.inputMode, lineup: body.payload?.lineup })
    else {
      const split = target.indexOf(':'), kind = target.slice(0, split), id = target.slice(split + 1)
      const rows = kind === 'record' ? body.payload?.records : kind === 'card' ? body.payload?.cards : body.clientState?.subs
      values.set(target, rows?.find((row: any) => row.id === id) || null)
    }
  }
  return values
}
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

function describeInput(command: LiveCommand, baseline?: any) {
  const body = command.request
  const rows = baseline?.payload || body.payload || {}
  const targets = commandTargets(command)
  const labels: string[] = []
  for (const target of targets) {
    const separator = target.indexOf(':')
    const kind = separator < 0 ? target : target.slice(0, separator)
    const id = target.slice(separator + 1)
    if (kind === 'record' || kind === 'card') {
      const row = (rows[kind === 'record' ? 'records' : 'cards'] || []).find((item: any) => item.id === id)
      labels.push(row ? `${kind === 'record' ? '행동' : '카드'} ${id}: ${row.half || ''} ${row.halfSeconds ?? row.seconds ?? 0}초 / ${row.act || row.card || ''} ${row.res || ''} / 선수 ${row.playerId ?? '미지정'} / 구역 ${row.area ?? ''}` : `${kind === 'record' ? '행동' : '카드'} ${id}: 없음(삭제)`)
      if (row && kind === 'record') {
        if (row.posX != null || row.posY != null) labels.push(`입력 위치: ${row.posX ?? '미지정'}, ${row.posY ?? '미지정'}`)
        if (row.shootPosX != null || row.shootPosY != null) labels.push(`슈팅 위치: ${row.shootPosX ?? '미지정'}, ${row.shootPosY ?? '미지정'}`)
      }
    } else if (kind === 'sub') {
      const subs = baseline ? baseline.inputSetup?.subs : body.clientState?.subs
      const row = (subs || []).find((item: any) => item.id === id)
      labels.push(row ? `교체 ${id}: ${row.outPlayer} → ${row.inPlayer}` : `교체 ${id}: 없음(삭제)`)
    } else if (kind === 'state') {
      const state = baseline?.sharedState || body.clientState || {}
      labels.push(`경기 상태: ${state.halfStatus || rows.status || ''}, ${state.seconds ?? 0}초, 점수 ${state.homeScore ?? rows.homeScore ?? 0}:${state.awayScore ?? rows.awayScore ?? 0}`)
    } else if (kind === 'setup') {
      const setup = baseline?.inputSetup || rows
      labels.push(`세팅: ${setup.formationKey || ''}, ${setup.fieldSide || ''}, 선수 ${(setup.lineup || []).map((item: any) => item.playerId).join(', ')}`)
    }
  }
  return labels.join('\n')
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
        running: false, initialized: false, needsRecovery: false, confirmedRecords: new Map(), recordVersions: new Map(), snapshotRevision: 0, acknowledgedTargets: new Map(), inputs: new Map(), inputVersions: new Map(), clockOffset: 0, lastEditAt: 0, pendingInputs: new Map(), completed: new Set(), cancelled: new Set(), enqueue: Promise.resolve(), listeners: new Set(), waiters: new Map(), idle: new Set(),
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
      if (typeof response.serverTimeMs === 'number') room.clockOffset = response.serverTimeMs - Date.now()
      for (const target of commandTargets(command)) {
        if (ack.savedRevision > (room.acknowledgedTargets.get(target)?.revision || 0)) {
          room.acknowledgedTargets.set(target, { commandId: command.commandId, revision: ack.savedRevision })
        }
      }
      await store.remove(command.order!)
      room.status.revision = Math.max(room.status.revision, ack.savedRevision, Number(response.revision || 0))
      room.status.savedCommandId = command.commandId
      if (room.status.conflict?.commandId === command.commandId) room.status.conflict = undefined
      if (response.event) rememberDelta(room, response.event)
      for (const target of commandTargets(command)) {
        if (room.pendingInputs.get(target)?.commandId === command.commandId) room.pendingInputs.delete(target)
      }
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
      for (const item of commands) {
        if (item.blocked === 'draft_conflict' && (item.kind === 'draft' || item.kind === 'setup')) {
          item.blocked = undefined
          await store.update(item)
        }
      }
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
          if (command.blocked === 'draft_conflict' && room.status.conflict?.commandId !== command.commandId) {
            try {
              const baseline = await room.read()
              room.status.conflict = { commandId: command.commandId, local: describeInput(command), server: describeInput(command, baseline), revision: Number(baseline.revision || 0) }
            } catch { /* comparison can be retried; keep the rejected request */ }
          }
          break
        }
        room.status.phase = 'saving'
        if (selected.length > 1) {
          for (const item of selected) {
            if (item.request.baseRevision == null) { item.request.baseRevision = room.status.revision; item.request.roomEpoch = room.status.roomEpoch }
            if (!item.attempted) { if (item.request.edit) item.request.edit.sequence = item.order; item.attempted = true; await store.update(item) }
          }
          const sendHttp = () => request<any>(`/api/v1/match-input/drafts/${encodeURIComponent(command.gmId)}/${command.side}/commands`, {
            expectedUid: command.uid, method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ commands: selected.map(item => ({ kind: item.kind, request: item.request })) }), signal: AbortSignal.timeout(15_000),
          })
          let response: any
          try { response = room.socketBatch ? await room.socketBatch(selected) : await sendHttp() }
          catch (error: any) { if (!error?.transportFailure) throw error; response = await sendHttp() }
          const results = response.results
          if (typeof response.serverTimeMs === 'number') room.clockOffset = response.serverTimeMs - Date.now()
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
            try {
              const baseline = await room.read()
              const conflict = selected.find(item => item.blocked === 'draft_conflict')
              if (conflict) room.status.conflict = { commandId: conflict.commandId, local: describeInput(conflict), server: describeInput(conflict, baseline), revision: Number(baseline.revision || 0) }
              if (baseline.status === 'ok') notifyRoom(room, baseline)
            } catch { /* rejected commands remain durable */ }
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
          if (command.request.edit) command.request.edit.sequence = command.order
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
          if (code === 'draft_conflict') room.status.conflict = { commandId: failed.commandId, local: describeInput(failed), server: describeInput(failed, baseline), revision: Number(baseline.revision || 0) }
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
    const localEditedAt = Date.now()
    const { room, key, uid } = await roomFor(gmId, side)
    const commandId = crypto.randomUUID()
    const changedAtMs = room.lastEditAt = Math.max(room.lastEditAt, localEditedAt + room.clockOffset)
    const command: LiveCommand = { commandId, key, uid, gmId, side, kind,
      request: JSON.parse(JSON.stringify({ ...body, commandId, baseRevision: room.status.phase === 'loading' ? null : room.status.revision,
        roomEpoch: room.status.phase === 'loading' ? null : room.status.roomEpoch })) }
    const targets = commandTargets(command)
    const pendingAtCreation = new Set(room.pendingInputs.keys())
    const before = JSON.parse(JSON.stringify(Object.fromEntries([...targets].map(target =>
      [target, room.pendingInputs.has(target) ? room.pendingInputs.get(target)!.value : room.inputs.get(target) ?? null]))))
    if (kind === 'draft' || kind === 'setup') {
      for (const [target, value] of commandInputs(command)) room.pendingInputs.set(target, { commandId, value: JSON.parse(JSON.stringify(value)) })
    }
    const added = room.enqueue.then(async () => {
      if (kind === 'draft' || kind === 'setup') {
        for (const earlier of await store.list(key)) {
          if (earlier.request.roomEpoch !== command.request.roomEpoch) continue
          for (const [target, value] of commandInputs(earlier)) {
            if (targets.has(target) && !pendingAtCreation.has(target)) before[target] = value
          }
        }
        command.request.edit = { changedAtMs, deviceId: editingDevice(), sequence: 0, before }
      }
      await store.add(command)
      room.status.pending += 1
      if (room.status.phase !== 'conflict' && room.status.phase !== 'error') room.status.phase = 'saving'
      notifyRoom(room, { pendingCommand: command })
    })
    room.enqueue = added.catch(() => undefined)
    try { await added } catch (error) {
      for (const target of targets) if (room.pendingInputs.get(target)?.commandId === commandId) room.pendingInputs.delete(target)
      throw error
    }
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
    if (typeof response.serverTimeMs === 'number') room.clockOffset = response.serverTimeMs - Date.now()
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
      room.inputs.clear(); room.inputVersions.clear()
      for (const [prefix, rows] of [['record', response.payload.records], ['card', response.payload.cards], ['sub', response.inputSetup?.subs]] as const) {
        for (const row of rows || []) room.inputs.set(`${prefix}:${row.id}`, row)
      }
      room.inputs.set('state', response.sharedState || { ...response.clientState, halfStatus: response.payload.status, homeScore: response.payload.homeScore, awayScore: response.payload.awayScore })
      if (response.inputSetup) room.inputs.set('setup', response.inputSetup)
    }
  }

  function rememberDelta(room: Room, event: any) {
    const revision = Number(event.revision || 0)
    for (const [prefix, rows, deleted] of [['record', event.records, event.deletedRecordIds], ['card', event.cards, event.deletedCardIds], ['sub', event.subs, event.deletedSubIds]] as const) {
      for (const row of rows || []) {
        const key = `${prefix}:${row.id}`
        if (revision > Math.max(room.snapshotRevision, room.inputVersions.get(key) || 0)) { room.inputs.set(key, row); room.inputVersions.set(key, revision) }
      }
      for (const id of deleted || []) {
        const key = `${prefix}:${id}`
        if (revision > Math.max(room.snapshotRevision, room.inputVersions.get(key) || 0)) { room.inputs.delete(key); room.inputVersions.set(key, revision) }
      }
    }
    for (const [key, value] of [['state', event.sharedState], ['setup', event.setup]] as const) {
      if (value && revision > Math.max(room.snapshotRevision, room.inputVersions.get(key) || 0)) { room.inputs.set(key, value); room.inputVersions.set(key, revision) }
    }
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
    seedRoom(room, baseline)
    const remaining = await store.list(key)
    notifyRoom(room, { ...baseline, acceptServer: true, replayCommands: remaining })
    room.status.phase = remaining.length ? 'loading' : 'saved'
    room.status.error = ''
    room.status.conflict = undefined
    await pump(key, room)
  }

  // Explicitly chosen overwrite creates a NEW request. Never alter an
  // attempted request and retry it under its original command ID.
  async function keepLocal(gmId: string, side: 'H' | 'A') {
    const { room, key } = await roomFor(gmId, side)
    if (room.running) return
    const commands = await store.list(key)
    const head = commands[0]
    if (head?.blocked !== 'draft_conflict' || !['draft', 'setup'].includes(head.kind)) return
    const baseline = await room.read()
    if (baseline.status !== 'ok' || Number(baseline.roomEpoch || 0) !== head.request.roomEpoch) {
      room.status.error = '입력 회차가 변경되어 덮어쓸 수 없습니다. 서버 상태를 확인하세요.'
      return
    }
    if (room.status.conflict?.commandId !== head.commandId || room.status.conflict.revision !== Number(baseline.revision || 0)) {
      room.status.conflict = { commandId: head.commandId, local: describeInput(head), server: describeInput(head, baseline), revision: Number(baseline.revision || 0) }
      room.status.error = '서버 내용이 바뀌었습니다. 갱신된 비교 내용을 확인한 뒤 다시 선택하세요.'
      return
    }
    const commandId = crypto.randomUUID()
    const replacement: LiveCommand = { ...head, commandId, attempted: false, blocked: undefined,
      request: { ...head.request, commandId, baseRevision: Number(baseline.revision || 0), predecessorCommands: {} } }
    await store.update(replacement)
    notifyRoom(room, { pendingCommand: replacement })
    room.status.conflict = undefined
    room.status.phase = 'saving'
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

  return { submit, attach, observe, seed, observeDelta, changedRecords, bindSocket, acceptServer, keepLocal, flush, pending, readSnapshot }
}
