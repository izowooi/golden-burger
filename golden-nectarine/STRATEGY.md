# Golden Nectarine — Bottom Fisher (롤링 최저가 역추세)

## 1. 한 줄 요약

**장기(30일+) tail~중간 구간 시장에서 YES 가격이 20일 롤링 최저가 이하로 떨어지면 매수하고, 보유 120시간(5일)이 지나면 손익과 무관하게 무조건 청산한다.**

노리는 심리 편향: **손실 회피(loss aversion)發 투매 과잉(다운 오버슈트)**. 얇은 장기 시장에서 패닉/노이즈 매도가 가격을 펀더멘털 이하로 일시적으로 누르고, 며칠 안에 평균 회귀한다.

## 2. 왜 이 전략인가

### 2.1 공개 백테스트 규칙의 시간별 근사

이 전략은 공개된 계량 백테스트의 **X=20/Y=5 규칙을 가져온 시간별 가격 근사**다. CLOB `fidelity=60` 시계열은 원문의 daily-close와 체결 표본을 정확히 재현하지 않으므로 직접 복제라고 부르지 않는다.

> QuantPedia (2026-04), **"Exploiting Mean-Reversion in Decentralized Prediction Markets"**:
> Polymarket 바이너리 계약 전수 데이터에서 "X일 롤링 최저가에서 매수 → Y일 후 청산" 규칙을
> 그리드 서치한 결과, **X=20 / Y=5 조합이 거래비용 10bps 반영 후에도 CAR +18.9~22.1%로 생존**했다.

지금까지 검토한 공개 문헌 중 Polymarket에서 **거래비용 반영 후 생존한 유일한 규칙 기반 백테스트**다. 골든 시리즈의 다른 봇들(cherry의 마감 수렴, elderberry의 패닉 페이드 등)이 심리 가설에서 출발해 파라미터를 설계했다면, nectarine은 반대로 **데이터에서 생존한 규칙을 가져와 심리적 해석을 붙인다**. 이 전략은 사용자의 기존 골든 시리즈 전략과 **독립적으로, 공개 문헌·백테스트 근거에서 도출**되었다.

### 2.2 심리적 메커니즘: 왜 신저가가 사는 자리인가

- **손실 회피**: Kahneman-Tversky의 전망이론이 보여주듯 손실의 심리 가중치는 이득의 약 2배다. 보유자는 하락하는 tail 포지션을 "더 떨어지기 전에" 던지고, 얇은 호가에서 이 투매는 가격을 실제 확률 이하로 오버슈트시킨다.
- **얇은 장기 시장의 구조**: 마감이 먼 시장은 관심(attention)과 유동성이 적다. 소수의 매도만으로 신저가가 만들어지고, 정정할 차익거래꾼도 자본 잠김(장기 락업) 때문에 느리게 들어온다 — Scott Alexander의 "Prediction Market FAQ"가 지적하는 장기 시장의 만성적 가격 압축·왜곡과 같은 구조다.
- **평균 회귀의 시간 스케일**: 레포 리서치 문서의 실측("10%+ 급변은 mean-revert")과 QuantPedia의 Y=5일 최적값이 일치한다. 오버슈트는 며칠 단위로 복원된다. 그래서 청산은 가격 목표가 아니라 **달력(5일)** 이다.

### 2.3 기존 봇에서 배운 것 (공통 개선 반영)

- banana의 "스냅샷 개수 기반 윈도우" 버그 → **timestamp 기반 윈도우 + 유효성 검증** (invalid면 진입 금지, 관대한 cold-start 폴백 금지).
- 20일 룩백은 스냅샷 축적만으로는 못 채운다 → **CLOB `/prices-history` 백필(fidelity=60)이 이 전략의 생명선**. 백필 실패 시 "데이터 부족"으로 취급하고 진입하지 않는다.
- cherry의 영구 one-shot/rapid_jump 밴 → **쿨다운 기반 재진입**. 단, 롤링 최저가 부근에서는 매 사이클이 신저가라 연속 재진입이 쉬우므로 쿨다운을 **168h(7일)** 로 길게 잡는다.
- 해결된 시장 좀비 HOLDING → EXPIRED 마감 처리. midpoint 0 투매 방지 가드.

### 2.4 왜 장기(30일+) 시장만인가

이론 근거가 아니라 **오염 차단 장치**다. 마감이 가까운 시장은 시간 가치(theta) 소멸만으로도 YES tail 가격이 단조 하락해 "매일이 신저가"가 된다 — 이건 평균 회귀가 아니라 정당한 감쇠다. `hours_left >= 720`(30일)은 룩백 20일 동안 theta 감쇠가 신저가를 계속 만드는 시장을 후보에서 제거한다.

## 3. 진입/청산 규칙 정밀 명세

### 진입 (모두 충족, YES 토큰 기준 가격 p — YES 매수 고정, 시간별 근사)

| # | 조건 | 값 | 이유 |
|---|------|----|----|
| 1 | 유동성 | liquidity >= $10,000 | 최소 체결 가능성 (백테스트 대상도 저유동 포함이라 낮게) |
| 2 | 남은 시간 | hours_left >= 720h (30일) | theta 감쇠發 가짜 신저가 차단 (§2.4) |
| 3 | 가격 밴드 | p ∈ [0.03, 0.50] | tail~중간 구간. 0.03 미만은 붕괴/해결 임박 노이즈 |
| 4 | 신저가 | p <= min(지난 20일, **최근 24h 제외** 구간의 최저가), 동률 허용 | 백테스트의 X=20 규칙. 최근 24h 제외는 진행 중 하락 자체를 기준선에서 배제 |
| 5 | 윈도우 유효성 | 포인트 >= 20 AND 커버리지 >= 19일(20일의 95%) — `fidelity=60` 백필 포함 | invalid면 진입 금지. daily-close 직접 복제 아님 |
| 6 | 재진입 쿨다운 | 168h (HOLDING/청산/skip 이후) | 최저가 부근 연속 재진입 방지 |

### 청산 (우선순위 순, trailing 없음)

| 우선순위 | 조건 | exit_reason | 성격 |
|---------|------|-------------|------|
| 1 | **보유 120h 경과 — 손익 무관 무조건 청산** | `max_holding` | **주 청산 경로** (백테스트 Y=5 복제) |
| 2 | P&L <= -30% | `stop_loss` | 안전판 (백테스트에 없는 방어 장치) |
| 3 | P&L >= +30% (목표가 0.99 캡) | `take_profit` | 조기 행운 익절 안전판 |
| 4 | 해결까지 < 24h | `time_exit` | 30일+ 진입이라 드문 경로 |
| - | midpoint 조회 불가 + 해결 24h 경과 | `resolved_unredeemed` (EXPIRED) | 수동 redeem 필요 |

## 4. 파라미터 · env var 표

| env | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_LOOKBACK_DAYS` | 20 | 롤링 최저가 룩백 (백테스트 X) |
| `POLYBOT_EXCLUDE_RECENT_HOURS` | 24 | 최저가 산출 시 제외할 최근 구간 |
| `POLYBOT_HOLD_HOURS` | 120 | calendar exit 보유 시간 (백테스트 Y=5일) |
| `POLYBOT_PROB_MIN` | 0.03 | 진입 YES 가격 하한 |
| `POLYBOT_PROB_MAX` | 0.50 | 진입 YES 가격 상한 |
| `POLYBOT_ENTRY_HOURS_MIN` | 720 | 해결까지 최소 시간 (30일) |
| `POLYBOT_EXIT_HOURS` | 24 | 해결 임박 청산 |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 168 | 재진입 쿨다운 (7일) |
| `POLYBOT_TAKE_PROFIT` | 0.30 | 익절 안전판 |
| `POLYBOT_STOP_LOSS` | -0.30 | 손절 안전판 |
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 10000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 (0 = 비활성 — 얇은 시장이 대상) |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 (**끄면 사실상 무전략**) |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (기본 비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 |

## 5. 이 전략이 실패하는 경우 (솔직한 리스크)

1. **백테스트 과적합**: QuantPedia 백테스트는 표본이 작다(2025년 계약 위주, 단일 기간). X=20/Y=5는 그리드 서치 승자라 선택 편향이 있고, 시장 구조(참가자·유동성)가 달라진 2026년에 재현되지 않을 수 있다. **이것이 이 봇의 존재 이유다 — 소액 실전으로 재현성을 검증한다.**
2. **passive 체결 전제**: 백테스트는 passive(지정가) 체결을 가정했다. tail 시장의 스프레드는 fat-tail이라 **시장가로 환산하면 알파가 소멸**한다는 것이 원문의 경고다. 우리의 GTC limit @ midpoint는 semi-passive 근사일 뿐이며(§8), 체결 슬리피지가 실제 수익률을 백테스트보다 반드시 낮출 것이다.
3. **신저가가 '진짜 정보'인 경우**: 이 전략에는 뉴스/거래량 필터가 없다(백테스트 충실 복제 우선). 실제 악재로 인한 정당한 하락에도 매수한다. SL -30%와 5일 청산이 유일한 방어다.
4. **tail 시장의 해결 리스크**: p=0.03~0.10 구간 포지션은 시장이 조기 해결(NO 확정)되면 전액 손실에 가깝다. EXPIRED 처리로 기록은 남지만 자금은 잠긴다.
5. **군집 손실**: 시장 전체 하락 국면(예: 크립토 급락과 상관된 시장들)에서는 여러 시장이 동시에 신저가를 만들어 포지션이 한 방향으로 몰린다. `POLYBOT_MAX_POSITIONS`로 상한을 두는 것을 권장한다.

## 6. A/B 검증 방법

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job sim-test`를 Jenkins 3~5분 간격으로. 진입 빈도와 `rolling_min_at_buy`/`lookback_days_at_buy` 분포 확인 (`lookback_days_at_buy >= 19`만 진입하는지 검증).
2. **소액 실전 (4주+)**: `POLYBOT_BUY_AMOUNT`를 최소로. 5일 보유 전략이라 회전이 느리다 — 최소 4주, **30+ 거래** 확보 후 판단.
3. **판단 기준**: `exit_reason=max_holding` 거래의 평균 P&L이 양수인가 (이 전략의 본질 지표). 승률보다 평균손익 우선 (tail 매수는 승률이 낮고 payoff가 비대칭). CSV의 `hold_hours_at_exit`, `rolling_min_at_buy` 컬럼으로 회고.
4. **중단 기준**: 30거래 후 총 P&L < -15% 또는 `stop_loss` 비중 > 40%면 가설 기각.

## 7. 베리에이션 아이디어

- **A-1 (백테스트 원형에 더 가깝게)**: `POLYBOT_STOP_LOSS=-0.99`, `POLYBOT_TAKE_PROFIT=9.9` — 안전판을 사실상 끄고 순수 calendar exit만. 백테스트와의 괴리 최소화.
- **A-2 (짧은 룩백/보유)**: `POLYBOT_LOOKBACK_DAYS=10`, `POLYBOT_HOLD_HOURS=72` — 회전율을 높여 표본을 빨리 모은다.
- **A-3 (좁은 tail 집중)**: `POLYBOT_PROB_MIN=0.05`, `POLYBOT_PROB_MAX=0.25` — 오버슈트가 가장 큰 구간만.

## 8. 알려진 구현 한계

- **GTC limit @ midpoint + 체결 가정**: 주문을 넣으면 체결됐다고 가정하고 DB에 기록한다 (cherry와의 A/B 비교를 위해 유지). 미체결 시 실제 포지션과 DB가 어긋날 수 있다. 백테스트의 passive 체결과도, 진짜 시장가와도 다른 **semi-passive 근사**다.
- **스냅샷 + 백필 의존**: `/prices-history`는 문서화되지 않은 public endpoint다. 막히면 20일 윈도우를 채울 수 없어 봇이 (안전하게) 진입을 멈춘다. 스냅샷 축적으로 자연 회복하지만 20일이 걸린다.
- **hourly approximation**: `fidelity=60` 가격의 19일 이상 span으로 20일 규칙을 근사한다. 일별 종가 정렬·원 논문의 체결 표본을 재현하지 않으므로 원 백테스트 성과와 직접 비교할 수 없다.
- **YES 가격 기준 스냅샷**: 스냅샷은 항상 YES 가격으로 저장한다. 이 전략은 YES 매수 고정이라 환산이 없지만, 방향을 바꾸는 베리에이션을 만들려면 signals.py에서 1-p 변환을 추가해야 한다.
- **시뮬레이션도 CLOB 인증 필요**: midpoint 조회에 실키가 필요하다. 완전 오프라인 검증은 `pytest` + `config` 명령까지.

## 출처

- QuantPedia (2026-04), "Exploiting Mean-Reversion in Decentralized Prediction Markets" — X=20/Y=5 규칙, 10bps 비용 반영 CAR +18.9~22.1%, passive 체결 전제 경고
- Scott Alexander, "Prediction Market FAQ" — 장기 시장의 가격 압축(50% 방향 왜곡)과 자본 락업으로 인한 차익거래 지연
- Kahneman & Tversky, Prospect Theory — 손실 회피(약 2배 가중)로 인한 투매 과잉의 심리적 기반
- arXiv 2602.19520 "Decomposing Crowd Wisdom" (Kalshi+Polymarket 2.92억 거래 분석) — 예측시장 가격 오차의 체계성(무작위가 아님)에 대한 배경 근거
