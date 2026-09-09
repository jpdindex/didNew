// 일회성 패치 — plan.mjs의 `now` 변수가 Timestamp.now() 대신 Timestamp.fromMillis(0)로
// 잘못 하드코딩돼 있던 버그(§plan.mjs:9, enrich.mjs:36) 때문에, 이미 Firestore에 들어간
// 문서들의 createdAt/updatedAt이 전부 1970-01-01로 찍혀 있다. 그 문서들만 골라서
// 지금 시각으로 다시 써준다. 실제 축구 데이터(date/score/kpi 등)는 건드리지 않는다.
//
// 사용법:
//   node fix-timestamps.mjs --dry-run   # 몇 건이 고쳐질지만 보여주고 아무것도 안 씀
//   node fix-timestamps.mjs             # 실제로 고침

import { initializeApp, applicationDefault } from 'firebase-admin/app'
import { getFirestore, Timestamp } from 'firebase-admin/firestore'

const dryRun = process.argv.includes('--dry-run')
const projectIndex = process.argv.indexOf('--project')
const project = projectIndex >= 0 ? process.argv[projectIndex + 1] : 'jpd-did'

// 로컬에서는 서비스 계정 JSON(GOOGLE_APPLICATION_CREDENTIALS)으로, Cloud Shell에서는
// 이미 로그인된 gcloud 사용자 인증(ADC)으로 동작한다 — 여기서 어느 쪽인지 미리 안 따진다.
// 둘 다 없으면 applicationDefault()가 알아서 자기 언어로 에러를 내준다.
initializeApp({ credential: applicationDefault(), projectId: project })
const db = getFirestore()

const isEpochZero = v => v instanceof Timestamp && v.toMillis() === 0

// plan.mjs가 `now`를 쓴 곳: teams/players/leagues/seasons/stadiums/matches/contracts/cards.
// coaches는 enrich.mjs가 따로 하드코딩했다(§enrich.mjs:36). recordings/records는 enrich.mjs가
// ts(x.gi_regdt)/ts(x.gr_regdt)로 다시 덮어써서 이 버그의 영향을 받지 않았을 것이므로 안 건드린다.
const TOP_LEVEL_COLLECTIONS = ['teams', 'players', 'leagues', 'seasons', 'stadiums', 'matches', 'coaches']
const COLLECTION_GROUPS = ['contracts', 'cards']

let fixed = 0, checked = 0
const batchOps = []

function queueFix(ref, data) {
  checked++
  const patch = {}
  if (isEpochZero(data.createdAt)) patch.createdAt = Timestamp.now()
  if (isEpochZero(data.updatedAt)) patch.updatedAt = Timestamp.now()
  if (Object.keys(patch).length === 0) return
  fixed++
  batchOps.push({ ref, patch })
}

for (const name of TOP_LEVEL_COLLECTIONS) {
  const snap = await db.collection(name).get()
  for (const doc of snap.docs) queueFix(doc.ref, doc.data())
}
for (const name of COLLECTION_GROUPS) {
  const snap = await db.collectionGroup(name).get()
  for (const doc of snap.docs) queueFix(doc.ref, doc.data())
}

console.log(`검사: ${checked}건, 고칠 대상: ${fixed}건`)

if (dryRun) {
  console.log('dry-run — 아무것도 쓰지 않았다')
} else {
  for (let i = 0; i < batchOps.length; i += 450) {
    const batch = db.batch()
    for (const { ref, patch } of batchOps.slice(i, i + 450)) batch.update(ref, patch)
    await batch.commit()
    console.log(`  → ${Math.min(i + 450, batchOps.length)}/${batchOps.length} 저장`)
  }
  console.log('완료')
}
