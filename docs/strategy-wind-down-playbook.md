# 전략 퇴역과 계정 전환 플레이북

## 결론

14개 `golden-*` 전략은 모두 동일한 수명주기 스위치를 지원한다. 전략을 퇴역할 때는 즉시
일괄 매도하지 않고 Jenkins 잡을 먼저 `close_only`로 바꾼다. 기존 GTC BUY 주문을 한 번
취소한 뒤 같은 주기로 자연 청산을 반복하고, 유예기간 뒤에도 남은 포지션만 유동성과
스프레드를 확인해 단계적으로 정리한다.

`close_only`는 강제 청산 모드가 아니다. 신규 스캔과 BUY만 차단하고 각 전략의 기존 청산
조건을 그대로 실행한다. 따라서 최대 보유시간이 없거나 시장 종료일이 누락된 전략은
포지션이 영구히 남을 수 있다. `archive_only`는 포지션·미체결 주문·execution ledger를
정리한 뒤에만 사용한다.

## 수명주기 모드

| 모드 | 기존 스냅샷/정리 Phase | 기존 포지션 청산 | 신규 스캔/매수 | 용도 |
|---|---:|---:|---:|---|
| `active` | O | O | O | 기본 운영. 환경변수 미설정 시 항상 이 값이다. |
| `close_only` | O | O | X | 퇴역 유예기간. 자연 청산을 계속한다. |
| `archive_only` | O | X | X | 거래 증거가 모두 정리된 뒤 아카이브만 유지한다. |

스냅샷 Phase가 없는 `golden-apple`·`golden-cherry`에서는 첫 번째 열이 해당되지 않는다.
그 외 전략은 `close_only`에서도 청산 신호와 증거 축적에 필요한 기존 snapshot/cleanup을
계속 수행한다. `archive_only`에서도 사이클 시작 전 execution ledger 대사는 수행한다.
유효하지 않은 모드는 설정 로드 단계에서 실패한다. `POLYBOT_BUY_AMOUNT=0`은 설정 검증에서
거부되므로 드레인 스위치로 사용하지 않는다.

지원 전략: `golden-apple`, `golden-banana`, `golden-cherry`, `golden-date`,
`golden-elderberry`, `golden-fig`, `golden-grape`, `golden-honeydew`, `golden-lime`,
`golden-mango`, `golden-nectarine`, `golden-orange`, `golden-papaya`, `golden-queen`.

`lifecycle_mode`는 resolved config와 run provenance에 포함된다. 따라서 이 기능을 처음
배포하면 환경변수를 생략해 `active`로 실행하더라도 이전 배포와 `config_hash` cohort가 한 번
분리된다. 이는 매매 규칙 변경이 아니라 명시적인 기본 수명주기 값이 증거에 추가된 결과다.

## 단계별 전환 절차

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
   STRATEGY_DIR=golden-apple
   uv run tools/wind_down.py --env-file "$STRATEGY_DIR/.env" status
   ```

   Jenkins Credentials Binding만 사용하는 운영 환경에서는 `.env`를 만들지 말고 동일한
   credential-bound shell 안에서 `status`를 실행한다.

### T0: 신규 진입 동결

1. 퇴역할 Jenkins 잡에 다음 한 줄만 추가한다. 기본값은 `active`이므로 이 값을 추가하지
   않은 다른 잡의 매매 로직은 바뀌지 않는다.

   ```bash
   export POLYBOT_LIFECYCLE_MODE=close_only
   ```

2. `uv run python main.py config`의 `Lifecycle Mode: close_only`와 첫 실행 로그의
   `Phase 2/3 건너뜀`을 확인한다. 잡 주기는 기존 3~5분을 유지할 수 있다. 각 실행은 한
   사이클짜리 프로세스이므로 Jenkins의 **동시 빌드 허용은 끄고**, 이전 빌드가 끝난 뒤
   다음 빌드가 시작되게 한다. 실행시간이 5분을 넘으면 queue 지연이나 back-to-back 실행이
   생길 수 있으므로 build duration과 queue를 감시하고, p95가 주기에 근접하면 cron 간격을
   늘린다.
3. `close_only`는 이미 CLOB에 올라간 GTC BUY 주문을 자동 취소하지 않는다. 각 계정에서
   BUY 주문만 먼저 dry-run한 후 한 번 취소한다. SELL 주문까지 취소하려면 `ALL`을
   명시해야 한다.

   ```bash
   STRATEGY_DIR=golden-apple
   uv run tools/wind_down.py --env-file "$STRATEGY_DIR/.env" cancel
   uv run tools/wind_down.py --env-file "$STRATEGY_DIR/.env" cancel --side BUY --yes
   ```

4. 첫 사이클의 `lifecycle_mode=close_only`, `buy_candidates=0`, `bought=0`, 성공한 run audit를
   확인한다. SELL GTC는 일괄 취소하지 않는다.

### T+1일~전략별 grace 종료일: 자연 청산

- 전략별 원래 청산 규칙을 그대로 적용한다. `golden-apple`은 매도 임계값만,
  `golden-banana`는 임계값/익절/손절/dead-cross만 있어 최대 보유시간이 없다.
  `golden-cherry`도 최대 보유시간이 없고 종료일이 없는 시장에는 time exit가 적용되지
  않는다. 이 세 전략은 유예기간 뒤 잔여 포지션 수동 분류가 특히 중요하다.
- 최대 보유시간이 있는 전략은 `golden-elderberry` 48h, `golden-honeydew` 24h,
  `golden-nectarine` 120h, `golden-orange` 72h이다. 시간은 정상 주문 접수·체결과 대사가
  성공한다는 전제이므로 네트워크 오류와 부분 체결을 위한 버퍼를 둔다.
- 해결 잔여시간 상한으로 진입하는 전략의 기본 grace는 `golden-date` 약 7일,
  `golden-fig` 약 10일, `golden-mango` 약 14일에 대사 버퍼를 더한다. 설정값을 바꿨다면
  실제 resolved config의 상한을 기준으로 다시 계산한다.
- `golden-papaya`는 사전 해결 time exit이 없으므로 72h 뒤 강제 청산을 가정하지 않는다.
  0.90 absolute stop이 체결되지 않은 포지션은 resolution 결과(1/0/드문 0.5)와 실제 redeem
  evidence까지 별도 분류하고, grace 종료 후에도 시장가성 sweep으로 억지 처분하지 않는다.
- `golden-queen`도 time exit이 없다. 진입 horizon 12h/24h는 신규 BUY 필터일 뿐 기존
  포지션 만료가 아니다. immutable 0.98 목표/0.85 stop 또는 resolution evidence까지
  관리하고, horizon 변경만으로 기존 포지션을 매도하지 않는다. Queen은 현재
  redeemable/실제 redeem transaction을 수집하지 않으므로 별도 운영 증거가 필요하다.
- `golden-grape`·`golden-lime`은 최대 보유시간이나 진입 잔여시간 상한이 없어 자연
  청산 완료시점을 보장할 수 없다. 손절/익절/모멘텀 또는 해결 임박 조건을 기다리되,
  사전에 정한 grace 종료일에 잔여분을 수동 분류한다. `close_only` 자체는 별도 만료시간을
  추가하지 않는다.
- 매일 `wind_down.py status`를 실행해 CLOB 미체결 BUY=0인지 확인한다. DB `HOLDING` 수만
  보고 지갑 포지션이 있다고 가정하지 않는다.
- `not enough balance / allowance`, `SubmissionEvidenceError`, unresolved intent가 있으면
  강제 매도를 반복하지 말고 먼저 주문/체결 증거를 대사한다.
- 유예기간 중 신규 전략은 기존 eco/fox 계정을 덮어쓰지 않고 lion/tiger/wolf 같은 별도
  슬롯에서 시작한다. 기존 DB와 Jenkins job_name을 새 전략에 재사용하지 않는다.

## 전략별 grace 종료 후 잔여 포지션 처리

다음 순서로 처리하며 모든 mutation 전에는 dry-run 결과를 보관한다.

1. **redeemable**: 매도 주문을 만들지 않고 redeem 절차로 분리한다.
2. **정상 유동성, 스프레드 5센트 이하**: midpoint 지정가를 5분 간격으로 최대 6회
   재호가한다. 먼저 `--yes` 없이 계획과 예상 비용을 확인한다.

   ```bash
   STRATEGY_DIR=golden-apple
   uv run tools/wind_down.py --env-file "$STRATEGY_DIR/.env" \
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

`golden-honeydew`·`golden-nectarine`의 중앙 snapshot 보존기간은 최소 60일이므로 전환 후
적어도 60일은 아카이브 책임을 유지한다. 이 두 잡을 모두 중지하려면 먼저 다른 job이 같은
시장 catalog/snapshot 계약과 5분 bucket coverage를 제공하는지 strict audit로 증명한다.
`golden-papaya`와 `golden-queen`도 기본 $1k
(`min(configured entry liquidity, $1k)`) request envelope의 자체 archive를 최소 60일
유지한다. 두 잡을 중지하려면 cursor-complete sweep, schedule/run manifest 기준 cadence
coverage, first observed crossing lineage를 다른 job이 제공하는지 먼저 증명한다.
다른 전략도 로컬 청산 신호가 snapshot history에 의존하면 증거 보존기간을 확인하고 잡을
중지한다.

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
