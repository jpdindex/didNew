// =============================================================================
// 카드(경고·퇴장) 관련 로직
//
// DidInput.vue/TeamSelection.vue 가 공유한다. 지금은 useMatchState 의 메모리
// 배열(CardRecord[])을 그대로 쓰지만, 여기 함수들은 CardRecord[] 형태만 맞으면
// 동작하므로 나중에 Firestore 구독 데이터(matches/{gm_id}/recordings/{H|A}/cards)로
// 갈아끼워도 호출부 입력만 바뀔 뿐 이 파일은 그대로 재사용된다.
// =============================================================================

import type { Half } from '~/types/schema'

/** 레거시 ff_game_card 대응. Firestore CardDoc(schema.ts)의 클라이언트 스크래치 형태. */
export interface CardRecord {
  half: Half
  seconds: number
  /** players(HOME_SQUAD/AWAY_SQUAD) 배열의 인덱스 */
  player: number
  card: 'Y' | 'R'
}

/**
 * 선수별 카드 목록 맵을 만든다.
 * queued(아직 Submit 안 한 초안)는 판정(isSentOff 등)에 즉시 반영돼야 화면이 어긋나지
 * 않으므로 committed 와 합쳐서 계산한다 — 단, 그 초안은 패널을 닫을 때(Cancel/토글닫기/
 * 다른 패널로 전환) 반드시 비워야 한다. 비우지 않으면 제출도 안 한 카드가 선수를
 * 계속 그라운드에서 빼놓는 상태로 남는다.
 */
export function groupCardsByPlayer(committed: CardRecord[], queued: CardRecord[] = []): Map<number, CardRecord[]> {
  const map = new Map<number, CardRecord[]>()
  for (const c of [...committed, ...queued]) {
    map.set(c.player, [...(map.get(c.player) ?? []), c])
  }
  return map
}

/** 퇴장 여부 — 레드카드 1장, 또는 옐로카드 누적 2장. */
export function isSentOff(cards: CardRecord[]): boolean {
  return cards.some(c => c.card === 'R') || cards.filter(c => c.card === 'Y').length >= 2
}

/**
 * 카드 패널의 선수 목록에서 이 선수를 새로 선택할 수 있는지.
 * 이미 퇴장 처리된 선수는 더 카드를 줄 수 없어 막아야 하지만, 지금 그 선수의 카드를
 * "수정" 중이라면(editingPlayer === playerIdx) 예외로 열어준다 — 그래야 그 선수의
 * 경고/퇴장 카드 자체를 고치는 도중에 목록에서 사라져 되돌릴 수 없게 되는 일이 없다.
 */
export function canPickForCard(
  playerIdx: number,
  cardsByPlayer: Map<number, CardRecord[]>,
  editingPlayer: number | null,
): boolean {
  if (playerIdx === editingPlayer) return true
  return !isSentOff(cardsByPlayer.get(playerIdx) ?? [])
}
