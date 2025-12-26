<?php
// 이 파일은 실제 DB 연결 대신, 미리 준비된 데이터를 JSON 형태로 응답하는 '가짜 서버(Mock Server)'입니다.
header('Content-Type: application/json; charset=utf-8');

// 클라이언트(HTML)에서 보낸 요청 파라미터 받기
$func = isset($_POST['func']) ? $_POST['func'] : '';
$h_t_code = isset($_POST['h_t_code']) ? $_POST['h_t_code'] : '';
$a_t_code = isset($_POST['a_t_code']) ? $_POST['a_t_code'] : '';

// 기본 응답 구조
$response = [
    'result' => false,
    'output' => null,
    'message' => ''
];

// 1. 요청이 'player_list' (선수 명단 조회)인 경우
if ($func === 'player_list') {
    
    // DB에서 조회했다고 가정하는 가짜 데이터
    $all_players = [
        // FC 서울 (코드: SEOUL)
        ['name' => '강현무', 'back_no' => 1, 'position' => 'GK', 't_code' => 'SEOUL'],
        ['name' => '기성용', 'back_no' => 6, 'position' => 'MF', 't_code' => 'SEOUL'],
        ['name' => '임상협', 'back_no' => 7, 'position' => 'FW', 't_code' => 'SEOUL'],
        ['name' => '린가드', 'back_no' => 10, 'position' => 'MF', 't_code' => 'SEOUL'],
        ['name' => '일류첸코', 'back_no' => 90, 'position' => 'FW', 't_code' => 'SEOUL'],
        ['name' => '조영욱', 'back_no' => 32, 'position' => 'FW', 't_code' => 'SEOUL'],
        ['name' => '김주성', 'back_no' => 17, 'position' => 'DF', 't_code' => 'SEOUL'],
        ['name' => '최준', 'back_no' => 16, 'position' => 'DF', 't_code' => 'SEOUL'],
        ['name' => '이승모', 'back_no' => 77, 'position' => 'MF', 't_code' => 'SEOUL'],
        ['name' => '박성훈', 'back_no' => 40, 'position' => 'DF', 't_code' => 'SEOUL'],
        ['name' => '강성진', 'back_no' => 11, 'position' => 'FW', 't_code' => 'SEOUL'],

        // 수원 삼성 (코드: SUWON)
        ['name' => '양형모', 'back_no' => 21, 'position' => 'GK', 't_code' => 'SUWON'],
        ['name' => '이기제', 'back_no' => 33, 'position' => 'DF', 't_code' => 'SUWON'],
        ['name' => '김보경', 'back_no' => 10, 'position' => 'MF', 't_code' => 'SUWON'],
        ['name' => '뮬리치', 'back_no' => 9, 'position' => 'FW', 't_code' => 'SUWON'],
        ['name' => '전진우', 'back_no' => 11, 'position' => 'FW', 't_code' => 'SUWON'],
        ['name' => '카즈키', 'back_no' => 14, 'position' => 'MF', 't_code' => 'SUWON'],
        ['name' => '이종성', 'back_no' => 20, 'position' => 'MF', 't_code' => 'SUWON'],
        ['name' => '장호익', 'back_no' => 3, 'position' => 'DF', 't_code' => 'SUWON'],
        ['name' => '양상민', 'back_no' => 5, 'position' => 'DF', 't_code' => 'SUWON'],
        ['name' => '장석환', 'back_no' => 2, 'position' => 'DF', 't_code' => 'SUWON'],
        ['name' => '김주찬', 'back_no' => 13, 'position' => 'FW', 't_code' => 'SUWON'],
    ];

    // 요청받은 팀 코드에 맞는 선수만 필터링 (DB 조회 흉내)
    $filtered_players = [];
    foreach ($all_players as $p) {
        if ($p['t_code'] === $h_t_code || $p['t_code'] === $a_t_code) {
            $filtered_players[] = $p;
        }
    }

    $response['result'] = true;
    $response['output'] = $filtered_players;

} else {
    $response['message'] = '알 수 없는 요청(func) 입니다.';
}

// JSON으로 출력
echo json_encode($response);
?>