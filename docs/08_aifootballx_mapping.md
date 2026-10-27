# DID → AIfootballX 데이터 매핑

## 확정 잔여 기준 및 입력 보완 — 2026-10-23 V19

최신 정책은 `reviewed-raw-consistent-aggregates-v19`이다. 이 절이 아래 이전 기록보다 우선한다.

- dMST SSRScore와 SSR(AiSSR)는 99.9 상한을 적용하지 않는다. SSR은 OWN 제외 비율이며,
  SHOT·GOAL 성분의 평점 상한 및 FPX 산식은 유지한다. 비율은 3자리, 지수 Score는 2자리,
  표시용 Ai 지수는 1자리의 기존 운영 형식으로 낸다. 평점은 별도의 2자리 절삭 기준을 유지한다.
  mMST의 H/A 원비율은 6자리, 별도 6H/6A-SSR 표시는 3자리다. 같은 표시값의 IEEE 흔적만
  있는 기존 숫자는 보존한다. 실제 비율 변화나 평점 차이를 허용하는 epsilon 예외는 아니다.
- 0001A TTP=63, 0043 NEWX 누적 시각=2764초, R24 최솟값=61.4를 사용한다.
  sAVG.AVG는 해당 시즌 AI Rating과 같다. 이 차이들은 승인된 과거 운영 기록 예외다.
- 사용자 확인을 받은 생일·주발·국적 5필드와 선수명은 DID 값을 우선한다.
  팀명 에버턴·레스터는 명시한 표기로 내고 나머지는 동일 구단의 컬렉션별 운영 표기를 보존한다.
  선수명 끝의 공백과 리터럴 줄바꿈은 정리한다.
- 같은 DID 팀/시작일에 연결되는 기존 이력 별칭은 형식이 특이하더라도 원문을 보존한다.
  기존 playerDB 소속이 강등 팀이라는 이유로 오래된 시즌 참가 구단 계약으로 바꾸지 않는다.
  같은 구단의 선택 계약 등번호가 0이면 DID의 가장 최근 비영 번호를 사용하며 다른 구단 번호는 빌리지 않는다.
- 집계는 상세와 동일한 승인 RAW·경로·H3/H4 제외 결과를 사용한다. 원본 저장 KPI는 수정하지 않는다.
  마스터 수집은 이관 기록의 H3/H4 존재를 작은 조건 조회로 확인하고, 승인 예외의 영향이 있는 기록만 RAW를 읽는다.
  이 조회의 구성·버전도 전송 전 동시성 검사에 포함한다. 신규 입력은 기존 KPI POST 흐름을 유지한다.
- JMX 0096H·0167H·0231A·0258A의 저장 차이는 예전 평점 요청에서 레거시 플래그가 누락되어
  DAP가 각각 185→190, 348→349, 334→335, 164→165로 재분류된 입력으로 재현된다.
  정상 레거시 입력의 마지막 값은 로컬의 기존 실제 JPD 응답 요청과 일치한다.
  오래된 응답을 운영용 데이터로 자동 대체하거나 평점 산식을 전송기에 복제하지 않는다.
- 평점의 모든 5분 입력은 경기 전체의 확정 RAW 판정을 누적한다. 구간에서 잘린 RAW를 재분류해
  DAP가 MULTISHEET와 달라지는 경로를 제거했다. 일반 RAW와 이관 RAW 모두 같은 기준이며,
  DTP는 마지막 슛 결과가 확인된 시점부터 누적한다. 중간 JMX 원점수의 99.9 초과는 허용하며
  이 사실을 최종 평점 상한이나 SSR 지수의 상한과 혼동하지 않는다.
- 신규 평점 요청은 최종 5분 입력과 RAW KPI의 일치를 확인하고, 정확한 요청과 입력 정책의 지문을
  ratingInputFingerprint에 함께 저장한다. 버전만 같은 과거 입력의 캐시는 재사용하지 않는다.
  실제 평점 재요청·Firestore 쓰기는 이번 단계에서 하지 않는다. 기존 저장 JMX 4건은 원본에 남는다.
  지문 정책은 `finalized-raw-flags-own-exclusive-ssr-confirmed-dtp-v3`이다. 승인된 이관 KPI 차이는
  요청에만 투영하므로 OG placeholder 등의 이유로 이관 KPI POST를 요구하지 않는다.
- 평균은 십진 원천값을 평균한 뒤 절삭하여 76.65가 이진 실수 오차로 76.64가 되는 문제를 막는다.

## SSR OWN 제외 및 이관 검증 분리 — 2026-10-23 V17

최신 검증 정책은 `own-exclusive-ssr-import-validation-v17`이다. 아래 기준이 이전 SSR·이관 재구축 설명보다 우선한다.

- 팀 SSR은 `(RAW의 전체 GOAL − RAW의 OWN 득점 수) / SHOT`이며 분모 0은 0이다.
  multiSheet_ALL SSR도 경기 시작부터 같은 기준으로 누적하고 후반에 초기화하지 않는다.
  이관 저장 OG=0을 분자 보정에 사용하지 않는다. 일반·레거시 KPI, 5분 평점 입력,
  SQL 진단 구간표·평점 입력에 동일하게 적용한다.
- GOAL·GOAL 구간·골 시각·FPX는 OWN 포함을 유지한다. SHOT을 추가하지 않으며
  선수 playerGoal에는 OWN을 만들지 않는다. FPX 성분은 여전히 SHOT와 전체 GOAL이다.
  운영에 이미 존재하는 OWN playerGoal도 같은 경기·팀·하프·초의 이관 RAW 자책골로 확인되는
  경우에만 기존 문서 제외 목록에 기록하고 잔존 문서 차단에서 제외한다. 삭제나 재전송은 하지 않는다.
- 이관 자료는 신규 입력용 KPI POST를 하지 않는다. legacyGiId와 레거시 RAW 판정 근거가 있는
  기록에서 GOAL·SHOT·OWN 제외 SSR이 RAW와 같으면 OG=0을 이관 placeholder로 기록하고 차단하지 않는다.
  이 예외를 신규 입력이나 다른 KPI 차이에 적용하지 않는다.
- 이미 승인한 0070H·0275H 경로 차이와 0087H·0326H RAW 제외는 전송기 내부에서 처리한다.
  이관 문서의 저장 KPI를 다시 쓰도록 요구하지 않는다. 원래 값과 생성값 차이는 감사 기록에 남긴다.
  이 승인으로 실제 평점 수치 차이까지 자동 승인하지 않는다.
- 별도 승인된 0087H(E-EV / E-FU)의 APX 67.75 ↔ 67.50 차이만 검증 차단에서 제외한다.
  정확히 승인한 RAW 제외와 이관 근거가 있고 DAP가 원본·저장 271에서 270으로 바뀌며 기준값이 400인
  경우에만 적용한다. `actual_passed=false`와 승인 사유·수치를 남기고 저장 평점은 보존한다.
- 운영 multiSheet_ALL의 SSR은 각 5분 구간의 비율이고 전송값은 경기 전체 누적 비율이다.
  사용자 승인에 따라 이 해석 차이로 발생한 중간 SSR 7,060개 필드 차이는 허용한다.
  마지막 누적 SSR과 최종 KPI는 같은 기준이어야 하며, 임의 SSR 오류를 허용하는 예외는 아니다.
  다른 필드 차이는 이 승인에 포함하지 않는다.
- JMX는 SSR 입력 성분의 영향 범위다. 이번 코드 작업에서는 저장 평점을 재계산하지 않고
  저장 JMX를 보존한다. 외부 서비스의 새 응답 없이 모든 평점의 일치를 주장하지 않는다.
- 이전 검증 preview는 재사용하지 않는다. Firestore 쓰기·평점 요청·실제 전송은 실행하지 않는다.

## 실자료 재대조 후 보완 — 2026-10-23

이 절의 최초 검증 정책은 V15였으며, 현재는 위 V17이 우선한다. 이전 미리보기는 재사용하지 않는다.
이번 작업은 코드 보완·오프라인 재생성에 한정하며, 기존 Firestore를 수정하거나 평점 요청을 실행하지 않는다.

- H3/H4로 설명되는 선수 KPI 차이만 전송 적격성 검사에서 제외한다. 전체 RAW로 계산한
  해당 선수·필드 값과 저장값이 같아야 하며, 무관한 H1/H2 차이는 계속 차단한다.
- Player 이력은 현재 시즌 등록·출전 여부와 독립적으로 보존한다. 마스터 수집은 EPL 계약
  전체의 선수도 포함한다. 과거 계약만 있는 은퇴 선수에게 활성 playerDB 등록을 새로 만들지는 않는다.
- Player IDX_CODE는 해당 시즌 Player 안의 팀별 고유 대응을 우선한다. 역사적 계약에 그 참조가
  없으면 검증된 다른 시즌 code를 사용할 수 있다. LTNX/LUTX처럼 컬렉션별 표기가 다를 수 있다.
  경기 번호·집계 슬롯에는 이 예외를 적용하지 않는다. 팀 대응이 모호하면 차단한다.
- `p_id_old`의 팀 뒤 숫자는 등번호가 아니다. 시즌·선수·팀·계약 시작일이 같은 기존 Player의
  등록 식별자만 대응한다. playerDB P-17은 DID에 존재하는 계약들에 대응한 별칭의 이력이다.
  기존 별칭이 없는 새 DID 계약에 등번호를 붙여 가짜 레거시 식별자를 만들지 않고 빈 값으로 둔다.
  이 별칭은 이전 식별자의 연결일 뿐 이름·계약일·평점을 운영 문서에서 복사하는 경로가 아니다.
- Player IDX_CODE와 playerDB P-8은 다른 코드 공간이다. P-8은 해당 시즌 playerDB 팀별 고유
  약자를 우선하고, 없으면 code의 `구단(약자)` 또는 DID 팀 nameShort를 사용한다.
  Player/PlayerDB 참조는 신원·코드 연결에 사용한다. 운영 전용 필드 보존은 아래 V15 기준을 따른다.
  열린 시작·종료일도 명시적으로 출력한다.
- 시즌별 운영 code에 한글 팀명이 있으면 dMST·Manager·집계의 표시 이름에 사용한다.
  새 역사적 코드·별칭 참조가 바뀌면 전송 전에 재검증하도록 guard를 추가했다.
- 계약 전체 조회용 `contracts.leagueId` 컬렉션 그룹 인덱스를 `firestore.indexes.json`에 추가했다.
  인덱스가 아직 없으면 leagueId 필드만 최대 50,001문서까지 읽고 50,000 초과 시 차단한다.
  이 경로는 읽은 전체 문서 버전을 검사한다. 인덱스 배포는 이번 작업에서 실행하지 않는다.
  SDK가 반환하는 protobuf Timestamp와 DatetimeWithNanoseconds도 동일한 나노초 정밀도로 비교한다.
- 레거시 PHP는 SSR에서 OWN을 뺐고 이관 OG는 0으로 시작했다. KPI rebuild는 RAW와 GOAL·SHOT이
  같고, 기존 SSR=(GOAL−RAW의 OG)/SHOT인 경우만 알려진 과거 기준으로 검증한다.
  현재 V16에서 SSR=(GOAL−RAW의 OWN)/SHOT으로 통일했다. 임의 SSR 차이는 허용하지 않는다.
- rating 요청·기존 rating 재사용 전에 검토된 RAW와 저장 KPI 전체를 확인한다.
  맞지 않으면 `rating_kpi_definition_mismatch`로 KPI rebuild를 먼저 요구한다.
- rating의 5분 입력도 전송 구간표와 같은 마지막 슛 결과 시점을 사용한다. 레거시 경로에 DTP
  표시가 미리 있어도 첫 패스부터 DTP로 세지 않는다. 경계 300초는 다음 구간에 넣으며,
  마지막 구간은 실제 기록된 추가시간 이벤트를 누락하지 않는다. 수식은 여전히 JPD-RATING에만 둔다.

실자료에서 운영 JMX 구간 15,200개는 과거 기준값을 사용하면 전부 재현되었다.
현재 DID 기준과 혼용된 운영 타임라인을 그대로 새 정답으로 복사하지 않는다.
DID 원시 평점은 유지하며, 전송 평점은 아래 V15의 두 자리 절삭을 적용한다.
현재 기준은 DAP 400·DTP 12·SHOT 15·GOAL 3·SSR 0.18이며 과거 운영 타임라인과의 차이는 기록한다.

### 사용자 확정 사항 반영 — V15

- 운영 전용 Coach·Captain·PDF·W-TOT·W-TOT2 및 같은 성격의 필드는 보존한다.
  미리보기에서 기존 문서 위에 생성 필드를 얕게 합쳐 최종 문서를 만들고, 전체 기존 문서를
  baseline으로 확인한 트랜잭션에서 이 최종 문서를 기록한다. 생성된 중첩 맵은 전체 교체하여
  제거된 계산 항목을 남기지 않는다. 기존부터 미사용으로 제외한 pMST 예측·rank 필드는 복원하지 않는다.
  변경되지 않은 timestamp 등은 트랜잭션에서 읽은 원래 Firestore 타입을 유지한다.
- aifootballx 전송 평점은 둘째 자리까지 절삭한다. 10.685→10.68이며 반올림하지 않는다.
  기존 한 자리 집계 필드는 한 자리 절삭을 유지한다. DID 원시 평점·API 원본은 수정하지 않는다.
- 같은 팀·시작일의 중복 계약은 명시된 종료일을 null보다 우선하고, 번호 0과 실제 번호가
  공존하면 실제 번호를 사용한다. 서로 다른 실제 종료일은 생성 시각이 확인되는 최신 문서를
  사용한다. 작성 순서가 없거나 동률이면 차단하며, 서로 다른 실제 등번호·포지션 충돌도 차단한다.
  프랜시스는 createdAt이 더 늦은 2023-08-31 종료 이력으로 대응한다.
- KPI rebuild는 RAW 없는 경로 메타데이터를 가상 H4 이벤트로 추가하지 않는다.
  0001A의 오래된 경로 중복을 제거하여 RAW 기준 DTP 9·TTP 63을 계산한다.
  0070H는 DTP 21→16·TTP 69→58, 0275H는 TTP 104→103의 승인된 차이만 허용한다.
- 0087H의 RAW `023145453dec4d7aa36f3c733ad77ae2`(선수 0000000833)와
  0326H의 RAW `46e47d4b2bb44e7284736502f16d0387`(선수 0000003732)는
  계산·전송 입력에서 제외한다. 해당 경기·팀·선수·시각·액션이 검토 내용과 달라지면 차단한다.
  다른 미등록 선수 RAW에 일반화하지 않으며 Firestore 원본은 삭제하지 않는다.
- 최종 보고에는 위 제외, BMAS·미사용 컬렉션, H3/H4, OWN playerGoal 제외,
  경로 지수표기, 승인된 동시각 RAW 7건, 감독 실제 계약일·유일 순위 차이를 모두 남긴다.
  0043 NEWX의 후반 2초 TACTIC 한 건은 운영 누적 시각 2762와 일관된 계산값 2764의
  기존 데이터 차이로 기록하며, 코드에 임의 2초 보정을 넣지 않는다.

저장 KPI와 평점은 코드 변경만으로 갱신되지 않는다. 다만 이관 데이터에는 신규 KPI POST를
완료 조건으로 요구하지 않는다. 실제 평점 차이와 승인된 이관 원천 차이는 구분하여 검증한다.

## 현재 전송 기준 — 2026-10-22

아래 기준이 이 문서의 이전 merge·분석 골·선수 마스터 설명보다 우선한다.
이번 변경은 코드와 오프라인 테스트에 한정한다. 실제 POST, Firestore 재대조/쓰기,
평점 재계산, 실행 중인 백엔드 재시작은 별도 진행 승인 후 수행한다.

- `playerMST`, `aiPHR`, `yPHR`, `prediction`, `pushMatches`는 미사용으로 전송하지 않는다.
  BMAS 네 컬렉션도 이번 범위에서 제외하고 writer 허용 목록에서 제거했다.
- 팀 GOAL에는 OWN을 이미 포함한다. V16의 SSR은 OWN을 제외한 득점/SHOT이며,
  multiSheet_ALL SSR은 경기 시작부터 같은 기준의 누적 비율이다. FPX는 전체 GOAL을 사용한다.
  OWN 때문에 SHOT을 추가하지 않고, 개인 playerGoal에는 OWN을 만들지 않는다.
- vibePlayer의 `P-DTP`는 메이커·어시스터 기여 `DTM + DTA`다.
  `playerStats.DTP`의 고유 경로 참여 횟수, 빌드업 DTB, 슛 역할 DTS와 구분한다.
- DTP 구간은 마지막 슛의 결과 이벤트로 판정한다. 뒤의 무관한 액션으로 구간을 옮기지 않고,
  결과가 아직 없는 시퀀스는 확정 DTP 구간을 만들지 않는다.
- H3/H4는 이번 전송 적격성 판단의 입력에서 제외한다. 원본을 삭제하거나 변경하지 않는다.
- TACTIC 원본 path ID는 문자열로 보존한다. 기존 지수표기와는 표시 정밀도와 나머지
  이벤트 식별값을 함께 확인하여 양방향 대응한다. 복수 후보는 자동 선택하지 않는다.
- Player는 DID의 `teamId + from` 계약 구분을 따라 이력별 문서를 만든다.
  대상 자연키는 시즌·선수·리그·팀·계약 시작일이며, 종료일은 값이므로 계약 종료 시 같은
  문서를 갱신한다. 시작 미상 `0000.00.00`, 열린 종료 `9999.99.99`를 유지한다.
  playerDB는 시즌 최신 등록 정보를 유지한다. 감독은 실제 계약일을 사용한다.
- 현재 순위는 승점→득실→득점→팀코드로 동률을 해소하며 공동순위를 만들지 않는다.
- 아래는 당시 기준이며 V15의 운영 전용 필드 보존으로 대체되었다. writer의 `merge=False`는
  보존값까지 미리 합친 최종 문서를 원자적으로 기록하기 위한 방식이다.
  전체 기존 문서를 baseline으로 검사하므로 preview 후 다른 필드가 바뀌어도 새 검증이 필요하다.
  영수증 기록과 원자적 재시도는 유지하며 이전 정책의 preview를 재사용하지 않는다.

최종 보고에서 누락하면 안 되는 예외:

1. BMAS 및 미사용 컬렉션은 검증 통과가 아니라 범위 제외다.
2. 과거 playerGoal의 OWN 33건은 새 생성 대상에서 의도적으로 제외한다.
3. 과거 TACTIC의 지수표기 384건은 ID 표현 차이로 분류한다. 동일 시간 입력 오류로
   확인된 추가 RAW 7건은 승인된 감사 예외이며, 일반적인 동시 이벤트를 일괄 삭제하지 않는다.
4. 감독의 시즌 적용일과 실제 계약일 차이, 과거 공동순위와 현재 유일 순위 차이는 승인된 정책 차이다.
5. 기존 운영 SSR 값과 새 정의의 대조, rating 완료 후 결과 대조, 실제 전송 후 검증은 아직 미완료다.

SQL 상세 생성기의 playerMST는 과거 SQL 원천/평점 요청을 점검하기 위한 로컬 진단 자료다.
전송 후보가 아니며 writer가 해당 컬렉션을 거부한다.

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


## 모듈 통합 - 2026-10-27

- `backend/build/build_legacy_import.py`: SQL 이관, 메타데이터 복구, Firestore 이관 대조 검증 통합. API는 check 폴더가 아닌 이 모듈을 참조한다.
- `backend/check/check_legacy_import.py`: 이관 회귀 테스트 및 오프라인 SQL 파이프라인 검증 통합. 검증 CLI는 `python -m backend.check.check_legacy_import <SQL 파일> --output <보고서 경로>`로 실행한다.
- `backend/check/check_aifootballx.py`: 전송 정책 테스트를 포함한 AIfootballX 검증 통합.
- `backend/system/system_kpi_rules.py`: RAW 승인 예외 및 평점 절삭 규칙 통합. 계약 이력 중복 해소 규칙은 `system_firestore.py`에 통합한다.
- `backend/temporary`와 통합 전의 개별 감사·정책·예외 모듈은 제거했다. 이 정리는 파일 구성과 참조 경로만 변경하며 외부 서비스 호출이나 Firestore 쓰기를 수행하지 않는다.


## 과거 계약 팀 식별값 조회 보완 - 2026-10-27

23-24 ALL 검증 오류 126건은 허더즈필드 57건, 카디프 64건, 바르셀로나·지로나·비야레알·베티스·오비에도 각 1건이었다. 해당 팀의 code 문서는 없지만, 기존 24-25~26-27 Player에는 팀별 식별값이 존재한다. 시즌 한정 조회 때문에 기존 식별값을 찾지 못한 것이 원인이다.

현재 시즌의 Player 및 code에서 해석하지 못하는 과거 계약 팀만 대상으로 다른 시즌 Player의 t_code/IDX_CODE를 조회한다. 단일 식별값만 재사용하며 충돌은 계속 차단한다. 다른 시즌 문서는 수정 후보에 넣지 않는다. 경기용 팀 코드와 20팀 슬롯 규칙도 변경하지 않는다. 전송 직전에 참조값이 바뀌면 재검증을 요구한다. 검증 정책은 v21로 변경하여 이전 후보를 그대로 재사용하지 않는다.

실제 Firestore 읽기 기반 23-24 ALL 재검증: 380경기·선수 2,469명, 후보 8,600개 문서, 검증 오류 0건, 집계 ready. 후보 8,600개 문서 ID를 목적지에서 재조회한 결과 기존 문서 761개와 대응하며, 24-25~26-27 운영 시즌 문서와의 충돌은 0건이다. 과거 팀 참조값 재조회도 통과했다. 전체 로컬 테스트 370개 통과. Firestore 쓰기·실제 전송·평점 요청은 실행하지 않았다.


## 전체 전송 진행률 - 2026-10-27

`/send/status`의 percent는 전송 요청 전체를 기준으로 계산한다. 조회·후보 생성·검증은 0~50%, 실제 쓰기 전 원본 재확인 및 문서 전송은 50~99%, 정상 완료는 100%로 표시한다. 단독 preview 요청은 검증 완료 시 100%/ready다. 실패·부분 실패는 100%로 올리지 않는다.

선수 계약 조회, 경기 RAW 조회, 이관 H3/H4 검사, 원본 변경 재확인 등 실제 처리 단위에 따라 진행 상황을 기록한다. 단계 가중치 기반이며 경과 시간 또는 남은 시간 비율은 아니다. 진행률은 로컬 작업 저장소에 보존하여 단계 전환 및 같은 요청의 재검증에서 감소하지 않게 한다. 처리 중 스냅샷과 원본 보호 규칙은 유지한다. 전체 로컬 테스트 376개 통과. 실제 전송·Firestore 쓰기·백엔드 재시작은 수행하지 않았다.


## Swagger 전송 역할 분리 - 2026-10-27

공개 API는 GET `/aifootballx/send/status`와 개별 3개·통합 ALL의 총 4개 POST로 구성한다. `/preview`, `/send/latest` 공개 경로는 제거했으며 `/send/all`은 경기 상세까지 포함하는 통합 요청으로 교체했다. 전송 전 원본 캡처·후보 생성·검증은 비동기 작업 내부에서 유지한다.

| POST | 컬렉션 |
|---|---|
| `/send/matches` | dMST, TACTIC, vibePlayer, playerGoal, multiSheet_BAP, multiSheet_DAP, multiSheet_DTP, multiSheet_SHOT, multiSheet_GOAL, multiSheet_GOAL-시간, multiSheet_JMX, multiSheet_ALL |
| `/send/masters` | Player, playerDB, Manager, code |
| `/send/aggregates` | pMST, mMST, cAVG, rAVG, rank, sAVG |
| `/send/all` | 위 세 그룹 전체 22개 컬렉션 |

세 POST는 season만 필수이며 latest(boolean, 기본 false)를 선택할 수 있다. latest=true는 최근 라운드 선택이 아니라 신규·변경 문서만 전송하는 옵션이다. matches와 aggregates는 round_from, round_to, gm_id를 선택 입력으로 받는다. masters는 시즌 전체 식별·소속·계약·감독·코드만 처리하며 경기 RAW·Rating을 조회하지 않는다. aggregates는 선수·감독 계약을 조회하거나 생성하지 않는다.

세 POST는 같은 비동기 작업 흐름과 공통 status를 사용한다. status는 최근 요청을 표시하며 0~100% 전체 진행률을 유지한다. Swagger 각 POST 설명에는 대상 컬렉션 이름만 표시한다. 이전 작업 이력 조회를 위해 내부의 구 all/latest 범위 해석은 남겨 두되 공개 전송 경로로 제공하지 않는다.

집계 라운드 필터는 해당 경기의 pMST/mMST를 선택하며, 라운드 집계는 계산에 필요한 이전 라운드 원본을 함께 참조한다. 과거 라운드 변경 시 이미 발행된 이후 라운드 누적 집계와 sAVG도 갱신한다. 새 시즌에서 1라운드만 지정하고 이후 집계가 없다면 이후 라운드 집계를 새로 생성하지 않는다. 집계 자체의 검증 실패는 해당 aggregates 전송을 차단하며 masters 결과와 섞지 않는다.

전체 로컬 테스트 389개 통과. 저장된 23-24 캡처를 재사용한 대조에서 기존 후보 8,600개가 masters 7,345개·aggregates 1,255개로 분리되며 문서 ID와 payload는 전부 동일하다. 새 파일 추가, 실제 Firestore 쓰기·전송·평점 호출·백엔드 재시작·커밋·push는 수행하지 않았다.


## 통합 ALL - 2026-10-27

`POST /send/all`은 masters, 선택 경기 상세(matches), aggregates를 하나의 비동기 요청으로 실행한다. season만 필수이며 latest·round_from·round_to·gm_id는 선택이다. 경기·라운드 필터는 matches와 aggregates에 적용하고 masters는 지정 시즌 전체를 유지한다. 공통 GET `/send/status`에서 mode=all인 하나의 진행률·검증·전송 결과를 확인한다.

각 그룹의 후보를 내부에서 생성하고 전체 검증을 마친 후에만 실제 전송을 시작한다. 어느 그룹이든 검증이 실패하면 해당 ALL 요청의 쓰기를 시작하지 않는다. 공통 원본 버전·쿼리 멤버십은 합쳐 검사하며, 그룹별 코드·슬롯·계약 참조 보호는 각각 유지한다. ALL 후보에 포함된 신규 시즌 코드만 정상적인 추가로 허용한다. latest=true는 세 그룹 모두에 적용하며, 경기 수는 matches에서만 집계하여 집계 그룹과 중복 계산하지 않는다.

구 all(마스터+집계) 작업 이력과 새 통합 ALL은 내부 scope의 unified 값으로 구분한다. 새 HTTP ALL은 항상 unified=true이며, 이전 작업 이력을 새 통합 전송으로 오인해 재사용하지 않는다. 전체 로컬 테스트 396개 통과. 실제 Firestore 쓰기·전송·평점 호출·재시작·커밋·push는 수행하지 않았다.


## 유지보수 API 표시 정리 - 2026-10-27

`/match-ratings/read`, `/legacy-import/metadata-repair`, `/legacy-import/metadata-status`는 Swagger/OpenAPI에서만 숨긴다(`include_in_schema=False`). 내부 함수·실제 경로·인증 및 기존 동작은 유지한다. KPI·Rating 생성, 이관·검증·backfill 및 전송 경로는 변경하지 않는다. 관련 기존 테스트 4개 통과. 실제 데이터 쓰기나 유지보수 실행은 수행하지 않았다.


## latest 옵션 변수 충돌 수정 - 2026-10-27

감독 계약 선택에 사용하던 지역 변수 latest를 latest_contract_start로 변경하여 전송의 latest boolean을 덮어쓰지 않도록 수정했다. 감독 계약이 있는 경우에도 latest=false는 전체 후보, latest=true는 신규·변경 후보를 선택한다. 해당 회귀 테스트를 추가했으며 전체 로컬 테스트 398개 통과. 실제 Firestore 쓰기·전송·재시작·커밋·push는 수행하지 않았다.
