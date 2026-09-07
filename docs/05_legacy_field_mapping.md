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
| `gm_sub_league` | `group` | **이름변경** — 서브리그가 아니라 토너먼트 조(그룹) 표기용이었다. 아래 참고 |
| `is_realtime` | `recordings.inputMode` | **이름변경 + 이동** — 경기 속성이 아니라 기록자별 설정 |
| `is_old` | — | 삭제(불필요) — 마이그레이션 플래그 |
| `is_view` | — | 삭제(불필요) |
| `is_onair` | — | 삭제(불필요) — BID 방송용 |
| — | `seasonId` | **신규** — 단 `gm_id` 앞 8자리와 중복. 질의 편의용 |
| — | `matchType` | **신규** — `'league' \| 'tournament'` |
| — | `stage` | **신규** — 토너먼트 전용(`'R32'\|'R16'\|'QF'\|'SF'\|'F'`). `round` 를 재사용하지 않는다 |
| — | `leg` | **신규** — 토너먼트 2차전(홈/원정 합산) 구분. 합산 스코어로 진출 여부를 가릴 때 필요 |
| — | `createdAt` / `updatedAt` | 신규 — 레거시 `ff_game` 에는 없음. 관례 |

> **`gm_id` 는 우리가 채번하지 않는다.** 경기 일정 관리 화면(`/manage/schedules`)에서 담당자가 직접 입력한 값이 그대로 문서 ID가 된다. `match_schedule.json` 자동 임포트도 하지 않는다.
>
> `sRound` 는 `matches`/`recordings` 어디에도 저장하지 않는다 — 레거시 SQL에도 없던 엑셀 워크플로 전용 값이었고, JaionX 쪽 dMST 출력 단계(파이썬)에서 생성한다. 아래 ff_game_info 참고.

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
| `gi_part_ex` | `fieldSideEx` | **이름변경 — 유지 결정**. 연장전(H3) 진영. 토너먼트 지원용. H3/H4 UI는 아직 미구현 |
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
| `gi_ctb` | `kpi.DTB` | 이름변경(신용어) — `PlayerStatsDoc`과 동일 명칭 |
| `gi_ctm` | `kpi.DTM` | 이름변경(신용어) — `PlayerStatsDoc`과 동일 명칭 |
| `gi_cta` | `kpi.DTA` | 이름변경(신용어) — `PlayerStatsDoc`과 동일 명칭 |
| `gi_cts` | `kpi.DTS` | 이름변경(신용어) — `PlayerStatsDoc`과 동일 명칭 |
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
| `gi_score` | `teamRating` | **이동 + 형태 변경** — 계산은 `jpd-rating`(대외비)이 하고 결과값(5분구간 JMX 시계열)만 되돌려받는다. 공식은 여전히 밖으로 안 나간다 |
| — | `kpiComputedAt` / `syncedAt` | 신규 — 스냅샷 신선도·동기화 멱등성 |
| — | `maxSeq` | 신규 — 레코드 정렬키 발급 |
| — | `lineup` | **신규 위치** — `ff_game_player` 구성 부분이 여기로 |

> **S-Round는 DID 저장 필드에서 제외한다.** JaionX 쪽(`admin/dMST.vue`, `composables/pMST.ts`)에서 여전히 쓰이는 건 확인했지만, 경기 식별·홈원정 매칭은 `gm_id` + `team_type`(문서 ID의 H/A)만으로 충분하다. S-Round가 필요하면 파이썬 dMST 출력 단계에서 생성한다 — 3시즌 780경기로 검증한 채번 규칙이 있으니 그때 그대로 쓰면 된다.

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
| `gr_area_code_org` | — | **삭제(불필요)** — `03.areacode.sql` 확인 결과 구역코드 재계산 마이그레이션 전 백업용. 실시간 기록엔 불필요 |
| `gr_part_pos_x/y` | — | **삭제(불필요)** — `posX/posY`로 통합 |
| `gr_shoot_rate_x/y` | — | **삭제(불필요)** — 안 쓰는 값. 저장 안 함 |
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
| `gp_point_plus` / `gp_point_minus` | — | **삭제(불필요)** — 상단 `-3/-1/+1/+3` 버튼은 경기시간 조정용이라 무관. 저장 안 함 |
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
| `gp_score_rel` `gp_score_abs` `gp_score` | `scoreRel` `scoreAbs` `score` | **이동** — 계산은 `jpd-rating`(대외비)이 하고 결과값만 되돌려받아 채운다. 확정 전엔 `null` |
| `p_id` | `playerId` | **신규 재도입** — 문서 ID와 중복이지만 `collectionGroup` 쿼리 전용. "선수 한 명의 전 경기"를 경로 없이 검색하려면 필드로도 있어야 한다(SQL의 `p_id` 컬럼과 같은 이유) |

## ff_game_player_log (10) → `LineupEntry`에 병합

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `gi_id` / `t_code` | — | 삭제(경로) |
| `pl_out_p_id` | `lineup[해당선수].outHalf/outSeconds` | **병합** — 맵 키가 곧 선수 |
| `pl_in_p_id` | `lineup[해당선수].inHalf/inSeconds` | 병합 |
| `pl_in_half` | `inHalf` | 병합 |
| `pl_in_seconds` | `inSeconds` | 병합 |
| `pl_in_position` | `pos` | 병합 |
| `pl_in_order` | — | **삭제(불필요)** — 저장 안 함 |
| `pl_regdt` / `pl_moddt` | — | 삭제(불필요) |

> **별도 `subs` 컬렉션을 만들지 않는다.** 교체 정보는 `lineup` 맵 안에서 완결되고, 어차피 `recordings` 문서를 통째로 읽으므로 추가 읽기가 0이다. 교체 목록 화면은 `lineup`에서 `inHalf`/`outHalf`가 채워진 항목만 골라 시간순 정렬하면 된다.

## ff_game_card (7) → `CardDoc` — `recordings/{H|A}/cards/{cardId}`

| 레거시 | 새 필드 | 상태 |
|---|---|---|
| `c_id` | (문서 ID) | 이름변경 |
| `gi_id` | — | 삭제(경로) |
| `gp_id` | `playerId` | 이름변경 |
| `gp_card_half` | `half` | 이름변경 |
| `gp_card_time` | `halfSeconds` | 이름변경 |
| `gr_seconds` | — | 삭제(계산) |
| `gp_card_card` | `card` (`Y`/`R`) | 이름변경 |

> **해결됨.** 카드는 교체와 달리 "act"가 없고 공격 체인과 무관해 `RecordDoc`에 병합하면 KPI 계산 필터링만 오염된다 — 별도 컬렉션 + `CardDoc` 타입으로 `schema.ts`에 정의했다. 화면 UI·저장 로직 연동은 아직 후속 구현 대상.

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
| `t_coach` / `t_coach_kr` | `currentCoachId` + `coaches/{id}` | **구조 변경** — 문자열 하나로 두면 감독 교체 시 과거 경기의 감독 이력이 사라진다. 아래 참고 |
| `t_begin` / `t_end` | `foundedAt` / `dissolvedAt` | 이름변경 |
| `l_code` | `currentLeagueId` + `teams/{id}/seasons/{seasonId}` | **구조 변경** — 단일값이면 승강제를 표현 못 함 |
| `t_name_ae` / `t_name_cn` | — | **삭제(불필요)** — 나중에 다국어 팀명 필요해지면 그때 필드 추가 |
| — | `crestUrl` | 신규 — 엠블럼 |
| — | `createdAt/By` `updatedAt/By` | 신규 — 감사 필드 |

## (레거시에 없음) → `CoachDoc` + `CoachContractDoc` — `coaches/{coachId}`, `coaches/{coachId}/contracts/{id}`

> 레거시엔 감독 전용 테이블이 없었다 — `ff_team.t_coach`/`t_coach_kr` 문자열 두 개가 전부였다. `PlayerDoc`/`ContractDoc`과 같은 이유(원칙 7 — 이적해도 과거 기록 불변)로 정체성과 이력을 분리해 새로 만든다.

| 타입 | 필드 | 비고 |
|---|---|---|
| `CoachDoc` | `name` / `nameKr` / `nameEn` / `birth` / `nation` / `active` / `currentTeamId`(파생 캐시) / `legacyCoachId` | `PlayerDoc`과 동일 패턴 |
| `CoachContractDoc` | `teamId` / `leagueId` / `seasonId` / `from` / `to`(null=현재) / `fromTeamId` | `ContractDoc`과 동일 패턴 |
| `TeamDoc.currentCoachId` | (필드) | 파생 캐시. 이름이 아니라 ID |

관리 화면(`/manage`)에 감독 등록·이적 처리 화면이 새로 필요하다 — 아직 미구현.

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

# ✅ 결정된 것 (구멍 목록 — 전부 해결됨)

| # | 항목 | 결정 |
|---|---|---|
| 1 | `ff_game_card` | `CardDoc` 신설 — 별도 `cards` 컬렉션. 화면 연동은 후속 구현 |
| 2 | `gp_point_plus` / `gp_point_minus` | 삭제 — 상단 `-3/-1/+1/+3` 버튼은 경기시간 조정용이라 무관. `DidInput.vue`의 `stepSeconds()`에 이미 구현·연결되어 있음(코드 확인 완료) |
| 3 | `pl_in_order` | 삭제 |
| 4 | `gr_area_code_org` | 삭제 — 구역코드 재계산 마이그레이션 전 백업용 |
| 5 | `gr_shoot_rate_x/y` | 삭제 |
| 6 | `gi_part_ex` | 유지 — `fieldSideEx`. 토너먼트 연장전 지원용 |
| 7 | `gm_sub_league` | `group`으로 이름변경 — 토너먼트 조 표기용이었다 |
| 8 | `t_name_ae` / `t_name_cn` | 삭제 — 필요해지면 나중에 추가 |
| 9 | 좌표 단위 | 971×634 픽셀 ↔ 0~100%. 실데이터로 검증 완료 |
| 10 | `kpi.B/M/A/S` vs `DTB/DTM/DTA/DTS` | `DTB/DTM/DTA/DTS`로 통일 — 팀·선수 KPI가 같은 값을 다른 이름으로 저장하던 것을 발견, TAP/DAP/DTP처럼 "같은 이름, 타입으로만 구분" 원칙에 맞춤 |
| 11 | `leg` (토너먼트 2차전) | `MatchDoc.leg?: 1 \| 2` 신규 — 합산 스코어로 진출 여부를 가릴 때 이 경기가 1차전/2차전인지 필요 |

---

# 파이프라인 운영 안정성 (컬럼 매핑과 별개인 구조 결정)

DID 입력 → jpd-did KPI → jpd-rating 평점 → jpd-did 조립 → JaionX 전송 흐름을 실제로 돌릴 때 필요한 것들. 레거시 컬럼과 무관한 신규 결정이라 여기 따로 적는다. 상세는 `04_firestore_schema.html` §12(운영 안정성) · §13(실시간 증분 계산) 참고.

| 항목 | 결정 |
|---|---|
| 갱신 버튼의 동기 체인 | `exportJobs/{gm_id}_{H\|A}` 문서로 분리 — 파이썬 서비스가 `stage`를 보고 이어받는 워커가 됨. 중간 실패 복구 + 동시 경기 종료 폭주 흡수 |
| jpd-rating 왕복 버전 관리 | `RecordingDoc.kpiVersion`(+1씩 증가) / `teamRating` / `ratingBasedOn`. 다르면 낡은 값으로 재요청 |
| `PlayerStatsDoc` 평점 필드 | `scoreRel`/`scoreAbs`/`score`/`ratingBasedOn` 신설 — jpd-rating이 계산해서 되돌려준 값만 채움. 공식은 여전히 jpd-rating에만 |
| `RecordingDoc` 쓰기 경합 | `maxSeq`는 half 종료·일시정지 때만 서버 반영, 그 사이엔 클라 메모리에서 증가 |
| 잠금 상태 | `h1Locked`/`h2Locked`를 `RecordingDoc` 필드로 편입 — 실연동 시 실시간 구독으로 자동 동기화 |
| 2인 기록 동시 편집 | 항상 `updateDoc`(부분 갱신). 레코드 생성 충돌 자체는 `createdBy`/`playerIdBy` 구조상 안 생김 |
| JaionX 전송 멱등성 | `exportJobs` 문서 ID = `gm_id + team_type`(자연키) — 재시도해도 중복 행 없음 |
