# DID KPI 용어 변경 이력 & 데이터 모델 계약

레거시(`lib/extra.lib.php`, Gnuboard5/MySQL) → 신규(`app/utils/didLogic.ts`, Nuxt/Firestore)
포팅 시 적용한 용어 변경과, 그 과정에서 확정한 레코드 데이터 모델을 기록한다.

**기본 방침: 레거시 동작을 100% 재현한다.** 직관과 어긋나 보이는 부분이 있어도
그대로 옮긴다. 과거 데이터와 숫자가 어긋나면 안 되기 때문이며, 개선은 나중에
별도 결정으로 진행한다.

---

## 1. 용어 변경 매핑

| 레거시 | 신규 | 레거시 DB 컬럼 | 근거 |
|---|---|---|---|
| TMP | **TAP** | `gr_is_tmp`, `gr_is_tmp_s` | PPT `DID 프로세스 슬라이드_20251120` 슬라이드 7 (`TMP -> TAP`) |
| TAP | **DAP** | `gr_is_tap`, `gr_is_tap_s` | 동 PPT 슬라이드 7 (`TAP -> DAP`) + `extra.lib.php:4021` 주석 `// 21.12.29 TAP->DAP 용어 변경.` |
| CTP | **DTP** | `gt_ctp` | 동 PPT 슬라이드 7 (`CTP -> DTP`) |
| CSP | **DSP** | `gt_csp` | `extra.lib.php:2414` 주석 `/* csp = dsp */` + 사용자 확인. 의미는 "DTP 중 유효슈팅을 포함하는 건" |
| CTS | **DTS** | `gr_is_cts` | 사용자 확인 |
| CTA | **DTA** | `gr_is_cta` | CTS→DTS 와 동일한 `CT*`→`DT*` 패턴 |
| CTM | **DTM** | `gr_is_ctm` | 〃 |
| CTB | **DTB** | `gr_is_ctb` | 〃 |

> ⚠️ `TAP` 은 레거시와 신규에서 **의미가 다르다.** 레거시 TAP = 신규 DAP,
> 신규 TAP = 레거시 TMP. 옛 문서·쿼리를 볼 때 혼동 주의.

### 변경하지 않은 용어

| 용어 | DB 컬럼 | 사유 |
|---|---|---|
| UPP | `gt_upp` | 신규 명칭 근거 없음. 내부 분류값(집계 대상 아님) |
| UTP | `gt_utp` | 〃 |
| STP | `gt_stp` | 〃 |
| TTP | `gt_ttp` | 〃 (UTP + DTP 합계) |
| GTB / GTM | `gr_is_gtb`, `gr_is_gtm` | `CT*`→`DT*` 패턴의 대상(`C` 접두)이 아니며 별도 근거 없음 |
| SHOT / ASR / GSR / SSR / BAP | — | 레거시부터 동일 명칭 |

> `DTA`/`DTM`/`DTB` 는 `CTS→DTS` 에서 패턴을 확장 적용한 것.

### 화면에 노출되는 8개 KPI (PPT 슬라이드 7 기준)

`TAP` · `DAP` · `DTP` · `SHOT` · `ASR` · `GSR` · `SSR` · `BAP`
→ `app/pages/TeamSelection.vue` 의 `kpis` 배열과 일치.

---

## 2. 레코드 데이터 모델 — 플레이 하나 = 레코드 하나

APK 디컴파일(`DPlayInputDefaultFragment.java`)로 확인한 실제 동작이다.

| 단계 | 동작 | 결과 |
|---|---|---|
| 액트 버튼(C/P/K/F/S/H/R) | `setActCode(act)` + `setResCode("O")` | `act='C', res='O'` |
| 결과 버튼(X/B, 골대 존) | 같은 레코드의 `res` 만 덮어씀 | `act='C', res='X'` |
| **예외** 슛(S/H/R)에 X/B | 위 + `setActCode(null)` | `act='', res='X'` |

**`'O'` 는 계산용 임시값이 아니라 앱이 실제로 저장하는 값이다**
(`DPlayInputDefaultFragment.java:151`). 결과를 별도 레코드로 나눠 저장하지 않는다.

### 2-1. write-back

슛이 X/B 로 끝나 `act` 가 비워진 경우, 서버는 **직전 레코드의 `res` 도 같은 값으로
덮어쓴다** (`extra.lib.php:1211`). 이 조건은 들어온 데이터의 `act` 가 비어 있을 때만
성립하므로, 비-슛(C/P/K/F)의 X/B 에서는 write-back 이 일어나지 않는다.

`didLogic.ts` 의 `applyResult()` 가 act 비움과 write-back 을 함께 재현한다.

### 2-2. lookahead 보정 — 레거시 그대로 유지

`dplay_game_path_reset_calc`(`:2148`) 와 `dplay_game_bap_reset_calc`(`:1853`) 는
판정 직전에 `act` 가 있는 레코드의 `res` 를 되돌린다.

```php
if (!empty($act)){
    if ($act=="S" || $act=="H" || $act=="R"){
        if ($nextR && empty($nextR['gr_act_code'])) $res = "O";
    } else $res = "O";
}
```

주석에 이유가 있다 — *"DID 상에서는 ACT를 먼저 누르고 X/B 누르는 구조이기 때문에
DID와 동일한 기준 및 순서로 판단하기 위한 처리"*. 단말이 **액트를 누른 시점**에
판정하던 타이밍을, 서버가 1-step-delay 루프로 재현한 것이다.

**결과적으로 C/P/K/F 는 `res` 가 X/B 여도 공격루트를 끊지 않는다.**
루트를 끊는 것은 **슛**과 **4초 규칙** 두 가지뿐이다.

> 예) `C(O) → P(X) → P(O) → S(GOAL)` 은 **하나의 DTP 루트**로 묶인다.
> 패스를 뺏겨도 루트가 이어지는 것이 레거시의 실제 집계 기준이다.

직관과 어긋나지만 **의도적으로 그대로 둔다.** 여기를 고치면 루트를 자르는 지점이
달라져 UTP/DTP 판정과 DAP 개수가 전부 바뀌고, 과거 데이터와 값이 어긋난다.

### 2-3. 보정을 적용하는 곳 / 하지 않는 곳

| 함수 | 보정 | 레거시 대응 |
|---|---|---|
| `splitIntoChains` (루트 자르기) | **적용** | `dplay_game_path_reset_calc` |
| `computeBap` (BAP 판정) | **적용** | `dplay_game_bap_reset_calc` |
| `classifyChain` (분류·플래그 산출) | **미적용** (원본 `res` 사용) | `dplay_game_path_update` |

레거시도 `_reset_calc` 는 지역변수 `$res` 만 고치고 `$currR` 자체는 그대로
`$g_path_array` 에 push 하므로, `_path_update` 는 보정 전 원본 값을 본다.

---

## 3. 미해결 — DSP(유효슛) 는 아직 정확히 산출할 수 없다

레거시 DSP 판정 조건 (`extra.lib.php:2415`):

```php
if ($res=="G" || $res=="R" || $res=="L" || $res=="H" ||
    ($res=="B" && ($item['gr_shoot_pos_x'] > 0 || $item['gr_shoot_pos_y'] > 0)))
```

- `L`/`H`/`R` 은 **골대 이미지 위를 탭한 좌표**에서 앱이 파생시키는 값이다
  (`DPlayInputDefaultFragment.java:270-279`).
- `B` 는 `gr_shoot_pos_x/y` 가 있어야 유효슛으로 인정된다.

현재 `app/pages/DidInput.vue` 의 골대는 **이산 존 버튼**(GOAL/B/X/HX/LX/RX)이라
탭 좌표를 수집하지 않는다. 따라서 지금 구조로는 **`GOAL` 일 때만 DSP 가 잡히고
나머지는 전부 누락된다.**

**해결하려면** 골대 안쪽을 좌표로 찍는 방식으로 바꾸고, 그 좌표에서 `L`/`H`/`R`/`G` 를
파생시켜야 한다. `DidRecord.shootPosX/shootPosY` 필드는 미리 마련해 두었다.

---

## 4. 로직 연결 시 함께 처리할 항목

`didLogic.ts` 는 순수 로직만 담고 있고 아직 `DidInput.vue` 에 연결되지 않았다.
연결할 때 아래를 함께 맞춰야 한다.

- **X/B 처리 방식 변경** — 현재 `DidInput.vue` 는 X/B 를 새 액트로 만든다
  (`clickAct('X', false)` → `{act:'X', result:'O'}`). 레거시 기준으로 `X`/`B` 는
  act code 가 아니라 res code 다 (PPT 슬라이드 17: "X, B 는 ACT의 결과로서 입력").
  → 직전 레코드에 `applyResult()` 로 반영해야 한다.
- **`seconds` 필드 추가** — 현재는 `time` 문자열만 있어 4초 규칙을 판정할 수 없다.
- **골대 좌표 수집** — 3장 참고.
- **선수 입력창 트리거** — `isDap === true` 인 레코드에만 선수 선택 버튼을 노출한다
  (PPT 슬라이드 17·19·20: 데이터 리스트의 선수 컬럼은 "DAP일 경우에만").
