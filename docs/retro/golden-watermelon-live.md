# Golden Watermelon Live 회고 계약

공통 execution evidence 정의는 [EVIDENCE_CONTRACT.md](EVIDENCE_CONTRACT.md)를 따른다.

```text
REVIEW_START=2026-08-24T13:00:00Z
REVIEW_END=2026-08-31T13:00:00Z
FOLLOWUP_END=2026-09-07T13:00:00Z
```

`polybot-cat`은 exact `$5` ask VWAP `[0.98,0.999]`, `polybot-dog`은
`[0.99,0.999]`를 사용한다. threshold 외 계약은 동일하다. 분석 cohort는
`config_hash × strategy_source_digest × mode × job_name`으로 나누고 account/job 차이도
별도로 표시한다.

## 2026-08-24 live 배포 증거

- 최종 source commit: `5bb2b12`; source digest `89afb9bf4347…`
- Cat: Jenkins config SHA-256 `d4a409e3b910…`, runtime
  `watermelon-live-cat-98`, config cohort `e5799397045a…`
- Dog: Jenkins config SHA-256 `ec4a8b4d781b…`, runtime
  `watermelon-live-dog-99`, config cohort `aa0793da1a5f…`
- 두 job 모두 clean 비활성, non-concurrent, build retention 14일, 실제
  `TimerTrigger=H/5 * * * *`다.
- 최종 cohort에서 Cat `#5158/#5159` 수동 + `#5160` timer, Dog
  `#5053/#5054` 수동 + `#5055` timer가 모두 `SUCCESS`였다. 각 DB에 3/3 SUCCESS run,
  cursor-complete sweep 3/3이 기록됐다.
- `create_or_derive_api_key()`의 create-first 400 로그를 발견해 timer를 끄고
  `derive_api_key()` fail-closed 방식으로 고친 뒤 재배포했다. 최종 6개 build는 모두
  `derive-api-key 200`이며 신규 API-key 생성 요청이 없다.
- 최초 commit `8236040`의 각 3개 run은 배포 검증 epoch다. event·market·trade·order가
  모두 0이어서 금전·선택 오염은 없지만 source digest가 다르므로 성과 분석에서는 최종
  cohort와 합치지 않는다.
- 최종 sync cutoff에서 두 DB 모두 `quick_check=ok`, FK 위반 0, pending BUY/SELL 0,
  trade/order/fill 0이다. 당시 허용 리그의 진행 중 event가 0이었으므로 실제 FOK 체결과
  stop 경로는 첫 후보 발생 후 별도로 검증해야 한다.

최종 verified DB:

- Cat: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cat/strategies/golden-watermelon-live/runtime/watermelon-live-cat-98/databases/latest/trades.db`
  (`SHA-256 8a8169f25c73…`, source cutoff `2026-08-24T14:31:38.205261Z`)
- Dog: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-dog/strategies/golden-watermelon-live/runtime/watermelon-live-dog-99/databases/latest/trades.db`
  (`SHA-256 53dc26260cad…`, source cutoff `2026-08-24T14:31:38.235478Z`)

`daily-rsync verify`는 job별 8개 artifact를 검사해 failed/conflict/retention skip 0으로
`SUCCESS`였다. 배포 시점에 대상 경기가 없었다는 사실을 cadence 실패나 universe 결함으로
해석하지 않는다. 24시간 collection health에서 실제 리그 membership과 crossing/book
coverage를 다시 확인한다.

24시간 점검에서는 cadence, cursor completion, five-league identity, whole-match
HOME/DRAW/AWAY YES membership, exact `$5` ask depth, first episode, FOK submission,
order/fill/fee reconciliation, stop bid-depth evidence, DB integrity만 확인한다. 수익성이나
threshold 승자를 판단하지 않는다.

7일 entry 종료에는 다음을 arm별·league별로 기록한다.

- eligible unique event와 threshold-crossing event
- 한 event 한 entry 계약과 실제 FOK BUY/confirmed fill coverage
- `PENDING_BUY`, `HOLDING`, `PENDING_SELL`, `COMPLETED`, `RESOLVED`,
  `UNFILLED`, `QUARANTINED` 상태
- best-bid `0.70` trigger, full-depth executable VWAP, trigger-to-fill gap, zero-fill/depth 부족
- exact BUY/SELL fill size·VWAP·fee와 proven payout coverage
- manual wallet position 비편입 및 과거 Papaya DB epoch 분리

성과 판정은 follow-up cutoff까지 terminal evidence가 모인 뒤 수행한다. requested order,
accepted response, requested price/size, settlement assumption을 realized P&L로 바꾸지 않는다.
CRITICAL/HIGH gap, mixed cohort, fee 누락, unresolved open state, 표본 부족이 있으면 수익성·
scale-up 판단을 중단한다. 0.98/0.99는 선행 표본이 매우 작은 보수적 pilot이며 “최적값”으로
간주하지 않는다.

동기화 후 verified catalog DB 절대 경로만 audit에 넘긴다.

```bash
cd daily-rsync
uv run daily-rsync verify --job polybot-cat --strategy golden-watermelon-live
uv run daily-rsync verify --job polybot-dog --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-cat --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-dog --strategy golden-watermelon-live

cd ..
uv run --project polybot-observability polybot-retro audit \
  --db <verified-cat-db> \
  --db <verified-dog-db> \
  --days 7 \
  --as-of 2026-08-31 \
  --output-dir <output-dir> \
  --strict
```
