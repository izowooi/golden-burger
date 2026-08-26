# Golden Watermelon Live — In-Play Match Result Live A/B

## 검정 질문

6개 축구 리그의 경기 중 home/draw/away 결과 YES token이 매우 높은 가격에 도달했을 때,
exact `$5` FOK로 진입하고 `0.70` emergency stop 또는 proven resolution까지 관리하면 실제
confirmed fill·fee 이후 양의 기대값이 있는가?

## 선행 증거와 보수적 선택

v3a 종료 전 verified White/Grey DB에서 White 1분 cadence는 4,001 run, Grey 5분 cadence는
801 run이었고 각각 99.98%/100% coverage였다. White의 0.96 episode는 7건(6승 1패),
0.99는 1건(1승)이었다. 발렌시아–베티스의 승리 결과는 exact ask 0.97에서 0.96 arm에
포착됐지만 0.98/0.99 arm에는 없었다. 반대로 뉴캐슬–리버풀에서는 0.97 진입 직후 executable
bid가 약 0.27로 급락해 0.96의 tail risk도 확인됐다. 표본은 threshold 우열을 확정하기에 작다.

따라서 Cat `0.96`, Dog `0.99`는 신호량과 tail risk를 직접 비교하는 prospective pilot이다.
`0.999`는 세 번째 arm이 아니라 이미 terminal인
`1.000`을 제외하기 위한 공통 상한이다. 7일 안에 Bundesliga가 관측되지 않거나 unique event가
부족하면 승자를 고르지 않는다.
직전 v2f Cat의 `[0.98, 0.999]` arm은 immutable historical cohort이며 v2g 결과에 합산하지
않는다.

White replay에서 `0.80` 이상 stop은 최종 승자를 여러 번 잘못 잘랐고, `0.70`이 그중 가장 덜
해로웠다. 실제 패배 event는 threshold 부근에서 다음 실행 가능한 bid 약 `0.27`까지 gap이
발생했다. 따라서 `0.70`은 보장 가격이 아니라 **trigger**다.

## Arm

| arm | Jenkins | runtime job | 진입 band |
|---|---|---|---:|
| Cat | `polybot-cat` | `watermelon-live-cat-96-1m-v2g` | `[0.96, 0.999]` |
| Dog | `polybot-dog` | `watermelon-live-dog-99-1m-v2g` | `[0.99, 0.999]` |

threshold 외 universe, notional, cadence, entry/exit, exposure, clock은 같다. wallet 차이는
무작위 배정이 아니므로 결과는 job/account별로도 보고한다.

## Cadence 선택

같은 `condition × token × threshold`를 비교했을 때 Grey 5분의 episode key 15개는 모두
White 1분에도 있었고 White에는 총 26개가 있었다. paired entry 시각 차이 p95는 약
856초였으며, 5분 job은 일부 막판 episode를 놓쳤다. Cat/Dog cycle은 약
8초라 1분 timer 안에서 충분히 종료된다. 따라서 시험한 두 cadence 중 coverage가 완전한
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
| Serie A | 12 | 100618 | 10203 |

e-sports tag, child/halftime event, 비축구, parent event, 비명시적 live 상태를 거부한다. market은
`sportsMarketType=moneyline`, `[Yes, No]`, `negRisk=true`, 서로 다른 두 token, active/open,
order-book enabled/accepting이어야 한다. event의 두 team identity와 `groupItemTitle`을 대조해
HOME/DRAW/AWAY를 결정하며 NO token은 거래하지 않는다.

Gamma `endDate`를 경기 종료시각으로 가정하지 않는다. `startTime`/`gameStartTime`을 기준으로
경기 시작 후 `[0h,4h]`만 허용한다.
market description이 정규 90분과 stoppage time만 settlement에 포함한다고 명시해야 한다.
같은 description의 다른 절이 연장전·승부차기 포함을 말하면 모순으로 거절한다. 따라서
연장전과 승부차기는 이 moneyline payout에 포함되지 않으며, 범위가 누락되거나 다른 규칙이면
주문 전에 fail closed한다. `Draw No Bet`은 무승부 결과로 간주하지 않는다. 4시간 envelope는
정규시간·하프타임·stoppage time과 source 지연보다
넓으므로 발렌시아–베티스의 0.97 이후 관측이 끊긴 원인은 clock filter가 아니라 CLOB book
제거였다.

## Entry와 exposure

1. 대상 YES token의 full displayed book을 batch로 읽는다.
2. ask level을 실제 가격 순으로 walk해 정확히 `$5`를 모두 소진할 수 있을 때 shares·VWAP·
   최종 소비 ask를 계산한다.
3. arm band의 첫 관측만 append-only `entry_episodes`로 claim한다.
4. 한 event에서 여러 result가 동시에 threshold를 넘으면 identity/market anomaly로 보고 event
   전체를 fail closed한다.
5. `QUARANTINED`를 포함한 DB open state와 trade에 연결되지 않은 모든 live BUY intent를
   합산해 capacity 최대 20을 확인한다. 동일한 orphan reservation을 event당 1개 한도에도
   적용하며, cycle당 신규 최대 20이다.
6. 주문 직전 exact `$5` walk와 in-play clock을 다시 검증한다.
7. 저장된 Gamma fee rate/exponent/taker-only와 CLOB v2 market-info의 condition/token/fee
   identity가 완전히 일치하는지 확인한다. 누락·불일치면 주문 전에 실패한다.
8. venue tick에 맞춘 marketable FOK BUY를 제출한다. accepted 응답만으로 HOLDING으로 바꾸지
   않고 exact order/fill ledger가 terminal executed fill을 대사해야 한다.

매수 walk는 실행에 필요한 ask만 있으면 유효하고 bid 부재를 이유로 버리지 않는다. 반대로
stop walk는 실행에 필요한 bid만 있으면 유효하며 ask 부재를 이유로 버리지 않는다. 불확실한
BUY POST가 한 건이라도 발생하면 해당 cycle의 남은 BUY를 즉시 비활성화한다. terminal BUY
fill도 완전한 fee 증거가 없으면 `PENDING_BUY`에 유지하고 stop/resolution 관리로 넘기지 않는다.

Trade와 episode 연결은 validation·flush·commit 어느 단계에서든 실패하면 Session 전체를
rollback한다. 이후 실패 사유 annotation이 commit되더라도 unlinked ghost Trade가 함께
commit될 수 없다.

20개 한도는 현재 보유 수가 아니라 최대 동시 open request notional `$100`의 safety cap이다.
한 경기당 한 건이므로 하루 경기 수를 임의로 제한하지 않는다.

Phase 1 뒤 `PENDING_BUY`, `PENDING_SELL`, `QUARANTINED`, untracked BUY, open BUY fill/fee gap,
order reconciliation gap이 하나라도 남으면 그 cycle의 신규 BUY를 전부 차단한다. 단,
Gamma/CLOB 후보 scan은 계속 수행해 “안전장치가 막은 것”과 “조건에 맞는 시장이 없었던 것”을
분리한다. 불확실한 BUY intent는 token/side 격리와 capacity 예약을 유지한다. 열린 주문 목록에
없다는 사실만으로 해제하지 않으며, exact zero-fill/no-order 증거나 exact terminal fill과
완전한 fee/episode identity가 있어야 해제 또는 Trade 복구한다. orphan 복구는 current sweep이
갱신한 catalog의 condition/event와 exact `[Yes,No]` token alignment, entry snapshot outcome,
signed `$5` maker amount도 모두 일치해야 한다. 단, 동기 응답이 `FAILED`이고
order ID가 없으며 reconciliation도 필요하지 않다고 ledger가 증명한 명시적 거절은 실제
노출이 아니므로 capacity에서 즉시 제외한다. 해석 불가능한 응답, timeout, 5xx,
evidence-write failure는 이 예외에 포함하지 않는다.

첫 in-arm 관측은 guard나 fresh-book 재검증에서 주문되지 않아도 재시도하지 않는다. 대신
`entry_episodes.execution_state/reason`에 차단·거절 사유를 남겨 희소 신호와 운영 병목을
분리한다. 주문 성공 뒤 Trade와 episode link는 한 transaction으로 commit한다.

하루 한 번의 membership detail checkpoint는 qualified뿐 아니라 excluded condition도 저장한다.
classifier 제외 reason에는 bounded source sport code/status를 포함하므로, 후보 0건이 실제로
허용 리그 경기 부재인지 frozen identity drift인지 사후 검증할 수 있다.

## Stop과 resolution

HOLDING마다 SDK가 실제로 서명 가능한 소수 둘째 자리 내림 수량을 먼저 계산하고, 그 수량을
fresh order book의 bid depth에 걸어 본다. 원래 BUY fill과의 `0.01` share 미만 차이는 명시적
SDK dust로 남긴다. best bid가 `0.70`
초과면 보유한다. `0.70` 이하면 서명 가능 shares를 소진하는 데 필요한 가장 낮은 bid를 limit으로
FOK SELL한다. 일부 수량만 임의로 팔지 않으며 full depth가 없으면 주문하지 않는다.

Gamma keyset은 페이지당 connect/read `2s/5s`, 최대 4페이지, in-process retry 0회로
fail-fast한다. 429의 60초 `Retry-After`를 기다리지 않고 다음 1분 Jenkins cycle을 retry로 쓴다.

1분 polling에서도 trigger와 주문 사이, 또는 두 cycle 사이에 gap이 생길 수 있다. stop은
`0.70` 체결을 보장하지 않는다. 손절 속도를 보장하려면 이 실험과 별도로 장기 실행 daemon이나
venue-native order 지원 여부를 설계해야 한다.

stop SELL은 exact BUY/SELL fill과 fee를 모두 대사한 뒤에만 `COMPLETED`와 realized P&L을
기록한다. FOK zero-fill은 `HOLDING`으로 되돌려 다음 cycle에 다시 평가한다. SDK가 SELL
share를 소수 둘째 자리로 내림해 만든 `0.01` share 미만 잔여는 명시적으로 기록하고 팔린
부분의 P&L에서 제외한다. 그 이상 수량 차이는 `PENDING_SELL`을 유지하며 fail closed한다.

CLOB v2의 signed order/trade payload에 남은 legacy `fee_rate_bps=0`은 taker zero-fee 증거가
아니다. exact authenticated fill의 maker/taker role·size·price와 해당 token의 동적 CLOB fee
schedule로 5-decimal fee amount를 계산해 명시적으로 저장한다. Gamma와 CLOB schedule이
다르거나 fee evidence가 불완전하면 fee-net 성과와 lifecycle 종결을 fail closed한다.

book이 사라지면 Gamma exact one-hot payout을 먼저 확인하고, 부족하면 CLOB exact condition의
closed two-token unique winner와 exact `0/1`을 확인한다. `0.5/0.5`는 resolution이 아니다.
condition/token/outcome identity와 terminal BUY fill/fee가 모두 맞는 bot-owned trade만
`RESOLVED`로 기록하며 synthetic SELL/redeem이나 wallet-wide mutation은 하지 않는다.

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

실험 clock은 entry `[2026-08-26T15:00:00Z, 2026-09-02T15:00:00Z)`, follow-up
`2026-09-09T15:00:00Z`다. cohort key는
`config_hash × strategy_source_digest × mode × job_name`이며 Git commit은 provenance다.
