# Polymarket Daily Reporter

여섯 개 Polymarket 계정의 잔고를 조회해 Slack으로 보고하고, 같은 일일 스냅샷을 Supabase의 `pb_*` 테이블에 저장하는 Jenkins 작업입니다.

## 실행 순서

```text
Polymarket Data API 조회
  → 전 계정(카탈로그 기준)의 완전한 스냅샷 검증
  → 로컬 evidence SQLite에 run/account/position 원자적 저장
  → Supabase RPC에서 6계정+합계를 한 transaction으로 확정
  → 성공한 경우에만 Slack COMPLETE 일간/월간 메시지 전송
```

- 같은 날짜가 이미 있으면 최신 실행 값으로 덮어씁니다.
- 월간 모드는 Slack 메시지에 30일 P&L을 추가할 뿐, DB에는 동일한 일일 잔고를 한 번 저장합니다.
- 계정 하나라도 수집에 실패하거나 DB 계정 카탈로그와 매핑되지 않으면 정상 Slack 리포트를 보내지 않고 명시적 오류 알림과 실패 코드만 남깁니다. 기존 값을 0이나 부분 데이터로 덮지 않습니다.
- DB atomic RPC 실패는 Jenkins 종료 코드 `1`로 전달되고 Slack 오류 알림 대상에 포함되며, 정상 `COMPLETE` Slack 메시지는 보내지 않습니다.
- Slack 전송 실패와 DB 적재는 독립적입니다. Slack webhook 장애가 있어도 정상 수집 데이터는 DB에 저장을 시도하되 Jenkins 종료 코드는 `1`입니다.
- 7d/30d P&L은 기간 floor 또는 그 다음 날의 과거 snapshot이 있을 때만 표시하며, 더 짧은 이력을 해당 기간 성과로 가장하지 않고 `N/A`로 둡니다.

## 저장 테이블

| 테이블 | 용도 | 충돌 키 |
|---|---|---|
| `pb_algorithm_accounts` | Jenkins 이름과 안정적인 account ID 매핑 | `account_id` |
| `pb_daily_algorithm_balances` | 날짜·계정별 총액, 포지션, 현금 | `report_date, account_id` |
| `pb_daily_portfolio_totals` | 날짜별 전체 합계 | `report_date` |

현재 매핑은 다음과 같습니다.

| Jenkins 표시 이름 | account ID |
|---|---|
| `GOLDEN-APPLE (1)` | `golden-apple-1` |
| `GOLDEN-BANANA` | `golden-banana` |
| `GOLDEN-CHERRY` | `golden-cherry` |
| `GOLDEN-APPLE (2)` | `golden-apple-2` |
| `GOLDEN-ECO` | `golden-eco` |
| `GOLDEN-FOX` | `golden-fox` |

## 설치

Python 3.10 이상과 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
cd daily-report
uv sync --frozen
```

`supabase==2.31.0`을 정확한 버전으로 고정했으며 `uv.lock`도 함께 관리합니다.

## 환경변수

```bash
cp .env.example .env
chmod 600 .env
```

필수 변수는 다음과 같습니다.

```dotenv
ACCOUNT_1_NAME=golden-apple
ACCOUNT_1_ADDRESS=0x...
ACCOUNT_2_NAME=golden-banana
ACCOUNT_2_ADDRESS=0x...
ACCOUNT_3_NAME=golden-cherry
ACCOUNT_3_ADDRESS=0x...
ACCOUNT_4_NAME=golden-apple
ACCOUNT_4_ADDRESS=0x...
ACCOUNT_5_NAME=golden-eco
ACCOUNT_5_ADDRESS=0x...
ACCOUNT_6_NAME=golden-fox
ACCOUNT_6_ADDRESS=0x...

SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SECRET_KEY=sb_secret_your_server_only_key
REPORT_TIMEZONE=Asia/Seoul
DAILY_EVIDENCE_DB=data/daily_evidence.sqlite3
DAILY_REPORT_LOG_FILE=daily_report_local.log
```

`daily_report.py`는 실행 파일과 같은 폴더의 `.env`를 자동으로 읽습니다. 이미 Jenkins 환경변수로 주입했다면 `.env`는 필요하지 않습니다.

`DAILY_EVIDENCE_DB`에는 이미 조회한 계정·포지션의 비밀값 없는 일별 증거를 SQLite로
저장합니다. `evidence_report_runs`의 `COMPLETE`/`FAILED` 상태와 기대/관측 계정 수를
먼저 확인한 뒤 `evidence_account_snapshots`, `evidence_positions`를 사용합니다. 이
collection status는 downstream 전달 성공을 뜻하지 않습니다.
`evidence_delivery_status`의 Supabase/Slack `PENDING|SUCCESS|FAILED|SKIPPED`와 최종
`PENDING|COMPLETE|FAILED|NOT_ATTEMPTED`를 함께 확인해야 합니다. simulation과 collection
실패는 `NOT_ATTEMPTED`, 두 sink가 모두 성공한 경우만 delivery `COMPLETE`입니다. delivery
상태 갱신 자체가 실패하면 정상 Slack 발행을 중단합니다. 포지션은
`conditionId`, `asset`, `outcome`, `size`, `avgPrice`, `currentValue`, `cashPnl`,
`realizedPnl`, `redeemable`, `endDate`만 저장하며 wallet address와 API secret은 저장하지
않습니다. account total/position/cash는 Slack·Supabase와 같은 canonical cent로 저장합니다.
로컬 financial log와 evidence SQLite는 symlink를 거부하고 Unix mode `0600`을 강제하며,
권한을 보장할 수 없으면 실행을 중단합니다. Jenkinsfile은 DB를 매 build artifact로 fingerprint와 함께 보존하지만, artifact
retention(현재 30 build)은 **내구성 backup이 아닙니다**. 누적 DB가 이어지려면 같은
persistent workspace/volume을 사용해야 하며, SQLite online backup 또는 작업 종료 후
복사본을 workspace 밖 **암호화된** object storage/backup volume로 전송하고 SHA-256 및 정기 복구
검증을 해야 합니다. Jenkins artifact와 외부 backup은 운영자/감사자 최소 인원만 읽도록
권한을 제한하고, raw wallet address·webhook·credential이 들어간 파일은 archive/upload
대상에 절대 포함하지 않습니다. ephemeral agent에서 이전 DB를 restore하지 않은 실행은 새 이력으로
시작하므로 월간 evidence coverage로 인정하지 않습니다.

Jenkins는 `DAILY_REPORT_LOG_FILE=daily_report_build_<BUILD_NUMBER>.log`로 현재 build의
sanitized log 하나만 archive합니다. wildcard로 persistent workspace의 과거 log를 다시
archive하지 않습니다. 반면 `daily_evidence.sqlite3`는 의도적으로 누적되는 원장이라 30개
build artifact만 남겨도 DB 내부의 과거 금융 evidence가 자동 삭제되지는 않습니다. 이 DB의
row retention·최소권한 접근·암호화 backup 정책은 build log와 별도로 운영해야 합니다.

## Slack 리포트 계약

정상 통합 리포트는 `pb-portfolio/v2` marker와 `COMPLETE`, `tz=<IANA timezone>`을
fallback text와 summary footer에 기록합니다. v2는 계정별 `author_name` + 첫 text 줄의
`$total (Position: $position, Cash: $cash)` 형식이며 **현재 6계정 전체**만 허용합니다.
금액은 모든 sink가 같은 canonical cent 계약을 사용합니다. authoritative `total`과
`position`을 각각 `ROUND_HALF_UP`으로 cent에 맞춘 뒤 `cash = total - position`으로
도출하며, 음수·비유한 값·원본 대사 허용 범위 초과는 전송 전에 거부합니다. 따라서
Slack, Supabase account rows와 portfolio total은 정확히 같은 식으로 대사됩니다.
수집 실패 알림은 별도 `pb-portfolio/error-v1` marker를 사용하고 정상 리포트와 섞지
않습니다. `slack-data-collector`는 이 계약과 과거 4계정 fields 형식(v1)을 모두 읽습니다.

### Supabase URL과 Secret key 얻기

이 작업에는 anon key, publishable key, legacy service role JWT가 필요하지 않습니다.

1. [Supabase Dashboard](https://supabase.com/dashboard)에서 프로젝트를 선택합니다.
2. **Project Settings → API Keys**로 이동합니다.
3. Project URL을 `SUPABASE_URL`로 사용합니다.
4. **Secret keys**에서 `sb_secret_...` 형식의 키를 생성하거나 복사해 `SUPABASE_SECRET_KEY`로 사용합니다.

Secret key는 RLS를 우회할 수 있는 서버 전용 키입니다. `NEXT_PUBLIC_` 같은 공개 변수에 넣거나 Git, Jenkinsfile, 빌드 로그에 기록하면 안 됩니다. 자세한 키 설명은 [Supabase API key 문서](https://supabase.com/docs/guides/api/api-keys)를 참고하세요.

다음 두 키는 이름이 비슷하지만 권한이 다릅니다.

```text
잘못된 값: sb_publishable_...  # 브라우저/anon 권한, DB 적재 불가
필요한 값: sb_secret_...       # Jenkins 같은 서버 전용
```

`SUPABASE_SECRET_KEY`라는 환경변수 이름만 맞추는 것으로는 충분하지 않습니다. 값 자체가 반드시 `sb_secret_...` 형식이어야 합니다. 보안을 위해 `pb_*` 테이블에 anon/publishable 권한을 추가하는 방식으로 해결하지 않습니다.

### 필수 Supabase schema/RPC migration

운영 전 SQL Editor에서 다음 순서로 적용합니다.

1. `slack-data-collector/sql/pb_portfolio_schema.sql`
2. `slack-data-collector/sql/pb_portfolio_history_v2.sql`

두 번째 migration의 `pb_portfolio_writer_preflight_v2`와
`pb_write_complete_portfolio_snapshot_v2`는 선택 사항이 아닙니다. writer는 여섯 계정
balance와 portfolio total을 단일 Postgres transaction으로 쓰며 RPC가 없으면 unsafe
multi-request fallback 없이 종료합니다. `check-supabase`도 read-only preflight RPC를
호출하므로 Jenkins가 Data API 조회나 Slack 전송 전에 migration 누락을 탐지합니다.

## Jenkins 권장 설정

### Credentials 등록

**Manage Jenkins → Credentials → System → Global credentials → Add Credentials**에서 다음 Secret text를 등록합니다.

| Credential ID | 내용 |
|---|---|
| `polymarket-slack-webhook` | Slack Incoming Webhook URL |
| `polymarket-supabase-secret-key` | `sb_secret_...` 형식의 Supabase Secret key |
| `polymarket-golden-apple-1-address` | 첫 번째 golden-apple 주소 |
| `polymarket-golden-banana-address` | golden-banana 주소 |
| `polymarket-golden-cherry-address` | golden-cherry 주소 |
| `polymarket-golden-apple-2-address` | 두 번째 golden-apple 주소 |
| `polymarket-golden-eco-address` | golden-eco 테스트 슬롯 주소 |
| `polymarket-golden-fox-address` | golden-fox 테스트 슬롯 주소 |

Freestyle job이라면 Credentials Binding에서 위 값을 각각 `SLACK_WEBHOOK_URL`, `SUPABASE_SECRET_KEY`, `ACCOUNT_*_ADDRESS` 환경변수에 연결합니다. Project URL은 비밀값이 아니므로 job 환경변수에 직접 설정할 수 있습니다.

`ACCOUNT_<숫자>_NAME`/`ACCOUNT_<숫자>_ADDRESS` 쌍은 숫자 상한 없이 동적으로
탐색하며 슬롯 번호가 중간에 비어 있어도 된다. `check-supabase`는 단순 행 수가 아니라
중복 이름 처리 후의 6개 표시 이름뿐 아니라 각 표시 이름의 stable `account_id`가
고정 계약과 정확히 일치하는지 검증한다. 하나라도 누락·미등록·교차 매핑되면 데이터
조회와 Slack 전송 전에 실패한다. NAME/ADDRESS 중 한쪽만 있는 추가 slot도 무시하지
않고 설정 오류로 종료한다.

```bash
export SUPABASE_URL=https://your-project-ref.supabase.co
export REPORT_TIMEZONE=Asia/Seoul
```

그 다음 기존 실행 명령을 그대로 사용합니다.

```bash
cd ./daily-report
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run python ./daily_report.py check-supabase
/Users/jongwoopark/.local/bin/uv run python ./daily_report.py run
```

연결 점검이 성공하면 `Supabase 연결/계정 계약 확인 성공 - 계정 카탈로그: 6개`가 출력됩니다. 이 명령은 계정 mapping과 atomic writer RPC contract를 읽기 전용으로 확인하며 Slack을 보내거나 DB를 수정하지 않습니다.

Pipeline job은 저장소의 `daily-report/Jenkinsfile`을 Script Path로 사용합니다. Secret 값은 Jenkins Credentials에서만 주입됩니다.

### `.env` 파일을 Jenkins 서버에 복사하는 방식

Credentials Binding을 사용할 수 없다면 Jenkins 계정만 읽을 수 있는 `.env`를 `daily-report/.env`에 둡니다.

```bash
chmod 600 daily-report/.env
```

`.env`는 `.gitignore` 대상입니다. Workspace 정리나 새 checkout 때 삭제될 수 있으므로 Jenkins 외부의 제한된 경로에서 실행 시 복사하는 방식이 안전합니다.

## 실행 명령

```bash
# 실제 Slack 전송 + Supabase 적재
uv run python daily_report.py run

# 월간 형식 강제: Slack 형식만 달라지고 DB 적재는 동일
uv run python daily_report.py --monthly run

# Slack/Supabase 전송 없이 API 조회와 로컬 evidence 적재만 확인
uv run python daily_report.py --simulate run

# 키/DB 읽기 권한과 6계정 env-catalog 일치 확인 (Slack/DB 수정 없음)
uv run python daily_report.py check-supabase
```

매월 1일에는 별도 플래그가 없어도 월간 Slack 형식을 사용합니다. 같은 날 작업이 두 번 실행되면 DB는 뒤에 실행된 값으로 upsert됩니다.

## 검증

```bash
uv run --extra dev pytest tests
uv run --extra dev ruff check daily_report.py src tests
```

Supabase SQL Editor에서는 최근 적재 결과를 다음과 같이 확인할 수 있습니다.

```sql
select *
from public.pb_daily_portfolio_totals
order by report_date desc
limit 7;

select *
from public.pb_daily_algorithm_balances
order by report_date desc, account_id
limit 28;
```

## 보안 체크리스트

- `.env`, Secret key, service role key, Slack webhook을 커밋하지 않습니다.
- Jenkins Console Output에서 `env`, `printenv`, `set -x`를 사용하지 않습니다.
- Secret key는 Jenkins Secret text 또는 권한 `600`인 `.env`로만 제공합니다.
- Secret이 노출되면 Supabase Dashboard에서 새 키를 만든 뒤 기존 키를 폐기합니다.
- `--simulate`는 Slack과 Supabase 쓰기를 생략하지만 sanitized local evidence는 남깁니다.

## 문제 해결

- **필수 Supabase 환경변수가 없습니다**: `SUPABASE_URL`, `SUPABASE_SECRET_KEY` 주입 여부를 확인합니다.
- **`permission denied for table pb_algorithm_accounts` / HTTP 401**: `SUPABASE_SECRET_KEY` 값이 `sb_publishable_...`인지 확인합니다. `sb_secret_...` 키로 교체하고 `check-supabase`를 실행합니다. anon 권한을 추가하지 않습니다.
- **`SUPABASE_SECRET_KEY에 sb_publishable_... 키가 설정되었습니다`**: 변수 이름은 맞지만 값 종류가 잘못된 상태입니다. Dashboard의 Secret keys에서 서버 키를 가져옵니다.
- **일부 DB 계정의 리포트가 없습니다**: `ACCOUNT_*` env 설정과 DB 카탈로그(`pb_algorithm_accounts`)가 정확히 일치하는지 확인합니다. 계좌를 추가/제거할 때는 env와 카탈로그 행을 같은 시점에 반영해야 합니다.
- **Jenkins 이름을 찾지 못했습니다**: 중복 `golden-apple` 계정의 순서가 1번과 4번인지 확인합니다.
- **`atomic snapshot RPC preflight 실패`**: `pb_portfolio_history_v2.sql` migration과 function의 `service_role` EXECUTE 권한을 적용한 뒤 `check-supabase`를 다시 실행합니다. writer는 일부 행 적재 fallback을 하지 않습니다.
- **월간 실행에서 중복 행이 생김**: 날짜가 기본키이므로 중복 행은 생성되지 않습니다. 최신 값으로 갱신됩니다.
