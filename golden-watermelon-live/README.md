# Golden Watermelon Live

`golden-watermelon`의 accountless evidence를 Cat/Dog wallet에서 exact `$5`로 검정하는 독립
live A/B다. 기존 wallet credential과 수동 position은 건드리지 않는다.

| arm | Jenkins | runtime | exact `$5` ask VWAP |
|---|---|---|---:|
| Cat | `polybot-cat` | `watermelon-live-cat-96-1m-v2h` | `[0.96, 0.999]` |
| Dog | `polybot-dog` | `watermelon-live-dog-99-1m-v2h` | `[0.99, 0.999]` |

두 arm의 유일한 treatment 차이는 진입 하한이다. cadence는 모두 매분이고 주문은 계속 `$5`다.
White/Grey의 timing·notional evidence가 충분해질 때까지 live 시간 gate나 금액을 바꾸지 않는다.

## 거래 계약

- EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A, UEFA Champions League, UEFA Europa League의
  exact numeric identity만 허용.
- UEFA는 competition tag/series/prefix/UEFA resolution host를 모두 요구하며 다른 국내 league
  소속 두 팀을 정상적으로 허용.
- 경기 시작 후 `[0h,4h]`, explicit `live=true`, `ended=false`인 top-level event만 허용.
- 정규 90분과 stoppage time만 payout인 HOME/DRAW/AWAY whole-match moneyline YES만 허용.
- child/advancement/extra time/penalty market, e-sports와 다른 대회는 주문 전 제외.
- exact `$5` full ask-depth prewalk 후 fresh marketable FOK BUY.
- best bid `<=0.70`이고 Gamma event와 CLOB condition이 각각 live/open이며 market이
  order-taking 상태임을 재확인한 경우에만 전체 signable shares의 full bid-depth를 다시 읽고
  FOK SELL. 최저 level/VWAP `>=0.65`, spread `<=0.10`, projected loss `<=35%`를 모두 강제하며
  한 cycle에 한 건만 제출한다. 경기 종료 후 0.001 cleanup bid는 손절로 해석하지 않는다.
- accepted order는 fill이 아니다. terminal fill과 fee 대사 전 lifecycle을 확정하지 않음.
- account 20/event 1/cycle 20, manual wallet position 편입·청산 금지.
- PENDING/QUARANTINED/orphan/fill·fee gap이 있으면 후보는 기록하되 신규 BUY fail closed.
- confirmed SELL + proven-resolution 경제손익이 `-$10`에 도달하면 기존 position 관리는
  계속하되 신규 BUY를 자동 차단한다.

Gamma liquidity/volume 숫자는 gate가 아니다. 실제 실행 가능성은 주문에 필요한 CLOB 쪽의 full
depth로 직접 검증한다. 20개 제한은 현재 wallet position 수가 아니라 bot-owned open exposure와
unresolved BUY reservation의 최대치다.

1분 polling은 0.97 체결이나 0.70 stop 가격을 보장하지 않는다. 두 cycle 사이에 가격이 jump하거나
book이 닫힐 수 있고 FOK는 full fill이 불가능하면 0 fill이다. 이런 miss/gap도 실행 evidence로
남긴다.

정기 Jenkins cycle은 검증된 release commit을 workspace에 pin한 뒤 SCM checkout 없이 실행한다.
매 cycle에는 `polybot run` 하나만 두며 `uv sync`, `polybot config`, `polybot status`는 release
배포 검증 시에만 실행한다. 이를 통해 1분 signal cadence를 GitHub fetch 지연과 분리한다.

entry는 `[2026-08-26T18:30:00Z, 2026-09-02T18:30:00Z)`, follow-up은
`2026-09-09T18:30:00Z`까지다. cohort는
`config_hash × strategy_source_digest × mode × job_name`이며 Git commit은 provenance다.

v2g와 이전 DB는 immutable archive다. 마지막 v2f runtime
`watermelon-live-cat-98-1m-v2f`/`watermelon-live-dog-99-1m-v2f` 및 v2g를 v2h와 합치거나
재실행하지 않는다.

## 2026-08-27 execution-contract hotfix

Gamma의 `outcomes`, `outcomePrices`, `clobTokenIds`가 JSON string으로 오는 production shape를
catalog에 한 번만 encoding한다. 이미 v2h DB에 저장된 한 겹의 legacy double encoding은 읽기
호환하되, 다음 catalog upsert에서 canonical array로 교정한다.

fee/token identity나 signed amount처럼 **주문 POST 전에** 검증되는 계약 오류는 일반 주문 거절로
숨기지 않는다. run audit와 Jenkins build를 실패시키고, 실제 주문이 전송되지 않았음이 증명된
`PRE_SUBMISSION_CONTRACT_ERROR` 및 global `BLOCKED_GUARD` episode만 fresh in-band book에서
재시도한다. POST 가능성이 있는 오류와 명시적 FOK 결과는 이 경로로 재시도하지 않아 중복 주문을
막는다. 핫픽스 전 v2h의 `NOT_EXECUTED` episode는 실제 fill로 해석하지 않는다.

## 검증과 lifecycle

```bash
cd golden-watermelon-live
uv sync --frozen --extra dev
uv run pytest
uv build
```

실주문은 Jenkins에서 명시적 `--live`와 기존 credential이 모두 있을 때만 허용한다.
`POLYBOT_LIFECYCLE_MODE`는 `active`, `close_only`, `archive_only`를 지원한다. 중단·청산은
[공통 wind-down 절차](../docs/strategy-wind-down-playbook.md)를 따르며 clean build, workspace
wipe, 기존 DB 삭제를 사용하지 않는다.
