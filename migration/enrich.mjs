import { Timestamp } from 'firebase-admin/firestore'
const str = x => x == null ? '' : String(x)
const date = x => str(x).replaceAll('.', '-').slice(0,10)
const ts = x => {
  if (!x) return Timestamp.fromMillis(0)
  const millis = typeof x === 'number' ? x : Date.parse(str(x).replace(' ', 'T') + '+09:00')
  if (!Number.isFinite(millis)) throw Error(`Invalid date: ${x}`)
  return Timestamp.fromMillis(millis)
}
export function enrich(plan, tables) {
  const {documents: docs, errors, warnings = []} = plan
  const rows = t => tables.get(t) ?? []
  const players = new Map(rows('ff_player').map(x=>[str(x.p_id), x]))
  const games = new Map(rows('ff_game').map(x=>[str(x.gm_id), x]))
  const sessions = new Map(rows('ff_game_info').map(x=>[str(x.gi_id), x]))
  const recPath = id => { const i=sessions.get(str(id)); return i ? `matches/${i.gm_id}/recordings/${i.gi_write_code}` : null }
  const missing = (kind, id) => errors.push(`${kind}: ${id}`)
  for(const x of rows('ff_game')) {
    const d=docs.get(`matches/${x.gm_id}`)
    Object.assign(d,{date:date(x.gm_date),kickoffTime:str(x.gm_time),stadiumId:str(x.gm_s_code),score:{home:Number(x.gi_goal_home),away:Number(x.gi_goal_away)},matchType:'league'})
    if(str(x.gm_league).toUpperCase()!=='EPL') missing('match-type-needs-mapping',x.gm_id)
    for(const team of [x.gm_h_t_code,x.gm_a_t_code]) {
      const key=`teams/${team}/seasons/${d.seasonId}`
      if(docs.has(key)&&docs.get(key).leagueId!==d.leagueId) missing('team-season-league-conflict',key)
      docs.set(key,{leagueId:d.leagueId})
      if(rows('ff_team').length && !docs.has(`teams/${team}`)) missing('missing-team',team)
    }
    if(rows('ff_stadium').length && !docs.has(`stadiums/${d.stadiumId}`)) missing('missing-stadium',d.stadiumId)
  }
  for(const x of rows('ff_team')) {
    const name=str(x.t_coach_kr||x.t_coach).trim()
    const d=docs.get(`teams/${x.t_code}`)
    if(name) {
      const id=encodeURIComponent(name.replace(/\s+/g,'-'))
      d.currentCoachId=id
      docs.set(`coaches/${id}`,{name,nameKr:str(x.t_coach_kr),nameEn:str(x.t_coach),active:true,createdAt:Timestamp.now(),updatedAt:Timestamp.now(),createdBy:'legacy-import',updatedBy:'legacy-import'})
    }
  }
  for(const x of rows('ff_game_info')) {
    const prefix=recPath(x.gi_id), d=docs.get(prefix), g=games.get(str(x.gm_id))
    if(!g || !['H','A'].includes(str(x.gi_write_code))) { missing('invalid-session',x.gi_id); continue }
    const status={END:'final','000':'ready',H1B:'H1',H1E:'H1_done',H2B:'H2',H2E:'H2_done',H3B:'H3',H3E:'H3_done',H4B:'H4',H4E:'H4_done'}[x.gi_state]
    if(!status) missing('unknown-status',x.gi_state)
    const halves={}
    for(let h=1;h<=4;h++) if(x[`gi_h${h}_begin`]) halves[`H${h}`]={startedAt:ts(x[`gi_h${h}_begin`]),seconds:Number(x[`gi_h${h}_seconds`])}
    Object.assign(d,{status:status??'ready',recorders:{},recorderIds:[],inputMode:g.is_realtime==='Y'?'실시간':'분석',fieldSide:x.gi_part==='R'?'right':'left',formationKey:str(x.gi_formation),halves,h1Locked:status==='final',h2Locked:status==='final',maxSeq:0,kpiComputedAt:ts(x.gi_moddt),legacyGiId:str(x.gi_id),syncedAt:null,createdAt:ts(x.gi_regdt),updatedAt:ts(x.gi_moddt)})
    if(x.gi_part_ex) d.fieldSideEx=x.gi_part_ex==='R'?'right':'left'
    // Legacy login IDs are not Firebase Auth UIDs.
    d.legacyRecorderId=str(x.gi_user_id)
    if(!d.lineup) d.lineup={}
    for(const [id, entry] of Object.entries(d.lineup)) {
      entry.name=str(players.get(id)?.p_name)
      const contracts=rows('ff_player_team').filter(c=>str(c.p_id)===id&&str(c.t_code)===d.teamId&&date(c.pt_begin)<=date(g.gm_date)&&(!c.pt_end||date(c.pt_end)>=date(g.gm_date)))
      if(rows('ff_player_team').length && contracts.length!==1) missing('lineup-contract-count',`${prefix}/${id}: ${contracts.length}`)
      entry.no=str(contracts[0]?.pt_num);entry.pos=str(contracts[0]?.pt_pos)
      if(rows('ff_player').length && (!entry.name||!entry.no||!entry.pos)) missing('incomplete-lineup',`${prefix}/${id}`)
      if(!entry.inHalf) entry.inSeconds=null
      if(!entry.outHalf) entry.outSeconds=null
    }
  }
  const sourceRecords=new Map()
  for(const x of rows('ff_game_record')) {
    const prefix=recPath(x.gi_id)
    if(!prefix) {missing('orphan-record',x.gr_id);continue}
    const key=`${prefix}/records/${x.gr_id}`, d=docs.get(key)
    sourceRecords.set(key,x)
    if(str(x.t_code)!==docs.get(prefix).teamId) missing('record-team-mismatch',key)
    Object.assign(d,{posX:Number(x.gr_pos_x)/971*100,posY:Number(x.gr_pos_y)/634*100,shootPosX:Number(x.gr_shoot_pos_x),shootPosY:Number(x.gr_shoot_pos_y),isShot:!!Number(x.gr_is_sht),createdAt:ts(x.gr_regdt)})
    if(!x.p_id) delete d.playerId
  }
  // Dump order is by record ID. Restore chronology using original creation time,
  // then ID as deterministic tie-break; do not pretend this is original sort metadata.
  const ordered=[...sourceRecords].sort(([a,x],[b,y])=>str(x.gi_id).localeCompare(str(y.gi_id))||str(x.gr_half).localeCompare(str(y.gr_half))||Number(x.gr_half_seconds)-Number(y.gr_half_seconds)||str(x.gr_regdt).localeCompare(str(y.gr_regdt))||a.localeCompare(b))
  const sequence=new Map()
  for(const [key,x] of ordered) {const prefix=recPath(x.gi_id), seq=sequence.get(prefix)??0;docs.get(key).seq=seq; sequence.set(prefix,seq+1);docs.get(prefix).maxSeq=seq}
  for(const [key,d] of docs) if(key.includes('/paths/')) {
    const prefix=key.split('/paths/')[0]
    for(const id of d.recordIds) {
      const r=sourceRecords.get(`${prefix}/records/${id}`)
      if(!r||str(r.gt_id)!==key.split('/').at(-1)) missing('invalid-path-record',`${key}/${id}`)
    }
    d.recordIds.sort((a,b)=>docs.get(`${prefix}/records/${a}`).seq-docs.get(`${prefix}/records/${b}`).seq)
  }
  for(const x of rows('ff_game_bap')) if(!sourceRecords.has(`${recPath(x.gi_id)}/records/${x.gr_id}`)) missing('orphan-bap',x.gr_id)
  for(const x of rows('ff_game_player_log')) {
    const prefix=recPath(x.gi_id), d=docs.get(prefix)
    if(!d||!d.lineup[str(x.pl_in_p_id)]||!d.lineup[str(x.pl_out_p_id)]) { missing('substitution-player-missing',`${x.gi_id}/${x.pl_in_p_id}/${x.pl_out_p_id}`);continue }
    if(str(x.t_code)!==d.teamId) missing('substitution-team-mismatch',x.gi_id)
  }
  for(const [key,d] of docs) {
    if(key.split('/').length%2 || key.split('/').some(x=>!x)) missing('invalid-document-path',key)
    if(d.createdAt && !key.startsWith('matches/')) {d.createdBy='legacy-import';d.updatedBy='legacy-import'}
  }
  plan.errors=[...new Set(errors)]
  plan.warnings=[...new Set(warnings)]
  return plan
}

/** Fill the match dump from the reference collections already in Firestore. */
export async function hydrateReferenceData(plan, db, tables) {
  const rows = name => tables.get(name) ?? []
  const games = new Map(rows('ff_game').map(row => [String(row.gm_id), row]))
  const playerIds = new Set(rows('ff_game_player').map(row => String(row.p_id)))
  const teamIds = new Set(rows('ff_game').flatMap(row => [String(row.gm_h_t_code), String(row.gm_a_t_code)]))
  const stadiumIds = new Set(rows('ff_game').map(row => String(row.gm_s_code)))
  const getAll = async refs => {
    const out = []
    for (let index = 0; index < refs.length; index += 200) out.push(...await db.getAll(...refs.slice(index, index + 200)))
    return out
  }
  const [players, teams, stadiums, contractSnapshot] = await Promise.all([
    getAll([...playerIds].map(id => db.doc(`players/${id}`))),
    getAll([...teamIds].map(id => db.doc(`teams/${id}`))),
    getAll([...stadiumIds].map(id => db.doc(`stadiums/${id}`))),
    db.collectionGroup('contracts').get(),
  ])
  const playerById = new Map(players.filter(snap => snap.exists).map(snap => [snap.id, snap.data()]))
  for (const [path, data] of plan.documents) if (path.startsWith('players/') && path.split('/').length === 2) playerById.set(path.split('/')[1], data)
  const teamById = new Set(teams.filter(snap => snap.exists).map(snap => snap.id))
  const stadiumById = new Set(stadiums.filter(snap => snap.exists).map(snap => snap.id))
  for (const path of plan.documents.keys()) {
    const bits = path.split('/')
    if (bits.length === 2 && bits[0] === 'teams') teamById.add(bits[1])
    if (bits.length === 2 && bits[0] === 'stadiums') stadiumById.add(bits[1])
  }
  const contracts = contractSnapshot.docs
    .filter(snap => playerIds.has(snap.ref.parent.parent?.id ?? ''))
    .map(snap => ({ playerId: snap.ref.parent.parent.id, ...snap.data() }))
  for (const [path, data] of plan.documents) {
    const bits = path.split('/')
    if (bits.length === 4 && bits[0] === 'players' && bits[2] === 'contracts') contracts.push({ playerId: bits[1], ...data })
  }
  // During a full run, contracts created by the one-match proof are present in
  // Firestore as well as in this SQL plan. They are the same contract, not two
  // simultaneous affiliations.
  const contractKey = contract => [contract.playerId, contract.teamId, date(contract.from), contract.to ? date(contract.to) : '', contract.no ?? '', contract.pos ?? ''].join('|')
  const uniqueContracts = [...new Map(contracts.map(contract => [contractKey(contract), contract])).values()]
  const errors = []
  for (const id of teamIds) if (!teamById.has(id)) errors.push(`missing-team-in-firestore: ${id}`)
  for (const id of stadiumIds) if (!stadiumById.has(id)) errors.push(`missing-stadium-in-firestore: ${id}`)
  for (const [path, recording] of plan.documents) {
    if (!path.endsWith('/recordings/H') && !path.endsWith('/recordings/A')) continue
    const matchId = path.split('/')[1]
    const game = games.get(matchId)
    for (const [playerId, entry] of Object.entries(recording.lineup ?? {})) {
      const player = playerById.get(playerId)
      if (!player?.name) { errors.push(`missing-player-in-firestore: ${path}/${playerId}`); continue }
      const candidates = uniqueContracts.filter(contract =>
        contract.playerId === playerId && contract.teamId === recording.teamId &&
        date(contract.from) <= date(game.gm_date) &&
        (!contract.to || date(contract.to) >= date(game.gm_date)))
      if (candidates.length !== 1) { errors.push(`lineup-contract-count-in-firestore: ${path}/${playerId}: ${candidates.length}`); continue }
      entry.name = String(player.name)
      entry.no = String(candidates[0].no ?? '')
      entry.pos = String(candidates[0].pos ?? '')
      if (!entry.no || !entry.pos) errors.push(`incomplete-lineup-in-firestore: ${path}/${playerId}`)
    }
  }
  plan.errors = [...new Set([...plan.errors, ...errors])]
  return plan
}
