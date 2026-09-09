import { Timestamp } from 'firebase-admin/firestore'
export function half(value, nullable = false) {
  if (nullable && (value == null || value === '' || value === '00' || value === 0)) return null
  const h = String(value).replace(/^H/, '')
  if (!['1','2','3','4'].includes(h)) throw Error(`Unknown half: ${value}`)
  return `H${h}`
}
export async function buildPlan(tables) {
const now=Timestamp.fromMillis(0), s=x=>x==null?'':String(x), n=x=>Number(x)||0
const date=x=>s(x).replaceAll('.', '-').slice(0,10)
const seasonId=x=>s(x).replace(/\D/g,'')
const isOpen=x=>!x||s(x)==='9999.99.99'||s(x)==='9999-99-99'
const documents=new Map(), counts={}, errors=[], warnings=[]
async function put(items,name) { counts[name]=items.length; for(const item of items) {
 // BAP is merged into its source record later in the plan.  Any other
 // duplicate is a malformed legacy key and must stop the import.
 // ff_team is a season-scoped legacy table: the same stable team ID appears
 // once per competition.  Its identity document is intentionally merged.
 if (documents.has(item.path) && !['teams','lineup','bap','path-recordIds','records'].includes(name)) errors.push(`duplicate: ${item.path}`)
 documents.set(item.path,{...(documents.get(item.path)??{}),...item.data})
}}
const games=tables.get('ff_game')??[],infos=tables.get('ff_game_info')??[],byGame=new Map(games.map(x=>[s(x.gm_id),x])),byInfo=new Map(infos.map(x=>[s(x.gi_id),x]))
await put((tables.get('ff_team')??[]).map(x=>({path:`teams/${s(x.t_code)}`,data:{name:s(x.t_name),nameKr:s(x.t_name_kr)||null,nameFull:s(x.t_name_full)||null,nameShort:s(x.t_name_short)||null,textColor:s(x.u_text_color_code).trim()||null,stadiumId:s(x.s_code)||null,foundedAt:isOpen(x.t_begin)?null:date(x.t_begin),dissolvedAt:isOpen(x.t_end)?null:date(x.t_end),currentLeagueId:s(x.l_code)||null,createdAt:now,createdBy:'legacy-import',updatedAt:now,updatedBy:'legacy-import'}})),'teams')
await put((tables.get('ff_player')??[]).map(x=>({path:`players/${s(x.p_id)}`,data:{name:s(x.p_name),nameEn:s(x.p_name_en)||null,nameFull:s(x.p_name_full)||null,birth:s(x.p_birth)?date(x.p_birth):null,foot:s(x.p_foot)||null,height:x.p_height==null?null:n(x.p_height),nation:s(x.p_nation)||null,legacyPlayerId:x.p_id_old==null?null:s(x.p_id_old),active:true,createdAt:now,createdBy:'legacy-import',updatedAt:now,updatedBy:'legacy-import'}})),'players')
await put((tables.get('ff_league')??[]).map(x=>({path:`leagues/${s(x.league_code)}`,data:{name:s(x.league_name),nameEn:s(x.league_name_en)||null,createdAt:now,createdBy:'legacy-import',updatedAt:now,updatedBy:'legacy-import'}})),'leagues')
await put((tables.get('ff_season')??[]).map(x=>({path:`seasons/${seasonId(x.s_name)}`,data:{leagueId:s(x.l_code),name:s(x.s_name),from:date(x.s_fr_date),to:date(x.s_to_date),alias:s(x.s_alias)||null,createdAt:now,createdBy:'legacy-import',updatedAt:now,updatedBy:'legacy-import'}})),'seasons')
await put((tables.get('ff_stadium')??[]).map(x=>({path:`stadiums/${s(x.s_code)}`,data:{name:s(x.s_name),nameKr:s(x.s_name_kr)||null,seats:x.s_seats==null?null:n(x.s_seats),country:s(x.s_country)||null,city:s(x.s_city)||null,homeTeamId:s(x.s_hometeam)||null,surface:s(x.s_ground)||null,createdAt:now,createdBy:'legacy-import',updatedAt:now,updatedBy:'legacy-import'}})),'stadiums')
const contractId=x=>`${s(x.t_code)}_${date(x.pt_begin)}_${isOpen(x.pt_end)?'current':date(x.pt_end)}_${s(x.p_id_old)||'legacy'}`
await put((tables.get('ff_player_team')??[]).map(x=>({path:`players/${s(x.p_id)}/contracts/${contractId(x)}`,data:{teamId:s(x.t_code),leagueId:s(x.l_code)||null,from:date(x.pt_begin),to:isOpen(x.pt_end)?null:date(x.pt_end),no:x.pt_num==null?'':s(x.pt_num),pos:s(x.pt_pos)||'',createdAt:now,createdBy:'legacy-import',updatedAt:now,updatedBy:'legacy-import'}})),'contracts')
await put((tables.get('ff_player_team')??[]).filter(x=>isOpen(x.pt_end)).map(x=>({path:`teams/${s(x.t_code)}/squad/${s(x.p_id)}`,data:{name:'',no:x.pt_num==null?'':s(x.pt_num),pos:s(x.pt_pos)||'',contractId:contractId(x),since:date(x.pt_begin)}})),'squad')
for (const x of (tables.get('ff_player_team')??[]).filter(x=>isOpen(x.pt_end))) {
  const player=documents.get(`players/${s(x.p_id)}`)
  const squad=documents.get(`teams/${s(x.t_code)}/squad/${s(x.p_id)}`)
  if(player) Object.assign(player,{currentTeamId:s(x.t_code),currentNo:x.pt_num==null?'':s(x.pt_num),currentPos:s(x.pt_pos)||''})
  if(squad&&player) squad.name=player.name
}
await put(games.map(x=>({path:`matches/${s(x.gm_id)}`,data:{date:date(x.gm_date),kickoffTime:s(x.gm_time)||null,leagueId:s(x.gm_league),seasonId:s(x.gm_id).slice(0,8),matchType:'league',round:n(x.gm_round),group:s(x.gm_sub_league)||null,stadiumId:s(x.gm_s_code)||null,homeTeamId:s(x.gm_h_t_code),awayTeamId:s(x.gm_a_t_code),score:{home:n(x.gi_goal_home),away:n(x.gi_goal_away)},createdAt:now,updatedAt:now}})),'matches')
await put(infos.map(x=>{const g=byGame.get(s(x.gm_id)),side=s(x.gi_write_code);return {path:`matches/${s(x.gm_id)}/recordings/${side}`,data:{side,teamId:s(side==='H'?g.gm_h_t_code:g.gm_a_t_code),opponentTeamId:s(side==='H'?g.gm_a_t_code:g.gm_h_t_code),status:'final',kpi:{TAP:n(x.gi_tmp),DAP:n(x.gi_tap),TTP:n(x.gi_ttp),DTP:n(x.gi_ctp),BAP:n(x.gi_bap),DTB:n(x.gi_ctb),DTM:n(x.gi_ctm),DTA:n(x.gi_cta),DTS:n(x.gi_cts),SHOT:n(x.gi_sht),ASR:n(x.gi_asr),SSR:n(x.gi_ssr),GOAL:n(x.gi_gol),OG:0},kpiVersion:1,teamRating:null,ratingBasedOn:null,createdAt:now,updatedAt:now}}}),'recordings')
await put((tables.get('ff_game_player')??[]).flatMap(x=>{const i=byInfo.get(s(x.gi_id));if(!i)return[];const g=byGame.get(s(i.gm_id)),side=s(i.gi_write_code);return[{path:`matches/${s(i.gm_id)}/recordings/${side}/playerStats/${s(x.p_id)}`,data:{playerId:s(x.p_id),TAP:n(x.gp_tmp),DAP:n(x.gp_tap),UTP:n(x.gp_utp),DTP:n(x.gp_ctp),TTP:n(x.gp_ttp),SHOT:n(x.gp_sht),AST:n(x.gp_ast),GOAL:n(x.gp_gol),DTB:n(x.gp_ctb),DTM:n(x.gp_ctm),DTA:n(x.gp_cta),DTS:n(x.gp_cts),GTB:n(x.gp_gtb),GTM:n(x.gp_gtm),ASR:n(x.gp_asr),SSR:n(x.gp_ssr),scoreRel:null,scoreAbs:null,score:null,jmx:null,apx:null,tpx:null,fpx:null,ratingBasedOn:null}}]}),'playerStats')
await put((tables.get('ff_game_card')??[]).flatMap(x=>{const i=byInfo.get(s(x.gi_id));if(!i)return[];const g=byGame.get(s(i.gm_id)),side=s(i.gi_write_code);return[{path:`matches/${s(i.gm_id)}/recordings/${side}/cards/${s(x.c_id)}`,data:{playerId:s(x.gp_id),half:half(x.gp_card_half),halfSeconds:n(x.gp_card_time),card:s(x.gp_card_card)==='R'?'R':'Y',createdBy:'legacy-import',createdAt:now}}]}),'cards')
await put((tables.get('ff_game_path')??[]).flatMap(x=>{const i=byInfo.get(s(x.gi_id));if(!i)return[];const g=byGame.get(s(i.gm_id)),side=s(i.gi_write_code);return[{path:`matches/${s(i.gm_id)}/recordings/${side}/paths/${s(x.gt_id)}`,data:{gtId:s(x.gt_id),resCode:s(x.gt_res_code),dsp:!!n(x.gt_csp),ttp:!!n(x.gt_ttp),ptype:n(x.gt_ctp)?'DTP':n(x.gt_utp)?'UTP':n(x.gt_stp)?'STP':'UPP',recordIds:[]}}]}),'paths')
await put((tables.get('ff_game_bap')??[]).flatMap(x=>{const i=byInfo.get(s(x.gi_id));if(!i)return[];const g=byGame.get(s(i.gm_id)),side=s(i.gi_write_code);return[{path:`matches/${s(i.gm_id)}/recordings/${side}/records/${s(x.gr_id)}`,data:{bapReason:'legacy-import'}}]}),'bap')
// ff_game_player owns the squad and player KPI. Its time values are player
// duration values, not the event clock used by LineupEntry.  ST is a legacy
// substitute row; starters enter at the opening whistle, and actual changes
// come exclusively from ff_game_player_log below.
const lineups=new Map();for(const x of tables.get('ff_game_player')??[]){const i=byInfo.get(s(x.gi_id));if(!i)continue;const g=byGame.get(s(i.gm_id)),side=s(i.gi_write_code),key=`${s(i.gm_id)}/${side}`,map=lineups.get(key)??{};const type=s(x.gp_type)==='ST'?'BENCH':'START';map[s(x.p_id)]={slot:s(x.p_id),order:n(x.gp_order),type,no:null,pos:null,name:'',inHalf:type==='START'?'H1':null,inSeconds:type==='START'?0:null,outHalf:null,outSeconds:null};lineups.set(key,map)}
for(const x of tables.get('ff_game_player_log')??[]){const i=byInfo.get(s(x.gi_id));if(!i)continue;const g=byGame.get(s(i.gm_id)),side=s(i.gi_write_code),map=lineups.get(`${s(i.gm_id)}/${side}`);if(!map)continue;const h=half(x.pl_in_half),sec=n(x.pl_in_seconds);if(map[s(x.pl_in_p_id)]){map[s(x.pl_in_p_id)].inHalf=h;map[s(x.pl_in_p_id)].inSeconds=sec}if(map[s(x.pl_out_p_id)]){map[s(x.pl_out_p_id)].outHalf=h;map[s(x.pl_out_p_id)].outSeconds=sec}}
await put([...lineups].map(([key,lineup])=>({path:`matches/${key.split('/')[0]}/recordings/${key.split('/')[1]}`,data:{lineup}})),'lineup')
const out=[],seq=new Map(),pathIds=new Map(),pathRows=new Map();for(const x of tables.get('ff_game_record')??[]){const i=byInfo.get(s(x.gi_id));if(!i)continue;const g=byGame.get(s(i.gm_id)),side=s(i.gi_write_code),h=half(x.gr_half),k=`${i.gm_id}/${side}/${h}/${n(x.gr_half_seconds)}`,order=seq.get(k)??0;seq.set(k,order+1);const pk=`${s(i.gm_id)}/${side}/${s(x.gt_id)}`;if(x.gt_id){pathIds.set(pk,[...(pathIds.get(pk)??[]),s(x.gr_id)]);pathRows.set(pk,[...(pathRows.get(pk)??[]),x])}out.push({path:`matches/${s(i.gm_id)}/recordings/${side}/records/${s(x.gr_id)}`,data:{half:h,halfSeconds:n(x.gr_half_seconds),seq:order,act:s(x.gr_act_code),res:s(x.gr_res_code),area:n(x.gr_area_code),playerId:x.p_id?s(x.p_id):null,createdAt:now,createdBy:'legacy-import',source:'did'}})}
await put(out,'records');await put([...pathIds].map(([k,recordIds])=>{const [gm,side,id]=k.split('/');const path=`matches/${gm}/recordings/${side}/paths/${id}`;if(documents.has(path))return{path,data:{recordIds}};const rows=pathRows.get(k)??[],last=rows.at(-1)??{};return{path,data:{gtId:id,recordIds,ptype:'DTP',dsp:rows.some(r=>!!n(r.gr_is_sht_s)),ttp:true,resCode:s(last.gr_res_code)}}}),'path-recordIds');

return {documents, counts, errors, warnings}
}
