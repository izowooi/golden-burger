# 운영자가 처리한 것으로 인수하는 과거 주문

사용자의2026-09-06명시적지시에따른운영상예외다. 거래소의체결/미체결확인과구분한다.

- 정확히선택한기존미확인BUY에만`OPERATOR_HANDLED_ASSUMPTION`을별도추가기록한다.
- 원래`order_submissions`의UNKNOWN,order_id,수량,성공여부와모든fill/P&L은변경하지않는다.
- 기존관리trade나fill이있는token은이경로로정리할수없다.
- 원본submission의해시와승인ID/이유/시각을보존한다. 이기록은UPDATE/DELETE불가다.
- 승인한submission만봇의노출예약에서제외한다. 해당token은앞으로BUY/SELL모두금지한다.
  다른새로운미확인주문은여전히신규매수를차단한다.
- 원본이변경되거나새venue fill이발견되면다시중단한다. 운영자확인은과거수익성검증을
  통과시킨다는뜻이아니며,일반엄격검사의과거venue evidence gap은남을수있다.
- `operator_handled_unknown_buy_count`와경고로그로노출제외근거를따로표시한다.

## 실행

Jenkins예약을멈추고실행중인writer가끝난뒤동일DB잠금을잡아서처리한다.
다음도구는API를호출하지않고실제주문도제출하지않는다.

```bash
uv run python scripts/acknowledge_handled_intents.py \
  --db data/default/trades.db --submission-id <exact-id> \
  --approval-id <operator-approval-reference> --reason <explicit-reason>
```

출력한정확한set hash를확인한뒤동일한인자에다음을추가한다.

```text
--apply --expected-set-sha256 <dry-run-hash> --backup-dir <durable-outside-data-directory>
```

DB online backup과SHA manifest를만들고한transaction으로등록한다. 같은승인의재실행은
중복기록을만들지않는다. dry-run은DB를변경하지않지만동시실행방지잠금파일을사용한다.
처리뒤Jenkins원래명령을복원하고DB/로그에서진입허용,보호token,과거손익보존을검증한다.

Yellow의승인된누적손실기준은Jenkins의
`POLYBOT_ENTRY_DRAWDOWN_FLOOR_USDC=-200`이다. 기존경제손익을0으로초기화하거나
추가$200예산으로해석하지않는다. 기본설정-$30은다른사용환경을위해유지한다.
