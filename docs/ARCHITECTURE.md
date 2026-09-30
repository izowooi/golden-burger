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

- 종목: soccer(주요 리그), MLB, NBA, NFL, NHL. Gamma tag: soccer 100350, MLB 100381, NBA 745, NFL 450, NHL 899.
- 마켓: whole-game moneyline(축구는 home/draw/away)을 1순위로, 볼륨 기준(`min_volume_usd`)을 넘는 totals/spread 등
  **경기 결과 자체**에 대한 마켓만 2순위로 수집한다. 선수 prop·이색 마켓은 제외한다.

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

| Jenkins job | 주기 (KST) | 역할 |
|---|---|---|
| polylab-tick | 매 1분 | discover(10분마다)·poll·전략 실행·주문·대사 |
| polylab-stream | 5분 watchdog | launchd WS daemon(sports+market) 상태 확인·재기동 |
| polylab-backfill | 매시 | 종료 경기 prices-history·trades·resolution·OI 백필, 과거 데이터 점진 백필 |
| polylab-publish | 5분 | 대시보드 JSON 스냅샷 → Supabase Storage, git pull |
| polylab-retro | 03:30, 08:00, 19:30 매일 / 월 08:30 주간 / 1일 09:00 월간 | 결정론 리포트 → claude -p 회고 → 제안 적용 → Slack |

**Stake ladder (결정론)**: 모든 변형은 5 USDC에서 시작. 현 단위에서 정산 거래 ≥ 20, 순손익 > 0, 거래당 ROI의
bootstrap 80% 하한 > 0, 최대 낙폭 < 현 단위×6 이면 한 단계 증액(5→10→25→50→100, 상한 100).
최근 20건 순손익 < 0 이고 ROI 하한 < 0 이면 한 단계 감액. 5에서 40건 이상 누적 손실이면 paper 로 강등.
단위 변경 후 최소 3일 cooldown.

**AI 회고 루프**: `polylab retro <daily|weekly|monthly>` 가 결정론 지표(context pack)를 만들고, Mac mini 의
`claude -p`(CLAUDE_CODE_OAUTH_TOKEN) 가 서술 회고 + `proposal.json`(파라미터 변경/신규 paper 변형/폐기)을 작성한다.
validator 가 bounds·max_step·최소 표본·cooldown 을 강제하고 통과분만 yaml 에 반영, 테스트 후 commit/push.
주간 회고는 paper 변형 생성과 백테스트, 월간 회고는 논문용 연구 요약(docs/research/monthly)을 만든다.
AI 가 실패해도 결정론 리포트와 ladder 는 동작한다.
