/**
 * 골대 타깃 좌표 규약.
 *
 * footballX TacticVibe 파이프라인과 동일한 원본 좌표 영역을 쓴다.
 * 전체 슈팅 맵은 628 × 300, 실제 골문 프레임은 x=83~464 / y=83~300이다.
 * 골문 물리 규격은 IFAB Laws of the Game, Law 1 기준(7.32m × 2.44m)이다.
 */
export const GOAL_TARGET = {
  canvas: { width: 628, height: 300 },
  frame: { xMin: 83, xMax: 464, yMin: 83, yMax: 300 },
  meters: { width: 7.32, height: 2.44 },
  /** 포스트·크로스바 바깥으로 확장하는 DSP 1m 범위. */
  oneMeter: { xRatio: 1 / 7.32, yRatio: 1 / 2.44 },
} as const

export type GoalPoint = { x: number; y: number }

/** 골문 프레임 내부의 화면 비율을 footballX 원본 좌표로 변환한다. */
export function goalFramePoint(xRatio: number, yRatio: number): GoalPoint {
  const { frame } = GOAL_TARGET
  return {
    x: Math.round(frame.xMin + Math.min(1, Math.max(0, xRatio)) * (frame.xMax - frame.xMin)),
    y: Math.round(frame.yMin + Math.min(1, Math.max(0, yRatio)) * (frame.yMax - frame.yMin)),
  }
}

/** 골대 타깃 전체(628×300)에서 클릭한 위치를 원본 좌표로 변환한다. */
export function goalTargetPoint(xRatio: number, yRatio: number): GoalPoint {
  const { canvas } = GOAL_TARGET
  return {
    x: Math.round(1 + Math.min(1, Math.max(0, xRatio)) * (canvas.width - 1)),
    y: Math.round(1 + Math.min(1, Math.max(0, yRatio)) * (canvas.height - 1)),
  }
}

/** 골문 밖 타깃의 레거시 결과 코드. 프레임 내부는 GOAL 영역이다. */
export function goalTargetResult(point: GoalPoint): 'GOAL' | 'LX' | 'HX' | 'RX' {
  const { frame } = GOAL_TARGET
  if (point.y < frame.yMin) return 'HX'
  if (point.x < frame.xMin) return 'LX'
  if (point.x > frame.xMax) return 'RX'
  return 'GOAL'
}

/** 중앙 정렬 UI의 바깥 타깃을 footballX 원본 좌표로 저장한다. */
export function goalOuterPoint(
  zone: 'LX' | 'HX' | 'RX',
  xRatio: number,
  yRatio: number,
): GoalPoint {
  const clamp = (value: number) => Math.min(1, Math.max(0, value))
  const { canvas, frame } = GOAL_TARGET
  if (zone === 'HX') {
    return {
      x: Math.round(1 + clamp(xRatio) * (canvas.width - 1)),
      y: Math.round(1 + clamp(yRatio) * (frame.yMin - 2)),
    }
  }
  return {
    x: zone === 'LX'
      ? Math.round(1 + clamp(xRatio) * (frame.xMin - 2))
      : Math.round(frame.xMax + 1 + clamp(xRatio) * (canvas.width - frame.xMax - 1)),
    y: Math.round(frame.yMin + clamp(yRatio) * (canvas.height - frame.yMin)),
  }
}

/** 골문 내부 및 포스트·크로스바 바깥 1m 이내인지 판정한다. */
export function isWithinGoalOneMeter(point: GoalPoint) {
  const { frame, meters } = GOAL_TARGET
  const xExtension = (frame.xMax - frame.xMin) / meters.width
  const yExtension = (frame.yMax - frame.yMin) / meters.height
  return point.x >= frame.xMin - xExtension &&
    point.x <= frame.xMax + xExtension &&
    point.y >= frame.yMin - yExtension &&
    point.y <= frame.yMax
}

/** 저장된 원본 좌표를 정규 골대 실측 좌표(m)로 환산한다. */
export function goalPointMeters(point: GoalPoint) {
  const { frame, meters } = GOAL_TARGET
  return {
    x: ((point.x - frame.xMin) / (frame.xMax - frame.xMin)) * meters.width,
    y: ((point.y - frame.yMin) / (frame.yMax - frame.yMin)) * meters.height,
  }
}
