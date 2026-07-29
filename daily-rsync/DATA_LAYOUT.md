# 데이터 구조와 identity

## Identity

Jenkins 표시 이름, 전략 폴더명, runtime `--job`, 계좌 이름은 서로 같은 값이 아니다.
`daily-rsync`는 다음 값을 독립적으로 보존한다.

| 필드 | 예시 | 의미 |
|---|---|---|
| source | `macmini-m5` | SSH/Jenkins 원본 |
| Jenkins job | `polybot-king` | Jenkins build와 console log 소유자 |
| strategy | `golden-queen` | 실행된 코드/전략 |
| runtime job | `queen-live-12h` | 봇 DB와 로그 namespace |
| deployment epoch | build 범위 | job의 전략/계좌 재사용 구간 |
| config cohort | `config_hash` | 실제 resolved parameter 집합 |

따라서 `default` 폴더를 Jenkins job 이름으로 바꾸지 않는다. 실제 Jenkins job이 이미
상위 namespace를 제공하므로 `polybot-yellow/golden-cherry/default`는 다른 job의
`default`와 충돌하지 않는다.

## Local layout

```text
data/
├── catalog.sqlite3
├── plans/
├── incoming/
├── sources/macmini-m5/jobs/
│   └── polybot-king/
│       ├── builds/000000/725.log.gz
│       └── strategies/golden-queen/runtime/queen-live-12h/
│           ├── databases/latest/trades.db
│           ├── databases/latest/manifest.json
│           ├── databases/pinned/<timestamp>/
│           └── logs/2026/07/20260729.log
└── bundles/<bundle-id>/
```

Jenkins build log는 strategy 아래로 복제하지 않는다. job/build가 canonical 위치이고,
catalog가 build와 strategy epoch를 연결한다. 봇 DB와 파일 로그는 원격 strategy/runtime
경로를 보존한다.

## Catalog

`catalog.sqlite3`는 source, job, artifact, build log, sync run, account epoch와 pin을
transaction으로 기록한다. 실제 DB를 합치거나 거래 row를 재작성하지 않는다. 서로 다른
job의 성과 비교는 catalog가 가리키는 독립 DB를 대상으로 수행한다.
