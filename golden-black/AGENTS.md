# L3 AGENTS.md — Golden Black

이 문서는 `golden-black/`에만 적용된다. 상위 모노레포 규칙은 `../AGENTS.md`를 따른다.

## 목적

Golden Black은 스포츠 이진 시장의 고확률 outcome을 exact CLOB ask로 가상 매수하고 one-hot
resolution까지 보유하는 accountless, simulation-only paired experiment다. A군은 `0.94`, B군은
`0.92`이며 두 값 외의 차이를 만들지 않는다. 실제 주문, wallet, realized P&L은 없다.

## 고정 계약

- Data contract: `sports-resolution-paired-v1`.
- Runtime job: `black-shadow-paired`.
- 권장 Jenkins job/workspace: `polybot-black`, `/Volumes/t7/jenkins/polybot-black`.
- Cadence: `H/5 * * * *`; concurrent build 금지.
- Entry window: `[2026-08-21T00:00:00Z, 2026-09-20T00:00:00Z)`.
- Follow-up end: `2026-10-20T00:00:00Z`.
- Exit policy grid: `HOLD_TO_RESOLUTION`, `STOP_0.80`, `STOP_0.70`, `STOP_0.60`.
- Cohort: `config_hash × strategy_source_digest × mode × job_name`.
- Preregistration: `research/frozen-2026-08-20/PREREGISTRATION.md`.

실험 identity를 바꾸면 기존 DB에 섞지 않는다. 새 preregistration, source digest, config hash,
runtime job 또는 DB epoch로 분리한다. Git commit은 provenance이지 cohort key가 아니다.

## Universe와 실행 근사

- Gamma `/events/keyset`의 server-side `tag_slug=sports`, `closed=false`, event liquidity
  `>=10,000`, cumulative volume `>=5,000`, `endDate` 6시간 window를 먼저 적용한다.
- page size 500, 최대 4페이지이며 terminal cursor가 아니면 cycle을 publish하지 않는다.
- nested market에서 sports evidence, strict binary, open/orderbook/accepting, market liquidity와
  volume, endDate를 다시 검증한다.
- 진입은 Gamma probability가 아니라 exact CLOB full book에서 `$5` ask walk가
  `[threshold, threshold+0.01]`에 들어온 최초 관측이다.
- 열린 episode는 원래 share 수량의 displayed bid path와 CLOB market의 unique winner를 기록한다.
- stop은 best bid trigger와 실제 full-depth VWAP를 분리한다. 부분 fill은 채우지 않고 실제
  filled/remaining share와 다음 cycle retry를 기록한다.
- Gamma `endDate`가 실제 경기 종료와 같다는 보장은 없다. `gameStartTime`, phase, 두 clock을
  모두 저장하며 분석에서 이 한계를 숨기지 않는다.

## 안전 규칙

- `--live`, private/funder/signature/API credential은 빈 문자열이어도 파일·DB·HTTP 전에 거절한다.
- lifecycle은 `archive_only`, simulation은 `true`만 허용한다.
- 주문 SDK, signer, order submission code를 추가하지 않는다.
- free space 50GiB 미만, filesystem 90% 이상, overlapping writer, incomplete cursor,
  malformed source, SQLite quick check 실패는 fail closed다.
- Jenkins에 clean/wipe를 넣거나 research DB를 삭제하지 않는다.
- 이 프로젝트 작업으로 다른 live/research Jenkins job을 변경하지 않는다.

## Evidence 계약

- API attempt, gzip raw payload, sweep, market/outcome, exact book/levels, decision, episode, path,
  exit policy, stop attempt/partial/retry/completion, resolution, DQ issue, run/config/source
  provenance, storage metric은 append-only다.
- `OBSERVED` book은 체결이 아니다. 결과는 displayed-book counterfactual로만 부른다.
- closed market이어도 unique one-hot winner가 없으면 resolution으로 만들지 않는다.
- 누락 라벨, 부족한 depth, cursor 실패를 수익 또는 손실로 추정해 채우지 않는다.
- 과거 Pomegranate/Nectarine/Honeydew 결과는 후보 생성 근거일 뿐 이 prospective cohort와
  합쳐 성과를 만들지 않는다.

## 주요 파일

- `config.yaml`, `src/polybot/config.py`: frozen config와 credential boundary.
- `src/polybot/api/gamma_client.py`: bounded sports event keyset discovery.
- `src/polybot/api/clob_client.py`: full books와 resolution reads.
- `src/polybot/collector.py`: paired decisions, paths, resolutions.
- `src/polybot/db/repository.py`: append-only SQLite evidence.
- `src/polybot/analyzer.py`: verified immutable DB의 arm별 분석.
- `OPERATIONS.md`: Jenkins와 daily-rsync runbook.

## 검증

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-20 && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
```

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot config --simulate --job black-shadow-paired
```

수익성 판정은 첫 24시간에 하지 않는다. 첫날은 cadence/cursor/book/DB/storage health만,
7일은 표본과 resolution coverage만 본다. 최초 arm 비교는 30일 entry window가 닫힌 뒤 하고,
follow-up이 끝나지 않은 episode는 censored로 유지한다. Live 구현은 별도 승인·별도 프로젝트와
새 evidence gate가 필요하다.
