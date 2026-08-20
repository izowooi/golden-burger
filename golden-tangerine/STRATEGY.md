# Golden Tangerine — Sports Resolution Hold Live A/B

## 검정 질문

Gamma 종료시각까지 6시간 이내인 고유동성 sports strict-binary market에서, 한 outcome을
정확한 `$5` ask VWAP 0.92대 또는 0.94대에 처음 살 수 있을 때 resolution까지 보유하면
실제 confirmed fill 비용 후 양의 기대값이 있는가?

Golden Black이 같은 universe와 네 가지 가상 stop 경로를 상세 수집한다. Tangerine은 stop
값을 아직 선택하지 않고 primary `HOLD_TO_RESOLUTION` 경로만 실제 최소 금액으로 검증한다.

| arm | Jenkins | runtime job | exact `$5` entry VWAP |
|---|---|---|---:|
| A | `polybot-orange` | `tangerine-live-a-94` | `[0.94,0.95]` |
| B | `polybot-fox` | `tangerine-live-b-92` | `[0.92,0.93]` |

threshold 외 차이는 허용하지 않는다. wallet/signature 종류는 계정 고유 속성이므로 기존
Jenkins 값을 그대로 보존하되 분석은 account/job별 cohort로 분리한다.

## Universe와 clock

Gamma `/events/keyset`에 `tag_slug=sports`, open event, liquidity `10,000`, cumulative volume
`5,000`, `end_date_min=now`, `end_date_max=now+6h`를 서버 필터로 전달한다. page size 500,
최대 4페이지이며 terminal cursor가 없으면 일부 시장으로 거래하지 않고 cycle 전체를 실패시킨다.
각 nested market은 active/open/orderbook/accepting, explicit `negRisk=false`, exact
`[Yes,No]`와 두 token, liquidity/volume/endDate를 다시 검증한다.

`endDate`는 반드시 실제 경기 종료시각이라는 뜻은 아니다. `gameStartTime`과 다른 clock일 수
있으므로 “경기 종료 6시간 전”이 아니라 **Gamma endDate 6시간 이내 sports cohort**라고
보고한다. Golden Black은 후속 clock strata 분석을 위해 더 상세한 metadata를 보존한다.

## Entry와 execution

1. 두 outcome token의 full displayed book을 batch로 가져온다.
2. 각 ask level을 실제 순서로 걸어 정확히 `$5`를 소진할 수 있을 때만 shares와 VWAP를 계산한다.
3. 현재 job의 arm band에 들어온 token의 첫 관측을 `entry_episodes`에 영구 기록한다. 주문이
   후속 gate에서 거절되어도 같은 token의 band 체류/재진입을 새 신호로 재사용하지 않는다.
4. 총 open state 3, event open state 1, cycle 신규 1을 확인한다.
5. 주문 직전 full book을 다시 walk한다. VWAP가 band를 벗어나거나 `$5` depth가 없으면 중단한다.
6. 최고 소비 ask를 venue별 tick에 맞춰 위로 정렬한 FOK BUY를 제출한다. 전량 즉시 체결되지
   않으면 잔여 GTC 주문을 남기지 않는다.
7. live trade는 CLOB execution ledger의 exact terminal confirmed fill이 확인될 때만
   `PENDING_BUY`에서 `HOLDING`으로 이동한다.

## Exit와 수동 포지션 경계

이번 cohort에는 pre-resolution SELL이 없다. midpoint 하락만으로 stop/TP를 제출하지 않고,
Gamma closed final one-hot payout을 확인한 뒤 own trade를 `RESOLVED`로 기록한다. live 손익은
confirmed BUY size/VWAP/fee에서 계산한 settlement assumption으로 분리하며, synthetic SELL이나
requested-order P&L을 realized P&L로 기록하지 않는다.

봇은 자기 runtime DB의 open trade만 순회한다. wallet 전체 position을 DB에 import하거나
account-wide cancel/redeem/wind-down을 실행하지 않으므로 사용자가 수동 매수한 잔여 포지션은
대상이 아니다.

## 안전 한도와 반증 조건

- 주문 금액 정확히 `$5`; account당 open request notional 최대 `$15`
- event당 1개, cycle당 신규 1개, token 재진입 cooldown 720시간
- unknown/malformed book, cursor, outcome identity, endDate, fill, fee, resolution은 추정하지 않음
- concurrent Jenkins build, clean build, DB 교체 금지
- A/B 중 한쪽 설정이 threshold 외에 달라지면 해당 구간은 비교 cohort에서 제외
- 24시간과 7일 점검은 collection/execution health만 판단하고 threshold를 바꾸지 않음
- 30일 entry window와 resolution follow-up이 끝나기 전 수익 승자를 선택하지 않음
- 최종 판정에는 arm별 exact confirmed fill/fee coverage 100%, resolution coverage 90% 이상,
  충분한 unique event와 event-cluster 비용 후 interval이 필요하다. 표본이 적으면 미판정이다.

이 live pilot은 운영자의 명시적 승인에 따라 research-only collector의 최종 승격 gate보다 먼저
최소 금액으로 시작한다. 따라서 결과는 low-notional prospective pilot evidence이며, 규모 확대
근거가 되려면 별도 untouched confirmatory cohort가 필요하다.
