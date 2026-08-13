# Golden Raspberry Operations

## 배포 순서

1. root와 `golden-raspberry` test/build/contract verifier를 통과한다.
2. commit·push 후 세 Jenkins config 원본 SHA를 확인하고 rollback copy를 보관한다.
3. `/Volumes/t7`의 APFS/external/UUID identity, host-side UUID pin과 volume sentinel을
   확인하고 job별 custom workspace를 정확히 분리한다.
4. timer 없이 `polybot-do → polybot-re → polybot-mi`를 한 번씩 실행한다.
5. 각 build에서 외장 workspace preflight, cursor-complete Gamma, 두 token book, SQLite
   quick_check, status/health를 확인한다.
6. timer를 각각 0/1/2 minute offset으로 추가한다.
7. 최소 2회 자연 실행 뒤 duplicate/off-slot/overlap이 없는지 확인한다.

clean workspace/build는 DB를 지우므로 사용하지 않는다. instrumentation patch는 같은 DB에
새 source/config cohort로 남길 수 있지만, confirmatory 30일 시작점은 최종 healthy cohort
첫 complete slot로 다시 고정한다.

현재 confirmatory epoch는
`[2026-08-13T12:00:00Z, 2026-09-12T12:00:00Z)`이며 최초 내부 workspace 자료는
운영 검증 이력이다. 현재 custom workspace는 다음 세 곳이다.

```text
/Volumes/t7/jenkins/polybot-do
/Volumes/t7/jenkins/polybot-re
/Volumes/t7/jenkins/polybot-mi
```

모든 shell은 `scripts/verify_external_workspace.py`를 `uv sync`, DB 생성과 public HTTP보다
먼저 실행한다. 검사는 exact APFS mount, `Internal=false`, volume UUID의 external sentinel과
off-volume host pin 이중 일치, workspace device/canonical path를 검증한다. 통과한 실제
workspace에만 `.daily-rsync-workspace.json`을 쓴다. volume이 빠졌거나 다른 volume이면
수집을 시작하지 않는다.

## Daily Rsync

각 source는 `Jenkins job × golden-raspberry × runtime job`으로 분리한다.

local-only `daily-rsync/config.local.toml`에는 내부 root와 외장 root를 모두 allowlist한다.

```toml
remote_workspace_roots = [
  "/Users/jongwoopark/.jenkins/workspace",
  "/Volumes/t7/jenkins",
]
```

```bash
cd ../daily-rsync
uv run daily-rsync scan --job polybot-do
uv run daily-rsync scan --job polybot-re
uv run daily-rsync scan --job polybot-mi
# scan 결과로 job별 별도 plan을 만든 뒤 sync한다.
uv run daily-rsync verify --job polybot-do --strategy golden-raspberry
uv run daily-rsync verify --job polybot-re --strategy golden-raspberry
uv run daily-rsync verify --job polybot-mi --strategy golden-raspberry
```

분석에는 catalog가 가리키는 verified DB 절대 경로와 SHA-256만 사용한다. Jenkins console
log retention skip을 bot log coverage로 오해하지 않는다. canonical DB의
`PRAGMA journal_mode`는 `delete`여야 하며, snapshot manifest의
`snapshot_journal_mode`도 `delete`인지 확인한다.

## 24시간 뒤 요청 문장

다음 문장을 그대로 요청하면 된다.

> polybot-do, polybot-re, polybot-mi를 daily-rsync로 다시 동기화하고 golden-raspberry
> Queue Echo의 첫 24시간 collection health를 검증해줘. 아직 수익성 판정이나 파라미터
> 튜닝은 하지 말고, 세 hash shard의 cadence/off-slot/duplicate, Gamma terminal cursor,
> YES·NO book pair와 raw payload coverage, cohort/source digest, DO·RE·MI decision lineage,
> 60~75분 follow-up 및 neutral/opposite control missingness, DB quick_check와 저장공간 증가량을
> 확인해줘. HIGH/CRITICAL evidence gap이 있으면 원인을 고쳐 Jenkins 재배포와 자연 실행
> 재검증까지 해줘.

## 7일 뒤 요청 문장

> golden-raspberry 세 shard를 daily-rsync로 동기화하고 정확한 7 complete UTC day 범위로
> Queue Echo collection health gate를 실행해줘. 수익성은 preliminary diagnostic으로만
> 보여주고 MI promotion 판정이나 threshold 변경은 하지 마. 30일을 계속 수집할 수 있는지
> cadence, pair/outcome/control coverage, runtime p95, cohort 단일성, disk growth를 판정해줘.

## 장애 원칙

- Gamma repeated cursor/page limit/partial page는 run 실패이며 partial sweep을 publish하지 않는다.
- CLOB token 오류는 token-level missingness와 raw request receipt를 남긴다. 95% pair gate와
  same-request pair 100%를 못 넘으면 collection health 실패다.
- follow-up은 +60~75분 창 안의 첫 request 한 번만 사용한다. quote 부재·invalid·depth 부족은
  즉시 censor하며 다음 cycle의 성공 quote로 교체하지 않는다. 창 전에 한 번도 시도하지 못한
  case만 +75분 뒤 `WINDOW_EXPIRED`로 끝낸다.
- credentials 또는 `--live` 거부 실패는 즉시 모든 timer를 중단하는 CRITICAL incident다.
- free space 30GiB 미만/90% 사용은 source fetch 전에 STOP한다. DB나 evidence row를 임의
  삭제해 우회하지 않는다.
