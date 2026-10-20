# DID → AIfootballX 데이터 매핑

팀원과 컬렉션별 연결 규칙을 맞추기 위한 작업 문서. **경기 변환·6개 집계·SQL 상세 생성 및 검증, DID 백엔드의 미리보기·전송·결과 조회·재시도 API를 구현했다. 실제 Firestore 전송은 기본 비활성화이며 실행하지 않았다.**

## 연결 범위

`DID 확정 데이터 → DID 측 변환·전송 → AIfootballX 기존 Firestore 컬렉션`

AIfootballX의 API·파이프라인·화면은 유지하고, 기존 SQL 추출·수동 업로드의 데이터 공급 경로를 교체한다. 평점은 JPD-RATING에서 DID에 이미 내려온 값을 사용한다.

## DID → AIfootballX 컬럼 대조표

**구현**은 로컬 생성 코드가 있는 항목이다. 2024–25 경기·KPI 검증의 정답은 SQL 덤프이며, 기존 Firestore 값과의 일치는 합격 기준으로 쓰지 않는다.

| 대상 | DID에서 읽는 값 | AIfootballX에 넣는 컬럼 | 상태 / 변환 |
|---|---|---|---|
| dMST | `recordings.kpi.TAP` | `gi_tmp` | 구현 |
| dMST | `kpi.DTB / DTM / DTA / DTS` | `B / M / A / S` | 구현 |
| dMST | `kpi.DAP / DTP / BAP / SHOT / ASR / SSR / GOAL / OG` | 같은 이름의 컬럼 | 구현 |
| dMST | `teamRating.jmx / apx / tpx / fpx` | `JMX/JMX2/gi_score2`, `APX/TPX/FPX` | 구현; DID 저장 평점 사용 |
| dMST | `kpi.DAP / 400×100`, `kpi.DTP / 12×100` | `DAP(AiAttack)`, `DTP(AiTactic)` | 구현; 보정 전 값 |
| multiSheet_ALL 및 개별 시트 | `records`의 KPI 판정 + `half/halfSeconds` | `DAP-/DTP-/BAP-/SHOT-/GOAL-H1/H2-*` | 구현; 20구간 집계 |
| multiSheet_ALL / JMX | `teamRating.jmxSeries[0..19]` | `JMX-H1/H2-*` | 구현; 평점 재호출 없음 |
| multiSheet_ALL | 반별 누적 분석 골 ÷ 누적 슛 | `SSR-H1/H2-*` | 구현; 분모 0은 0 |
| TACTIC / bmas_* | `act/res/area/half/halfSeconds`, KPI 역할 판정 | `gr_act_code/gr_res_code/gr_area_code/gr_half/gr_half_seconds/Type` | 구현; 역할별 bmas 분리 |
| TACTIC / bmas_* | `posX/posY`, `shootPosX/Y`, `paths.gtId` | `gr_pos_x/y`, `gr_shoot_pos_x/y`, `gt_id` | 구현; 경기장만 971×634 복원 |
| playerGoal / 골 시간 시트 | 자책골 제외 골 기록 + `players` + `stadiums` | `GOAL1..10`, `gr_half/gr_half_seconds/p_id/p_name/p_name_en/s_name` | 구현; `H1_초/H2_초` |
| Player | `players.name/nameEn/nameFull` | `p_name/p_name_en/p_name_full` | 구현; 선수 이름 직접 연결 |
| playerDB | 같은 선수 이름 3개 | `P-2/P-3/P-4` | 구현 |
| Player / playerDB | `birth/height/nation/foot` | `p_birth/p_height/p_nation/p_foot`; `P-11/12/13/16` | 구현; 날짜 형식 변환 |
| Player / playerDB | DID 수동 선수 ID + 경기일·대회별 계약 | `p_id/pt_num/pt_pos/pt_begin/pt_end/t_code`; `P-1/9/10/14/15/7` | 구현; 기존 이력 + 계약으로 `p_id_old/P-17` 구성 |
| playerMST | `players.name`, 계약·라인업 `pos`, 숫자 선수 ID | `P-DB5/P-DB6/P-DB7` | 구현 |
| playerMST | `playerStats.TAP/DAP/UTP/DTP/TTP/SHOT/AST/GOAL/DTB/DTM/DTA/DTS` | `P-DB8/12/16/17/18/23/26/27/29/30/31/32` | 구현; 성공수·기여율도 기록 판정으로 산출 |
| vibePlayer | `playerStats.apx/tpx/fpx/jmx`, 등급 + `players.name` | `P-APX/P-TPX/P-FPX/P-JMX`, `G-*`, `p_name` | 구현 |
| vibePlayer | **`playerStats.DTB`** | **`P-BAP`** | 구현; 같은 대상 경기 32명에서 `P-BAP=P-DB29` 일치. UTP가 아님 |
| vibePlayer | `lineup.type/slot/inHalf/inSeconds` + 포지션 | **`gp_type`** | 구현; 선발 GK→GK, 나머지 선발→KY, 교체 출전→ST. 미출전 벤치 제외 |
| Manager | `coachRounds.coachId/coachName/coachNameEn` | `m_id/Manager_KR/Manager_EN` | 구현; 숫자 감독 ID와 이름 연결 |
| Manager | `coachRounds.plSeason/round/teamId/from/to/status` + 팀 코드·이름 | `PlSeason/Round/T_Code/gm_date/e_date/Status_EN/Status_KR/IDX_CODE/T_Name` | 구현; MANAGER→Manager/정식, CARETAKER→Caretaker/감독대행 |
| mMST | 같은 경기 H/A의 KPI·JMX·팀·날짜·스코어 | `H-*/A-*`, `H-FX/A-FX`, `H-DWL/A-DWL`, `HA-avg` | 구현; 평점은 DID 저장 JMX. `6H-/6A-`는 같은 경기의 6개 지표 복제이며 최근 6경기가 아님 |
| pMST | 팀 KPI/JMX + 상대 팀 골 | `gi_tmp/DAP/.../gi_score`, `DAP2/.../GOAL2`, `Goal` | 구현; `GOAL`은 자기 골, `Goal`은 상대 골. gi_score에는 DID 저장 JMX |
| pMST | 예측 지수·예측 입력 | `I-*/PH-*/PA-*/PI-*` | 미사용 제외. prediction/aiPHR/yPHR도 제외 |
| cAVG | 팀·라운드 JMX, 승점, 득점, 상대 득점 | `C{slot}-AVG/WDL/S/C`, `R-AVG/RS-AVG/RC-AVG`, `C-W/D/L` | 구현; 기존 시즌 슬롯표 필수 |
| rAVG | gm_id 순서의 홈·원정 IDX/JMX/승무패 | `MHn_NM/MAn_NM`, `MHn-IDX/MAn-IDX`, `MHn-WDL/MAn-WDL`, `Mn-AVG` | 구현; 날짜가 아닌 gm_id 순번 |
| sAVG | 완료 경기의 KPI·평점 모음 | `TAP/DAP/.../GOAL`, `B/M/A/S`, `AI Rating/AVG/SEASON` | 구현; 해당 시즌 AI Rating=AVG. 여러 시즌 평균 사용 안 함 |
| rank | 경기 스코어→승점·누적 승점, JMX | `WDLn/WTTn/WRKn/RAVn/RRKn` | 구현; 현행 승점·JMX 정렬. WGR*/Season/S_Avg/R_Rate 미사용 제외 |
| code | `teams` + 기존 목적지 시즌별 코드·슬롯 | `TCODE/Code/IDX_NAME/Full Name/Team Code/No` | 기존 번호·슬롯 보존; 팀 명칭 연결 |

## 확정 범위·규칙

| 구분 | 처리 |
|---|---|
| 미사용 | pMST I-*/PH-*/PA-*/PI-*, prediction/aiPHR/yPHR, rank WGR*/Season/S_Avg/R_Rate, X-*, BAP(AiBuildup), P-KPI50~68은 신규 생성 제외 |
| 기존 수동 업로드 | Manager.Coach/Captain, P-KPI40/41 등 원천 없는 잔여 선수 점수는 팀원 엑셀 관리·직접 업로드 유지. DID 누락 검증 대상이 아니며 기존 값을 삭제하지 않음 |
| 시즌 평균 | sAVG.AI Rating은 해당 시즌 완료 팀 기록의 저장 JMX 평균. sAVG.AVG는 같은 값. 이전 시즌 평균을 섞지 않음 |
| 운영·콘텐츠 | AIAnalysis/analysis_history/aiPodcast/aiShorts/forecastSnapshots/snapshot_history/shot_x 등은 기존 생성 주체 유지 |

- **정답 원천:** 2024–25 경기·KPI는 제공 SQL 덤프. AIfootballX 기존 Firestore는 스키마·문서 ID/UID·팀 코드·슬롯 참조와 차이 진단용이다. 차이를 맞추려고 SQL 값을 변경하지 않는다.
- **평점:** 실제 연동은 DID 저장 teamRating/playerStats와 jmxSeries를 읽고 ratingBasedOn=kpiVersion을 확인한다. SQL gi_score를 새 JMX로 대체하지 않는다. SQL 덤프에 현재 JPD-RATING 값이 없어 이번 SQL 평점 검사는 건너뛴다. 24/25 시즌 81.2는 확인된 값이며 73.8을 정답으로 쓰지 않는다.
- **순위:** WRK는 누적 승점↓·득실차↓·득점↓·IDX↑, RRK는 rAVG 표시 JMX↓·직전 출전 라운드 대비 상승률↓·IDX↑로 순번을 정한다. 상승률은 (현재−직전)/직전×100의 소수 첫째 자리, 첫 출전·직전 0은 0이다. 근거는 현재 AIfootballX ai_chart/build_ranking.py이며 관리자만 표시하는 미사용 필드와 분리한다.
- **ID·계약:** 선수 숫자 ID는 DID에서 수동 발급한다. 기존 이력 보존·중복 제거와 DID의 경기일·리그/컵/국가대표별 계약 선택 기준을 재사용한다. gm_id 순번·대상 UID·시즌 슬롯을 유지하며 연기 시 gm_date만 변경한다. 2025–26 rank 슬롯 호환은 번리27/리즈12/선덜랜드21이다. EPL 외 번호식은 별도 확인이 필요하다.
- **구간·골:** 정확히 5:00은 5–10. 분석 골은 자책골 제외, 공식 경기 스코어는 유지. 구간 SSR은 반별 누적 골/슛이며 분모 0은 0이다. DTP는 구간 합과 총계를 맞추며 과거 SQL 추출·업로드 원인은 추가 조사하지 않는다.
- **점수·등급:** NEW 고정 기준 DAP400/DTP12/SHOT15/SSR0.18/GOAL3. 원본 DAP·DTP 점수와 상한 99.9 평점을 구분한다. FPX는 보정 SHOT·GOAL 평균, Power3X는 APX·TPX·FPX 평균. 등급은 F<50, D≥50, C≥60, B≥70, A≥80, A+≥90이다.
- **시간·좌표:** 후반 전체 초는 전반 실제 종료 초+halfSeconds. 경기장 좌표만 백분율에서 971×634로 복원하고 슈팅 좌표 단위는 유지한다.

## SQL 기준 검증 — 2026-10-15

2024–25 380경기·760팀 기록으로 1,255개 집계 문서를 로컬 생성했다. SQL 원천에서 별도로 확인한 경기·KPI, 공식 스코어 기반 승점·순위, 집계 평균·문서 수 등 **38,554개 검사 모두 통과**, 생성 오류 0이다. 이 수치는 기존 Firestore 값과의 일치 건수가 아니다.

| 컬렉션 | 생성 문서 | SQL 기준 검사 |
|---|---:|---:|
| mMST | 380 | 12,921 / 12,921 |
| pMST | 760 | 19,001 / 19,001 |
| cAVG | 38 | 2,586 / 2,586 |
| rAVG | 38 | 1,635 / 1,635 |
| sAVG | 1 | 16 / 16 |
| rank | 38 | 2,395 / 2,395 |

평점은 SQL 검사 범위에서 제외한다. 현재 순위 계산은 별도 오프라인 대조에서 AIfootballX 계산 함수와 WRK/RRK 각각 760/760 일치했다. 이는 순위 산식 확인이며 현재 DID 평점 정확성의 증명으로 세지 않는다. 미사용·수동 업로드 항목은 blocked에 포함하지 않는다.

## 파일·실행

- `system_aifootballx.py`: 경기·선수·감독 변환, 6개 집계, 원천 캡처, 공통 검증, 비동기 전송·상태를 한 파일에서 관리한다.
- `build_aifootballx.py`: SQL을 실행하지 않고 읽어 로컬 생성·SQL 원천 검증. 기본 current, 과거 산식 재현만 legacy-audit. 과거 Firestore 평점 재집계는 --legacy-target-replay를 명시할 때만 실행한다.
- `check_aifootballx.py`: 생성·집계·매핑·전송 회귀 검사. 매핑 조사는 build의 `mapping` 명령으로 실행한다.
- 공유 엑셀: `outputs/01a138c8-aifootballx/DID_AIfootballX_컬럼매핑.xlsx`. 상세 JSON·진단 보조 파일은 Git 제외 폴더 `backend/data/aifootballx/`에 둔다.

```powershell
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx aggregates --sql "C:/Users/user/Desktop/EPL_2024-2025_R1-R38_통합.sql" --season 2024-2025 --target-snapshot backend/data/aifootballx/aggregate-target-evidence.json
.\.venv\Scripts\python.exe -m backend.check.check_aifootballx
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx mapping --self-test
```

전송 API는 아래 후속 단계에서 구현했다. Firestore 쓰기·AIfootballX 코드 변경·커밋·push는 없다.

## 4단계 — DID 실제 시즌 전체 생성 검증

`backend/build/build_aifootballx.py`는 DID의 한 시즌 경기·팀 기록·선수·계약·감독을 읽고, 경기별 대상 문서와 6개 집계를 함께 로컬 JSON으로 생성한다. 대상 Firestore는 스키마·기존 ID/UID·코드 참조만 읽는다. `season` 명령은 Firestore 쓰기 및 평점 API 호출을 하지 않는다.

```powershell
# Firestore 읽기 + 로컬 스냅샷·생성·검증
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx season --season-id 20232024 --live --output-dir backend/data/aifootballx/step4-2023-2024
# 이후에는 네트워크 없이 동일 스냅샷 재검증
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx season --season-id 20232024 --output-dir backend/data/aifootballx/step4-2023-2024
.\.venv\Scripts\python.exe -m backend.check.check_aifootballx
```

- `season-validation.json`: 최종 합격 여부, 경기별 검사·오류·수동 필드/ID 보존 검사, 시즌 전체 중복·소속 충돌.
- `match-previews/`: 경기별 생성 문서와 KPI 재계산·구간 합계·저장 평점 연결 검사.
- `aggregate-preview.json`, `aggregate-validation.json`: 6개 집계 후보와 DID 경기/KPI·저장 JMX를 기준으로 한 검사. SQL 평점을 대체 입력으로 쓰지 않는다.
- `shared-player-conflicts.json`: 같은 시즌 선수 문서에 서로 다른 소속·계약 값이 생기는 후보. 충돌 문서는 `shared-player-preview.json`의 통합 후보에서 제외한다. 처리 순서로 소속을 결정하지 않는다.
- `source-common.json`, `source-matches/`: 읽기 전용 원천 스냅샷. 경기 및 각 팀 기록을 자식 컬렉션 수집 후 다시 읽어 변경을 감지하며, 시즌 전체에 대한 단일 트랜잭션 스냅샷은 아니다.

시즌 순위 POSITION이나 cAVG 슬롯이 없으면 다른 계산 가능한 집계도 생성하되 해당 필드를 누락·오류로 명시하고 전체 합격을 막는다. 경기별 `validation_passed`와 시즌 전체 합격은 별개다. 모든 출력은 Git 제외 경로에 저장한다.

### 2026-10-15 초기 2023–24 실험 결과: 미통과

DID 실제 2023–24 EPL 380경기·760팀 기록을 읽어 전체 생성했다. 경기별로 **323/380경기 통과**, **193,741개 검사 중 122개 실패**다. 시즌 통합 후보는 725,913개이며, 그중 6개 집계는 mMST 380 / pMST 760 / cAVG 38 / rAVG 38 / sAVG 1 / rank 38, 합계 1,255개다. 설정·데이터 오류가 있는 진단 후보이므로 전송 가능한 데이터로 간주하지 않는다.

| 미통과 항목 | 관측 결과 |
|---|---|
| 저장 KPI vs 원본 기록 재계산 | OG 48건, DTP/TTP 각각 9건 불일치. 구간 합계도 GOAL 47건, DTP 9건 불일치. 서로 연결된 검사이므로 별개 원인 수로 합산하지 않는다. |
| 경기일 기준 계약 누락 | 선수·경기 연결 5건, 4경기. 예: 경기 ID 끝 `0202`의 선수 `0000003374`. |
| 지원하지 않는 시간 구간 | 경기 ID 끝 `0317/A`, `0323/H`, `0361/H`에 H4·0초 기록 각 1건. H1/H2 구간으로 임의 변환하지 않는다. |
| 시즌 선수 문서 충돌 | 선수 17명, Player/playerDB 합계 34개 자연키에 서로 다른 계약·등번호·소속 값. 팀이 같은 경우의 계약 변경도 포함한다. |
| 시즌 대상 설정 누락 | 2023–24 시즌별 code.POSITION 및 cAVG 슬롯표 없음. rank/cAVG 필수 필드 생성이 차단된다. |

기존 경기 문서 ID/UID 보존 검사 757건, 수동 필드 비포함 검사 15,870건에서 위반은 없었다. sAVG 기존 문서 ID/UID는 집계 바인더가 보존한다. 저장 평점 버전 누락·불일치는 관측되지 않았고, 생성 가능한 집계 평점 연결 검사는 일치했다. rank 평점 슬롯 검사는 설정 누락으로 실패하며, 평점 검증 전체를 통과로 표시하지 않는다.

오프라인 회귀 테스트 49개와 매핑 self-test 20개는 통과했다. DID/AIfootballX Firestore 쓰기, 원천 수정, AIfootballX 로컬 변경, 5단계 API 구현은 수행하지 않았다. 현재 `validation_passed=false`, `ready_for_write=false`다.

## 현재 진행 기준 — SQL 상세 기록 기반 재계산

2024–2025와 2025–2026 SQL dump를 먼저 검증한다. 원본 SQL과 로컬 원본 인덱스는 보존하고, 전후반이 빈 행은 생성·집계에서 제외한다. 0초·1초·4초나 전체 경과 초로 전후반을 추정하지 않는다. 팀 코드의 빈 값만으로 제외하지 않는다. 연결된 유효 상세 기록이 없는 경로 요약은 DTP/TTP 등에 포함하지 않는다.

팀·선수 KPI를 유효 상세 이벤트의 기존 판정 플래그와 연결 경로에서 다시 계산한다. BAP는 상세 BAP 기록을 사용한다. 재계산한 값으로 dMST·playerMST·구간·6개 집계와 JPD 입력을 생성한다. 과거 SQL 총계의 잔여 값 차이는 생성 오류나 차단 사유로 취급하지 않는다. 실제 생성 데이터의 ID·소속·구간·집계 불일치는 계속 검사한다.

| 시즌 | 상세 통과 경기 | 상세 문서 | 상세 검사 | 6개 집계 문서·검사 |
|---|---:|---:|---:|---|
| 2024–2025 | 380/380 | 689,334 | 875,689/875,689 | 1,255개 · 38,554/38,554 |
| 2025–2026 | 380/380 | 684,792 | 874,636/874,636 | 1,255개 · 38,554/38,554 |

빈 half 행은 총 35개(11/24)이며 DAP에 영향을 주던 행이 그중 12개(2/10)다. 동일 기준으로 모두 제외했다. DTP는 맨유–풀럼의 풀럼 9, 웨스트햄–입스위치의 웨스트햄 16, 웨스트햄–에버턴의 웨스트햄 8로 확인했다. 이를 별도 오류 목록으로 기록하지 않는다.

전체 결과는 `backend/data/aifootballx/sql-acceptance/validation-summary.json`, 상세 결과는 시즌별 `*-details/details-validation.json`, 생성값은 `*-details/matches/*.json`이다. 입력 SQL SHA-256과 모든 제공 테이블 행의 연결 범위도 확인한다. SQL 후보에는 현재 평점과 대상 상세 ID 바인딩이 완성되지 않았으므로 직접 전송 manifest로 사용하지 않는다.

## 실행 파일 두 개

- `backend/build/build_aifootballx.py`: 매핑 감사·경기 미리보기·시즌 생성·SQL 상세·집계·현재 평점 검증을 담당한다. 하위 명령은 `mapping`, `preview`, `season`, `sql-details`, `aggregates`, `full-validation`이다.
- `backend/check/check_aifootballx.py`: 생성·집계·매핑·전송 회귀 검사. 중복 fixture 이름은 기능별로 구분한다.

기존 build 5개, check 6개 및 매핑 감사 파일을 두 실행 파일로 통합했다. 운영 system 4개도 `backend/system/system_aifootballx.py` 하나로 통합했다. system에서 build를 import하던 공통 함수는 system으로 이동해 순환 참조를 없앴다. API는 기존 입력·경로를 유지하며 통합 system을 사용한다.

```powershell
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx --help
.\.venv\Scripts\python.exe -m backend.check.check_aifootballx
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx sql-details --sql "C:/Users/user/Desktop/EPL_2024-2025_R1-R38_통합.sql" --season 2024-2025 --target-snapshot backend/data/aifootballx/aggregate-target-evidence.json --output-dir backend/data/aifootballx/sql-acceptance/2024-2025-details
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx aggregates --sql "C:/Users/user/Desktop/EPL_2024-2025_R1-R38_통합.sql" --season 2024-2025 --target-snapshot backend/data/aifootballx/aggregate-target-evidence.json --output-dir backend/data/aifootballx/sql-acceptance
# 2025–2026은 SQL 파일·시즌·출력 폴더를 교체한다.
```

## DID 백엔드 전송 API — 상태 GET 1개 + 폼 POST 2개

기존 Swagger의 `aifootballx-delivery`에는 아래 순서로 3개만 표시한다. 기존 실행 서버의 OpenAPI와 실제 응답에서도 확인했다.

| 순서 | 경로 | 입력·동작 |
|---|---|---|
| 1 | `GET /api/v1/aifootballx/send/status` | 입력 없이 최근 요청의 진행률·생성값·검증·실패 사유 조회 |
| 2 | `POST /api/v1/aifootballx/preview` | `season`, `round_from`, `round_to`, `gm_id` 폼 입력으로 비동기 미리보기 |
| 3 | `POST /api/v1/aifootballx/send` | 같은 폼 입력으로 자동 생성·검증 후 비동기 전송. 같은 입력 재실행 시 실패·미완료 건 재시도 |

시즌 입력은 `2024-2025` 또는 `20242025` 모두 가능하다. `round_from=1`, `round_to=1`은 1라운드, `gm_id`는 특정 경기다. query 입력, JSON 요청 본문, 공개 job ID, 멱등키 입력, 별도 설정/재시도/실패 조회 엔드포인트를 없앴다. 작업 식별자는 서버 내부에만 유지한다.

GET 응답의 `status`는 idle/queued/running/ready/complete/fail/error이며, `phase`, `percent`, `scope`, `counts`, `reason`, `failures`, `failedChecks`, `previewValues`를 함께 보여준다. fail/error의 상세 사유를 같은 응답 아래에 표시한다. 서버는 소유자·입력 범위 기준으로 진행 중 중복 요청을 합친다. 전송은 내부적으로 미리보기부터 다시 확인하며, 클라이언트가 미리보기 ID를 복사할 필요가 없다.

실제 전송 설정 `AIFOOTBALLX_WRITE_ENABLED`는 기본 false이며 이번 작업에서도 false다. 로컬 인증은 기존 X-Swagger-Key, 배포 환경은 admin claim이다. 일반 녹화 화면 인증만으로 전송할 수 없다. 자격증명은 `AIFOOTBALLX_CREDENTIALS_PATH`, 기존 시즌 슬롯은 `AIFOOTBALLX_SLOT_MAPPING_PATH`로 설정한다. 로컬에서는 기존 AIfootballX 서비스 계정/슬롯 파일을 읽기 참조로 재사용하며, 내용은 응답에 노출하지 않는다.

시즌 전체는 라운드별로 생성해 로컬 SQLite에 합치고, 선수 마스터와 집계는 중복 제거한다. 요청된 경기 상세와 같은 시즌 전체 확정 기록의 집계를 함께 검증한다. 대상 문서 ID/UID를 재사용하고 소유 필드만 merge한다. Manager.Coach/Captain, playerMST 수동 점수 등은 보내지 않는다. 변경되거나 남아 있는 대상 상세 문서가 있으면 검증 실패로 표시하고 임의 삭제하지 않는다.

전송은 100문서 단위 트랜잭션에서 payload와 `_didDeliveryReceipts`를 함께 기록한다. 배치 실패는 문서별로 분리한다. 응답 유실 후 재시도에도 동일 미리보기의 완료 표시는 중복 쓰기를 막으며, 이미 같은 생성값이면 merge를 생략한다. 성공·실패·시도 수는 `backend/data/aifootballx/delivery/delivery.sqlite3`에 보관한다. 백엔드 종료·장애 뒤에는 같은 입력으로 미완료 건을 재처리한다. 원천 변경 시 새 검증이 필요하다.

원천 버전·자식 구성·코드·슬롯과 대상 소유 필드의 동시 수정을 확인한다. 긴 전송은 원천을 5분 간격으로 재확인한다. 두 Firestore 사이의 단일 분산 트랜잭션은 아니다. 이미 성공한 문서는 결과에 남기고 후속 실패 사유를 표시한다.

선수 식별 마스터 Player/playerDB는 경기 KPI·출전 여부와 별도로 생성한다. 시즌 내 EPL 계약이 있으면 해당 소속을 사용하고, 없으면 DID의 현재 등록 팀·스쿼드 연결·기존 팀 계약 정보로 식별 소속을 확인한다. 경기일 계약을 임의로 늘리거나 만들지 않는다. 계약 날짜·등번호·포지션이 불명확하면 해당 선택 필드는 생략한다. 경기별 실제 출전 소속 검증은 별도로 유지한다. 신규 이전 시즌은 기존 전역 Team Code/No/POSITION을 유지한 시즌 code 후보와 Team Code 오름차순 cAVG 슬롯을 만든다. 슬롯은 `code.DID_CAVG_SLOT`로 보관해 후속 미리보기에서 재사용한다. 기존 시즌의 슬롯은 재배정하지 않는다. 실제 신규 시즌 생성은 아직 실행하지 않았다.

## 현재 JPD·선수·감독 검증 상태

기존 DID 마스터 스냅샷에서 두 시즌 각각 감독 라운드 760개와 선수 15,186행을 재검증했다. 기존 미출전 벤치 5명은 식별 마스터에 모두 보존했고, 두 시즌 선수·감독 마스터 검증은 각각 오류 0건으로 통과했다. KPI가 0이어도 선발·교체 출전이면 출전 선수 검증을 적용한다. 원천 연결·시간 문제로 JPD 입력을 생성하지 못하는 팀은 이제 0개다.

정상 상세 기록으로 다시 계산한 입력을 사용자가 승인한 기존 JPD 계산 API에 보내 두 시즌 1,520개 팀 기록을 모두 재검증했다. 접근·API 오류는 0건이며, 선수 평점 응답 30,372행의 ID 연결을 확인했다. 결과는 로컬 JSON에만 저장했고 Firestore 쓰기는 실행하지 않았다.

| 시즌 | JPD 응답 | 통과 | 불일치 팀 기록 | FPX 50.0/F 등급 | 자책골 제외 산식 |
|---|---:|---:|---:|---:|---:|
| 2024–2025 | 760 | 714 | 46 | 25 | 23 |
| 2025–2026 | 760 | 708 | 52 | 20 | 33 |

합계 1,422개 통과·98개 불일치다. FPX 50.0의 등급이 F인 경우는 D 기준과 맞지 않는 45건이다. 자책골을 제외한 GOAL−OG 기준 대신 GOAL 전체를 사용한 FPX와 일치하는 응답이 56건이다. 두 유형이 겹치는 팀 기록은 3개다. 나머지 검증 실패는 없다. 이전에 시간 문제로 제외했던 에버턴–리버풀(2025–2026 R33)의 에버턴도 새 입력에서 FPX 50.0/F 불일치로 확인됐다. 현재 전체 전송 검증은 이 JPD 응답 불일치 때문에 미통과다.

요청·응답은 `sql-acceptance/full-validation/<season>/ratings/*.json`, 통합 결과와 실패 근거는 `sql-acceptance/full-validation/jpd-validation-summary.json`에 저장했다. 검증 기준을 낮추거나 응답을 임의 보정하지 않았다.

이전 입력의 결과와 이번 새 입력의 검증을 구분한다. 이번 결과는 새 입력과 일치하는 실제 응답으로 확인했다. 평점 입력 JSON은 확인용이며 Firestore 전송 manifest가 아니다. 로컬 SQL 상세·집계 검증 통과와 전체 전송 검증 통과를 구분한다. 실제 Firestore 쓰기는 실행하지 않았다.

```powershell
# 네트워크 호출 없이, 입력·날짜가 같은 기존 응답만 재검증
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx full-validation --cached-ratings-only
# 승인된 JPD 재검증 명령; Firestore에는 쓰지 않음
.\.venv\Scripts\python.exe -m backend.build.build_aifootballx full-validation --ratings
```

DID 기반 API 미리보기도 빈 half 행을 상세/선수 플래그 계산에서 제외한다. DID에 저장된 KPI·평점이 유효 기록의 계산 결과와 다르면 기존 검증으로 차단한다. 저장 평점을 새 KPI의 평점으로 간주하지 않는다. 이전 검증 정책의 미리보기를 전송에 재사용할 수 없도록 정책 버전을 갱신했다.

## 등록 선수와 경기 출전 데이터 분리

DID 팀 등록 선수와 스쿼드를 playerStats 유무와 독립적으로 수집한다. Player/playerDB는 이름과 실제 DID 팀 연결을 확인하여 생성하고, 미출전 벤치의 경기일 계약·평점을 요구하지 않는다. 기록이 없는 신규 선수도 포함한다. playerStats가 이미 있는 미출전 벤치의 playerMST 기록은 보존하되 vibePlayer는 만들지 않는다. vibePlayer는 선발 또는 실제 교체 투입 기록이 있는 선수만 생성한다. 실제 출전한 선수의 계약·KPI·평점 연결 검증은 유지한다.

DID 신규 선수의 자동 문서 ID는 재전송해도 같은 대상 숫자 식별자가 되도록 변환하고 Player/playerDB의 DID_PLAYER_ID로 연결한다. 기존 숫자 식별자는 보존한다. 충돌이나 기존 대상의 다른 소유자는 차단하며, 기존 문서 ID/UID와 수동 필드는 유지한다.

로컬 등록 선수 후보는 `sql-acceptance/full-validation/<season>/registered-players.json`에 저장한다. 사용자 승인 후 새 입력 1,520개의 JPD 재검증을 완료했다. 현재 마스터 검증은 통과했고 JPD 응답 불일치가 남아 있다.

## FPX·평점 표시 기준 변경 — 2026-10-20

FPX 검증과 dMST의 GOAL 점수 성분은 자책골을 포함한 GOAL 전체를 사용한다. 기존 GOAL−OG 검증으로 분류한 56건은 새 기준의 오류가 아니다. SHOT 판정값, 자책골 제외 분석 골·골 시각·구간 SSR 규칙은 이 변경에서 수정하지 않는다.

등급은 정밀 점수로 판정하고, 한 자리 표시는 반올림 없이 버린다. 59.76→59.7, 49.9999/F→49.9/F, 50.0/D→50.0/D다. 기존 숫자 조회 필드와 저장값을 덮어쓰지 않고 `/match-ratings/read`에 `teamRatingDisplay`, `playerRatingsDisplay` 문자열 표시 필드를 별도로 제공한다. 현재 DID 프런트에는 평점 표시 화면이 없다.

현재 모드의 집계 평점은 한 자리 필드에서 버림을 적용하고, mMST의 두 자리 평점 필드는 두 자리에서 버린다. pMST와 dMST의 원본 정밀 평점은 유지한다. 평균은 원본 정밀 점수로 계산한 뒤 결과를 자른다. 현재 순위의 평점 비교는 rAVG와 같은 버림 표시 정밀도를 사용하며, 경기 승점·득실·성장률의 기존 산식은 유지한다. `legacy-audit`는 과거 재현을 위한 기존 반올림을 유지한다.

이 변경은 코드와 로컬 테스트에만 적용했다. RATING 재계산·DID/AIfootballX Firestore 쓰기·외부 JPD 호출은 실행하지 않았다. 이전 실제 응답 보고서는 당시 검증 기준의 결과이므로 최신 규칙의 합격 판정으로 간주하지 않는다. 기존에 반올림되어 저장된 점수는 조회만으로 원래 정밀값을 복구하지 않는다.
