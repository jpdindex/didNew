# 레거시 컬럼 → Firestore 필드 매핑표

레거시 19개 표 240개 컬럼 전부에 대해 **어디로 갔는지 / 왜 사라졌는지 / 무엇이 새로 생겼는지**를 한 줄씩 적는다.
`docs/04_firestore_schema.md`가 "왜 그렇게 설계했나"라면, 이 문서는 **"그래서 이 컬럼은 어떻게 됐나"**에만 답한다.

원본: `Aimbroad_database_schema-240812.xlsx` `전체주요목록` 시트
대상: `app/types/schema.ts`

## 상태 표기

| 표기 | 뜻 |
|---|---|
| **유지** | 이름 그대로 옮김 |
| **이름변경** | 값은 같고 이름만 바뀜 (접두사 제거 / 신용어) |
| **삭제(계산)** | 저장 안 함. 필요할 때 `records`에서 계산 (원칙 3) |
| **삭제(불필요)** | 새 구조에서 존재 이유가 없어짐 |
| **삭제(경로)** | Firestore 경로가 그 역할을 대신함 |
| **신규** | 레거시에 없던 것 |
| ⚠️ **미결정** | 아직 갈 곳이 정해지지 않음 — 결정 필요 |

---

# ① 입력 데이터 스키마

## ff_game (16) → `MatchDoc` — `matches/{gm_id}`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gm_id` | (문서 ID) | **이름변경** — 문서 ID가 곧 `gm_id` |
| `gm_date` | `date` | 이름변경 |
| `gm_time` | `kickoffTime` | 이름변경 |
| `gm_h_t_code` | `homeTeamId` | 이름변경 |
| `gm_a_t_code` | `awayTeamId` | 이름변경 |
| `gm_league` | `leagueId` | 이름변경 |
| `gm_round` | `round` | 이름변경 |
| `gm_s_code` | `stadiumId` | 이름변경 |
| `gi_goal_home` | `score.home` | 이름변경 |
| `gi_goal_away` | `score.away` | 이름변경 |
| `gm_state` | — | **삭제(불필요)** — `recordings.status`가 대신함 |
| `gm_sub_league` | — | ⚠️ **미결정** — 서브리그 개념을 쓸지 안 쓸지 |
| `is_realtime` | `recordings.inputMode` | **이름변경 + 이동** — 경기 속성이 아니라 기록자별 설정 |
| `is_old` | — | 삭제(불필요) — 마이그레이션 플래그 |
| `is_view` | — | 삭제(불필요) |
| `is_onair` | — | 삭제(불필요) — BID 방송용 |
| — | `seasonId` | **신규** — 단 `gm_id` 앞 8자리와 중복. 질의 편의용 |
| — | `createdAt` / `updatedAt` | 신규 — 레거시 `ff_game` 에는 없음. 관례 |

> `sRound` 는 `matches` 가 아니라 **`recordings`** 로 간다 — "팀 하나 × 경기 하나" 단위이기 때문이다. 아래 참고.

## ff_game_info (49) → `RecordingDoc` — `recordings/{H|A}`

### 식별·상태

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gi_id` | `legacyGiId` | **삭제(경로) + 매핑용 보존** — 키로는 불필요, 동기화용으로만 |
| `gm_id` | — | **삭제(경로)** — 부모 경로가 이미 가리킴 |
| `gi_write_code` | (문서 ID) + `side` | 이름변경 — 문서 ID가 `H`/`A` |
| `gi_user_id` | `recorders` / `recorderIds` | **이름변경 + 확장** — 단수 → 복수(2인 기록) |
| `gi_h_t_code` | `teamId` | 이름변경 |
| `gi_a_t_code` | `opponentTeamId` | 이름변경 |
| `gi_state` | `status` | **이름변경** — `000/H1B/H1E/…` → `ready/H1/H1_done/…` |
| `gi_part` | `fieldSide` | 이름변경 |
| `gi_part_ex` | — | ⚠️ **미결정** — 연장전 진영. H3/H4 UI 미구현 |
| `gi_formation` | `formationKey` | 이름변경 |
| `gi_regdt` / `gi_moddt` | `createdAt` / `updatedAt` | 이름변경 |
| `is_old` | — | 삭제(불필요) |

### 시간

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gi_h1_begin`~`gi_h4_begin` | `halves.H1~H4.startedAt` | **이름변경 + 구조화** — 4개 컬럼 → 맵 |
| `gi_h1_seconds`~`gi_h4_seconds` | `halves.H1~H4.seconds` | 이름변경 + 구조화 |
| `gi_seconds` | — | **삭제(계산)** — `halves` 합계 |

### KPI (확정 시점에만 `kpi` 맵으로 저장)

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gi_tmp` | `kpi.TAP` | 이름변경(신용어) |
| `gi_tap` | `kpi.DAP` | 이름변경(신용어) |
| `gi_ctp` | `kpi.DTP` | 이름변경(신용어) |
| `gi_ttp` | `kpi.TTP` | 유지 |
| `gi_sht` | `kpi.SHOT` | 이름변경 |
| `gi_gol` | `kpi.GOAL` | 이름변경 |
| `gi_bap` | `kpi.BAP` | 유지 |
| `gi_ctb` | `kpi.B` | 이름변경(신용어 DTB) |
| `gi_ctm` | `kpi.M` | 이름변경(신용어 DTM) |
| `gi_cta` | `kpi.A` | 이름변경(신용어 DTA) |
| `gi_cts` | `kpi.S` | 이름변경(신용어 DTS) |
| `gi_asr` | `kpi.ASR` | 유지 |
| `gi_ssr` | `kpi.SSR` | 유지 — 단 산식은 `(GOAL − OG)/SHOT` |
| — | `kpi.OG` | **신규** — 자책골 수 (D-MST 요구) |
| `gi_utp` | — | **삭제(계산)** — `paths`에서 셈 |
| `gi_stp` | — | 삭제(계산) |
| `gi_csp` | — | 삭제(계산) — 신용어 DSP |
| `gi_gtb` / `gi_gtm` | — | 삭제(계산) |
| `gi_gsr` | — | 삭제(계산) — `GOAL/DAP` |
| `gi_tmp_sc` `gi_tmp_sr` | — | **삭제(계산)** — 성공수·성공률은 원자값 나눗셈 |
| `gi_tap_sc` `gi_tap_sr` | — | 삭제(계산) |
| `gi_ttp_sc` `gi_ttp_sr` `gi_ttp_pr` | — | 삭제(계산) |
| `gi_score` | — | **삭제(이동)** — 팀평점은 `jpd-rating`(대외비)이 산출 |
| — | `kpiComputedAt` / `syncedAt` | 신규 — 스냅샷 신선도·동기화 멱등성 |
| — | `sRound` | **신규** — D-MST 한 행을 가리키는 JaionX 키. `시즌(4)+라운드(2)+팀순번(2)`, 예 `26270219`. 일정에서 그대로 가져온다 |
| — | `maxSeq` | 신규 — 레코드 정렬키 발급 |
| — | `lineup` | **신규 위치** — `ff_game_player` 구성 부분이 여기로 |

## ff_game_record (37) → `RecordDoc` — `records/{id}`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gr_id` | (문서 ID) | 이름변경 — Firestore 자동 ID |
| `gi_id` | — | **삭제(경로)** — 1,200회 중복이 사라짐 |
| `p_id` | `playerId` | 이름변경 (`'OWN'` = 자책골) |
| `t_code` | — | **삭제(경로)** — `recordings.teamId`에 있음 |
| `gr_half` | `half` | 이름변경 |
| `gr_half_seconds` | `halfSeconds` | 이름변경 |
| `gr_seconds` | — | **삭제(계산)** — `halves` + `halfSeconds` |
| `gr_area_code` | `area` | 이름변경 |
| `gr_act_code` | `act` | 이름변경 |
| `gr_res_code` | `res` | 이름변경 — `GB`/`GX`/`GOAL` 신 코드 |
| `gr_pos_x` / `gr_pos_y` | `posX` / `posY` | 이름변경 + **단위 변환** — 971×634 픽셀 → 0~100%. `posX = gr_pos_x/971*100` |
| `gr_shoot_pos_x/y` | `shootPosX` / `shootPosY` | 이름변경 |
| `gr_regdt` | `createdAt` | 이름변경 |
| `gt_id` | — | **삭제(계산)** — export 시 `computeAttackPaths()`가 채움 |
| `gr_area_code_org` | — | ⚠️ **미결정** — 원본 구역코드. 진영 반전 전 값으로 추정 |
| `gr_part_pos_x/y` | — | **삭제(불필요)** — `posX/posY`로 통합 |
| `gr_shoot_rate_x/y` | — | ⚠️ **미결정** — 골대 내 비율 좌표 |
| `gr_is_tmp` | — | **삭제(계산)** — `flags.isTap` |
| `gr_is_tap` | — | 삭제(계산) — `flags.isDap` |
| `gr_is_ast` | — | 삭제(계산) — `flags.isAst` |
| `gr_is_sht` | — | 삭제(계산) — `flags.isSht` |
| `gr_is_gol` | — | 삭제(계산) — `flags.isGol` |
| `gr_is_ctb/ctm/cta/cts` | — | 삭제(계산) — `flags.isDtb/isDtm/isDta/isDts` |
| `gr_is_gtb/gtm` | — | 삭제(계산) |
| `gr_is_tmp_s` `gr_is_tap_s` `gr_is_sht_s` | — | 삭제(계산) |
| `gr_moddt` | — | 삭제(불필요) |
| `is_old` | — | 삭제(불필요) |
| — | `seq` | **신규** — 같은 초 안의 순서 |
| — | `isShot` | 신규 — write-back 후에도 슛이었음 보존 |
| — | `shootDspRange` | 신규 — 골포스트 1m 판정 캐시 |
| — | `bapReason` | **신규** — `ff_game_bap` 병합 |
| — | `createdBy` / `playerIdBy` | **신규** — 2인 기록 시 누가 넣었나 |
| — | `source` / `playerIdSource` | 신규 — 사람/비전 구분 |

## ff_game_bap → **표 자체 삭제**

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `bap_id` | — | **삭제(불필요)** — 레코드 1:1이라 별도 ID 불필요 |
| `gi_id` / `gr_id` | — | 삭제(경로) — 레코드 자신이 곧 그것 |
| (BAP 여부·사유) | `RecordDoc.bapReason` | **병합** |
| `gr_half` `gr_half_seconds` `gr_seconds` | — | 삭제(중복) — 레코드에 이미 있음 |

## ff_game_path (11) → `PathSnapshotDoc` — `paths/{pathId}` [확정 시점에만]

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gt_id` | `gtId` | 이름변경 |
| `gi_id` | — | 삭제(경로) |
| `gt_res_code` | `resCode` | 이름변경 |
| `gt_csp` | `dsp` | 이름변경(신용어) |
| `gt_ttp` | `ttp` | 유지 |
| `gt_upp` `gt_utp` `gt_ctp` `gt_stp` | `ptype` | **구조 변경** — 불린 4개 → 단일 열거값 |
| `gt_regdt` / `gt_moddt` | — | 삭제(불필요) |
| — | `recordIds` | **신규** — 이 루트에 속한 레코드 |

## ff_game_player (46) → `LineupEntry` + `PlayerStatsDoc`

### 출전 정보 → `RecordingDoc.lineup[playerId]` (`LineupEntry`)

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gi_id` / `p_id` | — | 삭제(경로) — 경로 + 맵 키 |
| `gp_type` | `type` (`START`/`BENCH`) | **이름변경** |
| `gp_order` | `order` | 이름변경 |
| `gp_in_half` | `inHalf` | 이름변경 |
| `gp_in_half_seconds` | `inSeconds` | 이름변경 |
| `gp_out_half` | `outHalf` | 이름변경 |
| `gp_out_half_seconds` | `outSeconds` | 이름변경 |
| `gp_in_seconds` / `gp_out_seconds` | — | 삭제(계산) — 경기 기준 초는 `halves`로 산출 |
| `gp_seconds` | — | 삭제(계산) — 출전시간 = out − in |
| `gp_point_plus` / `gp_point_minus` | — | ⚠️ **미결정** — 상단 `-3/-1/+1/+3` 버튼의 저장 위치. **지금 화면에는 버튼만 있고 저장이 없음** |
| `is_old` | — | 삭제(불필요) |
| — | `slot` | **신규** — 포메이션 슬롯 ID |
| — | `no` / `name` / `pos` | **신규(스냅샷)** — 이적해도 과거 기록 불변 (원칙 7) |

### 개인 KPI → `playerStats/{playerId}` (`PlayerStatsDoc`) [확정 시점에만]

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gp_tmp` | `TAP` | 이름변경(신용어) |
| `gp_tap` | `DAP` | 이름변경(신용어) |
| `gp_utp` | `UTP` | 유지 |
| `gp_ctp` | `DTP` | 이름변경(신용어) |
| `gp_ttp` | `TTP` | 유지 |
| `gp_sht` | `SHOT` | 이름변경 |
| `gp_ast` | `AST` | 이름변경 |
| `gp_gol` | `GOAL` | 이름변경 |
| `gp_ctb` | `DTB` | 이름변경(신용어) |
| `gp_ctm` | `DTM` | 이름변경(신용어) |
| `gp_cta` | `DTA` | 이름변경(신용어) |
| `gp_cts` | `DTS` | 이름변경(신용어) |
| `gp_gtb` | `GTB` | 유지 |
| `gp_gtm` | `GTM` | 유지 |
| `gp_asr` | `ASR` | 유지 |
| `gp_ssr` | `SSR` | 유지 |
| `gp_tmp_sc/sr/tr` | — | **삭제(계산)** — 성공수·성공률·시도율 |
| `gp_tap_sc/sr/tr` | — | 삭제(계산) |
| `gp_ttp_sc/sr/tr/pr` | — | 삭제(계산) |
| `gp_sht_sc/tr` | — | 삭제(계산) |
| `gp_gol_tr` | — | 삭제(계산) |
| `gp_score_rel` `gp_score_abs` `gp_score` | — | **삭제(이동)** — 평점은 `jpd-rating`(대외비)만 채움 |

## ff_game_player_log (10) → `LineupEntry`에 병합

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gi_id` / `t_code` | — | 삭제(경로) |
| `pl_out_p_id` | `lineup[해당선수].outHalf/outSeconds` | **병합** — 맵 키가 곧 선수 |
| `pl_in_p_id` | `lineup[해당선수].inHalf/inSeconds` | 병합 |
| `pl_in_half` | `inHalf` | 병합 |
| `pl_in_seconds` | `inSeconds` | 병합 |
| `pl_in_position` | `pos` | 병합 |
| `pl_in_order` | — | ⚠️ **미결정** — 몇 번째 교체인지 순번. `LineupEntry.subOrder` 추가 필요 |
| `pl_regdt` / `pl_moddt` | — | 삭제(불필요) |

> **별도 `subs` 컬렉션을 만들지 않는다.** 교체 정보는 `lineup` 맵 안에서 완결되고, 어차피 `recordings` 문서를 통째로 읽으므로 추가 읽기가 0이다. 교체 목록 화면은 `lineup`에서 `inHalf`/`outHalf`가 채워진 항목만 골라 시간순 정렬하면 된다.

## ff_game_card (7) → ⚠️ **타입 없음 — 만들어야 함**

| 레거시 | 새 필드(제안) | 상태 |
|---|---|---|
| `c_id` | (문서 ID) | 이름변경 |
| `gi_id` | — | 삭제(경로) |
| `gp_id` | `playerId` | 이름변경 |
| `gp_card_half` | `half` | 이름변경 |
| `gp_card_time` | `halfSeconds` | 이름변경 |
| `gr_seconds` | — | 삭제(계산) |
| `gp_card_card` | `card` (`Y`/`R`) | 이름변경 |

> **`schema.ts`에 `CardDoc`이 아직 없다.** 트리(§4)에는 `cards/{cardId}`로 적혀 있으나 타입 미작성. 카드도 교체처럼 `lineup`에 병합할지, 별도 컬렉션으로 둘지 **결정 필요**.

## ff_game_state (7) → **표 자체 삭제**

| 레거시 | 상태 |
|---|---|
| `gm_id` `gm_state` `gi_goal_home` `gi_goal_away` `gs_regdt` `gs_moddt` `n_gm_id` | **삭제(불필요)** — 관리자가 `recordings.status`를 직접 고치는 것으로 대체 |

---

# ② 저장 관리 데이터 스키마

## ff_team (14) → `TeamDoc` — `teams/{teamId}`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `t_code` | (문서 ID) | 이름변경 |
| `t_name` | `name` | 이름변경 |
| `t_name_kr` | `nameKr` | 이름변경 |
| `t_name_full` | `nameFull` | 이름변경 |
| `t_name_short` | `nameShort` | 이름변경 |
| `u_text_color_code` | `textColor` | 이름변경 |
| `s_code` | `stadiumId` | 이름변경 |
| `t_coach` / `t_coach_kr` | `coach` / `coachKr` | 이름변경 |
| `t_begin` / `t_end` | `foundedAt` / `dissolvedAt` | 이름변경 |
| `l_code` | `currentLeagueId` + `teams/{id}/seasons/{seasonId}` | **구조 변경** — 단일값이면 승강제를 표현 못 함 |
| `t_name_ae` / `t_name_cn` | — | ⚠️ **미결정** — 아랍어·중국어 팀명. 쓸지 안 쓸지 |
| — | `crestUrl` | 신규 — 엠블럼 |
| — | `createdAt/By` `updatedAt/By` | 신규 — 감사 필드 |

## ff_player (9) → `PlayerDoc` — `players/{playerId}`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `p_id` | (문서 ID) | 이름변경 |
| `p_name` | `name` | 이름변경 |
| `p_name_en` | `nameEn` | 이름변경 |
| `p_name_full` | `nameFull` | 이름변경 |
| `p_foot` | `foot` | 이름변경 |
| `p_birth` | `birth` | 이름변경 |
| `p_height` | `height` | 이름변경 |
| `p_nation` | `nation` | 이름변경 |
| `p_id_old` | `legacyPlayerId` | 이름변경 |
| — | `active` | **신규** — 은퇴/삭제 대신 비활성 |
| — | `currentTeamId` `currentNo` `currentPos` | **신규(파생 캐시)** — 계약에서 갱신 |
| — | 감사 필드 | 신규 |

## ff_player_team (8) → `ContractDoc` — `players/{id}/contracts/{id}`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `p_id` | — | 삭제(경로) |
| `t_code` | `teamId` | 이름변경 |
| `pt_begin` | `from` | 이름변경 |
| `pt_end` | `to` (`null`이면 현재) | 이름변경 |
| `pt_num` | `no` | 이름변경 |
| `pt_pos` | `pos` | 이름변경 |
| `l_code` | `leagueId` | 이름변경 |
| `p_id_old` | — | 삭제(불필요) — `PlayerDoc`에 있음 |
| — | `seasonId` | 신규 |
| — | `fromTeamId` | **신규** — 직전 소속팀. 한 문서로 "A→B" 완성 |
| — | `transferType` | **신규** — TRANSFER/LOAN/… |
| — | `fee` / `currency` | 신규 |
| — | 감사 필드 | 신규 |

## ff_league (3) → `LeagueDoc`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `league_code` | (문서 ID) | 이름변경 |
| `league_name` | `name` | 이름변경 |
| `league_name_en` | `nameEn` | 이름변경 |
| — | `country` | 신규 |
| — | 감사 필드 | 신규 |

## ff_season (7) → `SeasonDoc`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `l_code` | `leagueId` | 이름변경 |
| `s_name` | `name` | 이름변경 |
| `s_fr_date` / `s_to_date` | `from` / `to` | 이름변경 |
| `s_alias` | `alias` | 이름변경 |
| `s_fr_dt` / `s_to_dt` | — | 삭제(중복) — 날짜/일시 이중 보관 |
| — | 감사 필드 | 신규 |

## ff_stadium (8) → `StadiumDoc`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `s_code` | (문서 ID) | 이름변경 |
| `s_name` | `name` | 이름변경 |
| `s_name_kr` | `nameKr` | 이름변경 |
| `s_seats` | `seats` | 이름변경 |
| `s_country` / `s_city` | `country` / `city` | 이름변경 |
| `s_hometeam` | `homeTeamId` | 이름변경 |
| `s_ground` | `surface` | 이름변경 |
| — | 감사 필드 | 신규 |

## ff_user (5) → `RecorderProfileDoc` — `recorders/{uid}`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `u_id` | (문서 ID) | **이름변경** — Firebase Auth UID |
| `u_name` | `name` | 이름변경 |
| `t_code` | `teamId` | 이름변경 |
| `u_hand` | `handedness` | 이름변경 |
| `u_pw` | — | **삭제(불필요)** — 비밀번호는 Auth가 관리 |
| — | `role` (`recorder`/`admin`) | **신규** — 데이터 관리 권한 |
| — | `level` (`basic`/`advanced`) | **신규** — 갱신 버튼 구성 |

## ff_session / ff_member_session → **표 자체 삭제**

세션ID·로그인ID·GCM등록ID·기기ID·등록일시 — 전부 **삭제(불필요)**. Firebase Auth 토큰이 대체하며 남길 값이 없다.

## ff_formation (3) → **컬렉션 안 만듦**

| 레거시 | 상태 |
|---|---|
| `t_code` | **삭제(불필요)** — 포메이션은 팀 소유물이 아니라 공용 도형 |
| `fm_order` | 삭제(불필요) — 팀별 출력순서 |
| `fm_formation` | **삭제(상수화)** — 슬롯 좌표는 `TeamSelection.vue` 상수. 세션에는 `formationKey` 문자열만 저장 |

---

# ⚠️ 결정이 필요한 것 (구멍 목록)

| # | 항목 | 내용 |
|---|---|---|
| 1 | **`ff_game_card`** | `CardDoc` 타입 자체가 없음. 별도 컬렉션 vs `lineup` 병합 결정 필요 |
| 2 | **`gp_point_plus` / `gp_point_minus`** | 상단 `-3/-1/+1/+3` 버튼. **지금 화면에는 버튼만 있고 저장 로직이 없음.** 원래 무슨 점수인지 확인 필요 |
| 3 | `pl_in_order` | 교체 순번. `LineupEntry.subOrder` 추가 여부 |
| 4 | `gr_area_code_org` | 원본 구역코드 (진영 반전 전 값으로 추정) |
| 5 | `gr_shoot_rate_x/y` | 골대 내 비율 좌표. `shootPosX/Y`와 중복인지 확인 |
| 6 | `gi_part_ex` | 연장전 진영. H3/H4 UI 미구현 상태 |
| 7 | `gm_sub_league` | 서브리그 개념 사용 여부 |
| 8 | `t_name_ae` / `t_name_cn` | 아랍어·중국어 팀명 사용 여부 |
