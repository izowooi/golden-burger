# L2 AGENTS.md — golden-burger 모노레포

이 문서는 `t1`(golden-burger) 모노레포 루트에 적용되는 운영 지침이다.

- 상위 계층: L1 `/Users/izowooi/git/AGENTS.md`(워크스페이스 전역 규칙).
- 하위 직속 프로젝트에 `AGENTS.md`(L3)가 있으면 그 지침을 우선한다.
- 전역 개발 철학·보안·Git·응답·문서화 규칙은 L1을 따르며 여기서 반복하지 않는다. 본 문서는 이 저장소 고유의 인덱싱과 공통 운영에 집중한다.

## 저장소 목적

Polymarket 예측시장 자동매매 전략 봇과, 그 수익을 적재·리포팅·시각화하는 도구, 그리고 별도 주식 신호/대시보드 도구를 한 git 저장소에 모은 폴리글랏 운영 워크스페이스다. remote: `github.com/izowooi/golden-burger.git`.

## 구조 (직속 프로젝트 인덱스)

전략 봇 — Polymarket 자동매매 (Python/uv, `main.py`+`config.yaml`+`src/polybot/`):

- `golden-apple/`: 확률 80% 매수 / 90% 매도 전략. 2개 인스턴스로 운영 → 대시보드의 `GOLDEN-APPLE (1)`·`(2)`. 인스턴스 2의 상수는 저장소가 아니라 Jenkins env override에 있다.
- `golden-banana/`: 모멘텀(85~97% + 골든크로스) 전략.
- `golden-cherry/`: Resolution Momentum(75~92%, `entry_hours_max` 120h) 전략. **자금은 golden-banana 계정에 있고 Jenkins job 이름은 `polybot-yellow`다** — 폴더명·계정명·job명이 모두 다르다. → L3 `AGENTS.md` 참조.

→ 이 3개는 L3 `AGENTS.md`가 없는 상태로 오래 운영됐다. `golden-apple`·`golden-banana`는 여전히 미보유이며 `tools/verify_strategy_contracts.py`의 `PRE_L3_STRATEGIES`가 예외로 처리한다. 전략 코드베이스는 14개다.

→ 계정 slot은 `daily-report`가 `ACCOUNT_<n>_NAME`/`ACCOUNT_<n>_ADDRESS` 쌍을 번호순으로 훑어 동적으로 발견한다 (`daily-report/src/polybot_reporter/account_config.py`). 코드에 상한은 없고, 현재 `Jenkinsfile`·`.env.example`이 **13 slot**을 선언한다. slot→전략 배치는 `slack-data-collector/src/slack_data_collector/portfolio.py`에 11행이 seed되어 있고(golden-eco=honeydew, golden-fox=nectarine, golden-wolf=fig), 나머지는 Supabase 실데이터라 저장소만으로는 확인할 수 없다.

신규 전략 봇 — 대중 심리 기반, 단계적 A/B 검증 예정 (각 폴더 L3 `AGENTS.md`·`STRATEGY.md` 보유, 개요는 `docs/prediction-market-strategy-portfolio.md`):

- `golden-date/`: Conviction Ladder — cherry 고도화 (시간 사다리 진입 밴드 + 모멘텀 게이트).
- `golden-elderberry/`: Panic Fade — favorite 급락 과잉반응 역매수.
- `golden-fig/`: Hope Crusher — 롱샷 페이드 (NO 토큰 매수, 만기 theta 수확).
- `golden-grape/`: Cascade Rider — 완만한 일관 드리프트 + 거래량 가속 편승.
- `golden-honeydew/`: Night Watch — 미국 새벽·주말 무근거 이탈 복원.
- `golden-lime/`: Shock Follow — 거래량 동반 급등 편승 (elderberry와 A/B 쌍).
- `golden-mango/`: Patience Premium — 연환산 캐리 수익률 허들 단일 수식 (settlement discount 수확).
- `golden-nectarine/`: Bottom Fisher — 20일 롤링 최저가 매수 / 5일 보유 (QuantPedia 백테스트 복제).
- `golden-orange/`: Fear Spike Fade — tail 시장 공포 급등 후 NO 매수 (probability neglect).
- `golden-papaya/`: Final Five — 표준 이진 YES의 first observed 0.95 상향 교차를 0.95–0.97에서 매수하고 해결까지 보유.
- `golden-queen/`: Crown Momentum — 표준 이진 YES의 첫 0.90 상향 교차를 0.90–0.94에서 매수하고 0.98 목표/0.85 stop으로 관리. 스포츠 기본 포함.

전략 문서 HTML 버전은 `docs/strategy-pages/`, A/B 회고 절차는 `docs/ab-retro-playbook.md` 참조, 월간 파라미터 회고(전 봇)는 `docs/retro/README.md` 참조.

공통 관측성·리포팅·적재 (Python/uv):

- `polybot-observability/`: 14개 전략의 resolved config/Git/run provenance, CLOB order/fill 대사, 회고 readiness audit와 SQLite online backup.
- `daily-report/`: 선언된 전 계정(현재 13 slot) 잔고를 Slack 보고 + Supabase `pb_*` 적재 (`Jenkinsfile` 보유).
- `slack-data-collector/`: Slack 리포트 이력 수집·정규화·DB 적재.

시각화·도구:

- `polymarket-dashboard/`: 전 계정 잔고/수익률 비교 대시보드 (Next.js/Cloudflare). → L3 `AGENTS.md` 참조.
- `streamlit_proj/`: "Golden Burger" 주식 차트 대시보드 (Streamlit).
- `cloud_run_proj/`: 나스닥·한국 ETF 이평선 신호 알리미.
- `legacy/`: 이평 추세매매 + 이메일·텔레그램 알림 (구버전, `requirements.txt`).
- `tools/`: 저장소 공통 스크립트. `verify_strategy_contracts.py`(14개 전략 공통 계약 검증), `wind_down.py`(전략 전환 시 잔여 주문 취소·포지션 정리 CLI, 절차는 `docs/strategy-wind-down-playbook.md`), `reconcile_positions.py`(봇 DB 오픈 포지션을 지갑 실보유와 대조·정리. 공개 API만 쓰므로 private key 불필요).
- `docs/`: 문서 자산. 위에 인덱싱되지 않은 것으로 `sqlite-storage-maintenance.md`, `strategy-wind-down-playbook.md`, `nectarine-max-positions-retro.md`가 있다.

## 데이터 흐름

봇(Jenkins 실행) → 각 SQLite에 전략 판단 + resolved config/Git/run + order/fill lifecycle 기록 → `daily-report`가 계정 완전성 검증 후 secret-free local evidence, Slack, Supabase(`pb_*`)에 일일 snapshot 적재 → `polymarket-dashboard`가 공통 날짜 **구간** 기준 수익률·freshness·누락·합계 대사를 표시한다.

**공유 저장소는 없다.** 14개 전략 모두 자기 폴더의 `data/<job>/trades.db`만 읽고 쓴다. `golden-honeydew`·`golden-nectarine`은 거래 대상이 아닌 시장까지 포함한 universe 전체 snapshot을 60일 보존하고, `golden-papaya`·`golden-queen`은 `min(설정 진입 유동성, $1k)` 기준으로 자체 archive를 적재한다(이쪽만 보존일수가 config로 조정 가능). "중앙 archive"란 회고 시 분석자가 이 4개 DB를 찾아 참조한다는 **분석 관행**이지 런타임 의존이 아니다 — 경로 예시는 `docs/retro/golden-honeydew.md` 참조. `market_catalog`는 이 4개에만 있다.

GTC 주문의 `live`/`accepted` 응답은 체결이 아니다. 실현 성과는 `order_fills.status='CONFIRMED'`의 실제 size/price와 fee coverage로만 확정한다. `trades.realized_pnl`은 **요청 가격 × 요청 수량**으로 계산되므로 성과 지표로 쓰면 안 된다 — 매도 GTC가 `orderID`만 받아도 `COMPLETED`로 기록된다. 체결된 적 없는 매수는 `TradeStatus.UNFILLED`(유령 포지션), CLOB 카탈로그에서 사라진 주문은 `QUARANTINED`로 종결되며 **둘 다 오픈 노출로 집계**되어 `max_positions`를 소모한다. 계측 배포 전 legacy 구간과 배포 후 구간은 분리하고, evidence gap을 추정값으로 채우지 않는다. 상세 계약은 `docs/retro/EVIDENCE_CONTRACT.md`를 따른다.

## 공통 작업 원칙

- 각 하위 폴더는 독립 프로젝트로 취급한다. 한 폴더 작업이 다른 폴더에 영향을 주지 않게 한다.
- Python 프로젝트는 **uv** 표준을 따른다: `uv sync --frozen` 후 `uv run ...`. (`legacy`만 `requirements.txt` 예외.)
- Node 프로젝트(`polymarket-dashboard`)는 npm을 쓴다.
- 공통 유틸은 2개 이상 실제 사용 사례가 생긴 뒤 고려하고, 먼저 폴더 내부에서 단순 해결한다.
- 실거래 cycle은 관측성 기록 실패 시 fail closed한다. 전략 판단을 바꾸기 전에 `config_hash × git_commit × mode × job_name` cohort와 fill/archive coverage를 확인한다.

## 작업 전 확인

1. 워크스페이스 `REPOS.md`와 본 문서
2. 작업 대상 폴더의 `AGENTS.md`(있으면)
3. 대상 폴더의 `README.md`
4. 대상 폴더의 package/config 파일 (`pyproject.toml`, `package.json`, `config.yaml`)
5. 전략·회고 작업이면 `docs/retro/EVIDENCE_CONTRACT.md`; 새 전략이면 `docs/new-strategy-playbook.md`

## 공통 명령어

폴더별로 다르다. Python은 `uv run <entry>`(golden-* 는 `uv run polybot`), 대시보드는 `npm run <script>`. 전략 공통 계약은 루트에서 `uv run tools/verify_strategy_contracts.py`, 관측성은 `uv run --project polybot-observability pytest polybot-observability/tests`로 검증한다. 상세는 각 폴더 README/AGENTS.md를 따른다.

## CI / 배포

- 전략 봇·`daily-report`: **Jenkins** 실행 (`daily-report/Jenkinsfile`). 루트에 GitHub Actions/GitLab CI 없음.
- `polymarket-dashboard`: Cloudflare Workers로 배포 — 트리거·운영 URL 등 상세는 L3 `AGENTS.md` 참조.

## 검증 기준

- 특정 폴더만 수정했다면 해당 폴더의 검증(lint/test/build)만 수행한다.
- 루트 공통 파일(`.gitignore`, `REPOS.md`)이나 Supabase `pb_*` 데이터 계약에 영향을 주는 변경은 영향 범위를 먼저 확인한다.
- 공통 전략 계약이나 shared observability를 수정하면 14개 전략의 `uv sync --frozen --extra dev`와 test를 모두 실행하고, contract verifier를 통과시킨다.
- 월간 수치 조정·전략 승격 전에 `uv run --project polybot-observability polybot-retro audit --root . --output-dir <경로> --strict`를 실행한다(`--output-dir`은 필수 인자다). `CRITICAL`/`HIGH` evidence issue가 있으면 조정하지 않고 수집·대사부터 복구한다.
- 수치를 조정하기 전에 대상 구간이 단일 cohort인지 확인한다. `strategy_configs` 테이블에 `config_hash`별 전체 config JSON이 남으므로, 여러 cohort가 섞인 구간의 집계로 파라미터를 정하지 않는다.

## 새 서브 프로젝트 추가 기준

1. 기존 폴더와 목적이 겹치지 않는가.
2. naming convention: 전략 봇은 과일 코드네임(`golden-*`), 인프라·도구는 역할/런타임 기반(`daily-report`, `*_proj`).
3. Python이면 uv, Node면 npm 스캐폴드를 맞춘다.
4. 독립 `README.md`와 필요 시 L3 `AGENTS.md`를 둔다.
5. `REPOS.md`와 본 인덱스에 등록한다.
6. `docs/new-strategy-playbook.md`의 research/falsification/backtest, config validation, simulation, run/order/fill/archive, reporting, retro, promotion gate를 모두 충족한다.
7. `docs/retro/golden-<name>.md`를 만들고 `uv run tools/verify_strategy_contracts.py`를 통과한다. unit test만으로 수익성을 주장하지 않는다.

## 주의사항

- 실거래 봇은 `config.yaml`의 `simulation_mode`와 `.env` 실키에 민감하다. 키 취급은 L1 보안 규칙을 따른다. `golden-papaya`·`golden-queen`은 `simulation_mode: true`가 기본이라 실주문을 내지 않는다.
- `POLYMARKET_SIGNATURE_TYPE`은 계정 종류에 따라 반드시 맞춰야 한다: `1`=POLY_PROXY(구형 이메일 계정), `3`=POLY_1271(2026년 이후 신규 계정의 스마트 지갑). 틀리면 CLOB이 `maker address not allowed`로 전 주문을 거절한다. 14개 전략과 `tools/wind_down.py` 모두 이 env를 읽는다.
- Jenkins Freestyle에서 private key를 inline `export`하거나 `sh -x`/`sh -xe`로 노출하지 않는다. Credentials Binding을 사용하고 secret 참조 전부터 `set +x`를 적용한다.
- SQLite DB와 Jenkins artifact는 유일한 backup으로 취급하지 않는다. online backup + SHA-256 manifest를 workspace 밖 내구성 저장소에 복제하고 복구 검증한다.
- 루트 `firebase-debug.log`는 추적되지 않는 잔여 로그다 (정리 권장, 임의 삭제는 하지 않음).
- `streamlit_proj`·`cloud_run_proj`의 기존 `CLAUDE.md`는 L1 `@AGENTS.md` 컨벤션과 다를 수 있다. 정리는 별도 작업으로 다룬다.
