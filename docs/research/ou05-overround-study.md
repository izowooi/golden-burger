# 축구 O/U 0.5 시장의 생애 전체 overround 연구

작성 2026-10-05. 코드 `src/polylab/ou05/`(수집), `src/polylab/analysis/ou05.py`(분석), 대시보드 `/ou05`.

## 질문

축구 "합계 0.5골 Over/Under" 시장에서 양쪽 호가의 합이 1을 넘는 일이 잦다.
예: 알바니아–산마리노 Over ask 0.99, Under ask 0.07, 합 1.06.
이 overround가 시장이 열린 순간(Gamma `createdAt`, 킥오프 중앙값 약 13일 전, 최대 약 97일 전)부터 정산까지 어떻게 움직이는지 본다.
또 시장 규모(거래량·유동성)와 리그에 따라 어떻게 다른지, 그 기간에 Over 가격이 실제 0:0 빈도와 얼마나 맞는지도 본다.

## 먼저 알아야 할 사실 (2026-10-05 Mac mini 관측)

1. **Over와 Under 호가창은 서로의 거울이다.**
   - 열린 O/U 0.5 시장 933개 가운데 양쪽에 호가가 있는 894개 모두에서 `under_ask = 1 − over_bid`, `under_bid = 1 − over_ask`가 정확히 성립했다.
   - CLOB은 보완 토큰 주문을 서로 매칭(mint/merge)하므로 한쪽 주문이 다른 쪽 호가창에 그대로 나타난다.
   - 따라서 **overround = over_ask + under_ask − 1 = over_ask − over_bid = Over 토큰의 bid-ask 스프레드**이고, `sum_bid = 1 − 스프레드`다.
   - "한쪽이 비싸게 매겨졌다"가 아니라 **스프레드가 넓다**는 뜻이다. 예시의 1.06은 Over 0.93/0.99 호가창의 스프레드 6c다.
   - 수집기는 이 성질을 계속 감시한다. 거울이 깨지면 poll 결과의 `mirror_break`와 quality_events `ou05_mirror_break`에 남는다. 기대값은 0이다.
2. **시장 대부분은 거래가 거의 없다.**
   - 같은 시점에 열린 시장 894개 중 881개는 거래량이 100 USDC 미만이었다. 이들의 `sum_ask` 중앙값은 1.58로, 0.01/0.99 자리표시 호가창에 가깝다.
   - 거래량 100~1만 USDC 시장의 중앙값은 1.02~1.03이었다.
   - 모든 시장을 한데 모은 수치는 죽은 호가창을 재는 셈이므로, 모든 지표를 **리그 등급 × 거래량 등급**으로 나눠 본다.
3. **과거 bid/ask, 스프레드, overround는 복원할 수 없다.**
   - CLOB `prices-history`는 토큰별 가격 `p` 하나(중간가로 추정)만 준다. 호가, 잔량, 체결가는 없다.
   - 거래량 상위 4개 시장에서 Over와 Under의 `p`를 같은 분끼리 더하면 1.000이었다. 최대 편차는 0.015, 편차가 0.005를 넘는 분은 0.1% 미만이었다.
   - 즉 **과거 두 가격의 합은 overround 정보를 담지 않는다**. 과거로 복원되는 것은 Over 중간가(= 1 − Under 중간가) 시계열뿐이다.
   - 두 토큰의 history 시각은 27~65%만 겹친다. 그래서 토큰마다 따로 분 단위로 묶고, 점이 없는 분은 NULL로 둔다. 값을 이어 붙이지 않는다.
   - overround의 생애 추이는 **실시간 poll(2026-10-05~)로만** 쌓인다.
4. **Gamma API 제약**
   - `line` 필터가 서버에서 무시된다. 그래서 열린 totals 전체(약 84페이지, 8.3k 시장, 약 30~45초)를 받아 0.5만 남긴다.
   - 정산은 `outcomes` 라벨로 Over/Under를 판정한다. 인덱스 0을 Over로 가정하지 않는다.

## 정의

| 용어 | 정의 |
|---|---|
| sum_ask | over_ask + under_ask (level-1). 거울 성질 때문에 1 + Over 스프레드 |
| overround | sum_ask − 1 |
| sum_bid | over_bid + under_bid (= 1 − 스프레드) |
| spread | over_ask − over_bid |
| Over 가격 | 스프레드 ≤ 0.10인 poll 중간가(Polymarket 표시 규칙과 같다). 없으면 history 중간가 |
| 킥오프까지 분 | (최신 gameStartTime − ts)/60. 연기된 경기는 최신 킥오프 기준으로 다시 계산한다 |
| 경기 중 구간 | 킥오프 후 **벽시계** 분(0–15′ … 120–180′, 하프타임 약 15분 포함), 180′ 이후는 post |
| 리그 등급 | major = `MAJOR_SOCCER_LEAGUES`(EPL·La Liga·Bundesliga·Serie A·Ligue 1·MLS·UCL·UEL·WC·Euro·UNL), 그 외 other |
| 거래량 등급 | 시장의 최신 Gamma volume(종료 시장은 최종값): < 1천, 1천–1만, ≥ 1만 USDC |

## 수집

- `polylab ou05 discover`(매시)
  - 열린 soccer totals에서 line 0.5만 골라 `data/ou05/registry.db` `ou05_markets`에 생성 시점부터 등록한다(모든 리그).
  - 매 실행마다 킥오프·거래량·유동성을 갱신한다. 거래량·유동성 이력은 `ou05_metrics`에 쌓는다.
  - 닫힌 시장의 정산(`resolved_over`)과 최종 스코어를 반영한다.
  - core.db에는 쓰지 않는다. 그곳에 등록하면 tick poll과 전략 대상에 끌려 들어간다.
- `polylab ou05 poll`(매분)
  - `POST /books`(500토큰씩 병렬)로 양쪽 L1 호가를 받는다. 1.8k 토큰 기준 실측 0.9~1.8초가 걸렸다.
  - 호가 필드(bid/ask/L1 잔량/last)가 직전 저장 행과 다를 때, 또는 직전 행이 10분 이상 지났을 때(heartbeat) 1행을 쓴다.
  - 따라서 10분을 넘는 공백은 "변화 없음"이 아니라 **결측**이다. poll 실행 간격이 3분을 넘으면 `ou05_poll_gap`에 남는다.
  - 킥오프 후 24시간이 지나도 닫히지 않은 시장은 poll을 멈추고 `ou05_stale_open`에 남긴다.
- `polylab ou05 backfill`(매시, discover 뒤 25분 예산)
  - 2026-02-01 이후 닫힌 O/U 0.5 시장과 열린 시장의 생성 이전 구간이 대상이다. 주요 리그를 먼저, 최근 경기부터 처리한다.
  - `batch-prices-history` fidelity=1로 두 토큰을 가져온다(15일 창). `source='history'` 행에는 over_mid/under_mid만 넣고 나머지는 NULL로 둔다.
  - 같은 변화-또는-10분 규칙을 적용하며, 같은 분에 poll 행이 있으면 poll 행을 우선한다.

저장 테이블: `data/ou05/YYYY-MM.db` `ou05_quotes`(PK condition_id, ts). 열 정의는 `src/polylab/ou05/store.py`에 있다.

## 지표 (`polylab analyze ou05`, 대시보드 `/ou05`)

1. 킥오프까지 시간 구간(30일+ … 0–15분 전, 경기 중, post) × 등급별 `sum_ask`·`sum_bid`·스프레드의 **시간가중** p10/p25/p50/p75/p90과 양쪽 호가 존재 비율.
   - 한 행은 같은 시장의 다음 행까지 유효하다(최대 10분). 다음 행이 13분(heartbeat + poll 몇 회 누락) 넘게 없으면 그 행은 60초만 인정한다.
   - 변화 시에만 저장하므로 단순 행 평균을 내면 변동이 잦은 구간이 과대 반영된다.
2. sum_ask 분포(킥오프 전 전체 / 마지막 24시간 / 경기 중) × 등급.
3. 리그 비교(시장 5개 이상): 킥오프 전·마지막 1시간·경기 중 sum_ask 사분위.
4. 거래량·유동성과의 관계: 시장별로 킥오프 전 24시간 sum_ask 중앙값을 구해 거래량·유동성 구간별 사분위와 Spearman ρ를 낸다.
5. Over 가격 보정: 정산된 시장만 대상으로 구간별 시간가중 Over 가격과 실제 Over 비율을 비교한다(Wilson 95% CI, `nil_rate` = 0:0 비율).
   - 경기 중 구간에는 이미 득점한 경기(가격 ≈ 1)가 섞여 있다.
   - 생성 직후 가격은 빈 호가창(0.01/0.99)의 자리표시 중간가 0.5일 수 있다. 정확히 0.500인 과거 중간가는 가격에서 뺀다. 그래도 수일 전 구간은 호가가 얇아 과거 중간가를 확률로 읽기 어렵다(history에는 스프레드가 없다).
6. 시장 브라우저: 최근·임박 경기와 30일 내 거래량 상위 시장의 생애 전체를 보여준다. Over/Under bid·ask, 구간 최대 sum_ask, history 중간가, 킥오프, 득점(core `game_states`가 있는 경기만)을 담는다.
7. 논문용 원자료: `<research_dir>/ou05/parquet/`. `polylab analyze ou05`(기본, export 포함)가 바뀐 shard만 다시 쓴다. 매시 잡은 `--no-export`로 집계만 한다.

집계는 매시 `polylab-ou05-discover` 잡 끝에서 계산하고, 5분 주기 `polylab publish`는 바뀐 파일만 올린다(계산하지 않는다).

## 해석할 때 주의

- overround는 스프레드이므로 "Over/Under 어느 쪽이 비싼가"는 overround가 아니라 **Over 중간가와 실제 0:0 빈도의 차이**(지표 5)로 본다.
- 사려는 사람이 실제로 내는 비용은 ask 쪽 절반 스프레드다. 즉 중간가 대비 (sum_ask − 1)/2.
- 경기 중 첫 득점 이후 Over는 사실상 1로 확정되고 호가창은 한쪽만 남는다. 경기 중 구간의 양쪽 호가 존재 비율이 이를 보여준다.
- 시장별 분 단위 관측은 강하게 자기상관한다. 신뢰구간은 시장 단위로 해석한다(보정은 시장×구간당 관측 1개).
