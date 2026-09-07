# Cat/Dog 0.99 익절 — 로컬 구현 완료, 운영 미배포

사용자가 제안한 `.95` 대 `.97` 진입을 비교하는 새 정책이다. 기존 실험에서 최적화됐다는
주장이 아니다. `.99`로 진입해 `.99`로 나가는 왕복은 비용 후 이익이 없으므로 새 진입 상한은
`.989`다. 실제 tick이 `.01`인 book에서는 체결 가능한 최고 진입 limit이 `.98`이 된다.

|항목|Cat 후보 A|Dog 후보 B|
|---|---|---|
|exact $5 ask VWAP 진입|`[0.95,0.989]`|`[0.97,0.989]`|
|signed BUY limit|`<=0.989`|`<=0.989`|
|익절|전량 표시 bid의 worst limit `>=0.99` + 양의 예상 순익|동일|
|손절|기존 `max(0.70, confirmed BUY VWAP-0.30)`|동일|
|금액·노출·SELL 예산|$5, 기존 account/event/cycle 한도, stop+TP 합쳐 cycle 1건|동일|

`take_profit.enabled`의 기본은 `false`이고 기존 `config.yaml`, runtime registry, Jenkins
설정은 그대로다. 활성화된 정책은 `take_profit.effective_from_utc`를 명시해야 하며 기존
band와 새 flag가 섞이면 DB를 만들기 전에 거절한다. `profile_environment`는 flag가 명시된
경우에만 Cat/Dog의 soccer/MLB/NFL 자식 실행에 새 band를 원자적으로 전달한다. 이 변경은
NFL의 배포·가동 또는 기존 entry/follow-up 종료일의 연장을 수행하지 않는다.
enabled 상태의 미래 effective-from은 startup에서 거절하여 새 진입과 TP가 서로 다른 시각에
시행되지 않게 한다. 미래 예약은 flag를 켜 놓는 방식으로 구현하지 않는다.

실제 설정 키는 다음과 같다. 이 표는 변경 계약이며 운영 적용을 기록한 표가 아니다.

|설정|새 release의 값|
|---|---|
|`POLYBOT_TAKE_PROFIT_ENABLED`|`true`|
|`POLYBOT_TAKE_PROFIT_EFFECTIVE_FROM_UTC`|운영 변경의 실제 UTC 시각, 필수|
|`POLYBOT_ENTRY_PROB_MIN`|Cat `0.95`, Dog `0.97`|
|`POLYBOT_ENTRY_PROB_MAX`|`0.989`|
|`POLYBOT_TAKE_PROFIT_INCLUDE_EXISTING`|코드 기본 `false`; 사용자가 요청한 전환 범위는 `true`|

기존 보유는 BUY 시각이 effective-from보다 이르면 기본적으로 새 TP 대상에서 제외한다.
`include_existing_holdings=true`로 지정하면 이미 DB가 관리하는 confirmed BUY holding만
별도의 carry-in 집단으로 새 TP를 적용할 수 있다. 수동 지갑 position은 편입하지 않고,
과거 entry/config/fill은 재작성하지 않는다. effective-from 이전에는 carry-in flag가 있어도
새 TP를 제출하지 않는다. 미확정 BUY/SELL은 기존 대사를 끝내기 전 새 TP 대상으로 만들지 않는다.
사용자의 “Cat/Dog는 더 이상 경기 종료까지 단순 보유하지 않는다”는 요청은 기존 봇 소유
confirmed HOLDING도 이 carry-in 전환에 포함하는 범위다. `false`는 현재 비활성 코드의
보수적 기본값이며 추가 승인이 없다는 이유로 기존 보유를 영구 제외하라는 정책이 아니다.
과거 `.99` BUY는 `.99` SELL로 수수료 후 이익이 나지 않으면 TP 조건을 통과하지 않는다.

`Trader.execute_sell`은 기존 stop 또는 새 TP를 판단한 뒤 같은 FOK 제출·PENDING_SELL·정확한
confirmed fill 대사 경로를 사용한다. TP는 다음 정보를 모두 요구한다.

1. exact terminal BUY 수량·VWAP·fee와 DB 보유 수량 일치.
2. 기존 독립 Gamma+CLOB의 OPEN proof.
3. proof 뒤 다시 읽은 전량 SELL book, signed 0.01주 내림 뒤 잔여 `<0.01` 보존.
4. full-depth VWAP와 최악 signed limit 모두 `.99` 이상. ask가 존재하면 유한한 non-crossed
   spread `<=0.10`을 요구한다. CLOB parser가 정상 빈 목록 `asks=[]`로 검증한 bid-only
   book은 허용한다. SELL의 실행 근거는 bid 수량과 signed 최소 가격이므로 반대편 ask가
   비었다는 이유만으로 전량 매도를 막지 않는다. 누락·형식 오류·NaN asks는 빈 목록과
   구분해 차단한다. 이는 21개 과거 수익 사례로 고른 임계값이 아니라 SELL의 기계적 실행
   조건을 반영한 것이며, 손절의 기존 spread 필수 조건은 그대로다.
5. 새 authoritative dynamic fee schedule quote와 $0.001 rounding reserve를 뺀 전체 보유분
   순익 하한이 엄격히 양수. `signed SELL limit × sellable 수량 − confirmed BUY VWAP ×
   전체 confirmed BUY 수량 − 전체 confirmed BUY fee − SELL fee quote − reserve`로 계산한다.
   즉 SDK dust 잔여의 지급액을 0으로 보아도 전체 매수 원가를 회수해야 한다. book 요청
   시작부터 이 계산까지 3초 초과면 재사용하지 않고 보류.

이 금액은 주문 전 추정치다. FOK 응답·`orderID`·accepted는 체결이 아니며, 실제 수수료가
확정되기 전 이익을 기록하지 않는다. 접수 후 결과 불명도 PENDING/격리와 capacity를 유지한다.
venue delay, depth 소멸, 실제 fill 별 fee rounding은 추정치와 다를 수 있다. TP confirmed
청산을 stop 청산으로 기록하지 않으므로 TP 뒤 반대 결과 재진입 권한도 생기지 않는다.
실제 체결 뒤의 ledger 손익은 계속 매도된 수량에 비례 배분한 원가·BUY fee로 계산하며,
미매도 dust를 매도·소각·0원 확정한 것으로 기록하지 않는다. 위 0원 가정은 제출 전 하한에만 쓴다.
전체 holding의 stop을 먼저 검사하고 TP는 두 번째 단계에서 실행하여 같은 cycle의 긴급
손절이 익절 때문에 밀리지 않게 한다. TP의 연속 실패 시각은 따로 보존하며 180분 격리·
ledger 실패 뒤 늦은 confirmed SELL이 도착해도 TP라는 원인을 유지한다.
새 정책을 되돌릴 때는 flag와 기존 `.96/.99` entry band를 함께 복원하되, TP 원인과 격리를
읽을 수 있는 이 코드에서 기존 PENDING/QUARANTINED SELL 대사를 계속한다. 과거 원장을
지우거나 TP 상태를 모르는 이전 binary로 교체해서 미확정 주문을 숨기지 않는다.

회고에서는 cohort `config_hash × strategy_source_digest × mode × job_name`을 새로 구분하고,
동일 event를 A/B 두 독립 경기로 세지 않는다. 동일 entry에서 hold/TP만 바꾸는 paired
반사실과 `.95/.97` 진입 변경 효과를 따로 보고한다. gap을 성공 매도·0손익으로 채우지 않는다.

재생 도구: `tools/catdog_takeprofit_replay.py`. 수집 DB/manifest checksum을 고정하고 90초 초과
공백, failed run, token·cohort 누락을 검열한다. 표시호가·가정 fee 민감도이며 actual fill이
아니다. 수수료 후 완결 paired 표본과 별도의 다음 경기 검증 없이 임계값 최적화를 주장하지 않는다.

검증: `uv sync --frozen --extra dev`, `uv run pytest`, `uv build`.
`tests/test_take_profit_release.py`는 주문 client를 mock으로 대체해 기본 OFF, 새 설정 원자성,
양의 순익, fee·depth·시각 누락 차단, carry-in, SDK dust, accepted/unknown/confirmed 구분을
실제 trader 경로에서 검증한다. 이 문서와 테스트 실행은 실주문을 제출하지 않는다.
