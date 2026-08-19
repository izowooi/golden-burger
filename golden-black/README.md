# Golden Black

스포츠 outcome의 exact `$5` ask가 `0.92` 또는 `0.94` 진입 band에 처음 들어오면 가상 매수하고,
CLOB이 unique winner를 표시할 때까지 보유하는 accountless paired research collector다.
고정 데이터 계약은 `sports-resolution-paired-v1`이다.

현재 코드는 의도적으로 **simulation-only**다. `--live`와 모든 Polymarket/CLOB credential을
source-level로 거절하며 주문 코드는 없다. DB `trades_sim.db`의 displayed-book episode를 실제
fill이나 realized P&L로 해석하면 안 된다.

## 왜 0.92와 0.94인가

- `0.94`: Pomegranate에서 사후 안정성이 가장 좋았고, 별도 Nectarine/Honeydew 아카이브에서도
  기간 전·후반 point estimate가 양수였다.
- `0.92`: Pomegranate와 Nectarine의 시간 전·후반이 모두 양수였던 더 넓은 표본의 대조군이다.
- `0.95~0.97`: Pomegranate validation에서 비용 후 음수였으므로 선택하지 않았다.

이 근거는 후보 선정일 뿐 수익 보장이 아니다. 과거 자료의 CLOB depth, resolution coverage,
clock 정확도가 불완전해 30일 prospective 검증이 필요하다.

## 효율적인 market discovery

전체 `/sampling-markets`를 333페이지씩 스캔하지 않는다. Gamma `/events/keyset`에 sports,
liquidity `10,000`, cumulative volume `5,000`, endDate 6시간 filter를 서버에서 적용한 뒤 nested
market을 재검증한다. page size는 500이고 안전 상한은 4페이지다. 상한에서 cursor가 끝나지
않으면 일부 universe를 조용히 사용하지 않고 cycle 전체를 실패시킨다.

## 로컬 검증

```bash
cd golden-black
uv sync --frozen --extra dev
uv run pytest
uv run polybot config --simulate --job black-shadow-paired
```

실제 public-data cycle은 credential이 없는 환경에서만 실행한다.

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot run --simulate --job black-shadow-paired
```

운영·Jenkins·daily-rsync 절차는 [OPERATIONS.md](OPERATIONS.md), 가설과 판정 gate는
[STRATEGY.md](STRATEGY.md)를 따른다.
