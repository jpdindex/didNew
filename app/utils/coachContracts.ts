// =============================================================================
// coachContracts 컬렉션 읽기/쓰기 — manage/teams.vue(팀 상세의 감독 재임 이력)와
// manage/coaches/index.vue(감독 관리 화면)가 공유한다. 스키마는 app/types/schema.ts의
// CoachContractDoc이 정본이다.
// =============================================================================

import {
  collection, deleteDoc, doc, getDocs, query, setDoc, Timestamp, where, type Firestore,
} from 'firebase/firestore'
import type { CoachContractDoc } from '~/types/schema'

export type CoachStatus = CoachContractDoc['status']

/** 화면에서 다루는 형태 — 문서 ID를 필드로 들고 다닌다(수정/삭제 시 필요). */
export type CoachContract = Pick<CoachContractDoc, 'coachId' | 'coachName' | 'coachNameEn' | 'teamId' | 'from' | 'to' | 'status'> & { id: string }

/** "오늘" 날짜를 이 화면들에서 쓰는 점(.) 구분 표기로. */
export function todayLabel(): string {
  const d = new Date()
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`
}

/** dateLabel(예: "2026.09.15")에서 delta일 만큼 이동한 날짜를 같은 표기로 돌려준다. */
function addDaysLabel(dateLabel: string, delta: number): string {
  const [y, m, d] = dateLabel.split('.').map(Number)
  const date = new Date(y!, (m ?? 1) - 1, d ?? 1)
  date.setDate(date.getDate() + delta)
  return `${date.getFullYear()}.${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')}`
}

/** 재임기간을 날짜순(현재 재임 먼저, 그다음 최근순)으로 정렬한다. */
export function sortContracts(list: CoachContract[]): CoachContract[] {
  return [...list].sort((a, b) => {
    if (a.to === null && b.to !== null) return -1
    if (b.to === null && a.to !== null) return 1
    return b.from.localeCompare(a.from)
  })
}

export async function fetchCoachContracts(db: Firestore, teamId: string): Promise<CoachContract[]> {
  const snap = await getDocs(query(collection(db, 'coachContracts'), where('teamId', '==', teamId)))
  return sortContracts(snap.docs.map(d => ({ id: d.id, ...(d.data() as Omit<CoachContract, 'id'>) })))
}

export async function updateCoachContract(db: Firestore, id: string, patch: Partial<CoachContract>): Promise<void> {
  const { id: _drop, ...data } = patch as CoachContract
  await setDoc(doc(db, 'coachContracts', id), { ...data, updatedAt: Timestamp.now(), updatedBy: 'manage-ui' }, { merge: true })
}

export async function deleteCoachContract(db: Firestore, id: string): Promise<void> {
  await deleteDoc(doc(db, 'coachContracts', id))
}

/**
 * 새 감독을 "진행중"으로 추가한다. 그 팀에 이미 진행중이던 재임(들)이 있으면 새 감독
 * 부임일 바로 전날로 종료 처리한다 — 같은 날 두 명이 동시에 진행중으로 남지 않게 한다.
 */
export async function addCoachContract(
  db: Firestore,
  teamId: string,
  existing: CoachContract[],
  data: { coachName: string; coachNameEn: string; status: CoachStatus },
): Promise<void> {
  const today = todayLabel()
  const yesterday = addDaysLabel(today, -1)
  for (const c of existing) {
    if (c.to === null) await updateCoachContract(db, c.id, { to: yesterday })
  }
  const id = doc(collection(db, 'coachContracts')).id
  await setDoc(doc(db, 'coachContracts', id), {
    coachId: id, coachName: data.coachName, coachNameEn: data.coachNameEn,
    teamId, from: today, to: null, status: data.status,
    createdAt: Timestamp.now(), createdBy: 'manage-ui', updatedAt: Timestamp.now(), updatedBy: 'manage-ui',
  })
}
