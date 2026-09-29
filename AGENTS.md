# Golden Burger 모노레포 운영 지침

이 파일은 `/Users/izowooi/git/t1`에 적용한다. 상위 `/Users/izowooi/git/AGENTS.md`의 Git·보안·자율성 규칙을 따른다. 이 저장소의 기존 계층 표기는 **L2=git 루트, L3=직속 프로젝트**다. [workspace 가이드](/Users/izowooi/git/docs/AGENTS_MD_L1_L4_Guide.md)와 일부 `REPOS.md`는 같은 위치를 L3/L4로 부르므로, 숫자보다 **현재 파일과 대상 폴더의 적용 경로**로 우선순위를 판단한다. 원격은 `github.com/izowooi/golden-burger.git`이다.

## 어디를 볼지

| 작업 | 먼저 볼 자료 |
|---|---|
| 특정 `golden-*` 전략·수집기 | 대상 폴더의 `AGENTS.md`, `README.md`, 실제 config와 source |
| 스포츠 실거래 회고 | `.agents/skills/sports-trade-report/SKILL.md`, `docs/retro/EVIDENCE_CONTRACT.md` |
| Jenkins job·runtime 매핑 또는 동기화 | local-only `docs/local/jenkins-job-strategy-inventory.md`(있으면), 현재 Jenkins config, `daily-rsync/README.md` |
| 신규 전략 | `docs/new-strategy-playbook.md`, 대상에 가장 가까운 L3 지침 |
| 공통 계약·대시보드 | `polybot-observability/`, `daily-report/`, `polymarket-dashboard/AGENTS.md` |

`golden-watermelon-live/`, `golden-apricot/`, `golden-plum/`은 현재 스포츠 실거래의 주요 코드 경계다. `golden-coconut/` 등 accountless 수집 자료는 실거래 원장이 아니다. 저장소를 통합하더라도 전략·runtime·epoch의 출처와 경제적 원장은 구분한다. 봇 소유가 입증되지 않은 수동 wallet position은 전략 거래로 편입하거나 청산하지 않는다. `daily-rsync/`는 검증된 운영 자료 사본을 만든다. 그 밖의 전략은 디렉터리와 해당 L3 문서를 찾아 필요한 것만 읽는다. 과거 전체 전략 설명은 [보존본](docs/agent-context/legacy-root-2026-09-26.md)에 있으나 현재 배치나 금액의 권위로 사용하지 않는다.

## 작업 자율성과 저장소 전환

- 사용자 요청의 완료 조건까지 진행한다. 이미 허가된 코드·테스트·commit·push·배포·마이그레이션을 단계마다 다시 승인받지 않는다. 필요한 문서와 검증은 변경의 영향에 맞게 선택하며, 특정 이전 모델에 맞춘 고정 절차를 반복하지 않는다.
- 공용 Polymarket 시장·호가·경기·정산 원본은 중복 저장을 줄여 한 공용 저장소에서 적재·조회할 수 있다. 전략 판단·실험 설정·주문 intent·CONFIRMED fill·fee·포지션·계좌 원장은 전략별 namespace로 소유권과 대사 경계를 보존한다. 물리적 파일 분리를 영구 불변 조건으로 해석하지 않는다.
- 사용자가 승인한 공용 DB 전환은 기존 하위 문서의 독립 DB·migration 금지 설명보다 우선한다. 실행 전 원본과 잡 상태를 기록하고, 전환 후 참조 완전성·재생 경로·실제 쓰기 위치·동시성·재개를 검증한다. 불필요해진 원본은 검증 후 검토 폴더로 이동할 수 있으며, 근거가 남지 않은 데이터를 임의로 삭제하거나 체결로 채우지 않는다.

## 운영 데이터의 권위

- 실제 실행 설정은 **해당 runtime DB의 resolved config와 run provenance**, 현재 Jenkins effective config로 확인한다. 이 파일이나 오래된 메모의 금액·entry·TP·SL을 현재 값으로 가정하지 않는다. 같은 Jenkins job에 sport별 active/close-only child가 공존할 수 있으므로 `source × Jenkins job × strategy × runtime × sport`를 먼저 분리한다.
- 실제 손익은 CONFIRMED BUY/SELL의 수량·가격·수수료와 exact token-aligned resolution 또는 확인된 redemption으로 계산한다. accepted/live 주문, 요청 가격, `trades.realized_pnl`, wallet 입출금, 평가손익, displayed-book simulation은 실제 확정 손익에 넣지 않는다. 미확정·QUARANTINED·fee/reconciliation gap은 0으로 채우지 않는다.
- 회고는 작업 시작 시각에 UTC 반개구간을 고정한다. 기본 cohort는 `config_hash × git_commit × mode × job_name`이다. 해당 L3가 source digest를 계약으로 정하면 `git_commit` 대신 `strategy_source_digest`를 쓰고, runtime·sport·목표 금액이 다른 진입 cohort도 나눈다.
- 먼저 local catalog·기존 verified pin의 기간과 source cutoff를 확인한다. 요청된 회고·검증·마이그레이션에 필요한 자료가 부족하면 그 범위를 밝히고 해당 source를 `daily-rsync scan → plan → sync → verify → pin`한다. 승인된 데이터 이전은 checksum·참조·원본 출처를 기록하며 수행하고 결손을 추정값으로 메우지 않는다. 실제 분석에는 요청 기간의 출처·무결성·cutoff가 확인된 고정 사본만 쓴다. `SOURCE_MISSING`, retention skip, UTC 당일 mutable shard, fee/reconciliation gap은 `daily-rsync/OPERATIONS.md`와 Evidence Contract대로 분리한다. 외장 data root가 없으면 내부 디스크로 fallback하지 않는다.
- 저장된 호가의 체결 가능액은 실제 FOK confirmed fill이 아니다. raw 공백·VPN/Gamma 장애·실패 run을 양끝 가격으로 보간하지 않는다. 품질 제외 구간은 `docs/retro/sports-source-quality-exclusions.json`에서 확인한다.

## 통합 스포츠 recorder의 현행 배치

2026-09-27부터 White만 5종목 full raw를 수집하고 Silver는 비활성 historical이다.
White recorder v5의 운영 storage gate는 사용률90%/최소50GiB이며 실제 config로 재확인한다.
`golden-coconut/OPERATIONS_SINGLE_WHITE.md`가 현재 배치를 설명한다. 이 프로젝트의 기존
AGENTS/README/STRATEGY는 v7 historical manifest의 hash 검증 대상이므로 보존하며,
오래된 배치 설명을 현행 recorder 값으로 사용하지 않는다.

## 수정과 검증

- 각 하위 폴더는 독립 프로젝트다. Python은 해당 폴더의 `uv` 명령, `polymarket-dashboard`는 npm을 사용한다. 해당 L3의 테스트·배포 절차를 우선한다.
- 변경이 영향을 주는 테스트와 실제 운영 경로를 검증한다. 공용 계약 변경은 그 계약을 사용하는 프로젝트까지 범위를 넓히고 `tools/verify_strategy_contracts.py`를 실행한다. 대대적 저장소 전환의 최종 검증은 모든 전환 대상 job·수집기·reader를 포함한다. 의존성이 바뀌지 않은 반복 수정마다 전 프로젝트 환경 재설치나 이미 통과한 무관한 테스트를 반복하지 않는다.
- 실거래 cycle의 관측성·주문 대사는 fail closed로 유지한다. 버그 수정은 증거를 보존하고 테스트한 뒤, 사용자가 이미 허가한 배포 범위라면 결과까지 확인한다. 금액·파라미터는 독립 시간 구간, 손실 꼬리, full-depth와 FOK 성공률을 보고 전략×종목별로 판단한다.
- 신규 전략은 `docs/new-strategy-playbook.md`의 research→simulation→live 검증 경계와 해당 프로젝트의 epoch/credential 계약을 따른다.
- Git staging은 의도한 파일만 지정한다. 기존 사용자 변경은 되돌리지 않고, secret·실 DB·local-only inventory·task summary를 commit하지 않는다. 검증 후 commit/push와 원격 확인은 상위 지침을 따른다.
- 최종 응답 직전 상위 L1의 local-only `task-summaries/YYYY/MM/` 기록을 남긴다.
