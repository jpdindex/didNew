// 잔디(경기장 배경) 스펙.
//
// 레거시 APK 의 mipmap 이미지 7종에서 그대로 추출했다.
//   dplay_input_main_ground_bg_p000 / p109 / p110 / p111 / p209 / p210 / p211
//   (DPlayInputFragment.fieldImageList — ground.setBackgroundResource(list.get(mPattern)))
//
// 이미지 규격은 971 × 634 로, 03.areacode.sql 의 좌표 캔버스와 정확히 같다.
//
// 파일명 규칙: p + [패턴] + [줄수 2자리]
//   p0 00 → 패턴 없음(단색)
//   p1 09/10/11 → 패턴 1, 밝은 색으로 시작
//   p2 09/10/11 → 패턴 2, 진한 색으로 시작
//
// "9줄/10줄/11줄" 은 밝은+진한 한 쌍을 1줄로 센 것이다. 실제 세로 띠는 18/20/22개이며,
// 픽셀 측정값(폭 ≈ 54 / 48 / 44 px @971)과 971÷(줄수×2) 가 일치한다.

/** 진한 녹색 — 이미지에서 추출 */
export const GRASS_DARK = '#2E5229'
/** 밝은 연두 — 이미지에서 추출 */
export const GRASS_LIGHT = '#40622F'

/** 0 = 패턴 없음(단색), 1 = 연두로 시작, 2 = 녹색으로 시작 */
export type GrassPattern = 0 | 1 | 2
/** 한 쌍(연두+녹색)을 1줄로 센 개수 */
export type GrassLines = 9 | 10 | 11

export const GRASS_PATTERNS: { value: GrassPattern; label: string }[] = [
  { value: 1, label: '연두-녹색' },
  { value: 2, label: '녹색-연두' },
  { value: 0, label: '패턴 없음' },
]
export const GRASS_LINE_OPTIONS: GrassLines[] = [9, 10, 11]

/** 페널티 박스 폭(%) — DidInput.vue 의 .boxL/.boxR width 와 같아야 한다 */
export const PENALTY_BOX_WIDTH = 15.5

function stripeLayout(lines: GrassLines) {
  const total = lines * 2
  const box = PENALTY_BOX_WIDTH
  const mid = 100 - box * 2

  // 박스 안 띠 개수: 박스 쪽 폭과 가운데 쪽 폭이 가장 비슷해지는 값
  let k = 1
  for (let n = 1; n < total / 2; n++) {
    const diff = Math.abs(box / n - mid / (total - n * 2))
    if (diff < Math.abs(box / k - mid / (total - k * 2))) k = n
  }
  const midCount = total - k * 2
  return { box, mid, k, midCount, midStripe: mid / midCount }
}

/** 박스 바깥(가운데 구간) 띠 하나의 폭(%) — 페널티 아크 반지름 상한으로 쓴다 */
export function grassMidStripeWidth(lines: GrassLines): number {
  return stripeLayout(lines).midStripe
}

/**
 * 경기장 배경 CSS 값을 만든다.
 * 띠 경계가 페널티 박스 라인에 정확히 걸리도록, 박스 구간과 가운데 구간을 따로 균등 분할한다.
 * 전체 띠 개수(줄수×2)와 하프라인 위치의 경계는 그대로 유지된다.
 */
export function grassBackground(pattern: GrassPattern, lines: GrassLines): string {
  if (pattern === 0) return GRASS_DARK

  const { box, mid, k, midCount } = stripeLayout(lines)

  const edges: number[] = [0]
  for (let i = 1; i <= k; i++) edges.push((box / k) * i)
  for (let i = 1; i <= midCount; i++) edges.push(box + (mid / midCount) * i)
  for (let i = 1; i <= k; i++) edges.push(100 - box + (box / k) * i)

  const [first, second] = pattern === 1 ? [GRASS_LIGHT, GRASS_DARK] : [GRASS_DARK, GRASS_LIGHT]
  const stops = edges.slice(1).map((end, i) => {
    const start = +edges[i]!.toFixed(4)
    return `${i % 2 === 0 ? first : second} ${start}% ${+end.toFixed(4)}%`
  })

  return `linear-gradient(90deg, ${stops.join(', ')})`
}
