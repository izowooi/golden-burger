# 운영과 복구

## 정상 실행

1. `doctor`
2. `scan --job <job>`
3. `plan --job <job> --strategy <strategy>`
4. plan의 파일 수·logical bytes·전송 상한과 양쪽 free space 확인
5. `sync --plan <id>`
6. `verify --job <job>`

처음에는 하나의 job으로 검증한 뒤 저장 profile을 늘린다. 선택하지 않은 job은 상세
build log를 조사하거나 전송하지 않는다.

## 실패 처리

- SSH 실패: local latest와 catalog 완료 상태를 변경하지 않는다.
- remote snapshot 공간 부족: 해당 DB만 실패하고 로그 작업은 재시도할 수 있다.
- rsync 중단: `data/incoming/*.partial`을 유지해 다음 실행에서 재사용한다.
- SHA 또는 `quick_check` 불일치: incoming을 격리하고 기존 latest를 보존한다.
- source 삭제: `source_missing`으로 표시하고 local 파일은 보존한다.
- local free space 50GB 미만: 새 sync와 bundle 생성을 차단한다.

앱은 원격 Jenkins workspace, build, DB, 로그를 삭제하지 않는다. 정리 대상은
`~/.cache/daily-rsync`의 자기 staging뿐이다.

## 보존

로그는 source timestamp가 365일을 넘으면 정리한다. catalog row는 남는다. bundle에
포함된 로그와 pinned DB, bundle 자체는 자동 정리하지 않는다.

```bash
# 항상 먼저 dry-run으로 파일 수와 회수 용량 확인
uv run daily-rsync prune --dry-run

# 확인 후 실제 적용
uv run daily-rsync prune --apply
```

## 실제 자료 확인

```bash
sqlite3 data/catalog.sqlite3 \
  "select kind,status,local_path,remote_size_bytes from artifacts order by synced_at desc;"

sqlite3 data/sources/macmini-m5/jobs/polybot-king/strategies/golden-queen/runtime/queen-live-12h/databases/latest/trades.db \
  "pragma quick_check;"
```
