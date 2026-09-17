// =============================================================================
// players/{id} + players/{id}/contracts 읽기/쓰기 — manage/players/index.vue 전용.
// 스키마는 app/types/schema.ts의 PlayerDoc/ContractDoc이 정본이다.
//
// 계약(ContractDoc)엔 seasonId를 안 채운다(레거시 이관도 안 채움) — "이 시즌에 이 팀
// 소속이었나"는 from/to 날짜가 시즌 경계(8/1~다음해 5/31)와 겹치는지로 판단한다.
// 팀별 계약을 모아 조회할 방법이 contracts가 players 하위 서브컬렉션이라 collectionGroup
// 쿼리뿐이다 — teams.vue처럼 팀마다 알려진 경로를 직접 읽는 방식을 못 쓴다(선수는 몇 명일지
// 미리 알 수 없어서). firestore.indexes.json에 contracts.teamId를
// COLLECTION_GROUP 스코프로 올려둬야 이 쿼리가 동작한다.
// =============================================================================

import {
  collection, collectionGroup, deleteDoc, doc, getDoc, getDocs, query, setDoc, Timestamp, where, type Firestore,
} from 'firebase/firestore'
import type { ContractDoc, PlayerDoc } from '~/types/schema'

export type PlayerContract = ContractDoc & { id: string }
export type PlayerRow = PlayerDoc & { id: string }

/** "오늘"을 이 화면들이 쓰는 점(.) 표기로. coachContracts.ts와 같은 관례. */
export function todayLabel(): string {
  const d = new Date()
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`
}

/** 시즌 문자열("2026/27") -> 그 시즌의 날짜 경계(8/1~다음해 5/31), 점(.) 표기. */
export function seasonRange(season: string): { from: string; to: string } {
  const startYear = Number(season.split('/')[0])
  return { from: `${startYear}.08.01`, to: `${startYear + 1}.05.31` }
}

function overlapsRange(c: { from: string; to: string | null }, range: { from: string; to: string }): boolean {
  if (c.from > range.to) return false
  if (c.to !== null && c.to < range.from) return false
  return true
}

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size))
  return out
}

export function sortContracts(list: PlayerContract[]): PlayerContract[] {
  return [...list].sort((a, b) => {
    if (a.to === null && b.to !== null) return -1
    if (b.to === null && a.to !== null) return 1
    return b.from.localeCompare(a.from)
  })
}

/** teamIds 중 하나라도 season과 겹치는 계약을 가진 선수를, 그 계약들과 함께 돌려준다.
 *  같은 선수가 팀을 옮긴 적이 있으면 계약이 여러 개 실려 올 수 있다. */
export async function fetchPlayersByTeamsAndSeason(
  db: Firestore, teamIds: string[], season: string,
): Promise<{ player: PlayerRow; contracts: PlayerContract[] }[]> {
  if (!teamIds.length) return []
  const range = seasonRange(season)
  const snaps = await Promise.all(chunk(teamIds, 30).map(ids =>
    getDocs(query(collectionGroup(db, 'contracts'), where('teamId', 'in', ids))),
  ))
  const byPlayer = new Map<string, PlayerContract[]>()
  for (const snap of snaps) {
    for (const d of snap.docs) {
      const c = d.data() as ContractDoc
      if (!overlapsRange(c, range)) continue
      const playerId = d.ref.parent.parent?.id
      if (!playerId) continue
      const arr = byPlayer.get(playerId) ?? []
      arr.push({ id: d.id, ...c })
      byPlayer.set(playerId, arr)
    }
  }
  const playerIds = [...byPlayer.keys()]
  const playerSnaps = await Promise.all(playerIds.map(id => getDoc(doc(db, 'players', id))))
  const out: { player: PlayerRow; contracts: PlayerContract[] }[] = []
  for (const snap of playerSnaps) {
    if (!snap.exists()) continue
    out.push({ player: { id: snap.id, ...(snap.data() as PlayerDoc) }, contracts: sortContracts(byPlayer.get(snap.id) ?? []) })
  }
  return out
}

export async function fetchPlayerContracts(db: Firestore, playerId: string): Promise<PlayerContract[]> {
  const snap = await getDocs(collection(db, 'players', playerId, 'contracts'))
  return sortContracts(snap.docs.map(d => ({ id: d.id, ...(d.data() as ContractDoc) })))
}

export async function updatePlayerContract(db: Firestore, playerId: string, contractId: string, patch: Partial<ContractDoc>): Promise<void> {
  await setDoc(doc(db, 'players', playerId, 'contracts', contractId), { ...patch, updatedAt: Timestamp.now(), updatedBy: 'manage-ui' }, { merge: true })
}

export async function deletePlayerContract(db: Firestore, playerId: string, contractId: string): Promise<void> {
  await deleteDoc(doc(db, 'players', playerId, 'contracts', contractId))
}

/**
 * 이적 처리 — 기존에 진행중(to===null)이던 계약이 있으면 오늘 바로 전날로 닫고
 * (coachContracts.ts와 같은 관례), 새 팀으로 오늘부터 시작하는 계약을 만든다.
 * 파생 캐시(players.currentTeamId 등 + teams/{팀}/squad)도 함께 갱신한다 —
 * 이전 팀 squad 문서는 지우고 새 팀 squad 문서를 새로 만든다.
 */
export async function transferPlayer(
  db: Firestore, playerId: string, playerName: string,
  data: {
    teamId: string; no?: string; pos?: string; leagueId?: string
    transferType?: NonNullable<ContractDoc['transferType']>; from?: string
    fee?: number; currency?: string
  },
): Promise<void> {
  const existing = await fetchPlayerContracts(db, playerId)
  const current = existing.find(c => c.to === null)
  const today = todayLabel()
  // 새 계약 시작일 — 지정 안 하면(보통 진짜 이적 처리 때) 오늘. 선수를 처음 등록할 때는
  // 실제 입단일이 오늘이 아닐 수 있어 화면에서 직접 받는다.
  const startDate = data.from?.trim() || today

  if (current) {
    if (current.teamId === data.teamId) {
      // 같은 팀 안에서 등번호/포지션만 바뀌는 경우 — 새 계약을 만들 필요 없이 그대로 갱신한다.
      await updatePlayerContract(db, playerId, current.id, { no: data.no ?? null, pos: data.pos ?? null })
    } else {
      // 이전 계약은 새 계약 시작일에 닫는다(같은 날짜를 종료일로 써도 겹침으로 안 친다 —
      // fetchPlayersByTeamsAndSeason의 overlapsRange는 to를 배타적으로 보지 않으므로, 정말
      // 하루도 안 겹치게 하려면 나중에 "전날"로 다듬을 수 있지만 우선은 단순하게 같은 날로 둔다).
      await updatePlayerContract(db, playerId, current.id, { to: startDate })
      await deleteDoc(doc(db, 'teams', current.teamId, 'squad', playerId)).catch(() => {})
    }
  }

  let contractId = current?.teamId === data.teamId ? current.id : ''
  if (!contractId) {
    contractId = doc(collection(db, 'players', playerId, 'contracts')).id
    await setDoc(doc(db, 'players', playerId, 'contracts', contractId), {
      teamId: data.teamId, leagueId: data.leagueId ?? null,
      from: startDate, to: null, no: data.no ?? null, pos: data.pos ?? null,
      fromTeamId: current?.teamId ?? null, transferType: data.transferType ?? 'TRANSFER',
      fee: data.fee ?? null, currency: data.currency ?? null,
      createdAt: Timestamp.now(), createdBy: 'manage-ui', updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
    })
  }

  await setDoc(doc(db, 'players', playerId), {
    currentTeamId: data.teamId, currentNo: data.no ?? null, currentPos: data.pos ?? null,
    updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
  }, { merge: true })

  await setDoc(doc(db, 'teams', data.teamId, 'squad', playerId), {
    name: playerName, no: data.no ?? '', pos: data.pos ?? '', contractId, since: current?.teamId === data.teamId ? current.from : startDate,
  })
}

/**
 * 은퇴 처리 — transferPlayer와 달리 옮겨갈 팀이 없다. 진행중이던 계약을 닫고 스쿼드에서
 * 빼기만 한다. PlayerDoc은 active:false로 바꾼다(삭제 대신 비활성 — 원칙대로 과거 기록을
 * 고아로 만들지 않는다).
 */
export async function retirePlayer(db: Firestore, playerId: string, date?: string): Promise<void> {
  const existing = await fetchPlayerContracts(db, playerId)
  const current = existing.find(c => c.to === null)
  const endDate = date?.trim() || todayLabel()
  if (current) {
    await updatePlayerContract(db, playerId, current.id, { to: endDate })
    await deleteDoc(doc(db, 'teams', current.teamId, 'squad', playerId)).catch(() => {})
  }
  await setDoc(doc(db, 'players', playerId), {
    active: false, currentTeamId: null, currentNo: null, currentPos: null,
    updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
  }, { merge: true })
}

/**
 * "타 리그"로 이적 — 우리가 추적하는 팀이 아닌 곳으로 나가는 경우. retirePlayer와 계약/스쿼드
 * 처리는 같지만 active는 true로 유지한다(은퇴가 아니라 그냥 우리가 안 쫓는 곳으로 갔을 뿐,
 * 여전히 뛰고 있는 선수라서). 나중에 우리 리그 팀으로 다시 오면 그때 transferPlayer로 잡는다.
 */
export async function departToOtherLeague(db: Firestore, playerId: string, date?: string): Promise<void> {
  const existing = await fetchPlayerContracts(db, playerId)
  const current = existing.find(c => c.to === null)
  const endDate = date?.trim() || todayLabel()
  if (current) {
    await updatePlayerContract(db, playerId, current.id, { to: endDate })
    await deleteDoc(doc(db, 'teams', current.teamId, 'squad', playerId)).catch(() => {})
  }
  await setDoc(doc(db, 'players', playerId), {
    currentTeamId: null, currentNo: null, currentPos: null,
    updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
  }, { merge: true })
}
