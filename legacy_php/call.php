<?php
use \MatchIsOn\Monitor\Lib4DID;

include_once('./_common.php');
include_once(G5_LIB_PATH."/extra.lib.php");

$func = $_REQUEST['func'];

$res['result' ] = TRUE ;
$res['output' ] = null ;
$res['message'] = ""   ;

//
// FUNC : 로그인
//
if ($func == "login")
{
    $u_id = $_REQUEST['u_id'];
    $u_pw = $_REQUEST['u_pw'];
    $ss_device_id = $_REQUEST['ss_device_id'];

    $user = user_info($u_id, $u_pw);
    if ($user){
        $ss_id = session_check($u_id, $ss_device_id);
        if (!$ss_id){
            $ss_id = session_create($u_id, $ss_device_id);
        }
        $user['t_logo'] = G5_DATA_URL."/team/{$user['t_code']}.png";
        $user['ss_id' ] = $ss_id;
        $res ['output'] = $user;
    }else{
        $res['result' ] = FALSE;
        $res['message'] = "아이디 또는 비밀번호가 일치하지 않습니다.";
    }
}
//
// FUNC : 자동로그인
//
elseif ($func == "login_auto")
{
    $ss_id = $_REQUEST['ss_id'];
    $ss_device_id = $_REQUEST['ss_device_id'];

    $user = session_check_by_id($ss_id, $ss_device_id);
    if ($user){
        $user['t_logo'] = G5_DATA_URL."/team/{$user['t_code']}.png";
        $user['ss_id' ] = $ss_id;
        $res ['output'] = $user;
    }else{
        $res['result' ] = FALSE;
        $res['message'] = "일치하는 세션 정보가 없습니다.";
    }
}
//
// FUNC : 로그아웃
//
elseif ($func == "logout")
{
    $ss_id = $_REQUEST['ss_id'];
    session_remove($ss_id);
}
//
// FUNC : 선수목록
//
elseif ($func == "player_list")
{
  //matchison.wizsoft.kr/call.php?func=player_list&h_t_code=KOR1&a_t_code=COS1&gm_date=2018.09.07
    $h_t_code = $_REQUEST['h_t_code'];
    $a_t_code = $_REQUEST['a_t_code'];
    $gm_date  = $_REQUEST['gm_date' ];

    $res['output'] = player_list($h_t_code, $a_t_code, $gm_date);
}
//
// FUNC : 팀별 포메이션 목록
//
elseif ($func == "formation_list")
{
    //$t_code = $_REQUEST['t_code'];
    $lst = formation_list($t_code);
    if (count($lst) == 0)
        $lst = formation_list("0000");
    $res['output'] = $lst;
}
//
// FUNC : Player Summary (JSASS+JPASS)
//
elseif ($func == "player_summary")
{
    $p_id = $_REQUEST['p_id'];

    if (empty($p_id)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{

        $res['output']['jsass_player'     ] = jsass_player_summary($p_id);
        $res['output']['jsass_recent_test'] = jsass_player_recent_test($p_id);
        $res['output']['jsass_recent_ssr' ] = jsass_player_recent_ssr($p_id);
        $res['output']['jsass_results'    ] = jsass_lastest_results($p_id);

    }
}

//
// FUNC : DID-Play gm_id 단일 경기
//
elseif($func == "dplay_game") {
  $gm_id = $_REQUEST['gm_id'];

  if (empty($gm_id)){
      $res['result' ] = FALSE;
      $res['message'] = "Invalid Arguments";
  } else {
      $res['output'] = dplay_game($gm_id);
  }
}

//
// FUNC : DID-Play 경기 스케듈 목록 조회
//
elseif ($func == "dplay_game_schedule_list")
{
    //http://dev.matchison.wizsoft.kr/call.php?func=dplay_game_schedule_list&league_code=WC2018&month=2018.06
    //$t_code = $_REQUEST['t_code'];
    $month  = $_REQUEST['month' ]; // YYYY.MM
    $league_code = $_REQUEST['league_code'];

    //if (empty($t_code) || empty($month)){
    if (empty($month)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output'] = dplay_game_schedule_list($month, $league_code);
    }
}

elseif ($func == "dplay_game_schedule")
{
    //http://dev.matchison.wizsoft.kr/call.php?func=dplay_game_schedule&league_code=K1&date=2018.07.07
    //$t_code = $_REQUEST['t_code'];
    $date  = $_REQUEST['date' ]; // YYYY.MM.dd
    $league_code = $_REQUEST['league_code'];

    //if (empty($t_code) || empty($month)){
    if (empty($date)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output'] = dplay_game_schedule($date, $league_code);
    }
}

//
// FUNC : DID-Play 경기 설정 저장
//
elseif ($func == "dplay_game_save")
{
    dplay_game_save($_POST);

    if (is_array($_POST['gp_items'])){
        foreach($_POST['gp_items'] as $item){
            $decoded = json_decode(stripslashes($item), TRUE);
            switch(json_last_error())
            {
                case JSON_ERROR_DEPTH:
                    $res['message'] = 'JSON decode error : Maximum stack depth exceeded';
                    break;
                case JSON_ERROR_CTRL_CHAR:
                    $res['message'] = 'JSON decode error : Unexpected control character found';
                    break;
                case JSON_ERROR_SYNTAX:
                    $res['message'] = 'JSON decode error : Syntax error, malformed JSON';
                    break;
                case JSON_ERROR_UTF8:
                    $res['message'] = 'JSON decode error : JSON_ERROR_UTF8';
                    break;
                case JSON_ERROR_STATE_MISMATCH:
                    $res['message'] = 'JSON decode error : JSON_ERROR_STATE_MISMATCH';
                    break;
                case JSON_ERROR_NONE:
                    dplay_game_player_save($decoded);
                    break;
            }
            if (!empty($res['message'])){
                $res['result' ] = FALSE;
                break;
            }
        }
    }
}
//
// FUNC : DID-Play 경기 정보 구하기
//
elseif ($func == "dplay_game_info_for_gmid")
{
    $gm_id = $_REQUEST['gm_id'];
    if (empty($gm_id)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output'] = dplay_game_info_for_gmid($gm_id);
    }
}
elseif ($func == "dplay_game_info")
{
  $gi_id = $_REQUEST['gi_id'];
  if (empty($gi_id)){
      $res['result' ] = FALSE;
      $res['message'] = "Invalid Arguments";
  }else{
      $res['output'] = dplay_game_info($gi_id);
      $res['output']['gp_list'] = dplay_game_player_list($gi_id);
  }
}



//
// FUNC : DID-Play 경기 전/후반전 종료 처리
//
elseif ($func == "dplay_game_finish_half")
{
    $gi_id   = $_REQUEST['gi_id'  ];
    $seconds = $_REQUEST['seconds'];
    if (empty($gi_id) || empty($seconds)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $msg = dplay_game_finish_half($gi_id, $seconds);
        if (!empty($msg)){
            $res['result' ] = FALSE;
            $res['message'] = $msg;
        }
    }
}
//
// FUNC : DID-Play 경기 종료 처리
//
elseif ($func == "dplay_game_finish")
{
    $gi_id = $_REQUEST['gi_id'];
    if (empty($gi_id)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $msg = dplay_game_finish($gi_id);
        if (!empty($msg)){
            $res['result' ] = FALSE;
            $res['message'] = $msg;
        }
    }
}
//
// FUNC : DID-Play 경기 출전선수
//
elseif ($func == "dplay_game_player")
{
    $gi_id = $_REQUEST['gi_id'];
    $p_id  = $_REQUEST['p_id' ];
    if (empty($gi_id) || empty($p_id)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output'] = dplay_game_player($gi_id, $p_id);
    }
}
//
// FUNC : DID-Play 경기 출전선수 목록
//
elseif ($func == "dplay_game_player_list")
{
    $gi_id = $_REQUEST['gi_id'];
    if (empty($gi_id)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output'] = dplay_game_player_list($gi_id);
    }
}
//
// FUNC : DID-Play 선수 교체 처리
//
elseif ($func == "dplay_game_player_change")
{
    $gi_id        = $_REQUEST['gi_id'       ];
    $p_id_fr      = $_REQUEST['p_id_fr'     ];
    $p_id_to      = $_REQUEST['p_id_to'     ];
    $half         = $_REQUEST['half'        ];
    $half_seconds = $_REQUEST['half_seconds'];
    $seconds      = $_REQUEST['seconds'     ];

    $msg = dplay_game_player_change($gi_id, $p_id_fr, $p_id_to, $half, $half_seconds, $seconds);
    if (!empty($msg)){
        $res['result' ] = FALSE;
        $res['message'] = $msg;
    }
}
//
// FUNC : DID-Play 개별 Record 저장
//
elseif ($func == "dplay_game_record_save")
{
    $prev_record = dplay_game_record_save($_POST);

    // 업데이트된 직전 Record와 X, B값을 함께 리턴한다.
    if (!empty($prev_record)){
        $res['output'] = $prev_record;
    }
}
//
// FUNC : DID-Play 개별 Record 선수 업데이트
// http://matchison.wizsoft.kr/call.php?func=dplay_game_record_player&gr_id=b7c4e2e913644f8d91972473c2f3284d&p_id=0000000815
//
elseif ($func == "dplay_game_record_player")
{
    $grid = $_REQUEST['gr_id'];
    $pid  = $_REQUEST['p_id' ];
    if (empty($grid) || empty($pid)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res = dplay_game_record_player($grid, $pid);
        if ($res['result']){
            $res['output'] = TRUE;
        }
    }
}
//
// FUNC : DID-Play Record 목록
//
elseif ($func == "dplay_game_record_list")
{
    $gi_id   = $_REQUEST['gi_id'  ];
    $gr_half = $_REQUEST['gr_half'];
    if (empty($gi_id)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output'] = dplay_game_record_list($gi_id, $gr_half);
    }
}
//
// FUNC : DID-Play 개별 Record 삭제
//
elseif ($func == "dplay_game_record_delete")
{
    $msg = dplay_game_record_delete($_REQUEST['gr_id']);
    if (!empty($msg)){
        $res['result' ] = FALSE;
        $res['message'] = $msg;
    }
}
//
// FUNC : TEST
//
elseif ($func == "test")
{
    $param = '{"dt_pos_y":"496","dt_type_code":"C","dt_no":"9","dt_pos_x":"586","dt_code":"9","dt_id":"209aedab89ef48e898b0c9accb7705ce"}  ';
    $output = print_r(json_decode($param, TRUE), true);
    echo "test=".$output;
}
//
// FUNC : DID-Play 모든 레코드 순회 후 골 정보 불러오기
//
elseif ($func == "dplay_game_reload_goal_data")
{
  $gi_id = $_REQUEST['gi_id'  ];
  $res['output'] = dplay_game_reload_goal_data($gi_id);
}
//
// FUNC : DID-PLAY-INPUT 경기장 그라운드 패턴 변경
//
else if($func =="dplay_stadium_field_upload")
{
  $s_code  = $_REQUEST['s_code'  ];
  $s_ground = $_REQUEST['s_ground'];

  if (empty($s_code) || (empty($s_ground) && $s_ground!=0)) {
      $res['result' ] = FALSE;
      $res['message'] = "Invalid Arguments 1 ".$s_code.$s_ground;
  }else{
      $res['output'] = dplay_stadium_field_upload($s_code, $s_ground);
  }
}
//
// FUNC : DID-Play 개별 Card 저장
//
elseif ($func == "dplay_game_card_save")
{
    $res['output'] = dplay_game_card_save($_POST);
}

elseif ($func == "dplay_game_card_list")
{
  $gi_id   = $_REQUEST['gi_id'  ];
  if (empty($gi_id)) {
      $res['result' ] = FALSE;
      $res['message'] = "Invalid Arguments";
  }else{
    $res['output'] = dplay_game_card_list($gi_id);
  }
}

elseif ($func == "dplay_game_card_delete") {
  $c_id   = $_REQUEST['c_id'  ];
  if (empty($c_id)) {
      $res['result' ] = FALSE;
      $res['message'] = "Invalid Arguments";
  }else{
    $res['output'] = dplay_game_card_delete($c_id);
  }
}


elseif($func == "dplay_load_league_data") {
  $res['output'] = dplay_load_league_data();
}

elseif($func == "dplay_game_card_info") {
  $c_id   = $_REQUEST['c_id'  ];
  if (empty($c_id)) {
      $res['result' ] = FALSE;
      $res['message'] = "Invalid Arguments";
  }else{
  }
}
//
// FUNC : BAP 데이터 저장
//
elseif($func == "dplay_game_bap_save") 
{
  $res['output'] = dplay_game_bap_save($_POST);
}
elseif($func == "dplay_game_bap_load") 
{
  $res['output'] = dplay_game_bap_load($gi_id);
}
//
// FUNC : BAP 건수만 구하기
//
elseif($func == "dplay_game_bap_count") 
{
    $gi_id   = $_REQUEST['gi_id'  ];
    $gr_half = $_REQUEST['gr_half'];

    if (empty($gi_id) || empty($gr_half)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output' ] = dplay_game_bap_count($gi_id, $gr_half);
    }
}
//
// FUNC : BAP 건수 재계산(경기 전체 다시 계산)
//
elseif($func == "dplay_game_bap_reset")
{
    /*
    http://matchison.wizsoft.kr/call.php?func=dplay_game_bap_reset&gi_id=a34e089dfb2a4ac5aff2f6bcc2353588&gr_half=H1
    */
    
    $giid   = $_REQUEST['gi_id'  ];
    $grhalf = $_REQUEST['gr_half'];
    $uplast = $_REQUEST['up_last'];

    if (empty($giid) || empty($grhalf)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output' ] = dplay_game_bap_reset($giid, $grhalf, $uplast);
    }
}
//
// FUNC : DID-Play 플레이어 점수 계산을 위한 해당 gi_id 에 대한 경기정보 다운로드
//
elseif ($func == "dplay_load_game_info_for_score")
{
    //http://dev.matchison.wizsoft.kr/call.php?func=dplay_load_game_info_for_score&gi_id=3237dbf505164400b9e3f2f2ccbd122e

    $gi_id  = $_REQUEST['gi_id' ]; // ff_game id

    if (empty($gi_id)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output' ] = dplay_load_game_info_for_score($gi_id);
    }
}

//
// FUNC : DID-Play 경기 종료/수정시 플레이어별 평점 저장 test
//
elseif ($func == "dplay_player_score_save")
{
      //return "0";
      //return print_r($_POST['datas'], true);
      //dplay_game_save($_POST);
//      $res['output'] = dplay_player_score_save($_POST);

       if (is_array($_POST['datas'])){
          foreach($_POST['datas'] as $data){
              $decoded = json_decode(stripslashes($data), TRUE);
              switch(json_last_error())
              {
                  case JSON_ERROR_DEPTH:
                      $res['message'] = 'JSON decode error : Maximum stack depth exceeded';
                      break;
                  case JSON_ERROR_CTRL_CHAR:
                      $res['message'] = 'JSON decode error : Unexpected control character found';
                      break;
                  case JSON_ERROR_SYNTAX:
                      $res['message'] = 'JSON decode error : Syntax error, malformed JSON';
                      break;
                  case JSON_ERROR_UTF8:
                      $res['message'] = 'JSON decode error : JSON_ERROR_UTF8';
                      break;
                  case JSON_ERROR_STATE_MISMATCH:
                      $res['message'] = 'JSON decode error : JSON_ERROR_STATE_MISMATCH';
                      break;
                  case JSON_ERROR_NONE:
                      //$res['message'] = print_r($decoded, true);
                      dplay_player_score_save($_POST['gi_id'], $decoded);
                      //dplay_player_card_save($_POST['gi_id']);
                      break;
              }
              if (!empty($res['message'])){
                  $res['result' ] = FALSE;
                  break;
              }
          }
      }
}
//
// 경기 선수별 카드 발급건수 갱신
//
elseif ($func == "dplay_player_card_save") {
  $gi_id  = $_REQUEST['gi_id' ]; // ff_game id
  $res['output' ] = dplay_player_card_save($gi_id);
}

elseif ($func == "mycommit") {
  dplay_player_card_save_commit($gi_id);
}

elseif($func == "dplay_get_all_giid_by_league") {
  $league_code  = $_REQUEST['league_code' ]; // ff_game id
  $res['output' ] = dplay_get_all_giid_by_league($league_code);
}

elseif($func == "dplay_migration_gr_shoot_rate") {
  //http://dev.matchison.wizsoft.kr/call.php?func=dplay_migration_gr_shoot_rate
  $res['output'] = dplay_migration_gr_shoot_rate();
}

elseif($func == "dplay_migration_gi_team_score") {
  //http://dev.matchison.wizsoft.kr/call.php?func=dplay_migration_gi_team_score&gi_id=0078a68d965544d68e0ca93a3eda70b1
  $gi_id  = $_REQUEST['gi_id' ]; // ff_game id

  if (empty($gi_id)){
      $res['result' ] = FALSE;
      $res['message'] = "Invalid Arguments";
  }else{
      $res['output' ] = dplay_migration_gi_team_score($gi_id);
  }
}

elseif($func == "dplay_migration_load_gi_id_list") {
  // http://dev.matchison.wizsoft.kr/call.php?func=dplay_migration_load_gi_id_list&league_code=K1
      $league_code  = $_REQUEST['league_code' ]; // ff_game id
      $res['output' ] = dplay_migration_load_gi_id_list($league_code);
}

elseif($func == "dplay_game_set_time") {
  // http://dev.matchison.wizsoft.kr/call.php?func=dplay_game_set_time&gi_id=25ecc49004534520bae5d52689adb4f4&half_code=h3&time=-3
      $gi_id  = $_REQUEST['gi_id' ]; // ff_game id
      $half_code  = $_REQUEST['half_code' ]; // ff_game id
      $time  = $_REQUEST['time' ]; // ff_game id

      if (empty($gi_id) || empty($half_code) || empty($time)){
          $res['result' ] = FALSE;
          $res['message'] = "Invalid Arguments";
      }else{
          $res['output' ] = dplay_game_set_time($gi_id, $half_code, $time);
      }
}

// elseif($func == "dplay_migration_gr_part_pos") {
//   //http://dev.matchison.wizsoft.kr/call.php?func=dplay_migration_gr_part_pos
//   $res['output'] = dplay_migration_gr_part_pos();
//}

//
// FUNC : DID-Play 공격루트 업데이트
// http://matchison.wizsoft.kr/call.php?func=dplay_game_path_update
//
elseif ($func == "dplay_game_path_update")
{
    $json = json_decode_ex($_REQUEST['gt_info']);
    $gtinfo = $json['result'];
    
    if (!$gtinfo || !$_REQUEST['gr_list']){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $grlist = array();
        if (is_array($_REQUEST['gr_list'])){
            foreach($_REQUEST['gr_list'] as $json_str){
                $json = json_decode_ex($json_str);
                $item = $json['result'];
                if ($item === FALSE){
                    $res = $json;
                    break;
                }
                $grlist[] = $item;
            }
        }
        if ($res['result']){
            $res = dplay_game_path_update($gtinfo, $grlist, TRUE, TRUE);
            if ($res['result']){
                $res['output'] = $g_path_newer;
            }
        }
    }
}
//
// FUNC : DID-Play 공격루트 재계산(경기 전체 다시 계산)
//
elseif($func == "dplay_game_path_reset")
{
    /*
    http://matchison.wizsoft.kr/call.php?func=dplay_game_path_reset&gi_id=71c41e8d646b48b4af7108c69be04d0d&gr_half=H2
    */

    $giid   = $_REQUEST['gi_id'  ];
    $grhalf = $_REQUEST['gr_half'];

    if (empty($giid) || empty($grhalf)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output' ] = dplay_game_path_reset($giid, $grhalf);
    }
}
//
// FUNC : DID-Play 공격루트 Garbage Collection
//
elseif($func == "dplay_game_path_clean")
{
    /*
    http://matchison.wizsoft.kr/call.php?func=dplay_game_path_clean&gi_id=1124cb47d501405c9be99067b4f0f476&gr_half=H1
    */

    $giid   = $_REQUEST['gi_id'  ];
    $grhalf = $_REQUEST['gr_half'];

    if (empty($giid)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output' ] = dplay_game_path_clean($giid, $grhalf, TRUE);
    }
}
//
// FUNC : 경기별(gi) 선수 연봉정보 재계산
//
elseif($func == "salary_reset_game_team")
{
    // http://did.matchison.com/call.php?func=salary_reset_game_team&gi_id=1124cb47d501405c9be99067b4f0f476

    $giid = $_REQUEST['gi_id'];

    if (empty($giid)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $ret = salary_reset_game_team($giid);
        if ($ret === TRUE)
            $res['output' ] = TRUE;
        else{
            $res['result' ] = FALSE;
            $res['message'] = $ret;
        }
    }
}
//
// FUNC: 게임(gm) 방송 이벤트 리스트 조회
//
elseif($func == "broadcast_server_list")
{
    $res['output'] = broadcast_server_list();
}
//
// FUNC: 게임(gm) 방송 이벤트 리스트 조회
//
elseif($func == "broadcast_event_list")
{
    $gmid = $_REQUEST['gm_id'];
    $filters = $_REQUEST['filters'];
    
    if (empty($gmid)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    } else {
        $res['output'] = broadcast_event_list($gmid, $filters);
    }
}
//
// FUNC: 게임(gm) 방송 이벤트 리스트 조회
//
elseif($func == "broadcast_event_clear")
{
    $gmid = $_REQUEST['gm_id'];

    if (empty($gmid)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    } else {
        $res['output'] = broadcast_event_clear($gmid);
    }
}
//
// FUNC: 게임(gm) 방송 이벤트 리스트 조회
//
elseif($func == "broadcast_event_time_set")
{
    $gmid = $_REQUEST['gm_id'];
    $halfCode = $_REQUEST['half_code'];
    $time = $_REQUEST['time'];
    $order = $_REQUEST['order'];

    if ($order <= 0) {
        $res['result' ] = FALSE;
        $res['message'] = "Set correct order";
    } elseif (empty($gmid)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    } else {
        $res['output'] = broadcast_event_time_set($gmid, $halfCode, $time, $order);
    }
}
//
// FUNC : 선수 교체내역 저장
//
elseif ($func == 'change_game_player_complete')
{
    $gi_id      = $_REQUEST['gi_id'   ];
    $t_code     = $_REQUEST['t_code'  ];
    $p_id_fr    = $_REQUEST['p_id_fr' ];
    $p_id_to    = $_REQUEST['p_id_to' ];
    $position   = $_REQUEST['position'];
    $half       = $_REQUEST['half'    ];
    $seconds    = $_REQUEST['seconds' ];
    $order      = $_REQUEST['order'   ];
    
    $result = change_game_player_complete($gi_id, $t_code, $p_id_fr, $p_id_to, $position, $half, $seconds, $order);
    if (is_string($result)){
        $res['result' ] = FALSE;
        $res['message'] = $result;
    } else {
        $res['output'] = $result;
    }
}
//
// FUNC : 선수 교체내역 목록
//
elseif ($func == 'changed_game_player_list')
{
    $gi_id  = $_REQUEST['gi_id' ];
    $t_code = $_REQUEST['t_code'];
    
    if (empty($gi_id) || empty($t_code)){
        $res['result' ] = FALSE;
        $res['message'] = "Invalid Arguments";
    }else{
        $res['output' ] = get_changed_game_player_list($gi_id, $t_code);;
    }
}
//
// FUNC : 선수 교체내역 수정
//
elseif ($func == 'update_changed_game_player')
{
    $prev = $_REQUEST['prev'];
    $curr = $_REQUEST['curr'];

    $res['output'] = update_changed_player($prev, $curr);
}
//
// FUNC : 선수 교체내역 삭제
//
elseif ($func == 'delete_changed_player')
{
    $row = $_REQUEST['row'];

    $res['output'] = delete_changed_player($row);
}
//
// FUNC : 모니터 테스트01
//
elseif ($func == 'monitor_test01')
{
    //$monitor = new Lib4DID();
    
    $res['output'] = $monitor->event("match", "state");
    
    if(!$res['output' ]){
        $res['result' ] = FALSE;
        $res['message'] = $monitor->last_error();
    }
}
//
// FUNC : Error
//
else
{
    $res['result' ] = FALSE;
    $res['message'] = "Invalid function";
}

//
// Write Log
//
//call_log($func, $_REQUEST, $res);

//
// Response
//
echo json_encode($res);
exit();
