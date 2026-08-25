# Golden Watermelon Live — In-Play Match Result Live A/B

## 검정 질문

5개 축구 리그의 경기 중 home/draw/away 결과 YES token이 매우 높은 가격에 도달했을 때,
exact `$5` FOK로 진입하고 `0.70` emergency stop 또는 proven resolution까지 관리하면 실제
confirmed fill·fee 이후 양의 기대값이 있는가?

## 선행 증거와 보수적 선택

1m-v2a freeze 직전 verified White/Grey DB에서 White 1분 cadence는 1,373/1,374 interval,
Grey 5분 cadence는 275/275 interval이 정상이었다. 대상 unique event는 48개였고 리그 분포는
EPL 6, Ligue 1 12, LaLiga 18, MLS 12, Bundesliga 0으로 좁다. White
hold-to-resolution은 0.95/0.96/0.97에서 동일한 축구 역전패 한 건을 포함해 fee-net
음수였고, 0.98은 3/3, 0.99는 1/1 양수였다. Grey의 양수 결과는 EPL 패배 표본을 놓쳐
threshold 선택 근거로 단독 사용하지 않는다.

따라서 Cat `0.98`, Dog `0.99`는 최적화 결과가 아니라 손실 사례를 포함한 작은 표본에서
보수적으로 정한 prospective pilot이다. `0.999`는 세 번째 arm이 아니라 이미 terminal인
`1.000`을 제외하기 위한 공통 상한이다. 7일 안에 Bundesliga가 관측되지 않거나 unique event가
부족하면 승자를 고르지 않는다.

White replay에서 `0.80` 이상 stop은 최종 승자를 여러 번 잘못 잘랐고, `0.70`이 그중 가장 덜
해로웠다. 실제 패배 event는 threshold 부근에서 다음 실행 가능한 bid 약 `0.27`까지 gap이
발생했다. 따라서 `0.70`은 보장 가격이 아니라 **trigger**다.

## Arm

| arm | Jenkins | runtime job | 진입 band |
|---|---|---|---:|
| Cat | `polybot-cat` | `watermelon-live-cat-98-1m-v2c` | `[0.98, 0.999]` |
| Dog | `polybot-dog` | `watermelon-live-dog-99-1m-v2c` | `[0.99, 0.999]` |

threshold 외 universe, notional, cadence, entry/exit, exposure, clock은 같다. wallet 차이는
무작위 배정이 아니므로 결과는 job/account별로도 보고한다.

## Cadence 선택

같은 `condition × token × threshold`를 비교했을 때 Grey 5분의 episode key 11개는 모두
White 1분에도 있었고 White에는 추가 8개가 있었다. paired entry 시각 차이 p95는 약
1,109초였으며, 5분 job은 EPL 손실 episode와 0.99 episode를 놓쳤다. Cat/Dog cycle은 약
7초라 1분 timer 안에서 충분히 종료된다. 따라서 시험한 두 cadence 중 coverage가 완전한
1분을 두 live arm에 공통 적용한다. 이 선택은 더 많은 진입과 더 빠른 stop 관측을 위한
운영 선택이며, 1분이 fee-net 수익을 최대화한다는 판정은 아니다. White/Grey는 계속 1분/5분
pair로 남아 이 차이를 prospective하게 측정한다.

## Universe와 identity

Gamma `/events/keyset`을 `closed=false`, `live=true`, soccer tag `100350`,
`related_tags=false`로 cursor-complete하게 읽는다. 최대 4페이지 안에 terminal cursor가 없거나
cursor가 반복되면 cycle 전체를 실패시킨다.

다음 numeric identity가 모두 맞는 리그만 허용한다.

| league | sport id | primary tag | series id |
|---|---:|---:|---:|
| EPL | 2 | 306 | 10188 |
| Bundesliga | 7 | 1494 | 10194 |
| Ligue 1 | 11 | 102070 | 10195 |
| LaLiga | 3 | 780 | 10193 |
| MLS | 33 | 100100 | 10189 |

e-sports tag, child/halftime event, 비축구, parent event, 비명시적 live 상태를 거부한다. market은
`sportsMarketType=moneyline`, `[Yes, No]`, `negRisk=true`, 서로 다른 두 token, active/open,
order-book enabled/accepting이어야 한다. event의 두 team identity와 `groupItemTitle`을 대조해
HOME/DRAW/AWAY를 결정하며 NO token은 거래하지 않는다.

Gamma `endDate`를 경기 종료시각으로 가정하지 않는다. `startTime`/`gameStartTime`을 기준으로
경기 시작 후 `[0h,4h]`만 허용한다.

## Entry와 exposure

1. 대상 YES token의 full displayed book을 batch로 읽는다.
2. ask level을 실제 가격 순으로 walk해 정확히 `$5`를 모두 소진할 수 있을 때 shares·VWAP·
   최종 소비 ask를 계산한다.
3. arm band의 첫 관측만 append-only `entry_episodes`로 claim한다.
4. 한 event에서 여러 result가 동시에 threshold를 넘으면 identity/market anomaly로 보고 event
   전체를 fail closed한다.
5. DB open state와 trade에 연결되지 않은 unresolved live BUY intent를 합산해 capacity 최대
   20을 확인한다. event당 1, cycle당 신규 최대 20이다.
6. 주문 직전 exact `$5` walk와 in-play clock을 다시 검증한다.
7. 저장된 Gamma fee rate/exponent/taker-only와 CLOB v2 market-info의 condition/token/fee
   identity가 완전히 일치하는지 확인한다. 누락·불일치면 주문 전에 실패한다.
8. venue tick에 맞춘 marketable FOK BUY를 제출한다. accepted 응답만으로 HOLDING으로 바꾸지
   않고 exact order/fill ledger가 terminal executed fill을 대사해야 한다.

20개 한도는 현재 보유 수가 아니라 최대 동시 open request notional `$100`의 safety cap이다.
한 경기당 한 건이므로 하루 경기 수를 임의로 제한하지 않는다.

Phase 1 뒤 `PENDING_BUY` 또는 `PENDING_SELL`이 남아 있거나 SELL intent/fill 대사 gap이 있으면
그 cycle의 신규 BUY를 전부 차단한다. 단, Gamma/CLOB 후보 scan은 계속 수행해 “안전장치가
막은 것”과 “조건에 맞는 시장이 없었던 것”을 분리한다. 불확실한 BUY intent는 token/side
격리를 유지하면서 capacity 한 칸을 예약하고 운영자가 증거로 해제하기 전 삭제하지 않는다.

하루 한 번의 membership detail checkpoint는 qualified뿐 아니라 excluded condition도 저장한다.
classifier 제외 reason에는 bounded source sport code/status를 포함하므로, 후보 0건이 실제로
허용 리그 경기 부재인지 frozen identity drift인지 사후 검증할 수 있다.

## Stop과 resolution

HOLDING마다 전체 보유 shares를 fresh order book의 bid depth에 걸어 본다. best bid가 `0.70`
초과면 보유한다. `0.70` 이하면 전체 shares를 소진하는 데 필요한 가장 낮은 bid를 limit으로
FOK SELL한다. 일부 수량만 임의로 팔지 않으며 full depth가 없으면 주문하지 않는다.

1분 polling에서도 trigger와 주문 사이, 또는 두 cycle 사이에 gap이 생길 수 있다. stop은
`0.70` 체결을 보장하지 않는다. 손절 속도를 보장하려면 이 실험과 별도로 장기 실행 daemon이나
venue-native order 지원 여부를 설계해야 한다.

stop SELL은 exact full BUY/SELL fill과 fee를 모두 대사한 뒤에만 `COMPLETED`와 realized P&L을
기록한다. FOK zero-fill은 `HOLDING`으로 되돌려 다음 cycle에 다시 평가한다. 부분 수량으로 줄여
재시도해 PENDING_SELL을 만드는 경로는 사용하지 않는다.

CLOB v2의 signed order/trade payload에 남은 legacy `fee_rate_bps=0`은 taker zero-fee 증거가
아니다. exact authenticated fill의 maker/taker role·size·price와 해당 token의 동적 CLOB fee
schedule로 5-decimal fee amount를 계산해 명시적으로 저장한다. Gamma와 CLOB schedule이
다르거나 fee evidence가 불완전하면 fee-net 성과와 lifecycle 종결을 fail closed한다.

book이 사라지면 Gamma exact one-hot payout을 먼저 확인하고, 부족하면 CLOB exact condition의
closed two-token unique winner와 exact `0/1`을 확인한다. confirmed BUY fill이 있는 bot-owned
trade만 `RESOLVED`로 기록하며 synthetic SELL/redeem이나 wallet-wide mutation은 하지 않는다.

## 판정

- 24시간: cadence, cursor completion, league/result identity, exact-book coverage, DB integrity,
  order/fill reconciliation만 점검
- 7일 entry 종료: arm별 unique event, threshold opportunity와 실제 entry/fill coverage,
  stop gap/depth, fee coverage를 점검. 표본이 작으면 연장 또는 미판정
- follow-up 종료: event-clustered fee-net interval, loss tail, stop execution shortfall을 arm별·
  league별로 비교
- CRITICAL/HIGH evidence gap, mixed source digest/config cohort, 수동 position 혼입이 있으면
  수익성·scale-up 판단 중단
- 사용자가 기대한 계정 잔고 증가는 목표일 뿐 보장값이나 promotion gate가 아니다.

실험 clock은 entry `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`, follow-up
`2026-09-07T13:00:00Z`다. cohort key는
`config_hash × strategy_source_digest × mode × job_name`이며 Git commit은 provenance다.
