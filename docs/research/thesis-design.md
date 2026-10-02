# 연구 설계 — 경기 시간대별 과대/과소 평가와 이벤트 민감도

가제: *Time-Varying Mispricing in Live Sports Prediction Markets: Minute-Level Evidence from Polymarket*

## 연구 질문

| RQ | 질문 | 핵심 산출물 | 모듈 |
|---|---|---|---|
| RQ1 | 종목별로 경기 **단계(phase)** 마다 시장 가격은 실제 승률과 calibration 되어 있는가? 어느 가격대·단계에서 과소(가격<승률)/과대(가격>승률) 평가가 생기는가? | reliability diagram(종목×단계), calibration gap, Brier score | `analysis/calibration.py` |
| RQ2 | 같은 이벤트(득점·점수 변화)에 대한 가격 반응(민감도)은 경기 시간이 흐를수록 얼마나 커지는가? 그 반응은 과잉인가(되돌림) 과소인가(추가 drift)? | 분 버킷별 평균 점프, 1/5/10분 reversion, 이론 승률 변화 대비 과잉 비율 | `analysis/events.py` |
| RQ3 | RQ1·RQ2 의 편향을 이용하는 단순 규칙(고확률 favourite 보유, 경기 후반 진입, 중간대 leader 추세, resolution momentum)은 거래비용·체결 가능성(FOK·호가 깊이)을 반영해도 수익이 나는가? | 전략 변형별 CONFIRMED 손익, 백테스트 vs 실거래 괴리 | `analysis/performance.py`, `analysis/backtest.py` |
| RQ4 | 주문 단위(5·10·25·50·100 USDC)에 따라 체결 가능성·슬리피지·손익 안정성은 어떻게 달라지는가? | 단위별 ROI·표준편차·최대낙폭·FOK 성공률 | `analysis/performance.py` stake tiers |

## RQ2 의 역할 재정의 (2026-10-02 연구자 결정)

RQ2(같은 이벤트에 대한 민감도의 경기 시간별 증가)는 단독 가설보다 **실거래 수익화 논리의 근거**로 쓴다.

- 관찰: 경기 막판으로 갈수록 가격 변동이 폭증한다(`/explore` 스윙 분포: 5%p 이상 1분 변동 비율 NBA 0.4%→12.6%(마지막 10%)→17.9%(연장),
  MLB 6%→10.8%→14.1%; 축구 상위 1% 1분 변동 11pp→32.5pp→38pp(추가시간)).
- 실거래 증거: watermelon 이 0.92 에 산 아일랜드 승리 토큰이 13분 만에 0.17 로 급락했고, 1분 주기 손절은 stop 보다 0.42~0.48 낮게 체결됐다.
- 결론(전략 설계 원칙): 과대/과소 평가를 증명해도 막판 급락은 손절로 방어할 수 없다. 따라서 전략은 막판까지 보유하지 않고 **조기 익절**로 수익을 확정한다.
  논문에서는 "편향의 존재(RQ1)"와 "편향을 수익으로 바꾸는 실행 조건(RQ2·RQ3)"을 분리해 서술한다.
- 축구 득점 이벤트 표본은 실시간 수집(2026-09-30~)으로만 쌓이므로 축구 구간별 민감도는 표본이 충분해질 때까지 보조 결과로 둔다.

## 정의

- **가격** `p_t`: 해당 outcome token 의 1분 canonical 가격(poll midpoint > WS last trade > prices-history). 체결 분석은 호가 walk 가격을 쓴다.
- **결과** `y`: Gamma `outcomePrices` 가 "1" 인 outcome(정산 확인분만).
- **Calibration gap** `g = win_rate − mean(p)`: g>0 이면 **과소평가**(시장이 확률을 낮게 봄), g<0 이면 **과대평가**.
- **경기 단계**: 정규화 경기 분 `m`(sports WS clock; 없으면 시작 후 경과 분)을 종목별 정규 시간의 비율로 나눈다
  (pre: m<0, early: 0–33%, mid: 33–66%, late: 66–90%, final: >90%).
- **이벤트 반응**: 득점 시각 T 에서 득점 팀 moneyline 의 `Δp(T−1m → T+k)`, k∈{1,3,5,10}분, peak, reversion=`p(T+k)−p(peak)`.
  같은 점수차·같은 사전 확률 구간에서 분 버킷별로 비교해 "민감도 증가"를 추정한다.

## 데이터

- 2026-02-01 이후 종료 경기: prices-history(1분)로 백필(soccer ≈15k, MLB ≈2.7k, NBA ≈0.6k, NHL ≈0.55k, NFL ≈0.1k).
  과거분은 게임 상태(점수·시계)가 없으므로 RQ1(경과 시간 기준)에만 쓰고, RQ2 는 실시간 수집분(Sports WS)으로 한다.
- 실시간(2026-10-01~): 1분 poll(호가 top-10) + market WS + sports WS 원본 보존.
- 실거래: 전략 변형별 원장, CONFIRMED fill 만.

## 통계적 주의

- 한 경기 안의 분 단위 관측은 강하게 자기상관 → 경기 단위 cluster bootstrap 으로 CI 를 낸다.
- 여러 버킷 동시 검정 → Benjamini–Hochberg 로 보정한 결과를 함께 보고한다.
- 선택 편향: 볼륨 하한으로 걸러낸 마켓만 쓰므로 결과는 "유동성 있는 주요 시장"에 대한 것이다.
- Look-ahead 금지: 전략·백테스트는 시각 t 이전 데이터만 사용한다.
- 실거래 손익은 소표본이다. 단위별 비교는 기간·종목 구성 차이를 함께 보고한다.
