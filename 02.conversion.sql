/*
 * ff_game
 */
ALTER TABLE `ff_game`
	CHANGE COLUMN `is_old` `is_old` VARCHAR(1) NULL DEFAULT 'N' COMMENT '마이그레이션 데이터 여부 (N:없음, F:FIFA데이터, B:BIGDB데이터)' AFTER `gi_goal_away`
;

update ff_game
   set is_old = if(is_old = '1', 'F', 'B')
 where 1=1
;

/*
 * ff_game_info
 */
truncate table `ff_game_info`
;
insert into `ff_game_info`
     ( gi_id
	 , gm_id
	 , gi_user_id
	 , gi_write_code
	 , gi_h_t_code
	 , gi_a_t_code
	 , gi_state
	 , gi_part
	 , gi_formation
	 , gi_h1_begin
	 , gi_h2_begin
	 , gi_h3_begin
	 , gi_h4_begin
	 , gi_h1_seconds
	 , gi_h2_seconds
	 , gi_h3_seconds
	 , gi_h4_seconds
	 , gi_seconds
	 , gi_regdt
	 , gi_moddt
	 , gi_tmp
	 , gi_tmp_sc
	 , gi_tmp_sr
	 , gi_tap
	 , gi_tap_sc
	 , gi_tap_sr
	 , gi_utp
	 , gi_ctp
	 , gi_stp
	 , gi_ttp
	 , gi_ttp_sc
	 , gi_ttp_sr
	 , gi_ttp_pr
	 , gi_sht
	 , gi_gol
	 , gi_bap
	 , gi_ctb
	 , gi_ctm
	 , gi_cta
	 , gi_cts
	 , gi_gtb
	 , gi_gtm
	 , gi_asr
	 , gi_gsr
	 , gi_ssr
	 , gi_score
	 , is_old
     )
select gi_id         -- gi_id
     , gm_id         -- gm_id
     , gi_user_id    -- gi_user_id
     , gi_write_code -- gi_write_code
     , gi_h_t_code   -- gi_h_t_code
     , gi_a_t_code   -- gi_a_t_code
     , gi_state      -- gi_state
     , gi_part       -- gi_part
     , gi_formation  -- gi_formation
     , gi_h1_begin   -- gi_h1_begin
     , gi_h2_begin   -- gi_h2_begin
     , gi_h3_begin   -- gi_h3_begin
     , gi_h4_begin   -- gi_h4_begin
     , gi_h1_seconds -- gi_h1_seconds
     , gi_h2_seconds -- gi_h2_seconds
     , gi_h3_seconds -- gi_h3_seconds
     , gi_h4_seconds -- gi_h4_seconds
     , gi_seconds    -- gi_seconds
     , gi_regdt      -- gi_regdt
     , gi_moddt      -- gi_moddt
     , 0             -- gi_tmp
     , 0             -- gi_tmp_sc
     , 0             -- gi_tmp_sr
     , 0             -- gi_tap
     , 0             -- gi_tap_sc
     , 0             -- gi_tap_sr
     , 0             -- gi_utp
     , 0             -- gi_ctp
     , 0             -- gi_stp
     , 0             -- gi_ttp
     , 0             -- gi_ttp_sc
     , 0             -- gi_ttp_sr
     , 0             -- gi_ttp_pr
     , 0             -- gi_sht
     , 0             -- gi_gol
     , 0             -- gi_bap
     , 0             -- gi_ctb
     , 0             -- gi_ctm
     , 0             -- gi_cta
     , 0             -- gi_cts
     , 0             -- gi_gtb
     , 0             -- gi_gtm
     , 0             -- gi_asr
     , 0             -- gi_gsr
     , 0             -- gi_ssr
     , 0             -- gi_score
     , if(is_old = 1, 'F', 'B') -- is_old
  from mig_game_info
 WHERE gi_id IS NOT NULL
   AND gi_id != ''
   and gm_id IS NOT NULL
   AND gm_id != ''
;


/*
 * ff_game_path (이관할 데이터 없음)
 */

/*
 * ff_game_player
 */
truncate table `ff_game_player`
;
insert into `ff_game_player`
     ( gi_id
     , p_id
     , gp_type
     , gp_order
     , gp_in_half
     , gp_in_half_seconds
     , gp_in_seconds
     , gp_out_half
     , gp_out_half_seconds
     , gp_out_seconds
     , gp_seconds
     , gp_point_plus
     , gp_point_minus
     , is_old
     , gp_tmp
     , gp_tmp_sc
     , gp_tmp_sr
     , gp_tmp_tr
     , gp_tap
     , gp_tap_sc
     , gp_tap_sr
     , gp_tap_tr
     , gp_utp
     , gp_ctp
     , gp_ttp
     , gp_ttp_sc
     , gp_ttp_sr
     , gp_ttp_tr
     , gp_ttp_pr
     , gp_sht
     , gp_sht_sc
     , gp_sht_tr
     , gp_ast
     , gp_gol
     , gp_gol_tr
     , gp_ctb
     , gp_ctm
     , gp_cta
     , gp_cts
     , gp_gtb
     , gp_gtm
     , gp_asr
     , gp_ssr
     , gp_score_rel
     , gp_score_abs
     , gp_score
     )
select gp.gi_id                 -- gi_id
     , gp.p_id                  -- p_id
     , gp.gp_type               -- gp_type
     , gp.gp_order              -- gp_order
     , gp.gp_in_half            -- gp_in_half
     , gp.gp_in_half_seconds    -- gp_in_half_seconds
     , gp.gp_in_seconds         -- gp_in_seconds
     , gp.gp_out_half           -- gp_out_half
     , gp.gp_out_half_seconds   -- gp_out_half_seconds
     , gp.gp_out_seconds        -- gp_out_seconds
     , gp.gp_seconds            -- gp_seconds
     , gp.gp_point_plus         -- gp_point_plus
     , gp.gp_point_minus        -- gp_point_minus
     , ifnull(gi.is_old,'N')    -- is_old
     , 0                        -- gp_tmp
     , 0                        -- gp_tmp_sc
     , 0                        -- gp_tmp_sr
     , 0                        -- gp_tmp_tr
     , 0                        -- gp_tap
     , 0                        -- gp_tap_sc
     , 0                        -- gp_tap_sr
     , 0                        -- gp_tap_tr
     , 0                        -- gp_utp
     , 0                        -- gp_ctp
     , 0                        -- gp_ttp
     , 0                        -- gp_ttp_sc
     , 0                        -- gp_ttp_sr
     , 0                        -- gp_ttp_tr
     , 0                        -- gp_ttp_pr
     , 0                        -- gp_sht
     , 0                        -- gp_sht_sc
     , 0                        -- gp_sht_tr
     , 0                        -- gp_ast
     , 0                        -- gp_gol
     , 0                        -- gp_gol_tr
     , 0                        -- gp_ctb
     , 0                        -- gp_ctm
     , 0                        -- gp_cta
     , 0                        -- gp_cts
     , 0                        -- gp_gtb
     , 0                        -- gp_gtm
     , 0                        -- gp_asr
     , 0                        -- gp_ssr
     , 0                        -- gp_score_rel
     , 0                        -- gp_score_abs
     , 0                        -- gp_score
  from mig_game_player gp
  left outer
  join ff_game_info gi
    on gi.gi_id = gp.gi_id
;


/*
 * ff_game_record
 */
truncate table `ff_game_record`
;
insert into `ff_game_record`
	 ( gr_id
	 , gi_id
	 , p_id
	 , t_code
	 , gr_half
	 , gr_half_seconds
	 , gr_seconds
	 , gr_area_code
	 , gr_area_code_org
	 , gr_act_code
	 , gr_res_code
	 , gr_pos_x
	 , gr_pos_y
	 , gr_part_pos_x
	 , gr_part_pos_y
	 , gr_shoot_pos_x
	 , gr_shoot_pos_y
	 , gr_shoot_rate_x
	 , gr_shoot_rate_y
	 , gt_id
	 , gr_is_tmp
	 , gr_is_tmp_s
	 , gr_is_tap
	 , gr_is_tap_s
	 , gr_is_ast
	 , gr_is_sht
	 , gr_is_sht_s
	 , gr_is_gol
	 , gr_is_ctb
	 , gr_is_ctm
	 , gr_is_cta
	 , gr_is_cts
	 , gr_is_gtb
	 , gr_is_gtm
	 , gr_regdt
	 , gr_moddt
	 , is_old
	 )
select gr.gr_id              -- gr_id
	 , gr.gi_id              -- gi_id
	 , gr.p_id               -- p_id
	 , gr.t_code             -- t_code
	 , gr.gr_half            -- gr_half
	 , gr.gr_half_seconds    -- gr_half_seconds
	 , gr.gr_seconds         -- gr_seconds
	 , gr.gr_area_code       -- gr_area_code
	 , gr.gr_area_code       -- gr_area_code_org
	 , gr.gr_act_code        -- gr_act_code
	 , gr.gr_res_code        -- gr_res_code
	 , gr.gr_pos_x           -- gr_pos_x
	 , gr.gr_pos_y           -- gr_pos_y
	 , gr.gr_part_pos_x      -- gr_part_pos_x
	 , gr.gr_part_pos_y      -- gr_part_pos_y
	 , gr.gr_shoot_pos_x     -- gr_shoot_pos_x
	 , gr.gr_shoot_pos_y     -- gr_shoot_pos_y
	 , gr.gr_shoot_rate_x    -- gr_shoot_rate_x
	 , gr.gr_shoot_rate_y    -- gr_shoot_rate_y
	 , 0                     -- gt_id
	 , 0                     -- gr_is_tmp
	 , 0                     -- gr_is_tmp_s
	 , 0                     -- gr_is_tap
	 , 0                     -- gr_is_tap_s
	 , 0                     -- gr_is_ast
	 , 0                     -- gr_is_sht
	 , 0                     -- gr_is_sht_s
	 , 0                     -- gr_is_gol
	 , 0                     -- gr_is_ctb
	 , 0                     -- gr_is_ctm
	 , 0                     -- gr_is_cta
	 , 0                     -- gr_is_cts
	 , 0                     -- gr_is_gtb
	 , 0                     -- gr_is_gtm
	 , gr.gr_regdt           -- gr_regdt
	 , gr.gr_moddt           -- gr_moddt
	 , ifnull(gi.is_old,'N') -- is_old
  from mig_game_record gr
  left outer
  join ff_game_info gi
    on gi.gi_id = gr.gi_id
;
