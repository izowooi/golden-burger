# Mac mini 저장공간 일일 모니터

`disk_monitor.py`는 Jenkins agent에서 실제 mount point의 전체·사용·여유 byte를 읽고,
`host × mount × date` 단위로 Supabase에 저장합니다. 포트폴리오 리포트와 독립적으로
실행되며 Polymarket credential, 지갑 주소, Slack webhook은 사용하지 않습니다.

## 1. Supabase migration

Supabase SQL Editor에서 다음 파일을 한 번 적용합니다.

```text
slack-data-collector/sql/pb_host_storage_v1.sql
```

이 migration은 다음 객체를 만듭니다.

- `pb_host_storage_daily`: 일별 filesystem capacity
- `pb_storage_writer_preflight_v1()`: 읽기 전용 contract 점검 RPC
- `pb_write_host_storage_snapshot_v1(...)`: 같은 host의 모든 mount를 원자적으로 upsert

테이블과 RPC는 `anon`·`authenticated`에 공개하지 않으며 `service_role`만 읽고 쓸 수
있습니다. 정기 Jenkins job에 database password나 migration 권한을 넣지 않습니다.

적용 후 다음 명령으로 contract를 확인합니다.

```bash
cd ./daily-report
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run python ./disk_monitor.py check-supabase
```

## 2. Jenkins Freestyle job

권장 schedule은 KST 기준 하루 한 번입니다.

```text
TZ=Asia/Seoul
H 8 * * *
```

Credentials Binding에서 기존 `polymarket-supabase-secret-key`를
`SUPABASE_SECRET_KEY` Secret text로 주입합니다. Secret을 shell에 직접 쓰지 않습니다.

```bash
#!/bin/bash
set +x
set -euo pipefail

export SUPABASE_URL=https://your-project-ref.supabase.co
export STORAGE_MONITOR_HOST_ID=macmini-m5
export STORAGE_MONITOR_TIMEZONE=Asia/Seoul

cd ./daily-report

/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run python ./disk_monitor.py collect \
  --mount "internal=/System/Volumes/Data" \
  --label "internal=Mac mini internal" \
  --mount "external-t7=/Volumes/t7" \
  --label "external-t7=External T7"
```

`/System/Volumes/Data`는 macOS의 쓰기 가능한 내부 Data volume입니다. `/`와
`/System/Volumes/Data`를 동시에 등록하면 같은 APFS container의 용량을 중복 해석할 수
있으므로 내부 저장소는 하나만 등록합니다.

현재 Mac mini의 1TB 외장 디스크는 `/Volumes/t7`으로 확인되었습니다. 다른 host에서 volume
이름이 다르면 위의 `external-t7` 경로만 실제 mount point에 맞게 바꿉니다.

추가 외장 디스크도 보려면 같은 형식의 인자를 더합니다.

```bash
  --mount "shadow-backup=/Volumes/YOUR_VOLUME_NAME" \
  --label "shadow-backup=Shadow backup"
```

모든 경로는 현재 존재하는 실제 mount point여야 합니다. 외장 디스크가 분리되었거나
경로가 일반 directory로 바뀌면 Supabase에 잘못된 내부 디스크 값을 쓰지 않고 job이
실패합니다. 여러 mount 중 하나라도 검증에 실패하면 아무 행도 적재하지 않습니다.

## 3. 최초 실행과 점검

Supabase 쓰기 없이 filesystem 검증만 하려면 `--simulate`를 붙입니다.

```bash
uv run python ./disk_monitor.py collect \
  --host-id macmini-m5 \
  --mount "internal=/System/Volumes/Data" \
  --simulate
```

실제 적재 후 SQL Editor에서 확인합니다.

```sql
select
  report_date,
  host_id,
  mount_id,
  mount_path,
  pg_size_pretty(total_bytes) as total,
  pg_size_pretty(used_bytes) as used,
  pg_size_pretty(available_bytes) as available,
  round(used_bytes::numeric / total_bytes * 100, 2) as used_percent,
  reported_at
from public.pb_host_storage_daily
order by report_date desc, host_id, mount_id
limit 30;
```

같은 날짜에 다시 실행하면 더 최신인 `reported_at` 관측값으로 갱신됩니다. stable
`STORAGE_MONITOR_HOST_ID`와 `--mount` ID는 변경하지 않아야 하나의 시계열로 이어집니다.

## 4. 대시보드 판정

`polymarket-dashboard`의 `/storage` 화면은 다음 기준을 사용합니다.

- 사용률 `80%` 이상: 주의
- 사용률 `90%` 이상: 위험
- 최신 관측이 `36시간` 초과: 보고 지연
- 최근 최대 30일 사용량 기울기: 일평균 증가량과 예상 소진일
- total capacity가 1% 넘게 바뀐 구간: volume 교체 가능성이 있어 증가율 계산 제외

한 번의 관측만 있거나 사용량이 증가하지 않으면 예상 소진일은 표시하지 않습니다.
