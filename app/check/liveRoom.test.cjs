// Run with: node --test app/check/liveRoom.test.cjs
// Exercise the real composables with deterministic transport and IndexedDB.
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function fakeIndexedDB() {
  const rows = new Map()
  let sequence = 0, initialized = false
  return {
    rows,
    open() {
      const request = {}
      const database = {
        createObjectStore() {}, close() {},
        transaction() {
          const transaction = {}
          transaction.objectStore = () => {
            const execute = action => {
              const result = {}
              queueMicrotask(() => {
                try { result.result = action(); transaction.oncomplete?.() }
                catch (error) { transaction.error = error; transaction.onabort?.() }
              })
              return result
            }
            return {
              add: row => execute(() => { const order = ++sequence; rows.set(order, structuredClone({ ...row, order })); return order }),
              put: row => execute(() => { rows.set(row.order, structuredClone(row)); return row.order }),
              getAll: () => execute(() => [...rows.values()].map(row => structuredClone(row))),
              delete: order => execute(() => rows.delete(order)),
            }
          }
          return transaction
        },
      }
      queueMicrotask(() => {
        request.result = database
        if (!initialized) { initialized = true; request.onupgradeneeded?.() }
        request.onsuccess?.()
      })
      return request
    },
  }
}

function load(file, globals, imports = {}) {
  const source = fs.readFileSync(path.resolve(__dirname, '..', file), 'utf8')
  const compiled = transformSync(source, { loader: 'ts', format: 'cjs', target: 'es2022', define: { 'import.meta.client': 'false' } }).code
  const module = { exports: {} }
  vm.runInNewContext(compiled, {
    module, exports: module.exports, require: name => {
      if (name in imports) return imports[name]
      throw new Error(`Unexpected import ${name}`)
    },
    console, Map, Set, Promise, Date, JSON, Error, Object, Number, String, Array,
    ...globals,
  }, { filename: file })
  return module.exports
}

function environment(transport, indexedDB = fakeIndexedDB(), nativeBatch = false) {
  const timers = []
  let serial = 0
  const auth = { currentUser: { uid: 'p' } }
  const globals = {
    indexedDB, AbortSignal,
    crypto: { randomUUID: () => `command-${++serial}` },
    setTimeout: callback => { timers.push(callback); return callback }, clearTimeout(callback) {
      const index = timers.indexOf(callback)
      if (index >= 0) timers.splice(index, 1)
    },
    useNuxtApp: () => ({ $auth: auth, $authReady: Promise.resolve() }),
    useBackendApi: () => ({ request: async (url, options = {}) => {
      // Existing single-command server fixtures also emulate the batch API.
      // Batch throughput tests use nativeBatch to inspect the actual envelope.
      if (!nativeBatch && url.endsWith('/commands')) {
        const results = []
        for (const command of JSON.parse(options.body).commands) {
          try {
            const result = await transport(url.replace(/\/commands$/, ''), { ...options, method: 'PUT', body: JSON.stringify(command.request) })
            results.push({ status: 'ack', ...result })
          } catch (error) {
            if (!error.status) throw error
            results.push({ status: 'error', commandId: command.request.commandId, code: error.code, httpStatus: error.status, message: error.message })
          }
        }
        return { results }
      }
      return await transport(url, options)
    } }),
  }
  const storage = load('utils/liveOutbox.ts', globals)
  const composable = load('composables/useLiveRoom.ts', globals, { vue: { reactive: value => value }, '~/utils/liveOutbox': storage })
  return { room: composable.useLiveRoom(), storage, indexedDB, auth, timers, globals }
}

async function settle() { for (let i = 0; i < 40; i++) await Promise.resolve() }

function socketEnvironment() {
  const sockets = [], timers = []
  class Socket {
    static OPEN = 1
    constructor() { this.readyState = 1; this.closes = 0; sockets.push(this) }
    send() {}
    close() { this.closes++; this.readyState = 3; this.onclose?.() }
  }
  const { useBackendApi } = load('composables/useBackendApi.ts', {
    WebSocket: Socket, useRuntimeConfig: () => ({ public: {} }), useState: () => ({ value: 'http://backend' }),
    useNuxtApp: () => ({ $auth: { currentUser: { uid: 'p', getIdToken: async () => 'token' } }, $authReady: Promise.resolve() }),
    window: { setTimeout: (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer }, clearTimeout: timer => { const index = timers.indexOf(timer); if (index >= 0) timers.splice(index, 1) } },
  })
  return { api: useBackendApi(), sockets, timers }
}

test('socket authentication tolerates a delayed ready response and closes a failed handshake', async () => {
  const env = socketEnvironment()
  const opened = env.api.openSocket('/live', () => {})
  await settle()
  assert.equal(env.timers[0].delay, 12_000)
  env.sockets[0].onmessage({ data: JSON.stringify({ type: 'ready' }) })
  const transport = await opened
  assert.equal(env.timers.length, 0)
  transport.close()
  const failed = env.api.openSocket('/live', () => {})
  const rejected = assert.rejects(failed, /초과/)
  await settle()
  env.timers[0].callback()
  await rejected
  assert.equal(env.sockets[1].closes, 1)
  env.sockets[1].onmessage({ data: JSON.stringify({ type: 'ready' }) })
  assert.equal(env.sockets[1].closes, 1)
})

test('rapid same-record edits retain their time, order and local previous values', async () => {
  const gate = deferred(), sent = []
  const env = environment(async (url, options) => {
    const body = JSON.parse(options.body)
    sent.push(body)
    if (sent.length === 1) await gate.promise
    return { commandId: body.commandId, savedRevision: sent.length }
  })
  await env.room.seed('m', 'H', { revision: 0, roomEpoch: 0 })
  const first = env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'r', playerId: 'one' }] } })
  await settle()
  const second = env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'r', playerId: 'two' }] } })
  await settle()
  gate.resolve()
  assert.deepEqual(await Promise.all([first, second]), [true, true])
  assert.equal(sent[1].baseRevision, 0)
  assert.equal(sent[1].edit.before['record:r'].playerId, 'one')
  assert.ok(sent[1].edit.sequence > sent[0].edit.sequence)
  assert.ok(sent[1].edit.changedAtMs >= sent[0].edit.changedAtMs)
})

test('server clock correction and edit metadata survive ACK loss and browser reload unchanged', async () => {
  const sent = [], db = fakeIndexedDB()
  const first = environment(async (url, options) => {
    sent.push(JSON.parse(options.body))
    throw new Error('saved but ACK lost')
  }, db)
  const serverTime = Date.now() + 60_000
  await first.room.seed('m', 'H', { revision: 5, roomEpoch: 0, serverTimeMs: serverTime })
  assert.equal(await first.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'r', playerId: 'mine' }] } }), false)
  assert.ok(Math.abs(sent[0].edit.changedAtMs - serverTime) < 1_000)
  const reloaded = environment(async (url, options) => {
    const body = JSON.parse(options.body); sent.push(body)
    return { commandId: body.commandId, savedRevision: 6 }
  }, db)
  await reloaded.room.seed('m', 'H', { revision: 5, roomEpoch: 0, serverTimeMs: serverTime + 10_000 })
  assert.equal(await reloaded.room.flush('m', 'H'), true)
  assert.deepEqual(sent[0], sent[1])
  assert.equal(db.rows.size, 0)
})

test('unrelated own ACK cannot rebase a queued edit over a peer change', async () => {
  const gate = deferred(), sent = []
  const env = environment(async (url, options) => {
    const body = JSON.parse(options.body)
    sent.push(body)
    if (sent.length === 1) await gate.promise
    return { commandId: body.commandId, savedRevision: 10 + sent.length }
  })
  await env.room.seed('m', 'H', { revision: 5, roomEpoch: 0 })
  const first = env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'other' }] } })
  await settle()
  const second = env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'peer-edited' }] } })
  await settle()
  gate.resolve()
  await Promise.all([first, second])
  assert.equal(sent[1].baseRevision, 5)
  assert.equal(sent[1].edit.before['record:peer-edited'], null)
})

test('persisted old conflict resumes with the original immutable command', async () => {
  const sent = [], env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 7, roomEpoch: 0, payload: { records: [{ id: 'r', playerId: 'peer' }] } }
    const body = JSON.parse(options.body); sent.push(body)
    return { commandId: body.commandId, savedRevision: 8 }
  })
  const storage = new env.storage.LiveCommandStore()
  await storage.add({ commandId: 'old', key: 'p:m:H', uid: 'p', gmId: 'm', side: 'H', kind: 'draft', blocked: 'draft_conflict', attempted: true,
    request: { commandId: 'old', baseRevision: 5, roomEpoch: 0, syncScope: 'records', payload: { records: [{ id: 'r', playerId: 'mine' }] } } })
  const attached = await env.room.attach('m', 'H', () => {})
  await env.room.flush('m', 'H')
  assert.equal(attached.status.phase, 'saved')
  assert.equal(sent[0].commandId, 'old')
  assert.equal(sent[0].baseRevision, 5)
  assert.equal(sent[0].payload.records[0].playerId, 'mine')
  assert.equal(env.indexedDB.rows.size, 0)
})

test('ignored older input acknowledges the canonical server values without dropping newer pending edits', async () => {
  let revision = 7, writes = 0
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision, roomEpoch: 0, payload: { records: [{ id: 'r', playerId: `peer-${revision}` }] } }
    writes++
    const body = JSON.parse(options.body)
    return { commandId: body.commandId, savedRevision: revision + 1, event: { type: 'delta', revision: revision + 1, roomEpoch: 0,
      records: [{ id: 'r', playerId: `peer-${revision}` }] } }
  })
  const storage = new env.storage.LiveCommandStore()
  await storage.add({ commandId: 'old', key: 'p:m:H', uid: 'p', gmId: 'm', side: 'H', kind: 'draft', blocked: 'draft_conflict', attempted: true,
    request: { commandId: 'old', baseRevision: 5, roomEpoch: 0, syncScope: 'records', payload: { records: [{ id: 'r', playerId: 'mine' }] } } })
  const attached = await env.room.attach('m', 'H', () => {})
  await env.room.flush('m', 'H')
  assert.equal(writes, 1)
  assert.equal(env.indexedDB.rows.size, 0)
  assert.equal(attached.status.phase, 'saved')
  assert.equal((await env.room.changedRecords('m', 'H', [{ id: 'r', playerId: 'peer-7' }])).length, 0)
})

test('socket batch ACK loss falls back to HTTP with identical persisted commands', async () => {
  let bridge, frame, httpBody
  const env = environment(async (url, options) => {
    assert.ok(url.endsWith('/commands'))
    httpBody = JSON.parse(options.body)
    return { results: httpBody.commands.map((item, i) => ({ status: 'ack', commandId: item.request.commandId, savedRevision: i + 1 })) }
  }, fakeIndexedDB(), true)
  await env.room.seed('m', 'H', { revision: 0, roomEpoch: 0 })
  const storage = new env.storage.LiveCommandStore()
  for (let i = 0; i < 12; i++) await storage.add({ commandId: `c${i}`, key: 'p:m:H', uid: 'p', gmId: 'm', side: 'H', kind: 'draft',
    request: { commandId: `c${i}`, baseRevision: 0, roomEpoch: 0, syncScope: 'records', payload: { records: [{ id: `r${i}` }] } } })
  bridge = await env.room.bindSocket('m', 'H', value => {
    frame = value
    queueMicrotask(() => bridge.detach(true))
  })
  assert.equal(await env.room.flush('m', 'H'), true)
  assert.equal(frame.command, 'batch')
  assert.deepEqual(JSON.parse(JSON.stringify(frame.commands)), httpBody.commands)
  assert.equal(env.indexedDB.rows.size, 0)
})

test('fifty mixed queued edits drain in three bounded HTTP batches without losing command IDs', async () => {
  let revision = 0
  const batches = [], accepted = []
  const env = environment(async (url, options) => {
    assert.ok(url.endsWith('/commands'))
    const commands = JSON.parse(options.body).commands
    batches.push(commands)
    return { results: commands.map(item => {
      accepted.push(item.request.commandId)
      return { status: 'ack', commandId: item.request.commandId, savedRevision: ++revision }
    }) }
  }, fakeIndexedDB(), true)
  const storage = new env.storage.LiveCommandStore()
  for (let i = 0; i < 50; i++) {
    const kind = i % 4 === 3 ? 'setup' : 'draft'
    const scope = i % 4 === 3 ? 'substitutions' : i % 4 === 2 ? 'cards' : 'records'
    await storage.add({ commandId: `c${i}`, key: 'p:m:H', uid: 'p', gmId: 'm', side: 'H', kind,
      request: { commandId: `c${i}`, baseRevision: 0, roomEpoch: 0, syncScope: scope,
        payload: { records: scope === 'records' ? [{ id: `r${Math.floor(i / 4)}`, playerId: i % 4 ? 'player' : null }] : [],
          cards: scope === 'cards' ? [{ id: `card${i}` }] : [] }, clientState: { subs: scope === 'substitutions' ? [{ id: `sub${i}` }] : [] } } })
  }
  await env.room.seed('m', 'H', { revision: 0, roomEpoch: 0 })
  assert.equal(await env.room.flush('m', 'H'), true)
  assert.deepEqual(batches.map(batch => batch.length), [20, 20, 10])
  assert.deepEqual(accepted, Array.from({ length: 50 }, (_, i) => `c${i}`))
  assert.equal(env.indexedDB.rows.size, 0)
})

test('lost batch ACK retains all commands and retry preserves every ID and request', async () => {
  let lost = true
  const batches = []
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 18, roomEpoch: 0 }
    const batch = JSON.parse(options.body).commands
    batches.push(batch)
    if (lost) { lost = false; throw new Error('batch committed; ACK lost') }
    return { results: batch.map((item, i) => ({ status: 'ack', commandId: item.request.commandId, savedRevision: i + 1 })) }
  }, fakeIndexedDB(), true)
  const storage = new env.storage.LiveCommandStore()
  for (let i = 0; i < 18; i++) await storage.add({ commandId: `c${i}`, key: 'p:m:H', uid: 'p', gmId: 'm', side: 'H', kind: 'draft',
    request: { commandId: `c${i}`, baseRevision: 0, roomEpoch: 0, syncScope: 'records', payload: { records: [{ id: `r${i}` }] } } })
  await env.room.seed('m', 'H', { revision: 0, roomEpoch: 0 })
  assert.equal(await env.room.flush('m', 'H'), false)
  assert.equal(env.indexedDB.rows.size, 18)
  assert.equal(await env.room.flush('m', 'H'), true)
  assert.deepEqual(batches[0], batches[1])
  assert.equal(env.indexedDB.rows.size, 0)
})

test('same-record and independent queued requests all drain without conflict', async () => {
  const sent = []
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 7, roomEpoch: 0, payload: { records: [] } }
    const body = JSON.parse(options.body)
    if (body.commands) return { results: body.commands.map((item, i) => ({ status: 'ack', commandId: item.request.commandId, savedRevision: 6 + i })) }
    sent.push(body)
    return { commandId: body.commandId, savedRevision: 8 }
  }, fakeIndexedDB(), true)
  const storage = new env.storage.LiveCommandStore()
  for (let i = 0; i < 3; i++) await storage.add({ commandId: `c${i}`, key: 'p:m:H', uid: 'p', gmId: 'm', side: 'H', kind: 'draft',
    request: { commandId: `c${i}`, baseRevision: 5, roomEpoch: 0, syncScope: 'records', payload: { records: [{ id: i < 2 ? 'same' : 'independent' }] } } })
  await env.room.seed('m', 'H', { revision: 5, roomEpoch: 0 })
  assert.equal(await env.room.flush('m', 'H'), true)
  assert.equal(env.indexedDB.rows.size, 0)
  assert.equal(await env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'later-independent' }] } }), true)
  assert.equal(sent[0].payload.records[0].id, 'later-independent')
  assert.equal(env.indexedDB.rows.size, 0)
})

test('rejected event-free state cannot block later ACTs or erase their queue', async () => {
  const sent = []
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { revision: 5, roomEpoch: 0 }
    const command = JSON.parse(options.body)
    sent.push(command)
    if (command.syncScope === 'state') throw Object.assign(new Error('primary only'), { status: 403, code: 'primary_required' })
    return { commandId: command.commandId, savedRevision: 6 }
  })
  await env.room.seed('m', 'H', { revision: 5, roomEpoch: 0 })
  const attached = await env.room.attach('m', 'H', () => {})
  assert.equal(await env.room.submit('m', 'H', 'draft', { syncScope: 'state', payload: { records: [] } }), false)
  assert.equal(await env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'a' }] } }), true)
  assert.equal(sent.length, 2)
  assert.equal(attached.status.phase, 'saved')
  assert.equal(attached.status.error, '')
  assert.equal(attached.status.notice, undefined)
  assert.equal(env.indexedDB.rows.size, 0)
})

test('persisted forbidden state from the old client is removed before replaying ACTs', async () => {
  const sent = []
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { revision: 5, roomEpoch: 0 }
    const command = JSON.parse(options.body); sent.push(command)
    return { commandId: command.commandId, savedRevision: 6 }
  })
  const storage = new env.storage.LiveCommandStore()
  await storage.add({ commandId: 'old-state', uid: 'p', key: 'p:m:H', gmId: 'm', side: 'H', kind: 'draft',
    attempted: true, blocked: 'primary_required', request: { commandId: 'old-state', syncScope: 'state', baseRevision: 5, roomEpoch: 0, payload: { records: [] } } })
  assert.equal(await env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'a' }] } }), true)
  assert.equal(sent.length, 1)
  assert.equal(sent[0].syncScope, 'records')
  assert.equal(env.indexedDB.rows.size, 0)
})

test('seeded online commands use socket ACKs without HTTP reads or writes', async () => {
  let http = 0, revision = 5, bridge
  const frames = []
  const env = environment(async () => { http++; throw new Error('unexpected HTTP') })
  await env.room.seed('m', 'H', { revision, roomEpoch: 0, payload: { records: [] } })
  bridge = await env.room.bindSocket('m', 'H', frame => {
    frames.push(frame)
    if (frame.command === 'batch') {
      queueMicrotask(() => bridge.receive({ type: 'batch_ack', commandId: frame.commandId,
        results: frame.commands.map(item => ({ status: 'ack', commandId: item.request.commandId, savedRevision: ++revision,
          event: { type: 'delta', revision, roomEpoch: 0, records: item.request.payload.records } })) }))
      return
    }
    queueMicrotask(() => bridge.receive({ type: 'mutation', commandId: frame.commandId, savedRevision: ++revision,
      revision, roomEpoch: 0, event: { type: 'delta', revision, roomEpoch: 0, records: frame.request.payload.records } }))
  })
  const attached = await env.room.attach('m', 'H', () => {})
  const first = env.room.submit('m', 'H', 'draft', { payload: { records: [{ id: 'one' }] } })
  const second = env.room.submit('m', 'H', 'draft', { payload: { records: [{ id: 'two' }] } })
  assert.equal(await first, true)
  assert.equal(await second, true)
  assert.equal(http, 0)
  assert.deepEqual(frames.flatMap(frame => frame.commands ? frame.commands.map(item => item.request.payload.records[0].id) : [frame.request.payload.records[0].id]), ['one', 'two'])
  assert.equal(attached.status.phase, 'saved')
  assert.equal(attached.status.pending, 0)
  assert.equal(env.indexedDB.rows.size, 0)
  assert.equal((await env.room.changedRecords('m', 'H', [{ id: 'one' }, { id: 'two' }])).length, 0)
})

test('socket ACK loss retries the same durable command over HTTP', async () => {
  let socketFrame, bridge, httpCommand
  const env = environment(async (url, options) => {
    assert.equal(options.method, 'PUT')
    httpCommand = JSON.parse(options.body)
    return { commandId: httpCommand.commandId, savedRevision: 6, revision: 6 }
  })
  await env.room.seed('m', 'H', { revision: 5, roomEpoch: 0 })
  bridge = await env.room.bindSocket('m', 'H', frame => {
    socketFrame = frame
    queueMicrotask(() => bridge.detach(true))
  })
  assert.equal(await env.room.submit('m', 'H', 'draft', { edit: 'one' }), true)
  assert.equal(httpCommand.commandId, socketFrame.commandId)
  assert.equal(httpCommand.baseRevision, socketFrame.request.baseRevision)
  assert.equal(env.indexedDB.rows.size, 0)
})

test('late baseline cannot replace records confirmed by a newer delta', async () => {
  const env = environment(async () => { throw new Error('unexpected HTTP') })
  await env.room.seed('m', 'H', { revision: 5, roomEpoch: 0, payload: { records: [{ id: 'r', playerId: 'old' }] } })
  await env.room.observeDelta('m', 'H', { revision: 6, roomEpoch: 0, records: [{ id: 'r', playerId: 'new' }] })
  await env.room.seed('m', 'H', { revision: 5, roomEpoch: 0, payload: { records: [{ id: 'r', playerId: 'old' }] } })
  assert.equal((await env.room.changedRecords('m', 'H', [{ id: 'r', playerId: 'new' }])).length, 0)
})

test('outbox keeps lost ACKs, retries the same ID, and preserves edit order', async () => {
  let revision = 0, fail = true
  const sent = [], receipts = new Map()
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision, roomEpoch: 0 }
    const command = JSON.parse(options.body)
    sent.push(command)
    if (!receipts.has(command.commandId)) receipts.set(command.commandId, ++revision)
    if (fail) { fail = false; throw new Error('ACK lost after commit') }
    return { commandId: command.commandId, savedRevision: receipts.get(command.commandId), revision }
  })
  const attached = await env.room.attach('m', 'H', () => {})
  assert.equal(await env.room.submit('m', 'H', 'draft', { label: 'first' }), false)
  assert.equal(env.indexedDB.rows.size, 1)
  assert.equal(attached.status.phase, 'offline')
  assert.equal(await env.room.submit('m', 'H', 'draft', { label: 'second' }), true)
  assert.deepEqual(sent.map(item => item.label), ['first', 'first', 'second'])
  assert.deepEqual(sent[0].edit, sent[1].edit)
  assert.equal(sent[0].commandId, sent[1].commandId)
  assert.equal(sent[0].baseRevision, sent[1].baseRevision)
  assert.equal(revision, 2)
  assert.equal(env.indexedDB.rows.size, 0)
  assert.equal(attached.status.phase, 'saved')
})

test('saving remains pending until a matching server ACK arrives', async () => {
  const ack = deferred()
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 7, roomEpoch: 0 }
    const command = JSON.parse(options.body)
    await ack.promise
    return { commandId: command.commandId, savedRevision: 8, revision: 8 }
  })
  const attached = await env.room.attach('m', 'H', () => {})
  const saving = env.room.submit('m', 'H', 'draft', {})
  await settle()
  assert.equal(attached.status.phase, 'saving')
  assert.equal(env.indexedDB.rows.size, 1)
  ack.resolve()
  assert.equal(await saving, true)
  assert.equal(attached.status.phase, 'saved')
})

test('mismatched ACK cannot remove a durable command', async () => {
  const env = environment(async (url, options = {}) => options.method
    ? { commandId: 'wrong', savedRevision: 1 } : { status: 'ok', revision: 0 })
  assert.equal(await env.room.submit('m', 'H', 'draft', {}), false)
  assert.equal(env.indexedDB.rows.size, 1)
})

test('conflict blocks replay until explicit adoption of server state', async () => {
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 2, roomEpoch: 0 }
    throw Object.assign(new Error('same input changed'), { status: 409, code: 'draft_conflict' })
  })
  const events = []
  const attached = await env.room.attach('m', 'H', event => events.push(event))
  assert.equal(await env.room.submit('m', 'H', 'draft', {}), false)
  assert.equal(attached.status.phase, 'conflict')
  assert.equal(env.indexedDB.rows.size, 1)
  assert.equal(env.timers.length, 0)
  await env.room.acceptServer('m', 'H')
  assert.equal(env.indexedDB.rows.size, 0)
  assert.ok(events.some(event => event.acceptServer))
})

test('failed head prevents later edits from being sent out of order', async () => {
  const sent = []
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 0 }
    sent.push(JSON.parse(options.body).label)
    throw new Error('network offline')
  })
  const result = await Promise.all([
    env.room.submit('m', 'H', 'draft', { label: 'one' }),
    env.room.submit('m', 'H', 'draft', { label: 'two' }),
  ])
  assert.deepEqual(result, [false, false])
  assert.ok(sent.length >= 1 && sent.every(edit => edit === 'one'))
  assert.equal(env.indexedDB.rows.size, 2)
})

function collaborationEnvironment(queued = [], snapshotReader) {
  let receive
  let receiveRoom
  const commands = []
  const recoveryTimers = []
  const intervals = []
  const socketState = { opens: 0, closes: 0, frames: [] }
  let now = 0
  const game = { matchId: 'm', team: 'home', records: [], cards: [], subs: [], participantRole: 'primary', lifecycleControl: true }
  const initial = { status: 'ok', gmId: 'm', side: 'H', revision: 5, roomEpoch: 0,
    payload: { gmId: 'm', side: 'H', formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', lineup: [], records: [{ id: 'r', half: 'H1', halfSeconds: 1, seq: 0, act: 'P', res: 'O', area: 1, playerId: 'server' }], cards: [] }, clientState: {} }
  const status = { phase: 'saved', revision: 5, roomEpoch: 0, pending: 0, participants: {} }
  const globals = {
    shallowRef: value => ({ value }),
    Date: class extends Date { static now() { return now } },
    setTimeout: (callback, delay) => { const timer = { callback, delay }; recoveryTimers.push(timer); return timer },
    clearTimeout: timer => { const index = recoveryTimers.indexOf(timer); if (index >= 0) recoveryTimers.splice(index, 1) },
    setInterval: (callback, delay) => { const timer = { callback, delay }; intervals.push(timer); return timer },
    clearInterval: timer => { const index = intervals.indexOf(timer); if (index >= 0) intervals.splice(index, 1) },
    useNuxtApp: () => ({ $auth: { currentUser: { uid: 'p' } }, $authReady: Promise.resolve() }),
    useBackendApi: () => ({ request: async () => initial, openSocket: async (url, callback, onClose) => {
      receive = callback
      socketState.opens++
      let closed = false
      return { close() { if (!closed) { closed = true; socketState.closes++; onClose?.() } }, send(frame) { socketState.frames.push(frame) } }
    } }),
    useLiveRoom: () => ({ pending: async () => queued, readSnapshot: async () => snapshotReader ? await snapshotReader(initial) : initial, observe() {}, seed() {}, observeDelta() {},
      bindSocket: async () => ({ receive: () => false, detach() {} }), attach: async (id, side, listener) => {
      receiveRoom = listener
      return { status, detach() {} }
    }, submit: async (gmId, side, kind, request) => {
      commands.push({ gmId, side, kind, request })
      return true
    } }),
  }
  const draft = { cloneState: value => structuredClone(value), payloadFromState: value => ({
    ...initial.payload, formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', lineup: [],
    homeScore: value.homeScore, awayScore: value.awayScore,
    records: value.records.map(record => ({ ...record, halfSeconds: record.seconds })),
    cards: value.cards.map(card => ({ id: card.id, playerId: card.player, half: card.half, halfSeconds: card.seconds, card: card.card })),
  }) }
  const formation = load('utils/formationLayout.ts', {})
  const { useMatchCollaboration } = load('composables/useMatchCollaboration.ts', globals, { '~/composables/useMatchDraft': draft, '~/utils/formationLayout': formation })
  const collaboration = useMatchCollaboration()
  const handlers = {
    applyState: state => Object.assign(game, state),
    applyRecords: records => { game.records = collaboration.mergeRemoteRecords(records, game.records) },
    applyCards: cards => { game.cards = cards },
    applySetup: setup => { game.subs = setup.subs; game.assigned = formation.assignmentsFromLineup(setup.formationKey, setup.lineup) },
  }
  return { game, initial, collaboration, handlers, commands, recoveryTimers, intervals, socketState, advance: ms => { now += ms }, receive: event => receive(event), receiveRoom: event => receiveRoom(event) }
}

test('idle input keeps its socket alive through heartbeats without ACT writes', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial); await settle()
  for (let i = 0; i < 120; i++) {
    env.advance(5_000)
    env.intervals.find(timer => timer.delay === 5_000).callback()
    env.receive({ type: 'pong' })
  }
  assert.equal(env.socketState.closes, 0)
  assert.equal(env.commands.length, 0)
  assert.equal(env.socketState.frames.filter(frame => frame.type === 'heartbeat').length, 120)
  env.collaboration.stop()
})

test('background timer suspension gets a heartbeat grace period, then a dead socket reconnects automatically', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial); await settle()
  const heartbeat = env.intervals.find(timer => timer.delay === 5_000)
  env.advance(60_000); heartbeat.callback()
  assert.equal(env.socketState.closes, 0)
  assert.equal(env.socketState.frames.at(-1).type, 'heartbeat')
  for (let i = 0; i < 7; i++) { env.advance(5_000); heartbeat.callback() }
  assert.equal(env.socketState.closes, 1)
  const retry = env.recoveryTimers.find(timer => timer.delay === 250)
  assert.ok(retry)
  retry.callback(); await settle()
  assert.equal(env.socketState.opens, 2)
  env.collaboration.stop()
})

test('transient snapshot failure preserves clock and records and retries online without duplicate reads', async () => {
  let reads = 0
  const env = collaborationEnvironment([], async initial => {
    if (++reads === 1) throw Object.assign(new Error('snapshot busy'), { status: 503, code: 'draft_snapshot_busy' })
    return { ...initial, revision: 6 }
  })
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.game.seconds = 99; env.game.clockStartedAt = 1234
  env.receive({ type: 'resync' }); env.receive({ type: 'resync' })
  await settle()
  assert.equal(reads, 1)
  assert.equal(env.game.records.length, 1)
  assert.equal(env.game.seconds, 99)
  assert.equal(env.game.clockStartedAt, 1234)
  const timer = env.recoveryTimers.find(timer => timer.delay === 500)
  assert.ok(timer)
  env.receive({ type: 'resync' }); await settle()
  assert.equal(reads, 1)
  env.advance(500); env.recoveryTimers.splice(env.recoveryTimers.indexOf(timer), 1); timer.callback(); await settle()
  assert.equal(reads, 2)
  env.collaboration.stop()
  assert.equal(env.recoveryTimers.length, 0)
})

test('shared snapshot reads coalesce and a failed baseline cannot discard queued edits', async () => {
  const gate = deferred()
  let reads = 0, writes = 0
  const env = environment(async (url, options = {}) => {
    if (options.method) { writes++; throw new Error('must not send before recovery') }
    reads++; return await gate.promise
  })
  const first = env.room.readSnapshot('m', 'H'), second = env.room.readSnapshot('m', 'H')
  const pending = env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'local' }] } })
  await settle()
  assert.equal(reads, 1)
  gate.reject(Object.assign(new Error('snapshot busy'), { status: 503, code: 'draft_snapshot_busy' }))
  const results = await Promise.allSettled([first, second, pending])
  assert.equal(results[0].status, 'rejected')
  assert.equal(results[1].status, 'rejected')
  assert.equal(await pending, false)
  assert.equal(env.indexedDB.rows.size, 1)
  assert.equal(writes, 0)
})

test('real page debounce sends assistant ACTs without lifecycle and skips empty state writes', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.game.participantRole = 'assistant'; env.game.lifecycleControl = false
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/DidInput.vue'), 'utf8')
  const fn = source.match(/function queueDraftSave\([\s\S]*?\n}/)[0]
  let timer
  const dirty = new Set()
  const context = vm.createContext({ game: { value: env.game }, records: { value: env.game.records },
    applyingRemoteDraft: false, disposed: false, draftReady: true, isEditMode: false,
    sharedDraftDirty: false, sharedScoreDirty: false, scoreEditRevision: 0, dirtyRecordIds: dirty, clearedRecordPlayerIds: new Set(), recordSavesInFlight: new Map(), draftSaveTimer: null,
    markLocalRecords: env.collaboration.markLocalRecords, saveToStore() {}, saveLocal: async () => true,
    syncState: env.collaboration.syncState, syncStateRecords: env.collaboration.syncStateRecords,
    setTimeout: fn => { timer = fn; return 1 }, clearTimeout() {} })
  vm.runInContext(transformSync(fn, { loader: 'ts', format: 'cjs' }).code, context)
  context.queueDraftSave(true); assert.equal(timer, undefined); await settle()
  assert.equal(env.commands.length, 0)
  dirty.add('r')
  context.queueDraftSave(true); timer(); await settle()
  assert.equal(env.commands.length, 1)
  assert.equal(env.commands[0].request.syncScope, 'records')
  assert.equal(env.commands[0].request.payload.records[0].id, 'r')
  env.collaboration.stop()
})

test('ordinary primary ACT edits omit state but goal changes still save their score with the record', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial); await settle()
  env.game.homeScore = 0; env.game.awayScore = 0
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/DidInput.vue'), 'utf8')
  const dirty = new Set(), homeScore = { value: 0 }, awayScore = { value: 0 }
  let timer
  const context = vm.createContext({ game: { value: env.game }, records: { value: env.game.records }, homeScore, awayScore, team: { value: 'home' },
    applyingRemoteDraft: false, disposed: false, draftReady: true, isEditMode: false,
    sharedDraftDirty: false, sharedScoreDirty: false, scoreEditRevision: 0, dirtyRecordIds: dirty, clearedRecordPlayerIds: new Set(), recordSavesInFlight: new Map(), draftSaveTimer: null,
    markLocalRecords: env.collaboration.markLocalRecords,
    saveToStore() { env.game.homeScore = homeScore.value; env.game.awayScore = awayScore.value }, saveLocal: async () => true,
    syncStateRecords: env.collaboration.syncStateRecords, setTimeout: callback => { timer = callback; return callback }, clearTimeout() {} })
  const functions = [source.match(/function queueDraftSave\([\s\S]*?\n}/)[0], source.match(/function syncOwnScore\([\s\S]*?\n}/)[0]].join('\n')
  vm.runInContext(transformSync(functions, { loader: 'ts', format: 'cjs' }).code, context)
  for (const [result, scope, score] of [['O', 'records', 0], ['GOAL', 'state_records', 1], ['X', 'state_records', 0]]) {
    env.game.records[0].res = result
    dirty.add('r'); context.queueDraftSave(); context.syncOwnScore(); timer(); await settle()
    assert.equal(env.commands.at(-1).request.syncScope, scope)
    assert.equal(env.commands.at(-1).request.payload.homeScore, score)
  }
  env.game.records = []; env.game.homeScore = 0
  await env.collaboration.removeRecord(env.game, 'r', true)
  assert.equal(env.commands.at(-1).request.syncScope, 'state_records')
  assert.deepEqual(Array.from(env.commands.at(-1).request.deletedRecordIds), ['r'])
  env.collaboration.stop()
})

test('prepared half navigates while the start ACK remains pending', async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/TeamSelection.vue'), 'utf8')
  const fn = source.match(/async function enterPreparedHalf\([\s\S]*?\n}/)[0]
  const ack = deferred()
  let navigations = 0, writes = 0
  const game = { value: { lifecycleControl: true, halfStatus: 'ready', seconds: 0, clockStartedAt: null } }
  const context = vm.createContext({ game, lifecycleBusy: { value: false }, setupSaved: { value: true },
    canStart: { value: true }, matchInfoEditMode: { value: false }, lifecycleError: { value: '' },
    preparedInput: { value: { draft: { status: 'ok', sharedState: {} } } },
    syncState: () => { writes++; return ack.promise }, saveLocal: async () => true,
    payloadFromState: game => ({ status: game.halfStatus }), cloneState: game => ({ ...game }),
    didInputQuery: () => ({}), navigateTo: async () => { navigations++ }, lifecycleErrorMessage: error => error.message })
  vm.runInContext(transformSync(fn, { loader: 'ts', format: 'cjs' }).code, context)
  await context.enterPreparedHalf('H1')
  assert.equal(navigations, 1)
  assert.equal(writes, 1)
  assert.equal(game.value.halfStatus, 'H1')
  assert.equal(context.preparedInput.value.draft.payload.status, 'H1')
  ack.resolve(true)
})

for (const primary of [false, true]) test(`finished-half editor stays idle across remote updates (${primary ? 'primary' : 'assistant'})`, async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  env.game.lifecycleControl = primary
  env.game.participantRole = primary ? 'primary' : 'assistant'
  const vueDir = fs.readdirSync(path.resolve(__dirname, '../../node_modules/.pnpm')).find(name => name.startsWith('vue@'))
  const vue = require(path.resolve(__dirname, '../../node_modules/.pnpm', vueDir, 'node_modules/vue'))
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/DidInput.vue'), 'utf8')
  const records = vue.ref(env.game.records), dirty = new Set(), timers = []
  const context = vm.createContext({ records, game: { value: env.game }, disposed: false, draftReady: false,
    applyingRemoteDraft: false, isEditMode: true, sharedDraftDirty: false, sharedScoreDirty: false, scoreEditRevision: 0,
    dirtyRecordIds: dirty, clearedRecordPlayerIds: new Set(), recordSavesInFlight: new Map(), draftSaveTimer: null,
    markLocalRecords: env.collaboration.markLocalRecords, saveToStore() { env.game.records = JSON.parse(JSON.stringify(records.value)) },
    saveLocal: async () => true, syncStateRecords: env.collaboration.syncStateRecords,
    setTimeout: callback => { timers.push(callback); return callback }, clearTimeout() {} })
  vm.runInContext(transformSync(source.match(/function queueDraftSave\([\s\S]*?\n}/)[0], { loader: 'ts', format: 'cjs' }).code, context)
  const stop = vue.watch(records, () => context.queueDraftSave(), { deep: true })
  for (let index = 0; index < 6; index++) {
    records.value = records.value.map(row => ({ ...row }))
    await vue.nextTick()
    if (index === 1) context.draftReady = true
  }
  assert.equal(timers.length, 0)
  assert.equal(env.commands.length, 0)
  dirty.add('r'); records.value[0].playerId = 'edited'
  await vue.nextTick(); timers.shift()(); await settle()
  assert.equal(env.commands.length, 1)
  assert.equal(env.commands[0].request.syncScope, 'records')
  assert.equal(env.commands[0].request.payload.records[0].playerId, 'edited')
  assert.equal(dirty.size, 0)
  await env.collaboration.syncStateRecords(env.game, [], [], false)
  assert.equal(env.commands.length, 1)
  stop(); env.collaboration.stop()
})

test('snapshot echo during a pending record save cannot enqueue the same edit again', async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/DidInput.vue'), 'utf8')
  const gate = deferred(), dirty = new Set(['r'])
  let timer, writes = 0
  const records = { value: [{ id: 'r', playerId: 'one' }] }
  const context = vm.createContext({ records, game: { value: {} }, disposed: false, draftReady: true,
    applyingRemoteDraft: false, isEditMode: true, sharedDraftDirty: false, sharedScoreDirty: false, scoreEditRevision: 0,
    dirtyRecordIds: dirty, clearedRecordPlayerIds: new Set(), recordSavesInFlight: new Map(), draftSaveTimer: null,
    markLocalRecords() {}, saveToStore() {}, saveLocal: async () => true,
    syncStateRecords: async () => { writes++; return gate.promise },
    setTimeout: callback => { timer = callback; return 1 }, clearTimeout() {} })
  vm.runInContext(transformSync(source.match(/function queueDraftSave\([\s\S]*?\n}/)[0], { loader: 'ts', format: 'cjs' }).code, context)
  context.queueDraftSave(); timer()
  records.value = records.value.map(row => ({ ...row }))
  context.queueDraftSave(); timer()
  assert.equal(writes, 1)
  gate.resolve(true); await settle()
  assert.equal(dirty.size, 0)
})

test('leaving a finished-half editor saves pending records without a lifecycle checkpoint', async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/DidInput.vue'), 'utf8')
  const writes = [], dirty = new Set(), records = { value: [{ id: 'r', playerId: 'edited' }] }
  let checkpoints = 0, navigations = 0
  const context = vm.createContext({ game: { value: { halfStatus: 'H2', clockStartedAt: null } }, records,
    isEditMode: true, paused: { value: true }, clock: { value: '16:50' }, half: { value: '후반' },
    timer: undefined, draftSaveTimer: undefined, editReturnStatus: 'H2_done',
    dirtyRecordIds: dirty, clearedRecordPlayerIds: new Set(),
    askConfirm: async () => true, clearTimeout() {}, clearInterval() {}, stopCollaboration() {}, saveToStore() {},
    saveLocal: async () => true, saveDraft: async () => { checkpoints++; return true },
    syncStateRecords: async (...args) => { writes.push(args); return true }, goTeamSelection: () => { navigations++ } })
  vm.runInContext(transformSync(source.match(/async function exitToLobby\([\s\S]*?\n}/)[0], { loader: 'ts', format: 'cjs' }).code, context)
  await context.exitToLobby()
  assert.equal(writes.length, 0); assert.equal(checkpoints, 0)
  dirty.add('r')
  await context.exitToLobby()
  assert.equal(writes.length, 1); assert.equal(writes[0][3], false)
  assert.equal(checkpoints, 0); assert.equal(navigations, 2)
})

test('a rejected duplicate setup is removed after a live server rejection', async () => {
  const setup = { formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', lineup: [], subs: [] }
  const env = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 5, roomEpoch: 0, inputSetup: setup }
    const body = JSON.parse(options.body)
    if (body.syncScope === 'setup') throw Object.assign(new Error('primary only'), { status: 403, code: 'primary_required' })
    return { commandId: body.commandId, savedRevision: 6 }
  })
  await env.room.seed('m', 'H', { revision: 5, roomEpoch: 0 })
  assert.equal(await env.room.submit('m', 'H', 'setup', { syncScope: 'setup', payload: setup, clientState: { subs: [] } }), false)
  assert.equal(await env.room.flush('m', 'H'), true)
  assert.equal(env.indexedDB.rows.size, 0)
})

test('forbidden checkpoint keeps ACT, card and substitution edits durable', async () => {
  for (const event of ['records', 'cards', 'subs']) {
    const setup = { formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', lineup: [], subs: [] }
    const env = environment(async () => ({ status: 'ok', revision: 5, roomEpoch: 0, inputSetup: setup }))
    const storage = new env.storage.LiveCommandStore()
    await storage.add({ commandId: 'old', uid: 'p', key: 'p:m:H', gmId: 'm', side: 'H', kind: 'draft',
      attempted: true, blocked: 'primary_required', request: { commandId: 'old', syncScope: 'checkpoint', roomEpoch: 0,
        payload: { ...setup, records: event === 'records' ? [{ id: 'r' }] : [], cards: event === 'cards' ? [{ id: 'c' }] : [] },
        clientState: { subs: event === 'subs' ? [{ id: 's', half: 'H2', seconds: 2, outPlayer: 'out', inPlayer: 'in' }] : [] } } })
    assert.equal(await env.room.flush('m', 'H'), false)
    assert.equal(env.indexedDB.rows.size, 1, event)
  }
})

test('lobby bootstrap, locked lineup and assistant changes do not auto-save setup', async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/TeamSelection.vue'), 'utf8')
  let writes = 0, timer
  const context = vm.createContext({ game: { value: { matchId: 'm', formationKey: '4-3-3', lifecycleControl: true,
    halfStatus: 'H2_done', formationChanges: [] } }, applyingRemoteSetup: false,
    bootstrapApplied: { value: false }, matchInfoEditable: { value: true },
    pendingSetupFingerprint: '', appliedSetupFingerprint: '', formationSaveTimer: null, setupEpoch: 0,
    currentSetupFingerprint: () => 'changed', persistCurrentSetup: async () => { writes++; return true },
    setTimeout: callback => { timer = callback; return 1 }, clearTimeout() {} })
  vm.runInContext(transformSync(source.match(/function recordFormationChange\([\s\S]*?\n}/)[0], { loader: 'ts', format: 'cjs' }).code, context)
  context.recordFormationChange(); assert.equal(timer, undefined)
  context.bootstrapApplied.value = true; context.matchInfoEditable.value = false
  context.recordFormationChange(); assert.equal(timer, undefined)
  context.matchInfoEditable.value = true
  context.recordFormationChange(); context.game.value.lifecycleControl = false; timer()
  assert.equal(writes, 0)
  context.game.value.lifecycleControl = true
  context.recordFormationChange(); timer()
  assert.equal(writes, 1)
  const env = collaborationEnvironment()
  env.game.lifecycleControl = false
  assert.equal(await env.collaboration.syncSetup(env.game), false)
  assert.equal(env.commands.length, 0)
  await env.collaboration.syncSetup(env.game, 'substitutions')
  assert.equal(env.commands.length, 1)
  assert.equal(env.commands[0].request.syncScope, 'substitutions')
})

for (const scope of ['setup', 'checkpoint']) for (const changed of [false, true]) {
  test(`legacy rejected ${scope} ${changed ? 'preserves a real edit' : 'removes an unchanged snapshot'}`, async () => {
    const baseline = { status: 'ok', revision: 5, roomEpoch: 0, inputSetup: { formationKey: '4-3-3',
      fieldSide: 'left', inputMode: '분석', lineup: [{ playerId: 'one', slot: 'gk', type: 'START', order: 1 }], subs: [] } }
    const sent = []
    const env = environment(async (url, options = {}) => {
      if (!options.method) return baseline
      const body = JSON.parse(options.body); sent.push(body)
      return { commandId: body.commandId, savedRevision: 6 }
    })
    const storage = new env.storage.LiveCommandStore()
    await storage.add({ commandId: 'old', uid: 'p', key: 'p:m:H', gmId: 'm', side: 'H',
      kind: scope === 'setup' ? 'setup' : 'draft', attempted: true, blocked: 'primary_required',
      request: { commandId: 'old', syncScope: scope, baseRevision: 5, roomEpoch: 0,
        payload: { ...baseline.inputSetup, lineup: [{ ...baseline.inputSetup.lineup[0], playerId: changed ? 'edited' : 'one' }], records: [], cards: [] }, clientState: { subs: [] } } })
    assert.equal(await env.room.submit('m', 'H', 'draft', { syncScope: 'records', payload: { records: [{ id: 'act' }] } }), true)
    assert.equal(await env.room.flush('m', 'H'), !changed)
    assert.equal(sent.length, 1)
    assert.equal(env.indexedDB.rows.size, changed ? 1 : 0)
  })
}

test('peer deltas merge while local input is pending, including reordered independent revisions', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.game.records[0].playerId = 'local-pending'
  env.collaboration.markLocalRecords(env.game.records)
  const event = (revision, id) => ({ type: 'mutation', event: { type: 'delta', gmId: 'm', side: 'H', roomEpoch: 0,
    revision, records: [{ ...env.initial.payload.records[0], id, playerId: 'peer' }] } })
  env.receive(event(7, 'peer-two'))
  env.receive(event(6, 'peer-one'))
  env.receive(event(6, 'peer-one'))
  assert.deepEqual(Array.from(env.game.records, record => record.id).sort(), ['peer-one', 'peer-two', 'r'])
  assert.equal(env.game.records.find(record => record.id === 'r').playerId, 'local-pending')
  env.collaboration.stop()
})

test('peer batch deltas keep every ACT and the final player edit through duplicate deliveries', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  const delta = (revision, id, playerId) => ({ type: 'delta', gmId: 'm', side: 'H', roomEpoch: 0, revision,
    records: [{ ...env.initial.payload.records[0], id, playerId }] })
  const frame = { type: 'mutation', event: { type: 'batch', events: [delta(6, 'new', null), delta(7, 'other', 'two'), delta(8, 'new', 'one')] } }
  env.receive(frame); env.receive(frame)
  assert.equal(env.game.records.length, 3)
  assert.equal(env.game.records.find(row => row.id === 'new').playerId, 'one')
  assert.equal(env.game.records.find(row => row.id === 'other').playerId, 'two')
  env.collaboration.stop()
})

test('solo pending ACT survives empty snapshots and late player responses', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.game.records[0].playerId = 'local-new'
  env.collaboration.markLocalRecords(env.game.records)
  env.receive({ type: 'draft', response: { ...env.initial, revision: 6, payload: { ...env.initial.payload, records: [] } } })
  assert.equal(env.game.records.length, 1)
  assert.equal(env.game.records[0].playerId, 'local-new')
  env.receive({ type: 'draft', response: { ...env.initial, revision: 4 } })
  env.receive({ type: 'draft', response: { ...env.initial, revision: 7 } })
  assert.equal(env.game.records[0].playerId, 'local-new')
  env.collaboration.stop()
})

test('solo pending card survives a full Draft with an older card list', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.game.cards = [{ id: 'c', player: 'one', half: 'H1', seconds: 2, card: 'Y' }]
  env.collaboration.markLocalCards(env.game)
  env.receive({ type: 'draft', response: { ...env.initial, revision: 6 } })
  assert.equal(env.game.cards.length, 1)
  assert.equal(env.game.cards[0].id, 'c')
  env.collaboration.stop()
})

test('an earlier ACK cannot acknowledge a later edit of the same ACT', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  function command(id, player) {
    return { commandId: id, kind: 'draft', request: { syncScope: 'state_records',
      payload: { ...env.initial.payload, records: [{ ...env.initial.payload.records[0], playerId: player }] } } }
  }
  const first = command('one', 'A'), second = command('two', 'B'), third = command('three', 'A')
  for (const edit of [first, second, third]) {
    env.game.records[0].playerId = edit.request.payload.records[0].playerId
    env.collaboration.markLocalRecords(env.game.records)
    env.receiveRoom({ pendingCommand: edit })
  }
  env.receiveRoom({ ...env.initial, revision: 6, payload: first.request.payload, ackCommand: first })
  env.receive({ type: 'draft', response: { ...env.initial, revision: 7, payload: second.request.payload } })
  assert.equal(env.game.records[0].playerId, 'A')
  env.receiveRoom({ ...env.initial, revision: 8, payload: third.request.payload, ackCommand: third })
  env.receive({ type: 'draft', response: { ...env.initial, revision: 9 } })
  assert.equal(env.game.records[0].playerId, 'server')
  env.collaboration.stop()
})

test('reload restores queued ACTs before the first server snapshot is applied', async () => {
  const queued = [{ commandId: 'offline-act', kind: 'draft', request: { roomEpoch: 0, syncScope: 'records',
    payload: { records: [{ id: 'offline', half: 'H1', halfSeconds: 2, seq: 1, act: 'P', res: 'O', area: 1 }] } } }]
  const env = collaborationEnvironment(queued)
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  assert.ok(env.game.records.some(record => record.id === 'offline'))
  assert.ok(env.game.records.some(record => record.id === 'r'))
  env.collaboration.stop()
})

test('remote player clearing is authoritative once local edits are acknowledged', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.receive({ type: 'draft', response: { ...env.initial, revision: 6,
    payload: { ...env.initial.payload, records: [{ ...env.initial.payload.records[0], playerId: null }] } } })
  assert.equal(env.game.records[0].playerId, undefined)
  env.collaboration.stop()
})

test('a fresh client replays its persisted command with the original ID and base revision', async () => {
  const sent = []
  const old = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 3, roomEpoch: 1 }
    sent.push(JSON.parse(options.body))
    throw new Error('connection dropped')
  })
  assert.equal(await old.room.submit('m', 'H', 'draft', { edit: 'offline' }), false)
  const fresh = environment(async (url, options = {}) => {
    if (!options.method) return { status: 'ok', revision: 3, roomEpoch: 1 }
    const command = JSON.parse(options.body)
    sent.push(command)
    return { commandId: command.commandId, savedRevision: 4, revision: 4 }
  }, old.indexedDB)
  await fresh.room.attach('m', 'H', () => {})
  assert.equal(await fresh.room.flush('m', 'H'), true)
  assert.equal(sent.length, 2)
  assert.deepEqual(sent[0], sent[1])
  assert.equal(fresh.indexedDB.rows.size, 0)
})

test('ACT, card, and substitution writes carry only their affected events', async () => {
  const env = collaborationEnvironment()
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.game.cards = [{ id: 'c', player: 'one', half: 'H1', seconds: 2, card: 'Y' }]
  env.game.subs = [{ id: 's', half: 'H1', seconds: 2, outPlayer: 'one', inPlayer: 'two' }]
  env.game.squads = { home: [{ biography: 'x'.repeat(100000) }], away: [] }
  await env.collaboration.syncStateRecords(env.game, env.game.records)
  await env.collaboration.syncCards(env.game)
  await env.collaboration.syncSetup(env.game, 'substitutions')
  const [act, card, sub] = env.commands.map(item => item.request)
  assert.equal(act.payload.records.length, 1)
  assert.equal(act.payload.cards.length, 0)
  assert.equal(act.clientState.records.length, 0)
  assert.equal(card.payload.records.length, 0)
  assert.equal(card.payload.cards.length, 1)
  assert.equal(sub.payload.records.length, 0)
  assert.equal(sub.payload.cards.length, 0)
  assert.equal(sub.clientState.subs.length, 1)
  assert.equal(act.clientState.squads, undefined)
  assert.equal(sub.clientState.squads, undefined)
  assert.ok(JSON.stringify(act).length < 10000)
  env.collaboration.stop()
})

test('pending substitution survives an older setup snapshot', async () => {
  const env = collaborationEnvironment()
  env.initial.inputSetup = { formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', lineup: [{ playerId: 'one', type: 'START', slot: 'gk', order: 1 }], subs: [], revision: 1 }
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.game.subs = [{ id: 's', half: 'H1', seconds: 2, outPlayer: 'one', inPlayer: 'two' }]
  await env.collaboration.syncSetup(env.game, 'substitutions')
  env.receive({ type: 'setup', gmId: 'm', side: 'H', setup: { ...env.initial.inputSetup, revision: 2 } })
  assert.equal(env.game.subs.length, 1)
  assert.equal(env.game.subs[0].id, 's')
  env.collaboration.stop()
})

test('another analyst checkpoint cannot replace route identity or grant primary authority', () => {
  const globals = { useBackendApi: () => ({ request() {} }), useLiveRoom: () => ({}) }
  const { useMatchDraft } = load('composables/useMatchDraft.ts', globals, {
    '~/utils/formationLayout': { assignmentsFromLineup: () => ({}), lineupOrderForSlot: () => 0 },
  })
  const game = { matchId: 'm', team: 'home', participantRole: 'assistant', lifecycleControl: false,
    participantName: 'Assistant', recorderLevel: 'basic', squads: { home: [], away: [] }, assigned: {}, cards: [], subs: [], records: [] }
  const checkpoint = { ...game, matchId: 'different', team: 'away', participantRole: 'primary', lifecycleControl: true, recorderLevel: 'advanced' }
  const payload = { gmId: 'm', side: 'H', formationKey: '4-3-3', lineup: [], records: [], cards: [],
    inputMode: '분석', fieldSide: 'left', homeScore: 0, awayScore: 0, status: 'H1', halves: { H1: { seconds: 1 }, H2: { seconds: 0 } } }
  useMatchDraft().hydrate(game, payload, checkpoint)
  assert.equal(game.matchId, 'm')
  assert.equal(game.team, 'home')
  assert.equal(game.participantRole, 'assistant')
  assert.equal(game.lifecycleControl, false)
  assert.equal(game.recorderLevel, 'basic')
})

test('empty setup cannot erase players or block the subsequent populated setup revision', async () => {
  const env = collaborationEnvironment()
  const lineup = [{ playerId: 'one', type: 'START', slot: 'gk', order: 1 }, { playerId: 'two', type: 'BENCH', slot: 'b0', order: 1 }]
  env.initial.inputSetup = { formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', lineup, subs: [], revision: 1 }
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  env.receive({ type: 'setup', gmId: 'm', side: 'H', setup: { ...env.initial.inputSetup, lineup: [], revision: 99 } })
  assert.equal(env.game.assigned.gk, 'one')
  assert.equal(env.game.assigned.b0, 'two')
  env.receive({ type: 'setup', gmId: 'm', side: 'H', setup: { ...env.initial.inputSetup, lineup: [{ ...lineup[0], playerId: 'corrected' }, lineup[1]], revision: 2 } })
  assert.equal(env.game.assigned.gk, 'corrected')
  env.collaboration.stop()
})

test('durable Draft lineup restores a cleared screen when setup lineup is empty', async () => {
  const env = collaborationEnvironment()
  env.initial.payload.lineup = [{ playerId: 'one', type: 'START', slot: 'gk', order: 1 }, { playerId: 'two', type: 'BENCH', slot: 'b0', order: 1 }]
  env.initial.inputSetup = { formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', lineup: [], subs: [], revision: 1 }
  await env.collaboration.start(env.game, env.handlers, env.initial)
  await settle()
  assert.equal(env.game.assigned.gk, 'one')
  assert.equal(env.game.assigned.b0, 'two')
  env.collaboration.stop()
})

test('both pages retain starter and bench choices when an empty setup arrives', () => {
  const formation = load('utils/formationLayout.ts', {})
  const lineup = [{ playerId: 'gk', type: 'START', slot: 'gk', order: 1 },
    ...Array.from({ length: 10 }, (_, index) => ({ playerId: `p${index}`, type: 'START', slot: `o${index}`, order: index + 1 })),
    { playerId: 'bench', type: 'BENCH', slot: 'b0', order: 1 }]
  for (const file of ['DidInput.vue', 'TeamSelection.vue']) {
    const source = fs.readFileSync(path.resolve(__dirname, '../pages', file), 'utf8')
    const fn = source.match(/function applyInputSetup\([\s\S]*?\n}/)[0]
    const compiled = transformSync(fn, { loader: 'ts', format: 'cjs' }).code
    const game = { value: { formationKey: '4-3-3', assigned: {}, subs: [], side: 'left', inputMode: '분석' } }
    const context = vm.createContext({ game, ...formation, HALF_ORDER: { H1: 0, H2: 1 }, appliedSetupRevision: -1,
      pendingSetupFingerprint: '', setupFingerprint: value => JSON.stringify(value), nextTick: callback => callback() })
    vm.runInContext(compiled, context)
    context.applyInputSetup({ formationKey: '4-3-3', lineup, subs: [], fieldSide: 'left', inputMode: '분석', revision: 1 })
    const before = JSON.stringify(game.value.assigned)
    context.applyInputSetup({ formationKey: '4-4-2', lineup: [], subs: [], fieldSide: 'right', inputMode: '분석', revision: 99, forceApply: true })
    assert.equal(JSON.stringify(game.value.assigned), before, file)
    assert.equal(game.value.formationKey, '4-3-3', file)
    assert.equal(Object.keys(game.value.assigned).filter(id => !id.startsWith('b')).length, 11)
    assert.equal(Object.keys(game.value.assigned).filter(id => id.startsWith('b')).length, 1)
  }
})

function halfEntryEnvironment({ baseline = false, delayed = false, differentPeerSetup = false } = {}) {
  const vueDir = fs.readdirSync(path.resolve(__dirname, '../../node_modules/.pnpm')).find(name => name.startsWith('vue@'))
  const vue = require(path.resolve(__dirname, '../../node_modules/.pnpm', vueDir, 'node_modules/vue'))
  const formation = load('utils/formationLayout.ts', {})
  const source = fs.readFileSync(path.resolve(__dirname, '../pages/TeamSelection.vue'), 'utf8')
  const game = vue.ref({ matchId: 'm', team: 'home', halfStatus: 'H1_done', participantRole: 'manager', lifecycleControl: true,
    formationKey: '4-3-3', side: 'left', inputMode: '분석', assigned: { gk: 'keeper', o7: 'in', b0: 'out' },
    squads: { home: [], away: [] }, grassPattern: 'stripes', grassLines: 6,
    // Deliberately different key order from a normalized server substitution.
    subs: [{ inPlayer: 'in', outPlayer: 'out', seconds: 42, half: 'H1', id: 's' }] })
  const setup = { formationKey: '4-3-3', fieldSide: 'left', inputMode: '분석', revision: 2, lineupIsBaseline: true,
    lineup: [{ playerId: 'keeper', slot: 'gk', type: 'START', order: 1 },
      { playerId: baseline ? 'out' : 'in', slot: 'o7', type: 'START', order: 1 },
      { playerId: baseline ? 'in' : 'out', slot: 'b0', type: 'BENCH', order: 1 }],
    subs: [{ id: 's', half: 'H1', seconds: 42, outPlayer: 'out', inPlayer: 'in' }] }
  const gate = deferred()
  let navigations = 0
  const context = vm.createContext({ ...vue, ...formation, game, HALF_ORDER: { H1: 0, H2: 1 },
    appliedSetupRevision: -1, pendingSetupFingerprint: '', appliedSetupFingerprint: '', applyingRemoteSetup: false,
    lifecycleBusy: vue.ref(false), lifecycleError: vue.ref(''), bootstrapApplied: vue.ref(true), canStart: vue.ref(true), matchInfoEditMode: vue.ref(false),
    preparedInput: vue.ref(null), savedEntryFingerprint: vue.ref(''), requestedRole: 'primary', formationSaveTimer: undefined,
    preloadRouteComponents: async () => {}, clearTimeout() {},
    ensureLobbyHistory: async () => {},
    ensureLobbyParticipant: async () => { game.value.participantRole = 'primary'; game.value.lifecycleControl = true },
    persistCurrentSetup: async () => {
      if (delayed) await gate.promise
      context.applyInputSetup({ ...setup, forceApply: true })
      return true
    },
    saveDraft: async () => true,
    enterCollaboration: async () => ({ gmId: 'm', side: 'H', participant: { role: 'primary', control: true },
      draft: { status: 'ok', payload: { status: 'H1_done' }, sharedState: {} },
      inputSetup: { H: differentPeerSetup ? { ...setup, revision: 3, lineup: [setup.lineup[0], { ...setup.lineup[1], playerId: 'peer' }, setup.lineup[2]] } : setup, A: null } }),
    syncState: async () => true, saveLocal: async () => true, payloadFromState: game => ({ status: game.halfStatus }), cloneState: game => ({ ...game }),
    didInputQuery: () => ({}), navigateTo: async () => { navigations++ },
  })
  const functions = ['setupFingerprint', 'currentSetupFingerprint', 'applyInputSetup', 'entrySetupSnapshot', 'lifecycleErrorMessage', 'saveEntrySetup', 'enterPreparedHalf']
    .map(name => source.match(new RegExp(`(?:async )?function ${name}\\([\\s\\S]*?\\n}`))[0]).join('\n')
  vm.runInContext(transformSync(functions, { loader: 'ts', format: 'cjs' }).code, context)
  const declarations = source.slice(source.indexOf('const entrySetupFingerprint ='), source.indexOf('function lifecycleErrorMessage'))
  vm.runInContext(transformSync(declarations, { loader: 'ts', format: 'cjs' }).code, context)
  return { context, game, gate, ready: () => vm.runInContext('setupSaved.value', context), navigations: () => navigations }
}

for (const baseline of [false, true]) test(`second-half preparation saves once and enters after ${baseline ? 'baseline' : 'already-applied'} substitution ACK`, async () => {
  const env = halfEntryEnvironment({ baseline })
  const before = env.context.entrySetupSnapshot()
  await env.context.saveEntrySetup()
  assert.equal(env.context.lifecycleError.value, '', `${before}\n${env.context.entrySetupSnapshot()}\nedit ${vm.runInContext('entryEditRevision', env.context)}`)
  assert.equal(env.ready(), true)
  assert.equal(env.game.value.assigned.o7, 'in')
  assert.equal(env.game.value.assigned.b0, 'out')
  await env.context.enterPreparedHalf('H2')
  assert.equal(env.navigations(), 1)
  assert.equal(env.game.value.halfStatus, 'H2')
})

test('user changes during slow setup save require confirmation even when changed back', async () => {
  const env = halfEntryEnvironment({ delayed: true })
  const saving = env.context.saveEntrySetup()
  await settle()
  env.game.value.assigned.o7 = 'edited'
  env.game.value.assigned.o7 = 'in'
  env.gate.resolve()
  await saving
  assert.match(env.context.lifecycleError.value, /세팅이 변경/)
  assert.equal(env.ready(), false)
  assert.equal(env.context.preparedInput.value, null)
})

test('a genuinely different peer setup during preparation cannot silently allow half entry', async () => {
  const env = halfEntryEnvironment({ differentPeerSetup: true })
  await env.context.saveEntrySetup()
  assert.match(env.context.lifecycleError.value, /세팅이 변경/)
  assert.equal(env.ready(), false)
  assert.equal(env.navigations(), 0)
})
