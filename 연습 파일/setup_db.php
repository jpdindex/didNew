<?php
// MySQL 접속 정보 (본인 환경에 맞게 수정 필요)
$host = '127.0.0.1';
$user = 'root';      // 기본 사용자
$pass = '';          // 비밀번호 (설정했다면 여기에 입력하세요)

try {
    // 1. MySQL 접속
    $pdo = new PDO("mysql:host=$host", $user, $pass);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    echo "MySQL 접속 성공!<br>";

    // 2. 데이터베이스 생성 (이미 있으면 건너뜀)
    $pdo->exec("CREATE DATABASE IF NOT EXISTS did_practice DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");
    echo "데이터베이스 'did_practice' 확인 완료.<br>";

    // 3. 만든 DB 선택
    $pdo->exec("USE did_practice");

    // 4. 테이블 생성 (players)
    $sql_table = "
    CREATE TABLE IF NOT EXISTS players (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(50) NOT NULL,
        back_no INT NOT NULL,
        position VARCHAR(10) NOT NULL,
        t_code VARCHAR(20) NOT NULL
    )";
    $pdo->exec($sql_table);
    echo "테이블 'players' 확인 완료.<br>";

    // 5. 기존 데이터가 있다면 비우기 (중복 방지)
    $pdo->exec("TRUNCATE TABLE players");

    // 6. 데이터 넣기 (SQL INSERT)
    $stmt = $pdo->prepare("INSERT INTO players (name, back_no, position, t_code) VALUES (:name, :no, :pos, :code)");

    // 넣을 데이터 리스트
    $data = [
        // FC 서울
        ['강현무', 1, 'GK', 'SEOUL'], ['기성용', 6, 'MF', 'SEOUL'], ['임상협', 7, 'FW', 'SEOUL'],
        ['린가드', 10, 'MF', 'SEOUL'], ['일류첸코', 90, 'FW', 'SEOUL'], ['조영욱', 32, 'FW', 'SEOUL'],
        ['김주성', 17, 'DF', 'SEOUL'], ['최준', 16, 'DF', 'SEOUL'], ['이승모', 77, 'MF', 'SEOUL'],
        ['박성훈', 40, 'DF', 'SEOUL'], ['강성진', 11, 'FW', 'SEOUL'],
        // 수원 삼성
        ['양형모', 21, 'GK', 'SUWON'], ['이기제', 33, 'DF', 'SUWON'], ['김보경', 10, 'MF', 'SUWON'],
        ['뮬리치', 9, 'FW', 'SUWON'], ['전진우', 11, 'FW', 'SUWON'], ['카즈키', 14, 'MF', 'SUWON'],
        ['이종성', 20, 'MF', 'SUWON'], ['장호익', 3, 'DF', 'SUWON'], ['양상민', 5, 'DF', 'SUWON'],
        ['장석환', 2, 'DF', 'SUWON'], ['김주찬', 13, 'FW', 'SUWON']
    ];

    foreach ($data as $row) {
        $stmt->execute([':name' => $row[0], ':no' => $row[1], ':pos' => $row[2], ':code' => $row[3]]);
    }

    echo "선수 데이터 22명 입력 완료! <br>";
    echo "<h3>모든 설정이 끝났습니다. 이제 mock_api.php를 수정하세요.</h3>";

} catch (PDOException $e) {
    echo "<h3 style='color:red'>오류 발생!</h3>";
    echo "메시지: " . $e->getMessage() . "<br><br>";
    echo "<b>혹시 비밀번호가 틀렸나요? '연습 파일/setup_db.php' 파일을 열어서 \$pass 변수를 수정해주세요.</b>";
}
?>