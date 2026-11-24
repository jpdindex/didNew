export type CurveDirection = 'left' | 'right'
export type EstimatedShotInput = {
  posX: number; posY: number; shootPosX: number; shootPosY: number
  goalX: 0 | 105; timeToGoal: number | null
  curveDirection?: CurveDirection | null
}
export type EstimatedShot = {
  input: EstimatedShotInput; modelVersion: number; trajectoryGrade: string; curveWeight: number
  trajectory: { x: number; y: number; z: number }[]
  goalPoint: { x: number; y: number; z: number }; distance: number; averageSpeed: number | null
}
export function validEstimatedCoordinates(value: Record<string, unknown>): boolean {
  return ([['posX', 0, 100], ['posY', 0, 100], ['shootPosX', 83, 464], ['shootPosY', 83, 300]] as const)
    .every(([key, min, max]) => typeof value[key] === 'number' && Number.isFinite(value[key] as number)
      && (value[key] as number) >= min && (value[key] as number) <= max)
    && (value.goalX === 0 || value.goalX === 105)
}
/** Keep version 2 coefficients/sampling in sync with backend/system/system_goalx.py. */
export function estimateGoalShot(input: EstimatedShotInput, grade: 'high' | 'medium' | 'low'): EstimatedShot {
  const start = { x: input.posX * 1.05, y: input.posY * .68, z: 0 }
  const end = { x: input.goalX, y: 34 + (input.shootPosX - 83) / 381 * 7.32 - 3.66, z: (300 - input.shootPosY) / 217 * 2.44 }
  const distance = (a: typeof start, b: typeof start) => Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z)
  const curveWeight = { high: .20, medium: .10, low: 0 }[grade]
  const lift = distance(start, end) * curveWeight
  const dx = end.x - start.x, dy = end.y - start.y
  const horizontal = Math.hypot(dx, dy)
  const sign = input.curveDirection === 'left' ? 1 : input.curveDirection === 'right' ? -1 : 0
  const bend = horizontal * curveWeight * .5 * sign
  const trajectory = Array.from({ length: 101 }, (_, i) => {
    const t = i / 100
    const arc = 4 * t * (1 - t)
    return { x: start.x + dx * t + (horizontal ? dy / horizontal * bend * arc : 0),
      y: start.y + dy * t - (horizontal ? dx / horizontal * bend * arc : 0),
      z: start.z + (end.z - start.z) * t + 4 * lift * t * (1 - t) }
  })
  const length = trajectory.slice(1).reduce((sum, point, i) => sum + distance(trajectory[i]!, point), 0)
  return { input, modelVersion: 2, trajectoryGrade: grade, curveWeight, trajectory, goalPoint: end,
    distance: length, averageSpeed: input.timeToGoal ? length / input.timeToGoal * 3.6 : null }
}

type ExportEvaluation = { id: string; analystName?: string; grades?: { trajectory?: string }; curveDirection?: CurveDirection | null; estimatedShot?: EstimatedShot | null }
export type GoalxExportRecord = {
  gmId: string; side: string; recordId: string; evaluations: ExportEvaluation[]
  representativeStatus: string | null
  representative: { approvedBy: string; approvedAt: string; trajectory: string; timeToGoal: number | null
    timeSampleCount: number; score: number; categoryScores: Record<string, number>; shotId: string | null
    estimatedShot: EstimatedShot | null; curveDirection?: CurveDirection | null; sourceVersion: string } | null
}
const trajectoryNames: Record<string, string> = { high: '딥 커브', medium: '커브', low: '스트레이트' }
const directionNames: Record<string, string> = { left: '좌', right: '우' }

/** Numeric cells and one point per row keep exported trajectories reusable. */
export function goalxEstimationSheets(records: GoalxExportRecord[], labels: Map<string, unknown[]>) {
  const identity = ['경기 ID', '기록 팀', '득점 기록 ID', '득점자', '득점 시각']
  const estimates: unknown[][] = [[...identity, '데이터 구분', '분석관 또는 승인자 ID', '분석관 이름', '궤적 유형',
    '골라인 통과 소요시간 (초)', '슈팅 X (%)', '슈팅 Y (%)', '골문 결과 X', '골문 결과 Y', '공격 골대 X (m)',
    '추정 거리 (m)', '추정 평균 속도 (km/h)', '곡률', '계산 버전', '커브 방향 (슈팅자 기준)']]
  const points: unknown[][] = [[...identity, '데이터 구분', '분석관 또는 승인자 ID', '좌표 순서', 'X (m)', 'Y (m)', 'Z (m)']]
  const representatives: unknown[][] = [[...identity, '승인 상태', '승인자 ID', '승인 시각', '예술성 평균', '수비 환경 평균', '궤적 평균',
    '평균 총점', '대표 궤적', '소요시간 중앙값 (초)', '소요시간 측정 건수', 'ShotX ID', '원본 버전', '커브 방향 (슈팅자 기준)']]
  for (const record of records) {
    const base = labels.get(JSON.stringify([record.gmId, record.side, record.recordId]))
    if (!base) continue
    function addEstimate(kind: string, uid: string, name: string, grade?: string, shot?: EstimatedShot | null, direction?: CurveDirection | null) {
      const input = shot?.input
      estimates.push([...base, kind, uid, name, trajectoryNames[grade ?? ''] ?? '', input?.timeToGoal ?? null,
        input?.posX ?? null, input?.posY ?? null, input?.shootPosX ?? null, input?.shootPosY ?? null, input?.goalX ?? null,
        shot?.distance ?? null, shot?.averageSpeed ?? null, shot?.curveWeight ?? null, shot?.modelVersion ?? null,
        grade === 'low' ? '' : directionNames[direction ?? input?.curveDirection ?? ''] ?? '미선택'])
      shot?.trajectory.forEach((point, index) => points.push([...base, kind, uid, index, point.x, point.y, point.z]))
    }
    record.evaluations.forEach(entry => addEstimate('분석관', entry.id, entry.analystName ?? '', entry.grades?.trajectory, entry.estimatedShot, entry.curveDirection))
    const approved = record.representative
    if (approved) {
      representatives.push([...base, record.representativeStatus === 'approved' ? '승인 완료' : '재검토 필요', approved.approvedBy,
        approved.approvedAt, approved.categoryScores.artistry, approved.categoryScores.defense, approved.categoryScores.trajectory,
        approved.score, trajectoryNames[approved.trajectory], approved.timeToGoal, approved.timeSampleCount, approved.shotId, approved.sourceVersion,
        approved.trajectory === 'low' ? '' : directionNames[approved.curveDirection ?? approved.estimatedShot?.input.curveDirection ?? ''] ?? '미선택'])
      addEstimate('대표', approved.approvedBy, '', approved.trajectory, approved.estimatedShot, approved.curveDirection)
    }
  }
  return [{ name: '추정 3D 분석', rows: estimates }, { name: '추정 궤적 좌표', rows: points }, { name: '대표 승인', rows: representatives }]
}
