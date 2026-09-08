# 체결 판정과 보고 필드

상위 계약은 repo `docs/retro/EVIDENCE_CONTRACT.md`다. 이 문서는 보고 helper의 입력/출력 경계만 설명한다.

## 실제 원장

- `order_submissions.simulation=0` 및 exact submission/order/token/side 연결을 요구한다. `accepted`, `live`, GTC order ID, `trades.status=COMPLETED`, `trades.realized_pnl` 자체는 fill이 아니다.
- `order_fills.status=CONFIRMED`의 실제 size×price를 합산한다. match time과 증거 snapshot 시각을 구별한다. 보고 기간은 경제적 체결 시각에 적용하며, 후속 확인은 snapshot 기준의 사후 대사라고 표시한다.
- 주문의 terminal 상태·confirmed sum↔latest_size_matched·대사 완료·domain error 부재를 확인한다. 부분 체결은 실제 수량만 포함한다. unmatched/unresolved intent는 0체결로 단정하지 않는다.
- fee amount가 유효하면 사용한다. 명시적 fee_rate_bps=0은 증명된 0이다. fee amount/rate가 모두 없는 MAKER는 해당 wrapper의 no-builder-fee 계약이 별도 증명된 경우에만 0으로 인정한다. 그 외 fee 미확인은 순손익을 unknown으로 둔다.
- 동일 token의 여러 BUY가 있으면 exact linkage 또는 겹치지 않는 bot-owned position 구간을 검증한다. 모호한 SELL을 임의로 가장 가까운 BUY에 붙이지 않는다. 수동 wallet 거래는 편입하지 않는다.
- BUY보다 SELL이 많거나 timestamp/side/token/수량이 모순되면 확정 합계에서 제외하고 이유를 적는다. SDK dust를 0원 처분했다고 가정하지 않는다. sold quantity에 비례한 BUY 원가·fee만 매도손익에 배분한다.

## 정산

`resolution_value=0/0.5/1`이나 설명 문자열만으로 충분하지 않다. exact condition/token과 raw evidence의 token→outcome/payout 연결, one-hot 또는 authoritative void, receipt 시각을 대조한다. source 정책에 따른 검증된 resolution record는 지급권 가치이며 실제 cash redemption 증거와 다르다. 원본을 찾을 수 없으면 `RESOLUTION_UNVERIFIED`로 남긴다.

## 기간과 누락

- `entry_in_window`, `carry_in`, `exit_in_window`, `carry_out`을 별도 필드로 보존한다.
- 최근 기간에 체결되지 않았더라도 공식 경기 또는 사용자 목록의 경기 행을 남긴다. `NO_TRADE_RECORDED`와 `NOT_DISCOVERED`, `ENTRY_GUARD_BLOCKED`, `ORDER_REJECTED`, `UNRESOLVED_ORDER`, `MISSING_FEE`, `UNSUPPORTED_SCHEMA`를 구별한다.
- 기본 공식 결과 JSON은 `games` 배열이다. 각 행은 `sport`, `league`, `official_id`, `title`, `scheduled_at`, `status`, `home`, `away`, `home_score`, `away_score`, `result_scope`, `source_url`, `verified_at`을 가진다. Polymarket event ID 연결은 별도 `venue_event_ids`로 증거를 남긴다. 팀 배열 순서만으로 home/away를 추정하지 않는다.

## 산출물

`report.json`은 window·source 검증·source별 run/config·경기별 positions/orders·공식 결과·미확정 이유·전략별 합계를 담는다. 단순 경제 계산은 `tools/sports_trade_report.py`의 pure 함수로 재사용할 수 있다. `report.md`는 종목×경기×strategy/Jenkins/runtime 순으로 읽을 수 있어야 한다. 원본 private order/account 식별자, credential, raw console은 공유 문서나 commit에 넣지 않는다.
