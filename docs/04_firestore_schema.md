# DID Firestore 스키마 설계

최종 갱신: 2026-09-02 (전면 개정)

---

## 0. 이 문서의 전제 — 스키마는 목적이 아니라 수단이다

didNew의 목표는 **사람이 하던 가공 단계를 없애는 것**이다.

```
현재   DID 입력 → MySQL → 사람이 추출·엑셀 가공 → CSV → JaionX admin 업로드
목표   DID 입력 → Firestore → 파이썬 자동 변환 → JaionX 갱신

       → JaionX에 들어가는 데이터가 지금과 동일해지는 순간, 구 DID 폐기
```

따라서 이 스키마의 합격 기준은 "잘 설계됐는가"가 아니다.

> **규칙 0 — JaionX가 받는 모든 컬럼은 Firestore 데이터만으로 복원 가능해야 한다.**
> 복원 불가능한 값이 하나라도 있으면 그 값은 반드시 저장한다.
> "계산으로 대체한다"는 결정은 **복원 가능함이 증명될 때만** 유효하다.

출력 규격의 실체는 `footballx` repo에 있다 (§9).

---

## 1. 설계 원칙

| # | 원칙 | 이유 |
|---|---|---|
| 0 | **JaionX 출력을 전부 복원할 수 있어야 한다** | 위 §0 |
| 1 | **접두사를 버린다** (`gr_`, `gi_`, `gm_`) | SQL은 네임스페이스가 평면이라 접두사가 필요했다. Firestore는 컬렉션이 이미 네임스페이스다 |
| 2 | **이름은 `docs/03`의 신용어를 쓴다** | TMP→TAP, TAP→DAP, CTP→DTP, CSP→DSP, CT*→DT*. 레거시 이름은 §9의 매핑표에만 남긴다 |
| 3 | **계산되는 값은 저장하지 않는다** | 1,200건 전체 재계산이 **0.16ms**다(§14 실측). 저장은 어긋날 자리만 만든다 |
| 4 | **단, 확정 시점의 스냅샷은 저장한다** | 리포트·JaionX 동기화는 "지금 값"이 아니라 "그때 값"을 요구한다 |
| 5 | **같이 읽는 건 한 문서, 계속 늘어나는 건 서브컬렉션** | 라인업 18명은 map, 레코드 1,200건은 서브컬렉션 |
| 6 | **이력이 필요한 참조 데이터는 원장 + 파생 뷰** | 이적시장 = 계약 원장(추가 전용), 현재 스쿼드 = 조회용 뷰 |
| 7 | **과거 경기는 과거 모습 그대로 남는다** | 이적·등번호 변경이 작년 기록을 바꾸면 안 된다 → 라인업에 이름·번호 스냅샷 |
| 8 | **모든 값은 출처를 남긴다** | 사람 입력 / 비전 / 보정을 구분하지 않으면 나중에 신뢰도 판단이 불가능하다 |

---

## 2. 역할 분담 — 무엇을 어디서 계산하나

**판정 로직의 권위는 파이썬에 있다. 클라이언트 TS는 화면 보조일 뿐이다.**

| | 프로젝트 | 하는 일 | 접근 |
|---|---|---|---|
| **Nuxt 클라이언트** (`didLogic.ts`) | `jpd-did` | 루트가 끊겼는지 + 공격영역 진입 여부 → **선수 입력 버튼 띄우기 전용** | 누구나 (상관없음) |
| **KPI 서비스 (파이썬)** | `jpd-did` | DAP/DTP/STP/UTP 분류, B/M/A/S 역할, BAP, KPI 집계 | 개발자 포함 |
| **평점 서비스 (파이썬)** | **`jpd-rating` (별도)** | 선수평점·팀지수·등급, 기준값, 채번 규칙, **dMST/playerMST 조립 및 JaionX 쓰기** | **1~2명** |

**클라이언트에 남는 판정은 하나뿐이다** — "어느 레코드에 선수를 물어볼까". DTP/DAP라는 **이름을 붙이는 일은 전부 파이썬**이 한다. 클라는 ① 루트가 끊겼나(4초 공백·결과) ② 공격영역에 들어갔나(`area < 7`) 두 가지만 보면 되고, 이는 분류 로직이 아니다.

> 클라 규칙이 다소 넉넉해도 문제없다. 버튼이 몇 개 더 떠서 선수가 더 붙을 뿐이고, 파이썬이 DAP가 아니라고 판정하면 집계에서 빠진다. **반대로 덜 뜨면 데이터가 비므로, 넉넉한 쪽이 안전하다.**

### 2.1 클라이언트 판정 결과는 저장하지 않는다

`didLogic.ts`가 만든 플래그(`isDap`, `isDts` …)는 **화면에 버튼을 띄우는 데만 쓰고 버린다.**
저장되는 것은 사람이 찍은 사실뿐이고, 파이썬이 그것을 읽어 다시 판정한다.
→ **두 구현이 어긋나도 JaionX에 틀린 값이 가지 않는다.** 클라 쪽 오차는 버튼이 하나 더/덜 뜨는 정도다.

### 2.2 왜 클라이언트에 판정이 남아 있나

선수 입력 요청 횟수 때문이다. 실측(2026-08-21 ARS):

| 방식 | 경기당 선수 입력 요청 |
|---|---|
| DAP 판정 사용 (현재) | **457회** (`DAP`) |
| 액트마다 전부 물어봄 | **861회** (`gi_tmp`) — 거의 2배, 실시간에서 못 따라감 |

**DAP 판정이 곧 "선수를 물어볼 최소 집합"이다.** 이것이 클라에 로직을 두는 유일한 이유다.

> 향후 비전이 등번호를 채우게 되면 이 버튼 자체가 사라지고, 클라이언트 판정도 제거할 수 있다.
> 그때 구조는 **파이썬 단일**이 된다. 비전 설계는 이 문서 범위 밖(§16).

---

## 3. 가장 중요한 구조 결정 — `matches` ≠ `recordings`

**레거시가 이미 이렇게 하고 있었다. 새로 고안한 것이 아니라 보존하는 것이다.**

`ff_game_info`에는 `gi_write_code`(H/A)와 `gi_user_id`(기록자)가 있고, KPI 집계(`gi_tap`, `gi_bap` …)가 전부 이 테이블에 있다. 즉 **KPI는 경기 단위가 아니라 팀-기록자 단위다.**

레거시 코드가 이를 명시한다 — `lib/extra.lib.php:3799`:

```php
$gi[$row['gi_write_code']] = $row;      // H/A 를 키로 담고
if (count($gi) < 2) return FALSE;       // 2개가 아니면 실패
// 주석: "양팀 입력 모두 종료되지 않은 경기는 진행 불가"
```

팀 판별도 전부 `if(gi.gi_write_code = 'H', gi.gi_h_t_code, gi.gi_a_t_code)` 패턴이다.

그리고 이건 지금 앱과 일치한다 — TeamSelection의 "입력할 팀 선택"(`game.team`)이 곧 `gi_write_code`다.

### 3.1 바뀌는 것은 표현 방법뿐

| | 레거시 | 새 스키마 |
|---|---|---|
| 관계 | `ff_game` 1행 : `ff_game_info` 2행 | `matches` 1문서 : `recordings` 2문서 (**동일**) |
| 소속 표현 | `gm_id` 컬럼 + JOIN | **경로** `matches/{id}/recordings/{H\|A}` |
| H/A 구분 | `gi_write_code` 컬럼 | **문서 ID 자체** |
| 세션 키 | `gi_id` (숫자) | 불필요 — 경로가 키. 단 `legacyGiId`는 남긴다 |
| 중복 세션 | 가능 (막을 장치 없음) | **구조적으로 불가능** |

Firestore에는 JOIN이 없다. 소속을 표현하는 방법은 ①필드에 부모를 적고 질의 ②경로에 넣기 두 가지인데, ②는 색인이 필요 없고 질의가 아닌 **직접 읽기**이며 결과가 "있거나 없거나" 둘 중 하나다.

`matches/{matchId}/recordings/H` — 이 주소 한 번 읽기로 복구가 끝난다.

---

## 4. 전체 트리

```
── 경기 기록 ─────────────────────────────────────────────
matches/{matchId}                          ← ff_game            일정·확정 스코어
  recordings/{H|A}                         ← ff_game_info       팀별 기록 세션 ★
    records/{recordId}                     ← ff_game_record     플레이 1건
    subs/{subId}                           ← ff_game_player_log 교체 로그
    cards/{cardId}                         ← ff_game_card       경고/퇴장
    paths/{pathId}                         ← ff_game_path        [확정 스냅샷]
    bapEvents/{bapId}                      ← ff_game_bap         [확정 스냅샷]
    playerStats/{playerId}                 ← ff_game_player.gp_* [확정 스냅샷]

── 참조 · 데이터 관리 ────────────────────────────────────
leagues/{leagueId}                         ← ff_league
seasons/{seasonId}                         ← ff_season
stadiums/{stadiumId}                       ← ff_stadium
teams/{teamId}                             ← ff_team
  seasons/{seasonId}                       ← (신규) 시즌별 소속 리그 — 승강제 대응
  squad/{playerId}                         ← (파생) 현재 스쿼드 조회용 뷰
players/{playerId}                         ← ff_player
  contracts/{contractId}                   ← ff_player_team — 이적시장 원장 ★
recorders/{uid}                            ← ff_user
```

### 만들지 않는 것

| 레거시 | 처리 |
|---|---|
| `ff_game_state` (강제처리) | `recordings.status`를 관리자가 고치는 것으로 대체 |
| `ff_session`, `ff_member_session` | Firebase Auth 토큰이 대체 |
| `ff_formation` | 슬롯 좌표는 경기마다 변하지 않는 상수. `TeamSelection.vue` 상수로 두고 `formationKey`만 저장 |

---

## 5. `matches/{matchId}` — 일정

두 기록 세션이 **공유하는 사실만** 담는다.

### 문서 ID는 `gm_id`를 그대로 쓴다

```
matches/20262027eplj2rgn13na28rd2b8q0001
```

`gm_id`는 레거시의 옛날 키가 아니라 **JaionX의 모든 테이블을 잇는 조인 키**다. `TACTIC`·`playerMST`의 첫 컬럼이 전부 `gm_id`이므로, 신규 경기도 이것 없이는 JaionX에 넣을 수 없다.

**구조 (실데이터 780건으로 확정)**

```
20262027 epl j2rgn13na28rd2b8q 0001
└──┬───┘ └┬┘ └───────┬───────┘ └─┬┘
  8자     3자        17자         4자
  시즌    리그    리그 고정 상수  일련번호
```

| 구간 | 내용 | 검증 |
|---|---|---|
| `[0:8]` | 시즌 `20262027` | |
| `[8:11]` | 리그 `epl` | |
| `[11:28]` | **리그 고정 상수** | 2024-25·2025-26·2026-27 **세 시즌 780건 전부 동일.** 시즌별 난수가 아니다 |
| `[28:32]` | 일련번호 | 시즌 통산 `0001`~`0380`, 빠짐없이 연속. 라운드1 = 1~10, 라운드2 = 11~20 |

> 다른 리그를 추가할 때 상수가 달라지는지는 **확인 불가** — EPL 데이터밖에 없다.

`gm_id`를 문서 ID로 쓰면 매핑 필드가 필요 없고, `TACTIC`·`playerMST` export 시 **변환이 아예 없다.**

### 일정은 만들지 않고 가져온다

```
footballx/server/backend/data/matchschedule/{시즌}/match_schedule.json
   ─── 임포트 ───►   matches/{gm_id}
```

원본에 필요한 것이 다 있다:

```json
{ "gm_id": "20262027eplj2rgn13na28rd2b8q0020",
  "season": "2026-2027", "gm_date": "2026.08.31", "gm_round": 2,
  "home": { "code": "AVLX", "goal": 0, "jmx": 40.0, "points": 1, "sRound": 26270219 },
  "away": { "code": "ARSX", … }, "is_pending": true, "status": "FORECAST" }
```

**우리가 일련번호를 따로 매기면 안 된다.** 같은 시즌·같은 라운드에서 경기를 구분하는 것은 일련번호 4자리뿐이고, 그 번호는 일정이 만들어질 때 정해진다. 우리가 발급하면 같은 경기에 다른 번호가 붙어 JaionX와 영영 맞지 않는다.

| 필드 | 타입 | 레거시 | 비고 |
|---|---|---|---|
| (문서 ID) | string | **`gm_id`** | 위 구조. 일정에서 그대로 가져온다 |
| `date` / `kickoffTime` | string | `gm_date`, `gm_time` | |
| `leagueId` / `seasonId` / `round` | string / number | `gm_league`, —, `gm_round` | |
| `stadiumId` | string | `gm_s_code` | |
| `homeTeamId` / `awayTeamId` | string | `gm_h_t_code`, `gm_a_t_code` | 일정의 `home.code` / `away.code` (IDX_CODE 체계) |
| `sRound` | `{home, away}` | — | 일정에 이미 있는 D-MST `S-Round` 채번값 |
| `score` | `{home, away}` | `gi_goal_home/away` | 각 recording이 자기 쪽 필드만 merge (§5.1) |
| `createdAt` / `updatedAt` | Timestamp | | |

`legacyGameId`는 **두지 않는다** — 문서 ID가 곧 `gm_id`이므로 매핑이 필요 없다.

`is_view` / `is_onair` / `is_old`는 옮기지 않는다.
`is_realtime`은 경기 속성이 아니라 **기록자의 입력 모드**이므로 `recordings`로 내린다.

### 5.1 스코어는 누가 쓰는가

골은 각 기록자가 자기 팀 것만 찍는다. `score.home`은 H 세션이, `score.away`는 A 세션이 쓴다.
Firestore는 필드 단위 merge가 되므로 동시에 써도 충돌하지 않는다.

```ts
setDoc(matchRef, { score: { home: goals } }, { merge: true })
```

> ⚠️ **현재 코드 버그**: `DidInput.vue:455`의 `recordGoalResult`가 기록 중인 팀과 무관하게 항상 `homeScore.value++` 한다. 연동 시 `recordings.side` 기준으로 고칠 것.

---

## 6. `matches/{matchId}/recordings/{H|A}` — 팀별 기록 세션 ★

| 필드 | 타입 | 레거시 | 비고 |
|---|---|---|---|
| `side` | `'H'\|'A'` | `gi_write_code` | 문서 ID와 중복이지만 collectionGroup 질의용으로 유지 |
| `teamId` / `opponentTeamId` | string | `gi_h_t_code`, `gi_a_t_code` | |
| `recorders` | map | `gi_user_id` | **한 팀을 두 명이 나눠 찍는 경우가 있다** — 아래 §6.3 |
| `status` | `'ready'\|'H1'\|'H1_done'\|'H2'\|'H2_done'\|'final'` | `gi_state` | 이미 코드에 있는 값 그대로. 화면 상태와 1:1이라 매핑 코드가 불필요 |
| `inputMode` | `'분석'\|'실시간'` | `is_realtime` | **집계·전송 트리거를 가른다** (§11) |
| `fieldSide` | `'left'\|'right'` | `gi_part` | 전반 기준 진영. 구역코드 반전에 쓰인다 |
| `formationKey` | string | `gi_formation` | |
| `lineup` | map | `ff_game_player` (구성) | §6.2 |
| `halves` | map | `gi_h1_begin`, `gi_h1_seconds` … | §6.1 |
| `maxSeq` | number | — | 신규. 레코드 정렬키 발급용 (§7.2) |
| `kpi` | map \| null | `gi_tap`, `gi_bap` … | 확정 스냅샷 (§8) |
| `kpiComputedAt` | Timestamp \| null | — | 신선도 판단 |
| **`legacyGiId`** | number \| null | **`gi_id`** | **JaionX/aifootballx 동기화 매핑용. 신규 경기는 null** |
| `syncedAt` | Timestamp \| null | — | 동기화 멱등성 |
| `createdAt` / `updatedAt` | Timestamp | `gi_regdt`, `gi_moddt` | |

### 6.1 `halves` — 시간의 기준

```ts
halves: {
  H1: { startedAt: Timestamp, seconds: 2712 },   // 종료 시 확정
  H2: { startedAt: Timestamp, seconds: 2840 },
}
```

레코드에는 **`half` + `halfSeconds`만 저장**하고, 경기 전체 기준 초(`gr_seconds`)는 export 시점에 `halves`로 계산한다.

> ⚠️ **현재 코드 버그를 이 설계가 고친다.** `DidInput.vue:35`는 `seconds = ref(0)`이고 후반에도 0부터 다시 센다. 그런데 `DidInput.vue:67`에서 후반 진입 시 전반 레코드를 그대로 이어받으므로, 배열 안에 `H1의 30초`와 `H2의 30초`가 같은 값으로 공존한다. `didLogic.ts`의 `resolvedOnly()`가 `seconds`로 정렬하는 순간 **전·후반 레코드가 뒤섞이고 4초 룰이 반 경계를 넘는다.**

> **그리고 `half`/`halfSeconds`는 JaionX 요구사항이기도 하다.** `2627-5분단위 통합.xlsx`는 전·후반 × 5분 구간 20칸(`JMX-H1-0-5` … `DAP-H2-45-Plus`)으로 되어 있고, **팀 JMX가 이 시계열에서 산출된다**(§9.3). 반 구분과 반 기준 경과초가 없으면 이 시트를 만들 수 없다.

### 6.2 `lineup` — 서브컬렉션이 아니라 map

18명을 항상 통째로 읽고 쓴다. 서브컬렉션이면 로드마다 18 read, map이면 1 read다.

```ts
lineup: {
  [playerId]: {
    slot: 'o0' | 'gk' | 'b3',    // TeamSelection 의 assigned 키
    order: number,
    type: 'START' | 'BENCH',     // gp_type
    no: '7',                     // ★ 스냅샷
    name: 'B. Saka',             // ★ 스냅샷
    pos: 'FW',                   // ★ 스냅샷
    inHalf / inSeconds / outHalf / outSeconds,
  }
}
```

`no`/`name`/`pos`를 복사하는 이유(원칙 7): 선수가 이적하거나 번호를 바꾸면 `players` 문서가 갱신된다. 스냅샷이 없으면 **작년 경기를 열었을 때 그때 뛰지도 않은 팀의 번호로 표시된다.**

선수별 지표(`gp_tap`, `gp_score` …)는 여기 두지 않는다 — 파생값이고 목록 화면을 무겁게 한다. 확정 시 `playerStats`로 뺀다(§8).

### 6.3 `recorders` — 한 팀을 두 명이 찍는 경우 ★

**기록자가 항상 한 명이 아니다.** 한 팀을 둘이 나눠 찍는 방식이 실제로 쓰인다:

```
A  액트 · 위치 담당      → 레코드를 만든다
B  선수 담당             → A가 만든 레코드에 등번호만 채운다
```

레거시 `gi_user_id`가 단수라 이 구조를 표현하지 못했다. 새 스키마는 맵으로 둔다.

```ts
recorders: {
  [uid]: { rank: 'main' | 'sub', joinedAt: Timestamp }   // 주/부 — 조직상 구분일 뿐
},
recorderIds: [uid, …]     // ← 질의용. Firestore는 map 의 키로 질의할 수 없다
```

> ⚠️ **`rank`는 권한이 아니다.** 주 분석관과 부 분석관의 **기능은 완전히 동일하다.**
> "한 명은 액트, 한 명은 선수"는 **그날의 역할 분담이지 계정 속성이 아니다.** 보안 규칙도 `recorders`에 포함되면 무엇이든 쓸 수 있게 둔다.
> 화면에서 "액트 입력 보기 / 선수 입력 보기"를 각자 고르되, 이는 **권한이 아니라 보기 설정**이므로 언제든 바꿀 수 있다. (둘 다 액트를 찍어 같은 플레이가 중복되는 것은 화면에서 막는다 — 선수 입력 보기에서는 액트 버튼을 감춘다.)

`recorderIds` 배열을 함께 두는 이유는 **기록자 대시보드**("내가 기록한 경기") 때문이다. Firestore는 map의 키를 조건으로 걸 수 없으므로, `array-contains`로 찾을 수 있는 배열이 별도로 필요하다. 보안 규칙은 map으로 검사한다(`uid in resource.data.recorders`).

**충돌하지 않는다.** 둘이 같은 레코드의 **다른 필드**를 건드리기 때문이다 — Firestore는 필드 단위로 병합한다. 홈/원정 기록자가 `score.home`/`score.away`를 각각 쓰는 것과 같은 원리다.

```
A → 레코드 생성   { act, res, area, posX, posY, half, halfSeconds }
B → 같은 문서 갱신 { playerId }        ← 필드 단위 merge
```

**누가 무엇을 했는지는 세션이 아니라 레코드에 남긴다.** 역할을 미리 정하지 않아도 실제 사실이 기록되고, 중간에 역할을 바꿔도 그대로 남는다.

```ts
records/{id}: {
  createdBy: uid,           // 이 레코드(액트·위치)를 만든 사람
  playerIdBy: uid | null,   // 선수를 넣은 사람
  playerIdSource: 'did' | 'vision',
}
```

**B의 화면은 A와 다르다** — "선수 입력 버튼이 뜬 레코드 목록"만 보면 된다. A가 찍는 대로 목록이 차오르고 B는 등번호만 찍는다. `records` 구독 하나로 성립하므로 별도 구조가 필요 없다.

> **두 모드 모두 가능하다.** 레코드가 두 모드 다 즉시 서버로 가므로(§11.1) 실시간·분석 어느 쪽이든 서로의 입력이 보인다.

---

## 7. `…/records/{recordId}` — 플레이 1건

**`app/utils/didLogic.ts`의 `DidRecord`가 곧 문서 스키마다.**

| 필드 | 레거시 | 비고 |
|---|---|---|
| (문서 ID) | `gr_id` | Firestore 자동 ID (§7.2) |
| `half` | `gr_half` | `'H1'\|'H2'\|'H3'\|'H4'`. **반드시 저장** (§6.1) |
| `halfSeconds` | `gr_half_seconds` | 해당 반 기준 경과초. 4초 룰의 입력값 |
| `seq` | `gr_regdt` 대용 | 같은 초 안의 순서 (§7.2) |
| `act` | `gr_act_code` | `ActCode` |
| `res` | `gr_res_code` | `ResCode`. `GB`/`GX`/`GOAL` 등 신 코드 체계 |
| `area` | `gr_area_code` | 1~18 구역 |
| `posX` / `posY` | `gr_pos_x/y` | 0~100%. **레거시는 픽셀값이다 — 변환 필요(§16)** |
| `shootPosX` / `shootPosY` | `gr_shoot_pos_x/y` | 골대 타깃 좌표 |
| `shootDspRange` | — | 신규. 골포스트 1m 판정 캐시 |
| `isShot` | — | 신규. write-back으로 `act`가 비워져도 원래 슛이었음을 보존 |
| `playerId` | `p_id` | **`'OWN'` = 자책골** (레거시 실데이터에서 확인, §14) |
| **`createdBy`** | — | **이 레코드(액트·위치)를 만든 사람의 uid** |
| **`playerIdBy`** | — | **선수를 넣은 사람의 uid.** 2인 기록에서 A≠B가 된다 (§6.3) |
| **`source`** | — | **`'did' \| 'vision'`. 지금은 전부 `'did'`** |
| **`playerIdSource`** | — | **`'did' \| 'vision'`. 선수를 누가 넣었나** |
| `createdAt` | `gr_regdt` | |

### 7.1 저장하지 않는 것 — `gt_id`와 `gr_is_*` 14개

레거시는 레코드마다 소속 루트(`gt_id`)와 판정 플래그 14개를 저장했다. **여기서는 전부 뺀다.**

- 파생값이다. 1,200건 전체 재계산이 **0.16ms**다(§14 실측)
- 레코드 하나를 수정하면 앞뒤 루트의 플래그가 연쇄로 바뀐다. 시간 1건을 고치면 **4건 × 14개 = 56개 값이 바뀐다**. 수정 기능이 이미 있으므로(`DidInput.vue:157`) 저장하면 반드시 어긋난다
- 레거시가 저장한 이유(계산이 서버에만 있었음 / SQL 집계가 필요했음 / 단말이 약했음)는 **셋 다 사라졌다**

> **단, `gt_id`는 JaionX가 요구한다** — `TACTIC.xlsx`에 컬럼이 있다. 저장하지 않되 **export 시점에 `computeAttackPaths()`의 `path.gtId`를 채워 내보낸다.** 확정 스냅샷의 `paths`(§8)가 그 자리다.

### 7.2 정렬키 — 지금 코드의 ID 충돌을 막는다

> ⚠️ **현재 코드 버그**: `didLogic.ts:121`의 `let recSeq = 0`은 모듈 전역 카운터다. 새로고침하면 0으로 돌아가므로, 저장된 기록을 이어서 입력하면 `rec_1`이 두 번 생기고 `seq`도 중복된다.

- 문서 ID는 Firestore 자동 ID (`rec_N` 폐기)
- `seq`는 세션 로드 시 `recordings.maxSeq`에서 이어받는다 (레코드마다 increment 트랜잭션을 돌리지 않는다 — 쓰기가 두 배가 된다)
- 정렬은 `orderBy('half').orderBy('halfSeconds').orderBy('seq')`. `seq` 단독 정렬은 불가 — 수정으로 시간을 바꾸면 입력 순서와 시간 순서가 어긋난다

### 7.3 자책골 처리 — `playerId = 'OWN'`

레거시 실데이터에 `p_id = 'OWN'`이 그대로 들어 있다. `didLogic.ts`는 이미 이를 처리하지만 **한 곳이 빠져 있다** — §14.3.

집계 규칙(레거시 D-MST와 대조해 확정):

```
GOAL  = 자책골 포함
SHOT  = 자책골 제외
SSR   = (GOAL − OG) / SHOT
OG    = 자책골 수 (별도 컬럼)
```

---

## 8. 파생 데이터는 언제 저장하나

| 시점 | 동작 |
|---|---|
| 기록 중 (`H1`~`H2_done`) | **저장하지 않는다.** 화면은 `records`로 즉석 계산 |
| 확정 (`status: 'final'`) | `paths` / `bapEvents` / `playerStats` / `kpi`를 **한 번에 배치 write** |
| 확정 후 레코드 수정 | 스냅샷 무효화 → 재계산 → 덮어쓰기 (`kpiComputedAt` 갱신) |

변동기와 확정기의 요구가 반대이기 때문이다. 기록 중에는 매 탭마다 값이 바뀌므로 저장이 곧 부채고, 확정 후에는 리포트·동기화가 "그때 값"을 안정적으로 요구한다.

`AttackPath.recordIds`가 레코드를 참조하므로, 삭제 가능한 기록 중에 저장하면 끊어진 참조가 남는 것도 이유다.

> **스냅샷 필드 목록의 기준은 화면이 아니라 JaionX다.** 이전 판에서 "화면에 보이는 8개 중심"이라고 쓴 것은 잘못이었다. §9의 컬럼 전체를 담는다.

---

## 9. 출력 규격 — JaionX가 받는 것

**이것이 규칙 0의 실체다.** 규격의 원본은 `footballx` repo에 있다.

| 대상 | 규격 위치 | 성격 |
|---|---|---|
| `dMST` | `footballx/app/pages/admin/dMST.vue` `tableHeaders` | 팀-경기 단위. "전 시스템의 중심 허브" |
| `playerMST` | `footballx/app/pages/admin/playerMST.vue` | 선수-경기 단위. 레거시 `gp_*` 거의 그대로 |
| `multiSheet` | `footballx/app/pages/admin/multiSheet/index.vue` | 헤더 고정 아님. 업로드 행의 키에서 동적 생성 |
| `TACTIC` | 레코드 원본 | `gm_id, p_id, t_code, gr_half, gr_half_seconds, gr_seconds, gr_area_code, gr_act_code, gr_res_code, gr_pos_x/y, gr_shoot_pos_x/y, gt_id, PlSeason` |

### 9.1 ★ 이미 Firestore다 — CSV는 껍데기다

`footballx/app/composables/dMST.ts`가 `collection(db, 'dMST')`에 `addDoc` 한다. `multiSheet.ts`, `playerMST.ts`도 같다.

> **즉 "CSV를 admin에 올린다"는 결국 Firestore에 문서를 쓰는 것이다. 우리는 CSV를 만들 필요가 없다.**

단, **프로젝트가 둘이다.**

| | 프로젝트 ID |
|---|---|
| didNew | `jpd-did` |
| footballX / JaionX | `aifootballx` |

클라이언트끼리는 서로 쓸 수 없다. **반드시 서버(파이썬)를 거친다.** 이는 대외비 격리와도 맞아떨어진다.

### 9.2 `dMST` 컬럼 중 우리 몫

| 구분 | 컬럼 | 담당 |
|---|---|---|
| **DID 산출 KPI** | `gi_tmp`(=TAP), `DAP`, `TTP`, `DTP`, `BAP`, `B`, `M`, `A`, `S`, `SHOT`, `ASR`, `SSR`, `GOAL`, `OG` | **우리** |
| **경기 메타** | `gm_round`, `gm_date`, `t_code`, `t_Nm`, `H-A` | **우리** |
| 채번/식별 | `Season`, `S-Round`, `M-Code`, `M_No1/2`, `Match`, `T-code`, `T-Round`, `IDX_CODE`, `PlSeason` | 규칙은 파악됨. 대외비 서버에 구현 |
| AI 예측 | `X-*`, `*(Ai*)` | 파이썬 분석 쪽 |
| 스코어링 | `JMX`, `APX`, `TPX`, `FPX`, `*Score`, `Power3X`, `G-*`, `W-TOT`, `Point`, `PDF` | **대외비 서버** |

`B/M/A/S`는 빌더/메이커/어시스터/슈터 수 = `gr_is_ctb/ctm/cta/cts` 집계다. **저장하지 않아도 `computeAttackPaths()`로 산출된다는 것을 실데이터로 검증했다**(§14).
`H-A`는 `gi_write_code` 그대로 = **우리 `recordings` 문서 ID**.

### 9.3 팀 JMX는 5분 구간 시계열에서 나온다

경기 전체 집계가 아니라 **전·후반 5분 구간 20칸의 누적값**을 정규화한 뒤 평균내어 산출한다. 그래서 §6.1의 `half`/`halfSeconds`가 필수다.

### 9.4 대외비 — KPI가 쌓이는 곳과 평점이 쌓이는 곳을 분리한다

선수평점(`P-JMX`, `P-APX/TPX/FPX`, 등급)과 팀지수(`JMX`, `APX/TPX/FPX`, `Power3X`) 산출식, 기준값·가중치·채번 규칙은 **대외비**다. 이 repo와 클라이언트 번들에 **넣지 않는다.**

```
① KPI 원장                          ② 평점 저장소
   프로젝트: jpd-did                   프로젝트: jpd-rating (별도)
   ─────────────────                  ─────────────────
   records                            ratings/{matchId}/{H|A}/{playerId}
   recordings.kpi                       → P-JMX, P-APX/TPX/FPX, G-*
   playerStats/{playerId}             teamRatings/{matchId}/{H|A}
     → DAP·BAP·DTP·SHOT·SSR·GOAL        → JMX, APX/TPX/FPX, Power3X
     → B·M·A·S                        ratingBaseline/{version}
                                         → 기준값·가중치 (대외비)
   접근: 개발자 포함                    접근: 1~2명
        │                                    │
        └──── KPI 읽기 (한 방향) ───────────►│
                                             │ 여기서만 합친다
                                             ▼
                                aifootballx: dMST / playerMST
```

**규칙 셋**

1. **평점 서비스만 KPI를 읽는다. 반대 방향은 없다.** KPI 쪽에서는 평점이 존재하는지도 모른다
2. **aifootballx 쓰기 권한도 평점 서비스만 가진다.** `dMST` 한 행에 KPI 컬럼과 평점 컬럼이 섞여 있어(`gi_tmp, DAP … JMX, APX, TPX`) 어차피 마지막에 합쳐야 하므로, 합치는 지점을 평점 쪽에 두면 **서비스 계정이 하나로 줄어든다**
3. **기준값(`ratingBaseline`)도 평점 저장소에만 둔다.** KPI 프로젝트를 전부 뒤져도 `DAP 65, BAP 7, SHOT 3` 같은 숫자만 있고 점수는 없다

접근 통제는 두 층이다 — **실행** 접근(누가 호출하나)과 **소스** 접근(누가 코드를 읽나). 후자가 본질이며, 배포된 소스는 프로젝트 IAM 권한자가 내려받을 수 있으므로 **프로젝트 분리**가 가장 확실하다.

#### 무엇이 지켜지고 무엇이 안 지켜지나 — 솔직한 평가

JaionX는 선수별 **입력(`P-DAP`, `P-BAP` …)과 출력(`P-JMX`, 등급)을 같은 표에 함께** 보여준다. 따라서 **작정하고 회귀분석을 하면 공식은 복원된다.** 공식이 6개 입력의 선형 조합 + 상한 클램프라 어렵지도 않다.

그러므로 **API 호출 제한·역산 방지 장치는 만들지 않는다.** 이미 화면에 있는 것을 API로 막는 셈이라 실익 없이 복잡도만 늘린다.

분리로 실제로 얻는 것은 이것뿐이며, **코드를 어디 두느냐의 문제라 비용이 0이므로** 그대로 한다:

| 지켜지는 것 | 내용 |
|---|---|
| **복사 경로 차단** | 권한 없는 개발자에게 "소스를 그대로 가져가는" 길이 없다. 남는 건 회귀분석뿐이고 그건 별개의 일이다 |
| **정확도와 시간** | 회귀는 근사치다. `0.99` 클램프, `99.9` 상한, `X-GOAL`의 계단식(0→50, 1→75, 2+→99.9), 등급 경계 같은 예외 처리는 잘 나오지 않는다 |
| **비공개 영역** | 채번 규칙과 중간값(`KPI-ALL`, `Conversion`, 기본점수, 골 보너스)은 화면에 없다 |
| **영업비밀 지위** | 비밀로 관리한 사실이 있어야 비밀이다 |

> **기준값을 사람이 조정하는 것 자체가 가장 강한 방어다.** 회귀로 과거 값은 맞출 수 있어도, 다음에 어떤 값으로 바꿀지는 규칙이 아니라 판단이라 예측할 수 없다. 반대로 **"기준값 = 시즌 평균"으로 자동화하면 그것도 규칙이 되어 복원 가능해진다** — 평균을 낼 재료가 JaionX에 이미 공개돼 있기 때문이다. 자동화는 보안이 아니라 **운영 편의**를 근거로 판단할 것(§15).

#### `ratingBaseline` — 기준값은 반드시 버전으로 저장한다

```ts
ratingBaseline/{version}   → { player: {DAP:30, BAP:8, DTP:6, SHOT:6, SSR:0.33, GOAL:2},
                               team:   {DAP:400, BAP:60, DTP:12, SHOT:15, SSR:0.18, GOAL:3},
                               weights: {...}, effectiveFrom, createdBy }

ratings/{...}/{playerId}   → { pJmx: 11.65, ..., baselineVersion: 'v3' }
```

기준값은 데이터를 보며 자주 바뀐다. **어떤 기준값으로 계산된 평점인지 같이 남기지 않으면, 기준값을 바꾸는 순간 과거 평점이 전부 흔들리고 왜 달라졌는지 설명할 수 없다.** 지금은 시즌별 엑셀 파일이 따로 있어 우연히 보존되고 있으나, 자동화하면 그 우연이 사라진다.

### 9.5 `playerStats`가 채우는 것

`playerMST`의 `gp_*` 컬럼이 그대로 목록이다: `gp_tmp, gp_tap, gp_utp, gp_ctp, gp_ttp, gp_sht, gp_ast, gp_goal, gp_ctb, gp_ctm, gp_cta, gp_cts, gp_gtb, gp_gtm, gp_asr, gp_ssr` 등.

단 `gp_score_rel / gp_score_abs / gp_score`(평점) 3개는 **대외비 서버만 채운다.**

---

## 10. 데이터 관리 — 이적시장 · 팀 정보

경기 기록이 아니라 **관리 화면이 쓰는 참조 데이터**다. 입력·수정 대상이므로 이력과 감사 필드가 필요하다.

### 10.1 `players/{playerId}` — 정체성만

| 필드 | 레거시 |
|---|---|
| `name` / `nameEn` / `nameFull` | `p_name`, `p_name_en`, `p_name_full` |
| `birth` / `height` / `foot` / `nation` | `p_birth`, `p_height`, `p_foot`, `p_nation` |
| `active` | — (은퇴/삭제 대신 비활성. 과거 기록을 고아로 만들지 않는다) |
| `currentTeamId` / `currentNo` / `currentPos` | — (**파생 캐시**) |
| `legacyPlayerId` | `p_id_old` |

어느 팀 소속인지는 시간에 따라 변하므로 여기 두지 않는다.

### 10.2 `players/{playerId}/contracts/{contractId}` — 이적시장 원장 ★

레거시 `ff_player_team`이 이미 시간축 테이블이다. 이를 **추가 전용 원장**으로 만든다.

| 필드 | 레거시 | 비고 |
|---|---|---|
| `teamId` | `t_code` | |
| `leagueId` / `seasonId` | `l_code` | |
| `from` / `to` | `pt_begin`, `pt_end` | `to = null`이면 현재 소속 |
| `no` / `pos` | `pt_num`, `pt_pos` | 계약에 종속 (팀 옮기면 바뀐다) |
| `fromTeamId` | — | 신규. 직전 소속팀 → 한 문서로 "A→B" 완성 |
| `transferType` | — | 신규. `TRANSFER/LOAN/LOAN_RETURN/FREE/YOUTH/RETIRE` |
| `fee` / `currency` | — | 신규 (선택) |

**이적 한 건 = 트랜잭션 하나**: ①이전 계약 `to` 닫기 ②새 계약 생성 ③`players.current*` 갱신 ④양 팀 `squad` 갱신. 부분 실패하면 스쿼드가 어긋난다.

이적 목록은 `collectionGroup('contracts').orderBy('from','desc')` 한 방이다. 별도 `transfers` 컬렉션을 두지 않는 이유 — **계약 문서가 곧 이적 이벤트다.**

### 10.3 `teams/{teamId}/squad/{playerId}` — 현재 스쿼드 (파생 뷰)

```ts
{ name, no, pos, contractId, since }
```

원장만 있으면 계약 30건 + 선수 30명 = **61 read + 조인**이 필요하다. 이 뷰가 있으면 **1 query**로 끝난다. 갱신은 이적 시에만(연 수십 회)이라 어긋날 위험이 낮다 — 원칙 3의 정당한 예외.

현재 `app/utils/squads.ts`의 하드코딩(`HOME_SQUAD`/`AWAY_SQUAD`)이 이 자리를 임시로 메우고 있다.

### 10.4 `teams/{teamId}`

`t_name*`, `u_text_color_code`, `s_code`, `t_coach*`, `t_begin/end` 등을 옮기고 `crestUrl`을 추가한다.

**`l_code`를 팀 문서에 단일값으로 두면 승강제를 표현할 수 없다.** 강등되면 작년 리그가 덮어써진다.

```
teams/{teamId}/seasons/{seasonId}  →  { leagueId, division, finalRank? }
```

`currentLeagueId`는 목록 필터용 캐시로만 유지한다.

### 10.5 `leagues` / `seasons` / `stadiums` / `recorders`

| 컬렉션 | 레거시 | 필드 |
|---|---|---|
| `leagues/{id}` | `ff_league` | `name`, `nameEn`, `country` |
| `seasons/{id}` | `ff_season` | `leagueId`, `name`, `from`, `to`, `alias` |
| `stadiums/{id}` | `ff_stadium` | `name`, `nameKr`, `seats`, `country`, `city`, `homeTeamId`, `surface` |
| `recorders/{uid}` | `ff_user` | `name`, `teamId`, `role`(`recorder`/`admin`), **`level`(`basic`/`advanced`)** — 갱신 버튼 구성을 가른다(§11.2.1), `handedness` |

`u_pw`는 옮기지 않는다 — 비밀번호는 Auth가 관리한다.

### 10.6 공통 감사 필드

`teams / players / contracts / stadiums / leagues / seasons` 전부: `createdAt, createdBy, updatedAt, updatedBy`.
경기 기록(`records`)에는 붙이지 않는다 — 세션이 이미 `recorders`로 귀속되고, 선수 입력 주체는 `playerIdSource`에 남는다.

---

## 11. 쓰기·조회 전략

### 11.1 레코드는 두 모드 모두 즉시 저장한다

| 방식 | 판단 |
|---|---|
| 메모리에 모았다가 반 종료 시 flush | ❌ **지금 겪는 문제 그대로.** 새로고침 한 번에 반 전체가 날아간다 |
| **탭할 때마다 즉시 write** | ✅ 채택 (두 모드 공통) |

핵심은 Firestore의 **오프라인 지속성**이다. `persistentLocalCache`를 켜면 쓰기가 먼저 IndexedDB에 떨어지고 UI는 즉시 반영되며, 네트워크가 돌아올 때 자동 동기화된다. **경기장 와이파이가 끊겨도 입력이 멈추지 않는다.**

### 11.2 모드가 가르는 것은 **집계·전송 트리거**다

비용이 나는 지점은 레코드 쓰기가 아니다.

| 무엇 | 경기당 |
|---|---|
| 레코드 1,200건 Firestore 쓰기 | **약 3원** |
| 파이썬 집계 실행 + JaionX 갱신 | **실행 횟수에 비례 — 여기가 실제 비용** |

```
분석 모드    레코드 → Firestore 즉시 저장
                     └ 집계·JaionX 전송은 [전송/갱신] 누를 때만

실시간 모드  레코드 → Firestore 즉시 저장
                     └ 집계·JaionX 전송도 계속 (5~10초 묶음 권장)
```

`useMatchState`는 버리지 않고 **Firestore 구독의 로컬 캐시**로 역할만 바꾼다.

> **오해하지 말 것 — 오프라인은 평상시 동작 방식이 아니다.** 기록은 보통 인터넷이 되는 곳에서 한다. 오프라인 지속성은 **끊겼을 때 입력이 멈추지 않게 하는 안전장치**이며, 품질을 위한 것이지 운영 전제가 아니다. 따라서 **네트워크를 일부러 끊어두지 않는다**(`disableNetwork`를 쓰지 않는다). 그래야 §6.3의 2인 기록이 두 모드 모두에서 성립한다.

### 11.2.1 갱신 트리거 — 계산은 오직 여기서만 일어난다

> **원칙: 레코드가 쌓이는 것과 계산이 도는 것은 별개다.**
> 레코드는 계속 서버에 쌓이지만, **판정·KPI·평점은 갱신 트리거 없이는 절대 돌지 않는다.**

이유는 셋이다.

- 자동으로 계속 돌면 무겁고, 파이썬 실행·JaionX 쓰기에 비용이 붙는다(§11.2)
- 기록 중에는 값이 매 탭 바뀌므로 저장이 곧 부채다(§8)
- **언제 확정됐는지가 명확해야** JaionX 동기화가 재현 가능하다

#### 누가 계산하나

| 단계 | 어디서 | 트리거 없이 도나 |
|---|---|---|
| 화면 KPI 표시 | 클라이언트 `didLogic.ts` | 계속 (참고용, **저장 안 함**) |
| 판정 · KPI 집계 · 5분 구간 누적 | **KPI 서비스** (파이썬, `jpd-did`) | ❌ **갱신 때만** |
| 팀 지수 · 선수 평점 · 등급 | **평점 서비스** (`jpd-rating`) | ❌ **갱신 때만** |
| JaionX(`dMST`/`playerMST`) 전송 | 평점 서비스 | ❌ **최종 갱신 때만** |

#### 버튼 세팅

| 버튼 | 활성 조건 | KPI 서비스 | 평점 서비스 | JaionX |
|---|---|---|---|---|
| **전반 데이터 갱신** | `status = H1_done` | 판정 + KPI + **5분 구간 1~10** | **팀 JMX 구간 1~10** | ❌ |
| **후반 데이터 갱신** | `status = H2_done` | 판정 + KPI + **5분 구간 1~20** | **팀 JMX 구간 1~20** | ❌ |
| **최종 데이터 갱신 & 경기 종료** | `status = H2_done` | 전체 확정 집계 | 팀 지수 + **선수 평점** + 등급 | ✅ 전송 |

- 중간 갱신은 **"여기까지 맞게 들어갔나" 확인용**이다. 밖으로 나가는 것은 최종 하나뿐이다
- 최종 갱신을 누르면 `status = 'final'`이 되고 스냅샷이 확정된다(§8)

#### 계정 등급 뱃지

```ts
recorders/{uid}: { …, level: 'basic' | 'advanced' }
```

로그인하면 뱃지가 표시되고, 그에 따라 버튼 구성이 달라진다.

| | basic | advanced |
|---|---|---|
| 전반 데이터 갱신 | 있음 | **없음** |
| 후반 데이터 갱신 | 있음 | **없음** |
| 최종 데이터 갱신 & 경기 종료 | 있음 | 있음 |

숙련자는 중간 확인 없이 끝까지 기록하고 마지막에 한 번만 누른다.

#### 실시간 모드

버튼 대신 **5분 구간이 닫힐 때마다 자동으로 트리거**된다 — KPI + 팀 JMX 구간값이 갱신되고 JaionX까지 반영된다. **선수 평점과 최종 확정은 여전히 "최종 갱신 & 경기 종료"에서만** 일어난다.

> **버튼과 백업은 다른 일이다.** 갱신 버튼은 "계산해서 확정하라"는 뜻이고, 레코드 자체는 버튼과 무관하게 계속 서버에 쌓인다(§11.1). 중간 갱신을 건너뛰어도 기록이 위험해지지 않는다.

### 11.3 ★ 조회 층 — 화면은 Firestore를 직접 나열하지 않는다

> **규칙: 목록·검색·집계를 `getDocs`로 컬렉션을 훑어서 해결하지 않는다.**
> 이 규칙이 없으면 화면을 만들 때마다 전량 로드로 되돌아간다.

**지금 footballX가 겪는 문제가 그것이다.** `footballx/app/composables/dMST.ts`는 조건에 맞는 문서를 **전부 받아서 배열에 쌓는다**:

| 컬렉션 | 시즌당 문서 | 한 번 열 때 |
|---|---|---|
| `dMST` | 380경기 × 2팀 = 760 | 760 read |
| `playerMST` | 11,500 | **11,500 read** + 수 MB 전송 |

Firestore의 구조적 한계 때문에 이렇게 될 수밖에 없다:

- **필드를 골라 받을 수 없다.** 웹 SDK에는 `SELECT col1, col2`가 없다 — 문서를 통째로 받는다
- **검색이 없다.** `LIKE '%손흥민%'`이 안 된다. 앞글자 일치가 전부다. 그래서 **전부 받아서 브라우저에서 필터**하게 된다
- **조인·집계가 없다.** 여러 컬렉션을 엮으려면 클라이언트에서 다시 읽어야 한다

### 세 층으로 나눈다

```
┌─ 쓰기 ────────────┐  ┌─ 목록 · 검색 ────┐  ┌─ 분석 · 추출 ──────┐
│  Firestore        │  │  파이썬 API      │  │  Parquet + DuckDB  │
│  원본 · 실시간     │  │  페이징 · 검색   │  │  SQL 그대로        │
│  트랜잭션         │  │  화면이 호출     │  │  대량 조회 · 리포트 │
└───────────────────┘  └──────────────────┘  └────────────────────┘
        │                       ▲                      ▲
        └──── 파이썬이 읽어서 ──┴──── 주기적으로 떨군다 ┘
```

| | Firestore | 파이썬 API | Parquet + DuckDB |
|---|---|---|---|
| 실시간 쓰기 · 오프라인 | ✅ | | |
| 검색 · 정렬 · 페이징 | ❌ | ✅ | ✅ |
| 집계 · 조인 · SQL | ❌ | | ✅ |

**분석 층은 새로 만드는 것이 아니다.** `footballx/server/backend/data/**/*.parquet`가 이미 그 구조다. 여기에 DuckDB만 얹으면 SQL이 그대로 된다:

```sql
SELECT t_code, AVG(DAP), SUM(GOAL)
FROM 'dMST_*.parquet'
WHERE gm_round BETWEEN 1 AND 10
GROUP BY t_code ORDER BY 2 DESC;
```

11,500행이 밀리초 단위로 처리되고, **Firestore read 비용이 0**이다.

### 화면이 Firestore를 직접 읽어도 되는 경우

세 가지뿐이다.

1. **문서 하나를 주소로 읽을 때** — `matches/{gm_id}/recordings/H` 같은 직접 읽기
2. **실시간 구독이 필요한 기록 화면** — DID 입력 중의 `records`
3. **방금 자기가 쓴 문서를 되읽을 때** — 스냅샷 갱신 전에 화면에 반영하기 위해

### 11.4 관리 화면 — 일정 관리 · 이적시장

**둘 다 입력이 많고 데이터도 많은 화면이다.** 읽기와 쓰기를 다르게 다룬다.

```
[화면]  검색 · 목록 · 이력 조회  ──► 파이썬 API ──► DuckDB 스냅샷
        등록 · 수정             ──► 파이썬 API ──► Firestore 트랜잭션 (원본)
                                                       │
                                                       └─► 스냅샷 갱신
```

| | 왜 이렇게 |
|---|---|
| **쓰기는 Firestore가 원본** | 이적 한 건 = 트랜잭션 4단계(§10.2). 부분 실패하면 스쿼드가 어긋난다 |
| **읽기는 스냅샷으로 충분** | 이적 이력 조회가 몇 초 늦어도 아무 문제 없다 |
| **검색은 파이썬이 한다** | 이름 부분 일치, 팀·기간·이적유형 복합 조건 — Firestore로는 불가능하고 파이썬에선 한 줄이다 |
| **쓰기 직후엔 그 문서만 직접 읽는다** | 스냅샷이 갱신되기 전에도 사용자는 자기가 방금 넣은 것을 본다 |

### 11.5 목록용 요약 문서

목록 화면이 상세 문서를 전부 읽지 않게 한다.

```
matches/{gm_id}          ← 상세 (무겁다)
matchIndex/{seasonId}    ← 목록용. 한 문서에 시즌 전체를 압축
   { items: [{ gm_id, date, round, home, away, score, status }, …] }
```

일정 목록이 **380 read → 1 read**가 된다. 갱신은 경기 상태가 바뀔 때뿐이라 어긋날 위험이 낮다 — 원칙 3의 정당한 예외다(§10.3의 `squad` 뷰와 같은 이유).

---

## 12. 보안 규칙 스케치

```
matches/{m}/recordings/{side}
  read : 로그인 사용자
  write: request.auth.uid in resource.data.recorders   (없으면 생성 가능 — 선점)
  .../records/{r}, subs, cards
  write: 부모 recording 의 recorders 에 포함될 때만
         ※ 한 팀을 둘이 나눠 찍는 경우가 있으므로 단수 recorderId 로 두면 안 된다 (§6.3)

teams, players, players/*/contracts, stadiums, leagues, seasons
  read : 로그인 사용자
  write: recorders/{uid}.role == 'admin'

# aifootballx 프로젝트
dMST, playerMST, multiSheet
  write: 클라이언트 전면 금지. Admin SDK(서버)만
```

마지막 줄이 중요하다 — 대외비 서버가 만든 산출물을 클라이언트가 덮어쓸 수 있으면 안 된다.

---

## 13. 필요한 색인

| 대상 | 색인 |
|---|---|
| 레코드 정렬 | `records`: `half` ASC, `halfSeconds` ASC, `seq` ASC |
| 기록자 대시보드 | collectionGroup `recordings`: `recorderIds` ARRAY_CONTAINS, `createdAt` DESC — 맵은 색인이 안 되므로 uid 목록을 배열 필드로 같이 둔다 |
| 일정 목록 | `matches`: `date` ASC, `leagueId` ASC |
| 이적 목록 | collectionGroup `contracts`: `from` DESC |
| 팀별 계약 조회 | collectionGroup `contracts`: `teamId` ASC, `to` ASC |

---

## 14. 검증 — 레거시 실데이터로 확인한 것 (2026-09-02)

`Admin.zip`의 `TACTIC.xlsx`(레거시 `ff_game_record` 실제 1,536행, 라운드 2, 20개 팀-경기)를 입력으로 **`didLogic.ts`를 수정 없이 그대로 실행**하고, `MST2627-NEW.xlsx`의 `D-MST(2627)` 값과 대조했다.

### 14.1 결과

| 항목 | 결과 |
|---|---|
| `DTP` (슛 포함 공격루트 수) | **20/20 팀 일치** |
| `GOAL` | **20/20 일치** |
| `SHOT` | 자책골 제외 후 **20/20 일치** |
| `B`/`M`/`A`/`S` | 17/20 일치 (3팀에서 1~2 차이) |
| 공격루트 그룹핑 | 382개 전부 레거시 `gt_id` 경계 안. **잘못 합친 것 0건** |

> 우리 4초 룰은 레거시 273루트를 382개로 **더 잘게 쪼갠다.** 쪼개진 조각은 슛이 없어 `UPP`(무효)가 되므로 DTP 개수에는 영향이 없었다. (파일에 슛 루트만 들어 있어 생기는 현상이기도 하다.)

### 14.2 성능 실측

`computeAttackPaths` + `computeBap` **전체 재계산** 1회:

| 레코드 | 시간 |
|---|---|
| 300건 | 0.11 ms |
| 600건 | 0.15 ms |
| **1,200건 (한 경기)** | **0.16 ms** |

한 프레임(16ms) 안에 100번 돌려도 남는다. **원칙 3("계산되는 값은 저장하지 않는다")의 근거다.**

### 14.3 ★ 발견한 버그 — 자책골이 SHOT에 포함된다

`didLogic.ts:415`의 공통 플래그 루프가 자책골 보정을 빠뜨렸다:

```ts
for (const r of chain) {
  const { act, res } = r          // ← 원본 act. isOwnGoal 보정 없음
  f.isTap = !!act
  f.isSht = isShotAct(act) || !!r.isShot
```

1차 패스(`:333`)와 2차 패스(`:380`)는 `const act = isOwnGoal ? '' : r.act`로 보정하는데 이 루프만 빠졌다. 파일 상단 주석은 "자살골은 TAP, DAP, DTP, Shoot 집계에서 제외"라고 명시하고 있다.

자책골을 제외하고 다시 돌리니 SHOT이 **3팀 불일치 → 20/20 일치**로 바뀌었다.

### 14.4 남은 불일치

| 팀 | 차이 | 추정 |
|---|---|---|
| BHAX | S 15/14 | `H2 2926s H/O` — **결과가 안 찍힌 슛**. 우리는 `closeTrailing`으로 마감해 슈터를 부여, 레거시는 미부여 |
| LIVX | A 11/10, S 13/12 | 미확정 |
| TOTX | B 19/17, M 6/5, A 11/10, S 16/15 | 루트 하나(`roles=BBMAS`)가 통째로 빠진 것과 정확히 일치. 이유 미확정 |

**규명하려면 `gr_is_cts/cta/ctm/ctb`가 포함된 export가 필요하다.** 현재 파일에는 없어 총합만 비교 가능하다.

### 14.5 아직 검증되지 않은 것

`TACTIC.xlsx`에는 **슛이 포함된 루트만** 들어 있다(273루트 전부에 슛 존재, 슛 없는 루트 0개). 따라서 다음은 **한 번도 실데이터로 검증된 적이 없다**:

- `gi_tmp` (TAP)
- **`DAP`**
- `TTP`
- **`BAP`**
- `ASR`

전체 레코드가 포함된 export가 필요하다.

---

## 15. 레거시 대비 정리

### 사라지는 것

| 레거시 | 왜 |
|---|---|
| `gi_id` (세션 키) | 경로가 키를 대신한다. 매핑용 `legacyGiId`만 남긴다 |
| 레코드마다의 `gi_id` (1,200회 중복) | 서브컬렉션 경로가 소속을 표현한다 |
| `gr_is_*` 14개 + `gt_id` | 파생값. 0.16ms에 재계산된다 |
| `dplay_game_path_reset` 배치 재계산 | 저장을 안 하므로 재계산 대상이 없다 |
| `ff_game_state` | `recordings.status` 수정으로 대체 |
| `ff_session`, `ff_member_session` | Firebase Auth |
| `ff_formation` | 상수 |
| `u_pw` | Auth가 관리 |
| PHP + MySQL 서버 운영 | 관리형 서비스 |
| **엑셀 수작업 6단계** | 파이썬 자동 변환 (§0) |

### 없어지는 엑셀 편법

| 지금 | 문제 | 새 구조 |
|---|---|---|
| `IF(MOD(Match,2)=1, 다음 행, 이전 행)` | **상대팀을 인접 행으로 찾는다.** 정렬이 깨지면 승점이 조용히 틀어짐 | `matches`의 반대편 `recordings`를 읽는다 |
| `IF(윗 행이 비었으면 "H" else "A")` | 행 위치로 홈/원정 판정 | **문서 ID가 곧 H/A** |
| `Point = W-TOT2 − W-TOT` | 베이스 숫자를 더했다 빼는 우회 | 골 비교로 직접 산출 |
| `'C:\EPL2425\[footballX-24R.xlsx]P-KEY'!J4` | 라운드마다 사람이 셀 링크 수정 | 불필요 |

### 개선되는 것

| | 지금 | 새 구조 |
|---|---|---|
| 탭당 네트워크 | HTTP 왕복 1회 + **응답 대기** | 0회 (로컬 우선, 백그라운드 동기화) |
| 탭당 SQL | 2~4개 | — |
| 오프라인 | 불가 (입력 중단) | 가능 |
| 새로고침 | — | 살아남음 |
| 파생값 정합성 | 저장 + 배치로 맞춤 | 저장 안 함 = 어긋날 자리 없음 |
| JaionX 반영 | 사람이 6단계 | 자동 |
| 대외비 로직 | 엑셀 파일로 유통 | 서버에 격리, 접근자 1~2명 |

### 문제였던 것 (현재 코드)

| # | 문제 | 해결 |
|---|---|---|
| 1 | 새로고침하면 기록 유실 | 즉시 저장 + 오프라인 지속성 (§11) |
| 2 | 후반 `seconds`가 0부터 재시작 → 전·후반 레코드가 섞이고 4초 룰이 반 경계를 넘음 | `half` 명시 저장 (§6.1) |
| 3 | `recSeq` 모듈 카운터 → 새로고침 후 ID·seq 중복 | 자동 ID + `maxSeq` 시드 (§7.2) |
| 4 | 원정팀 기록 중에도 `homeScore++` | `side` 기준 merge (§5.1) |
| 5 | 파생 플래그 저장 시 수정마다 어긋남 | 미저장, 확정 시 스냅샷 (§7.1, §8) |
| 6 | 이적하면 과거 경기의 선수 이름·번호가 바뀜 | 라인업 스냅샷 (§6.2) |
| 7 | 스쿼드가 `squads.ts`에 하드코딩 | 계약 원장 + 스쿼드 뷰 (§10.2, §10.3) |
| 8 | **자책골이 SHOT에 포함됨** | `didLogic.ts:415` 보정 (§14.3) |

---

## 16. 아직 결정하지 않은 것

- **기준값을 고정할 것인가, 시즌 평균으로 자동 갱신할 것인가** — 고정하면 과거 평점이 안 변해 **재현 가능**하다. 자동 갱신하면 리그 수준 변화를 따라가지만 **어제 11.65였던 평점이 오늘 11.4가 된다.** 지금 엑셀은 사실상 고정(값이 박혀 있음). 어느 쪽이든 `ratingBaseline` 버전 스탬프가 있으면 과거 복원은 가능하다(§9.4)
- **채번 규칙 구현 위치** — `Season / S-Round / M-Code / Match / T-Round / IDX_CODE`. 규칙은 파악됐으나 `jpd-rating`에 둘지 일반 파이썬에 둘지 미정
- **좌표 변환** — 레거시 `gr_pos_x/y`는 픽셀(예: 327, 598), 우리는 0~100%. **기준 해상도 확인 필요**
- **`D-MST` 실제 업로드 열** — 계산 시트는 221열, admin 화면은 60여 열. 어디까지가 업로드 대상인지 확인 필요
- **`BAP`가 팀 지수 계산에 쓰이지 않는 것이 의도인지** — `BAP(AiBuildup)` 열에 수식이 없고 JMX 평균에서도 빠져 있다. 기준값(60)만 남아 있음
- **비전 데이터 구조** — 별도 컬렉션 형태, 좌표 형식, 타임스탬프 정합. **지금은 `source` / `playerIdSource` 분기만 남기고 설계는 보류**
- **연장전(H3/H4)** — 스키마는 열어뒀으나 UI는 전·후반만
- **확정 후 수정 시 재계산 트리거** — 클라이언트 즉시 재계산 vs 파이썬 위임
- **`gp_score_rel` / `gp_score_abs`** — 대외비 평점 외 나머지 두 지표의 산출 근거

---

## 17. 참고

- 레거시 전체 필드: `/Users/taemin/Documents/Aimbroad_database_schema-240812.numbers`의 `전체주요목록` 시트
- 판정 로직 규칙: `docs/03_kpi_terminology.md` + `app/utils/didLogic.ts` 주석 — **이 문서는 저장 스키마만 다룬다**
- 출력 규격: `footballx/app/pages/admin/{dMST,playerMST,multiSheet}` + `composables/{dMST,playerMST,multiSheet}.ts`
- 대외비 산출식: repo 밖 별도 관리 (§9.4)
