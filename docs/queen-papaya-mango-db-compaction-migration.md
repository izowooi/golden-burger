# Golden Queen · Papaya · Mango SQLite 축소 마이그레이션

작성일: 2026-08-05
대상: `golden-queen`, `golden-papaya`, `golden-mango`의 live/simulation runtime DB

## 결론

세 전략의 거래·주문·체결 evidence는 그대로 두고 반복 market telemetry만 `compact-v1`로
축약한다. migration은 bot `run`과 분리된 `polybot-db-maintenance migrate` 명령으로 먼저
완료한다. 성공한 DB에는 활성 marker가 남으므로 이후 평소 Jenkins shell로 다시 실행하면
별도 migration 환경변수 없이도 한 시간마다 bounded cleanup이 자동 수행된다.

한 Jenkins job 안에 runtime `--job`이 여러 개라면 `trades.db`와 `trades_sim.db` 각각이
독립 DB다. 필요한 DB마다 한 번씩 수행하며, 경로를 계좌명·전략명으로 추정하지 않는다.
CLI는 등록된 18개 전략명만 허용하고, DB에 남은 `run_audits`/`strategy_configs` provenance가
요청한 `--strategy`와 다르거나 여러 전략이 섞여 있으면 변경 전에 거부한다.

## 실측 원인과 목표 정책

2026-08-04 동기화된 Queen 12h live 복제본은 9,276,411,904 bytes(8.6GiB)였다.
`market_sweep_memberships` 24,694,587행과 그 인덱스가 물리 공간의 대부분을 차지했고,
snapshot은 106,407행뿐이었다. 원본을 건드리지 않은 SQLite online-backup 복제본으로 실제
migration한 결과 membership은 187,000행(99.24% 감소), snapshot은 24,019행(77.43% 감소),
파일은 213,876,736 bytes(97.69% 감소)가 되었고 `quick_check=ok`를 통과했다. 1,562개 sweep의
scalar count/digest는 모두 유지하고, 그중 24시간 checkpoint 12개의 상세 membership만 남긴
결과다. migration CLI 구간은 이 MacBook에서 약 48분, 원본 복제까지 포함한 전체 검증은 약
50분이 걸렸다. 실제 runtime DB의 최종 크기와 시간은 저장장치·데이터 분포에 따라 달라진다.

| 전략 | 원형 snapshot | cold snapshot | telemetry retention | sweep 상세 |
|---|---:|---|---:|---:|
| Queen | 최근 1시간 | 12h bucket의 양방향 최저·최고 변화점 | 60일 | 24시간 checkpoint |
| Papaya | 최근 1시간 | 12h bucket의 양방향 최저·최고 변화점 | 60일 | 24시간 checkpoint |
| Mango | 최근 6시간 | 12h bucket의 최신 관측 | 7일 | 해당 테이블이 있으면 24시간 checkpoint |

Papaya와 Queen의 extrema는 과거 임계값 이상 관측 여부와 first crossing을 보존한다. 거래가
직접 참조하는 `entry_snapshot_id`와 `prior_snapshot_id_at_entry`는 retention 밖이어도
삭제하지 않는다.

Mango 동기화본은 353,095,680 bytes, snapshot 1,064,081행이었다. 종전 `compact-v1`은
실제 6시간 momentum 신호보다 넓은 최근 24시간을 5분 원형으로 남겼다. 새 기본값은 정확히
6시간이다. 원본을 건드리지 않은 SQLite online-backup 복제본으로 실제 migration한 결과
snapshot은 313,030행(70.6% 감소), 파일은 87,117,824 bytes(75.3% 감소)가 되었고
`quick_check=ok`를 통과했다. 실제 runtime DB의 최종 수치는 실행 시각과 데이터 분포에 따라
달라진다. 과거 24시간 policy가 활성화된 Mango DB는 새 코드로 평소 bot을 시작하기 전에
아래 전용 migration을 반드시 먼저 실행한다. 정상 bot 시작은 policy mismatch를 fail closed해
실수로 정책을 바꾸지 않는다.

다음 테이블은 정리 대상이 아니다.

- `trades`, `strategy_configs`, `run_audits`
- `order_submissions`, `order_status_events`, `order_fills`
- `quantity_scale_repairs`
- 거래가 참조하는 first-crossing snapshot lineage

## 전체 순서

1. 대상 Jenkins job의 timer를 끄고 진행 중인 build가 끝났는지 확인한다.
2. 같은 SQLite를 여는 다른 Jenkins job/process가 없는지 확인한다.
3. 정확한 runtime DB 경로와 파일 크기를 기록한다.
4. DB 파일 크기의 최소 3배 여유 공간을 같은 volume에 확보한다.
5. 9GB급 DB는 최소 60분 무거래 window와 90분 이상의 build timeout을 확보한다.
6. WAL checkpoint 후 journal mode를 `DELETE`로 바꾼다.
7. workspace 밖 backup 경로를 지정해 전용 migration을 실행한다.
8. JSON 결과의 `status=migrated`, backup 경로, 전후 byte/row count를 보관한다.
9. `quick_check=ok`, `auto_vacuum=2`, active marker와 strategy identity를 확인한다.
10. 평소 Jenkins shell을 수동으로 한 번 실행하고 성공하면 timer를 다시 켠다.

마이그레이션 동안에는 private key나 funder address가 필요 없다. migration 전용 Jenkins
build에 인증정보를 inline으로 넣지 않는다.

다만 저장 요구사항을 결정하는 전략 env는 정상 job과 같아야 한다. Queen/Papaya의
`POLYBOT_MAX_SNAPSHOT_GAP_MINUTES`, `POLYBOT_SNAPSHOT_RETENTION_DAYS`, Mango의
`POLYBOT_MOMENTUM_LOOKBACK_HOURS`를 기본값에서 바꿨다면 migration shell에도 같은 값을 넣는다.
CLI가 이 resolved requirement를 marker에 고정하므로 이후 정상 run과 다르면 fail closed한다.
아래에서 지우는 것은 전략 신호가 아니라 `POLYBOT_DB_*` 수동 저장정책 override뿐이다.

## 공통 사전 점검

아래에서 `BOT_DB`만 실제 경로로 바꾼다.

```bash
set +x
set -euo pipefail

# 과거 compact override가 새 기본 정책을 가리지 않게 migration shell에서 제거한다.
unset POLYBOT_DB_MAINTENANCE
unset POLYBOT_DB_HOT_HOURS
unset POLYBOT_DB_ROLLUP_HOURS
unset POLYBOT_DB_RETENTION_DAYS
unset POLYBOT_DB_MAINTENANCE_INTERVAL_HOURS
unset POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS

test -f "$BOT_DB"
df -h "$(dirname "$BOT_DB")"

sqlite3 "$BOT_DB" "PRAGMA busy_timeout=30000; PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 "$BOT_DB" "PRAGMA journal_mode=DELETE;"
test "$(sqlite3 "$BOT_DB" 'PRAGMA journal_mode;')" = "delete"
test ! -e "${BOT_DB}-wal"
test ! -e "${BOT_DB}-shm"
```

writer가 남아 있거나 WAL/SHM sidecar가 사라지지 않으면 강제로 삭제하지 말고 해당 build를
찾아 중지한다.

## Queen 실행 예시

각 runtime DB에 반복한다. 아래 이름은 예시이며 실제 존재하는 디렉터리를 먼저 확인한다.

```bash
#!/bin/bash
set +x
set -euo pipefail

UV=/Users/jongwoopark/.local/bin/uv
BOT_DB="$WORKSPACE/golden-queen/data/queen-live-5usdc-24h-20260805/trades.db"
unset POLYBOT_DB_MAINTENANCE POLYBOT_DB_HOT_HOURS POLYBOT_DB_ROLLUP_HOURS
unset POLYBOT_DB_RETENTION_DAYS POLYBOT_DB_MAINTENANCE_INTERVAL_HOURS
unset POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS

cd "$WORKSPACE/golden-queen"
"$UV" sync --frozen
"$UV" run polybot-db-maintenance migrate \
  --strategy golden-queen \
  --db "$BOT_DB" \
  --backup-dir "$HOME/polybot-db-backups" \
  --confirm
```

12h arm, 과거 `queen-live-12h`, simulation DB도 보존·계속 사용할 필요가 있으면 각각 정확한
파일을 지정해 별도로 실행한다. 폐기할 DB를 굳이 마이그레이션하지는 않는다. 단, 삭제하기
전에는 회고/backup 보존 여부를 먼저 결정한다.

## Papaya 실행 예시

```bash
#!/bin/bash
set +x
set -euo pipefail

UV=/Users/jongwoopark/.local/bin/uv
RUNTIME_JOB="papaya-live"  # 실제 --job 이름으로 교체
BOT_DB="$WORKSPACE/golden-papaya/data/$RUNTIME_JOB/trades.db"
unset POLYBOT_DB_MAINTENANCE POLYBOT_DB_HOT_HOURS POLYBOT_DB_ROLLUP_HOURS
unset POLYBOT_DB_RETENTION_DAYS POLYBOT_DB_MAINTENANCE_INTERVAL_HOURS
unset POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS

cd "$WORKSPACE/golden-papaya"
"$UV" sync --frozen
"$UV" run polybot-db-maintenance migrate \
  --strategy golden-papaya \
  --db "$BOT_DB" \
  --backup-dir "$HOME/polybot-db-backups" \
  --confirm
```

## Mango 실행 예시

Mango의 과거 active marker가 24h hot policy이면 이 명령이 새 backup을 만든 다음 6h policy로
안전하게 재프로파일한다.

```bash
#!/bin/bash
set +x
set -euo pipefail

UV=/Users/jongwoopark/.local/bin/uv
RUNTIME_JOB="default"  # 실제 --job 이름으로 교체
BOT_DB="$WORKSPACE/golden-mango/data/$RUNTIME_JOB/trades.db"
unset POLYBOT_DB_MAINTENANCE POLYBOT_DB_HOT_HOURS POLYBOT_DB_ROLLUP_HOURS
unset POLYBOT_DB_RETENTION_DAYS POLYBOT_DB_MAINTENANCE_INTERVAL_HOURS
unset POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS

cd "$WORKSPACE/golden-mango"
"$UV" sync --frozen
"$UV" run polybot-db-maintenance migrate \
  --strategy golden-mango \
  --db "$BOT_DB" \
  --backup-dir "$HOME/polybot-db-backups" \
  --confirm
```

## 사후 검증

```bash
sqlite3 "$BOT_DB" "PRAGMA quick_check; PRAGMA auto_vacuum;"
sqlite3 -json "$BOT_DB" \
  "SELECT profile, schema_version, strategy_name, active,
          activated_at, last_maintained_at, last_report_json
     FROM polybot_db_maintenance;"
```

정상값은 `quick_check=ok`, `auto_vacuum=2`, `profile=compact-v1`, `active=1`, 그리고
실행한 전략과 동일한 `strategy_name`이다. Mango는 `last_report_json.policy.hot_hours=6.0`,
Queen/Papaya는 `1.0`이어야 한다.

백업은 다음과 같이 생성된다.

```text
$HOME/polybot-db-backups/<strategy>/*.pre-compact-v1.db
$HOME/polybot-db-backups/<strategy>/*.manifest.json
```

manifest SHA-256과 backup `quick_check`가 맞지 않으면 bot을 재개하지 않는다.

backup은 원본과 같은 크기이므로 live DB가 줄어도 Mac 전체 사용량은 즉시 그만큼 줄지 않는다.
이 경로는 `daily-rsync` 수집 대상에 넣지 않는다. manifest hash 확인과 별도 내구성 저장소 복제,
복구 점검, 정한 보존기간이 모두 끝난 뒤에만 Mac의 오래된 backup을 수동 정리한다. 여러 runtime
DB는 한꺼번에 실행하지 말고 하나씩 migration·검증·backup 이전을 끝낸 다음 다음 DB로 간다.

## 정상 재실행

검증 후 기존 Jenkins trading shell을 그대로 실행한다. 다음 변수는 추가하지 않는다.

```text
POLYBOT_DB_MAINTENANCE
POLYBOT_DB_HOT_HOURS
POLYBOT_DB_ROLLUP_HOURS
POLYBOT_DB_RETENTION_DAYS
POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS
```

DB의 active marker가 정책을 기억하므로 다음 실행부터 자동 maintenance가 이어진다. migration
명령과 `polybot run`을 같은 일회성 shell에 연달아 두지 않는다. migration 성공을 별도로
확인한 뒤 평소 job을 수동 실행하는 것이 rollback 경계를 명확하게 한다.

## 실패와 rollback

원본 DB는 별도 working copy에서 delete/rollup/VACUUM과 무결성 검사를 모두 통과한 뒤에만
`os.replace`로 교체된다. source 크기나 mtime이 작업 중 바뀌면 교체하지 않고 실패한다.

rollback이 필요하면 timer를 다시 끈 상태에서 다음 순서를 따른다.

1. manifest의 backup SHA-256을 검증한다.
2. backup에 `PRAGMA quick_check`를 수행한다.
3. 실패한 현재 DB도 별도 이름으로 보존한다.
4. 실행 중 writer가 없는 상태에서 검증한 backup을 원자적으로 복원한다.
5. 복원 후 `quick_check`와 주요 보호 테이블 row count를 다시 확인한다.

실행 중인 SQLite 위에 `cp`로 덮어쓰거나 WAL sidecar만 삭제하지 않는다. 더 자세한 공통 계약은
[전략 SQLite 저장공간 경량화 운영 가이드](sqlite-storage-maintenance.md)를 따른다.
