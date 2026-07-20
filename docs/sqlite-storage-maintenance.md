# 전략 SQLite 저장공간 경량화 운영 가이드

## 결론

13개 `golden-*` 전략은 공통 `compact-v1` 프로필을 사용한다. 기존 DB에는 환경변수 하나로
일회성 안전 마이그레이션을 실행하고, 성공 후 그 환경변수를 제거한다. 활성화 상태는 DB의
`polybot_db_maintenance`에 남으므로 이후 실행도 자동으로 시간당 경량화를 계속한다. 현재
상태 schema는 `2`이며, 활성화 당시의 실제 전략 window 요구사항, destructive policy와 신뢰할
수 있는 snapshot 기준시각도 함께 고정한다.

```bash
export POLYBOT_DB_MAINTENANCE=compact-v1
```

이 프로필은 거래 판단·주문·체결·run/config 증거를 삭제하지 않는다. 용량의 대부분을 차지한
시장 telemetry만 전략별로 축약한다.

## 12GB DB에서 확인한 원인

`docs/db/trades.db`의 원본은 12,445,069,312 bytes(11.59GiB), `quick_check=ok`,
freelist 0이었다. 즉 단순 `VACUUM`으로 줄일 빈 페이지가 없었다.

| 데이터군 | 물리 크기 | 전체 비중 |
|---|---:|---:|
| `market_snapshots` + 인덱스 | 7,370,223,616 bytes | 59.22% |
| `market_sweep_memberships` + 인덱스 | 5,011,050,496 bytes | 40.27% |
| 합계 | 12,381,274,112 bytes | 99.49% |

건강한 수집일에는 snapshot이 약 202만 행/일 쌓여 기존 구조가 약 1.8GB/일 증가했다.
membership 정상 행의 99.66%는 같은 형태였고, snapshot 가격·호가도 약 93%가 직전 값과
같았다. 주문·체결 테이블이 아니라 매 sweep의 전체 시장 telemetry 반복 저장이 원인이었다.

## compact-v1 보존 계약

공통 규칙은 다음과 같다.

- `trades`, `strategy_configs`, `run_audits`, `order_submissions`,
  `order_status_events`, `order_fills`, `quantity_scale_repairs`의 행을 절대 정리하지 않는다.
- 매 Gamma sweep의 count, exclusion summary, membership SHA-256 digest, run 연결은 계속
  저장한다.
- 시장별 상세 membership은 24시간 checkpoint에 저장한다. 나머지 sweep은
  `membership_detail_stored=0`인 summary evidence다.
- 중복 snapshot `condition_id`/`run_id` 인덱스와 저선택도 boolean membership 인덱스를 제거한다.
- 보존기간 밖 telemetry를 삭제하고, 최초 실행은 full `VACUUM`과
  `auto_vacuum=INCREMENTAL` 전환을 수행한다.
- 활성화 뒤에는 기본 1시간마다 bounded maintenance와 incremental vacuum을 수행한다.

snapshot은 라이브 신호에 필요한 경계를 다르게 보존한다.

| 전략 | full-cadence hot 구간 | cold 구간(기본 12시간 bucket) |
|---|---:|---|
| `golden-elderberry` | 1시간 | 양방향 최저·최고 변화점. 48h favorite peak와 45분 안정화 신호 유지 |
| `golden-honeydew` | 24시간 | bucket의 최신 관측. 24h median 라이브 신호는 원형 유지 |
| `golden-nectarine` | 1시간 | 양방향 prefix/suffix 최저 변화점. 20일 이동창과 최근 24h 제외 경계의 최저를 정확히 복원 |
| `golden-orange` | 168시간 | bucket의 최신 관측. 비가중 7일 median은 축약할 수 없어 전체 base window 유지 |
| `golden-papaya` | 1시간 | 양방향 최저·최고 변화점. 과거 0.95 이상 관측 predicate 유지 |
| `golden-cherry` | 72시간 | bucket의 최신 관측 |
| `golden-banana` | 24시간 + 최근 N개 원형 보호 | bucket의 최신 관측. 기본 `long_window + 10`인 최근 82개는 장시간 수집 중단으로 24시간보다 오래돼도 원형 유지 |
| 나머지 전략 | 24시간 | bucket의 최신 관측 |

Papaya 거래가 참조한 `entry_snapshot_id`와 `prior_snapshot_id_at_entry`는 보존기간과
무관하게 영구 보호한다. 구버전 거래는 entry snapshot과 동일 condition의 직전
`(timestamp, id)` row를 마이그레이션 중 추론해 함께 보호한다. 마이그레이션 전후 dangling
lineage count가 달라지면 전체 작업을 실패시킨다.

## 일회성 Jenkins 실행

### 1. 사전 조건

1. 해당 전략의 timer/build를 중지하고 실행 중인 writer가 없는지 확인한다.
2. DB 크기의 최소 3배 여유 공간을 같은 volume에 확보한다.
3. Jenkins timeout을 12GB DB 기준 최소 2시간 이상으로 둔다.
4. private key와 funder 주소는 inline `export`하지 않고 Jenkins Credentials Binding을 쓴다.
   Freestyle shell이 `-x`로 실행될 수 있으므로 secret 참조 전부터 `set +x`를 사용한다.

DB가 WAL mode이면 파일 하나만 복제해서는 일관성을 보장할 수 없으므로 마이그레이션은
의도적으로 실패한다. **같은 DB를 쓰는 모든 job을 중지한 상태에서만** 다음 사전 변환을 한다.

```bash
BOT_DB="$(pwd)/data/default/trades.db"  # 실제 job/db 경로로 수정
sqlite3 "$BOT_DB" "PRAGMA busy_timeout=30000; PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 "$BOT_DB" "PRAGMA journal_mode=DELETE;"
test "$(sqlite3 "$BOT_DB" 'PRAGMA journal_mode;')" = "delete"
test ! -e "${BOT_DB}-wal"
test ! -e "${BOT_DB}-shm"
```

writer lock이 남아 있거나 WAL/SHM sidecar가 남아 있으면 원인을 확인하고 중단한다. 실행 중인
job을 둔 채 강제로 삭제해서는 안 된다.

### 2. 정확히 한 번 실행

아래 예시는 `golden-nectarine`이다. 폴더만 해당 전략으로 바꾸면 13개 전략에 동일하다.

```bash
#!/bin/bash
set -euo pipefail
set +x

# Jenkins Credentials Binding이 제공하는 기존 secret 환경변수는 여기서 사용한다.
export POLYBOT_DB_MAINTENANCE=compact-v1
# 생략 시 $HOME/.polybot/db-backups/<strategy> 사용. 반드시 workspace 밖이어야 한다.
export POLYBOT_DB_BACKUP_DIR="$HOME/polybot-db-backups"

cd ./golden-nectarine
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

다음 로그를 확인한다.

```text
SQLite compact-v1 완료 - strategy=... bytes=...->... snapshots=...->... memberships=...->...
```

백업 디렉터리에는 아래 두 파일이 생성된다.

- `*.pre-compact-v1.db`: SQLite online backup으로 만든 일관된 복구본
- `*.manifest.json`: source/backup bytes와 SHA-256

작업은 별도 DB copy에서 `quick_check`, 보호 테이블 count, lineage gap을 검증하고 full
`VACUUM`까지 끝낸 뒤에만 `os.replace`로 원자 교체한다. 작업 중 source DB의 크기나 mtime이
바뀌면 교체하지 않고 실패한다.

### 3. 성공 후 정상 shell

성공한 다음 build부터는 **`POLYBOT_DB_MAINTENANCE` 한 줄만 제거**한다. 활성화할 때 사용한
`POLYBOT_DB_HOT_HOURS`, `POLYBOT_DB_ROLLUP_HOURS`, `POLYBOT_DB_RETENTION_DAYS`,
`POLYBOT_DB_MEMBERSHIP_DETAIL_HOURS` override가 있었다면 이후에도 값을 바꾸거나 제거하지 않는다.
가장 안전한 운영은 처음부터 이 override들을 쓰지 않고 전략별 기본값을 쓰는 것이다.

```bash
#!/bin/bash
set -euo pipefail
set +x

export POLYBOT_DB_BACKUP_DIR="$HOME/polybot-db-backups"

cd ./golden-nectarine
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run python ./main.py run
```

플래그를 실수로 한 번 더 남겨도 active marker를 확인해 백업/마이그레이션을 반복하지 않는다.
하지만 성공 확인 후 제거하는 것이 운영 계약이다.

활성화 뒤 strategy YAML/env의 signal window 또는 compact policy가 달라지면 코드가 자동 삭제를
계속하지 않고 fail closed한다. 보존 경계를 바꾸려면 기존 profile 값을 억지로 수정하지 말고,
새 migration profile과 복제본 검증을 먼저 추가해야 한다. 기존 repository의 별도
`cleanup_old_snapshots`도 active DB에서는 삭제를 공유 maintainer에 양보한다.

## 13개 전략 적용 순서

각 DB마다 위 절차를 한 번씩 수행한다.

```text
golden-apple       golden-banana      golden-cherry
golden-date        golden-elderberry  golden-fig
golden-grape       golden-honeydew    golden-lime
golden-mango       golden-nectarine   golden-orange
golden-papaya
```

같은 DB를 공유하는 두 Jenkins job이 있다면 동시에 실행하지 말고 한 job에서만 migration을
성공시킨 뒤 둘 다 정상 shell로 복귀한다.

## 검증과 복구

마이그레이션 뒤 DB에서 최소한 아래를 확인한다.

```bash
sqlite3 "$BOT_DB" "PRAGMA quick_check; PRAGMA auto_vacuum;"
sqlite3 "$BOT_DB" \
  "SELECT profile, schema_version, strategy_name, active, activated_at, last_maintained_at
   FROM polybot_db_maintenance;"
```

정상값은 `quick_check=ok`, `auto_vacuum=2`, `profile=compact-v1`, `active=1`이다.
현재 code로 새로 활성화했다면 `schema_version=2`여야 한다. `last_report_json`의 `policy`,
`requirements`, `snapshot_anchor`를 임의 편집하지 않는다.
월간 회고 전에는 compact cadence를 이해하는 최신 audit를 사용한다.

```bash
uv run --project polybot-observability polybot-retro audit \
  --db "$BOT_DB" --days 30 --output-dir "$HOME/polybot-retro/compact-check" --strict
```

복구가 필요하면 Jenkins를 다시 중지하고 manifest SHA-256과 backup `quick_check`를 먼저
확인한 뒤 `*.pre-compact-v1.db`를 복원한다. 실행 중인 SQLite 파일을 `cp`로 덮지 않는다.

## 용량 예산과 경보선

실제 12,445,069,312-byte nectarine DB 복제본은 `quick_check`와 보호 테이블 count 검증을
통과하면서 332,431,360 bytes로 줄었다. **37.44배 축소(97.33% 감소)**이며 snapshot은
14,506,769→607,423행, 상세 membership은 13,113,974→74,490행이었다. 원본 DB의 SHA-256은
작업 전후 `c52936a41aa7e6aaf9cc52b75fc96f798bd9aeee1c3c9f7868e52f9e53f193c1`로 동일했다.

실측 원본의 row당 물리 비용과 가장 활발한 100개 condition 표본을 사용하면 nectarine 기본
정책은 30일 약 0.8~0.9GB 범위로 예상된다. 이는 universe와 가격 변화율에 따라 달라지는
예산이지 하드 cap은 아니다. qualifying universe 일평균이 약 9,000개를
지속해서 넘거나 DB 증가량이 7일 이동평균 30MB/일을 넘으면 다음 월까지 기다리지 말고
재측정한다.

아래 override는 신호를 훼손할 수 있어 기본값 사용을 권장한다. 코드도 명백히 위험한 값은
fail closed한다.

- Honeydew: `POLYBOT_DB_HOT_HOURS >= POLYBOT_MEDIAN_LOOKBACK_HOURS`
- Elderberry: hot window가 `POLYBOT_STAB_WINDOW_MINUTES` 이상이고,
  retention이 `POLYBOT_REF_WINDOW_HOURS` 이상이며, 실제 reference 구간
  (`REF_WINDOW - REF_EXCLUDE_RECENT`)이 rollup bucket 이상
- Orange: hot window와 retention이 모두 `POLYBOT_BASE_WINDOW_DAYS` 이상
- Banana: hot window가 명목 momentum window 이상이며, 실제 조회 tail인
  `long_window + 10`개도 시간과 무관하게 원형 보호
- Date/Fig/Grape/Lime/Mango: hot window가 각 live signal의 최대 lookback 이상
- Papaya: hot window가 `POLYBOT_MAX_SNAPSHOT_GAP_MINUTES` 이상이고,
  DB retention이 `POLYBOT_SNAPSHOT_RETENTION_DAYS` 이상
- Nectarine: DB retention이 `POLYBOT_LOOKBACK_DAYS` 이상
- 모든 전략: retention이 full-cadence hot window 이상

`POLYBOT_DB_ROLLUP_HOURS`, `POLYBOT_DB_HOT_HOURS`, `POLYBOT_DB_RETENTION_DAYS`나 signal
window를 바꿀 때는 새 migration profile/cohort로 취급하고 전략 테스트·실제 DB 복제본
audit·용량 추정을 다시 수행한다. 활성 DB에서 현재 profile 값을 직접 바꾸는 것은 지원하지
않는다.
