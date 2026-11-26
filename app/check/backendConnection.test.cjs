const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { transformSync } = require('../../node_modules/.pnpm/node_modules/esbuild')

test('changed runtime backend replaces a stale resolved remote address', async () => {
  const source = fs.readFileSync('app/composables/useBackendApi.ts', 'utf8')
  const body = source.slice(source.indexOf('  async function resolveBackendUrl()'), source.indexOf('  async function requestResponse'))
  const backendUrl = { value: 'https://didnew.onrender.com' }
  const requested = []
  const context = vm.createContext({
    config: { public: { backendUrl: 'http://127.0.0.1:8000/' } }, backendUrl,
    trimTrailingSlash: value => value.replace(/\/$/, ''), AbortController,
    window: { setTimeout, clearTimeout },
    fetch: async url => { requested.push(url); return { ok: true } },
  })
  vm.runInContext(transformSync(body + 'globalThis.resolve = resolveBackendUrl;', { loader: 'ts' }).code, context)
  assert.equal(await context.resolve(), 'http://127.0.0.1:8000')
  assert.equal(backendUrl.value, 'http://127.0.0.1:8000')
  assert.deepEqual(requested, ['http://127.0.0.1:8000/health'])
  await context.resolve()
  assert.equal(requested.length, 1)
})
