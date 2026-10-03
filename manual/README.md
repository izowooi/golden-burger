# 수동 AI 베팅 (트랙 2)

연구자가 직접(수동으로) 넣은 Polymarket 베팅을 시스템이 **키 없이** 자동 기록한다.

- 등록: Mac mini `~/.polylab/watch.env` 에 공개 지갑 주소만 적는다(개인키 불필요, 저장소에 절대 커밋하지 않음).
  ```
  WATCH_WOLF__ADDRESS=0x...            # 필수: Polymarket 지갑(프록시) 주소
  WATCH_WOLF__LABEL=트랙2-A            # 선택: 리포트·대시보드에 보일 이름 (주소는 어디에도 표시되지 않음)
  WATCH_WOLF__SINCE=2026-09-28         # 선택: 이 날짜(UTC) 이후 거래만 집계 (없으면 전체 이력)
  WATCH_WOLF__BANKROLL_USDC=1000       # 선택: 누적 실현손익이 이 금액의 −10% 아래면 attention 경고
  ```
- 수집: Jenkins `polylab-manual-sync`(15분마다) = `uv run polylab manual sync`. 체결·redeem·정산을 읽어
  경기·마켓(예: Over 0.5)과 연결하고 실현/미실현 손익, 승패, 진입 시점의 시장 P(0:0)를 계산한다.
- 결과: 일일 리포트 "수동 AI 베팅 (트랙 2)" 섹션, 대시보드 `latest/manual.json`, 주간 리포트의 AI 예측 vs 결과 표.

## AI 예측 기록 (선택)

`manual/predictions/YYYY-MM-DD.md` 파일을 GitHub 웹에서 만들거나 수정하면 된다(날짜 = 예측한 날, KST).
아래 표 하나면 충분하고, Claude/ChatGPT 스킬이 이 표를 그대로 출력하게 해도 된다.

```
| 경기 | 엔진 | P(0:0) | 순위 | 메모 |
|---|---|---|---|---|
| Greece vs Netherlands | claude | 7% | 1 | 양 팀 공격력 상위 |
| Korea Republic vs Uruguay | chatgpt | 0.09 | 2 | |
```

- 경기: Polymarket 표기와 같은 영문 팀 이름 `홈 vs 원정` 권장(한글 이름은 거래와 연결되지 않을 수 있음).
- 엔진: `claude` 또는 `chatgpt`. P(0:0): `7%`, `0.07`, `7` 모두 가능. 순위·메모는 비워도 된다.
- 같은 날짜 앞뒤(−1~+2일) 경기의 수동 거래와 팀 이름으로 자동 연결하며, 연결 못 한 행은 주간 리포트에 따로 표시된다.
