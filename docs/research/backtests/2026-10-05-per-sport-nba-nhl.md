# 2026-10-05 종목별 설정: watermelon·apricot·plum 의 NBA·NHL 확장과 apricot MLB 재판정

- 작성 2026-10-05 · 연구자 결정 `sports3:per-sport`, `apricot:edge-negative`(reports/decisions.md 2026-10-05)
- 데이터: 운영 `core.db`·`books/`·`strategies/*.db` 를 `VACUUM INTO` 로 만든 사본(2026-10-05 11:56 KST)을
  `/Volumes/t7/polylab/scratch/persport-1005` 에서 `POLYLAB_ROOT` 로 재생했다. 운영 경로는 읽기만 했고, 사본은 작업 후 삭제했다.
- **이 문서의 손익은 모두 paper replay 이며 실현 손익이 아니다.**

## 1. 무엇을 바꿨나 (코드)

- `strategies/*.yaml` 의 `sports` 를 목록 대신 **종목별 매핑**으로 쓸 수 있다:
  `sports: {soccer: {mode: live, stake_usdc: 5}, nba: {mode: paper, stake_usdc: 5, limits: {...}}}`.
  종목의 실제 모드는 변형 `mode`(마스터 스위치: off=전부 중지, paper=전부 paper)와 종목 `mode` 중 엄격한 쪽이다.
  종목별 파라미터는 기존 `params.sport_overrides.<종목>` 이다. 목록 형식(cherry·goal-over·llm-nil 등)은 동작이 그대로다.
- 엔진: 종목별 변형은 한 분에 live 다리(live 종목)와 paper 다리(paper 종목)를 따로 돈다. 진입 단위는 종목의 `stake_usdc`,
  종목 `limits` 가 있으면 변형 한도와 함께 검사한다. live→paper 로 바뀐 종목이 live 포지션을 들고 있으면 진입 없는 live 다리가
  대사·정산·청산을 계속한다. param version 은 모드별로 비교한다(두 다리가 매 분 새 버전을 만들지 않게).
- ladder: (변형, 종목)마다 그 종목의 정산 거래·`stake_events.sport` 로 판정한다(기존 원장은 `sport` 열을 ALTER 로 추가, 종목 없는
  옛 이벤트는 모든 종목에 적용). 리포트·대시보드 스냅샷에 `sports_detail[]`(종목별 mode·단위·ladder)를 추가했다(계약은 추가만).
- validator·autopilot: 제안에 `"sport"` 를 쓸 수 있다. params 는 `sport_overrides.<종목>.<이름>` 으로, stake·mode 는 그 종목만.
  종목별 변형의 params·stake 제안은 종목이 필수다(`sport_overrides.<종목>.*` 키만 쓰면 종목으로 추론). 표본·cooldown·증액 게이트는
  종목별이고, 종목의 "현재 파라미터" 기간은 그 종목의 실효 파라미터로 센다(NBA override 를 추가해도 soccer 표본 26건이 유지됨을 확인).
  **live 로 올리는 것은 여전히 사람만.**
- plum: `max_wall_minute`(미국 종목, 예정 시작 후 이 분이 지나면 진입하지 않음) 추가.
  apricot: `entry_game_minute`(경기 시계 tick, opt-in, 기본 null) 추가. **NBA·NHL 과거 경기 시계가 없어서**(game_states 는 2026-09-30
  이후만) 검증 가능한 것은 벽시계뿐이다. 모든 설정은 벽시계(tick0 이후 분)를 쓰고, 경기 시계는 올 시즌 상태 데이터로 검증한 뒤에 켠다.
  지금 수집되는 NBA 상태도 아직 믿기 어렵다(예: "End Q4" 의 game_minute 가 48 이 아니라 36).

## 2. 방법

- 범위: NBA 2026-02-01~10-05 경기 중 moneyline 거래량 2만 달러 이상 **441경기**, NHL **431경기**(대부분 2–6월, NHL 9–10월 프리시즌 포함),
  MLB(apricot) 2026-03-01~10-05 2,433경기. (전체 경기 수 NBA 644·NHL 661 중 실거래 범위와 같은 거래량 하한을 넘은 경기만.)
- 빠른 평가기(scratch 전용, 저장소에 넣지 않음): 경기마다 엔진과 같은 규칙(60초 step, 합성 호가 = 1분 가격 ±0.005·무한 깊이, 120초 신선도,
  paper broker 체결, fee schedule, 정확한 정산)으로 재생. **엔진 대조**: `polylab backtest` 와 watermelon NBA 455 vs 457건
  (ROI −1.77% vs −1.75%), apricot NBA 208 vs 208건(−3.09% vs −3.08%), plum NHL 312 vs 311건(−0.20% vs −0.19%). MLB apricot 현재값은
  10-04 문서 수치를 그대로 재현했다(eco −0.94%, fruit −0.40%).
- grid: watermelon 539셀(prob_min 0.85–0.97 × stop 0.50–0.85 × 조기 익절 delta 없음/0.01–0.06), apricot 2,016셀(벽시계 tick 30–160분 ×
  prob_min 0.80–0.94 × prob_max 0.97/0.99/0.999 × TP 0.90–0.99; MLB 는 tick 75–150분 1,728셀), plum 1,008셀(밴드 7개 × TP 0.85/0.90/0.95 ×
  delta 없음/0.03/0.05/0.08 × 손절 0.08–0.25 × 진입 마감 없음/60/120분).
- **수수료 두 번**: (a) 마켓에 저장된 schedule(NBA 2–3월 대부분 무료, 4–6월 sports_fees_v2 0.03), (b) 모든 경기에 **현재 schedule**
  (sports_fees_v3: rate 0.05, taker, 10월 NBA·NHL 마켓이 모두 이것)을 강제. **판정은 (b)로 한다.** 처음에는 (a)로만 판정해 plum-king NBA 를
  live 로 골랐다가 (b)에서 H1 −1.15% 로 실패해 paper 로 되돌렸다.
- 분할: 종목별 대상 경기 시작 시각의 중앙값(NBA 2026-03-28, NHL 2026-04-07, MLB 2026-06-27)으로 H1/H2. 모든 셀에 같은 분할점.
- **live 판정 규칙.** 1은 연구자 규칙이다. 2–4는 첫 grid 결과를 본 뒤 운으로 뜬 셀을 걸러내려고 **이 작업에서 추가한 기준**이다
  (사전에 정하지 못했다). 하나라도 어기면 paper:
  1. 연구자 규칙: n ≥ 40, 전체 ROI ≥ 0, H1 ≥ 0, H2 ≥ 0 (현재 수수료).
  2. 강건성: ±1칸 이웃 셀(결과가 같은 이웃 제외) 중 절반 이상이 1을 통과하고, 이웃과 합친 ROI ≥ 0 (수천 셀 중 운으로 뜬 셀 배제).
  3. 손절 지연 스트레스: 손절이 다음 1분 가격에 체결됐다고 다시 매긴 ROI(s1)가 두 반기 모두 ≥ 0 (1분 주기 손절 미끄러짐).
  4. 손실 1건 여유: 각 반기 손익 ≥ 1단위(5 USDC). 손절 없는 전략은 −100% 정산 1건이 결과를 뒤집으므로.
  5. 엔진 재생(`polylab backtest`, 현재 수수료를 넣은 DB 사본)으로 1을 다시 확인.
  6. apricot 은 TP ≤ 0.96 범위에서만 고른다(2026-10-02·10-04 조기 익절 원칙; 0.98–0.99 는 사실상 정산 보유).

## 3. 결과 요약 (현재 수수료 v3 기준, 괄호는 저장 수수료)

| 전략 × 종목 | 셀 | 규칙1 통과 | 규칙1–4 통과 | 결론 |
|---|---|---|---|---|
| watermelon NBA | 539 | 42 (60), 모두 조기 익절 없음 | prob_min 0.87–0.96 × stop 0.65–0.70 일대 | **live** (cat·dog) |
| watermelon NHL | 539 | 0 (6) | 0 | paper |
| apricot NBA | 2,016, TP≤0.96: 1,344 | TP≤0.96 83 (99) | 0 — 통과 셀은 모두 정산 손실 0건, 반기 손익 < 5 USDC | paper |
| apricot NHL | 2,016 (1,344) | 86 (95) | 0 — 같은 이유 | paper |
| apricot MLB | 1,728 (1,152) | TP≤0.96: **0** (0) | 0 | **live→paper** |
| plum NBA | 1,008 | 0 (34) | 0 | paper (king 은 저장 수수료로 통과했지만 v3 에서 실패) |
| plum NHL | 1,008 | 0 (4) | 0 | paper |

### watermelon NBA (선택 셀, 조기 익절 없음 = 손절 아니면 정산 보유)

| arm | 설정 | n | ROI (v3) | H1 / H2 | s1 H1 / H2 | 15분 최악 stress | MDD | 엔진(v3) n · ROI · H1 / H2 · 반기 손익 |
|---|---|---|---|---|---|---|---|---|
| cat | prob_min 0.87, stop 0.70 | 489 | +1.04% | +0.92 / +1.15 | +0.41 / +0.48 | −4.59% | 11.7 | 487 · +1.02% · +0.94 / +1.10 · 11.4 / 13.5 |
| dog | prob_min 0.96, stop 0.70 | 437 | +0.90% | +1.15 / +0.65 | +0.92 / +0.46 | −0.39% | 4.7 | 435 · +0.89% · +1.15 / +0.64 · 12.3 / 7.1 |
| (참고) cat 첫 후보 | 0.90 / 0.70 | 477 | +0.73% | +1.01 / +0.47 | +0.60 / **−0.13** | −4.31% | 9.4 | (저장 수수료 엔진 +1.18%) |
| (참고) soccer 현재값 그대로 | 0.93 / 0.65 / delta 0.02 | 457 | −2.14% | −1.85 / −2.42 | | | 50.1 | |

- **NBA 에서는 조기 익절이 손해다.** 규칙1 통과 셀은 v3 에서 모두 delta 없음이다(저장 수수료에서도 delta 셀 6개는 이웃이 무너짐).
  soccer 의 2026-10-02 결정(막판 급락 때문에 조기 익절)은 축구 근거이고, NBA 는 같은 grid 에서 정산 보유가 일관되게 낫다.
- 위험: 15분 최악 가격으로 손절을 다시 매기면 cat −4.6% 로 뒤집힌다. 1분 지연(s1)은 양수다. NBA 점수는 연속적으로 움직여 축구 골처럼
  한 분에 0.9→0.2 로 떨어지는 일이 적다는 가정이 깔려 있다. 실거래 손절 체결가를 첫 20건에서 반드시 확인할 것.
- A/B 축은 진입 하한 하나(cat 0.87 vs dog 0.96), 손절 0.70 공통. NBA 전용 한도: 동시 10건·보유 50 USDC·일일 손실 15 USDC.

### plum (모든 종목 paper)

- 저장 수수료로는 NBA 밴드 0.70–0.73, TP 0.90, SL 0.12, 60분 마감이 통과했다(+2.22%, H1 +1.47 / H2 +3.00, 엔진 +2.20%).
  현재 수수료(0.05)로는 +0.32%, **H1 −1.15%** 로 실패한다. 0.72 근처 매수·0.6–0.9 매도는 한 번에 약 1–1.4% 수수료라 이 전략의 이득을 거의 지운다.
  v3 에서는 NBA·NHL 모두 규칙1 통과 셀이 0개다. delta 조기 익절은 여기서도 손해다(10-04 문서와 같은 결론).
- paper 설정: NBA 밴드 0.70–0.73, TP 0.90, 60분 마감(king SL 0.12 / queen 0.17), NHL TP 0.85, 120분 마감.

### apricot (모든 종목 paper)

- **NBA·NHL**: TP ≤ 0.96 에서 규칙1 통과 셀(v3 NBA 83, NHL 86)은 tick 방향으로 통과·실패가 번갈아 나온다(NBA prob_min 0.94: tick 40·50 통과,
  60 실패, 70–90 통과, 100+ 실패). 통과 셀은 모두 정산 손실이 0건이고 실패 셀은 1–2건이다. 거래당 이득이 +0.5–1.2% 라서 −100% 정산 1건
  (=5 USDC)이면 반기 손익이 음수가 된다(예: NBA tick 50 반기 손익 약 1.1 / 1.4 USDC). 즉 "운 좋게 손실이 없었던 셀"이다 → 규칙4로 paper.
  paper 설정은 그 영역 그대로(prob_min 0.94, prob_max 0.99, TP 0.96, eco tick NBA 50·NHL 140, fruit tick NBA 80·NHL 100, 벽시계).
- **MLB**: 조기 익절 범위(TP ≤ 0.96) 1,152셀 중 **두 반기 모두 0 이상인 셀이 하나도 없다**(저장·현재 수수료 모두). 현재값 eco(tick 90)
  −1.02%(H1 −1.89 / H2 −0.10), fruit(tick 95) −0.46%(−0.68 / −0.23). 양수 셀은 TP 0.98–0.99 뿐이고(저장 수수료 최고 tick 95 TP 0.99 +0.95%,
  하지만 H2 −0.59%) 이웃 강건성도 없다(최대 2/8). → **MLB 실거래를 paper 로 내린다.**
- 해석: apricot 의 "후반 선두 과소평가"는 현재 수수료·스프레드에서 확인되지 않는다. 선두 가격이 시간이 갈수록 1틱 오르는(theta) 성질로
  작은 이익을 자주 내지만, 손절이 없어 드문 역전 1건이 그 이익을 지운다. 손절을 추가하거나 진입 가격을 더 높이는 방향은 다음 연구 과제로 남긴다.

## 4. 적용 (2026-10-05, 모두 5 USDC)

| 변형 (계좌) | soccer | mlb | nba | nhl | nfl |
|---|---|---|---|---|---|
| watermelon-cat (cat) | **live** (값 유지) | – | **live** 0.87 / stop 0.70 / 익절 없음 | paper 0.87 / 0.75 | – |
| watermelon-dog (dog) | **live** (값 유지) | – | **live** 0.96 / stop 0.70 / 익절 없음 | paper 0.96 / 0.75 | – |
| apricot-eco (eco) | – | paper (←live) | paper tick 50 | paper tick 140 | – |
| apricot-fruit (fruit) | – | paper (←live) | paper tick 80 | paper tick 100 | – |
| plum-king (king) | paper | – | paper SL 0.12 | paper | paper |
| plum-queen (queen) | paper | – | paper SL 0.17 | paper | paper |
| watermelon-us-paper · plum-us-paper | – | – | (cat·dog·king·queen 로 이동) | (이동) | paper |

- 연구자 규칙(규칙1)만 적용했다면 live 였을 조합: 현재 수수료 기준 apricot NBA·NHL(eco·fruit, TP ≤ 0.96 통과 셀 있음).
  저장 수수료 기준으로는 추가로 watermelon NHL, plum-king·queen NBA. 이들은 규칙 2–4(또는 현재 수수료)로 paper 에 두었다 — 연구자가 다시 결정할 수 있다.
- 계좌는 변형마다 하나(cat/dog, eco/fruit, king/queen)를 종목 공통으로 쓴다. apricot 은 마스터 `mode: live` 를 유지해 계좌와
  남은 live MLB 포지션의 대사·정산·청산을 이어 가고, 새 진입은 모든 종목 paper 다. plum-king·queen 은 마스터 paper 그대로다.
- NFL 은 어디에서도 live 가 아니다(2026-10-03 결정 유지). `cherry-us-paper` 는 바꾸지 않았다(다른 작업 범위).
- 각 NBA·NHL override 에 `bounds`(sport_overrides.<종목>.*)를 열었다. autopilot 은 이 안에서 종목별로 조정할 수 있고, 조기 익절을
  null→값으로 켤 수는 있지만 끌 수는 없으며, 어떤 종목도 live 로 올리지 못한다.
- 단위는 종목마다 5 USDC 에서 시작해 그 종목의 거래만으로 ladder(5→10→25→50→100)를 판정한다.

## 5. 한계 (결론을 바꿀 수 있는 것)

1. **NBA·NHL 이력은 전부 합성 호가다**(저장 호가는 2026-09-30 이후). 무한 깊이, 1분 가격 ±0.005. 실제 손절·익절 체결은 더 나쁠 수 있다.
   s1(1분 지연)과 15분 최악 stress 를 같이 봤다. watermelon NBA 는 15분 최악 기준으로는 음수다.
2. 표본 기간이 한 시즌 후반(2–6월, 플레이오프 포함)뿐이다. H2 는 대부분 플레이오프다. **NBA 프리시즌(10월 초)은 표본에 3경기뿐**인데,
   거래량 하한(2만 달러)을 넘으므로 이 설정이 배포되면 프리시즌 경기부터 live 로 진입한다. 정규 시즌은 10월 하순 시작.
3. 다중 비교: 셀 수천 개. 이웃 강건성·스트레스·손실 여유로 걸렀지만 독립 out-of-sample 은 아니고, 걸러내는 기준(규칙 2–4)도 결과를 본 뒤 정했다.
4. 거래량 하한은 최종 거래량(look-ahead)이다.
5. 경기 시계가 없어 벽시계를 썼다. 지연 시작·연장전·하프타임 길이 차이가 tick 의미를 흐린다(apricot tick, plum 마감).

## 재현

```
# Mac mini, 운영 DB 의 VACUUM INTO 사본에서 (빠른 평가기는 scratch 전용)
POLYLAB_ROOT=<scratch>/root uv run polylab backtest --variant x --variant-file <nba 만 남긴 yaml> --from 2026-02-01 --to 2026-10-05
# 현재 수수료 재생: 사본의 NBA markets.fee_schedule 을 sports_fees_v3 로 바꾼 뒤 같은 명령
```

grid 전체(약 1만 셀)는 8 프로세스로 약 2분, 엔진 재생은 변형당 약 15분 걸렸다.
