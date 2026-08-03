# golden-quince A/B/C 실행 런북 (자립형)

> **이 문서 하나만 읽고 실험을 시작할 수 있게 만들었다.** 다른 문서를 열지 않아도
> 되도록 Jenkins shell·금액·기간·판정 기준을 전부 안에 넣었다.
> 더 깊은 근거가 필요할 때만: `golden-quince/STRATEGY.md`(설계 근거),
> `docs/retro/golden-quince.md`(30일 회고 절차), `golden-quince/README.md`(전체 env).

작성 2026-07-31 · 대상 commit 기준 `golden-quince/config.yaml`

---

## 0. 30초 요약

| 질문 | 답 |
|---|---|
| 무슨 전략인가 | **Spread Harvest.** 방향성 예측을 포기하고 **매수 주문의 틱 반올림 방향**(maker/taker)만을 수익원으로 검정한다 |
| 몇 개 팔인가 | **핵심 3개(A/B/C).** 4번째(D)는 선택. **4개로 돌려도 되지만 A·B·C가 그 중 셋이어야 한다** |
| 건당 거래액 | **$5** (A/B/C 전부 동일). 선택적 D만 $10 |
| 지갑당 자금 | **$120~150** (A/B/C), D는 **$200** |
| 총 예산 | 3팔 **$400**, 4팔 **$600** |
| 기간 | **30일.** day-1 점검 → day-7 표본속도 점검 → day-30 판정 |
| 무엇으로 판정하나 | **MAKER 체결 비중 / 진입 실효가−midpoint(bps) / 체결률.** 승률·순손익 아님 |

---

## 1. 이 전략은 무엇인가

**"시장을 이기려 하지 않는다. 스프레드를 내는 쪽이 아니라 받는 쪽에 선다."**

2026-07-28~29에 5개 전략(cherry·lime·fig·mango·date)을 실측 검증하고 4개를 폐쇄했다.
가설은 전부 달랐는데 결과는 같았다.

| 검정한 것 | 결과 |
|---|---|
| 확률 예측 edge | **없음.** 99개 셀 중 BH q=0.05 통과 0개 |
| 경로 구조(평균회귀) | **실재하나 작다.** rho=−0.0901, t=−26.2인데 되돌림이 7.9 bps로 틱(≈100bps)보다 훨씬 작다 |
| 청산 규칙 edge | **없음.** 통합 785건 +0.44pp, p=0.74 |
| **실행 측면** | **진입 leg에서 ~73 bps 차이 — 유일하게 남은 것** |

실측 진입 비용($5 코호트, 결정 midpoint 대비 실효 체결가):

| 진입 leg | 비용 | 95% CI |
|---|---|---|
| MAKER | **−16.8 bps** | [−19.1, −14.3] |
| TAKER | **+56.7 bps** | [+54.3, +59.0] |

**차이 ≈73 bps. 우리가 탐지한 어떤 방향성 edge보다 크고, 완전히 우리 통제 아래 있고,
분산이 거의 없다(CI 폭 ±2 bps).** 마지막 성질이 §5의 검정력을 만든다.

### 메커니즘

기존 14개 봇의 `_round_to_tick`은 매수/매도를 구분하지 않고 가장 가까운 틱으로 반올림한다.
호가가 0.79/0.80이면 midpoint는 0.795다. 0.80으로 올라가면 **매도호가를 리프트해 taker**,
0.79로 내려가면 **매수호가에 합류해 maker**가 된다. 어느 쪽이 될지가 전략적 선택이 아니라
**반올림의 부산물**이었다.

quince는 이 축을 설정으로 고정한다. 그게 전부다.

| `POLYBOT_EXECUTION_MODE` | BUY | SELL | 역할 |
|---|---|---|---|
| `passive` | **내림** (매수호가 합류) | `nearest` | **A = 처치군** |
| `nearest` | 반올림 | `nearest` | **B = 대조군** (기존 14봇과 동일) |
| `cross` | **올림** (크로스) | `nearest` | **C = 비용 상한** |

> **SELL은 세 팔 모두 `nearest`로 고정된다. 코드가 그렇게 강제한다.**
> passive를 SELL에 적용하면 최우선 매수호가보다 **위에** 걸려 `0.85` 손절이 체결되지 않는다.
> **체결되지 않는 손절은 손절이 아니다.** 부수 효과로 청산이 전 팔에서 동일해져
> 진입 축의 대비가 더 깨끗해진다.

### 진입/청산 규칙 (세 팔 전부 동일 — 처치가 아님)

golden-queen의 Crown Momentum을 **의도적으로 그대로 상속**했다. 신호가 아니라 실행이
처치이므로 신호는 전 팔에서 같아야 한다.

```text
진입 = 표준 이진 시장(outcomes = [Yes, No], negRisk = false)
     + YES-only
     + 직전 저장 YES < 0.90  AND  현재 YES ∈ [0.90, 0.94]   ← 최초 상향 교차만
     + 두 snapshot 간격 0분 초과 15분 이하
     + 과거 60일 archive에 YES ≥ 0.90 관측이 없었음
     + 비스포츠·경기 전: 종료까지 (0h, 24h]
     + 스포츠 경기 중: kickoff 후 360분까지
     + event_id 필수, event당 동시 1개
     + fresh spread ≤ 0.02, best ask ≤ 0.94, ask depth ≥ 주문 수량 × 1.2

청산 = fresh YES ≥ 0.98 AND 실행가능 bid ≥ 0.98   → take_profit
     또는 fresh YES ≤ 0.85                        → absolute_stop
     trailing stop 없음, time exit 없음
```

### 실패 시나리오 (falsification)

1. **역선택이 스프레드보다 크다.** ← 가장 유력. 패시브 체결이 1틱 싸도, 그 체결이
   "계속 내려갈 시장"에 몰리면 순손실이다. **이 실험이 재려는 것이 정확히 이것이다.**
2. **체결이 안 된다.** 패시브 체결률 ~39%, 0.90~0.95 밴드 $5 주문 체결률 36.2%.
3. **미체결분이 유령이 된다.** 미체결 $5 BUY의 36.4%가 INVALID로 전환돼 `max_positions`를 잠식.
4. **신호가 너무 희소하다.** queen은 ~15,500개를 훑어 후보 2건. 진입이 0에 수렴하면 A/B가 성립 안 함.
5. **수수료가 실제로 부과된다.** 기록된 fill 수수료는 전부 0/NULL인데 Gamma는 94% 시장에
   `fee_rate` 0.04~0.07을 붙인다. **미해결 — 첫 실체결에서 반드시 확인.**

---

## 2. 팔 구성 — "4개 맞나요?"

**핵심은 3개다. 4개로 돌려도 되지만, 그 4개 안에 A·B·C가 반드시 들어가야 한다.**

| 팔 | Jenkins job = `--job` | `POLYBOT_EXECUTION_MODE` | 금액 | 필수? |
|---|---|---|---:|---|
| **A** | `polybot-quince-passive` | `passive` | $5 | **필수 (처치군)** |
| **B** | `polybot-quince-nearest` | `nearest` | $5 | **필수 (대조군)** |
| **C** | `polybot-quince-cross` | `cross` | $5 | **필수 (비용 상한)** |
| D | `polybot-quince-passive-10` | `passive` | **$10** | 선택 (크기 효과) |

### 왜 대조군이 둘(B·C)인가

B만 있으면 "A가 B보다 낫다"를 봐도 실행 때문인지 우연인지 모른다. **C가 있으면
예측된 순서가 생긴다:**

```
MAKER 비중:   A  >  B  >  C
진입 비용:    A  <  B  <  C     (bps, 결정 midpoint 대비 / A는 음수여야)
체결률:       A  <  B  <  C
```

**이 부등식이 깨지면 우리 비용 모형이 틀린 것**이고, 그 자체가 이 실험의 1차 산출물이다.
단일 대조군으로는 얻을 수 없는 falsifiability다.

### 계좌가 부족하면

| 가용 지갑 | 무엇을 돌리나 | 대가 |
|---|---|---|
| 4개 | A + B + C + D | 최선 |
| **3개** | **A + B + C** | **권장 기본값. 손실 없음** |
| 2개 | **A + B** (C 아님) | 순서 검정 불가 → null 결과의 해석이 훨씬 어려워짐 |
| 1개 | **하지 않는다** | 단일 팔은 A/B가 아니다. 비교 대상이 없다 |

2개일 때 A와 C가 아니라 **A와 B**인 이유: B가 기존 14봇이 실제로 하는 동작이므로
**의사결정에 직결되는 비교는 A vs B**다. C는 모형 검증용이다.

**D는 A·B·C 중 무엇도 대체할 수 없다.** D는 `passive`에서 금액만 다르므로
실행 모드 비교에 합치면 처치가 두 개 섞인다.

---

## 3. 금액과 예산

### 건당 거래액: $5 — 바꾸지 않는다

$5인 이유는 임의 선택이 아니다. 위 §1의 실측 비용표(MAKER −16.8 / TAKER +56.7 bps,
왕복 2,139건)가 **$5 코호트에서 측정된 값**이다. 같은 금액을 써야 근거와 실험이
같은 기반 위에 선다.

$5에서 자동 파생되는 gate (`config.yaml`이 금액에서 계산):

| 항목 | 값 |
|---|---:|
| metadata 유동성 하한 | `max($10,000, 5/0.001)` = **$10,000** |
| 24h 거래량 하한 | `max($2,000, 5/0.02)` = **$2,000** |
| 팔당 open notional 상한 | `$5 × 10` = **$50** |
| 동시 open 포지션 상한 | 20 (단 notional $50이 먼저 물려서 실질 **10개**) |

### 지갑당 자금과 kill switch — 두 숫자는 다르다

**여기서 헷갈리기 쉬우니 명확히 분리한다.**

| 숫자 | 값 | 성격 | 손대도 되나 |
|---|---:|---|---|
| `experiment_capital_usdc` | **200** | **kill switch 분모.** `config.yaml`에 사전 등록 | **❌ 절대 변경 금지.** 바꾸면 `config_hash`가 달라져 cohort가 갈린다 |
| `max_drawdown_stop` | **0.20** | kill switch 비율 | **❌ 변경 금지** |
| 실제 지갑 입금액 | $120~150 | **운영 결정.** config와 무관 | ✅ 자유 |

> **결과: kill switch는 지갑 잔고와 무관하게 경제손익 −$40에서 절대값으로 발동한다.**
> $200 × 20% = $40이다. 지갑에 $120을 넣었다면 그 −$40은 지갑의 20%가 아니라 **약 33%**다.
> 이것은 버그가 아니라 의도다 — 사전 등록한 정지선은 입금액에 따라 흔들리면 안 된다.

**팔당 권장 입금:**

| 팔 | 입금 | 근거 |
|---|---:|---|
| A / B / C | **$120~150** | 회전 노출 $50 + kill switch 손실 $40 + 정산 타이밍 버퍼 |
| D (선택) | **$200** | open notional이 $100이라 회전 노출이 2배 |

**총 예산:**

| 구성 | 총액 |
|---|---:|
| 3팔 (A/B/C) | **$360~450** → 넉넉히 **$400** |
| 4팔 (+D) | **$560~650** → 넉넉히 **$600** |

**팔당 최악의 손실은 약 −$90이다** (kill switch −$40 발동 시점에 열려 있던 최대
$50 노출이 전부 0으로 해결되는 경우). 3팔 최악 −$270. 이것이 이 실험의 실질적 손실 한도다.

> **D는 A보다 먼저 멈출 수 있다.** $10 거래는 같은 −$40에 절반의 손실 건수로 도달한다.
> D가 먼저 정지해도 그것은 신호가 아니라 **금액 차이의 산술적 귀결**이다. 그렇게 읽어야 한다.

---

## 4. 기간 — 30일, 단 체크포인트 2개

### 일정

| 시점 | 할 일 | 실패 시 |
|---|---|---|
| **day 0** | 세 팔 **동시 기동** (§6 체크리스트) | — |
| **첫 체결 시점** (day 1이 아닐 수 있음) | 각 팔의 **첫 CONFIRMED 체결**에서 §6의 kill-check 3개 확인 | 즉시 중단·수정·재시작 (표본 리셋) |
| **day 7** | **A의 CONFIRMED BUY 건수**를 세고 30일 투영 | 아래 "표본 부족" 분기 |
| **day 30** | 사전 등록 판정 (§5) | — |

### day-7 표본 속도 점검 — 이게 제일 중요한 중간 관문

1차 판정은 **A의 CONFIRMED BUY가 30건 이상**이어야 성립한다. 30건 미만이면 결과와
무관하게 **판정 불가(INCONCLUSIVE)** 다.

```
day 7에 A의 CONFIRMED BUY < 7건  →  30일에 30건 도달 어려움
```

이때 선택지는 둘뿐이다:

| 선택 | 방법 | 평가 |
|---|---|---|
| **연장** | 아무것도 바꾸지 않고 **60일까지 계속** | **권장.** cohort가 온전히 유지된다 |
| 재시작 | 진입 조건을 완화하고 **새 cohort로 다시 시작** | 그때까지의 표본은 버린다 |

**❌ 절대 하면 안 되는 것: 돌아가는 중에 조건을 완화하는 것.** 그러면 A의 표본이
두 cohort의 혼합이 되어 30일치가 통째로 못 쓰게 된다.

### ⚠️ C가 "다 찼다"고 멈추지 말 것

체결률이 팔마다 다르다 — A ≈36~39%, C ≈91%. **C는 A보다 두 배 이상 빨리 30건을 채운다.**
판정 규칙은 **A의 건수**를 본다. C의 숫자가 예뻐 보인다고 조기 종료하면 처치군의 표본이 없다.

---

## 5. 판정 기준 (사전 등록 — 지금 정하고 나중에 안 바꾼다)

### 왜 승률·순손익이 1차가 아닌가

순손익은 해결 결과가 지배한다. p≈0.92에서 YES는 +8.7%, NO는 −100%이므로 건당 표준편차가
**약 2,900 bps**다. 여기서 ~73 bps 차이를 80% 검정력으로 잡으려면 팔당 **수만 건**의
왕복이 필요하다. **30일 25건으로 "A ≤ B"를 판정하면 그건 동전던지기다.**

반면 처치가 실제로 움직이는 양은 분산이 거의 없다(CI 폭 ±2 bps). 이 축에서는
**팔당 30~50 체결이면 결정적**이다.

### 1차 종점 (30~50 체결이면 판정 가능)

| 지표 | 어디서 | 예측 |
|---|---|---|
| MAKER 체결 비중 | `order_fills.liquidity_role = 'MAKER'` (CONFIRMED BUY) | **A > B > C** |
| 진입 실효가 − 결정 midpoint (bps) | CONFIRMED BUY VWAP vs same-cycle midpoint | **A < B < C**, A는 음수 |
| 체결률 | CONFIRMED BUY / accepted BUY | **A < B < C** |

```
entry_cost_bps = (entry_vwap - decision_midpoint) / decision_midpoint * 10,000
```

### 2차 종점 (관측만 — 명시적으로 검정력 부족)

| 지표 | 상태 |
|---|---|
| 순손익 A vs B | **표본 부족으로 판정 불가.** 부호만 기록, 결론에 사용 금지 |
| 역선택 (체결 후 15분/60분 midpoint 표류) | 가격 표류라 해결 결과보다 분산이 훨씬 작아 낮은 n에서도 의미 있음 |

### 30일 결정표

| 조건 | 결정 |
|---|---|
| 상시 경제손익(확정+해결추정) ≤ **−$40** | **자동 중단** — 코드가 이미 막음. 발동 전후를 별도 cohort로 분리 |
| A의 CONFIRMED BUY < 30건 | **INCONCLUSIVE** — 표본 부족. 연장 또는 조건 완화 후 재시작 |
| A의 MAKER 비중이 B보다 유의하게 높지 않음 | **IMPLEMENTATION_FAIL** — 처치가 작동조차 안 했다 |
| A의 진입 비용(bps)이 B보다 낮지 않음 | **STOP** — 실행 측면 가설 기각 |
| `A>B>C` / `A<B<C` 순서가 반복적으로 깨짐 | **STOP/DIAGNOSE** — 비용 모형 재검토 |
| 순서 성립 **AND** A의 15/60분 표류 < 진입 할인 | **CONTINUE** — 금액 증액 검토, D 추가 검토 |

### 30일 회고 명령

```bash
export REVIEW_START=2026-08-01          # 실제 기동일로 교체
export REVIEW_END_EXCLUSIVE=2026-08-31
export REVIEW_AS_OF=2026-08-30          # exclusive end의 전날
export REVIEW_DAYS=30

cd daily-rsync
uv run daily-rsync verify --job polybot-quince-passive --strategy golden-quince
uv run daily-rsync verify --job polybot-quince-nearest --strategy golden-quince
uv run daily-rsync verify --job polybot-quince-cross   --strategy golden-quince
cd ..

uv run --project polybot-observability polybot-retro audit \
  --db <verified-passive-db> \
  --db <verified-nearest-db> \
  --db <verified-cross-db> \
  --days "$REVIEW_DAYS" --as-of "$REVIEW_AS_OF" \
  --output-dir "$HOME/polybot-retro/quince-execution-30d" \
  --strict
```

`CRITICAL`/`HIGH` issue나 evidence gap이 있으면 **집계는 진단용으로만 하고 결론을 만들지 않는다.**

### 결론 형식

```text
Decision: INCONCLUSIVE | IMPLEMENTATION_FAIL | STOP | CONTINUE | ADD_OPTIONAL_D_10
Evidence window [start, end):
Verified DB SHA-256:
Cohorts (config_hash × git_commit × mode × job_name):
CONFIRMED BUY n by arm:
MAKER share by arm:
Entry cost bps by arm:
Fill rate by arm:
15m / 60m adverse-selection drift:
Actual net P&L (secondary, underpowered):
Kill-switch status:
Primary limitation:
Next review date:
```

---

## 6. 기동 절차

### day-0 체크리스트

- [ ] 팔마다 **서로 다른 wallet / credential / funder**. 같은 지갑을 공유하면 A/B/C 격리가 아니다
- [ ] 팔마다 **서로 다른 Jenkins job 이름 = `--job` 값 = DB 경로**
- [ ] 세 job **같은 Git commit**, cadence 모두 `H/5 * * * *`, concurrent build 비활성화
- [ ] **세 팔을 같은 시각에 기동** (아래 ⚠️ 참조)
- [ ] private key·funder는 Jenkins **Credentials Binding**. shell 첫머리에 `set +x`
- [ ] `uv run polybot config --job <name>`으로 해석된 값을 먼저 눈으로 확인

> ### ⚠️ 세 팔은 반드시 동시에 기동한다
>
> 각 팔은 **자기 DB에 자기 archive를 따로 쌓는다.** "최초 0.90 상향 교차"는
> 그 팔의 archive 안에서의 최초다. 팔을 하루 늦게 켜면 그 팔은 **다른 후보 집합**을 보게 되고,
> `event_id × 교차 시각 창` pairing이 깨져 A/B 비교의 근거가 사라진다.
> 이것은 어느 커밋된 문서에도 크게 적혀 있지 않으므로 여기에 적어둔다.

### kill-check — 각 팔의 첫 CONFIRMED 체결에서 반드시 확인

> **day 1에 안 나올 수 있다.** 신호가 희소하고 A의 체결률이 ~36%이므로 첫 체결까지
> 며칠 걸릴 수 있다. **조용한 첫날은 실패 신호가 아니다.** 하지만 첫 체결이 나오는
> 순간 아래 셋을 즉시 본다. 셋 중 하나라도 실패하면 **30일을 돌려도 아무것도
> 측정되지 않는다.** 30일 뒤에 발견할 일이 아니다.

1. **`order_fills.liquidity_role`이 채워지는가.**
   1차 종점 전체가 이 컬럼의 MAKER 비중이다. live fill에서 NULL로 돌아오면
   **실험 자체가 무의미**하다. 첫 체결에서 즉시 확인한다.

   이 컬럼을 채우는 주체는 quince 자체 코드가 아니라 **`polybot-observability`의
   `ExecutionLedger.record_fill()`** 이다 (quince `api/clob_client.py`가 대사 단계에서
   호출한다). 이 경로는 **live에서만 동작한다** — simulation에서는 채워지지 않으므로
   sim 결과로 이 검사를 통과했다고 판단하지 않는다.

   ```bash
   sqlite3 golden-quince/data/polybot-quince-passive/trades.db \
     "SELECT order_id, side, status, liquidity_role, size, price,
             fee_rate_bps, fee_amount_usdc, matched_at
        FROM order_fills ORDER BY matched_at DESC LIMIT 5;"
   ```

   DB는 Jenkins workspace에 있으므로 그쪽에서 직접 조회하거나 `daily-rsync`로
   내려받은 local catalog 경로에 대해 실행한다. 테이블 이름이 없다고 나오면
   `order_fills_v2`로 한 번 더 시도한다(스키마 마이그레이션 경로가 둘이다).

   `liquidity_role`은 `MAKER` / `TAKER` / `UNKNOWN` 셋 중 하나다. **`UNKNOWN`이나
   NULL이 계속 나오면** CLOB trade 응답의 `maker_orders` 매칭이 실패한 것이므로
   그 fill은 1차 종점 모집단에서 빠진다. 첫날 `UNKNOWN` 비율이 높으면 즉시 진단한다.

2. **`POLYMARKET_SIGNATURE_TYPE`이 지갑마다 맞는가.** `1`=POLY_PROXY(구형 이메일 계정),
   `3`=POLY_1271(2026년 이후 신규 계정의 스마트 지갑). **틀리면 CLOB이 전 주문을
   `maker address not allowed`로 거절**한다. 새로 만든 지갑이면 보통 `3`이지만 계정마다 다르다.
   **"job은 초록불인데 거래가 0건"의 가장 흔한 원인이 이것이다.**

3. **`fee_amount_usdc`가 정말 0/NULL인가.** 지금까지 기록된 fill의 수수료는 전부 0/NULL인데,
   Gamma 메타데이터는 94% 시장에 `fee_rate` 0.04~0.07을 선언한다. **이 불일치는 미해결이다.**
   실제로 수수료가 붙는다면 73 bps 처치 효과의 상당 부분이 사라진다.

   위 쿼리에서 `fee_rate_bps`와 `fee_amount_usdc`를 **함께** 본다. 판독법:

   | `fee_rate_bps` | `fee_amount_usdc` | 해석 |
   |---|---|---|
   | 0 / NULL | 0 / NULL | 지금까지의 기록과 동일 — 수수료 없음. 계속 진행 |
   | **> 0** | **0 / NULL** | **불일치의 정체.** 요율은 선언되나 부과는 안 됨. 관측만 기록 |
   | > 0 | > 0 | **실제 부과.** 73 bps 가정을 재계산하기 전에는 증액하지 않는다 |

   > **2026-08-03 갱신 — 이 질문은 부분적으로 이미 풀렸다.**
   > golden-elderberry의 CONFIRMED fill 556건에서 `liquidity_role`로 쪼개면
   > **TAKER 111건은 전부 `fee_rate_bps = 0.0`, MAKER 445건은 전부 NULL**이다.
   > `execution_ledger.py`가 부재 시 `None`을 쓰고 0으로 coalesce하지 않으므로
   > 그 `0.0`은 **거래소가 실제로 요율 0을 보고한 값**이다. 수수료가 물린다면
   > 나타날 곳이 바로 taker leg인데 거기가 0이었다. 다만 `fee_amount_usdc`는
   > 전건 NULL이라 완결된 답은 아니므로, 위 확인은 그대로 수행한다.
   > 근거: [golden-elderberry 2026-08 파라미터 리뷰](retro/golden-elderberry-2026-08-parameter-review.md) §5-1

### Jenkins shell — A (passive, 처치군)

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"
: "${POLYMARKET_SIGNATURE_TYPE:?set 1 or 3 for wallet A}"
export POLYBOT_EXECUTION_MODE=passive
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_ENTRY_HOURS_MAX=24
export LOG_LEVEL=INFO
cd ./golden-quince
uv sync --frozen
uv run polybot config --job polybot-quince-passive
uv run polybot run --live --job polybot-quince-passive
```

### Jenkins shell — B (nearest, 대조군)

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"
: "${POLYMARKET_SIGNATURE_TYPE:?set 1 or 3 for wallet B}"
export POLYBOT_EXECUTION_MODE=nearest
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_ENTRY_HOURS_MAX=24
export LOG_LEVEL=INFO
cd ./golden-quince
uv sync --frozen
uv run polybot config --job polybot-quince-nearest
uv run polybot run --live --job polybot-quince-nearest
```

### Jenkins shell — C (cross, 비용 상한)

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"
: "${POLYMARKET_SIGNATURE_TYPE:?set 1 or 3 for wallet C}"
export POLYBOT_EXECUTION_MODE=cross
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_ENTRY_HOURS_MAX=24
export LOG_LEVEL=INFO
cd ./golden-quince
uv sync --frozen
uv run polybot config --job polybot-quince-cross
uv run polybot run --live --job polybot-quince-cross
```

### Jenkins shell — D (선택, passive + $10)

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"
: "${POLYMARKET_SIGNATURE_TYPE:?set 1 or 3 for wallet D}"
export POLYBOT_EXECUTION_MODE=passive
export POLYBOT_BUY_AMOUNT=10          # ← A와 다른 유일한 값
export POLYBOT_ENTRY_HOURS_MAX=24
export LOG_LEVEL=INFO
cd ./golden-quince
uv sync --frozen
uv run polybot config --job polybot-quince-passive-10
uv run polybot run --live --job polybot-quince-passive-10
```

**세 shell의 차이는 `POLYBOT_EXECUTION_MODE` 한 줄뿐이다** (D만 `POLYBOT_BUY_AMOUNT`도 다름).
다른 줄이 팔마다 다르면 그건 A/B 테스트가 아니다.

---

## 7. ⛔ 이것을 하면 실험이 무효가 된다

여기저기 흩어져 있던 무효화 조건을 한자리에 모았다. **이 문서가 존재하는 진짜 이유가 이 목록이다.**

| # | 하면 안 되는 것 | 왜 |
|---|---|---|
| 1 | 실행 중에 `POLYBOT_EXECUTION_MODE` 변경 | 처치축 자체를 바꾸는 것. 표본이 두 cohort 혼합이 됨 |
| 2 | 실행 중에 `POLYBOT_BUY_AMOUNT` 변경 | 금액이 유동성·거래량·노출 gate를 자동 변경 → 후보 집합이 달라짐 |
| 3 | 실행 중에 `POLYBOT_ENTRY_HOURS_MAX` 변경 | 신호 horizon은 전 팔 24h 고정. 실험 축이 아님 |
| 4 | 실행 중에 `experiment_capital_usdc` / `max_drawdown_stop` 변경 | `config_hash`가 바뀌어 cohort가 갈림 |
| 5 | 팔끼리 wallet / job 이름 / DB 공유 | 서로의 노출을 몰라 live 격리가 성립 안 함 |
| 6 | 팔을 시차를 두고 기동 | archive가 달라져 "최초 교차" 후보 집합이 달라짐 (§6 ⚠️) |
| 7 | 표본이 안 쌓인다고 **중간에** 진입 조건 완화 | 연장하거나, 새 cohort로 재시작. 중간 변경은 전체를 버리는 것 |
| 8 | D를 A·B·C 중 하나 대신 돌리기 | D는 금액이 다르므로 실행 모드 비교에 못 섞음 |
| 9 | 승률·순손익으로 판정 | 건당 sd ≈2,900 bps. 30일 표본이 73 bps를 담을 수 없음 (§5) |
| 10 | `trades.realized_pnl`을 성과로 사용 | 그건 **요청가 × 요청수량**이다. `order_fills.status='CONFIRMED'`만 유효 |
| 11 | 해결됐다고 1.00 synthetic SELL 만들기 | 해결 정산은 `settlement_pnl_assumption`에 분리 기록. 실현 P&L 아님 |
| 12 | kill switch 우회·비활성화 | golden-date가 판정 기준을 문서에만 두고 계좌 절반을 잃은 실패의 재현 |
| 13 | simulation과 live 결과 합산 | 별도 hypothetical cohort로 유지 |
| 14 | C가 30건 찼다고 조기 종료 | 판정은 **A의 건수**를 본다. C는 체결률이 2배 이상 높다 (§4) |

---

## 8. 참고 링크 (필요할 때만)

| 문서 | 내용 |
|---|---|
| `golden-quince/STRATEGY.md` | 설계 근거 전문, 경쟁 가설, falsification |
| `golden-quince/README.md` | 전체 환경변수 표, archive 계약, lifecycle |
| `golden-quince/AGENTS.md` | 변경 불가 전략 계약 |
| `docs/retro/golden-quince.md` | 30일 회고 절차 (복붙용 프롬프트 포함) |
| `docs/retro/2026-07-29-execution-cost-floor.md` | 왕복 실행 비용 실측 (이 전략의 근거) |
| `docs/retro/2026-07-29-market-structure-study.md` | 캘리브레이션·평균회귀 검정 |
| `docs/retro/closed-strategies-postmortem.md` | lime·fig·mango·date 폐쇄 통합 회고 |
| `docs/strategy-pages/strategy-quince.html` | HTML 전략 페이지 |
