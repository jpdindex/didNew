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

/**
 * 경기장 배경 CSS 값을 만든다.
 * 원본은 비트맵이지만 띠 폭이 일정하므로 그라디언트로 동일하게 재현된다.
 */
export function grassBackground(pattern: GrassPattern, lines: GrassLines): string {
  if (pattern === 0) return GRASS_DARK

  const w = 100 / (lines * 2) // 띠 하나의 폭(%)
  const [first, second] = pattern === 1 ? [GRASS_LIGHT, GRASS_DARK] : [GRASS_DARK, GRASS_LIGHT]

  return `repeating-linear-gradient(90deg, ${first} 0 ${w}%, ${second} ${w}% ${w * 2}%)`
}
