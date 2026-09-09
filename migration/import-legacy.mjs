import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { initializeApp, applicationDefault } from 'firebase-admin/app'
import { getFirestore } from 'firebase-admin/firestore'
import { parseSql } from './sql.mjs'
import { buildPlan } from './plan.mjs'
import { enrich, hydrateReferenceData } from './enrich.mjs'
const args=process.argv.slice(2), files=[], options={}
for(let i=0;i<args.length;i++) {
 const a=args[i]
 if(['--dry-run','--all','--repair'].includes(a)) options[a]=true
 else if(['--match','--project','--report'].includes(a)) {if(!args[i+1]||args[i+1].startsWith('--')) throw Error(`Missing value: ${a}`);options[a]=args[++i]}
 else if(a.startsWith('--')) throw Error(`Unknown option: ${a}`)
 else files.push(a)
}
if(!files.length) throw Error('Usage: node import-legacy.mjs dump.sql [master.sql] --dry-run [--match ID]')
const tables=new Map(), hash=createHash('sha256')
for(const file of files) {const sql=await fs.readFile(path.resolve(file),'utf8');hash.update(sql);for(const [name,rows] of parseSql(sql)) tables.set(name,[...(tables.get(name)??[]),...rows])}
// A match import owns only the leagues represented by ff_game.  The unified
// legacy dump carries unrelated K1 and international reference rows; keeping
// them would make one season ID point at conflicting league documents.
const importedLeagueIds=new Set((tables.get('ff_game')??[]).map(row=>String(row.gm_league)))
if(importedLeagueIds.size) {
 const games=tables.get('ff_game')??[]
 const gameTeamIds=new Set(games.flatMap(row=>[String(row.gm_h_t_code),String(row.gm_a_t_code)]))
 if(tables.has('ff_league')) tables.set('ff_league',tables.get('ff_league').filter(row=>importedLeagueIds.has(String(row.league_code))))
 if(tables.has('ff_season')) tables.set('ff_season',tables.get('ff_season').filter(row=>importedLeagueIds.has(String(row.l_code))))
 if(tables.has('ff_team')) tables.set('ff_team',tables.get('ff_team').filter(row=>importedLeagueIds.has(String(row.l_code)) || gameTeamIds.has(String(row.t_code))))
 if(tables.has('ff_player_team')) tables.set('ff_player_team',tables.get('ff_player_team').filter(row=>importedLeagueIds.has(String(row.l_code))))
 const playerIds=new Set([...(tables.get('ff_player_team')??[]).map(row=>String(row.p_id)),...(tables.get('ff_game_player')??[]).map(row=>String(row.p_id))])
 if(tables.has('ff_player')) tables.set('ff_player',tables.get('ff_player').filter(row=>playerIds.has(String(row.p_id))))
 const stadiumIds=new Set(games.map(row=>String(row.gm_s_code)))
 if(tables.has('ff_stadium')) tables.set('ff_stadium',tables.get('ff_stadium').filter(row=>stadiumIds.has(String(row.s_code))))
}
const sourceCounts=Object.fromEntries([...tables].map(([k,v])=>[k,v.length]))
if(options['--match']) {
 const id=options['--match'], games=(tables.get('ff_game')??[]).filter(x=>String(x.gm_id)===id)
 if(games.length!==1) throw Error(`Expected one match: ${id}, found ${games.length}`)
 tables.set('ff_game',games)
 const infos=(tables.get('ff_game_info')??[]).filter(x=>String(x.gm_id)===id), ids=new Set(infos.map(x=>String(x.gi_id)))
 tables.set('ff_game_info',infos)
 for(const [name,rows] of tables) if(name.startsWith('ff_game_')&&name!=='ff_game_info') tables.set(name,rows.filter(x=>ids.has(String(x.gi_id))))
 // A one-match write must not accidentally create the entire reference master.
 // Keep only the reference rows needed by that match and its two lineups.
 const playerIds=new Set((tables.get('ff_game_player')??[]).map(x=>String(x.p_id)))
 const teamIds=new Set(games.flatMap(x=>[String(x.gm_h_t_code),String(x.gm_a_t_code)]))
 const leagueIds=new Set(games.map(x=>String(x.gm_league)))
 const stadiumIds=new Set(games.map(x=>String(x.gm_s_code)))
 if(tables.has('ff_player')) tables.set('ff_player',tables.get('ff_player').filter(x=>playerIds.has(String(x.p_id))))
 if(tables.has('ff_player_team')) tables.set('ff_player_team',tables.get('ff_player_team').filter(x=>playerIds.has(String(x.p_id))&&teamIds.has(String(x.t_code))))
 if(tables.has('ff_team')) tables.set('ff_team',tables.get('ff_team').filter(x=>teamIds.has(String(x.t_code))))
 if(tables.has('ff_league')) tables.set('ff_league',tables.get('ff_league').filter(x=>leagueIds.has(String(x.league_code))))
 if(tables.has('ff_stadium')) tables.set('ff_stadium',tables.get('ff_stadium').filter(x=>stadiumIds.has(String(x.s_code))))
}
const project=options['--project'] ?? 'jpd-did'
if(!process.env.GOOGLE_APPLICATION_CREDENTIALS) throw Error('GOOGLE_APPLICATION_CREDENTIALS is required, including for --dry-run because reference collections are validated.')
initializeApp({credential:applicationDefault(),projectId:project})
const db=getFirestore()
const plan=await hydrateReferenceData(enrich(await buildPlan(tables),tables),db,tables)
const selectedCounts=Object.fromEntries([...tables].map(([k,v])=>[k,v.length]))
const documentCounts={}
for(const key of plan.documents.keys()) {const collection=key.split('/').at(-2);documentCounts[collection]=(documentCounts[collection]??0)+1}
for(const [table,collection] of [['ff_game','matches'],['ff_game_info','recordings'],['ff_game_record','records'],['ff_game_card','cards'],['ff_game_player','playerStats']]) if((selectedCounts[table]??0)!==(documentCounts[collection]??0)) plan.errors.push(`count-mismatch: ${table}=${selectedCounts[table]??0}, ${collection}=${documentCounts[collection]??0}`)
const derivedPathCount=(documentCounts.paths??0)-(selectedCounts.ff_game_path??0)
if(derivedPathCount<0) plan.errors.push(`count-mismatch: ff_game_path=${selectedCounts.ff_game_path??0}, paths=${documentCounts.paths??0}`)
const report={sourceCounts,selectedCounts,documentCounts,derivedPathCount,totalDocuments:plan.documents.size,errors:plan.errors,warnings:plan.warnings??[],valid:!plan.errors.length,firestoreWritten:false}
const reportPath=path.resolve(options['--report']??'import-validation.json')
await fs.writeFile(reportPath,JSON.stringify(report,null,2)+'\n')
console.log(JSON.stringify({...report,errors:report.errors.slice(0,20),warnings:report.warnings.slice(0,20),errorCount:report.errors.length,warningCount:report.warnings.length,reportPath},null,2))
if(plan.errors.length) {process.exitCode=1}
else if(!options['--dry-run']) {
 const fingerprint=hash.digest('hex')
 const progressPath=path.resolve('import-progress.json')
 let progress={fingerprint,project,verifiedMatches:[]}
 try {const saved=JSON.parse(await fs.readFile(progressPath,'utf8'));if(saved.fingerprint===fingerprint&&saved.project===project) progress=saved} catch(e) {if(e.code!=='ENOENT') throw e}
 // Reference-only dumps (players, contracts, teams, etc.) must be importable
 // before a match dump. Match dumps still require a successful one-match run.
 if((tables.get('ff_game')?.length ?? 0) && !options['--match']&&!progress.verifiedMatches.length) throw Error('First import and verify one match from the same input files and project')
 const items=[...plan.documents].sort(([a],[b])=>a.localeCompare(b))
 const includesPlanned=(actual,expected)=>{
  if(expected===null||typeof expected!=='object') return actual===expected
  if(typeof expected.toMillis==='function') return typeof actual?.toMillis==='function'&&actual.toMillis()===expected.toMillis()
  if(Array.isArray(expected)) return Array.isArray(actual)&&expected.length===actual.length&&expected.every((value,index)=>includesPlanned(actual[index],value))
  return actual&&typeof actual==='object'&&Object.entries(expected).every(([key,value])=>includesPlanned(actual[key],value))
 }
 for(let offset=0;offset<items.length;offset+=200) {
  const chunk=items.slice(offset,offset+200), refs=chunk.map(([key])=>db.doc(key)), snapshots=await db.getAll(...refs), batch=db.batch(), mustValidate=[]
  let created=0
  for(let j=0;j<chunk.length;j++) {
   if(snapshots[j].exists) {
    if(options['--repair']) { batch.set(refs[j],chunk[j][1],{merge:true}); mustValidate[j]=true; created++ }
   } else {batch.create(refs[j],chunk[j][1]);mustValidate[j]=true;created++}
  }
  if(created) await batch.commit()
  const actual=await db.getAll(...refs)
  for(let j=0;j<chunk.length;j++) if(mustValidate[j]&&(!actual[j].exists||!includesPlanned(actual[j].data(),chunk[j][1]))) throw Error(`Read-back verification failed: ${chunk[j][0]}`)
  progress.lastVerifiedPath=chunk.at(-1)[0]
  await fs.writeFile(progressPath+'.tmp',JSON.stringify(progress,null,2)+'\n');await fs.rename(progressPath+'.tmp',progressPath)
  console.log(`Verified ${Math.min(offset+chunk.length,items.length)}/${items.length}, created ${created}`)
 }
 if(options['--match']) progress.verifiedMatches=[...new Set([...progress.verifiedMatches,options['--match']])]
 await fs.writeFile(progressPath+'.tmp',JSON.stringify(progress,null,2)+'\n');await fs.rename(progressPath+'.tmp',progressPath)
 console.log('Firestore 저장 및 전체 필드 read-back 검증 완료')
}
