# L3 AGENTS.md — golden-papaya

`golden-papaya`는 Polymarket **Final Five** 전략 봇이다. 표준 이진 시장의 YES가 archive
cadence에서 처음 관측된
0.95를 상향 돌파할 때 0.95–0.97에서 진입하고, 해결까지 보유하되 YES가 0.90 이하로
내려오면 손절한다.

- 상위 규칙: L2 `/Users/izowooi/git/t1/AGENTS.md`, L1 `/Users/izowooi/git/AGENTS.md`
- 전략 계약: `STRATEGY.md`
- 실행·환경변수: `README.md`, `config.yaml`, `.env.example`
- 회고 계약: `../docs/retro/EVIDENCE_CONTRACT.md`, `../docs/retro/golden-papaya.md`

## 실행과 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv run polybot config
uv run polybot run --simulate --job sim
```

시뮬레이션도 CLOB 인증이 필요하다. 실제 주문 전에는 `config.yaml`의 보수적 simulation
기본값과 별도 `job_name`/DB를 유지한다.

## 변경 불가 전략 계약

- universe는 outcomes가 정확히 `Yes`/`No`인 2-token 표준 이진 시장이다. neg-risk 및
  다중 outcome/event 파생 시장을 제외한다.
- 방향은 YES-only다. CLI나 env로 NO 진입을 허용하지 않는다.
- 유효한 직전 관측값이 0.95 미만이고 현재 YES가 0.95 이상이 된 **first observed upward
  crossing**만 인정한다. 현재 snapshot은 현재 sweep에서 commit된 양의 ID여야 하고 직전
  persisted snapshot과의 간격은 기본 `0 < gap <= 30분`이어야 한다. sweep/run gap이 있으면
  실제 교차 시점은 interval-censored다. 현재 진입가는 `[0.95, 0.97]`, 잔여시간은
  `[2h, 72h]`다.
- 최초 threshold snapshot은 유동성·volume·window·event cap·fresh ask 같은 후속 gate에서
  주문이 거부돼도 one-shot을 소진한다. 보존 이력에 0.95 이상 관측이 하나라도 있으면 이후
  dip/re-cross를 새 후보로 만들지 않는다.
- 기본 주문은 $5, 동시 포지션 20, event당 1개다. 기본 유동성은 $1,000,
  24h 거래량 하한은 $0이다.
- 사전 해결 익절·trailing·time exit은 없다. 미해결 상태에서 YES midpoint가 0.90
  이하일 때만 절대 가격 손절을 시도한다.
- resolution 결과와 redeem/지급 증거는 SELL fill과 분리한다. 해결됐다는 이유로 1.00
  매도나 실현 P&L을 추정하지 않는다.
- papaya 자체 archive는 `YES >= 0.80`, 잔여 `<= 168h`, 유동성 `>= $1,000`, volume `>= $0`
  envelope를 60일 보존한다. entry filter를 높여도 archive baseline은 높이지 않는다.

## 코드 위치

- `src/polybot/strategy/signals.py`: 순수 진입·청산 판정
- `src/polybot/strategy/scanner.py`: strict binary 필터, first-observed-crossing 판정, archive 후보
- `src/polybot/strategy/trader.py`: 주문 및 confirmed-fill 연계
- `src/polybot/db/`: trade/snapshot/catalog/evidence 저장
- `src/polybot/bot.py`: lifecycle과 사이클 phase

전략 수치나 비교 경계를 바꾸면 `config.py`, tests, `STRATEGY.md`, retro grid를 함께
갱신한다. 실제 성과는 `order_fills.status='CONFIRMED'`의 size/price/fee와 별도
resolution/redeem evidence로만 평가한다.

## 보안과 운영

- private key, funder address 실값, API credential, `.env`, `data/`를 커밋하지 않는다.
- Jenkins secret은 Credentials Binding으로 주입하고 참조 전부터 `set +x`를 적용한다.
- `active`/`close_only`/`archive_only` lifecycle을 사용한다. `close_only`는 신규 BUY만
  막으며, papaya 포지션은 0.90 손절 또는 resolution/redeem 분류까지 남을 수 있다.
- 다른 `golden-*` 폴더는 read-only로 취급한다.

`CLAUDE.md`는 `@AGENTS.md` 한 줄만 유지한다.
