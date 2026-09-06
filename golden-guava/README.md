# Golden Guava — 스포츠 가격 차이 연구

기존 스포츠 전략 자료에서 새 수익 가설을 탐색하고 직접 호가·경기자료로 검증하는 독립 프로젝트다.
가설과 반증 기준은 [STRATEGY.md](STRATEGY.md), 수집 계약은
[사전등록](research/2026-09-06-guava-v1/PREREGISTRATION.md)을 따른다.

현재 연구 수집기와 실행 어댑터를 구현 중이다. **Lion/Wolf live 배포 완료나 수익성 입증 상태가 아니다.**
`run --live`는 선정 규칙·포지션 관리 통합이 끝나기 전 명시적으로 거부한다.

## 로컬 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv build
uv run polybot config --simulate --job guava-research-a-v1
```

실수집은 승인된 T7 Jenkins workspace에서만 실행한다. 로컬 테스트는 가짜 HTTP/SQLite
fixtures로 수행하며 실제 주문을 제출하지 않는다. 라이브 SDK는 명시적 live 경로에서만 로드한다.

| Jenkins | runtime | 역할 |
|---|---|---|
| polybot-sim-guava-a | guava-research-a-v1 | event hash shard 0/4 |
| polybot-sim-guava-b | guava-research-b-v1 | event hash shard 1/4 |
| polybot-sim-guava-c | guava-research-c-v1 | event hash shard 2/4 |
| polybot-sim-guava-d | guava-research-d-v1 | event hash shard 3/4 |
| polybot-lion | guava-live-lion-a-v1 | 새 실거래 A, 아직 미배포 |
| polybot-wolf | guava-live-wolf-b-v1 | 새 실거래 B, 아직 미배포 |

폴더·Jenkins·runtime·지갑을 동일시하지 않는다. 과거 Kiwi/NHL DB와 합치지 않는다.
연구 DB는 각 `data/<runtime>/trades_sim.db`, 실거래 DB는 별도 runtime의 `trades.db`다.

## 수집과 해석

- 진행 경기의 직접 6/2호가를 수집하고, 관측한 경기의 종료 후 자료도 추적한다.
- 4개 shard는 겹치지 않은 경기를 담당한다. 같은 경기의 여러 정책을 독립 표본으로 더하지 않는다.
- raw 원문·수신 시각·실패/부재·수수료 정보·$5~$100 표시 깊이를 보존한다.
- 공개 market stream은 짧은 관찰 창이다. 전체 trade tape나 정확한 maker queue를 보장하지 않는다.
- 경기자료의 결과 확인은 Polymarket oracle 정산이나 현금 상환과 다르다.
- 실제 수익은 CONFIRMED fill·수수료·잔여 수량의 대사 이후에만 계산한다.

[OPERATIONS.md](OPERATIONS.md)에 배포와 daily-rsync 확인 절차가 있다.
