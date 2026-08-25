# Golden Watermelon Live

`golden-watermelon`의 White/Grey accountless 관측 결과를 실제 최소 금액으로 확인하는 독립
live A/B 프로젝트다. 기존 collector 코드는 그대로 두고, 폐쇄된 `polybot-cat`과
`polybot-dog` wallet만 재사용한다.

| arm | Jenkins | runtime job | exact `$5` ask VWAP |
|---|---|---|---:|
| Cat | `polybot-cat` | `watermelon-live-cat-98-1m-v2d` | `[0.98, 0.999]` |
| Dog | `polybot-dog` | `watermelon-live-dog-99-1m-v2d` | `[0.99, 0.999]` |

두 arm의 유일한 처치 차이는 진입 하한이다. 계정·signature type은 각 Jenkins의 기존 값을
보존하며, 분석은 job별 cohort로 분리한다.

## 동결된 거래 계약

- EPL, Bundesliga, Ligue 1, LaLiga, MLS의 명시적 league identity만 허용
- 경기 시작 후 0~4시간, `live=true`, `ended=false`인 top-level whole-match event만 허용
- home/draw/away moneyline proposition의 **YES token**만 검사
- 한 경기에서 한 결과의 exact `$5` ask VWAP가 arm threshold에 처음 도달했을 때만 진입
- threshold에 도달한 결과가 없는 경기는 주문하지 않음. “전 경기”는 임의 결과를 강제
  선택한다는 뜻이 아니라, 조건을 충족한 모든 대상 경기를 빠뜨리지 않는다는 뜻이다.
- 진입 직전 full ask depth를 다시 walk하고 marketable FOK BUY 제출
- 결과 token의 fresh best bid가 `0.70` 이하이면 전체 보유 수량의 displayed bid depth를
  walk한 뒤 가장 낮은 소비 bid를 limit으로 marketable FOK SELL 제출
- stop이 체결되지 않거나 depth가 부족하면 손실을 추정해 종결하지 않고 보유·대사 상태 유지
- stop 전에는 resolution까지 보유하며 TP와 time exit는 없음
- account 최대 20개, event당 1개, cycle당 최대 20개, 주문당 정확히 `$5`
- max-position capacity는 `QUARANTINED`를 포함한 open trade와 trade에 연결되지 않은 모든
  live BUY intent를 함께 계산하며, 같은 예약을 event cap에도 반영
- unresolved pending state 또는 SELL evidence gap 중에는 후보를 계속 기록하되 신규 BUY를 중지
- 첫 후보가 guard·fresh-book·주문 단계에서 실행되지 않은 이유를 episode에 보존하고,
  ledger로 증명된 orphan BUY만 원자적으로 Trade에 복구
- live 주문 전 Gamma와 CLOB의 token/condition 및 동적 fee schedule이 정확히 일치해야 함
- confirmed taker fill은 legacy `fee_rate_bps=0`이 아니라 exact fill과 CLOB v2 schedule로
  계산한 5-decimal fee amount를 저장해야 함
- 봇 DB가 만든 trade만 관리하며 wallet의 수동 position은 편입하거나 청산하지 않음

Gamma liquidity/volume 숫자는 진입 gate로 쓰지 않는다. 매수는 정확히 `$5`를 전량 소진할 수
있는 displayed ask depth, 매도는 전체 보유 수량을 전량 소진할 수 있는 displayed bid depth가
필수이므로 실행 가능성은 CLOB에서 직접 검증한다.

White/Grey paired evidence에서 5분 Grey episode 11개는 모두 1분 White에도 있었지만,
White는 추가 8개를 관측했다. 실제 live cycle도 약 7초 안에 끝났으므로 두 arm은 동일한
1분 cadence를 쓴다. 이는 시험한 1분/5분 중 coverage가 더 완전한 선택이지, 수익성 기준의
최적 cadence 확정은 아니다.

1분 Jenkins cadence도 연속 stop daemon이 아니다. 가격이 두 cycle 사이에 급락하면 `0.70`보다
훨씬 낮은 가격에 체결되거나 depth 부족으로 체결되지 않을 수 있다. 이번 최소금액 pilot은 이
execution gap도 실제 증거로 수집한다.

entry window는 `[2026-08-24T13:00:00Z, 2026-08-31T13:00:00Z)`, follow-up cutoff는
`2026-09-07T13:00:00Z`다. 이 값은 source에서 fail-closed하게 고정된다. `0.999`는 세 번째
진입 threshold가 아니라 terminal `1.000`을 제외하는 두 arm의 공통 상한이다. White/Grey의
작은 표본 때문에 0.98/0.99를 “최적값”이라고 부르지 않으며, 보수적인 첫 prospective A/B로만
해석한다. 근거와 판정 기준은 [STRATEGY.md](STRATEGY.md), Jenkins 절차는
[OPERATIONS.md](OPERATIONS.md)를 따른다.

성과 cohort는 `config_hash × strategy_source_digest × mode × job_name`이다. Git commit은
배포 provenance로만 사용한다.

v2b는 과거 함대의 max-position 우회/고착과 불완전한 후보 근거를 막은 safety epoch다.
첫 Cat 체결에서 CLOB v2의 동적 taker fee를 legacy 0-rate로 오판한 결함이 발견되어 v2c가
fee metadata preflight와 exact fee amount 증거를 추가했다. 이후 함대 장애 이력 정적 감사에서
orphan BUY capacity, `QUARANTINED`, signed SELL 수량, exact resolution identity 등 잠재 경로가
발견되어 v2d가 lifecycle 방어와 episode별 실행 사유를 추가했다. threshold·stop·cadence는
바뀌지 않았다. v2a/v2b/v2c DB는 immutable 배포 증거로 보존하고 v2d DB와 합치지 않는다.

## 로컬 검증

```bash
cd golden-watermelon-live
uv sync --frozen --extra dev
uv run pytest

# 실제 값은 untracked .env 또는 Jenkins credential에서만 제공한다.
uv run polybot config --simulate --job watermelon-local
uv run polybot run --simulate --job watermelon-local
uv run polybot status --simulate --job watermelon-local
```

실주문은 매번 명시적인 `--live`가 있어야 한다. 기본 `config.yaml`은 simulation이다.
`POLYBOT_LIFECYCLE_MODE`는 `active`, `close_only`, `archive_only`를 지원한다. 중단·청산은
[공통 wind-down 절차](../docs/strategy-wind-down-playbook.md)를 따르며 clean build, workspace
wipe, 기존 DB 삭제는 사용하지 않는다.
