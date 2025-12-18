# DID Event Contract (from legacy call.php)

## 공통 구조

- 엔드포인트: `call.php`
- 메소드: 주로 `POST` (일부 GET 가능성)
- 공통 파라미터:
  - `func`: 수행할 기능 이름
  - 기타 공통 값들 필요 시 추가

---

## func = login

- 설명: DID 장비 / 앱 로그인
- 요청 파라미터:
  - `u_id` (string): 사용자 ID (분석관 ID)
  - `u_pw` (string): 비밀번호
  - `ss_device_id` (string): 디바이스 고유 ID
- 처리 로직 요약:
  - `user_info(u_id, u_pw)` 로 사용자 조회
  - 세션 체크 → 없으면 `session_create(u_id, ss_device_id)`
  - 팀 로고 경로: `G5_DATA_URL."/team/{$user['t_code']}.png"`
- 응답 예시 구조:
  - `result` (bool)
  - `output` (user 객체 or null)
  - `message` (string): 실패 시 에러 메시지

---

## func = auto_login

- 설명: DID 장비 / 앱 실행 시 기존 로그인 세션으로 자동 로그인 처리  
  (사용자가 비밀번호를 다시 입력하지 않아도 됨)

- 요청 파라미터:
  - `ss_id` (string): 저장된 세션 ID
  - `ss_device_id` (string): 기기 고유값  
    (session이 동일 기기에서 생성된 것인지 검증하기 위함)

- 처리 로직 요약:
  - `session_check(ss_id, ss_device_id)` 로 세션이 유효한지 확인
  - 세션이 유효하면:
    - 해당 세션의 사용자 정보를 조회 (`user_info_by_session`)
    - 동일 경로로 팀 로고 이미지 값 세팅 (`G5_DATA_URL."/team/{$user['t_code']}.png"`)
  - 세션이 만료되었거나 장비 ID가 일치하지 않으면:
    - 로그인 실패 처리
    - 메시지 예: `"세션이 만료되었습니다. 다시 로그인 해주세요."`

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (object | null): 로그인 사용자 정보 (성공 시)
  - `message` (string): 에러 또는 안내 메시지

  ---

## func = Logout

- 설명: DID 장비 / 앱에서 현재 로그인 세션을 종료하는 기능

- 요청 파라미터:
  - `ss_id` (string): 종료할 세션 ID

- 처리 로직 요약:
  - `session_remove(ss_id)` 를 호출해 해당 세션 정보를 서버에서 삭제
  - 별도의 추가 처리나 데이터 반환 없이, 세션만 정리

- 응답 구조:
  - `result` (bool): 기본값 `TRUE` (정상 호출 시)
  - `output` (null): 별도 데이터 없음
  - `message` (string): 기본적으로 빈 문자열 (에러 발생 시에만 메시지 세팅)

   ---

## func = Player_list

- 설명: 특정 경기 (홈팀 vs 원정팀, 특정 날짜)의 선수 목록을 조회하는 기능 
  -> 경기 선택 후, 양 팀 선수 명단을 DID에서 불러올 떄 사용 

- 요청 파라미터:
 - `h_t_code`: (string): 홈 팀 코드 
 - `a_t_code`: (string): 원정 팀 코드 
 - `gm_date`: (string) 경기 날짜 (예: `"2018.09.07"`)

 - 처리 로직 요약:
    - `player_list(h_t_code, a_t_code, gm_date)` 함수를 호출
  - 함수 내부에서:
    - 해당 날짜, 해당 팀 조합에 맞는 경기/선수 데이터를 조회
    - 홈/원정 팀 각각의 선수 정보(선수 ID, 등번호, 포지션 등)를 리스트 형태로 구성
  - 결과를 그대로 `$res['output']` 에 세팅하여 반환

  - 응답 구조:
    - `result` (bool): 성공 여부
    - `output` (array): 선수 목록 리스트  
       - 각 요소는 선수 정보를 담은 객체/배열 (예: `p_id`, `name`, `back_no`, `position` 등)
    - `message` (string): 에러 또는 안내 메시지 (정상일 때는 보통 빈 문자열)


   ---
## func = formation_list - 팀별 포메이션 목록

- 설명:  
  특정 팀의 포메이션(전술 배치) 목록을 조회하는 기능  
  → 팀별로 미리 저장된 포메이션 템플릿을 DID에서 선택할 때 사용

- 요청 파라미터:
  - `t_code` (string, optional): 팀 코드  
    - 코드가 유효한 포메이션을 찾지 못하면 `"0000"` 기준의 기본 포메이션으로 대체

- 처리 로직 요약:
  - 요청에서 `t_code` 를 받아 `formation_list(t_code)` 호출
  - 반환된 리스트의 개수가 `0` 이면:
    - `formation_list("0000")` 을 다시 호출해 기본 포메이션 목록 조회
  - 최종적으로 얻은 포메이션 리스트를 `$res['output']` 에 세팅

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (array): 포메이션 목록 리스트  
    - 각 요소는 포메이션 코드, 이름, 포지션 배치 정보 등을 포함
  - `message` (string): 에러 또는 안내 메시지

---

## func = player_summary

- 설명:  
  특정 선수의 종합 리포트(JSASS + JPASS 관련 지표)를 조회하는 기능  
  → 선수 개별 분석 화면에서 사용되는 요약데이터

- 요청 파라미터:
  - `p_id` (string): 선수 ID

- 처리 로직 요약:
  - `p_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"` 로 설정 후 종료
  - 유효한 `p_id` 인 경우:
    - `jsass_player_summary(p_id)` 결과를  
      `$res['output']['jsass_player']` 에 저장
    - `jsass_player_recent_test(p_id)` 결과를  
      `$res['output']['jsass_recent_test']` 에 저장
    - `jsass_player_recent_ssr(p_id)` 결과를  
      `$res['output']['jsass_recent_ssr']` 에 저장
    - `jsass_lastest_results(p_id)` 결과를  
      `$res['output']['jsass_results']` 에 저장
  - 즉, 한 번의 호출로 선수 요약, 최근 테스트, 최근 SSR, 최근 결과까지 한 번에 패키징해서 내려줌

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (object|null):
    - `jsass_player` : 선수 기본 요약 정보
    - `jsass_recent_test` : 최근 테스트 기록 요약
    - `jsass_recent_ssr` : 최근 SSR 관련 지표
    - `jsass_results` : 최근 시험/평가 결과 리스트
  - `message` (string):  
    - 파라미터 오류 시 `"Invalid Arguments"`  
    - 그 외 에러/안내 메시지 또는 정상 시 빈 문자열


---

## func = dplay_game - DID-Play gm_id 단일 경기

- 설명:  
  특정 `gm_id` 기준의 단일 경기 DID-Play 정보를 조회하는 기능  
  (이미 등록된 경기 1건에 대한 상세 정보 조회)

- 요청 파라미터:
  - `gm_id` (string): 경기 고유 ID

- 처리 로직 요약:
  - `gm_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `gm_id` 인 경우:
    - `dplay_game(gm_id)` 호출
    - 반환값을 그대로 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (object | null):  
    - 경기 기본 정보 및 DID-Play 관련 설정/데이터 포함
  - `message` (string): 에러 또는 안내 메시지


---

## func = dplay_game_schedule_list - DID-Play 경기 스케듈 목록 조회

- 설명:  
  특정 리그, 특정 월에 해당하는 DID-Play 경기 스케줄 목록 조회  
  (예: 월별 경기 캘린더용 리스트)

- 요청 파라미터:
  - `month` (string): 조회할 월 (형식: `YYYY.MM`, 예: `"2018.06"`)
  - `league_code` (string): 리그 코드 (예: `K1`, `WC2018` 등)

- 처리 로직 요약:
  - `month` 값이 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `month` 인 경우:
    - `dplay_game_schedule_list(month, league_code)` 호출
    - 반환된 스케줄 리스트를 `$res['output']` 에 저장
  - 원래 코드에서는 `t_code` 사용 여부를 주석 처리해둔 상태  
    → 현재 버전에서는 `month` + `league_code` 조합이 핵심 파라미터

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (array):  
    - 해당 월에 포함된 경기 스케줄 목록  
    - 각 요소에 `gm_id`, 날짜, 팀 정보 등 포함
  - `message` (string): 에러 또는 안내 메시지


  ---

## func = dplay_game_schedule - DID-Play 경기 스케줄

- 설명:  
  특정 리그, 특정 날짜의 DID-Play 경기 스케줄 조회  
  (일자 단위 경기 목록, match-day 스케줄)

- 요청 파라미터:
  - `date` (string): 조회할 날짜 (형식: `YYYY.MM.dd`, 예: `"2018.07.07"`)
  - `league_code` (string): 리그 코드

- 처리 로직 요약:
  - `date` 값이 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `date` 인 경우:
    - `dplay_game_schedule(date, league_code)` 호출
    - 반환된 스케줄 리스트를 `$res['output']` 에 저장
  - 주석 상 예시:
    - `http://dev.matchison.wizsoft.kr/call.php?func=dplay_game_schedule&league_code=K1&date=2018.07.07`

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (array):  
    - 해당 날짜에 속한 경기 리스트 (gm_id, 팀, 킥오프 타임 등)
  - `message` (string): 에러 또는 안내 메시지

  ---

## func = dplay_game_save  - 경기 설정 저장

- 설명:  
  DID-Play 경기 설정 및 선수별 설정 정보를 저장하는 기능  
  (경기 단위 설정 + 선수별 개별 설정을 한 번에 저장)

- 요청 파라미터:
  - `$_POST` 전체 사용  
    - 경기 설정 관련 필드들 (경기 기본 정보, 옵션 등)  
    - `gp_items` (array): 선수별 설정 정보가 담긴 JSON 문자열 배열  
      - 각 요소는 JSON string 형태 → decode 후 개별 플레이어 설정 저장

- 처리 로직 요약:
  1. `dplay_game_save($_POST)` 호출  
     - 경기 레벨의 기본 설정 저장 (예: 경기 메타 정보, 공통 옵션 등)
  2. `$_POST['gp_items']` 가 배열인 경우:
     - 각 `$item` 을 순회하면서:
       - `stripslashes($item)` → `json_decode(..., TRUE)` 로 배열 변환
       - `json_last_error()` 결과에 따라 에러 체크:
         - `JSON_ERROR_DEPTH` → `"Maximum stack depth exceeded"`
         - `JSON_ERROR_CTRL_CHAR` → `"Unexpected control character found"`
         - `JSON_ERROR_SYNTAX` → `"Syntax error, malformed JSON"`
         - `JSON_ERROR_UTF8` → `"JSON_ERROR_UTF8"`
         - `JSON_ERROR_STATE_MISMATCH` → `"JSON_ERROR_STATE_MISMATCH"`
         - `JSON_ERROR_NONE` 인 경우에만:
           - `dplay_game_player_save($decoded)` 호출  
             → 개별 선수 설정 저장
       - 에러 메시지가 세팅되면:
         - `res['result'] = FALSE`
         - 반복문 중단 (더 이상 처리하지 않음)
  3. JSON 파싱 에러가 없고, 모든 선수 설정이 정상 저장되면:
     - `result` 는 기본 TRUE 상태 유지

- 응답 구조:
  - `result` (bool):  
    - 전체 저장 과정 중 하나라도 JSON 에러 발생 시 `FALSE`
    - 모두 정상 저장되면 `TRUE`
  - `output` (null): 별도 출력 없음
  - `message` (string):  
    - JSON 파싱 에러 또는 기타 문제 발생 시 에러 메시지 저장  
    - 정상 저장 시 대부분 빈 문자열


    ---

## func = dplay_game_info_for_gmid - 경기 정보 구하기

- 설명:  
  `gm_id` 기준으로 DID-Play 경기 정보를 조회하는 기능  
  (경기 ID로 단일 경기 정보를 직접 가져올 때 사용)

- 요청 파라미터:
  - `gm_id` (string): 경기 ID

- 처리 로직 요약:
  - `gm_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `gm_id` 인 경우:
    - `dplay_game_info_for_gmid(gm_id)` 호출
    - 반환 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (object | null):  
    - 해당 경기의 DID-Play 설정 및 관련 메타 정보
  - `message` (string): 에러 또는 안내 메시지


---

## func = dplay_game_info  - 경기 정보 구하기

- 설명:  
  `gi_id` 기준으로 DID-Play 경기 및 선수 설정 정보를 함께 조회하는 기능  
  (경기 정보 + 그 경기에 등록된 선수 리스트까지 한 번에 가져옴)

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID (game info id)

- 처리 로직 요약:
  - `gi_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `gi_id` 인 경우:
    - `dplay_game_info(gi_id)` 호출 결과를 `$res['output']` 에 저장  
      → 경기 정보(메타, 설정값 등)
    - `dplay_game_player_list(gi_id)` 호출 결과를  
      `$res['output']['gp_list']` 에 추가 저장  
      → 이 경기와 연결된 선수별 DID-Play 설정 리스트
  - 따라서 이 func 하나로:
    - 경기 기본 정보 + 선수 설정 리스트를 동시에 조회 가능

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (object | null):
    - `...` : 경기 정보 필드들 (리그, 팀, 날짜, 옵션 등)
    - `gp_list` (array): 선수별 설정 리스트
  - `message` (string): 에러 또는 안내 메시지

  ---

## func = dplay_game_finish_half

- 설명:  
  DID-Play 경기에서 전반전 / 후반전 종료 처리를 수행하는 기능  
  (해당 하프의 실제 진행 시간(초)을 기록하고 상태를 마감)

- 요청 파라미터:
  - `gi_id`   (string): 경기 정보 ID (game info id)
  - `seconds` (int|string): 해당 전/후반이 진행된 실제 시간 (초 단위)

- 처리 로직 요약:
  - `gi_id` 또는 `seconds` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 값일 경우:
    - `dplay_game_finish_half(gi_id, seconds)` 호출
    - 반환된 `$msg` 값이 비어있지 않으면:
      - 에러로 간주
      - `result = FALSE`
      - `message = $msg` 로 설정
    - `$msg` 가 비어 있으면 정상 종료

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (null): 별도 데이터 없음
  - `message` (string): 에러 또는 안내 메시지

  ---

## func = dplay_game_finish

- 설명:  
  DID-Play 경기 전체를 종료 처리하는 기능  
  (전/후반 포함 전체 경기를 마감하고 최종 상태를 확정)

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID

- 처리 로직 요약:
  - `gi_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `gi_id` 인 경우:
    - `dplay_game_finish(gi_id)` 호출
    - 반환된 `$msg` 가 비어있지 않으면:
      - `result = FALSE`
      - `message = $msg`
    - `$msg` 가 비어 있으면 정상 종료로 간주

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (null): 별도 데이터 없음
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_game_player

- 설명:  
  DID-Play에서 특정 경기(`gi_id`)에 출전한 특정 선수(`p_id`)의 정보를 조회하는 기능

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID
  - `p_id`  (string): 선수 ID

- 처리 로직 요약:
  - `gi_id` 또는 `p_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 값일 경우:
    - `dplay_game_player(gi_id, p_id)` 호출
    - 반환값을 `$res['output']` 에 그대로 저장

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (object | null):  
    - 경기 내에서의 선수 정보 및 설정값 (포지션, 출전 상태 등)
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_game_player_list

- 설명:  
  DID-Play에서 특정 경기(`gi_id`)의 출전 선수 전체 목록을 조회하는 기능

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID

- 처리 로직 요약:
  - `gi_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `gi_id` 인 경우:
    - `dplay_game_player_list(gi_id)` 호출
    - 반환된 선수 목록을 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (array):  
    - 경기 출전 선수 리스트 (각 요소에 선수 ID, 등번호, 포지션, 상태 등 포함)
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_game_player_change

- 설명:  
  DID-Play 경기 중 선수 교체를 처리하는 기능  
  (나가는 선수, 들어오는 선수, 교체 시점 정보까지 함께 기록)

- 요청 파라미터:
  - `gi_id`        (string): 경기 정보 ID
  - `p_id_fr`      (string): 교체로 나가는 선수 ID (from)
  - `p_id_to`      (string): 교체로 들어오는 선수 ID (to)
  - `half`         (int|string): 교체가 발생한 전/후반 정보 (예: 1, 2)
  - `half_seconds` (int|string): 해당 하프 내 경과 시간(초 단위)
  - `seconds`      (int|string): 경기 전체 기준 경과 시간(초 단위)

- 처리 로직 요약:
  - 위 파라미터들을 이용해 `dplay_game_player_change(...)` 호출
  - 반환된 `$msg` 가 비어있지 않으면:
    - 에러로 간주
    - `result = FALSE`
    - `message = $msg`
  - `$msg` 가 비어 있으면 정상 처리로 간주

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (null): 별도 데이터 없음
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_game_record_save

- 설명:  
  DID-Play에서 개별 이벤트(Record)를 저장하는 기능  
  (패스/슈팅/태클 등 단일 플레이 기록 1건을 생성 또는 수정)

- 요청 파라미터:
  - `$_POST` 전체 사용  
    - 하나의 플레이(Record)에 대한 모든 정보(선수, 위치, 시간, 액션 코드 등)

- 처리 로직 요약:
  - `dplay_game_record_save($_POST)` 호출
  - 반환값을 `$prev_record` 에 받음:
    - 이 값은 “업데이트된 직전 Record와 X, B 값” 등을 포함
  - `prev_record` 가 비어 있지 않으면:
    - `$res['output'] = $prev_record` 로 설정 (프론트에서 직전 상태 업데이트용)

- 응답 구조:
  - `result` (bool): 기본적으로 TRUE (내부에서 에러 처리 시 FALSE)
  - `output` (object | null):  
    - 직전에 저장/수정된 레코드 정보 및 관련 보정값(X, B 등)
  - `message` (string): 에러 메시지 또는 빈 문자열

---

## func = dplay_game_record_player

- 설명:  
  DID-Play에서 특정 Record(`gr_id`)에 연결된 선수 정보를 변경하는 기능  
  (기록 자체는 유지한 채, 해당 기록의 선수만 교체)

- 요청 파라미터:
  - `gr_id` (string): 기록(Record) ID
  - `p_id`  (string): 새로 설정할 선수 ID

- 처리 로직 요약:
  - `gr_id` 또는 `p_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 값일 경우:
    - `dplay_game_record_player(gr_id, p_id)` 호출  
      → 이 함수의 반환값 자체를 `$res` 에 그대로 대입
    - 그 결과에서 `res['result']` 가 TRUE일 경우:
      - `res['output'] = TRUE` 를 추가로 세팅
      - (즉, 성공 플래그 역할)

- 응답 구조:
  - `result` (bool): 성공 여부 (`dplay_game_record_player` 내부 결과에 따름)
  - `output` (bool | null):
    - 성공 시 `TRUE`
    - 실패 시 미설정 또는 null
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_game_record_list

- 설명:  
  DID-Play에서 특정 경기(`gi_id`)에 대한 Record 목록을 조회하는 기능  
  (옵션으로 `gr_half` 를 넘겨 전/후반 별 조회 가능)

- 요청 파라미터:
  - `gi_id`   (string): 경기 정보 ID
  - `gr_half` (int|string, optional): 조회할 하프 정보 (예: 1, 2, 또는 전체)

- 처리 로직 요약:
  - `gi_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `gi_id` 인 경우:
    - `dplay_game_record_list(gi_id, gr_half)` 호출
    - 반환된 Record 목록을 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (array):  
    - 해당 경기(및 선택한 하프)에 소속된 개별 Record 리스트
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_game_record_delete

- 설명:  
  DID-Play에서 특정 개별 Record(플레이 기록)를 삭제하는 기능

- 요청 파라미터:
  - `gr_id` (string): 삭제할 Record ID  
    (`$_REQUEST['gr_id']` 로 입력 받음)

- 처리 로직 요약:
  - `dplay_game_record_delete($_REQUEST['gr_id'])` 호출
  - 반환된 `$msg` 가 비어있지 않으면:
    - 삭제 과정에서 문제가 발생한 것으로 간주
    - `result = FALSE`
    - `message = $msg`
  - `$msg` 가 비어 있으면 정상 삭제

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (null): 별도 데이터 없음
  - `message` (string): 에러 또는 안내 메시지

---

## func = test

- 설명:  
  단순 테스트용 기능. JSON 문자열을 decode 후 출력해주는 디버그 목적의 엔드포인트.

- 요청 파라미터:  
  - 없음 (코드 내부에서 고정된 `$param` 사용)

- 처리 로직 요약:
  - `$param` 에 하드코딩된 JSON 문자열 존재
  - `json_decode($param, TRUE)` 실행 후 결과 배열을 출력용 문자열로 변환
  - `echo "test=".$output` 형태로 화면에 직접 출력 (일반적인 DID 응답 포맷과 다름)

- 응답 구조:
  - *일반적인 JSON 응답이 아닌 직접 echo 출력 형태*
  - 개발/테스트 용도로만 사용됨

---

## func = dplay_game_reload_goal_data

- 설명:  
  특정 경기(`gi_id`)에 대해 DID-Play 전체 레코드(Record)를 다시 순회하여  
  “골 관련 데이터”를 재계산/재로드하는 기능

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID

- 처리 로직 요약:
  - `dplay_game_reload_goal_data(gi_id)` 호출
  - 함수 내에서:
    - 해당 경기의 모든 Record 를 다시 스캔하고
    - 골 관련 이벤트를 다시 분석/정리하여 반환
  - 반환값을 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (object | array): 골 데이터 재계산 결과
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_stadium_field_upload

- 설명:  
  경기장(스타디움)의 그라운드 패턴 값을 변경/업로드하는 기능  
  (예: 잔디 패턴 모양을 변경하는 UI에서 호출됨)

- 요청 파라미터:
  - `s_code`   (string): 스타디움 코드
  - `s_ground` (string|int): 선택한 그라운드 패턴 값  
    - 단, 숫자 0도 유효한 값이므로 `empty()` 처리에서 예외 처리 수행  
      → `(empty($s_ground) && $s_ground != 0)` 로 확인

- 처리 로직 요약:
  - `s_code` 가 비어 있거나 `s_ground` 가 비정상적으로 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments 1 " . $s_code . $s_ground`
  - 정상 값일 경우:
    - `dplay_stadium_field_upload(s_code, s_ground)` 호출
    - 반환값을 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (mixed): 업데이트된 경기장 패턴 정보
  - `message` (string): 에러 또는 안내 메시지

---

## func = dplay_game_card_save

- 설명:  
  DID-Play에서 개별 Card(카드 이벤트 — 예: 경고/퇴장 등)를 저장하는 기능

- 요청 파라미터:
  - `$_POST` 전체 사용  
    - 카드 이벤트 정보(선수, 시간, 카드 타입 등)가 포함됨

- 처리 로직 요약:
  - `dplay_game_card_save($_POST)` 호출
  - 반환값을 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool): 내부 처리 결과에 따름
  - `output` (mixed): 저장된 카드 정보 또는 상태
  - `message` (string): 에러 메시지 또는 빈 문자열

---

## func = dplay_game_card_list

- 설명:  
  특정 경기(`gi_id`)의 카드(Card) 이벤트 목록을 조회하는 기능

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID

- 처리 로직 요약:
  - `gi_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 `gi_id` 인 경우:
    - `dplay_game_card_list(gi_id)` 호출
    - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result`  (bool)
  - `output`  (array): 카드 이벤트 목록  
  - `message` (string)

---

## func = dplay_game_card_delete

- 설명:  
  특정 카드 이벤트(`c_id`)를 삭제하는 기능

- 요청 파라미터:
  - `c_id` (string): 카드 ID

- 처리 로직 요약:
  - `c_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 경우:
    - `dplay_game_card_delete(c_id)` 호출
    - 반환값을 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 삭제된 카드 데이터 또는 상태
  - `message` (string)

---

## func = dplay_load_league_data

- 설명:  
  DID-Play 에서 사용하는 리그 관련 기본 데이터 전체를 로드하는 기능  
  (리그 리스트, 팀 데이터, 시즌 기본값 등을 패키지로 가져옴)

- 요청 파라미터:
  - 없음

- 처리 로직 요약:
  - `dplay_load_league_data()` 호출
  - 반환 데이터를 `$res['output']` 에 세팅

- 응답 구조:
  - `result` (bool)
  - `output` (object | array): 리그 관련 데이터 패키지
  - `message` (string)

---

## func = dplay_game_card_info

- 설명:  
  특정 카드(`c_id`)의 상세 정보를 조회하는 기능  
  (단, 원본 코드에서는 **실제 처리 로직이 비어 있음**)

- 요청 파라미터:
  - `c_id` (string): 카드 ID

- 처리 로직 요약:
  - `c_id` 가 비어 있으면:
    - `result = FALSE`
    - `message = "Invalid Arguments"`
  - 유효한 경우:
    - 실제 코드에서는 아직 구현되어 있지 않음  
      (빈 블록 → 추후 로직 추가 가능)

- 응답 구조:
  - 현재 구현상:
    - `result` (bool): 기본 TRUE
    - `output`: 없음
    - `message`: 없음  
  - ※ 필요 시 코드 확장 가능

---

## func = dplay_game_bap_save

- 설명:  
  BAP(Ball Action Pattern) 데이터 저장 기능.  
  특정 경기에서 발생한 BAP 관련 정보를 저장할 때 사용됨.

- 요청 파라미터:
  - `$_POST` 전체  
    (BAP 데이터 구조가 JSON 또는 key-value 형태로 포함)

- 처리 로직 요약:
  - `dplay_game_bap_save($_POST)` 호출
  - 반환 값을 그대로 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 저장된 BAP 데이터 또는 저장 결과
  - `message` (string)

---

## func = dplay_game_bap_load

- 설명:  
  특정 경기(`gi_id`)의 BAP 데이터를 조회하는 기능

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID

- 처리 로직 요약:
  - `dplay_game_bap_load(gi_id)` 호출  
  - 반환 값 전체를 `$res['output']` 에 저장

- 응답 구조:
  - `output` (mixed): BAP 데이터 전체  
  - `result`, `message`: 기본 성공 기준

---

## func = dplay_game_bap_count

- 설명:  
  특정 경기/하프(`gr_half`) 기준 BAP 건수를 조회하는 기능

- 요청 파라미터:
  - `gi_id`   (string): 경기 정보 ID  
  - `gr_half` (string): 조회할 하프 (예: `"H1"`, `"H2"`)

- 처리 로직 요약:
  - `gi_id` 또는 `gr_half` 가 없으면:
    - `result = FALSE`, `message = "Invalid Arguments"`
  - 유효한 경우:
    - `dplay_game_bap_count(gi_id, gr_half)` 호출

- 응답 구조:
  - `result` (bool)
  - `output` (int): BAP 총 카운트
  - `message` (string)

---

## func = dplay_game_bap_reset

- 설명:  
  특정 경기(`gi_id`)의 BAP 데이터를 전체 재계산하는 기능  
  (기록 전체를 다시 순회하여 BAP를 재집계)

- 요청 파라미터:
  - `gi_id`   (string)
  - `gr_half` (string)
  - `up_last` (optional): 마지막 업데이트 여부

- 처리 로직 요약:
  - `gi_id` 또는 `gr_half` 비어있으면 Invalid
  - 정상일 경우:
    - `dplay_game_bap_reset(gi_id, gr_half, up_last)` 호출

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 재계산된 BAP 데이터
  - `message` (string)

---

## func = dplay_load_game_info_for_score

- 설명:  
  경기 종료 후 “플레이어 평점 계산”에 필요한 기초 경기정보를 로드하는 기능

- 요청 파라미터:
  - `gi_id` (string): ff_game id

- 처리 로직 요약:
  - `gi_id` 없으면 Invalid
  - 있으면:
    - `dplay_load_game_info_for_score(gi_id)` 호출  
      → 평점 계산에 필요한 선수/기록/경기 메타 데이터 등이 포함됨

- 응답 구조:
  - `result` (bool)
  - `output` (object or array): 평점 계산용 경기정보
  - `message` (string)

---

## func = dplay_player_score_save

- 설명:  
  경기 종료 또는 수정 시, 선수별 평점을 저장하는 기능  
  (여러 선수 점수를 한 번에 저장 가능)

- 요청 파라미터:
  - `$_POST['datas']`: JSON 문자열 배열  
    - 각 요소가 한 선수의 평점 데이터 구조임
  - `$_POST['gi_id']`: 경기 ID (각 score 저장 시 함께 사용)

- 처리 로직 요약:
  1. `datas` 배열을 순회
  2. 각 `$data` 를 `json_decode(stripslashes($data), TRUE)` 로 파싱
  3. JSON 에러 체크:
     - `JSON_ERROR_DEPTH`  
     - `JSON_ERROR_CTRL_CHAR`  
     - `JSON_ERROR_SYNTAX`  
     - `JSON_ERROR_UTF8`  
     - `JSON_ERROR_STATE_MISMATCH`  
  4. 에러 발생 시:
     - `result = FALSE`
     - `message = 해당 에러 메시지`
     - 즉시 중단
  5. 정상(`JSON_ERROR_NONE`) 이면:
     - `dplay_player_score_save($_POST['gi_id'], $decoded)` 수행  
       → 선수별 평점 저장 완료

- 응답 구조:
  - `result` (bool)
  - `output` (null 또는 성공 플래그)
  - `message` (string): JSON 에러 또는 저장 상태

---

## func = dplay_player_card_save

- 설명:  
  특정 경기(`gi_id`)에 대해 선수별 카드 발급 건수를 새로 계산/저장하는 기능

- 요청 파라미터:
  - `gi_id` (string)

- 처리 로직 요약:
  - `dplay_player_card_save(gi_id)` 호출
  - 반환값을 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed)
  - `message` (string)

---

## func = mycommit

- 설명:  
  선수 카드 저장 작업을 최종 commit 처리하는 기능  
  (내부 트랜잭션 확정 역할로 보임)

- 요청 파라미터:
  - `gi_id` (string)

- 처리 로직 요약:
  - `dplay_player_card_save_commit(gi_id)` 호출

- 응답 구조:
  - 표준 JSON 구조 동일 (코드에는 별도 값 설정 없음)

---

## func = dplay_get_all_giid_by_league

- 설명:  
  특정 리그(`league_code`)의 모든 `gi_id` 목록을 조회하는 기능

- 요청 파라미터:
  - `league_code` (string)

- 처리 로직 요약:
  - `dplay_get_all_giid_by_league(league_code)` 호출  
  - 반환값을 `$res['output']` 에 저장

- 응답 구조:
  - `output` (array): gi_id 리스트  
  - 기타 기본 응답 구조 동일

---

## func = dplay_migration_gr_shoot_rate

- 설명:  
  기존 Record 데이터의 슈팅 정보(`shoot_rate`)를 마이그레이션하는 기능  
  (레거시 데이터 정리용)

- 요청 파라미터: 없음

- 처리 로직 요약:
  - `dplay_migration_gr_shoot_rate()` 호출
  - 결과값을 그대로 `$res['output']` 에 저장

- 응답:
  - `output` (mixed): 마이그레이션 처리 결과

---

## func = dplay_migration_gi_team_score

- 설명:  
  기존 경기(`gi_id`)의 팀 스코어 데이터를 재계산/마이그레이션하는 기능

- 요청 파라미터:
  - `gi_id` (string)

- 처리 로직 요약:
  - `gi_id` 없으면 Invalid
  - 있으면:
    - `dplay_migration_gi_team_score(gi_id)` 호출  
      → 팀 전체 득점 정보 업데이트

- 응답 구조:
  - `result` (bool)
  - `output` (mixed)
  - `message` (string)

---

## func = dplay_migration_load_gi_id_list

- 설명:  
  특정 리그(`league_code`) 기준으로 마이그레이션 대상 `gi_id` 목록을 불러오는 기능

- 요청 파라미터:
  - `league_code` (string)

- 처리 로직 요약:
  - `dplay_migration_load_gi_id_list(league_code)` 호출

- 응답 구조:
  - `output` (array): gi_id 리스트

---

## func = dplay_game_set_time

- 설명:  
  특정 경기(`gi_id`)의 특정 하프(`half_code`) 시간 값을 직접 수정하는 기능  
  (예: 보정 값 업데이트, 후반전 시간 조정 등)

- 요청 파라미터:
  - `gi_id`     (string)
  - `half_code` (string) — 예: `"h1"`, `"h2"`, `"h3"`
  - `time`      (string|int): 설정할 시간 값

- 처리 로직 요약:
  - `gi_id`, `half_code`, `time` 중 하나라도 비어 있으면 Invalid
  - 유효하면:
    - `dplay_game_set_time(gi_id, half_code, time)` 호출
    - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed)
  - `message` (string)

---

## func = dplay_game_path_update

- 설명:  
  DID-Play 경기에서 공격 루트(패스 경로 등)를 업데이트하는 기능.  
  하이라이트용 공격 루트나 시각화용 경로 데이터를 재정의할 때 사용.

- 요청 파라미터:
  - `gt_info` (string, JSON): 공격 루트 전체 정보(JSON 문자열)  
    - `json_decode_ex(gt_info)` → `$gtinfo = $json['result']`
  - `gr_list` (array of string, JSON):  
    - 각 요소는 개별 Record(플레이) 정보에 대한 JSON 문자열  
    - 예: 각 패스/슈팅/빌드업 경로에 해당하는 정보들

- 처리 로직 요약:
  1. `gt_info` 를 `json_decode_ex()` 로 파싱 → `$gtinfo`  
     - 파싱 실패 또는 `$gtinfo` 가 falsy 이면 Invalid
  2. `gr_list` 가 비어 있는 경우도 Invalid 처리
  3. `gr_list` 가 배열이면:
     - 각 `$json_str` 에 대해 `json_decode_ex($json_str)` 호출
     - `$item = $json['result']` 추출
     - `$item === FALSE` 이면:
       - `$res = $json` (디코딩 에러 정보 그대로 응답)
       - 반복 중단
     - 그렇지 않으면 `$grlist[]` 에 `$item` 추가
  4. 위 과정에서 `$res['result']` 가 TRUE 유지되고 있으면:
     - `dplay_game_path_update($gtinfo, $grlist, TRUE, TRUE)` 호출
     - 반환된 `$res['result']` 가 TRUE 이면:
       - `$res['output'] = $g_path_newer` 로 최신 공격 루트 정보 반환
       - (`$g_path_newer` 는 글로벌/외부에서 세팅되는 최신 경로 정보)

- 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (mixed): 업데이트된 공격 루트 정보 (`$g_path_newer`)
  - `message` (string): JSON 디코드 에러 및 기타 에러 메시지

---

## func = dplay_game_path_reset

- 설명:  
  특정 경기/하프에 대해 공격 루트를 **처음부터 다시 계산**하는 기능.  
  (기존 기록을 기반으로 전체 경로를 재구성)

- 요청 파라미터:
  - `gi_id`   (string): 경기 정보 ID
  - `gr_half` (string): 하프 구분 (예: `"H1"`, `"H2"`)

- 처리 로직 요약:
  - `gi_id` 또는 `gr_half` 가 비어 있으면 Invalid
  - 유효할 경우:
    - `dplay_game_path_reset(giid, grhalf)` 호출
    - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 재계산된 공격 루트 정보
  - `message` (string)

---

## func = dplay_game_path_clean

- 설명:  
  특정 경기/하프에 대한 공격 루트의 **Garbage Collection** 수행.  
  (유효하지 않은 루트, 불완전한 데이터 등을 정리)

- 요청 파라미터:
  - `gi_id`   (string): 경기 정보 ID
  - `gr_half` (string): 하프 구분

- 처리 로직 요약:
  - `gi_id` 가 비어 있으면 Invalid
  - 그렇지 않으면:
    - `dplay_game_path_clean(giid, grhalf, TRUE)` 호출  
      → 세 번째 인자인 TRUE 는 강제/전체 정리 여부로 추정

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): GC 처리 결과
  - `message` (string)

---

## func = salary_reset_game_team

- 설명:  
  특정 경기(`gi_id`)를 기준으로, 해당 경기 양 팀/선수의 연봉 관련 정보를 재계산하는 기능.

- 요청 파라미터:
  - `gi_id` (string): 경기 정보 ID

- 처리 로직 요약:
  - `gi_id` 가 비어 있으면 Invalid
  - 유효할 경우:
    - `salary_reset_game_team(giid)` 호출
    - 반환값이 `TRUE` 이면:
      - `$res['output'] = TRUE`
    - 문자열(에러 메시지)면:
      - `result = FALSE`
      - `message = $ret`

- 응답 구조:
  - `result` (bool)
  - `output` (bool | null): 성공 시 TRUE
  - `message` (string): 에러 메시지 또는 빈 문자열

---

## func = broadcast_server_list

- 설명:  
  방송 서버 목록을 조회하는 기능.  
  (방송/스트리밍용 서버 정보 리스트)

- 요청 파라미터:
  - 없음

- 처리 로직 요약:
  - `broadcast_server_list()` 호출
  - 결과를 `$res['output']` 에 세팅

- 응답 구조:
  - `result` (bool)
  - `output` (array): 방송 서버 목록
  - `message` (string)

---

## func = broadcast_event_list

- 설명:  
  특정 경기(`gm_id`)에 대한 방송 이벤트 리스트 조회 기능.  
  (중계용 이벤트들: 골, 슈팅, 하이라이트 포인트 등)

- 요청 파라미터:
  - `gm_id`   (string): 경기 ID
  - `filters` (mixed): 필터 조건 (이벤트 타입/시간대 필터 등)

- 처리 로직 요약:
  - `gm_id` 가 비어 있으면 Invalid
  - 유효할 경우:
    - `broadcast_event_list(gmid, filters)` 호출
    - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (array): 방송 이벤트 리스트
  - `message` (string)

---

## func = broadcast_event_clear

- 설명:  
  특정 경기(`gm_id`)에 설정된 방송 이벤트 리스트를 모두 초기화(삭제)하는 기능.

- 요청 파라미터:
  - `gm_id` (string): 경기 ID

- 처리 로직 요약:
  - `gm_id` 가 비어 있으면 Invalid
  - 유효하면:
    - `broadcast_event_clear(gmid)` 호출
    - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 삭제/초기화 처리 결과
  - `message` (string)

---

## func = broadcast_event_time_set

- 설명:  
  특정 경기 방송 이벤트의 시간 정보를 설정/수정하는 기능.  
  (이벤트 시간, 하프 코드, 순서(order) 값을 재지정)

- 요청 파라미터:
  - `gm_id`     (string): 경기 ID
  - `half_code` (string): 하프 코드
  - `time`      (string|int): 설정할 시간 값
  - `order`     (int): 이벤트 순서 (1 이상)

- 처리 로직 요약:
  - `order <= 0` 인 경우:
    - `result = FALSE`
    - `message = "Set correct order"`
  - 그 외, `gm_id` 가 비어 있으면 Invalid
  - 모두 유효하면:
    - `broadcast_event_time_set(gmid, halfCode, time, order)` 호출
    - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed)
  - `message` (string)

---

## func = change_game_player_complete

- 설명:  
  선수 교체 내역을 최종 확정/저장하는 기능.  
  (선수 교체 데이터를 하나의 기록으로 완성)

- 요청 파라미터:
  - `gi_id`    (string): 경기 정보 ID
  - `t_code`   (string): 팀 코드
  - `p_id_fr`  (string): 나가는 선수 ID
  - `p_id_to`  (string): 들어오는 선수 ID
  - `position` (string): 교체 후 포지션
  - `half`     (string|int): 교체 발생 하프
  - `seconds`  (int|string): 경기 진행 시간(초)
  - `order`    (int): 교체 순번

- 처리 로직 요약:
  - `change_game_player_complete(...)` 호출
  - 반환값이 문자열이면:
    - 에러 메시지로 간주
    - `result = FALSE`
    - `message = result`
  - 문자열이 아닌 경우:
    - `result` 는 TRUE
    - `output = result` (저장된 교체 정보)

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 교체 결과 데이터
  - `message` (string)

---

## func = changed_game_player_list

- 설명:  
  특정 경기(`gi_id`), 특정 팀(`t_code`) 기준 선수 교체 내역 목록 조회 기능.

- 요청 파라미터:
  - `gi_id`  (string)
  - `t_code` (string)

- 처리 로직 요약:
  - `gi_id` 또는 `t_code` 가 비어 있으면 Invalid
  - 유효한 경우:
    - `get_changed_game_player_list(gi_id, t_code)` 호출
    - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (array): 선수 교체 내역 리스트
  - `message` (string)

---

## func = update_changed_game_player

- 설명:  
  기존에 저장된 선수 교체 내역을 수정하는 기능.

- 요청 파라미터:
  - `prev` (mixed): 수정 전 데이터
  - `curr` (mixed): 수정 후 데이터

- 처리 로직 요약:
  - `update_changed_player(prev, curr)` 호출
  - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 수정 결과
  - `message` (string)

---

## func = delete_changed_player

- 설명:  
  특정 선수 교체 내역을 삭제하는 기능.

- 요청 파라미터:
  - `row` (mixed): 삭제 대상 row 정보 (ID 또는 구조체)

- 처리 로직 요약:
  - `delete_changed_player(row)` 호출
  - 결과를 `$res['output']` 에 저장

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 삭제 결과
  - `message` (string)

---

## func = monitor_test01

- 설명:  
  모니터링 테스트용 기능.  
  `Lib4DID` 기반 `event("match", "state")` 호출 결과를 확인하는 디버그/테스트용 엔드포인트.

- 요청 파라미터:
  - 없음 (코드 내부에서 `$monitor` 객체 사용)

- 처리 로직 요약:
  - `$monitor->event("match", "state")` 호출 → `$res['output']` 에 저장
  - 호출 결과가 falsy 이면:
    - `result = FALSE`
    - `message = $monitor->last_error()`

- 응답 구조:
  - `result` (bool)
  - `output` (mixed): 모니터링 이벤트 결과
  - `message` (string): 에러 메시지 또는 빈 문자열

---

## 기타: 정의되지 않은 func 요청 시 처리

- 설명:  
  위에서 정의되지 않은 `func` 값으로 요청이 들어온 경우의 공통 처리.

- 처리 로직 요약:
  - `else` 블록에서:
    - `result = FALSE`
    - `message = "Invalid function"`

- 공통 로그 & 응답 처리:
  - (주석 처리된 코드)
    - `call_log($func, $_REQUEST, $res);`  
      → 각 요청에 대한 로그 기록용 함수(현재 주석)
  - 최종 응답:
    - `echo json_encode($res);`
    - `exit();` 로 스크립트 종료

- 기본 응답 구조:
  - `result` (bool): 성공 여부
  - `output` (mixed|null): 기능별 결과 데이터
  - `message` (string): 에러 또는 안내 메시지

