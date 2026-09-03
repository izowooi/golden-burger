# L4 AGENTS.md — Golden Peach

이 문서는 `golden-peach` 하위 프로젝트에만 적용된다. 저장소 공통 보안·Git·증거 계약은
상위 `../AGENTS.md`를 따른다.

## 프로젝트 목적

Golden Peach는 경기 시작 직후 직접 승자 호가의 표시 선두 하나를 한 번만 매수하고 작은
상대 가격 변화를 A/B 검증하는 전략이다. Eco/Fruit는 축구 `$5` live A/B이고, Grey는 축구
6호가와 MLB/NBA/NFL/NHL 2호가의 종목별·거래규모별 simulation 자료 수집기다.

## 기술 스택과 주요 파일

- Python 3.11+, `uv`, SQLAlchemy/SQLite, `py-clob-client-v2`, Gamma API.
- `src/polybot/config.py`: job별 mode·A/B 파라미터·기간의 fail-closed 계약.
- `src/polybot/league_classifier.py`: 허용 리그의 source identity 검증.
- `src/polybot/strategy/scanner.py`: sport profile별 event book 비교, sizing 증거와 entry claim.
- `src/polybot/strategy/trader.py`: 가변 FOK BUY, exact fill 대사, TP/SL과 resolution.
- `research/frozen-2026-09-03-mlb-live-ab-v4/`: 현재 source identity와 MLB live A/B 계약.
- `OPERATIONS.md`: Jenkins와 `daily-rsync` 운영 절차.

## 고정 실험 계약

| Jenkins | runtime job | mode | 유일한 A/B 차이 |
|---|---|---|---|
| `polybot-eco` | `peach-live-eco-3pp-1m-v1` | live | TP `+0.03` |
| `polybot-fruit` | `peach-live-fruit-5pp-1m-v1` | live | TP `+0.05` |
| `polybot-eco` | `peach-live-eco-mlb-7pp-20sl-1m-v1` | MLB live | TP `+0.07`, SL `-0.20` |
| `polybot-fruit` | `peach-live-fruit-mlb-10pp-20sl-1m-v1` | MLB live | TP `+0.10`, SL `-0.20` |
| `polybot-grey` | soccer/MLB/NBA/NFL/NHL shadow 5개 | simulation | TP `+0.05`, raw/sizing 자료 수집 |

- EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL만 허용한다.
- 예정 시작시각이 아니라 source `live=true`, `ended=false`, `elapsed/period`로 실제 시작을
  증명한다. 신규 진입은 source 0~10분이다.
- HOME/DRAW/AWAY 세 명제와 각 직접 YES/NO의 exact `$5` full-depth book이 모두 있어야 한다.
  NO 가격·깊이를 `1-YES`로 합성하지 않는다.
- MLB/NBA/NFL/NHL은 whole-game moneyline의 팀 token 두 개를 직접 비교한다. MLB live는
  등록된 두 runtime만 허용하고 NBA/NFL/NHL은 simulation-only다. sport-native
  clock과 종목별 TP/SL이 동결되기 전까지 Grey simulation만 허용한다.
- 표시 호가 midpoint의 유일한 선두를 선택하며 2위와 최소 `0.005`, 진입 VWAP
  `[0.60, 0.94]`, spread `<=0.05`를 요구한다.
- event당 실제/불확실 BUY는 평생 한 번뿐이다. exact terminal zero-fill만 재시도한다.
- 공통 SL은 confirmed BUY VWAP `-0.10`이다. source 80분부터 신규 stop은 금지하고,
  정상 TP의 절반을 넘으면 익절하며 손실 중이면 증명된 resolution까지 보유한다.
- SELL 실패는 event-local이다. 180분 뒤 성공 체결로 꾸미지 않고 `QUARANTINED`로 격리하며
  경제적 노출 한도는 계속 차지한다.
- 신호 비교는 baseline `$5` book으로 고정한다. 목표액을 올린 live cohort는 fresh book에서
  `$5` 이상 동결 ladder 중 가장 큰 전량 체결 가능 금액을 FOK 한 건으로 제출하고 목표액,
  선택액, 표시 가능액과 축소 사유를 저장한다.
- Grey는 같은 book에서 `$5`~`$1000`의 매수/즉시매도 표시 깊이를 계산하고 종목·리그·원본
  태그를 저장한다. 이를 실제 fill이나 수익으로 해석하지 않는다.

## 실행·검사

```bash
uv sync --frozen --extra dev
uv run pytest
uv run polybot config --simulate --job peach-shadow-1m-v1
POLYBOT_TAKE_PROFIT_DELTA=0.05 uv run polybot run --simulate --job peach-shadow-1m-v1
uv build
```

Live 명령은 Jenkins Credentials Binding으로 credential을 공급하고 명시적인 `--live`를
사용한다. simulation에는 credential-like 환경 변수를 주입하지 않는다.

## 작업 규칙

- `config_hash × strategy_source_digest × mode × job_name`을 하나의 cohort로 본다.
- accepted order를 fill로 해석하지 않는다. confirmed size/VWAP/fee가 완전할 때만 실현 성과다.
- raw direct book과 source clock이 없는 행을 보간하거나 합성해 live 결정을 만들지 않는다.
- 종목 또는 목표 주문액이 다르면 같은 cohort로 합치지 않는다.
- simulation에는 거래소 fill/fee 원장이 없으므로 그 부재를 live 증거 공백으로 오판해 다른
  경기의 shadow 진입을 막지 않는다. live의 동일 방어는 반드시 fail closed로 유지한다.
- DB를 clean/merge/backfill하지 않는다. runtime job 이름을 바꿔 epoch를 분리한다.
- 외부 API 테스트는 mock으로 수행하고 실제 credential 값을 출력·커밋하지 않는다.
- 배포 후 Jenkins console뿐 아니라 `daily-rsync verify`, SQLite `quick_check`, 최신 run audit,
  direct YES/NO·source clock·book coverage를 함께 확인한다.

## 자주 깨지는 부분

- source clock의 2H period-relative 값과 `90+N` 정규화.
- YES/NO token alignment와 HOME/DRAW/AWAY triad 누락.
- baseline `$5` 신호와 adaptive BUY, two-decimal SELL signing의 수량 정밀도.
- FOK accepted/DELAYED와 실제 fill을 혼동하는 lifecycle.
- 실패한 SELL 하나가 전체 신규 진입을 막는 global gate 회귀.
