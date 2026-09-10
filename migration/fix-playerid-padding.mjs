// 일회성 패치 — 프론트 이관 도구(app/pages/manage/legacy-import/index.vue)의 SQL 파서가
// 따옴표 안 숫자 문자열도 Number()로 바꿔버리던 버그 때문에, p_id(10자리, 예: "0000001072")의
// 앞자리 0이 다 날아간 채로 이미 Firestore에 들어가 있다. 그 문서/필드/맵 키를 찾아서
// 10자리로 다시 패딩한다. 실제 축구 데이터(기록/스탯 값 자체)는 건드리지 않는다.
//
// 사용법:
//   node fix-playerid-padding.mjs --dry-run   # 몇 건이 고쳐질지만 보여주고 아무것도 안 씀
//   node fix-playerid-padding.mjs             # 실제로 고침

import { initializeApp, applicationDefault } from 'firebase-admin/app'
import { getFirestore } from 'firebase-admin/firestore'

const dryRun = process.argv.includes('--dry-run')
const projectIndex = process.argv.indexOf('--project')
const project = projectIndex >= 0 ? process.argv[projectIndex + 1] : 'jpd-did'

initializeApp({ credential: applicationDefault(), projectId: project })
const db = getFirestore()

const PAD_LEN = 10
const CHUNK = 25
const isShortNumericId = v => typeof v === 'string' && /^\d+$/.test(v) && v.length > 0 && v.length < PAD_LEN
const pad = v => v.padStart(PAD_LEN, '0')

let renamedPlayers = 0, renamedContracts = 0, renamedSquad = 0, renamedPlayerStats = 0
let fixedRecordFields = 0, fixedLineupDocs = 0, skippedCollisions = 0
const collisionSamples = []

// ---- 여러 쓰기/삭제를 450개씩 묶어서 한 번에 커밋 (속도 개선) ----
let batch = db.batch()
let opCount = 0
async function flush() {
  if (opCount === 0) return
  await batch.commit()
  batch = db.batch()
  opCount = 0
}
async function queueSet(ref, data) {
  if (dryRun) return
  batch.set(ref, data)
  if (++opCount >= 450) await flush()
}
async function queueDelete(ref) {
  if (dryRun) return
  batch.delete(ref)
  if (++opCount >= 450) await flush()
}
async function queueUpdate(ref, data) {
  if (dryRun) return
  batch.update(ref, data)
  if (++opCount >= 450) await flush()
}

// candidates 중 실제 이름을 바꿀 것만 골라낸다 — 존재 확인(get)을 25개씩 동시에 보내서
// 하나씩 순서대로 확인하는 것보다 훨씬 빠르게 처리한다.
async function filterCollisions(candidates, buildNewRef, describe) {
  const toRename = []
  for (let i = 0; i < candidates.length; i += CHUNK) {
    const chunk = candidates.slice(i, i + CHUNK)
    const refs = chunk.map(buildNewRef)
    const snaps = await Promise.all(refs.map(r => r.get()))
    for (let j = 0; j < chunk.length; j++) {
      if (snaps[j].exists) {
        skippedCollisions++
        if (collisionSamples.length < 20) collisionSamples.push(describe(chunk[j], refs[j]))
      } else {
        toRename.push({ item: chunk[j], newRef: refs[j] })
      }
    }
    console.log(`  중복 확인 ${Math.min(i + CHUNK, candidates.length)}/${candidates.length}`)
  }
  return toRename
}

// ---- 1. players/{p_id} 문서 ID 교정 (+ 하위 contracts 서브컬렉션 통째로 이동) ----
console.log('players 검사 중...')
const playersSnap = await db.collection('players').get()
const playerCandidates = playersSnap.docs.filter(d => isShortNumericId(d.id))
console.log(`  전체 ${playersSnap.size}건, 짧은 ID 후보 ${playerCandidates.length}건 — 중복 확인 시작`)
const playersToRename = await filterCollisions(
  playerCandidates,
  d => db.collection('players').doc(pad(d.id)),
  (d, ref) => `players/${d.id} (-> ${ref.id} 이미 존재)`
)
console.log(`  실제 교정 대상 ${playersToRename.length}건, 저장 중...`)
let done = 0
for (const { item: doc, newRef } of playersToRename) {
  await queueSet(newRef, doc.data())
  const contractsSnap = await doc.ref.collection('contracts').get()
  for (const c of contractsSnap.docs) {
    await queueSet(newRef.collection('contracts').doc(c.id), c.data())
    await queueDelete(c.ref)
    renamedContracts++
  }
  await queueDelete(doc.ref)
  renamedPlayers++
  if (++done % 200 === 0) console.log(`  저장 ${done}/${playersToRename.length}`)
}

// ---- 2. teams/{t_code}/squad/{p_id} 문서 ID 교정 ----
console.log('squad 검사 중...')
const squadSnap = await db.collectionGroup('squad').get()
const squadCandidates = squadSnap.docs.filter(d => isShortNumericId(d.id))
console.log(`  전체 ${squadSnap.size}건, 짧은 ID 후보 ${squadCandidates.length}건 — 중복 확인 시작`)
const squadToRename = await filterCollisions(
  squadCandidates,
  d => d.ref.parent.parent.collection('squad').doc(pad(d.id)),
  (d, ref) => `${d.ref.path} (-> ${ref.id} 이미 존재)`
)
console.log(`  실제 교정 대상 ${squadToRename.length}건, 저장 중...`)
for (const { item: doc, newRef } of squadToRename) {
  await queueSet(newRef, doc.data())
  await queueDelete(doc.ref)
  renamedSquad++
}

// ---- 3. matches/{gm_id}/recordings/{H|A}/playerStats/{p_id} 문서 ID + playerId 필드 교정 ----
console.log('playerStats 검사 중...')
const statsSnap = await db.collectionGroup('playerStats').get()
const statsCandidates = statsSnap.docs.filter(d => isShortNumericId(d.id))
console.log(`  전체 ${statsSnap.size}건, 짧은 ID 후보 ${statsCandidates.length}건 — 중복 확인 시작`)
const statsToRename = await filterCollisions(
  statsCandidates,
  d => d.ref.parent.parent.collection('playerStats').doc(pad(d.id)),
  (d, ref) => `${d.ref.path} (-> ${ref.id} 이미 존재)`
)
console.log(`  실제 교정 대상 ${statsToRename.length}건, 저장 중...`)
for (const { item: doc, newRef } of statsToRename) {
  const data = doc.data()
  data.playerId = newRef.id
  await queueSet(newRef, data)
  await queueDelete(doc.ref)
  renamedPlayerStats++
}

// ---- 4. matches/{gm_id}/recordings/{H|A}/records 의 playerId 필드 교정 (문서ID는 gr_id라 안 건드림) ----
console.log('records 검사 중... (제일 오래 걸림, 중복 확인은 필요 없음)')
const recordsSnap = await db.collectionGroup('records').get()
let scanned = 0
for (const doc of recordsSnap.docs) {
  scanned++
  if (scanned % 5000 === 0) console.log(`  ${scanned}/${recordsSnap.size} 검사`)
  const data = doc.data()
  if (!isShortNumericId(data.playerId)) continue
  await queueUpdate(doc.ref, { playerId: pad(data.playerId) })
  fixedRecordFields++
}

// ---- 5. matches/{gm_id}/recordings/{H|A} 의 lineup 맵 키 + slot 필드 교정 ----
console.log('recordings.lineup 검사 중...')
const recordingsSnap = await db.collectionGroup('recordings').get()
for (const doc of recordingsSnap.docs) {
  const lineup = doc.data().lineup
  if (!lineup || typeof lineup !== 'object') continue
  let changed = false
  const newLineup = {}
  for (const [key, entry] of Object.entries(lineup)) {
    if (isShortNumericId(key)) {
      changed = true
      const newKey = pad(key)
      newLineup[newKey] = { ...entry, slot: newKey }
    } else {
      newLineup[key] = entry
    }
  }
  if (!changed) continue
  await queueUpdate(doc.ref, { lineup: newLineup })
  fixedLineupDocs++
}

await flush()

console.log('---')
console.log(`players 교정: ${renamedPlayers} (contracts ${renamedContracts}건 같이 이동)`)
console.log(`squad 교정: ${renamedSquad}`)
console.log(`playerStats 교정: ${renamedPlayerStats}`)
console.log(`records.playerId 필드 교정: ${fixedRecordFields}`)
console.log(`recordings.lineup 교정: ${fixedLineupDocs}`)
if (skippedCollisions) {
  console.log(`⚠️ 대상 ID가 이미 존재해서 건너뛴 것: ${skippedCollisions}건 — 직접 확인 필요`)
  console.log('  예시(최대 20개):')
  collisionSamples.forEach(s => console.log(`    ${s}`))
}
console.log(dryRun ? 'dry-run — 아무것도 쓰지 않았다' : '완료')
