# L2 AGENTS.md — golden-burger 모노레포

이 문서는 `t1`(golden-burger) 모노레포 루트에 적용되는 운영 지침이다.

- 상위 계층: L1 `/Users/izowooi/git/AGENTS.md`(워크스페이스 전역 규칙).
- 하위 직속 프로젝트에 `AGENTS.md`(L3)가 있으면 그 지침을 우선한다.
- 전역 개발 철학·보안·Git·응답·문서화 규칙은 L1을 따르며 여기서 반복하지 않는다. 본 문서는 이 저장소 고유의 인덱싱과 공통 운영에 집중한다.

## 저장소 목적

Polymarket 예측시장 자동매매 전략 봇과, 그 수익을 적재·리포팅·시각화하는 도구, 그리고 별도 주식 신호/대시보드 도구를 한 git 저장소에 모은 폴리글랏 운영 워크스페이스다. remote: `github.com/izowooi/golden-burger.git`.

## 구조 (직속 프로젝트 인덱스)

전략 봇 — Polymarket 자동매매 (Python/uv, `main.py`+`config.yaml`+`src/polybot/`):

- `golden-apple/`: 확률 80% 매수 / 90% 매도 전략. 2개 인스턴스로 운영 → 대시보드의 `GOLDEN-APPLE (1)`·`(2)`. 인스턴스 2의 상수는 저장소가 아니라 Jenkins env override에 있다.
- `golden-banana/`: 모멘텀(85~97% + 골든크로스) 전략.
- `golden-cherry/`: Resolution Momentum(75~92%, `entry_hours_max` 120h) 전략. **자금은 golden-banana 계정에 있고 Jenkins job 이름은 `polybot-yellow`다** — 폴더명·계정명·job명이 모두 다르다. → L3 `AGENTS.md` 참조.

→ 이 3개는 L3 `AGENTS.md`가 없는 상태로 오래 운영됐다. `golden-apple`·`golden-banana`는 여전히 미보유하며 `tools/verify_strategy_contracts.py`의 `PRE_L3_STRATEGIES`가 예외로 처리한다. `golden-*` 프로젝트는 28개며, 26개는 수익 가설을 검정하고 `golden-pomegranate`·`golden-coconut`은 각각 전 시장·major sports 전용 accountless observatory다. 수익 가설 중 `golden-black`·`golden-raspberry`·`golden-strawberry`·`golden-watermelon`은 주문 없이 displayed-book 반사실만 검정한다.

→ 계정 slot은 `daily-report`가 `ACCOUNT_<n>_NAME`/`ACCOUNT_<n>_ADDRESS` 쌍을 번호순으로 훑어 동적으로 발견한다 (`daily-report/src/polybot_reporter/account_config.py`). 코드에 상한은 없고, 현재 `Jenkinsfile`·`.env.example`이 **13 slot**을 선언한다. `slack-data-collector/src/slack_data_collector/portfolio.py`의 11행 seed 중 명시적인 과거 전략 매핑은 `golden-eco=honeydew`, `golden-fox=nectarine`뿐이다. 다른 계정 ID의 실제 전략 배치는 effective-dated Supabase 실데이터로 확인하며 계정명으로 추정하지 않는다.

신규 전략 봇 — 대중 심리 기반, 단계적 A/B 검증 예정 (각 폴더 L3 `AGENTS.md`·`STRATEGY.md` 보유, 개요는 `docs/prediction-market-strategy-portfolio.md`):

- `golden-black/`: **Sports Resolution Hold** — sports + Gamma `endDate` `(0h,6h]` + market
  liquidity 10k/cumulative volume 5k를 server-side keyset으로 먼저 좁힌 뒤, exact `$5` ask가
  `[0.92,0.93]` 또는 `[0.94,0.95]`인 outcome을 unique one-hot resolution까지 보유하는
  paired displayed-book 반사실이다. 각 episode에서 무손절과 `0.80/0.70/0.60` stop을 동시에
  재생하며 stop trigger와 actual full-depth VWAP, gap, partial fill과 잔여 retry를 분리해
  저장한다. 5분 cadence, accountless simulation-only이며 credential과 `--live`를 source-level로
  금지한다. 30일 prospective cohort 전 수익성이나 exit policy를 판정하지 않는다.
- `golden-coconut/`: **Five-Family Major Sports Observatory** — soccer·MLB·NBA·NFL·NHL의
  exact major-league whole-game moneyline을 경기 전부터 terminal lifecycle까지 5분마다 추적한다.
  `closed=false`, `slot-24h..slot+48h` 범위에서 soccer는 frozen 8개 대회 query tag로 fan-out하고
  미국 4종목은 각 family tag를 terminal cursor까지 읽은 뒤 event ID로 follow-up한다.
  `0.75..0.99` crossing, full CLOB book, `$5..$1000` depth와
  liquidity·volume·season phase를 append-only UTC daily shard에 보존한다. liquidity/volume은
  discovery gate가 아니라 strata이며, official preseason은 별도 `PRESEASON` cell로 둔다.
  마지막 v7은 `polybot-gold`의 external APFS workspace에서 실행한 accountless
  research-only collector였다. credential·order·`--live`는 source-level로 금지한다.
  **2026-09-01T12:15:14.674Z에 수집을 종료**했고 현재 scheduled Jenkins job은 없다.
  `polybot-gold`는 이후 Golden Plum MLB simulation으로 재사용하므로 Coconut DB·로그는
  immutable historical epoch로만 보존하고 Plum과 합치지 않는다.
- `golden-tangerine/`: **Sports Resolution Hold Live A/B** — Golden Black과 같은 sports 6h
  universe의 aligned two-outcome(팀명 moneyline과 Yes/No proposition)을 두 기존 wallet에서
  최소 `$5`로 prospective 검증한다. `polybot-orange`는 exact
  ask VWAP `[0.94,0.95]`, `polybot-fox`는 `[0.92,0.93]`; FOK BUY 뒤 resolution까지 보유하며
  총/event/cycle open 한도는 `3/1/1`이다. 봇 DB가 만든 trade만 관리하고 수동 wallet
  position은 편입·청산하지 않는다.
- `golden-date/`: Conviction Ladder — 시간 사다리 + 모멘텀 게이트. **⛔ 2026-07-29 폐쇄 완료** (edge -1.56pp, 회전율 14.8배가 손실을 증폭. `docs/retro/golden-date-2026-07-verdict.md`).
- `golden-elderberry/`: Panic Fade — favorite 급락 과잉반응 역매수.
- `golden-fig/`: Hope Crusher — 롱샷 페이드. **⛔ 2026-07-28 폐쇄 완료** (캘리브레이션 전 구간 edge 음수, `docs/retro/golden-mango-fig-2026-07-verdict.md`).
- `golden-grape/`: Cascade Rider — 완만한 일관 드리프트 + 거래량 가속 편승.
- `golden-honeydew/`: Night Watch — 미국 새벽·주말 무근거 이탈 복원. **⛔ 2026-07-30 폐쇄 완료** (strict confirmed-fill gross -3.54%, 주요 slice 전부 음수. `docs/retro/golden-honeydew-2026-07-verdict.md`).
- `golden-lime/`: Shock Follow — 거래량 동반 급등 편승. **⛔ 2026-07-28 폐쇄 완료** (백테스트로 가설 기각, `docs/retro/golden-lime-2026-07-backtest-verdict.md`). 같은 검정에서 elderberry 방향도 지지되지 않아 재확인이 필요하다.
- `golden-mango/`: Patience Premium — 연환산 캐리 허들. **⛔ 2026-07-28 폐쇄 완료** (허들이 시간가치가 아니라 손실확률을 탐지, 후보 정렬 부재).
- `golden-nectarine/`: Bottom Fisher — 20일 롤링 최저가 매수 / 5일 보유의 시간별 근사. **⛔ 2026-07-30 폐쇄 완료** (대사된 120h calendar-exit subset -4.70%; 정정된 24~240h 반사실 구간은 모두 0 포함. `docs/retro/golden-nectarine-2026-07-verdict.md`).
- `golden-orange/`: Fear Spike Fade — tail 시장 공포 급등 후 NO 매수 (probability neglect).
- `golden-papaya/`: Final Five — 표준 이진 YES의 first observed 0.95 상향 교차를 0.95–0.97에서 매수하고 해결까지 보유.
- `golden-peach/`: **Kickoff Leader** — 축구 경기 시작이 source clock으로 확인된 0~10분에
  HOME/DRAW/AWAY 세 명제의 직접 YES·NO 6개 full-depth book을 같은 시각에 비교하고 유일한
  선두 하나를 exact `$5` FOK로 event당 한 번만 매수한다. `polybot-eco`는 TP `+0.03`,
  `polybot-fruit`는 TP `+0.05`, 공통 SL은 entry `-0.10`이다. source 80분부터는 절반 TP를
  허용하되 신규 stop은 금지하고 resolution을 기다린다. `polybot-grey`는 같은 1분 모집단의
  credential-free simulation/raw six-book 수집기다. SELL 실패는 event-local이며 180분 뒤
  성공 체결로 꾸미지 않고 경제적 open 상태의 `QUARANTINED`로 격리한다.
- `golden-plum/`: **Sport-Profiled Full-Game Confirmation** — 경기 시작부터 종료까지 직접 결과
  호가 중 같은 token이 3회의 1분 관측에서 누적 +2%p로 상승하고 `[0.75,0.78]`을 처음
  통과하는지 종목별로 검정한다. 축구는 HOME/DRAW/AWAY YES·NO 6token이며 King TP 0.90,
  Queen 0.95의 exact `$5` live A/B다. 시간 강제 청산 없이 TP·SL·검증된 resolution만
  사용한다. `polybot-silver`는 축구, `polybot-gold`는 MLB direct two-team moneyline의
  credential-free 1분 raw path와 `$5~$500` displayed-depth 증액 자료를 수집한다.
  NBA·NFL·NHL은 code-ready이고 아직 배포하지 않는다. 과거 재생은 탐색 근거일 뿐 앞으로 수집하는 A/B가
  최소 표본 gate를 통과하기 전에는 수익성·증액을 판단하지 않는다.
- `golden-queen/`: Crown Momentum — 표준 이진 YES의 첫 0.90 상향 교차를 0.90–0.94에서 매수하고 0.98 목표/0.85 stop으로 관리. 스포츠 기본 포함.

- `golden-quince/`: **Spread Harvest** — 방향성 예측을 포기하고 **실행 측면(maker/taker)**
  하나를 수익원으로 삼는다. `execution_mode`(passive/nearest/cross)가 처치축이며 실측 왕복
  비용이 maker→maker -31.1bps / taker→taker +72.5bps로 **103bps** 갈린다. 진입 신호는
  queen에서 그대로 상속(신호가 아니라 실행이 처치이므로). $5 시작, 낙폭 kill switch를
  코드로 강제. A/B 3팔 사전 등록. 근거: `docs/retro/2026-07-29-market-structure-study.md`,
  `docs/retro/2026-07-29-execution-cost-floor.md`.
- `golden-melon/`: **Resolution Sprint** — `golden-cherry` 재설계. 해결까지 `(0h, 72h]`인
  표준 이진 YES가 처음으로 `[0.85, 0.93]`에 상향 교차하고 24h 거래량 gate를 통과하면
  매수, `0.97` 목표 / `0.78` 절대 손절. **trailing stop과 time exit 없음.**
  A/B/C 처치축은 `min_volume_24h`(20k/50k/150k) 하나다. 배리어 산술상 손익분기 승률
  58.0% = martingale 57.9%이므로 edge는 진입 선별에서만 온다. 근거: cherry 671건에서
  `stop_loss` 52건이 설정 −8%인데 **−24.78%** 로 실현됐고 그 **60%가 매수 후 30분 이내**
  (최악 −99.3%)였다. 꼬리는 파라미터가 아니라 $5 금액과 포지션 20 상한으로 막는다.
- `golden-kiwi/`: **Micro-Cascade** — 3/5회의 5분 연속 소폭 상승과 누적 +1/+2pp를
  2×2 네 팔로 수집하는 추세 연구 봇. 사전 등록한 독립 시간구간 검정에서 모든 팔이
  승격 gate를 실패했으므로 **simulation/research 전용이며 live 실행은 코드에서 차단**한다.
  과거 C의 유일한 양수 신호는 cross-commit lineage라 해석이 철회됐다. primary B를
  관측 결과로 교체하거나 threshold를 완화하지 않는다.
- `golden-blueberry/`: **Closing Surge** — 해결/경기 종료가 가까운 strict binary YES의
  첫 0.85 상향 교차를 `[0.85,0.93]`에서 추종한다. `(0h,72h]`, 0.97 target/0.78 stop,
  스포츠·in-play 기본 포함. $5 hard cap과 arm당 $150로 시작하며, 유일한 A/B 축은 직전
  15분 이내 snapshot 대비 최소 급등 `+2%p/+5%p`다. Git commit은 provenance로만 남기고
  `config_hash × strategy_source_digest × mode × job_name`을 cohort로 사용한다.
- `golden-pomegranate/`: **Accountless Market Observatory** — 주문·계좌·P&L이 없는
  research-only collector. Gamma keyset full non-closed market/outcome census, public Data API trade
  tape와 deterministic rotating public CLOB book sample을 `research-full-v1` UTC daily SQLite
  shard에 append-only로 보존한다. `--live`와 credential 주입을 source-level로 차단하며
  lifecycle은 `archive_only`로 고정한다. 초기 cadence는 15분이고 외장 APFS volume의
  여유 공간·mount identity·single writer를 확인한다.
- `golden-raspberry/`: **Queue Echo** — 동시 YES/NO displayed-depth imbalance가 3회의
  5분 snapshot에 지속될 때 60분 뒤 `$5` ask-to-bid 반사실 수익을 예측하는지 검정한다.
  `polybot-do/re/mi`는 arm이 아니라 condition hash 3-shard이며 각 shard가 DO(1회),
  RE(2회), MI(3회)를 모두 계산한다. MI만 primary이고 주문·credential·`--live`는
  source-level로 금지한다. 현재 confirmatory epoch는 `queue-echo-v3`의 세 독립 DB이며,
  invocation별 5분 slot을 public HTTP 전에 원자적으로 claim하고 225초 cooperative budget으로
  timeout·retry·`Retry-After`를 제한한다. external-v2 DB는 무효 운영 evidence로 보존하되
  v3와 migration·merge하지 않는다.
- `golden-strawberry/`: **Last Mile** — CLOB `/sampling-markets` 전체 cursor population에서
  outcome token의 최초 `0.95` 상향 교차를 관측하고 `$5` displayed ask 진입 뒤 `0.85` bid
  stop 또는 terminal `0/1` payout까지 반사실 경로를 기록한다. 스포츠·비(非)스포츠와
  `0.90/0.92/0.95/0.97` entry, `none/0.80/0.85/0.90` stop, `none/0.98/0.99` target을 같은
  frozen cohort에서 측정하되 1주 차에는 collection health만 판정한다. 주문·credential·
  `--live`는 source-level로 금지한다. 7일 entry 수집을 마친 `last-mile-clob-v1`은
  immutable source로 동결하고, `strawberry-shadow-one-followup-v2a`가 unresolved episode만
  10분마다 추적한다. v2a는 매 cycle v1 stat/source anchor와 imported row/count/hash를
  재검증하고 token/cycle당 canonical gzip full-book 한 행을 공유하는
  `last-mile-clob-followup-v2a` 계약이다. cycle evidence와 `SUCCEEDED`는 한 transaction으로
  게시하며 v1 census·신규 crossing을 재실행하지 않는다.
- `golden-watermelon/`: **Elite Soccer In-Play Match Winner** — 경기 시작 후 strict whole-match
  `moneyline`의 exact `$5` ask VWAP가 `0.95/0.96/0.97/0.98/0.99`를 통과한 시점을
  수집하고 resolution hold와 `0.95/0.93/0.90/0.85/0.80/0.70` stop을 동시
  재생한다. 현재 v3d universe는 EPL·Bundesliga·Ligue 1·LaLiga·MLS·Serie A와 exact
  UCL/UEL competition identity의 regular-time home/draw/away moneyline을 허용한다. e-sports,
  허용되지 않은 cup/league, advancement·prop·child market은 CLOB 전에 제외한다. public
  Sports WebSocket을 우선하고 same-cycle Gamma explicit clock을 fallback으로 보존해
  75/80/85분 timing strata를 만든다. accepted event의 distinct HOME/DRAW/AWAY triad를
  fail-closed로 검사하고 full book으로 `$5`~`$1000` notional ladder를 replay한다. `polybot-white` 1분과
  `polybot-grey` 5분은 같은 population/grid의 paired cadence 처치이며, 두 DB를 독립 거래로
  세지 않는다. `soccer-inplay-elite-competition-match-winner-v4` append-only evidence를 쓰고
  accountless simulation-only이며 credential·order·`--live`를 source-level로 금지한다.
- `golden-watermelon-live/`: **In-Play Match Result Live A/B** — Soccer/MLB/NHL whole-game
  winner를 family별 `0.96` 대 `0.99` arm으로 exact `$5` 검정한다. Soccer는 Cat/Dog, MLB는
  Bear/Tiger, NHL은 Lion/Wolf이고 모두 1분 cadence다. effective stop은
  `max(0.70, confirmed BUY VWAP-0.05)`이며 event당 1개·account당 20개로 제한한다. 모든 후보를
  POST 전 proven-no-POST queue에 남겨 앞 후보 오류가 뒤 후보를 영구 누락시키지 않는다. open
  trade뿐 아니라 미추적 BUY intent도 capacity에 예약하고, unresolved pending/SELL evidence가
  있으면 신규 BUY를 차단한다. 수동 wallet position은 편입·청산하지 않고 stop trigger를 보장
  체결가로 해석하지 않는다.
전략 문서 HTML 버전은 `docs/strategy-pages/`, A/B 회고 절차는 `docs/ab-retro-playbook.md` 참조, 월간 파라미터 회고(전 봇)는 `docs/retro/README.md` 참조.
quince A/B/C 실험을 실제로 기동할 때는 `docs/golden-quince-abc-runbook.md`(자립형 런북 — 팔 구성·금액·예산·기간·day-1 kill-check·무효화 조건)를 단독으로 따른다.

공통 관측성·리포팅·적재 (Python/uv):

- `polybot-observability/`: 21개 거래 전략의 resolved config/Git/run provenance, CLOB order/fill 대사, 회고 readiness audit와 SQLite online backup. `golden-black`·`golden-coconut`·`golden-pomegranate`·`golden-raspberry`·`golden-strawberry`·`golden-watermelon`은 공통 secret-free config contract만 재사용하고, run provenance는 자체 append-only audit로 기록한다.
- `daily-report/`: 선언된 전 계정(현재 13 slot) 잔고를 Slack 보고 + Supabase `pb_*` 적재 (`Jenkinsfile` 보유).
- `daily-rsync/`: Jenkins job별 SQLite·bot log·console log를 local-only로 증분 pull하고, catalog·plan·manifest로 provenance와 무결성을 보존하는 Python/uv 도구.
- `slack-data-collector/`: Slack 리포트 이력 수집·정규화·DB 적재.

시각화·도구:

- `polymarket-dashboard/`: 전 계정 잔고/수익률 비교 대시보드 (Next.js/Cloudflare). → L3 `AGENTS.md` 참조.
- `streamlit_proj/`: "Golden Burger" 주식 차트 대시보드 (Streamlit).
- `cloud_run_proj/`: 나스닥·한국 ETF 이평선 신호 알리미.
- `legacy/`: 이평 추세매매 + 이메일·텔레그램 알림 (구버전, `requirements.txt`).
- `tools/`: 저장소 공통 스크립트. `verify_strategy_contracts.py`(28개 `golden-*` 프로젝트의 거래/research-only 계약 검증), `wind_down.py`(전략 전환 시 잔여 주문 취소·포지션 정리 CLI, 절차는 `docs/strategy-wind-down-playbook.md`), `reconcile_positions.py`(봇 DB 오픈 포지션을 지갑 실보유와 대조·정리. 공개 API만 쓰므로 private key 불필요), `lime_jump_backtest.py`(`market_snapshots`로 점프 이벤트의 사후 수익률을 측정), `lime_barrier_sim.py`(TP/SL 구조를 실제 가격 경로로 재생, 다중검정 보정 포함), `market_calibration.py`(가격 구간별 실제 해결률 측정 — 확률 기반 전략의 전제를 직접 검정), `sell_retry_audit.py`(매도 무한 재시도 루프를 DB로 진단), `jenkins_log_audit.py`(Jenkins 실행 로그를 봇별로 판정), `resolve_stuck_intents.py`(매도를 막는 CLOB intent 격리를 거래소 열린 주문과 대조해 증거 기반 해제). 배경은 `docs/sell-retry-loop-defense.md`, 최근 판정은 `docs/retro/2026-07-28-fleet-log-verdict.md`.
- `docs/`: 문서 자산. 위에 인덱싱되지 않은 것으로 `sqlite-storage-maintenance.md`, `strategy-wind-down-playbook.md`, `nectarine-max-positions-retro.md`, `sell-retry-loop-defense.md`가 있다.

## 데이터 흐름

봇(Jenkins 실행) → 각 SQLite에 전략 판단 + resolved config/Git/run + order/fill lifecycle 기록 → `daily-report`가 계정 완전성 검증 후 secret-free local evidence, Slack, Supabase(`pb_*`)에 일일 snapshot 적재 → `polymarket-dashboard`가 공통 날짜 **구간** 기준 수익률·freshness·누락·합계 대사를 표시한다.

**공유 저장소는 없다.** 22개 거래 전략 모두 자기 폴더의 `data/<job>/trades.db` 또는
simulation 전용 `trades_sim.db`만 읽고 쓴다. 폐쇄된 `golden-honeydew`·
`golden-nectarine` DB는 넓은 universe snapshot 자산으로 보존한다. `golden-papaya`·
`golden-queen`·`golden-quince`·`golden-kiwi`·`golden-blueberry`·`golden-tangerine`·`golden-watermelon-live`·`golden-peach`·`golden-plum`은 각 전략의 request envelope와
lineage가 달라 자체 archive/catalog를 주 source로 사용한다. "중앙 archive"는 분석자가
폐쇄 DB를 찾아 교집합 대조에 사용하는 **분석 관행**이지 런타임 의존이 아니다.

`golden-pomegranate`는 예외적으로 거래 DB가 아닌 `data/<job>/trades_sim.db`와
`trades_sim_YYYYMMDD.db` 일별 shard에 `research-full-v1` 관측 증거를 저장한다.
`compact-v1`을 적용하지 않고 row를 지우지 않으며, external APFS workspace와
whole-shard backup/retention 계약을 사용한다. 이 DB를 trade/fill/P&L evidence로 해석하지 않는다.

`golden-coconut`의 마지막 v7 epoch도 거래 DB가 아닌
`data/coconut-major-sports-lifecycle-5m-v7/trades_sim.db`와
`trades_sim_YYYYMMDD.db` UTC daily shard를 사용했다. 당시 `polybot-gold`의 external APFS workspace,
exact workspace marker와 150 GiB/70%/80% storage guard를 확인한 뒤 soccer의 frozen 8개 대회
query-tag fan-out과 MLB·NBA·NFL·NHL family sweep을 서로 격리된 다섯 worker에서 동시에 시작한다.
그 뒤 semantic root-or-season identity census, event-by-ID lifecycle, full book,
crossing/path/resolution을 저장한다. liquidity·volume과 official
preseason은 selection이 아니라 독립 strata다. HTTP body는 15초 total-attempt 경계를 적용해
socket read가 이어지는 slow stream도 bounded retry/fail-closed하며, 이 DB를 actual fill/P&L
evidence로 해석하지 않는다. v1–v6 DB와 마지막 v7 DB를 서로 합치지 않으며, 어느 Coconut
DB도 현재 `polybot-gold`의 Golden Plum cohort와 합치지 않는다.

`golden-raspberry`도 `data/<runtime-job>/trades_sim.db`를 사용하지만 일별 shard가 아니라 세 개의
고정 hash-shard DB다. 현재 `raspberry-do/re/mi-v3-shard-*`의 `queue-echo-v3` raw Gamma/CLOB
evidence를 함께 검증한다. v3 이전 DB는 별도 historical epoch이며 섞지 않고, 표시 호가
반사실을 actual fill이나 realized P&L로 해석하지 않는다.

`golden-strawberry`의 동결 v1은 `data/strawberry-shadow-one/trades_sim.db`에 10분 CLOB
sampling census와 crossing-time book/Gamma evidence를 보존한다. entry 종료 뒤에는 이 파일을
read-only seed source로만 열고 active
`data/strawberry-shadow-one-followup-v2a/trades_sim.db`가 unresolved episode의 compact
book/path/resolution만 적재한다. 실패한 v2 attempt는 별도 historical provenance다. 두 active
epoch를 merge하거나 하나의 DB로 덮지 않으며, 둘 다 actual fill 또는 realized P&L로 해석하지
않는다.

`golden-black`은 단일 `data/black-shadow-paired/trades_sim.db`에 server-filtered sports event
keyset, exact `$5` books, `0.92/0.94` paired episode, 무손절·0.80·0.70·0.60 stop path와 one-hot
resolution을 append-only로 저장한다. stop 기준가는 체결가로 간주하지 않고 displayed bid
VWAP·부분 fill·retry를 따로 보존한다. actual fill/P&L evidence가 아니며, Gamma `endDate`를 실제
경기 종료 시각으로 가정하지 않는다. 24시간/7일 review는 collection health와 coverage만 판정한다.

`golden-watermelon`은 `polybot-white/watermelon-white-1m-v3d` 1분과
`polybot-grey/watermelon-grey-5m-v3d` 5분의 독립
`data/<runtime-job>/trades_sim.db`에 같은 in-play whole-match moneyline population과 X/Y
grid를 적재한다. 같은 `condition_id × token_id × entry_threshold`는 paired unit이며
두 거래로 세지 않는다. exact `$5` ask/bid depth, crossing provenance, stop gap·partial·
retry와 unique one-hot resolution, WSS/Gamma explicit source clock, exact result triad,
`$5`~`$1000` full-book capacity를
append-only로 보존한다. actual fill/P&L evidence가 아니며, 첫 health review에서는
cadence·cursor·classification·book·clock·DB·storage만 판정한다.

`golden-watermelon-live`은 `polybot-cat/watermelon-live-cat-96-1m-v2h`과
`polybot-dog/watermelon-live-dog-99-1m-v2h`의 독립 `trades.db`를 사용한다. exact `$5` FOK
BUY와 full-holding FOK stop SELL은 order/fill/fee ledger로만 확정하며, 과거 Papaya DB나
White/Grey simulation DB 또는 초기 5분/v2a zero-opportunity live DB와 merge하지 않는다.

`golden-peach`는 `polybot-eco/peach-live-eco-3pp-1m-v1`,
`polybot-fruit/peach-live-fruit-5pp-1m-v1`, `polybot-grey/peach-shadow-1m-v1`의 독립 DB를
사용한다. 세 job 모두 external T7 workspace의 1분 cadence다. live 두 arm은 TP만 다르고,
Grey는 직접 YES·NO 6개 raw book과 source clock을 저장한다. 과거 Watermelon의 YES-only
archive에서 만든 합성 NO 재생은 탐색 자료일 뿐 current direct-book cohort와 합치지 않는다.

`golden-plum`은 `polybot-king/plum-live-king-90-1m-v1`,
`polybot-queen/plum-live-queen-95-1m-v1`, `polybot-silver/plum-shadow-silver-1m-v1`,
`polybot-gold/plum-shadow-gold-mlb-1m-v1`의 독립 DB를 사용한다. King/Queen은 축구 절대 TP만
다르고 Silver는 축구, Gold는 MLB credential-free simulation이다. Gold의 과거 Golden
Coconut epoch와 새 Golden Plum epoch는 Jenkins 이름이 같아도 절대 합치지 않는다.
Golden Peach Grey의 직접 six-book 재생은 탐색 자료일 뿐 Golden Plum의 앞으로 수집하는
`config_hash × strategy_source_digest × mode × job_name` cohort와 합치지 않는다.

매도 거절은 trade 상태를 바꾸지 않으므로 `HOLDING`으로 남아 매 사이클 반복 제출된다. 이 루프가 `max_positions`를 잠식해 봇을 정지시킨 사례가 있다(cherry 2026-07-22~28). 전 전략에 거절 사유 분류 로그(`매도 실패 진단`)와 축소 재시도 방어가 들어 있다 — 상세는 `docs/sell-retry-loop-defense.md`.

GTC 주문의 `live`/`accepted` 응답은 체결이 아니다. 실현 성과는 `order_fills.status='CONFIRMED'`의 실제 size/price와 fee coverage로만 확정한다. `trades.realized_pnl`은 **요청 가격 × 요청 수량**으로 계산되므로 성과 지표로 쓰면 안 된다 — 매도 GTC가 `orderID`만 받아도 `COMPLETED`로 기록된다. 체결된 적 없는 매수는 `TradeStatus.UNFILLED`(유령 포지션), CLOB 카탈로그에서 사라진 주문은 `QUARANTINED`로 종결되며 **둘 다 오픈 노출로 집계**되어 `max_positions`를 소모한다. 계측 배포 전 legacy 구간과 배포 후 구간은 분리하고, evidence gap을 추정값으로 채우지 않는다. 상세 계약은 `docs/retro/EVIDENCE_CONTRACT.md`를 따른다.

## 공통 작업 원칙

- 각 하위 폴더는 독립 프로젝트로 취급한다. 한 폴더 작업이 다른 폴더에 영향을 주지 않게 한다.
- Python 프로젝트는 **uv** 표준을 따른다: `uv sync --frozen` 후 `uv run ...`. (`legacy`만 `requirements.txt` 예외.)
- Node 프로젝트(`polymarket-dashboard`)는 npm을 쓴다.
- 공통 유틸은 2개 이상 실제 사용 사례가 생긴 뒤 고려하고, 먼저 폴더 내부에서 단순 해결한다.
- 실거래 cycle은 관측성 기록 실패 시 fail closed한다. 전략 판단을 바꾸기 전에 `config_hash × git_commit × mode × job_name` cohort와 fill/archive coverage를 확인한다. 단, Golden Black·Coconut·Kiwi·Blueberry·Raspberry·Strawberry·Tangerine·Watermelon·Watermelon Live·Peach·Plum은 모노레포 commit을 cohort로 쓰지 않고 L3 계약의 `config_hash × strategy_source_digest × mode × job_name`을 사용한다. Golden Pomegranate도 Git commit을 provenance로만 두고 L3의 `config_hash × strategy_source_digest × mode × job_name × schema_profile`을 사용한다.

### Task summary 완료 checkpoint

상위 L1의 `작업 기록 보관` 규칙은 이 저장소의 모든 응답에 필수다. 독립된 사용자 요청마다
최종 응답 직전에 `task-summaries/YYYY/MM/YYYY-MM-DD_HHMMSS_<slug>.md`가 실제로 생성됐는지
확인한다. 파일에는 작업을 시작시킨 사용자 메시지 원문과 최종 결과 요약을 함께 적고,
credential·token·password·민감 개인정보는 `[REDACTED]`로 치환한다. summary 본문은
local-only이므로 staging·commit·push하지 않는다. 누락을 발견하면 다음 작업과 합치지 말고
누락된 요청의 summary를 먼저 별도 복구한다.

## 작업 전 확인

1. 워크스페이스 `REPOS.md`와 본 문서
2. 작업 대상 폴더의 `AGENTS.md`(있으면)
3. 대상 폴더의 `README.md`
4. 대상 폴더의 package/config 파일 (`pyproject.toml`, `package.json`, `config.yaml`)
5. 전략·회고 작업이면 `docs/retro/EVIDENCE_CONTRACT.md`; 새 전략이면 `docs/new-strategy-playbook.md`
6. Jenkins 현황·job↔strategy 매핑·`daily-rsync` routing 작업이면 local-only `docs/local/jenkins-job-strategy-inventory.md`(존재할 때)

## 회고 evidence 자동 발견

### Local Jenkins inventory routing

Jenkins의 현재 실행 현황, job↔strategy/runtime 매핑, 여러 전략의 `daily-rsync` 대상 선택을 묻는 경우 먼저 local-only `docs/local/jenkins-job-strategy-inventory.md`를 읽는다. 이 파일은 내부 topology snapshot이므로 `.gitignore` 상태를 유지하고 절대 commit하지 않는다. 파일이 없거나 대상 job이 없거나 config SHA-256이 달라졌으면 `inspect-jenkins-job`으로 실제 Jenkins config를 다시 읽어 local inventory를 갱신한다.

inventory는 routing 후보이지 sync 성공이나 historical epoch의 권위가 아니다. 실제 동기화 요청에서는 선택한 각 `Jenkins job × strategy`마다 `daily-rsync scan --job <job>`으로 current strategy evidence를 확인한 뒤 별도 plan을 만든다. 하나의 strategy에 live/simulation/shadow/close-only 또는 복수 Jenkins job이 있으면 조용히 하나를 고르지 않고 요청 범위에 맞는 모든 row와 mode를 먼저 제시한다. runtime job은 inventory에서 예상값을 확인하되 CLI parameter로 임의 주입하지 않고 remote scan/DB metadata가 발견하게 한다.

Jenkins job 또는 strategy 이름만 주어지면 DB/log 경로를 사용자에게 묻지 않고 `daily-rsync/README.md`, `daily-rsync/DATA_LAYOUT.md`, `daily-rsync/OPERATIONS.md`를 확인해 local catalog에서 evidence를 자동 발견한다. local evidence가 없거나 요청 기간을 덮지 않으면 임의 SSH/rsync를 실행하지 말고 evidence gap과 필요한 sync 범위를 보고한다.
`default`는 Jenkins job이 아니라 runtime job이며, 하나의 strategy가 여러 Jenkins job에, 하나의 Jenkins job이 여러 strategy epoch에 대응할 수 있으므로 `source × Jenkins job × strategy × runtime job`을 evidence discovery 경계로 분리한다.
실제 성과 분석에서는 각 DB 내부를 `config_hash × git_commit × mode × job_name` cohort로 더 분리하며, discovery 경계를 하나의 분석 cohort로 간주하지 않는다. Golden Black·Kiwi·Blueberry·Pomegranate·Raspberry·Strawberry·Tangerine·Watermelon·Watermelon Live·Plum은 각 L3에 명시된 strategy source digest 기반 예외를 따른다.
Golden Pomegranate는 trade/fill retro 대상이 아니다. active `trades_sim.db`와 요청 구간의
`trades_sim_YYYYMMDD.db` shard를 모두 `daily-rsync verify`로 확인한 뒤 collector health,
cursor-complete census, source-component coverage, watermark gap과 manifest checksum을 검사한다.
`polybot-retro audit`의 order/fill 계약을 여기에 적용하지 않는다.

Golden Raspberry도 trade/fill retro 대상이 아니다. `polybot-do/re/mi`를 각각 scan한 뒤
독립 plan으로 sync/verify하고, current `raspberry-*-v3-shard-*` DB 세 개만
`queue-echo-analyzer-v3`에 명시한다. external-v2 DB는 별도 historical health evidence로만
검증하며 v3와 합치지 않는다. 첫 7일에는 slot claim, duplicate/late HTTP 0, FAILED 포함
runtime, cursor, YES/NO pair, follow-up claim/control, cohort, DB/storage만 판정하고 30일 전
수익성·threshold·live 승격을 결론내리지 않는다.

Golden Strawberry도 trade/fill retro 대상이 아니다. `daily-rsync verify`를 각각 통과한 frozen
v1과 follow-up v2a `trades_sim.db`의 절대 경로를 함께 지정한다. v1-only analyzer로 sampling
cursor/membership, crossing book과 metadata를 확인하고 `polybot-followup analyze`로 v1 anchor,
v2a cadence, imported seed hash, atomic publication, compact book/path/resolution coverage, cohort,
DB 무결성과 storage/SLA를 검사한다.
두 epoch를 합쳐 actual fill/P&L로 해석하지 않으며 parameter tuning·live 승격은 별도 healthy
out-of-sample gate 전까지 결론내리지 않는다.

Golden Watermelon도 trade/fill retro 대상이 아니다. `polybot-white`와
`polybot-grey`를 각각 `daily-rsync scan`한 뒤 독립 plan으로 sync/verify하고,
검증된 두 `trades_sim.db` 절대 경로를 자체 `polybot analyze`에 반복 지정한다.
첫 health gate에서 1분/5분 cadence, cursor, strict moneyline classification, exact `$5`
book/path/resolution, paired coverage, cohort, DB 무결성과 storage growth만 판정하고
수익성·X/Y 선택·live 승격을 결론내리지 않는다.

회고 시작 전에 UTC half-open range `[review-start, review-end-exclusive)`를 고정하고 `review-days`를 그 기간과 일치시키며, 보고서 첫머리에 range와 timezone, `remote_path`, verified DB 절대 경로와 SHA-256(`local_sha256`), `latest_successful_sync.finished_at`과 DB `synced_at` sync cutoff, `source_completed_at` 또는 remote `source_mtime_at` source cutoff를 기록한다. `polybot-retro --as-of`는 포함 종료일을 받아 다음 날 00:00Z를 exclusive end로 만들므로 `review-end-exclusive`의 전날을 넘긴다.
각 match에서 `latest_sync_attempt.status='SUCCESS'`와 `latest_successful_sync.status='SUCCESS'`, local DB/log 존재, artifact status가 `SYNCED` 또는 `SOURCE_MISSING`인지 확인하고 `verify`를 실행하며, plan 파일이나 디렉터리 이름만으로 sync 성공을 추정하지 않는다.
`SOURCE_MISSING` DB는 local file이 있고 `verify`가 성공하며 요청 UTC range 전체가 source cutoff 안에서 완결되는 `review-end-exclusive <= source_completed_at`(없으면 remote `source_mtime_at`)일 때만 historical evidence로 사용하고 limitation을 기록하며, cutoff를 넘으면 중단한다.
`verify.status='SUCCESS'`여도 `skipped_retention_deleted`는 explicit retention skip이지 log coverage가 아니므로, 요청 range에 필요한 log면 limitation과 evidence gap을 기록하고 중단한다.
`verify`를 통과한 catalog DB 절대 경로만 Evidence Contract audit에 명시하고, 여러 DB는 `--db`를 반복하며, `CRITICAL`/`HIGH` issue나 evidence gap이 있으면 추정·parameter tuning·승격을 중단한다.

```bash
cd daily-rsync
uv run daily-rsync locate --job <jenkins-job>
uv run daily-rsync locate --strategy <strategy>
uv run daily-rsync verify --job <jenkins-job> --strategy <strategy>
cd ..
uv run --project polybot-observability polybot-retro audit \
  --db <verified-db-path-1> \
  --db <verified-db-path-2> \
  --days <review-days> \
  --as-of <review-end-inclusive-date> \
  --output-dir <output-dir> \
  --strict
```

## 공통 명령어

폴더별로 다르다. Python은 `uv run <entry>`(golden-* 는 `uv run polybot`), 대시보드는 `npm run <script>`. 전략 공통 계약은 루트에서 `uv run tools/verify_strategy_contracts.py`, 관측성은 `uv run --project polybot-observability pytest polybot-observability/tests`로 검증한다. 상세는 각 폴더 README/AGENTS.md를 따른다.

## CI / 배포

- 전략 봇·`daily-report`: **Jenkins** 실행 (`daily-report/Jenkinsfile`). 루트에 GitHub Actions/GitLab CI 없음.
- `polymarket-dashboard`: Cloudflare Workers로 배포 — 트리거·운영 URL 등 상세는 L3 `AGENTS.md` 참조.

## 검증 기준

- 특정 폴더만 수정했다면 해당 폴더의 검증(lint/test/build)만 수행한다.
- 루트 공통 파일(`.gitignore`, `REPOS.md`)이나 Supabase `pb_*` 데이터 계약에 영향을 주는 변경은 영향 범위를 먼저 확인한다.
- 공통 전략 계약이나 shared observability를 수정하면 21개 거래 전략의 `uv sync --frozen --extra dev`와 test를 모두 실행하고, `golden-black`·`golden-coconut`·`golden-pomegranate`·`golden-raspberry`·`golden-strawberry`·`golden-watermelon`의 research-only test와 27-project contract verifier를 통과시킨다.
- 월간 수치 조정·전략 승격의 strict gate도 broad `--root` discovery를 쓰지 않고 위 절차로 검증한 DB를 `--db`로 반복 명시한다. `CRITICAL`/`HIGH` evidence issue가 있으면 조정하지 않고 수집·대사부터 복구한다.
- 수치를 조정하기 전에 대상 구간이 단일 cohort인지 확인한다. `strategy_configs` 테이블에 `config_hash`별 전체 config JSON이 남으므로, 여러 cohort가 섞인 구간의 집계로 파라미터를 정하지 않는다.

## 새 서브 프로젝트 추가 기준

1. 기존 폴더와 목적이 겹치지 않는가.
2. naming convention: 전략 봇은 과일 코드네임(`golden-*`), 인프라·도구는 역할/런타임 기반(`daily-report`, `*_proj`).
3. Python이면 uv, Node면 npm 스캐폴드를 맞춘다.
4. 독립 `README.md`와 필요 시 L3 `AGENTS.md`를 둔다.
5. `REPOS.md`와 본 인덱스에 등록한다.
6. `docs/new-strategy-playbook.md`의 research/falsification/backtest, config validation, simulation, run/order/fill/archive, reporting, retro, promotion gate를 모두 충족한다.
7. `docs/retro/golden-<name>.md`를 만들고 `uv run tools/verify_strategy_contracts.py`를 통과한다. unit test만으로 수익성을 주장하지 않는다.

## 주의사항

- 실거래 봇은 `config.yaml`의 `simulation_mode`와 `.env` 실키에 민감하다. 키 취급은 L1 보안 규칙을 따른다. `golden-papaya`·`golden-queen`은 `simulation_mode: true`가 기본이라 실주문을 내지 않는다. `golden-kiwi`는 source-level live hard block이 있는 simulation/research 전용이다.
- `POLYMARKET_SIGNATURE_TYPE`은 계정 종류에 따라 반드시 맞춰야 한다: `1`=POLY_PROXY(구형 이메일 계정), `3`=POLY_1271(2026년 이후 신규 계정의 스마트 지갑). 틀리면 CLOB이 `maker address not allowed`로 전 주문을 거절한다. live-capable 19개 전략과 `tools/wind_down.py`가 이 env를 읽으며, Kiwi simulation에는 실제 credential을 주입하지 않는다.
- `golden-black`·`golden-coconut`·`golden-pomegranate`·`golden-raspberry`·`golden-strawberry`·`golden-watermelon`은 credential-free collector다. signature type을 포함한 credential-like
  environment variable가 하나라도 있거나 `--live`/`active`/`close_only`로 실행하면 network와 DB를 열기 전에 실패해야 한다.
- Jenkins Freestyle에서 private key를 inline `export`하거나 `sh -x`/`sh -xe`로 노출하지 않는다. Credentials Binding을 사용하고 secret 참조 전부터 `set +x`를 적용한다.
- SQLite DB와 Jenkins artifact는 유일한 backup으로 취급하지 않는다. online backup + SHA-256 manifest를 workspace 밖 내구성 저장소에 복제하고 복구 검증한다.
- 루트 `firebase-debug.log`는 추적되지 않는 잔여 로그다 (정리 권장, 임의 삭제는 하지 않음).
- `streamlit_proj`·`cloud_run_proj`의 기존 `CLAUDE.md`는 L1 `@AGENTS.md` 컨벤션과 다를 수 있다. 정리는 별도 작업으로 다룬다.
