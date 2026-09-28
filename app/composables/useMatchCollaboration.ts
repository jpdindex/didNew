import { collection, deleteField, doc, onSnapshot, serverTimestamp, setDoc, writeBatch } from 'firebase/firestore'
import type { DidRecord } from '~/utils/didLogic'
import type { MatchState } from '~/composables/useMatchState'

type ParticipantRole = 'primary' | 'assistant'

function sideFor(game: MatchState) {
  return game.team === 'away' ? 'A' : 'H'
}

function draftId(game: MatchState) {
  return `${game.matchId}_${sideFor(game)}`
}

function plain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

// 레코드에서 비워질 수 있는 선택 필드. merge 저장은 undefined 필드를 무시하므로,
// 로컬에서 지운 값(예: DAP 가 풀려 지운 playerId)이 서버에 남아 있다가 스냅샷으로 되살아난다.
// 로컬에 없는 필드는 deleteField() 로 서버에서도 지운다.
const OPTIONAL_RECORD_FIELDS = [
  'playerId', 'posX', 'posY', 'shootPosX', 'shootPosY', 'shootDspRange', 'isShot', 'edited',
] as const

/**
 * Firestore is the shared Draft transport. Every event record has its own document
 * so a primary analyst creating a new act cannot overwrite an assistant's player
 * assignment or correction. IndexedDB remains the local offline retry copy.
 */
export function useMatchCollaboration() {
  const { request } = useBackendApi()
  let stopRoot: (() => void) | undefined
  let stopRecords: (() => void) | undefined

  async function currentIdentity() {
    const { $auth, $authReady } = useNuxtApp()
    await $authReady
    return $auth.currentUser
  }

  async function join(game: MatchState, role: ParticipantRole) {
    const user = await currentIdentity()
    const response = await request<{ participants: Record<string, { name?: string; role?: ParticipantRole }> }>(
      `/api/v1/match-input/drafts/${encodeURIComponent(game.matchId)}/${sideFor(game)}/participants`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, displayName: user?.displayName || user?.email || undefined }) },
    )
    game.participantRole = role
    game.participantName = user?.displayName || user?.email || ''
    return response
  }

  function stop() {
    stopRoot?.()
    stopRecords?.()
    stopRoot = undefined
    stopRecords = undefined
  }

  async function start(game: MatchState, handlers: {
    applyState: (state: Partial<MatchState>) => void
    applyRecords: (records: DidRecord[]) => void
  }) {
    stop()
    const { $db, $auth, $authReady } = useNuxtApp()
    await $authReady
    if (!$auth.currentUser || !game.matchId) return false

    const root = doc($db, 'inputDrafts', draftId(game))
    const recordsRef = collection(root, 'records')
    stopRoot = onSnapshot(root, (snapshot) => {
      const shared = snapshot.data()?.sharedState
      if (shared && typeof shared === 'object') handlers.applyState(plain(shared as Partial<MatchState>))
    })
    stopRecords = onSnapshot(recordsRef, (snapshot) => {
      const records = snapshot.docs
        .map(item => item.data())
        .filter(item => !item.deleted)
        .map(item => plain(item) as DidRecord)
        .sort((a, b) => ((a.half || 'H1').localeCompare(b.half || 'H1')) || a.seconds - b.seconds || (a.seq ?? 0) - (b.seq ?? 0))
      handlers.applyRecords(records)
    })
    return true
  }

  async function syncState(game: MatchState) {
    const { $db, $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    // Identity/authority are per browser session, not shared game state. A
    // primary's snapshot must never turn an assistant into a primary locally.
    const { records, participantRole, participantName, recorderLevel, ...shared } = game
    const state = plain(shared)
    await setDoc(doc($db, 'inputDrafts', draftId(game)), {
      gmId: game.matchId,
      side: sideFor(game),
      sharedState: state,
      updatedAt: serverTimestamp(),
      updatedBy: $auth.currentUser.uid,
    }, { merge: true })
    return true
  }

  async function syncRecords(game: MatchState, records: DidRecord[]) {
    const { $db, $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    const batch = writeBatch($db)
    const root = doc($db, 'inputDrafts', draftId(game))
    for (const record of records) {
      const data = plain(record) as Record<string, unknown>
      const cleared = Object.fromEntries(OPTIONAL_RECORD_FIELDS.filter(key => data[key] === undefined).map(key => [key, deleteField()]))
      batch.set(doc(root, 'records', record.id), {
        ...cleared,
        ...data,
        half: record.half || 'H1',
        updatedAt: serverTimestamp(),
        updatedBy: $auth.currentUser.uid,
      }, { merge: true })
    }
    await batch.commit()
    return true
  }

  async function removeRecord(game: MatchState, recordId: string) {
    const { $db, $auth } = useNuxtApp()
    if (!$auth.currentUser || !game.matchId) return false
    // 삭제한 기록의 내용(시간·액트·선수 등)은 서버에 남기지 않는다. 문서 자체는 지우지 않고
    // deleted 표시만 남긴다 — 이 표시가 있어야 백엔드가 체크포인트(payload.records)에 남은
    // 옛 사본을 걸러내고, 다른 분석관의 동기화가 이 기록을 되살리지 않는다.
    await setDoc(doc($db, 'inputDrafts', draftId(game), 'records', recordId), {
      id: recordId, deleted: true, updatedAt: serverTimestamp(), updatedBy: $auth.currentUser.uid,
    })
    return true
  }

  return { join, start, stop, syncState, syncRecords, removeRecord }
}
