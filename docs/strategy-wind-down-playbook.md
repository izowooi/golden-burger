# 전략 퇴역과 계정 전환 플레이북

## 결론

`golden-honeydew`와 `golden-nectarine`은 즉시 일괄 매도하지 않는다. 두 Jenkins 잡을
먼저 `close_only`로 바꾸고 기존 GTC BUY 주문을 한 번 취소한 뒤, 같은 주기로 7일 동안
자연 청산을 반복한다. 7일 뒤에도 남은 포지션만 유동성과 스프레드를 확인해 단계적으로
정리한다.

두 봇은 중앙 시장 스냅샷도 저장하므로 포지션이 0이 되었다고 잡을 곧바로 끄지 않는다.
최소 하나는 `archive_only`로 유지하거나, 별도 아카이브 수집기로 책임을 이관한 뒤 중지한다.

## 수명주기 모드

| 모드 | 시장 스냅샷 | 기존 포지션 청산 | 신규 스캔/매수 | 용도 |
|---|---:|---:|---:|---|
| `active` | O | O | O | 기본 운영. 환경변수 미설정 시 항상 이 값이다. |
| `close_only` | O | O | X | 퇴역 유예기간. 자연 청산을 계속한다. |
| `archive_only` | O | X | X | 거래 증거가 모두 정리된 뒤 아카이브만 유지한다. |

`archive_only`에서도 사이클 시작 전 execution ledger 대사는 수행한다. 주문 생성 Phase만
건너뛴다. 유효하지 않은 모드는 설정 로드 단계에서 실패한다. `POLYBOT_BUY_AMOUNT=0`은
설정 검증에서 거부되므로 드레인 스위치로 사용하지 않는다.

## 7일 전환 절차

### T-0: 증거 고정과 사전 점검

1. 실행 중인 SQLite를 `cp`하지 말고 online backup과 SHA-256 manifest를 만든다.

   ```bash
   uv run --project polybot-observability polybot-retro backup \
     --root "$JENKINS_HOME/workspace" \
     --output-dir "$HOME/polybot-db-backup"
   ```

2. 최근 cohort를 strict audit하고 `CRITICAL`/`HIGH`, 미완료 주문, archive gap을 기록한다.

   ```bash
   uv run --project polybot-observability polybot-retro audit \
     --root "$JENKINS_HOME/workspace" \
     --days 30 \
     --output-dir "$HOME/polybot-retro/wind-down" \
     --strict
   ```

3. 각 계정의 포지션, redeemable, dust, no-book, 스프레드와 미체결 주문을 dry-run으로 확인한다.

   ```bash
   uv run tools/wind_down.py --env-file golden-honeydew/.env status
   uv run tools/wind_down.py --env-file golden-nectarine/.env status
   ```

   Jenkins Credentials Binding만 사용하는 운영 환경에서는 `.env`를 만들지 말고 동일한
   credential-bound shell 안에서 `status`를 실행한다.

### T0: 신규 진입 동결

1. 두 Jenkins 잡에 다음 한 줄만 추가한다.

   ```bash
   export POLYBOT_LIFECYCLE_MODE=close_only
   ```

2. `uv run python main.py config`의 `Lifecycle Mode: close_only`와 첫 실행 로그의
   `Phase 2/3 건너뜀`을 확인한다. 잡 주기는 기존 3~5분을 유지한다.
3. `close_only`는 이미 CLOB에 올라간 GTC BUY 주문을 자동 취소하지 않는다. 각 계정에서
   BUY 주문만 먼저 dry-run한 후 한 번 취소한다. SELL 주문까지 취소하려면 `ALL`을
   명시해야 한다.

   ```bash
   uv run tools/wind_down.py --env-file golden-honeydew/.env cancel
   uv run tools/wind_down.py --env-file golden-honeydew/.env cancel --side BUY --yes

   uv run tools/wind_down.py --env-file golden-nectarine/.env cancel
   uv run tools/wind_down.py --env-file golden-nectarine/.env cancel --side BUY --yes
   ```

### T+1일~T+7일: 자연 청산

- `golden-honeydew`의 정상 최대 보유시간은 24시간이다. 첫 48시간 동안 정상 청산,
  잔고/allowance 오류, `QUARANTINED`, redeemable 전환을 집중 확인한다.
- `golden-nectarine`의 정상 calendar exit는 120시간이다. 5일을 온전히 보장하고 네트워크
  오류·부분 체결 대사를 위한 2일 버퍼를 둔다.
- 매일 `wind_down.py status`를 실행해 CLOB 미체결 BUY=0인지 확인한다. DB `HOLDING` 수만
  보고 지갑 포지션이 있다고 가정하지 않는다.
- `not enough balance / allowance`, `SubmissionEvidenceError`, unresolved intent가 있으면
  강제 매도를 반복하지 말고 먼저 주문/체결 증거를 대사한다.
- 유예기간 중 신규 전략은 기존 eco/fox 계정을 덮어쓰지 않고 lion/tiger/wolf 같은 별도
  슬롯에서 시작한다. 기존 DB와 Jenkins job_name을 새 전략에 재사용하지 않는다.

## T+7일 잔여 포지션 처리

다음 순서로 처리하며 모든 mutation 전에는 dry-run 결과를 보관한다.

1. **redeemable**: 매도 주문을 만들지 않고 redeem 절차로 분리한다.
2. **정상 유동성, 스프레드 5센트 이하**: midpoint 지정가를 5분 간격으로 최대 6회
   재호가한다. 먼저 `--yes` 없이 계획과 예상 비용을 확인한다.

   ```bash
   uv run tools/wind_down.py --env-file golden-nectarine/.env \
     flatten --mode mid --rounds 6 --wait 300
   ```

   승인 후에만 같은 명령에 `--yes`를 붙인다.
3. **급한 잔여분**: 손실 한도가 허용되고 스프레드가 5센트 이하일 때만 `--mode bid`를
   사용한다.
4. **wide/no-book/dust**: sweep으로 억지 처분하지 않는다. 해결까지 보유 후 redeem하거나,
   별도 수동 검토 대상으로 남긴다.

`sweep`은 계정·보안·규제 비상처럼 즉시 노출 제거가 더 중요한 경우에만 쓴다. 일반적인
전략 교체에서 전량 시장가성 매도는 기본 선택이 아니다.

## `archive_only` 전환 게이트

아래 조건을 모두 만족한 뒤에만 Jenkins 값을 `archive_only`로 바꾼다.

- Data API 기준 live 포지션 0, 또는 남은 항목이 redeem/dust/no-book로 명시 분류됨
- CLOB open BUY/SELL 주문 0
- execution ledger의 pending/unknown intent와 reconciliation error 0
- DB의 `HOLDING`/`QUARANTINED`가 지갑·order/fill 증거와 대사됨
- 마지막 `close_only` run audit 성공, snapshot sweep 정상
- SQLite online backup, manifest 검증, daily report freshness 확인

중앙 snapshot 보존기간이 최소 60일이므로 전환 후 적어도 60일은 아카이브 책임을 유지한다.
두 잡을 모두 중지하려면 먼저 다른 job이 같은 시장 catalog/snapshot 계약과 5분 bucket
coverage를 제공하는지 strict audit로 증명한다.

## 새 전략 배치 원칙

가장 안전한 방식은 기존 계정을 재사용하지 않고 별도 계정/잡에서 새 전략을 시작하는 것이다.
이미 별도 슬롯이 있다면 다음 순서를 권장한다.

1. 새 전략은 simulation과 소액 live cohort를 별도 `job_name`/SQLite로 시작한다.
2. `pb_strategy_deployments`에는 실제 적용된 effective period만 기록하고, mutable account
   이름만으로 과거 성과를 새 전략에 귀속하지 않는다.
3. honeydew/nectarine 계정은 위 게이트를 통과할 때까지 old strategy identity를 유지한다.
4. 꼭 같은 계정을 재사용해야 한다면 잔고·주문·ledger 0, backup/manifest, 마지막 run audit를
   한 묶음으로 고정한 다음 날부터 새 deployment period를 시작한다.

## 롤백과 비상 종료

- `close_only → active` 롤백은 명시적 운영 결정 후 환경변수를 제거하거나 `active`로 바꾼다.
  새 전략이 별도 슬롯에 있으면 롤백 중 계정/DB 충돌이 없다.
- `archive_only → close_only`는 잔여 포지션이 뒤늦게 확인된 경우에만 사용한다.
- private key 노출, 잘못된 주문 폭주, 시장/규제 비상에서는 신규 진입 동결과 BUY 취소를
  즉시 수행하고, 손실 한도를 확인한 뒤 제한된 `bid`/`sweep` 청산을 별도 승인한다.
