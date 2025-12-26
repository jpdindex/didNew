
/*
Ground = 971 * 634 // 경기장 전체 크기
Bitmap =  44 *  44 // 볼마커 아이콘 크기

[R01:L18] Area CX:CY = 153:182 / X=  0~152 / Y=452~634 / Event= 91:565
[R02:L17] Area CX:CY = 153:271 / X=  0~152 / Y=181~451 / Event= 77:347
[R03:L16] Area CX:CY = 153:181 / X=  0~152 / Y=  0~180 / Event= 96: 66
[R04:L15] Area CX:CY = 166:182 / X=153~318‬ / Y=452~634 / Event=234:564
[R05:L14] Area CX:CY = 166:271 / X=153~318‬ / Y=181~451 / Event=249:368
[R06:L13] Area CX:CY = 166:181 / X=153~318‬ / Y=  0~180 / Event=235: 89
[R07:L12] Area CX:CY = 166:182 / X=319‬~484‬ / Y=452~634 / Event=426:572
[R08:L11] Area CX:CY = 166:271 / X=319‬~484‬ / Y=181~451 / Event=411:351
[R09:L10] Area CX:CY = 166:181 / X=319‬~484‬ / Y=  0~180 / Event=420: 88
[R10:L09] Area CX:CY = 167:182 / X=485‬~651‬ / Y=452~634 / Event=572:573
[R11:L08] Area CX:CY = 167:271 / X=485‬~651‬ / Y=181~451 / Event=596:374
[R12:L07] Area CX:CY = 167:181 / X=485‬~651‬ / Y=  0~180 / Event=609:101
[R13:L06] Area CX:CY = 167:182 / X=652‬~818‬ / Y=452~634 / Event=731:561
[R14:L05] Area CX:CY = 167:271 / X=652‬~818‬ / Y=181~451 / Event=730:383
[R15:L04] Area CX:CY = 167:181 / X=652‬~818‬ / Y=  0~180 / Event=745:105
[R16:L03] Area CX:CY = 152:182 / X=819‬~971 / Y=452~634 / Event=906:557
[R17:L02] Area CX:CY = 152:271 / X=819‬~971 / Y=181~451 / Event=902:348
[R18:L01] Area CX:CY = 152:181 / X=819‬~971 / Y=  0~180   Event=908:100
*/

/*
update ff_game_record
   set gr_area_code_org = gr_area_code
 where 1=1
;
*/

/*
select *
     , case when posx >=   0 and posx <= 152 and posy >= 452 and posy <= 634 then if(dir='L', '18',   '1')
            when posx >=   0 and posx <= 152 and posy >= 181 and posy <= 451 then if(dir='L', '17',   '2')
            when posx >=   0 and posx <= 152 and posy >=   0 and posy <= 180 then if(dir='L', '16',   '3')
            when posx >= 153 and posx <= 318 and posy >= 452 and posy <= 634 then if(dir='L', '15',   '4')
            when posx >= 153 and posx <= 318 and posy >= 181 and posy <= 451 then if(dir='L', '14',   '5')
            when posx >= 153 and posx <= 318 and posy >=   0 and posy <= 180 then if(dir='L', '13',   '6')
            when posx >= 319 and posx <= 484 and posy >= 452 and posy <= 634 then if(dir='L', '12',   '7')
            when posx >= 319 and posx <= 484 and posy >= 181 and posy <= 451 then if(dir='L', '11',   '8')
            when posx >= 319 and posx <= 484 and posy >=   0 and posy <= 180 then if(dir='L', '10',   '9')
            when posx >= 485 and posx <= 651 and posy >= 452 and posy <= 634 then if(dir='L',  '9',  '10')
            when posx >= 485 and posx <= 651 and posy >= 181 and posy <= 451 then if(dir='L',  '8',  '11')
            when posx >= 485 and posx <= 651 and posy >=   0 and posy <= 180 then if(dir='L',  '7',  '12')
            when posx >= 652 and posx <= 818 and posy >= 452 and posy <= 634 then if(dir='L',  '6',  '13')
            when posx >= 652 and posx <= 818 and posy >= 181 and posy <= 451 then if(dir='L',  '5',  '14')
            when posx >= 652 and posx <= 818 and posy >=   0 and posy <= 180 then if(dir='L',  '4',  '15')
            when posx >= 819 and posx <= 971 and posy >= 452 and posy <= 634 then if(dir='L',  '3',  '16')
            when posx >= 819 and posx <= 971 and posy >= 181 and posy <= 451 then if(dir='L',  '2',  '17')
            when posx >= 819 and posx <= 971 and posy >=   0 and posy <= 180 then if(dir='L',  '1',  '18')
       end area_code
  from (
        select gm.gm_id
             , gi.gi_id
             , gi.gi_write_code
             , gi.gi_part
             , gr.gr_id
             , gr.gr_half
             , gr.gr_pos_x + 22 posx -- DID에서 저장 시 볼마커 모서리 좌표로 저장됨
             , gr.gr_pos_y + 22 posy -- 볼마커 이미지 크기 : 22 * 22 반영
             , gr.gr_area_code  area_code_old
             , case when gi.gi_part = 'L' and gr.gr_half in ('H1','H3') then 'L'
                    when gi.gi_part = 'L' and gr.gr_half in ('H2','H4') then 'R'
                    when gi.gi_part = 'R' and gr.gr_half in ('H1','H3') then 'R'
                    when gi.gi_part = 'R' and gr.gr_half in ('H2','H4') then 'L'
               end dir
          from ff_game_record gr
          join ff_game_info   gi
            on gi.gi_id = gr.gi_id
          join ff_game		    gm
            on gm.gm_id = gi.gm_id
         where 1=1
           and gm.is_old = '1'
         order by
               gm.gm_date
             , gm.gm_id
             , gr.gi_id
             , gr.gr_seconds
             , gr.gr_regdt
         limit 10000
       ) m1
*/

update ff_game_record gr
     , (
        select gr_id
             , case when posx >=   0 and posx <= 152 and posy >= 452 and posy <= 634 then if(dir='L', '18',   '1')
                    when posx >=   0 and posx <= 152 and posy >= 181 and posy <= 451 then if(dir='L', '17',   '2')
                    when posx >=   0 and posx <= 152 and posy >=   0 and posy <= 180 then if(dir='L', '16',   '3')
                    when posx >= 153 and posx <= 318 and posy >= 452 and posy <= 634 then if(dir='L', '15',   '4')
                    when posx >= 153 and posx <= 318 and posy >= 181 and posy <= 451 then if(dir='L', '14',   '5')
                    when posx >= 153 and posx <= 318 and posy >=   0 and posy <= 180 then if(dir='L', '13',   '6')
                    when posx >= 319 and posx <= 484 and posy >= 452 and posy <= 634 then if(dir='L', '12',   '7')
                    when posx >= 319 and posx <= 484 and posy >= 181 and posy <= 451 then if(dir='L', '11',   '8')
                    when posx >= 319 and posx <= 484 and posy >=   0 and posy <= 180 then if(dir='L', '10',   '9')
                    when posx >= 485 and posx <= 651 and posy >= 452 and posy <= 634 then if(dir='L',  '9',  '10')
                    when posx >= 485 and posx <= 651 and posy >= 181 and posy <= 451 then if(dir='L',  '8',  '11')
                    when posx >= 485 and posx <= 651 and posy >=   0 and posy <= 180 then if(dir='L',  '7',  '12')
                    when posx >= 652 and posx <= 818 and posy >= 452 and posy <= 634 then if(dir='L',  '6',  '13')
                    when posx >= 652 and posx <= 818 and posy >= 181 and posy <= 451 then if(dir='L',  '5',  '14')
                    when posx >= 652 and posx <= 818 and posy >=   0 and posy <= 180 then if(dir='L',  '4',  '15')
                    when posx >= 819 and posx <= 971 and posy >= 452 and posy <= 634 then if(dir='L',  '3',  '16')
                    when posx >= 819 and posx <= 971 and posy >= 181 and posy <= 451 then if(dir='L',  '2',  '17')
                    when posx >= 819 and posx <= 971 and posy >=   0 and posy <= 180 then if(dir='L',  '1',  '18')
               end area_code
          from (
                select gr.gr_id
                     , gr.gr_pos_x + 22 posx -- DID에서 저장 시 볼마커 모서리 좌표로 저장됨
                     , gr.gr_pos_y + 22 posy -- 볼마커 이미지 크기 : 22 * 22 반영
                     , case when gi.gi_part = 'L' and gr.gr_half in ('H1','H3') then 'L'
                            when gi.gi_part = 'L' and gr.gr_half in ('H2','H4') then 'R'
                            when gi.gi_part = 'R' and gr.gr_half in ('H1','H3') then 'R'
                            when gi.gi_part = 'R' and gr.gr_half in ('H2','H4') then 'L'
                       end dir
                  from ff_game_record gr
                  join ff_game_info   gi
                    on gi.gi_id = gr.gi_id
                  join ff_game		    gm
                    on gm.gm_id = gi.gm_id
                 where 1=1
                   and gm.is_old != 'N'
                 order by
                       gm.gm_date
                     , gm.gm_id
                     , gr.gi_id
                     , gr.gr_seconds
                     , gr.gr_regdt
--               limit 10000
               ) m1
       ) mm
   set gr.gr_area_code = mm.area_code
 where gr.gr_id = mm.gr_id
;
   