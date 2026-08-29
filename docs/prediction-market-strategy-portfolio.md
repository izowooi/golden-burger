# Polymarket 전략 포트폴리오 (골든 시리즈)

총 26개 `golden-*` 프로젝트의 전체 지도다. 이 중 24개는 수익 가설을 검정하고,
`golden-pomegranate`는 범용 전 시장, `golden-coconut`은 major sports 전용 accountless
market observatory다.
`golden-black`, `golden-raspberry`, `golden-strawberry`, `golden-watermelon`은 수익 가설이지만 주문 없이 displayed-book
반사실만 수집한다. 현재 운영 상태는
[전략 운영 현황 HTML](strategy-pages/strategy-status.html), 상세 규칙은 각 폴더의
`STRATEGY.md`, 사람이 읽기 좋은 설명은 `docs/strategy-pages/`, 회고 절차는
`docs/ab-retro-playbook.md`를 따른다. **폴더 존재·과거 실행·현재 운영·폐쇄 완료는 서로
다른 사실**이며, 이 문서는 2026-08-27 확인 상태를 표시한다.

## 설계 원칙

예측시장은 주식시장과 다르다:

1. **항상 마감일이 있다** — 가격은 마감일에 0 또는 1로 강제 수렴한다. 시간 자체가 신호다.
2. **참여자가 리테일 대중이다** — 마켓메이커·기관이 얇아 심리 편향이 가격에 그대로 남는다.
3. **숏이 없다** — 페이드(반대 베팅)는 반대 토큰(NO) 매수로 표현한다.
4. **이평선 교차 같은 순수 기술적 신호는 통하지 않는다** (banana에서 실증) — 신호는 반드시 "누가, 왜 잘못된 가격을 만드는가"라는 심리 가설 위에 세운다.

우리의 구조적 우위: **Jenkins 24/7, 3–5분 간격 실행**. 밀리초 아비트라지는 불가능하지만, 사람이 잘 때도 시장을 보고, 분 단위 시계열(스냅샷)을 자체 축적할 수 있다.

## 전략 지도 — 어떤 심리를 노리는가

| 봇 | 전략 | 노리는 심리 | 방향 | 시장 구간 | 상태 |
|---|---|---|---|---|---|
| golden-apple | 80% 매수 / 90% 매도 | certainty effect (favorite 과소평가) | favorite 편승 | 0.80–0.90 | **운영 중** (2계정) |
| golden-banana | 85–97% + 골든크로스 | 모멘텀 지속 | favorite 편승 | 0.85–0.97 | **운영 중** (신호 evidence caveat) |
| **golden-black** | Sports Resolution Hold | 고확률 sports outcome의 terminal 수렴 | 주문 없는 `$5` ask→resolution/stop 반사실 | 0.92/0.94 × hold/0.80/0.70/0.60 stop, Gamma endDate ≤6h | **research-only · prospective 30일 · live/order 금지** |
| **golden-coconut** | Five-Family Major Sports Observatory | 종목별 lifecycle·유동성·거래량·고확률 crossing의 구조 측정 | 주문 없는 full-book/path/resolution census | soccer·MLB·NBA·NFL·NHL, 0.75–0.99, `$5`–`$1000`, 5분 | **research-only · polybot-gold · live/order 금지** |
| **golden-blueberry** | Closing Surge | 마감 임박 첫 급등 뒤 추가 수렴 | strict binary YES 편승 | 0.85–0.93, ≤72h | **구현 완료 · A/B 시작 evidence 없음** |
| golden-cherry | Resolution Momentum | 마감 임박 확증 편향 + 수렴 | favorite 편승 | 0.75–0.92, 설정 horizon | **운영 중** |
| ~~golden-date~~ | Conviction Ladder | cherry와 동일 + 시간 사다리 | favorite 편승 | 시간별 0.70–0.95 | **⛔ 폐쇄 완료 2026-07-29** |
| golden-elderberry | Panic Fade | 손실 회피 → 공황 투매 과잉반응 | 급락 역매수 | 0.35–0.75 | **운영 중** |
| ~~golden-fig~~ | Hope Crusher | favorite-longshot bias | 롱샷 페이드 (NO 매수) | YES 0.05–0.25 | **⛔ 폐쇄 완료 2026-07-28** |
| golden-grape | Cascade Rider | 정보 폭포 / 과소반응 | 완만한 드리프트 편승 | 0.40–0.80 | **구현 완료 · 시작 evidence 없음** |
| ~~golden-honeydew~~ | Night Watch | 새벽·주말 attention 희소성 | 무근거 이탈 복원 | 0.30–0.90 | **⛔ 폐쇄 완료 2026-07-30** |
| ~~**golden-kiwi**~~ | Micro-Cascade | 지연된 사회적 정보 반영 | 명목 15/25분 micro-trend 편승 | YES 0.20–0.80 | **⛔ 실험 폐쇄 2026-08-29 · STOP / UNRESEARCHABLE · live 금지** |
| ~~golden-lime~~ | Shock Follow | 대형 뉴스 불신·앵커링 | 급등 편승 | 점프 후 ≤0.85 | **⛔ 폐쇄 완료 2026-07-28** |
| ~~golden-mango~~ | Patience Premium | 자본 잠김 회피 → settlement discount | favorite 캐리 | 0.85–0.985, ≤14일 | **⛔ 폐쇄 완료 2026-07-28** |
| ~~golden-melon~~ | Resolution Sprint | 마감 임박 수렴의 거래량 선별 | strict binary YES 편승 | 0.85–0.93, ≤72h | **⛔ 운영 폐쇄 2026-08-24 · close-only 검증 2026-08-27** |
| ~~golden-nectarine~~ | Bottom Fisher | 손실 회피發 투매 오버슈트 | 롤링 최저가 역매수 | YES 0.03–0.50, 30일+ | **⛔ 폐쇄 완료 2026-07-30** |
| golden-orange | Fear Spike Fade | probability neglect | 공포 급등 페이드 (NO 매수) | base ≤0.15 → 스파이크 | **구현 완료 · 시작 evidence 없음** |
| golden-papaya | Final Five | 95% first observed crossing 뒤 해결 수렴 | strict binary YES 편승 | 0.95–0.97, ≤72h | **운영 중** |
| **golden-pomegranate** | Market Observatory | 수익 가설 없음 — 모든 후속 가설의 point-in-time 원자료 | 주문 없음, 전 시장 관측 | 전체 non-closed universe + 회전 CLOB book | **research-only · live/order 금지** |
| golden-queen | Crown Momentum | 90% first observed crossing 뒤 단기 수렴 | strict binary YES 편승 | 0.90–0.94, 12h/24h arms | **운영 중** |
| ~~golden-quince~~ | Spread Harvest | maker/taker execution cost | 동일 신호, BUY 가격만 처치 | queen 신호 상속 | **⛔ 운영 폐쇄 2026-08-27** |
| **golden-raspberry** | Queue Echo | 지속 displayed-depth 비대칭의 지연 가격 반영 | 주문 없는 `$5` ask→60m bid 반사실 | YES/NO 0.20–0.80, 3 hash shards | **research-only · live/order 금지** |
| **golden-strawberry** | Last Mile | 고확률 최초 교차 뒤 terminal 수렴 | 주문 없는 `$5` ask→bid/resolution 반사실 | 동결 v1 crossing census + 10분 compact follow-up v2 | **research-only · entry 종료/follow-up 중 · live/order 금지** |
| **golden-tangerine** | Sports Resolution Hold Live | 고확률 sports outcome의 terminal 수렴 | exact `$5` FOK BUY 후 resolution 보유 | 0.92–0.93 vs 0.94–0.95, Gamma endDate ≤6h | **최소금액 prospective live A/B · 2026-08-21 시작** |
| **golden-watermelon** | Elite Soccer In-Play Match Winner | 경기 중 고확률 whole-match winner의 terminal 수렴 | 주문 없는 full-book ask→resolution/stop 반사실 | X 0.95–0.99 × Y 0.95–0.70, 75/80/85분, `$5`~`$1,000`, 1분 vs 5분 | **research-only v3d · 6개 리그+UCL/UEL · live/order 금지** |
| **golden-watermelon-live** | In-Play Match Result Live | 경기 중 고확률 home/draw/away의 terminal 수렴 | exact `$5` FOK BUY, 0.70 full-depth FOK stop | Cat 0.96 vs Dog 0.99, 6개 리그+UCL/UEL | **최소금액 prospective live A/B v2h** |

상태 합계는 운영 8, 구현만 완료 5, research/simulation 전용 7, 명시적 보류 0, 폐쇄 완료
6이다. `close_only`/`archive_only`는 bot lifecycle mode이지 이 의사결정 상태와 같지 않다.

폐쇄 전략을 단순히 반대 방향으로 뒤집지 않는다. Lime은 shock-follow와 근사 반대 방향
모두 지지하지 않았고, Honeydew는 snapshot replay와 actual confirmed-fill 성과의 부호가
달랐으며, Nectarine은 단일 이벤트가 전체 양수를 만들었다. 새 가설은 독립 기간,
point-in-time catalog, event clustering, executable quote와 fill evidence로 다시 검정한다.

## 1차 설계군의 근거와 현재 판정

### golden-date — ⛔ 폐쇄 완료 (2026-07-29) — Conviction Ladder
cherry가 이미 돈을 벌고 있는 가설("마감이 다가오면 대중이 favorite으로 쏠리고, 시장 정확도는 24h 전 88.6% → 4h 전 94.2%로 수렴")을 유지하면서, 분석에서 확인된 cherry의 허점 5개를 수정한 직계 후계자다: 남은 시간과 무관한 고정 확률 밴드 → 시간 사다리(마감이 멀수록 싸게만 진입), --yes-only로 NO-favorite 시장 절반 폐기 → 양측 지원, 하락 중에도 매수 → 6h 모멘텀 게이트, rapid_jump 영구 skip → 쿨다운 재진입, 12h 전 조기 청산 → 2h 전까지 수렴 구간 수확.

### golden-elderberry — Panic Fade
손실 회피는 이득보다 약 2배 무겁게 작동한다. 악재·루머가 뜨면 보유자들이 공황 투매해 가격이 펀더멘털 아래로 오버슈팅한다. 레포 내 리서치 문서도 "10%+ 급변은 mean-revert"를 확인했다. 마감 48h+ 남은 시장으로 한정해 '마감 직전 급락 = 진짜 정보' 케이스를 배제하고, 45분 바닥 안정화를 확인한 뒤 진입해 떨어지는 칼날을 피한다.

### golden-fig — ⛔ 폐쇄 (2026-07-28) — Hope Crusher

> **가설 기각.** 거래 시장 1,567건의 실제 해결 결과 대조에서 **5개 가격 구간 전부 edge 음수**
> (진입가 0.8489 vs 실현률 83.41%). favorite-longshot bias가 존재하지 않는다. 필요 승률 69.0%
> vs 실제 57.5%. 스포츠 필터가 꺼져 있었으나 본래 우주(`No`)만 봐도 손실이다.
> → [판정](retro/golden-mango-fig-2026-07-verdict.md)

favorite-longshot bias는 예측시장 문헌에서 가장 잘 문서화된 편향이다 — 대중은 낮은 확률의 "복권"에 체계적으로 과지불한다. cherry가 이 편향의 favorite 쪽(과소평가)을 수확한다면, fig는 미러 이미지: "D일까지 X가 일어날까" 시장에서 마감이 다가와도 희망 보유자들이 앵커링으로 YES를 놓지 않는다. 시간이 소진되는 것 자체가 수익 동력이므로(NO는 1.0으로 수렴), 예측시장에만 존재하는 '만기'라는 구조를 가장 직접적으로 이용한다.

### golden-grape — Cascade Rider
뉴스는 대중에게 천천히 퍼진다(정보 폭포). 리서치 문서: "2–3%/일의 완만한 이동은 +6–8% 지속, 10%+ 급변은 회귀". banana의 골든크로스가 실패한 이유는 가설이 아니라 신호였다 — threshold 0.02가 사실상 도달 불가능해 모든 실거래가 cold-start 폴백으로 발생했다. grape는 같은 모멘텀 가설을 도달 가능한 신호(24h 일관 드리프트 +4~10pt, 4h 버킷 70% 일관성, 거래량 1.2배 가속)로 재구현하고, 드리프트 상한으로 mean-revert 영역을 배제한다.

### golden-honeydew — ⛔ 폐쇄 완료 (2026-07-30) — Night Watch

> 완전 대사된 confirmed-fill 316건의 fee 차감 전 gross P&L은 **-$55.92(-3.54%)**였고,
> deviation·유동성·거래량·평일/주말 주요 slice가 모두 음수였다. 낙관적 snapshot
> replay의 일부 양수 조합은 실제 체결 결과와 반대여서 live A/B 근거가 아니다.
> → [판정](retro/golden-honeydew-2026-07-verdict.md)

Polymarket 참여자 대다수는 미국 시간대의 사람이다. 미 동부 새벽 01–08시와 주말에는 호가가 얇아 소액 주문에도 가격이 밀리고, 아침에 주의가 돌아오면 복원된다. "24시간 계속 리퀘스트를 날릴 수 있다"는 우리의 구조적 우위를 가장 정면으로 수익화한다 — 이 전략의 경쟁자는 그 시간에 깨어 있을 수 없다. 거래량 급증 시 진입 금지(뉴스에 의한 진짜 이동 배제)로 무근거 이탈만 노린다.

### golden-lime — Shock Follow ⛔ 폐쇄 (2026-07-28)

> **가설 기각.** 14일 운영 -72% 후 `market_snapshots` 103만 행으로 백테스트한 결과, 파라미터
> 28개 조합 어디에도 거래비용을 넘는 표류가 없었다. barrier 승률 41.2%가 무편향 이론값
> 40.0%와 일치하고, 손실은 거래비용 그 자체로 설명된다(예측이 실측을 98% 재현).
> 이 문서가 인용한 "10%+ 급변은 mean-revert"가 맞았다.
> → [최종 판정](retro/golden-lime-2026-07-backtest-verdict.md)
>
> 같은 백테스트에서 **반대 방향(elderberry의 급락 후 반등)도 지지되지 않았다** —
> 급락 후 12h 평균 -18.92%(t=-2.10)로 반등이 아니라 계속 하락했다. 다만 이는 lime의
> snapshot universe로 elderberry 트리거를 근사한 것이므로, elderberry 자체 데이터로
> 재확인이 필요하다.

대형 서프라이즈에 대중은 "설마"(불신)와 기존 가격 앵커링으로 일부만 반영한다 — 주식의 실적 발표 후 드리프트(PEAD)와 같은 구조. elderberry와 정반대 트리거의 의도적 A/B 쌍: 급변 이벤트에서 거래량이 미약하고 고점을 반납하면 노이즈(elderberry가 페이드), 거래량이 2배+ 폭증하고 고점을 유지하면 정보(lime이 편승). 두 가설을 동시에 실전 검증한다.

## 2차 설계군 (공개 문헌·백테스트에서 도출)

이 3종은 기존 골든 시리즈를 참조하지 않고 예측시장 문헌 리서치에서 독립 도출했다. 출처는 각 `STRATEGY.md`에 명기.

### golden-mango — ⛔ 폐쇄 (2026-07-28) — Patience Premium

> **가설 기각.** 허들 `y>=2.0`은 자본 기회비용으로 정당화되는 할인의 **10배**를 요구한다 —
> 즉 시간가치가 아니라 **손실확률**을 탐지한다. 게다가 후보 정렬이 없어 매 사이클 140개 중
> 임의의 10개를 산다. 승률 54.5%(자체 기준 85%), stop_loss 42%(기준 15%).
> → [판정](retro/golden-mango-fig-2026-07-verdict.md)

예측시장 참여자는 자본이 잠기는 것을 싫어해서, "거의 확실한" 계약도 만기까지의 기간만큼 할인되어 거래된다(settlement discount). 2026년 arXiv 논문 2편이 이 할인 기간구조를 실측했고(할인 보정 시 근확실 구간 왜곡의 48~88%가 소거), Kalshi 실증도 고가 계약의 양(+)의 수익률을 확인했다. 단일 수식 `y = ((1-p)/p) × (8760/남은시간) ≥ 2.0` 하나로 진입을 판정한다 — 대중의 조급함이 만든 할인을 봇의 인내로 수확한다. 골든크로스만큼 간결하지만, 근거는 가장 강하다.

### golden-nectarine — ⛔ 폐쇄 완료 (2026-07-30) — Bottom Fisher

> 완전 대사된 전체 81건은 gross +$6.95였지만 단일 이벤트 +$26.09에 의존했다. 이를
> 제외하면 -$19.14이고, 대사된 120시간 `max_holding` 부분집합 59건은
> **-$14.46(-4.70%)**였다. 정정된 24·72·120·168·240시간 counterfactual의
> condition-cluster CI는 모두 0을 포함해, 특정 보유기간을 후속 A/B 후보로 선택하지 않는다.
> → [판정](retro/golden-nectarine-2026-07-verdict.md)

장기 tail 시장에서 패닉/노이즈 매도가 가격을 일시적으로 누르면 반등한다. QuantPedia(2026-04)의 Polymarket 공개 백테스트를 시간별 CLOB 데이터에 근사 이식했다: `현재가 ≤ 20일 롤링 최저가 → 매수, 5일(120h) 보유 후 무조건 청산`. 원문의 daily close·universe·fill rule과 동등하지 않으며, 이번 실거래 결과는 원 연구의 복제 성공이 아니다.

### golden-orange — Fear Spike Fade
무서운 헤드라인 아래에서 대중은 확률이 아니라 결과의 끔찍함에 반응한다(probability neglect, Sunstein 2002). tail 시장 YES가 급등했다가 90~120분 내 되돌림의 60%가 발생한 실측 사례(이란 휴전 35→68→58%, 핵폭발 시장 19%)를 근거로, 스파이크가 스톨한 뒤 NO를 사서 공포 프리미엄 감쇠를 수확한다. fig(정적 theta)·lime(급등 편승)과 구분되는 이벤트 직후 감정 과잉 전담.

## 3차 설계 — 단순 수렴 가설의 엄격한 대조군

### golden-papaya — Final Five
표준 이진 YES가 해결까지 0시간 초과 72시간 이하로 남았을 때 archive cadence에서
**first observed upward crossing**으로 0.95를 돌파하면 0.95–0.97에서만 진입하고, 사전 해결
익절·time exit 없이 resolution/redeem까지 보유한다. 수렴 실패 신호는
절대 YES 0.90 stop 하나뿐이다. 운영 진입 기본값은 Gamma 유동성 $10k/최근 24h volume
$2k이며, 실제 CLOB depth나 stop 체결의 안전 보장이 아니다. fresh best ask가 0.97을
넘으면 진입하지 않고, stop은 fresh best bid와 confirmed fill로 실행 가능성을 평가한다.
first-crossing lineage와 반사실을 위해 YES≥0.80·잔여≤168h·유동성 $1k/volume $0의 더 넓은
자체 archive를 60일 보존한다.
sweep/run gap이 있으면 실제 교차 시점은 interval-censored로 보고한다.

## 4차 설계 — Cherry 계승·실행 증거 강화

### golden-queen — Crown Momentum

Cherry의 “해결이 가까운 우세 YES 수렴” 가설을 유지하되, 불완전한 legacy fill과 snapshot
증거로 수익을 최적화한 것처럼 주장하지 않는다. 표준 이진 non-negRisk YES가 60일 자체
archive에서 처음 0.90을 상향 교차하고 현재가 0.90–0.94일 때만 진입한다. 일반 시장과
스포츠 경기 전은 `(0h, 24h]`, 스포츠 경기 중은 upstream이 계속 거래 가능할 때 kickoff 후
360분까지 대상이다. 스포츠는 기본 포함하고 `gameStartTime`이 없으면 `endDate`로 fallback한다.

주문 직전 fresh midpoint·best ask·spread와 same-snapshot ask depth를 검증한다. live BUY는
접수만으로 보유가 되지 않고 exact full confirmed fill까지 `PENDING_BUY`, live SELL도
confirmed BUY/SELL size와 fee가 모두 대사될 때까지 `PENDING_SELL`이다. 청산은 미해결
YES 0.98 목표(실행 가능한 bid도 0.98 이상) 또는 0.85 절대 stop 두 개뿐이며 time/trailing
exit은 없다. 현재 baseline은 건당 $100이며, 24h 대 12h 한 축만 별도 계정·job·DB에서
사전 등록 A/B한다. 주문 금액에 따라 liquidity·volume24h·open notional gate가 자동
확장된다.
Cherry 30일 strict audit가 CRITICAL/HIGH evidence issue로 실패했으므로 이 규칙은 “최적
수익값”이 아니라 반증 가능한 보수적 신규 가설이다.

## 5차 설계 — 실행 측면만 처치하는 3-arm 실험

### golden-quince — Spread Harvest

방향성 예측을 포기하고 **진입 실행 측면(maker/taker)** 하나만 수익원으로 검정한다.
실측 왕복 비용이 maker→maker `-31.1 bps`, taker→taker `+72.5 bps`로 103 bps
갈렸다는 관찰에서 출발했다. Queen 진입 신호, $5, 고정 24h horizon, SELL `nearest`는
세 팔에서 같고 BUY tick 처리만 바꾼다.

| 팔 | BUY mode | canonical Jenkins job | 역할 |
|---|---|---|---|
| A | `passive` | `polybot-quince-passive` | maker 처치군 |
| B | `nearest` | `polybot-quince-nearest` | 기존 반올림 대조군 |
| C | `cross` | `polybot-quince-cross` | taker 비용 상한 |

각 팔은 별도 wallet/account, Jenkins job, `--job`, DB를 사용한다. 30일의 1차 endpoint는
승률이나 최종 P&L이 아니라 MAKER 비중, decision midpoint 대비 진입 VWAP, 체결률,
체결 뒤 15/60분 역선택이다. 현재 상태는 **구현 완료, 실제 시작 evidence 없음**이다.

## 6차 설계 — 5분 Micro-Cascade 연구

### golden-kiwi — Micro-Cascade

사람들이 정보를 한 번에 반영하지 않고 서로의 거래를 따라갈 때 3~5회의 작은 5분
YES 상승이 한 시간 더 이어질 수 있다는 단순 가설이다. Lime의 6시간 shock-follow와
Grape의 24시간 drift 사이를 다시 최적화하지 않고, 정확한 5분 cadence에서 명목상
15/25분인 monotone micro-trend만 고정된 2×2로 수집한다. 허용 gap 3~10분을 적용한
실제 span은 3-step이 9~30분, 5-step이 15~50분이다.

| 팔 | 양의 step 수 | 최소 누적 상승 | 역할 |
|---|---:|---:|---|
| A | 3 | +1pp | loose sensitivity |
| **B** | **3** | **+2pp** | **사전 등록 primary** |
| C | 5 | +1pp | 긴 확인 sensitivity |
| D | 5 | +2pp | strict sensitivity |

공통 조건은 step마다 `0 < ΔYES ≤ 2pp`, 누적 ≤4pp, 관측 gap 3~10분, YES
0.20~0.80, 잔여 ≥6h, liquidity ≥$20k, volume24h ≥$10k, spread ≤2pp,
event 6h cooldown이다. 60분 뒤 첫 유효 bid로 quote-to-quote 결과를 기록한다.

하지만 2026-07-30에 **결과를 보기 전에** Arm B와 승격 gate를 동결하고 Honeydew의
독립 시간 구간으로 검정했을 때 어느 팔도 통과하지 못했다. strict event-purged B는
1 signal/1 event, +13.55 bps였고 10.4 bps stress 뒤 +3.15 bps였지만 CI를 계산할 수
없었다. cooldown-carried B는 2 events 평균 -1.8072%였다. 독립 재검토에서는 C의 유일한
양수 신호가 서로 다른 Git commit snapshot을 이었고, 과거 DB에 snapshot-level
strict-binary/`negRisk` 증거도 없음을 확인했다. 따라서 C 양수 해석은 철회했으며 당시
A/B/C/D 수치는 모두 현재 promotion evidence가 아니다.

따라서 Kiwi는 **research/simulation 전용**이고 live execution은 코드에서 금지한다.
새로운 독립 30일 5분 data에서 B가 50 quote-complete signals/30 event clusters,
98.75% clustered lower bound >0(10.4 bps stress 뒤에도 >0), 양쪽 time half 양수,
coverage ≥90%를 모두 충족하기 전에는 threshold 완화나 live 승격을 하지 않는다.
상세는 `golden-kiwi/STRATEGY.md`, `golden-kiwi/research/`,
`golden-kiwi/research/2026-07-30-cohort-correction.md`,
`docs/retro/golden-kiwi.md`를 따른다.

## 7차 설계 — 마감 임박 최초 급등 강도 A/B

### golden-blueberry — Closing Surge

운영자가 기억하는 “3일 이내 시장에서 확률이 치솟을 때 사고 해결 전에 판다”는 Cherry 계보를
실거래 가능한 최소 단위로 다시 검정한다. strict binary non-negRisk YES의 persisted 직전값이
0.85 미만이고 현재 `[0.85,0.93]`에 처음 들어올 때만 후보가 된다. 일반/경기 전은
`(0h,72h]`, 스포츠 경기 중은 kickoff 뒤 최대 360분까지 포함한다. fresh ask `<=0.93`,
spread `<=2%p`, depth `>=1.2×`를 다시 확인하고 0.97 target 또는 0.78 absolute stop으로
관리한다. trailing/time exit은 없다.

유일한 A/B 처치축은 연속 15분 이내 snapshot의 최소 급등 폭이다. A는 `+2%p`, B는
`+5%p`; 각각 별도 계좌/job/DB와 $150를 쓰고 건당 $5로 시작한다. `$1`은 CLOB 5-share
최소 주문을 충족하지 못한다. arm당 open notional $50, economic drawdown -$30 kill switch,
liquidity와 volume24h 각각 $10k gate가 있다. 최초 crossing이 B 급등폭을 못 넘으면 reject로
기록하고 나중 recross로 대체하지 않는다.

과거 Cherry 671건은 legacy fill gap 때문에 월 10%나 스포츠 우위를 확정하지 못한다. 따라서
Blueberry는 실제 급등 가설을 `entry_signal_decisions`와 exact confirmed fill/fee로 새로
측정한다. 1주는 health checkpoint, 30일에도 arm당 confirmed closed 20건 미만이면 판정하지
않는다. cohort는 `config_hash × strategy_source_digest × mode × job_name`이며 Git commit은
provenance다. 상세는 `golden-blueberry/STRATEGY.md`,
`golden-blueberry/research/2026-08-04-origin-and-preregistration.md`,
`docs/retro/golden-blueberry.md`를 따른다.

## 8차 설계 — 전략을 고르기 전의 범용 시장 관측소

### golden-pomegranate — Market Observatory

Pomegranate는 수익을 내는 trading strategy가 아니다. 기존 전략들이 각자의
probability·horizon·liquidity gate를 통과한 시장만 저장해 생긴 strategy-filter selection
bias를 `closed=false` source envelope 안에서 제거하고,
나중에 어떤 gate와 threshold가 실제로 유효했는지 다시 계산하기 위한 **accountless
research instrument**다.

매 cycle Gamma keyset을 cursor 끝까지 순회해 `closed=false` non-closed universe 전부와 variable-length
outcomes를 저장한다. 누적 `volume`과 `volume24hr`, liquidity 계열, 모든 source clock,
event/tag/sports, YES/NO가 아닌 복수 outcome까지 point-in-time으로 분리해 보존한다. CLOB
full book은 전체 token을 N+1로 긁지 않고 condition hash 기반 결정적 rotation으로
표본 수집하며, selection reason·bucket·cyclic offset·coverage를 함께 남긴다. 닫히거나
`closed=false` census에서 사라진 condition은 독립 resolution watcher가 추적하고 resolution과
redeemable evidence를 구분한다.

공개 Data API trade tape는 최초 `stabilized target - 24h` bootstrap baseline을 고정하고,
직전 complete watermark부터 cycle당 최대 1시간씩 bounded catch-up한다. source의 최신
stabilized end와 이번 bounded target을 별도로 남기며, `start`/`end` 10,000건 cap에 닿는 창은
시간으로 재귀 분할하고 overlap을 canonical trade hash로 dedupe한다. 1초 창도 cap을 넘으면
`possible_gap`을 남기고 watermark를 넘기지 않는다. 이는 public polling window를 증명하는
것이지 WebSocket의 모든 tick을 손실 없이 재현한다는 주장이 아니다.

저장 계약은 관측치를 thinning하는 `compact-v1`이 아니라 append-only
`research-full-v1`이다. 활성 `trades_sim.db`는 UTC 일 경계에서
`trades_sim_YYYYMMDD.db`로 회전한다. 1TB 외장 디스크도 유한하므로 초기 cadence는
15분, 최소 free space 150GiB, 70% 경고/80% 중단을 기본으로 한다. 7일간 p95 cycle이
8분 미만이고 120일 storage forecast가 안전할 때만 10분으로 올린다. private key·funder·
signature type이 주입되거나 `--live`를 요청하면 startup 자체가 실패하며, account/daily-report
slot도 만들지 않는다. 상세 계약은 `golden-pomegranate/STRATEGY.md`, 운영법은
`golden-pomegranate/README.md`, 회고는 `docs/retro/golden-pomegranate.md`를 따른다.

## 9차 설계 — persistent displayed-depth 가설의 accountless 검정

### golden-raspberry — Queue Echo

유동성이 충분한 표준 이진 시장에서 YES·NO 두 book의 3-tick 가중 잔량이 같은 방향으로
세 번 지속되면, 정보가 가격에 지연 반영되어 60분 뒤 실행 가능 bid가 개선된다는 가설이다.
REST snapshot은 주문 queue identity나 취소·spoofing을 증명하지 않으므로 “queue pressure”가
아니라 **persistent displayed-depth snapshots의 예측력**만 주장한다.

세 Jenkins job `polybot-do/re/mi`는 서로 다른 arm이 아니라
`sha256(condition_id) mod 3` source shard다. 각 shard가 동일 raw stream에서 DO(현재 1회),
RE(2회), MI(3회)를 모두 계산해 request timing과 missingness가 arm 차이로 섞이지 않게 한다.
MI만 primary이고 DO/RE는 sensitivity다. entry는 displayed ask를 걸어 정확히 `$5`를 쓰며,
60~75분 사이 첫 독립 request의 displayed bid로 같은 shares를 모두 파는 반사실이다.

`queue-echo-v1`은 terminal Gamma cursor, 동시 YES/NO raw CLOB body, signal lineage,
neutral/opposite control, follow-up censoring을 append-only SQLite에 남긴다. 24시간은 계측,
7일은 collection health, 30일은 event-cluster confirmatory gate만 판정한다. threshold를 같은
자료에서 완화하거나 DO/RE 승자를 사후 primary로 바꾸지 않는다. `--live`, credential,
order path는 source-level로 금지하며 통과 판정도 `SHADOW_REVIEW_ONLY`다. 상세는
`golden-raspberry/STRATEGY.md`, frozen 계약은
`golden-raspberry/research/frozen-2026-08-13/PREREGISTRATION.md`, 회고는
`docs/retro/golden-raspberry.md`를 따른다.

## 10차 설계 — 고확률 Last Mile의 accountless 검정

### golden-strawberry — Last Mile

Entry window에는 10분마다 public CLOB `/sampling-markets` full census를 cursor 끝까지 수집하고, 각 outcome token의
first-observed upward crossing을 entry `0.90/0.92/0.95/0.97` grid로 interval-censoring한다.
CLOB displayed book에서 정확히 `$5`의 ask 진입과 이후 bid 또는 proven terminal payout만
append-only 반사실 evidence로 남기는 **accountless research-only** collector다.

Primary는 `0.95` entry, `0.85` stop, price target 없이 terminal resolution까지 보유하는
정책 하나다. stop `none/0.80/0.85/0.90`과 target `none/0.98/0.99`는 sensitivity일 뿐이며,
첫 1주 pilot은 collection health만 판정한다. credential, wallet, order/fill path와 `--live`는
source-level로 금지한다. Gamma는 crossing-time volume/liquidity/event metadata와 terminal
resolution 확인에만 사용한다. 2026-08-22 entry 종료 뒤 v1은 immutable source로 동결하고,
`strawberry-shadow-one-followup-v2`가 unresolved episode만 10분 cadence와 compact full-book으로
추적한다. 상세는 `golden-strawberry/STRATEGY.md`, frozen 계약은
`golden-strawberry/research/frozen-2026-08-15-clob/PREREGISTRATION.md`, 회고는
`docs/retro/golden-strawberry.md`를 따른다.

## 11차 설계 — Sports Resolution Hold paired 검정

### golden-black — Sports Resolution Hold

전체 sampling census 대신 Gamma event keyset의 sports, endDate 6시간, liquidity 10k,
cumulative volume 5k server filter를 먼저 적용한다. 통과한 aligned two-outcome market의 두 token만
exact CLOB full book으로 읽고, 정확히 `$5`를 매수할 수 있는 VWAP가 `[0.92,0.93]` 또는
`[0.94,0.95]`에 처음 들어오면 paired counterfactual episode를 만든다. 조기 target 없이 CLOB
market의 unique one-hot winner까지 추적하는 hold baseline과 `0.80/0.70/0.60` stop policy를
동시에 계산한다. stop은 trigger 가격에 체결됐다고 가정하지 않고 실제 bid depth를 보유 share만큼
walk한다. gap-down VWAP, partial fill, 남은 수량과 다음 cycle retry를 append-only로 남긴다.

0.94는 Pomegranate의 사후 screen과 별도 historical archive에서 양수였지만 Wilson 하한이
현재 fee 포함 손익분기보다 낮아 수익을 보장하지 않는다. 0.92는 두 데이터원의 시간 전·후반이
모두 양수였던 더 넓은 대조군이다. 현재 공식 sports taker fee 0.05와 source fee schedule을
보존한다. Gamma endDate가 실제 경기 종료와 같다는 보장은 없으므로 gameStartTime과 phase를
같이 저장한다. 30일 prospective window가 끝나기 전 threshold를 바꾸거나 live로 승격하지 않는다.
같은 기간 동안 stop policy도 선택하지 않는다.
상세는 `golden-black/STRATEGY.md`, frozen 계약은
`golden-black/research/frozen-2026-08-20/PREREGISTRATION.md`, 회고는
`docs/retro/golden-black.md`를 따른다.

## 12차 설계 — elite soccer whole-match winner의 cadence·timing·capacity 검정

### golden-watermelon — In-Play Match Winner

Gamma `/events/keyset`에 numeric soccer tag와 `live=true`를 적용하되 volume/liquidity 하한은
두지 않는다. EPL·Bundesliga·Ligue 1·LaLiga·MLS·Serie A와 UCL/UEL의 exact numeric
identity만 허용하고 nested `sportsMarketType=moneyline`을 재검증한다. exactly two teams의
HOME/DRAW/AWAY regular-time YES만 남기며 `child_moneyline`, advancement, extra time, penalty,
spread/total/player prop과 e-sports는 fail closed한다.

exact `$5` ask VWAP가 X `0.95/0.96/0.97/0.98/0.99`를 처음 넘은 episode에서
resolution hold와 Y `0.95/0.93/0.90/0.85/0.80/0.70` stop을 같은 path에서 동시
재생한다. stop은 best bid trigger와 original shares에 대한 actual displayed-depth VWAP를
분리하고 gap, partial, remaining retry, fee를 보존한다. unique one-hot CLOB winner만
terminal payout으로 인정한다.

public Sports WebSocket을 우선하고 same-cycle Gamma explicit clock을 fallback으로 써 regulation
minute 75/80/85 이후 replay를 가능하게 한다. accepted event마다 distinct HOME/DRAW/AWAY
triad를 검사한다. full ask/bid levels는 `$5/$10/$15/$20/$25/$30/$40/
$50/$75/$100/$150/$250/$500/$750/$1,000` read-only sidecar ladder의 depth·VWAP·slippage를
사후 계산한다. 이 데이터로
timing이나 live scale을 지금 선택하지 않는다.

`polybot-white/watermelon-white-1m-v3d` 1분(`FAST_1M`)과
`polybot-grey/watermelon-grey-5m-v3d` 5분(`CONTROL_5M`)은 같은 grid의
paired cadence 실험이다. 두 DB의 같은 episode를 독립 표본으로 세지 않으며,
첫 성공 Jenkins build를 실제 source cutoff로 삼는다. 첫 24시간은 collection health만
판정하고 X/Y, timing, notional 수익성을 선택하지 않는다. 주문,
credential, actual fill, `--live`는 source-level로 금지한다. 상세는
`golden-watermelon/STRATEGY.md`, frozen 계약은
`golden-watermelon/research/frozen-2026-08-27-source-clock-triad-scale-v3d/PREREGISTRATION.md`, 회고는
`docs/retro/golden-watermelon.md`를 따른다.

### golden-watermelon-live — In-Play Match Result Live

White/Grey의 선행 관측은 표본이 작아 threshold 최적값을 확정하지 못했다. v2h는 Cat
`[0.96,0.999]` 대 Dog `[0.99,0.999]`의 signal-quantity/tail-risk 최소금액 pilot이다.

EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A와 exact UCL/UEL의 top-level regular-time
home/draw/away YES token만 허용한다. exact `$5` ask depth를 진입 직전에 재검증해 FOK BUY하고, fresh best
bid가 0.70 이하이면 전체 보유 shares의 displayed bid depth를 walk한 limit으로 FOK SELL한다.
1분 polling 사이 gap과 depth 부족은 stop shortfall evidence로 남기며 0.70 체결을 보장하지
않는다. threshold가 없는 경기에 임의 주문을 강제하지 않고, 조건을 충족한 대상 event만 모두
처리한다.

`golden-watermelon` collector는 accountless 상태로 그대로 유지한다. live cohort는
`polybot-cat/watermelon-live-cat-96-1m-v2h`과
`polybot-dog/watermelon-live-dog-99-1m-v2h`이며 수동 wallet position을 관리하지 않는다.
상세는 `golden-watermelon-live/STRATEGY.md`, frozen 계약은
`golden-watermelon-live/research/frozen-2026-08-26-uefa-v2h/PREREGISTRATION.md`, 회고는
`docs/retro/golden-watermelon-live.md`를 따른다.

## 13차 설계 — major sports별 유동성·volume·threshold capacity 관측

### golden-coconut — Five-Family Major Sports Observatory

`polybot-gold/coconut-major-sports-lifecycle-5m-v7`는 external APFS workspace에서
soccer·MLB·NBA·NFL·NHL의 exact major-league top-level whole-game moneyline을 경기 전부터
terminal lifecycle까지 5분마다 수집한다. Soccer는 frozen 8개 대회 query tag로 fan-out하고,
미국 4종목은 각 family tag를 `closed=false`, `slot-24h..slot+48h`에서 terminal cursor까지
읽는다. 다섯 family는 격리된 public HTTP session에서 동시에 시작하고 frozen family order로
정규화한 뒤 Gamma event ID로 추적한다.
축구는 EPL·Bundesliga·Ligue 1·LaLiga·MLS·Serie A·UCL·UEL의 regular-time result Yes token,
미국 4종목은 official major identity의 direct two-team outcome만 허용한다. minor, G League,
AHL/ECHL, NCAA, e-sports, period, spread/total, prop, future, advancement는 fail closed한다.

`0.75..0.99`의 1%p threshold crossing과 `$5..$1000` displayed-depth ladder를
같은 full CLOB book에서 계산한다. liquidity·누적/24시간 volume은 후보를 선별하는 gate가 아니라
sport/season별 strata이며, official preseason은 `PRESEASON`으로 분리한다. append-only UTC daily
shard와 game cluster를 사용해 같은 경기를 독립 표본으로 중복 계산하지 않는다. 계좌·주문·P&L은
없다. socket read와 별개로 response attempt 전체를 15초로 제한하고 다섯 family를 병렬 취득해
slow source 하나가 90초 same-cycle receipt 경계를 순차적으로 소진하지 못하게 한다. exhausted
retry는 cycle failure로 보존한다.
첫 review에서는 collection health와 coverage만 판정한다. 상세는
`golden-coconut/STRATEGY.md`, frozen 계약은
`golden-coconut/research/frozen-2026-08-28-v7/PREREGISTRATION.md`, 회고는
`docs/retro/golden-coconut.md`를 따른다.

## 공통 인프라 개선 (신규 전략 전체 적용)

기존 봇 분석에서 확인된 결함의 수정:

| 결함 (기존 봇) | 수정 (신규 봇) |
|---|---|
| 스냅샷 개수 기반 윈도우 — Jenkins 중단 시 왜곡 | timestamp 기반 윈도우 + 커버리지 검증, 데이터 부족 시 진입 금지 |
| cold start 시 히스토리 없음 | 일반 신규 봇은 CLOB `/prices-history` 백필을 사용한다. Kiwi는 시간축이 다른 backfill을 섞지 않고 persisted current-run snapshot만 사용하므로 필요한 3/5-step lineage가 쌓일 때까지 진입하지 않음 |
| condition_id당 영구 1회 거래 | 쿨다운(기본 24h) 후 재진입 허용 |
| 해결된 시장이 영원히 HOLDING | 일반 신규 전략은 endDate+24h 경과 시 EXPIRED 처리 + 수동 redeem 경고. papaya/queen은 예외로 time exit/수익성 EXPIRED 처리를 하지 않고 resolution을 SELL/cash realization과 분리한다. queen은 actual redeem ingestion이 아직 없음을 명시 |
| 진입가 높으면 take_profit 도달 불가 | 목표가 0.99 캡 |
| `LOG_LEVEL` env 무시 | 지원 |
| Gamma 전체 sweep 2회/사이클 | 1회로 통합 |
| excluded_categories env 불가 | 일반 신규 봇은 `POLYBOT_EXCLUDED_CATEGORIES`를 지원한다. Kiwi는 사전 등록한 exact 제외 집합을 고정하고 변경 시 시작 거부 |

유지한 것(비교 가능성): `POLYBOT_BUY_AMOUNT` 등 env 이름, `data/<job>/` 분리,
py-clob-client-v2, 1실행=1사이클. 기존 신규 전략은 GTC midpoint 흐름을 유지하지만 papaya/queen은
작은 edge와 낮은 유동성을 숨기지 않기 위해 주문 직전 fresh best ask 상한을 진입 게이트로
사용하고, queen은 같은 order-book snapshot의 spread와 ask depth까지 검증한다. 진입·성과
확정은 별도의 confirmed fill evidence로만 수행한다.

## 현재 실행 우선순위

| 대상 | 지금 할 일 | 하지 않을 일 |
|---|---|---|
| 운영 7개 | 현 config cohort를 보존하고 strict audit·confirmed fill·event-effective 성과를 수집 | 여러 knob를 동시에 변경하거나 legacy P&L로 증액 |
| grape / orange | 시작하려면 새 계정·job·DB와 사전 등록부터 확인 | 코드가 있다는 이유로 “운영 중” 표시 |
| blueberry | $5·72h·5분 cadence로 +2%p/+5%p A/B를 별도 wallet/job/DB에서 시작 | $1 주문, 여러 knob 변경, 1주 P&L로 승자 선택 |
| quince | $5·24h·5분 cadence로 A/B/C를 별도 wallet/job/DB에서 실행 | 12h/24h나 주문액까지 동시에 변경 |
| kiwi | 네 simulation job에서 독립 30일 5분 research archive 수집 | live 실행, threshold 완화, 관측 winner로 B 교체 |
| pomegranate | 계좌 없이 15분 full census를 수집하고 7일 health/storage gate를 점검 | 수익/P&L 주장, credential 주입, 관측치를 compact/rollup, 즉시 10분 고정 |
| 폐쇄 6개 | 문서·DB·로그·checksum을 보존하고 wallet/order/redeem 잔여 evidence 대사 | 재가동, 승자 slice 선택, 같은 데이터 재최적화 |

일반적인 “30건이면 충분” 규칙은 사용하지 않는다. 전략별 사전 등록 endpoint와
dependence unit을 따른다. 예를 들어 Queen/Papaya는 terminal event cluster,
Quince는 같은 event-window의 BUY execution endpoint, Kiwi는 quote-complete signal
50건과 event cluster 30개가 최소 gate다.

## 운영 주의

- 신규 봇의 `data/`는 git에 커밋하지 않는다 (기존 3개 봇과 다른 점).
- 일반 전략의 시뮬레이션 손익은 midpoint 체결·슬리피지 0 가정이라 낙관 편향이다.
  Kiwi도 fresh entry limit→exit best bid의 top-of-book counterfactual일 뿐 depth 전체,
  queue, latency, partial fill과 fee를 증명하지 않는다.
- GTC 주문의 `accepted`/order ID는 fill이 아니다. 실제 성과는 confirmed fill evidence로만 확정하며, modern fill-state 전략의 BUY/SELL은 exact fill 대사 전 terminal로 간주하지 않는다.
- Kiwi simulation 결과는 실제 fill 또는 live 수익이 아니다. 코드의 live hard block을 우회하지 않는다.
- live-capable 전략의 private key는 Jenkins credential로만 주입한다. 스크립트
  파일·채팅에 평문 노출 금지. Kiwi simulation에는 credential 자체를 주입하지 않는다.
