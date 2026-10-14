# DID → AIfootballX 데이터 매핑

팀원과 컬렉션별 연결 규칙을 맞추기 위한 작업 문서. **현재는 조사·검증 단계이며 자동 전송은 아직 구현하지 않았다.**

## 연결 범위

`DID 확정 데이터 → DID 측 변환·전송 → AIfootballX 기존 Firestore 컬렉션`

AIfootballX의 API·파이프라인·화면은 유지하고, 기존 SQL 추출·수동 업로드의 데이터 공급 경로를 교체한다. 평점은 JPD-RATING에서 DID에 이미 내려온 값을 사용한다.

## 매핑 대조표

| AIfootballX 대상 | DID 원천 / 주요 대응 | 같이 확인할 부분 |
|---|---|---|
| **dMST** | `matches` + `recordings.kpi/teamRating`. `gi_tmp←TAP`, `B/M/A/S←DTB/DTM/DTA/DTS`, `JMX/JMX2/gi_score2←jmx`, `APX/TPX/FPX←apx/tpx/fpx` | Match 순번, 추가 평가점수·등급, 누적 결과 |
| **TACTIC** | `records` + 경로 판정. `gr_half←half`, `gr_half_seconds←halfSeconds`, 행동·결과·구역·좌표·선수 연결 | `gt_id` 생성, 같은 초의 기록 식별, 기존 UID 대응 |
| **multiSheet_ALL 및 BAP/DAP/DTP/GOAL/JMX/SHOT** | 기록·KPI 판정으로 전후반 20구간 구성. `JMX-H*`는 저장된 `jmxSeries[0..19]` 사용 | 300초 경계, 45-Plus, 구간 SSR 분모. DTP 구간 합은 경기 총계와 같아야 함 |
| **multiSheet_GOAL-시간 / playerGoal** | 골 기록 + 선수·경기장. 시간은 `H1_초` / `H2_초`, 미지정은 `\N` | 자책골 표현, 득점 순서, GOAL10 초과 처리 |
| **bmas_DTA/DTB/DTM/DTS** | 역할별 KPI 판정 + 해당 기록의 위치·시간 | 역할 이벤트 추출 규칙과 시간 기준 |
| **Player / playerDB** | `players` + 대회 구분된 `contracts`. 이름·신체·국적·주발, 소속·등번호·포지션 연결 | 시즌·기간별 계약 선택, 신규 숫자 ID 발급. `p_id_old/P-17`은 기존 이력 보존 + 계약 기반 구성 |
| **playerMST / vibePlayer** | `playerStats` + 선수·라인업. `P-DB*`는 원자값·성공수·기여율, 저장된 선수 평점 사용 | `P-KPI*` 가중점수, `P-BAP` 정의, 팀/선수 등급 구분 |
| **Manager** | `coaches/coachContracts/coachRounds` + 팀 | `gm_date/e_date`는 감독 재임 기간. 감독 상태·주장 등 추가 역할 |
| **code** | 기존 시즌별 팀 코드 기준표를 DID 팀 ID와 연결 | 기존 번호·슬롯 유지, 팀 명칭만 대응 후 갱신 후보 |
| **mMST / pMST / cAVG / rAVG / sAVG / rank** | 양 팀 경기값과 시즌·라운드 집계의 기존 산출 규칙 재현 | 최근 경기·평균·누적·순위·예측용 컬럼의 기존 산식 |

`prediction/aiPHR/yPHR/pushMatches` 등은 기존 생성 경로를 유지한다. 계정·댓글·접속·영상·콘텐츠 이력은 DID의 직접 공급 대상에서 제외한다.

## 확정한 규칙

- **X-* 제외:** 사용하지 않는 6개 필드는 매핑하지 않는다. 기존 목적지 값을 삭제하지 않는다.
- **선수 이력:** 기존 이력을 보존하고 DID 계약으로 구성한다. `league/cup/national/test` 구분은 DID에 유지하고, 대상 리그·시즌에 맞춰 투영한다. 컵·국가대표 이력이 클럽 소속을 덮어쓰지 않도록 한다. `legacyPlayerId`를 이력 문자열에 복사하지 않는다.
- **DTP:** 구간 합과 경기 총계는 같아야 한다. 기존 불일치를 임의 배분해 맞추지 않고, 신규 전송 검증에서 차단한다.
- **시즌:** `20232024 → PlSeason=2023-2024`, 짧은 숫자 시즌은 `2324`. `playerDB.P-6`도 숫자형이다.
- **ID:** DID 팀 `E-MU`와 대상 IDX `MUNX`를 컬렉션별로 구분한다. 선수 `0000000571`은 Player에서 숫자 `571`, TACTIC에서 문자열 `571`. 비숫자 신규 ID는 대응표가 필요하다.
- **시간:** 후반 전체 초는 `halfSeconds + 전반 실제 종료 초`. 고정 2700초를 더하지 않는다. G/GOAL은 기존 소비 코드가 허용하며, SQL 호환 투영 후보는 GOAL→G다.
- **평점:** 저장된 평점과 20구간 JMX를 사용한다. `ratingBasedOn=kpiVersion`을 확인한다. 기존 대상 UID는 보존하고, 기록을 시간만으로 식별하지 않는다.

## 남은 결정

| 항목 | 현재 근거 / 필요한 결정 |
|---|---|
| **추가 평가점수** | `DAP(AiAttack)`·`DTP(AiTactic)`는 APX·TPX와 다른 값이다. X-* 제외와 별개로 산식·사용 여부 확인 필요. 선수 P-KPI 가중점수도 포함 |
| **Match 순번** | 기존 EPL 표본은 `M_No2=round×100+Match`, `M_No1=Season×10000+(round−1)×20+Match` 등이 맞는다. 신규 일정·연기 경기에서 Match를 어떻게 부여할지 결정 |
| **구간·골 규칙** | 300초 경계·45-Plus·SSR·자책골·10골 초과 규칙을 명시. 공식 점수 GOAL과 분석 득점 GOAL−OG를 구분 |
| **계약·역할 투영** | 중도 이적·중복 계약·감독 상태·주장 등 대상 필드에 필요한 선택 규칙. 이력 보존 방향은 확정, 문자열 병합·중복 제거 방식은 정리 필요 |
| **재수정 반영** | 경기 수정 시 삭제된 행동·선수·골과 관련 집계의 갱신 범위, 기존 파이프라인 실행 책임 결정 |

## 확인한 결과

2026-10-14 실제 조회: DID 11개 / AIfootballX 54개 최상위 컬렉션. 주요 컬렉션은 최대 20문서, code는 139문서, 구간 합 대조용 dMST는 1라운드 20행을 확인했다.

| 검사 | 결과 |
|---|---|
| DID 양 팀 기록 1,802개 | 저장 KPI와 재계산 결과 일치. 평점 버전·20구간 JMX 최종값도 일치 |
| TACTIC 후보 15개 필드 투영 | 27,030개 자료형 검사에서 오류 없음. 팀 코드·목적지 UID는 별도 확인 필요 |
| 선수 기본 정보 20명 / 140개 값 | 날짜 형식 정규화 후 모두 일치 |
| 개별 multiSheet ↔ ALL | 2,400개 구간값 모두 일치 |
| 구간 합 ↔ dMST | 풀럼 2024–25 1R DTP **9 대 11**: 기존 데이터 불일치. 크리스탈팰리스 GOAL **0 대 1**은 OG=1을 제외하면 일치 |
| 평점 별칭 | JMX/JMX2/gi_score2는 20행 모두 동일. APX와 DAP(AiAttack)는 8행, TPX와 DTP(AiTactic)는 9행에서 다름 |

현재 존재하는 시즌으로 구조·규칙·내부 값을 대조했다. 같은 경기의 양쪽 전체값 비교와 모든 시즌의 전수 검증을 완료했다는 뜻은 아니다. 이번 작업에서 운영 데이터 변경·평점 호출·AIfootballX 코드 수정은 없었다.

## 조사 도구

`backend/check/audit_aifootballx_mapping.py` 한 파일에 **읽기 전용 조사, 후보 변환 검증, 오프라인 테스트**를 모았다. 운영 연동 프로그램이 아니다.

```powershell
# 인증·네트워크 없이 규칙 테스트
.\.venv\Scripts\python.exe -m backend.check.audit_aifootballx_mapping --self-test
# 기존 로컬 증거로 재검증 (--live를 추가하면 두 DB를 읽기 전용 조회)
.\.venv\Scripts\python.exe -m backend.check.audit_aifootballx_mapping --snapshot <evidence.json 경로>
```

상세 JSON은 재검증용 로컬 캐시이며 Git에서 제외한다. 기본 출력은 `backend/data/aifootballx/`이고, 공유 문서는 이 파일 하나를 수동으로 갱신한다. 도구를 실행해도 별도 Markdown을 생성하거나 이 문서를 덮어쓰지 않는다.
