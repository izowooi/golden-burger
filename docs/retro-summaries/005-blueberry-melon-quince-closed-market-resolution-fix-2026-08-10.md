# 005 — Blueberry·Melon·Quince closed-market resolution fix — 2026-08-10

작성일: 2026-08-10

대상: `golden-blueberry`, `golden-melon`, `golden-quince`

선행 기록: [004 — close-only verification](004-blueberry-melon-quince-close-only-verification-2026-08-10.md)

## 0. 결론

환경변수 추가가 아니라 세 전략의 소스 수정이 필요했고, 모두 수정했다.

Gamma의 기본 condition-ID 목록 조회가 closed market을 생략해도, 이제 같은 condition ID에
`closed=true`를 붙여 한 번 더 조회한다. 두 조회 모두 요청한 condition ID와 정확히 일치하는
market만 채택하며, 두 번째 응답은 `closed is True`인 경우에만 반환한다.

최종 payout 증거 기준은 완화하지 않았다. 기존 `get_proven_resolution()`이 계속
`closed=true`, strict `[Yes, No]`, `negRisk=false`, outcome price가 정확히 `[1,0]` 또는
`[0,1]`인 경우에만 position을 `RESOLVED`로 전환한다.

## 1. 원인과 수정

기존 조회는 다음 요청 한 번만 보냈다.

```text
/markets?condition_ids=<condition-id>&limit=1
```

Gamma는 closed market에 이 요청을 빈 배열로 반환할 수 있다. CLOB midpoint도 이미 사라진
상태이므로 bot은 resolution 증거를 얻지 못하고 `HOLDING`을 계속 유지했다.

수정된 순서는 다음과 같다.

1. 기존 condition-ID 조회를 먼저 실행한다.
2. 정확히 같은 condition ID의 market이 있으면 기존처럼 반환한다.
3. 없으면 `closed=true`를 추가해 재조회한다.
4. 재조회 응답도 condition ID가 일치하고 `closed is True`일 때만 반환한다.
5. 잘못된 ID, 열린 market, list가 아닌 응답, 빈 ID는 fail closed한다.

변경 파일:

- `golden-blueberry/src/polybot/api/gamma_client.py`
- `golden-melon/src/polybot/api/gamma_client.py`
- `golden-quince/src/polybot/api/gamma_client.py`
- 각 프로젝트의 `tests/test_api_contracts.py`

전략 threshold, 주문액, lifecycle 환경변수, Jenkins 설정은 바꾸지 않았다.

## 2. 검증 결과

| 검증 | Blueberry | Melon | Quince |
|---|---:|---:|---:|
| 핵심 API·Trader 회귀 테스트 | 112 passed | 111 passed | 111 passed |
| 전체 테스트 | 348 passed | 329 passed | 329 passed |
| `uv build` | 성공 | 성공 | 성공 |

추가 검증:

- 세 프로젝트 모두 `uv sync --frozen --extra dev` 성공
- 루트 `uv run tools/verify_strategy_contracts.py`: `PASS (19 strategies)`
- public Gamma market `3202424` 실 API smoke test:
  - 세 클라이언트 모두 요청 condition ID와 일치하는 market 발견
  - `closed=True`
  - parsed `outcomePrices=['1', '0']`

실 API 확인은 읽기 전용이며 Jenkins build나 주문을 실행하지 않았다.

## 3. 운영 후속 절차

코드 수정과 로컬 검증은 끝났지만, Jenkins 운영 DB의 18개 `HOLDING`이 전환됐다는 뜻은
아직 아니다. 배포 후 확인이 남아 있다.

1. Jenkins 8개 job이 이 수정 commit을 checkout하게 한다.
2. timer는 계속 끄고 `POLYBOT_LIFECYCLE_MODE=close_only`를 유지한다.
3. Blueberry 2개, Melon 3개, Quince 3개 job을 각각 한 번 수동 실행한다.
4. DB와 로그를 `daily-rsync`로 다시 동기화하고 verify한다.
5. 수정 전 기준 `HOLDING=18`이 `RESOLVED=18`로 전환됐는지 확인한다.
6. 신규 BUY가 0인지, resolution 근거가 closed final payout인지 확인한 뒤에만
   `active`와 timer 복귀를 결정한다.

이 변경은 lifecycle source lineage를 바꾼다. 이후 active 실험은 Blueberry의
`strategy_source_digest`, Melon·Quince의 `git_commit` 기준으로 새 cohort가 되므로 수정 전후
성과를 하나의 cohort로 합치지 않는다. close-only 배포 run도 새 lineage를 RunAudit에 남긴다.

이 전환은 resolution 관측을 기록하는 것이며 실제 redeem transaction을 증명하지 않는다.
따라서 `RESOLVED` 전환 후에도 settlement assumption을 realized/net P&L이나 실제 회수 금액으로
표현하지 않는다.
