# 백엔드 개발자 작업 안내 — didNew → jpd-rating → footballX

전체 작업은 아래 5단계다. 순서대로 진행한다.

```
1. 스키마 설계        2. KPI 계산         3. jpd-rating 호출      4. 레거시 데이터로 검증    5. 조립·전송
(schema.ts 기준)  →  (팀/선수 KPI 산출) →  (평점 받아오기)   →   (옛날 SQL 경기로 대조)  →  (footballX로)
```

---

## 1단계. 스키마 설계 — `app/types/schema.ts` 기준

Firestore에 뭘 저장할지는 이미 `app/types/schema.ts`에 다 정해져 있다. 백엔드는 이 파일을
**그대로 따라간다** — 새로 설계하지 않는다.

- 사람이 읽는 설명은 `docs/04_firestore_schema.html` (기준 문서. `.md`로 된 같은 이름 파일은 구버전이라 안 봐도 됨)
- 레거시 SQL 컬럼과 새 필드 이름이 어떻게 바뀌었는지는 `docs/05_legacy_field_mapping.md`
- KPI 이름(TAP/DAP/DTP 등)이 왜 이렇게 바뀌었는지는 `docs/03_kpi_terminology.md`

이 3개 문서 + `schema.ts`가 기준이고, 여기 안 나온 필드는 만들지 않는다.

---

## 2단계. KPI 계산 — 팀 KPI / 선수 KPI

경기 기록(`records`)을 읽어서 팀 KPI 14개, 선수 KPI 17개를 계산한다.

**판정 로직(공격 루트 끊기, BAP 판정 등)은 이미 `app/utils/didLogic.ts`에 있다** — 레거시 780경기로
검증까지 끝난 로직이니 새로 짤 필요 없이 파이썬으로 그대로 옮기면 된다.

- `computeAttackPaths()` — records → 공격 루트별 분류
- `computeBap()` — records → BAP 판정
- 이 두 결과를 세어서 `RecordingKpi`(팀, 14개 필드) / `PlayerKpi`(선수, 17개 필드)로 집계하는 코드만
  새로 짜면 된다 (필드 이름·계산 규칙은 `docs/04_firestore_schema.html`의 "KPI" 표 참고)

**추가로 하나 더**: 위와 똑같은 계산을, 5분 단위로 20번(전반 10번 + 후반 10번) 나눠서도 돌린다.
이건 3단계(jpd-rating 호출)에 보낼 값이다 — 별도 저장은 안 하고 계산해서 바로 넘긴다.

저장 위치: `matches/{gm_id}/recordings/{H|A}` 문서의 `kpi` / `kpiVersion` 필드.

---

## 3단계. jpd-rating 호출 — 평점 받아오기

2단계에서 만든 KPI를 jpd-rating 서버로 보내면, 평점(JMX/APX/TPX/FPX)을 계산해서 돌려준다.
**공식은 jpd-rating 안에만 있고 백엔드는 절대 몰라도 된다** — 원자값 보내고 결과값만 받는 구조다.

### 주소

```text
https://jpd-rating-296087686925.asia-northeast3.run.app/v1/ratings/calculate
```

### 인증

```http
Authorization: Bearer <전달받은_토큰>
Content-Type: application/json
```

토큰은 이미 전달받은 상태. 소스코드·로그에는 남기지 말고 서버 환경변수로 읽는다.

### 요청 본문 (한 경기, 한 팀당 1번 호출 — 홈/원정 각각)

```json
{
  "gm_id": "경기 문서 ID",
  "side": "H",
  "leagueId": "리그 ID",
  "seasonId": "시즌 ID",
  "matchType": "league",
  "round": 1,
  "teamId": "팀 ID",
  "opponentTeamId": "상대팀 ID",
  "kpiVersion": 1,
  "teamKpi": {
    "TAP": 0, "DAP": 0, "TTP": 0, "DTP": 0, "BAP": 0,
    "DTB": 0, "DTM": 0, "DTA": 0, "DTS": 0,
    "SHOT": 0, "ASR": 0, "SSR": 0, "GOAL": 0, "OG": 0
  },
  "teamKpi5min": [
    { "DAP": 0, "DTP": 0, "SHOT": 0, "SSR": 0, "GOAL": 0 }
  ],
  "playerKpis": [
    {
      "playerId": "선수 문서 ID",
      "TAP": 0, "DAP": 0, "UTP": 0, "DTP": 0, "TTP": 0, "SHOT": 0,
      "AST": 0, "GOAL": 0, "DTB": 0, "DTM": 0, "DTA": 0, "DTS": 0,
      "GTB": 0, "GTM": 0, "ASR": 0, "SSR": 0
    }
  ]
}
```

- `teamKpi5min`은 **반드시 20개**, 2단계에서 만든 그 배열.
- 리그 경기는 `round`, 토너먼트는 `stage`(+`group`/`leg`)를 넣는다.
- 필드 이름을 마음대로 바꾸거나 위 목록에 없는 필드를 추가하면 요청이 거부된다(422).

### curl 테스트

```bash
curl -X POST 'https://jpd-rating-296087686925.asia-northeast3.run.app/v1/ratings/calculate' \
  -H 'Authorization: Bearer <전달받은_토큰>' \
  -H 'Content-Type: application/json' \
  --data @request.json
```

### 응답 저장 위치

| 응답 필드 | 저장 위치 |
|---|---|
| `teamRating` (jmx/jmxSeries/apx/apxGrade/tpx/tpxGrade/fpx/fpxGrade) | `matches/{gm_id}/recordings/{H\|A}`의 `teamRating` |
| `ratingBasedOn` | 같은 문서의 `ratingBasedOn` — `kpiVersion`과 대조용 |
| `playerRatings[]` | `matches/{gm_id}/recordings/{H\|A}/playerStats/{playerId}` 문서 |

`scoreRel`/`scoreAbs`/`score`는 아직 jpd-rating에 공식이 없어서 항상 `null`로 온다 — 에러 아니니 그대로 저장.

쓰기 직전에 `RecordingDoc.kpiVersion`을 한 번 더 읽어서 요청 때 쓴 값과 같은지 확인한다. 다르면
(그 사이 기록이 수정됐다는 뜻) 응답을 버리고 다시 요청한다.

### 여러 건 한꺼번에

- 라운드 전체, 시즌 전체처럼 여러 경기를 한 번에 보낼 땐 `POST /v1/ratings/calculate-batch`(최대 1000건)
- 오래 걸리는 대량 작업은 `POST /v1/ratings/jobs`로 접수하고 `GET /v1/ratings/jobs/{jobId}`로 진행 상황 확인

---

## 4단계. 레거시 SQL 데이터로 검증

옛날 SQL 데이터는 그냥 넣을 수 없다 — 새 스키마(`schema.ts`) 모양이 아니라서, **먼저 1회성 이관
스크립트**(파이썬/Node, 어느 쪽이든 상관없음)를 만들어야 한다. 순서:

1. 참조 데이터부터 — `ff_team`/`ff_player`/`ff_league`/`ff_season`/`ff_stadium` → `TeamDoc`/`PlayerDoc`/`LeagueDoc`/`SeasonDoc`/`StadiumDoc`. 경기 기록이 이 문서들의 ID를 참조하므로 반드시 먼저.
2. `ff_player_team` → `ContractDoc` 이력 재구성. 감독은 레거시에 이력이 없으므로(`t_coach` 문자열 하나뿐) `CoachContractDoc`은 수기로 채워야 할 수 있음.
3. 경기 기록 — `ff_game`→`MatchDoc`, `ff_game_info`→`RecordingDoc`, `ff_game_record`→`RecordDoc`. 컬럼 단위 대응표는 `docs/05_legacy_field_mapping.md`.
4. `gm_id`는 담당자가 수동으로 채번하는 값이라, 과거 시즌을 이관할 때도 기존 `gi_id`/일정 데이터에서 같은 규칙으로 채번해야 한다.

이 스크립트를 실행할 임시 화면을 `/manage/legacy-import`(데이터 관리 메뉴 안)에 하나 만들어뒀다 —
지금은 "준비 중" 자리만 있고, 실제 이관 로직은 백엔드 개발자가 여기에 연결한다.

이관이 끝나면, **옛날 SQL 경기 데이터로 새 파이프라인이 같은 값을 내는지 대조**한다. `didLogic.ts`
쪽은 이미 레거시 780경기로 20/20 검증이 끝나 있으니, 그중 일부를 파이썬 쪽에도 그대로 돌려서 KPI
값이 일치하는지 확인하면 된다 — 이관 직후에는 원본 SQL 집계값과도 한 번 더 대조한다(예: 팀당
TAP/DAP가 이관 전후 일치하는지).

5분 구간 값도 검증한다 — 실제 경기 하나를 골라 3단계 API에 보내고, 응답으로 온
`teamRating.jmxSeries`의 20번째(마지막) 값이 `teamRating.jmx`와 같은지 확인한다(항상 같아야 정상).

---

## 5단계. 조립해서 footballX로 전송

3단계까지 끝나면 다음을 만든다:

- **팀 최종 결과** (`kpi` + `teamRating`) + **선수 최종 결과** (`playerStats`) + 팀·선수 이름 같은
  참고 정보를 합쳐서 footballX가 원하는 표 형식(`dMST`/`playerMST`/`multiSheet`)의 행으로 만든다
  — 정확한 열 구성은 footballX 저장소(`footballx/app/pages/admin/...`) 쪽 코드가 기준
- **S-Round**(옛날부터 쓰던 채번값)는 didNew Firestore에는 저장하지 않고, **이 단계에서 그때그때
  다시 만들어서** 표에만 넣는다 — 이미 780경기로 검증된 채번 규칙 그대로 재사용
- 만들기 전에 `ratingBasedOn`이 `kpiVersion`과 같은지 확인한다. 다르면(3단계 이후 기록이 또 바뀐
  경우) 3단계부터 다시 한다
- footballX로 보낼 땐 같은 경기·같은 팀으로 두 번 보내도 중복 행이 안 생기게(멱등) 만든다

---

## 지금은 안 해도 되는 것

- 실시간 갱신(경기 도중 실시간으로 계산하는 것) — 나중에 필요할 때 별도로
- 이 파이프라인을 여러 서버가 동시에 처리하는 경우의 충돌 방지 — 서버 한 대로 돌릴 땐 필요 없음
- 평점 기준값(baseline) 버전을 경기 기록에 같이 남기는 것 — 나중에 "예전 평점이 왜 그렇게 나왔는지"
  다시 확인해야 할 일이 생기면 그때 추가
