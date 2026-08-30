# Golden Watermelon Live 주문 생명주기 안전 보정 v3c — 2026-08-30

- 안전 변경 시각: `2026-08-30T04:06:02Z`.
- 진입 구간: `[2026-08-29T04:00:00Z, 2026-09-05T04:00:00Z)` 유지.
- 후속 관찰 종료: `2026-09-12T04:00:00Z` 유지.
- 여섯 Jenkins job은 non-concurrent, 1분 주기, exact `$5`를 유지한다.
- 분석 묶음은 `config_hash × strategy_source_digest × mode × job_name`이다.

이 문서는 v3b의 종목군, 0.96/0.99 진입 하한, 손절 가격과 노출 한도를 바꾸지 않는다. 실제
Elversberg SELL과 PHI–LAA 반대 결과 사례에서 확인된 주문 생명주기 결함 두 개만 고친다. v3b와
v3c는 source digest로 분리하고 수익성 집계에서 섞지 않는다. 기존 DB를 clean, rewrite, migrate,
copy, merge, backfill 또는 delete하지 않는다.

## 유지되는 실험축

| family | A | B | exact `$5` ask VWAP |
|---|---|---|---|
| Soccer | `polybot-cat` | `polybot-dog` | `[0.96,0.999]` / `[0.99,0.999]` |
| MLB | `polybot-bear` | `polybot-tiger` | `[0.96,0.999]` / `[0.99,0.999]` |
| NHL | `polybot-lion` | `polybot-wolf` | `[0.96,0.999]` / `[0.99,0.999]` |

0.92 진입과 0.99 매도 조합은 이 변경에 추가하지 않는다. 해당 가설은 별도 향후 수집 묶음에서
최소 100개 독립 경기를 확보하기 전에는 선택하거나 폐기하지 않는다.

## 변경 1: 지연된 FOK BUY/SELL 0체결 자동 종결

FOK 주문이 `DELAYED`로 남고 exact order detail이 사라져도 다음 조건을 모두 만족하면 2분 뒤
0체결로 종결한다.

1. exact 주문 ID가 권위 있는 order catalog에 없다.
2. 해당 token의 전체 인증 trade catalog에도 exact 주문 ID가 없다.
3. cancellation API가 exact ID의 취소 또는 이미 취소/부재를 증명한다.
4. 원장에 positive fill, associated trade, terminal status가 없고 기존 대사 오류가 catalog
   absence를 기록했다.

조건 하나라도 모호하면 `PENDING_BUY/PENDING_SELL`을 유지하고 신규 진입을 계속 막는다. zero-fill
SELL만 기존 bot-owned position을 `HOLDING`으로 되돌리며, 매도 체결이나 손익을 만들지 않는다.
그 뒤 같은 cycle의 Gamma/CLOB resolution 또는 fresh stop 관리가 그 position을 이어받는다.

## 변경 2: confirmed 손절 뒤 반대 결과 1회 전환

기존 condition 단위 720시간 cooldown을 `event × token` 규칙으로 보정한다.

- 같은 token의 재매수는 720시간 동안 금지한다.
- 동일 event에 open position 또는 불확실한 BUY intent가 있으면 다른 결과도 금지한다.
- 첫 결과의 SELL이 execution ledger에서 exact confirmed stop으로 끝난 경우에만, 다른 token 한
  개를 다음 fresh in-band observation에서 한 번 허용한다.
- 한 event에서 서로 다른 token 두 개가 거래된 뒤에는 세 번째 진입과 왕복 재진입을 모두 막는다.
- resolution, 수동 종결, 증거 불완전 종결은 반대 결과 전환 권한을 만들지 않는다.
- 허용된 두 번째 Trade의 `entry_reason`에
  `one_time_opposite_after_confirmed_stop`을 기록한다.

## 유지되는 안전 조건과 판정

- account/event/cycle `20/1/5`, emergency SELL cycle당 `1`, manual wallet position 미편입·미청산.
- effective stop `max(0.70, confirmed BUY VWAP-0.05)`, 독립 Gamma+CLOB OPEN proof, fresh complete
  book과 spread `<=0.10` 유지.
- unresolved PENDING/QUARANTINED/orphan/fill-fee gap은 신규 BUY를 막는다.
- confirmed SELL + proven resolution 경제손익 `<=-$10`이면 신규 BUY를 막는다.
- 배포 후 `PENDING_SELL=0`만으로 충분하지 않다. bot-owned open token의 DB confirmed BUY 잔량과
  인증 지갑 잔고를 함께 대사하고 수동 보유는 별도로 둔다.
- 이 보정은 안전 결함 수정이다. 현재 표본으로 종목군, 0.96/0.99 arm, 0.92→0.99 가설의
  수익성을 판정하지 않는다.
