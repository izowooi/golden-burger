# Golden Watermelon Live

동일한 실행·대사·안전 로직으로 Soccer, MLB, NHL의 경기 중 승자 시장을 baseline `$5`로
검정하는 live A/B 프로젝트다. 현재 Jenkins 주문 목표도 계속 `$5`다. 나중에 운영자가 목표
금액을 올리면 같은 fresh book에서 전량 체결 가능한 가장 큰 사다리 금액으로 자동 축소한다.
종목은 Jenkins의 `POLYBOT_SPORT_FAMILY`로 고르며 별도 전략 fork를 만들지 않는다. 기존 wallet
credential과 수동 position은 건드리지 않는다.

| family | arm A | arm B |
|---|---|---|
| Soccer | `polybot-cat` / `watermelon-live-cat-96-1m-v2h` / `[0.96,0.999]` | `polybot-dog` / `watermelon-live-dog-99-1m-v2h` / `[0.99,0.999]` |
| MLB | `polybot-bear` / `watermelon-live-bear-mlb-96-1m-v3a` / `[0.96,0.999]` | `polybot-tiger` / `watermelon-live-tiger-mlb-99-1m-v3a` / `[0.99,0.999]` |
| NHL | `polybot-lion` / `watermelon-live-lion-nhl-96-1m-v3a` / `[0.96,0.999]` | `polybot-wolf` / `watermelon-live-wolf-nhl-99-1m-v3a` / `[0.99,0.999]` |

두 arm의 유일한 family 내 treatment는 진입 하한이다. cadence는 모두 1분, 현재 주문은 `$5`다.
0.96/0.99는 아직 최적값이 아니라 큰 손실 꼬리와 기회 수를 향후 수집 자료로 비교하는 값이다.

## 거래 계약

- Soccer: EPL, Bundesliga, Ligue 1, LaLiga, MLS, Serie A, UEFA Champions League(UCL),
  UEFA Europa League(UEL) exact identity와 정규시간 HOME/DRAW/AWAY YES. in-play `[0h,4h]`.
- MLB: numeric tag `100381`, sport `8`, root series `3`의 exact two-team whole-game direct
  moneyline. in-play `[0h,8h]`.
- NHL: numeric tag `899`, sport `35`, root series `10346`의 exact two-team whole-game direct
  moneyline. in-play `[0h,5h]`.
- World Series와 Stanley Cup Final은 같은 exact major-league identity일 때 포함한다.
- e-sports, MiLB/AHL/ECHL/NCAA, child/period/spread/total/prop/future/advancement는 주문 전
  제외한다.
- Gamma는 `liquidity_min=5000`, `volume_min=5000`으로 서버에서 먼저 축소하고 최대 4페이지
  cursor를 완결한다. 실제 실행 가능성은 fresh exact `$5` full CLOB depth로 다시 검증한다.

BUY는 marketable FOK만 사용한다. 신호는 항상 exact `$5` ask VWAP으로 비교한다. 목표 금액이
더 크면 같은 fresh book에서 `$5`부터 `$1,000`까지의 사다리 중 가격 상한 안에서 전량 체결 가능한
가장 큰 금액 하나를 선택한다. 선택된 주문 자체는 FOK이므로 전량 체결 아니면 0체결이다. 주문
accepted는 fill이 아니며 terminal fill과 dynamic fee를 확인하기 전에는 position lifecycle을
확정하지 않는다.

v3e effective stop은 `0.70`이다(`max(0.70, confirmed entry VWAP-0.30)`). White 재생과 최근 live
사례에서 0.94/0.95의 촘촘한 손절이 최종 승자를 손실로 끝낸 증거를 반영했다. 0.70도 수익 최적값을
뜻하지 않고 재난 방어선으로만 남긴다. current Gamma event와 CLOB condition의 독립 OPEN proof,
그 proof 뒤 fresh complete bid book, spread `<=0.10`을 요구한다. OPEN 상태에서 가격이 stop을 한
번에 건너뛴 gap도 손절 자체를 막지 않으며 actual worst bid/VWAP/gap을 기록한다. 이미 종료된
market의 `0.001` cleanup bid는 OPEN proof에서 계속 차단한다.

한 cycle의 실행 후보는 어떤 주문보다 먼저 `QUEUED_NO_POST`로 기록한다. 로컬 정밀도 검사나
event capacity처럼 POST가 없었다고 증명된 경우에만 다음 fresh in-band snapshot에서 재시도한다.
POST 가능성이 있는 예외·응답은 execution ledger 대사 전 재시도하지 않는다. 기존 결과를 stop한
event의 다른 HOME/DRAW/AWAY 결과는 SELL confirmed 뒤 다음 cycle에 여전히 arm 안이면 딱 한 번
진입 가능하다. 같은 token 재매수와 세 번째 결과 진입은 720시간 동안 막는다. `DELAYED` FOK
BUY/SELL은 exact order·trade 부재와 취소 증거가 모두 맞을 때만 2분 뒤 0체결로 종결한다.

account/event/cycle 한도는 `20/1/5`, cycle emergency SELL은 1건이다. 결과 불명 BUY, orphan BUY,
BUY 대사 오류는 한 capacity와 같은 token/event를 격리하되 다른 경기의 실행은 계속한다. 방향을
알 수 없는 대사 오류, open BUY fill/fee 누락, 일반 QUARANTINED 또는 경제손익 증거 누락은 신규
BUY를 전역 차단한다. SELL intent·대사 실패도 같은 token/event만 격리한다. 연속 손절
실패가 180분을 넘으면 성공 매도나 0체결로 꾸미지 않고 `QUARANTINED`로 자동 격리 종결하며,
실제 노출 가능성이 있으므로 account/event capacity는 계속 소비한다. confirmed SELL + proven
resolution 경제손익이 `-$10`이면 기존 position 관리는 계속하지만 신규 BUY를 중단한다.

catalog/snapshot/trade에는 종목·리그·원본 tag를 저장한다. trade에는 목표 주문액, 실제 선택액,
가격 상한 안의 표시 호가 최대 금액과 축소 사유를 함께 저장하므로 이후 종목별 체결 건수·손절값·
안전 주문 단위를 따로 분석할 수 있다. 이 계산은 기존 fresh CLOB 응답만 사용해 요청 수를 늘리지
않는다.

## 1분 cadence

경과시간 때문에 Gamma/CLOB 요청이나 주문을 중단하지 않는다. 각 HTTP 요청에 finite socket
timeout을 적용하고 cycle 50초 초과는 경고 evidence로 남긴다. DB별 nonblocking run lock이 다음
분 trigger와 겹치면 새 process가 즉시 skip하므로 동일 DB 쓰기와 중복 주문을 방지한다.
정상 cycle 반환 뒤에는 Gamma keep-alive, CLOB SDK의 process-global HTTP/2 pool, SQLite engine을
명시적으로 닫아 성공한 Jenkins shell이 열린 연결 때문에 남지 않게 한다.

Jenkins 배포 build만 Git SCM으로 exact commit을 checkout하고, 검증 뒤 정기 build는 그 workspace를
고정한 `NullSCM`으로 실행한다. 정기 shell은 lockfile hash가 변했을 때만 `uv sync --frozen`하며,
평소에는 `./.venv/bin/polybot run --live --job ...` 하나만 실행한다. `config`, `status`와 source
검증은 timer를 끈 배포 build에서 수행한다.

entry는 `[2026-08-29T04:00:00Z,2026-09-05T04:00:00Z)`, follow-up은
`2026-09-12T04:00:00Z`까지다. cohort는
`config_hash × strategy_source_digest × mode × job_name`이고 Git commit은 provenance다.
v3e 변경 계약은
`research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/PREREGISTRATION.md`에 고정한다.

Cat/Dog v2h DB는 기존 미해결 position을 관리하기 위해 이어 쓴다. 그보다 오래된 live DB와 신규
MLB/NHL DB는 immutable epoch로 분리하며 clean, merge, migration, backfill하지 않는다.

## 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
```

실주문은 Jenkins에서 명시적 `--live`와 기존 credential이 모두 있을 때만 허용한다.
`POLYBOT_LIFECYCLE_MODE`는 `active`, `close_only`, `archive_only`를 지원한다. 중단·청산은
[공통 wind-down 절차](../docs/strategy-wind-down-playbook.md)를 따른다.
