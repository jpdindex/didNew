import { computeAttackPaths, computeBap, type DidRecord } from '~/utils/didLogic'

type Half = 'H1' | 'H2' | 'H3' | 'H4'

export type InputPreviewFlag = {
  isTap: boolean
  isDap: boolean
  isDapSuccess: boolean
  isShot: boolean
  isGoal: boolean
}

export type InputPreviewPath = {
  id: string
  recordIds: string[]
  type: 'UPP' | 'UTP' | 'DTP' | 'STP'
  ttp: boolean
}

export type InputPreviewResult = {
  source: 'server' | 'fallback'
  kpis: Record<string, number>
  flags: Map<string, InputPreviewFlag>
  paths: InputPreviewPath[]
}

type ServerPreview = {
  kpis: Record<string, number>
  flags: Record<string, InputPreviewFlag>
  paths: InputPreviewPath[]
}

type CachedPreview = { fingerprint: string; result: InputPreviewResult }

const PREVIEW_DEBOUNCE_MS = 120

function scopeKey(half: Half | undefined, scope = '') {
  return `${scope || 'default'}:${half ?? 'all'}`
}

function scopedRecords(records: DidRecord[], half: Half | undefined) {
  return half ? records.filter(record => (record.half ?? 'H1') === half) : records
}

function fingerprint(records: DidRecord[], half: Half | undefined, scope = '') {
  return JSON.stringify({
    scope,
    half: half ?? 'all',
    records: scopedRecords(records, half).map(record => ({
      id: record.id, half: record.half ?? 'H1', seconds: record.seconds, seq: record.seq ?? 0,
      act: record.act, res: record.res, area: record.area,
      posX: record.posX ?? null, posY: record.posY ?? null,
      shootPosX: record.shootPosX ?? null, shootPosY: record.shootPosY ?? null,
      shootDspRange: record.shootDspRange ?? null, isShot: record.isShot ?? null,
      playerId: record.playerId ?? null,
    })),
  })
}

function fallbackPreview(records: DidRecord[], half: Half | undefined): InputPreviewResult {
  const source = scopedRecords(records, half)
  // Python calculates each half independently. Preserve that boundary in the
  // offline fallback too, so H1's final event can never chain into H2.
  const calculations = (['H1', 'H2', 'H3', 'H4'] as Half[])
    .map(currentHalf => {
      const halfRecords = source.filter(record => (record.half ?? 'H1') === currentHalf)
      return { records: halfRecords, calculation: computeAttackPaths(halfRecords, { closeTrailing: true }) }
    })
    .filter(item => item.records.length > 0)
  const flags = new Map<string, InputPreviewFlag>()
  let tap = 0
  let dap = 0
  let dapSuccess = 0
  let shot = 0
  let goal = 0
  let ownGoal = 0
  let dtb = 0
  let dtm = 0
  let dta = 0
  let dts = 0
  const paths = calculations.flatMap(({ records: halfRecords, calculation }) => {
    for (const record of halfRecords) {
      const flag = calculation.flags.get(record.id)
      if (!flag) continue
      flags.set(record.id, {
        isTap: flag.isTap,
        isDap: flag.isDap,
        isDapSuccess: flag.isDapS,
        isShot: flag.isSht,
        isGoal: flag.isGol,
      })
      tap += Number(flag.isTap)
      dap += Number(flag.isDap)
      dapSuccess += Number(flag.isDapS)
      shot += Number(flag.isSht)
      goal += Number(flag.isGol)
      ownGoal += Number(flag.isGol && record.playerId === 'OWN')
      dtb += Number(flag.isDtb)
      dtm += Number(flag.isDtm)
      dta += Number(flag.isDta)
      dts += Number(flag.isDts)
    }
    return calculation.paths.map(path => ({
      id: path.gtId,
      recordIds: path.recordIds,
      type: path.ptype,
      ttp: path.ttp,
    }))
  })
  return {
    source: 'fallback',
    kpis: {
      TAP: tap,
      DAP: dap,
      TTP: paths.filter(path => path.ttp).length,
      DTP: paths.filter(path => path.type === 'DTP').length,
      BAP: calculations.reduce((count, item) => count + computeBap(item.records).length, 0),
      DTB: dtb,
      DTM: dtm,
      DTA: dta,
      DTS: dts,
      SHOT: shot,
      ASR: dap ? dapSuccess / dap : 0,
      SSR: shot ? (goal - ownGoal) / shot : 0,
      GOAL: goal,
      OG: ownGoal,
    },
    flags,
    paths,
  }
}

function requestRecords(records: DidRecord[]) {
  return records.map((record, index) => ({
    id: record.id,
    half: record.half ?? 'H1',
    halfSeconds: record.seconds,
    seq: record.seq ?? index,
    act: record.act,
    res: record.res,
    area: record.area,
    posX: record.posX ?? null,
    posY: record.posY ?? null,
    shootPosX: record.shootPosX ?? null,
    shootPosY: record.shootPosY ?? null,
    shootDspRange: record.shootDspRange ?? null,
    isShot: record.isShot ?? null,
    playerId: record.playerId ?? null,
  }))
}

/**
 * Server preview is authoritative whenever its fingerprint matches the screen.
 * The local calculation never writes RAW; it only keeps input responsive while
 * a request is pending or the browser is offline.
 */
export function useInputPreview() {
  const { request } = useBackendApi()
  const cached = reactive<Record<string, CachedPreview | undefined>>({})
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const requestVersions = new Map<string, number>()

  function preview(records: DidRecord[], half?: Half, scope = ''): InputPreviewResult {
    const key = scopeKey(half, scope)
    const currentFingerprint = fingerprint(records, half, scope)
    const server = cached[key]
    return server?.fingerprint === currentFingerprint ? server.result : fallbackPreview(records, half)
  }

  function schedule(records: DidRecord[], half?: Half, scope = '') {
    const key = scopeKey(half, scope)
    const currentRecords = scopedRecords(records, half)
    const currentFingerprint = fingerprint(records, half, scope)
    const nextVersion = (requestVersions.get(key) ?? 0) + 1
    requestVersions.set(key, nextVersion)
    const timer = timers.get(key)
    if (timer) clearTimeout(timer)
    if (!currentRecords.length) {
      cached[key] = undefined
      return
    }
    timers.set(key, setTimeout(() => {
      void request<ServerPreview>('/api/v1/match-input/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ records: requestRecords(currentRecords), ...(half ? { half } : {}) }),
      }).then(response => {
        // A late response must not replace a newer preview for the same half.
        if (requestVersions.get(key) !== nextVersion) return
        cached[key] = {
          fingerprint: currentFingerprint,
          result: {
            source: 'server',
            kpis: response.kpis,
            flags: new Map(Object.entries(response.flags)),
            paths: response.paths,
          },
        }
      }).catch(() => {
        // IndexedDB and the local calculation remain available offline.
      })
    }, PREVIEW_DEBOUNCE_MS))
  }

  onScopeDispose(() => {
    for (const timer of timers.values()) clearTimeout(timer)
  })

  return { preview, schedule }
}
