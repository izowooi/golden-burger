# Golden Peach

경기 시작 직후 축구 3-way 시장의 직접 YES/NO 6개 토큰을 같은 시각에 비교해 가장 높은
실행 가능 가격 하나를 exact `$5`로 한 번만 매수하는 live A/B + shadow 전략이다.

## 실행 배치

| Jenkins | runtime | 역할 | TP | SL |
|---|---|---|---:|---:|
| `polybot-eco` | `peach-live-eco-3pp-1m-v1` | live A | `+0.03` | `-0.10` |
| `polybot-fruit` | `peach-live-fruit-5pp-1m-v1` | live B | `+0.05` | `-0.10` |
| `polybot-grey` | `peach-shadow-1m-v1` | simulation/raw 자료 | `+0.05` | `-0.10` |

세 job은 1분 cadence, T7 외장 workspace를 사용한다. A/B의 유일한 처리축은 TP 폭이다.

## 진입과 종료

1. EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL의 실제 in-play event만 읽는다.
2. source clock 0~10분, HOME/DRAW/AWAY triad, 직접 YES/NO 6개 full-depth book을 요구한다.
3. midpoint 선두와 2위가 최소 0.5%p 차이이고, 선두 exact `$5` ask VWAP이
   0.60~0.94이며 spread가 0.05 이하면 FOK BUY한다.
4. confirmed BUY VWAP 기준 arm TP 또는 공통 10%p SL에서 전체 보유량 FOK SELL을 시도한다.
5. source 80분부터는 절반 TP를 허용하고, 손실 중이면 새 stop 없이 proven resolution까지
   보유한다.
6. 한 event에서 실제 또는 불확실 BUY가 한 번 생기면 다시 들어가지 않는다. exact zero-fill은
   예외다.
7. 한 event의 명확한 주문 실패나 불명확한 BUY/SELL은 다른 event를 막지 않는다. 불명확 노출은
   포지션 한도 1칸을 예약하고 180분 뒤 event-local `QUARANTINED`로 전환해 계속 대사한다.

직접 NO는 별도 token/book으로 읽는다. 실시간 진입에서 `1-YES` 합성은 금지된다.

## 파라미터 근거와 한계

기존 Golden Watermelon White 1분/Grey 5분 자료에서 비교 가능한 35경기를 탐색했다. 과거
collector는 YES book만 저장했기 때문에 NO book을 보완적으로 합성해야 했고, 선택된 선두는
모두 NO였다. White 1분 primary의 시험 grid 평균은 모두 음수였고 Grey 5분 민감도에서 나온
한 개의 미세한 양수 조합도 합성 가격·소표본·다중 탐색 한계가 있다. 따라서 이 자료는 수익성 증명이
아니며, +3%p/+5%p와 공통 -10%p는 사용자의 작은 익절·넓은 손절 가설을 하나의 축으로
반증하기 위한 값이다. Grey는 이제 직접 NO book까지 저장하므로 향후 회고는 합성 가격을 쓰지
않는다.

상세 source 경계와 결과는
`research/frozen-2026-08-30-kickoff-leader-v1/HISTORICAL_REPLAY.md`에 기록한다.

## 로컬 실행

```bash
uv sync --frozen --extra dev
uv run pytest

# credential-free shadow
POLYBOT_TAKE_PROFIT_DELTA=0.05 \
  uv run polybot config --simulate --job peach-shadow-1m-v1
POLYBOT_TAKE_PROFIT_DELTA=0.05 \
  uv run polybot run --simulate --job peach-shadow-1m-v1

# daily-rsync verify를 통과한 Grey DB의 direct six-book TP/SL 재생
uv run python scripts/analyze_direct_book_grid.py \
  --db /absolute/path/to/verified/trades_sim.db \
  --output /absolute/path/to/analysis.json
```

Live는 반드시 Jenkins Credentials Binding과 명시적 `--live`를 사용한다. job/mode/TP가
사전 등록과 다르면 network와 DB mutation 전에 실패한다.

grid 분석기는 원본 DB를 read-only로 열고 exact `$5` full bid depth와 sports taker fee를
적용한다. 출력은 표시 호가 반사실이며 actual fill 또는 realized P&L로 해석하지 않는다.

## 검증 기간

- 신규 진입: `[2026-08-30T00:00:00Z, 2026-09-13T00:00:00Z)`
- resolution 후속 관측: `2026-09-20T00:00:00Z`까지

첫 24시간은 cadence, source clock, triad, direct book, DB·cohort·저장공간만 본다. 표본이
쌓이기 전 수익성이나 승자 arm을 선언하지 않는다.
