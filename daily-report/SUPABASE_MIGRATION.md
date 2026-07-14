# Supabase atomic writer migration 복구

`check-supabase`가 `PGRST202`를 반환하면 아래 두 경우 중 하나입니다.

1. `pb_portfolio_writer_preflight_v3()`가 운영 DB에 아직 없다.
2. 함수는 있지만 PostgREST schema cache가 이전 상태다.

`SUPABASE_SECRET_KEY`는 Data API용 서버 키이므로 table/function DDL을 설치할 수 없습니다.
정기 daily-report job에 DB 관리자 권한을 추가하거나, 여러 REST upsert로 우회하지 않습니다.

## 가장 빠른 1회 복구: Supabase SQL Editor

Supabase Dashboard의 해당 프로젝트에서 **SQL Editor**를 엽니다. 적용 전에 Dashboard의
project ref가 `SUPABASE_URL` hostname의 ref와 일치하는지, SQL 내용이 검토된 최신 `main`
commit의 파일인지 확인합니다. 먼저 다음 read-only 진단을 실행합니다.

```sql
select
  to_regprocedure('public.pb_portfolio_writer_preflight_v3()') as preflight,
  to_regprocedure(
    'public.pb_write_complete_portfolio_snapshot_v3(date,timestamptz,numeric,text,jsonb)'
  ) as writer;
```

- 둘 중 하나라도 `NULL`이면 저장소의 SQL을 아래 순서로 각각 실행합니다. 파일 내부에
  `BEGIN`/`COMMIT`이 있으므로 각 파일은 오류 시 전체 rollback됩니다.

  1. `slack-data-collector/sql/pb_portfolio_schema.sql`
  2. `slack-data-collector/sql/pb_portfolio_history_v2.sql`
  3. `slack-data-collector/sql/pb_portfolio_history_v3.sql`

- 둘 다 함수 이름을 반환하면 schema cache만 갱신합니다.

```sql
notify pgrst, 'reload schema';
```

그 다음 Jenkins workspace의 최신 `main`에서 아래 preflight를 다시 실행합니다.

```bash
cd daily-report
uv sync --frozen
uv run --frozen python daily_report.py check-supabase
```

성공 문구는 `Supabase 연결/계정 계약 확인 성공 - 계정 카탈로그: 9개`입니다. 성공하기
전에는 `run`을 실행하지 않습니다.

## 금지되는 임시 조치

- `pb_daily_*` table을 여러 REST 요청으로 직접 upsert
- RPC가 없을 때 정상 Slack `COMPLETE` 메시지만 전송
- `anon`/publishable role에 write 권한 부여
- Jenkins job에 DB 관리자 password 또는 migration 권한 부여

이 조치들은 카탈로그 전체 계정과 portfolio total의 단일 transaction 계약을 깨거나 불필요한 관리자
권한을 상시 노출합니다.
