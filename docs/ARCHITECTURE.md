# polylab 아키텍처 (v1, 2026-10-01)

Polymarket 스포츠 예측시장의 **경기 시간대별 과대/과소 평가(calibration gap)** 와 **동일 이벤트(득점 등)에 대한
시간대별 가격 민감도**를 연구하면서, 그 편향을 이용하는 전략을 실거래로 검증하는 완전 자동화 시스템이다.
사람은 대시보드(https://poly.zowoo.uk)와 Slack `#polymarket-report`로 결과만 확인한다.

## 1. 원칙

1. **한 곳에서 수집, 여러 전략이 소비.** Polymarket 공용 데이터(경기·마켓·가격·호가·체결·정산)는 중앙 DB 하나에만 쓴다.
   전략은 API를 직접 두드리지 않고 중앙 DB의 최신 스냅샷을 읽는다(주문 직전 호가 재확인만 예외).
2. **전략별 원장 분리.** 주문 intent·CONFIRMED fill·포지션·파라미터 버전은 전략 DB(`strategies/<id>.db`)에 둔다.
3. **모든 DB는 Mac mini 외장하드 `/Volumes/t7/polylab/data`.** 마운트가 없으면 fail closed(내부 디스크 fallback 금지).
4. **실제 손익 = CONFIRMED fill + fee + 확인된 resolution.** 요청가·평가손익·시뮬레이션은 실손익에 섞지 않는다.
5. **모든 시각은 UTC 저장**, 표시만 KST. source 시각과 received 시각을 분리 저장한다.
6. **AI는 결정론적 가드레일 안에서만 실거래 설정을 바꾼다.** (7절)
7. **공개 저장소.** 개인키·funder 주소·토큰은 저장소에 절대 두지 않는다. 비밀은 `~/.polylab/*.env`(chmod 600)에만 있다.

## 2. 범위

- 종목: soccer, MLB, NBA, NFL, NHL (복싱·MMA·e-sports·테니스 제외). Gamma tag: soccer 100350, MLB 100381, NBA 745, NFL 450, NHL 899.
- 축구 대회(`collector/common.py` `MAJOR_SOCCER_LEAGUES`, 대회별 거래량 근거는 `docs/research/api-sources.md`):
  EPL·La Liga·Bundesliga·Serie A·Ligue 1, MLS, UEFA Champions League·Europa League·Nations League, World Cup·Euro.
  친선전·예선·기타 리그는 수집하지 않는다(과거 적재분은 DB 에 남지만 분석 기본값에서 제외, `--all-leagues` 로 포함).
- 마켓: whole-game moneyline(축구는 home/draw/away)이 1순위. 거래량 하한을 넘는 **경기 자체**의 단순 마켓만 추가 수집:
  축구 합계 골 Over/Under(0.5–3.5, `total`), 양 팀 득점(`btts`), 팀 득점 여부(`team_to_score`), 미국 종목 totals/spreads.
  선수 prop·코너·카드·정확한 스코어·전후반 마켓은 제외한다.
- 실거래 대상은 이보다 좁다: 각 변형 yaml 의 `leagues` 와 `min_game_volume_usd`(기본 2만 USDC).

## 3. 런타임 레이아웃 (Mac mini)

```
/Volumes/t7/polylab/
  repo/                  # 이 git 저장소 checkout (deploy key, main). Jenkins·autopilot이 사용
  data/
    core.db              # 중앙 공용 DB (경기·마켓·토큰·1분 가격·게임상태·정산·체크포인트)
    books/YYYY-MM.db     # 호가 스냅샷(top-10 levels, zlib) 월별 shard
    raw/YYYY-MM-DD/*.jsonl.gz   # WebSocket 원본 (sports/market), 일별 gzip
    strategies/<strategy_id>.db # 전략별 원장
    research/            # parquet/csv export, 분석 산출물
  logs/
  archive/               # 레거시 원장 압축본 (legacy-ledgers-20260930.tar.gz, NAS /Volumes/VR/polylab-archive 에도 사본)
~/.polylab/              # 비밀 (chmod 700): accounts.env, services.env, claude_oauth_token
```

## 4. 저장소 레이아웃

```
pyproject.toml            # uv 단일 패키지 polylab (python 3.12)
src/polylab/
  settings.py             # 경로·env 로딩·fail-closed 체크
  db/                     # sqlite 연결, 스키마(core/books/strategy), 마이그레이션
  api/                    # gamma.py, clob_public.py, data_api.py, ws_sports.py, ws_market.py (출처 주석 필수)
  collector/              # discover, poll(1분 가격+호가), stream daemon(WS), backfill(prices-history, trades), resolve
  strategies/             # base.py + watermelon.py, apricot.py, plum.py, cherry.py (순수 로직, IO 없음)
  execution/              # accounts, clob trading client(FOK), reconcile(CONFIRMED), paper broker, redeem
  engine/                 # tick: 전략 실행 루프(스냅샷→signal→risk→execution→ledger)
  risk/                   # stake ladder, caps, kill switch
  analysis/               # calibration, event study(sensitivity by game minute), strategy perf, stake-tier stats, backtest
  reports/                # daily/weekly/monthly 결정론 리포트(md+json), slack
  publish/                # Supabase Storage JSON 스냅샷 업로드 (대시보드 read model)
  autopilot/              # AI 회고: context pack → claude -p → proposal JSON → validator → apply → commit/push
  cli.py                  # `polylab <command>`
strategies/*.yaml         # 전략 변형 레지스트리 (파라미터·경계·단위·계좌 alias·모드) — autopilot 이 수정
jenkins/                  # 잡 정의(config.xml 템플릿)와 sync 스크립트
dashboard/                # Next.js on Cloudflare Workers (poly.zowoo.uk)
docs/                     # ARCHITECTURE, research(논문용), strategies(명세)
reports/                  # autopilot 이 커밋하는 daily/weekly/monthly 회고 (공개)
tests/
```

## 5. 중앙 DB 스키마 (core.db 요약)

| table | key | 내용 |
|---|---|---|
| games | game_key(=gamma event id) | sport, league, home/away, start_time, status, polymarket_game_id, sportradar_game_id, final score, ended_at |
| markets | condition_id | event_id, game_key, market_type(moneyline/draw/total/spread/other), line, question, slug, volume, liquidity, fee_schedule, closed, resolved_outcome, resolved_at |
| tokens | token_id | condition_id, outcome_index, outcome_label, side(home/away/draw/yes/no/over/under) |
| price_bars | (token_id, ts_minute, source) | 1분 가격. source = poll_mid / ws_last / history |
| game_states | (game_key, ts) | status, period, elapsed(분), home/away score, source(ws/gamma), received_at — 변화 시에만 |
| market_metrics | (condition_id, ts) | volume, liquidity, open_interest |
| public_trades | trade uid | Data API 체결(경기 종료 후 backfill): wallet, side, price, size, ts, tx hash |
| checkpoints | name | 수집기 진행 상태 |
| quality_events | id | 역전 timestamp, bid>ask, gap, WS 끊김, backfill 불일치 등 |

books/YYYY-MM.db: `book_snapshots(token_id, ts, best_bid, best_ask, mid, spread, bid_depth_usd, ask_depth_usd, imb_l1, imb_l5, imb_l10, levels_z)`.

**Canonical price 정책**: 같은 분에 여러 source가 있으면 poll_mid(호가 기반) > ws_last > history 순. 분석 view가 이 정책을 구현한다.

## 6. 전략 레지스트리 (strategies/*.yaml)

```yaml
id: watermelon-cat            # 변형 id = 원장 DB 이름
family: watermelon            # src/polylab/strategies/<family>.py
hypothesis: "in-play .92+ favourites are underpriced vs realized win rate"
account: cat                  # ~/.polylab/accounts.env 의 POLYBOT_CAT__* 로 해석
mode: live                    # live | paper | off
sports: [soccer, nfl]
stake_usdc: 5                 # ladder 5→10→25→50→100 (registry.STAKE_LADDER)
params: {...}                 # 현재 값
bounds: {param: [min, max, max_step]}   # autopilot 탐색 경계
limits: {max_positions: 20, max_open_usdc: 300, daily_loss_stop_usdc: 50}
```

## 7. 자동화와 재귀 개선

| Jenkins job (view `polylab`) | 주기 (KST) | 역할 |
|---|---|---|
| polylab-tick | 매 1분 | 1분 poll(가격·호가) → 전략 청산·진입·대사, 시간당 1회 자동 redeem(원장 소유분만) |
| polylab-discover | 10분 | 5개 종목 경기·마켓 탐색(120h 앞까지), 정산 확인 |
| polylab-stream | 상시 | sports+market WebSocket daemon, 빌드당 59분·끝나면 즉시 다음 빌드가 이어받음 |
| polylab-backfill | 매시 | 종료 경기 prices-history·체결·정산 백필 + 2026-02 이후 과거 경기 점진 백필 |
| polylab-publish | 5분 | git pull → 대시보드 JSON(Supabase Storage) → `health --alert`(상태 변화 시만 Slack) |
| polylab-retro-daily | 03:30, 08:00, 19:30 | 결정론 리포트 → claude(→codex) 회고 → validator → commit/push → publish → Slack |
| polylab-retro-weekly | 월 08:30 | + 백테스트 grid·paper 변형 제안·codex second opinion |
| polylab-retro-monthly | 매월 1일 09:00 | + 논문용 월간 연구 요약 docs/research/monthly |

모든 잡은 macOS TCC 때문에 `ssh polylab-local` 을 거쳐 실행된다(`docs/ops/macmini-runbook.md`).

**Stake ladder (결정론)**: 모든 변형은 5 USDC에서 시작. 현 단위에서 정산 거래 ≥ 20, 순손익 > 0, 거래당 ROI의
bootstrap 80% 하한 > 0, 최대 낙폭 < 현 단위×6 이면 한 단계 증액(5→10→25→50→100, 상한 100).
최근 20건 순손익 < 0 이고 ROI 하한 < 0 이면 한 단계 감액. 5에서 40건 이상 누적 손실이면 paper 로 강등.
단위 변경 후 최소 3일 cooldown.

**AI 회고 루프**: `polylab retro <daily|weekly|monthly>` 가 결정론 지표(context pack)를 만들고, Mac mini 의
`claude -p`(CLAUDE_CODE_OAUTH_TOKEN) 가 서술 회고 + `proposal.json`(파라미터 변경/신규 paper 변형/폐기)을 작성한다.
validator 가 bounds·max_step·최소 표본·cooldown 을 강제하고 통과분만 yaml 에 반영, 테스트 후 commit/push.
주간 회고는 paper 변형 생성과 백테스트, 월간 회고는 논문용 연구 요약(docs/research/monthly)을 만든다.
AI 가 실패해도 결정론 리포트와 ladder 는 동작한다.

## 8. 0:0 회피 연구 (투 트랙, 2026-10-03)

- **트랙 1 (자동, 5 USDC, AI 재귀 개선)**: `goal-over-all`(주요 리그 모든 경기 Over 0.5 를 킥오프 60→5분 전 가격 범위 안에서 기계적 매수)과
  `llm-nil-consensus`(매일 10:00 Claude·ChatGPT 가 같은 질문으로 각자 모든 경기 P(0:0)를 예측 → 합의 top-3 중 시장 대비 우위가 있으면 매수).
  비교군 `llm-nil-draw`(Claude 단독, paper). 예측은 `research/llm_forecasts.db` 에 킥오프 전 append-only 로 기록. 설계 `docs/research/llm-forecast-study.md`.
- **트랙 2 (수동, 연구자 직접 베팅)**: 키 없이 공개 지갑 주소(`~/.polylab/watch.env`)만으로 15분마다 체결·정산을 가져와
  `data/manual/<alias>.db` 에 손익을 기록(`polylab manual sync`). 연구자 AI 스킬의 예측은 `manual/predictions/` 표로 선택 기록.
