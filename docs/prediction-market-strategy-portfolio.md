# Polymarket 전략 포트폴리오 (골든 시리즈)

기존 3개 + 신규 9개 = 총 12개 전략의 전체 지도. 각 전략의 상세 근거와 규칙은 각 폴더의 `STRATEGY.md`, 기존 봇 분석은 각 폴더의 `STRATEGY_ANALYSIS.md`, 사람이 읽기 좋은 HTML 버전은 `docs/strategy-pages/`, 회고 절차는 `docs/ab-retro-playbook.md` 참조.

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
| golden-apple | 80% 매수 / 90% 매도 | certainty effect (favorite 과소평가) | favorite 편승 | 0.80–0.90 | 운영 중 (2계정) |
| golden-banana | 85–97% + 골든크로스 | 모멘텀 지속 | favorite 편승 | 0.85–0.97 | 운영 중 (신호 결함 확인됨) |
| golden-cherry | Resolution Momentum | 마감 임박 확증 편향 + 수렴 | favorite 편승 | 0.75–0.92, 마감 1–30일 | **최고 수익, 운영 중** |
| golden-date | Conviction Ladder | cherry와 동일 + 시간 사다리 | favorite 편승 | 시간별 0.70–0.95 | 신규 |
| golden-elderberry | Panic Fade | 손실 회피 → 공황 투매 과잉반응 | 급락 역매수 | 0.35–0.75 (원래 favorite) | 신규 |
| golden-fig | Hope Crusher | favorite-longshot bias (복권 심리) | 롱샷 페이드 (NO 매수) | YES 0.05–0.25 | 신규 |
| golden-grape | Cascade Rider | 정보 폭포 / 과소반응 | 완만한 드리프트 편승 | 0.40–0.80 | 신규 |
| golden-honeydew | Night Watch | 주의(attention) 사이클 — 새벽·주말 부재 | 무근거 이탈 복원 | 0.30–0.90 | 신규 |
| golden-lime | Shock Follow | 대형 뉴스 불신·앵커링 (PEAD 유사) | 급등 편승 | 점프 후 ≤0.85 | 신규 |
| golden-mango | Patience Premium | 자본 잠김 회피(조급함) → settlement discount | favorite 캐리 | 0.85–0.985, ≤14일 | 신규 (문헌 도출) |
| golden-nectarine | Bottom Fisher | 손실 회피發 투매 오버슈트 | 롤링 최저가 역매수 | YES 0.03–0.50, 30일+ | 신규 (백테스트 복제) |
| golden-orange | Fear Spike Fade | probability neglect (공포의 확률 무시) | 공포 급등 페이드 (NO 매수) | base ≤0.15 → 스파이크 | 신규 (문헌 도출) |

포트폴리오 관점의 커버리지:

- **시간축**: 마감 임박(cherry/date) ↔ 마감 먼 구간(elderberry/grape) ↔ 시간 무관(honeydew)
- **방향**: 편승(date/grape/lime) ↔ 역행(elderberry/honeydew) ↔ 구조적 수렴(fig)
- **이벤트**: 급변 발생 시 elderberry(노이즈 가설)와 lime(정보 가설)이 서로 반대 베팅 → 자연스러운 A/B 쌍

## 신규 6개의 근거 요약

### golden-date — Conviction Ladder
cherry가 이미 돈을 벌고 있는 가설("마감이 다가오면 대중이 favorite으로 쏠리고, 시장 정확도는 24h 전 88.6% → 4h 전 94.2%로 수렴")을 유지하면서, 분석에서 확인된 cherry의 허점 5개를 수정한 직계 후계자다: 남은 시간과 무관한 고정 확률 밴드 → 시간 사다리(마감이 멀수록 싸게만 진입), --yes-only로 NO-favorite 시장 절반 폐기 → 양측 지원, 하락 중에도 매수 → 6h 모멘텀 게이트, rapid_jump 영구 skip → 쿨다운 재진입, 12h 전 조기 청산 → 2h 전까지 수렴 구간 수확.

### golden-elderberry — Panic Fade
손실 회피는 이득보다 약 2배 무겁게 작동한다. 악재·루머가 뜨면 보유자들이 공황 투매해 가격이 펀더멘털 아래로 오버슈팅한다. 레포 내 리서치 문서도 "10%+ 급변은 mean-revert"를 확인했다. 마감 48h+ 남은 시장으로 한정해 '마감 직전 급락 = 진짜 정보' 케이스를 배제하고, 45분 바닥 안정화를 확인한 뒤 진입해 떨어지는 칼날을 피한다.

### golden-fig — Hope Crusher
favorite-longshot bias는 예측시장 문헌에서 가장 잘 문서화된 편향이다 — 대중은 낮은 확률의 "복권"에 체계적으로 과지불한다. cherry가 이 편향의 favorite 쪽(과소평가)을 수확한다면, fig는 미러 이미지: "D일까지 X가 일어날까" 시장에서 마감이 다가와도 희망 보유자들이 앵커링으로 YES를 놓지 않는다. 시간이 소진되는 것 자체가 수익 동력이므로(NO는 1.0으로 수렴), 예측시장에만 존재하는 '만기'라는 구조를 가장 직접적으로 이용한다.

### golden-grape — Cascade Rider
뉴스는 대중에게 천천히 퍼진다(정보 폭포). 리서치 문서: "2–3%/일의 완만한 이동은 +6–8% 지속, 10%+ 급변은 회귀". banana의 골든크로스가 실패한 이유는 가설이 아니라 신호였다 — threshold 0.02가 사실상 도달 불가능해 모든 실거래가 cold-start 폴백으로 발생했다. grape는 같은 모멘텀 가설을 도달 가능한 신호(24h 일관 드리프트 +4~10pt, 4h 버킷 70% 일관성, 거래량 1.2배 가속)로 재구현하고, 드리프트 상한으로 mean-revert 영역을 배제한다.

### golden-honeydew — Night Watch
Polymarket 참여자 대다수는 미국 시간대의 사람이다. 미 동부 새벽 01–08시와 주말에는 호가가 얇아 소액 주문에도 가격이 밀리고, 아침에 주의가 돌아오면 복원된다. "24시간 계속 리퀘스트를 날릴 수 있다"는 우리의 구조적 우위를 가장 정면으로 수익화한다 — 이 전략의 경쟁자는 그 시간에 깨어 있을 수 없다. 거래량 급증 시 진입 금지(뉴스에 의한 진짜 이동 배제)로 무근거 이탈만 노린다.

### golden-lime — Shock Follow
대형 서프라이즈에 대중은 "설마"(불신)와 기존 가격 앵커링으로 일부만 반영한다 — 주식의 실적 발표 후 드리프트(PEAD)와 같은 구조. elderberry와 정반대 트리거의 의도적 A/B 쌍: 급변 이벤트에서 거래량이 미약하고 고점을 반납하면 노이즈(elderberry가 페이드), 거래량이 2배+ 폭증하고 고점을 유지하면 정보(lime이 편승). 두 가설을 동시에 실전 검증한다.

## 2차 신규 3개 (기존 전략과 독립적으로, 공개 문헌·백테스트에서 도출)

이 3종은 기존 골든 시리즈를 참조하지 않고 예측시장 문헌 리서치에서 독립 도출했다. 출처는 각 `STRATEGY.md`에 명기.

### golden-mango — Patience Premium
예측시장 참여자는 자본이 잠기는 것을 싫어해서, "거의 확실한" 계약도 만기까지의 기간만큼 할인되어 거래된다(settlement discount). 2026년 arXiv 논문 2편이 이 할인 기간구조를 실측했고(할인 보정 시 근확실 구간 왜곡의 48~88%가 소거), Kalshi 실증도 고가 계약의 양(+)의 수익률을 확인했다. 단일 수식 `y = ((1-p)/p) × (8760/남은시간) ≥ 2.0` 하나로 진입을 판정한다 — 대중의 조급함이 만든 할인을 봇의 인내로 수확한다. 골든크로스만큼 간결하지만, 근거는 가장 강하다.

### golden-nectarine — Bottom Fisher
장기 tail 시장에서 패닉/노이즈 매도가 가격을 일시적으로 누르면 반등한다. QuantPedia(2026-04)의 Polymarket 공개 백테스트를 충실 복제: `현재가 ≤ 20일 롤링 최저가 → 매수, 5일(120h) 보유 후 무조건 청산`. 10bps 비용 반영 후 CAR +18.9~22.1%로 생존한 유일한 공개 규칙. 소표본 과적합 위험은 STRATEGY.md에 명시.

### golden-orange — Fear Spike Fade
무서운 헤드라인 아래에서 대중은 확률이 아니라 결과의 끔찍함에 반응한다(probability neglect, Sunstein 2002). tail 시장 YES가 급등했다가 90~120분 내 되돌림의 60%가 발생한 실측 사례(이란 휴전 35→68→58%, 핵폭발 시장 19%)를 근거로, 스파이크가 스톨한 뒤 NO를 사서 공포 프리미엄 감쇠를 수확한다. fig(정적 theta)·lime(급등 편승)과 구분되는 이벤트 직후 감정 과잉 전담.

## 공통 인프라 개선 (신규 6개 봇 전체 적용)

기존 봇 분석에서 확인된 결함의 수정:

| 결함 (기존 봇) | 수정 (신규 봇) |
|---|---|
| 스냅샷 개수 기반 윈도우 — Jenkins 중단 시 왜곡 | timestamp 기반 윈도우 + 커버리지 검증, 데이터 부족 시 진입 금지 |
| cold start 시 히스토리 없음 | CLOB `/prices-history` 백필 (실패 시 조용히 스냅샷 축적으로 폴백) |
| condition_id당 영구 1회 거래 | 쿨다운(기본 24h) 후 재진입 허용 |
| 해결된 시장이 영원히 HOLDING | endDate+24h 경과 시 EXPIRED 처리 + 수동 redeem 경고 |
| 진입가 높으면 take_profit 도달 불가 | 목표가 0.99 캡 |
| `LOG_LEVEL` env 무시 | 지원 |
| Gamma 전체 sweep 2회/사이클 | 1회로 통합 |
| excluded_categories env 불가 | `POLYBOT_EXCLUDED_CATEGORIES` 지원 |

유지한 것(비교 가능성): GTC 지정가(midpoint) 주문 + 체결 가정, `POLYBOT_BUY_AMOUNT` 등 env 이름, `data/<job>/` 분리, py-clob-client-v2, 1실행=1사이클.

## 6개월 롤아웃 제안

한 달에 1–2개씩, 시뮬레이션 → 소액 실전 → 증액의 3단계. 순서는 "기존에 입증된 가설과의 거리" 기준:

| 월 | 투입 | 이유 |
|---|---|---|
| M1 | **date** | cherry 가설 그대로 + 버그 수정만 — 가장 낮은 리스크. cherry와 직접 A/B (같은 기간, 같은 금액) |
| M2 | **fig** | cherry의 미러. 문헌 근거 최강. 마감 수렴이라는 이미 입증된 동력 공유 |
| M3 | **elderberry** | 독립 가설 1호. 리서치 문서의 mean-revert 근거 |
| M4 | **grape** | banana의 교훈을 반영한 재도전 |
| M5 | **lime** | elderberry와 A/B 쌍으로 동시 평가 시작 |
| M6 | **honeydew** | 시간대 데이터가 충분히 쌓인 뒤 판단 (스냅샷 축적 필요성 최대) |

2차 3종은 월 2개 페이스로 병행 투입 가능: **mango**는 근거가 가장 강해 M1~M2에 date와 병행 A/B를 권장, **orange**는 fig/lime과 같은 계열이므로 M2~M3에, **nectarine**은 백테스트 복제 검증 목적으로 아무 때나 소액 병행.

### A/B 판단 기준 (제안)

- 각 전략 최소 **4주 + 30건 이상** 거래 후 판단.
- 지표: 총손익, 거래당 평균 손익, 승률, 최대 낙폭(MDD), exit_reason 분포 (stop_loss 비중이 40%+면 진입 조건 재검토).
- 입증되면 env 변수만 바꾼 베리에이션(A-1/A-2/A-3)을 별도 `--job`으로 병행 — 각 폴더 STRATEGY.md의 베리에이션 절 참조.
- 모든 봇이 `data/<job>/trades.db`에 동일 스키마로 기록하므로 폴더 간 비교 집계가 쉽다. 채택 시 `daily-report`의 계정 목록에 추가할 것.

## 운영 주의

- 신규 봇의 `data/`는 git에 커밋하지 않는다 (기존 3개 봇과 다른 점).
- 시뮬레이션 손익은 midpoint 체결·슬리피지 0 가정이라 낙관 편향 — 소액 실전 단계를 생략하지 말 것.
- GTC 주문 체결을 확인하지 않는 한계는 기존 봇과 동일하게 존재한다 (특히 급락장의 stop_loss). 각 STRATEGY.md의 "알려진 구현 한계" 참조.
- private key는 Jenkins credential로만 주입한다. 스크립트 파일·채팅에 평문 노출 금지.
