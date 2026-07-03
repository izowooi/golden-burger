# Golden Orange — Fear Spike Fade (공포 스파이크 페이드)

## 1. 한 줄 요약

평시 확률 15% 이하의 tail 시장이 무서운 헤드라인에 +10%p 이상 급등한 뒤 **스파이크가 멈추면 NO를 매수**해, 공포가 만든 프리미엄이 감쇠(YES 되돌림)하는 것을 수확한다.

**노리는 심리 편향**: probability neglect(확률 무시) + availability cascade(가용성 폭포).

## 2. 왜 이 전략인가

### 2-1. 심리학적 근거: 대중은 확률이 아니라 '끔찍함'에 반응한다

- **Probability neglect** — Cass Sunstein, *"Probability Neglect: Emotions, Worst Cases, and Law"* (Yale Law Journal, 2002) 및 *"Terrorism and Probability Neglect"* (Journal of Risk and Uncertainty, 2003). 결과가 감정적으로 강렬할수록(핵, 테러, 전쟁) 사람은 **발생 확률의 크기를 판단에서 사실상 제거**하고 결과의 끔찍함 그 자체에 반응한다. 1%와 0.1%를 구분하지 못하고 같은 공포 가격을 지불한다.
- **Availability cascade** — Kuran & Sunstein이 정리한 메커니즘. 무서운 뉴스가 반복 노출될수록 그 사건이 "일어날 것 같다"는 체감이 자기강화적으로 증폭된다. 예측시장에서는 이것이 tail 시장 YES에 대한 '보험/복권' 매수세로 나타난다.
- 이 편향의 결과: 공포 이벤트 직후 tail 시장 YES는 **정보가 정당화하는 수준 이상으로** 오버슈팅하고, 헤드라인의 감정적 강도가 식으면 되돌아온다.

### 2-2. 실측 사례 (공개 문헌)

- **Polymarket 핵폭발 시장**: CoinDesk (2026-03) 보도 — 지정학 긴장 헤드라인 구간에서 "2026년 내 핵무기 사용" 시장의 내재확률이 **19%까지 급등**. 전문가 추정 기저율(<1%)과의 괴리는 확률이 아니라 공포가 가격을 만들었음을 보여준다.
- **이란 휴전 시장**: polymarkets.co.il 전략 가이드에 기록된 사례 — 루머 헤드라인에 **8분 만에 35% → 68%로 급등**했다가 **2시간 내 58%에 안착**. 같은 가이드의 관찰: "**되돌림의 60%가 90~120분 내에 발생**". 본 전략의 90분 대기 + 45분 스톨 확인 파라미터는 이 관찰에서 직접 도출했다.
- **군중 편향의 구조적 실증**: arXiv 2602.19520 *"Decomposing Crowd Wisdom"* (Kalshi+Polymarket 2.92억 거래 분석) — 저확률 구간의 체계적 과대가격(favorite-longshot bias의 tail 쪽)은 개별 사건이 아니라 시장 구조 수준에서 반복 확인된다.

### 2-3. 독립 도출 선언

이 전략은 기존 골든 시리즈(apple/banana/cherry 및 date~lime)의 내부 데이터나 전략 문서에서 파생된 것이 **아니라**, 위의 공개 문헌(행동경제학 논문 + 시장 실측 기사)에서 독립적으로 도출되었다. 기존 봇과의 관계는 포지셔닝 구분뿐이다:

- **fig(Hope Crusher)**: 정적인 theta 수확 — 아무 일도 없는 롱샷의 시간 가치 소멸을 먹는다.
- **lime(Shock Follow)**: 거래량이 폭증하고 고점을 유지하는 '진짜 정보' 급등에 **편승**한다.
- **orange(Fear Spike Fade)**: 이벤트 직후의 **감정 과잉**만 노린다 — 스파이크가 멈춘 것을 확인한 뒤 반대편(NO)을 산다. lime이 편승하는 시장과 orange가 페이드하는 시장은 "고점 유지 여부"로 자연 분리되는 A/B 쌍이다.

## 3. 진입/청산 규칙 정밀 명세

### 진입 (모두 충족 → NO 토큰 매수)

| # | 조건 | 값 | 근거 |
|---|------|-----|------|
| 1 | 유동성 | liquidity >= $15,000 | 슬리피지/체결 가능성 |
| 2 | 해결까지 남은 시간 | hours_left >= 72h | 마감 임박 스파이크 = 진짜 정보일 가능성 배제 |
| 3 | 윈도우 유효성 | 7d 윈도우, >= 5포인트, 커버리지 >= 50% (백필 포함) | invalid면 진입 금지 (cold-start 폴백 금지) |
| 4 | 평시 확률 (base) | base = median(YES, [now-7d, now-6h]) <= 0.15 | 원래 tail 시장만 (최근 6h 제외로 스파이크 오염 방지) |
| 5 | 스파이크 | yes_now - base >= 0.10 AND yes_now <= 0.30 | +10%p 급등, 단 0.30 초과는 재평가된 수준일 수 있음 |
| 6 | 스파이크 경과 | 첫 threshold(base+0.10) 돌파 후 >= 90분 | 되돌림의 60%가 90~120분 내 발생 → 초기 과열 회피 |
| 7 | 스파이크 스톨 | 최근 45분 YES 신고가 없음 | 아직 오르는 중이면 페이드 금지 (떨어지는 칼날의 반대편) |
| 8 | 거래량 확인 | volume24h >= 2.0 x 윈도우 평균 | 공포가 실제 거래로 이어졌는지 (조용한 노이즈 배제) |

매수 대상: **NO 토큰** (NO 가격 ∈ [0.70, 0.95] 재검증 후 GTC limit @ midpoint).

### 청산 (우선순위 순, trailing 없음)

| 순위 | 조건 | 기준 | exit_reason |
|------|------|------|-------------|
| 1 | 손절 | NO P&L <= -10% (= YES가 계속 오름 = 진짜 정보) | `stop_loss` |
| 2 | **retrace 익절 (주 청산)** | 스냅샷 최신 YES <= base + 0.5 x (peak - base) | `retrace_target` |
| 3 | 익절 (보조) | NO >= buy x 1.08 (목표가 0.99 캡) | `take_profit` |
| 4 | 최대 보유 | 보유 72h 초과 (되돌림 실패 → 자본 회수) | `max_holding` |
| 5 | 시간 청산 | 해결까지 24h 미만 | `time_exit` |
| - | 해결 후 매도 불가 | end_date + 24h 경과 + midpoint 조회 불가 | `resolved_unredeemed` (EXPIRED) |

retrace 판정은 trade에 저장된 `base_price_at_buy`/`spike_peak_at_buy`와 스냅샷 최신 YES를 비교한다 (1 - NO midpoint 근사가 아니라 Phase 0 스냅샷의 YES를 그대로 사용 — 진입 시그널과 같은 단위).

## 4. 파라미터 / env var

| env | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_BASE_WINDOW_DAYS` | 7 | base 계산 윈도우 (일) |
| `POLYBOT_BASE_EXCLUDE_RECENT_HOURS` | 6 | base 계산에서 제외할 최근 시간 |
| `POLYBOT_BASE_MAX` | 0.15 | base 상한 (평시 tail 시장만) |
| `POLYBOT_JUMP_MIN` | 0.10 | 스파이크 최소 상승폭 (yes_now - base) |
| `POLYBOT_YES_MAX` | 0.30 | 스파이크 후 YES 상한 |
| `POLYBOT_SPIKE_WAIT_MINUTES` | 90 | 스파이크 시작 후 대기 (분) |
| `POLYBOT_STALL_WINDOW_MINUTES` | 45 | 신고가 부재 확인 윈도우 (분) |
| `POLYBOT_VOL_MULT_MIN` | 2.0 | 거래량 확인 배수 |
| `POLYBOT_RETRACE_RATIO` | 0.5 | retrace 익절 비율 |
| `POLYBOT_ENTRY_HOURS_MIN` | 72 | 진입 최소 잔여 시간 |
| `POLYBOT_MAX_HOLDING_HOURS` | 72 | 최대 보유 시간 |
| `POLYBOT_EXIT_HOURS` | 24 | 해결 전 청산 시간 |
| `POLYBOT_TAKE_PROFIT` | 0.08 | 보조 익절 % |
| `POLYBOT_STOP_LOSS` | -0.10 | 손절 % |
| `POLYBOT_MIN_LIQUIDITY` | 15000 | 최소 유동성 $ |
| 공통 | — | `POLYBOT_BUY_AMOUNT`(5.0), `POLYBOT_MIN_VOLUME_24H`(0), `POLYBOT_MAX_POSITIONS`(-1), `POLYBOT_REENTRY_COOLDOWN_HOURS`(24), `POLYBOT_HISTORY_BACKFILL`(true), `POLYBOT_EXCLUDED_CATEGORIES`(""), `LOG_LEVEL`(INFO) |

## 5. 이 전략이 실패하는 경우 (솔직한 리스크)

1. **봇은 '진짜 정보'와 '감정 스파이크'를 구분하지 못한다.** 스파이크가 실제 사건의 전조였다면(핵 실험 발표, 실제 개전) YES는 되돌아오지 않고 계속 오른다. **SL -10%가 유일한 방어선**이다 — 90분 대기·스톨·거래량 게이트는 확률을 낮출 뿐 제거하지 못한다.
2. **내부자 선행매매**: 전쟁·정치 이벤트 시장에는 정보 우위를 가진 참가자가 존재한다. '스파이크 후 스톨'이 내부자의 분할 매집 휴지기일 수 있다.
3. **연쇄 스파이크**: 위기 국면에서는 헤드라인이 연달아 나온다. 1차 스파이크를 페이드한 직후 2차 헤드라인이 오면 손절 연타를 맞는다 (base가 점차 올라 재진입은 자연 차단되지만, 첫 손절은 피할 수 없다).
4. **base 산출 왜곡**: 7일 중앙값은 저유동성 구간의 스냅샷 결손이나 이미 서서히 오르던 시장에서 실제 '평시'보다 높거나 낮게 잡힐 수 있다.
5. **지정가(passive) 체결 전제**: 실측 사례의 되돌림 수익은 스파이크 고원에서 NO를 실제로 살 수 있어야 실현된다. 급변 구간의 스프레드 확대는 midpoint 체결 가정을 낙관적으로 만든다.
6. **표본 희소성**: 조건(base<=15%, +10%p, 72h+)을 모두 만족하는 이벤트는 드물다. 거래 빈도가 낮아 통계적 판단까지 오래 걸린다.

## 6. A/B 검증 방법

1. **시뮬레이션 (2주+)**: `uv run python main.py run --simulate --job orange-sim` 을 Jenkins 3~5분 간격으로. 진입 시그널 발생 빈도와 `reason` 분포 확인 (거의 없으면 `POLYBOT_JUMP_MIN` 완화 검토).
2. **소액 실전 (4주+)**: `POLYBOT_BUY_AMOUNT`를 소액(예: $10~50)으로 실행.
3. **판단 기준**: 4주 + 최소 30거래(이벤트 희소성으로 미달 시 8주로 연장) 후 —
   - 승률 >= 60% AND 평균 손익 > 0 → 증액 검토
   - exit_reason 분포: `retrace_target`이 주 수익원이어야 정상. `stop_loss` 비중 > 40%면 게이트(대기/스톨/거래량)가 진짜 정보를 못 거르는 것 → 전략 재검토
   - `max_holding` 비중이 높으면 되돌림 가설 자체가 약한 것
   - 모든 거래는 trades DB의 `base_price_at_buy`/`spike_peak_at_buy`/`vol_mult_at_buy`/`yes_price_at_exit` 컬럼으로 사후 분석 가능 (strategy_name='orange'로 교차 봇 UNION 쿼리).

## 7. 베리에이션 아이디어

- **A-1 (보수)**: `POLYBOT_SPIKE_WAIT_MINUTES=120`, `POLYBOT_VOL_MULT_MIN=3.0` — 더 확실한 스톨만. 빈도 감소, 승률 상승 기대.
- **A-2 (공격)**: `POLYBOT_JUMP_MIN=0.07`, `POLYBOT_BASE_MAX=0.20` — 작은 공포도 페이드. 빈도 증가, 노이즈 증가.
- **A-3 (깊은 되돌림)**: `POLYBOT_RETRACE_RATIO=0.3` — base 근처까지 기다려 더 크게 먹기. `max_holding` 청산 비중 증가 리스크.

## 8. 알려진 구현 한계

- **GTC limit @ midpoint 체결 가정**: 주문을 내면 체결된 것으로 간주한다 (cherry와의 A/B 비교를 위해 유지). 미체결/부분체결은 추적하지 않는다.
- **스냅샷 의존**: base/스파이크 시작/스톨 판정이 3~5분 간격 스냅샷 + `/prices-history` 백필에 의존한다. 백필 endpoint는 비공식이라 실패할 수 있고, 실패 시 "데이터 부족"으로 진입하지 않는다 (스냅샷 축적으로 자연 회복).
- **retrace 판정의 YES 가격**: 청산 사이클의 Phase 0 스냅샷 최신 YES를 쓴다. 스냅샷 저장과 청산 체크 사이의 수 초~수 분 지연 동안의 가격 변화는 반영되지 않는다.
- **스파이크 시작 = 첫 threshold 돌파**: 돌파 후 되돌아갔다가 재돌파한 경우에도 첫 돌파 시각을 쓴다 (경과 시간이 과대평가될 수 있으나, 보수적 방향은 아님).
- 거래량 게이트는 백필 포인트에 volume이 없어 실제 축적된 스냅샷이 있어야 통과된다 (cold start 직후엔 진입 불가 — 의도된 보수성).
