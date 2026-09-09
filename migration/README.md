# 대량 SQL 이관

## 1. 준비

이 폴더에 SQL 파일을 넣습니다.

```text
migration/dumps/legacy_dump.sql
```

Firebase 서비스 계정 키는 저장소에 넣지 말고 로컬에 둡니다.

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/절대경로/service-account.json"
```

## 2. 설치

```bash
cd migration
npm install
```

## 3. 먼저 확인만 하기

```bash
npm run dry-run -- dumps/legacy_dump.sql
```

이 검증은 기존 Firestore의 `players`, `players/{playerId}/contracts`, `teams`, `stadiums`를
읽어 라인업의 이름·등번호·포지션과 참조 무결성을 확인한다. 누락 또는 교체 시간 충돌이
있으면 저장하지 않고 `import-validation.json`에 오류를 남긴다.

## 4. 원장 데이터가 별도 SQL이면 먼저 이관

경기 덤프에 `ff_player`와 `ff_player_team`이 없으면, 이 두 테이블을 포함한 원장 SQL을 먼저
같은 명령으로 이관한다. 원장만 담긴 SQL은 1경기 검증 기록 없이도 저장할 수 있다.

```bash
npm run import -- dumps/master_data.sql
```

## 5. 먼저 1경기 저장 및 read-back 검증

```bash
npm run import -- dumps/legacy_dump.sql --match 경기ID
```

이 명령은 새 문서만 생성하고, 이미 존재하는 문서가 계획과 다르면 덮어쓰지 않고 중단한다.
저장 뒤 전체 필드를 다시 읽어 대조하며, 통과한 경기 ID는 `import-progress.json`에 기록된다.

기존 이관분을 현재 스키마 매핑으로 보정할 때만 `--repair`를 붙인다. 이 옵션은 대상
문서의 이관 필드를 merge로 갱신한다.

```bash
npm run import -- dumps/legacy_dump.sql --match 경기ID --repair
```

## 6. 실제 전체 이관

```bash
npm run import -- dumps/legacy_dump.sql
```

같은 덤프·프로젝트의 1경기 검증 기록이 있어야 시작됩니다. 진행 상황은
`migration/import-progress.json`에 저장됩니다. 중단 후 같은 명령을 다시 실행하면 이미
동일한 문서는 건너뛰고 이어서 진행합니다.
