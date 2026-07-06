# L2 AGENTS.md — golden-burger 모노레포

이 문서는 `t1`(golden-burger) 모노레포 루트에 적용되는 운영 지침이다.

- 상위 계층: L1 `/Users/izowooi/git/AGENTS.md`(워크스페이스 전역 규칙).
- 하위 직속 프로젝트에 `AGENTS.md`(L3)가 있으면 그 지침을 우선한다.
- 전역 개발 철학·보안·Git·응답·문서화 규칙은 L1을 따르며 여기서 반복하지 않는다. 본 문서는 이 저장소 고유의 인덱싱과 공통 운영에 집중한다.

## 저장소 목적

Polymarket 예측시장 자동매매 전략 봇과, 그 수익을 적재·리포팅·시각화하는 도구, 그리고 별도 주식 신호/대시보드 도구를 한 git 저장소에 모은 폴리글랏 운영 워크스페이스다. remote: `github.com/izowooi/golden-burger.git`.

## 구조 (직속 프로젝트 인덱스)

전략 봇 — Polymarket 자동매매 (Python/uv, `main.py`+`config.yaml`+`src/polybot/`):

- `golden-apple/`: 확률 80% 매수 / 90% 매도 전략. 상수값만 다른 2개 인스턴스로 운영 → 대시보드의 `GOLDEN-APPLE (1)`·`(2)`.
- `golden-banana/`: 모멘텀(85~97% + 골든크로스) 전략.
- `golden-cherry/`: Resolution Momentum(75~92%, 해결 직전) 전략.

→ 운영 4계정(apple 2 + banana 1 + cherry 1) + 신규 테스트 슬롯 2계정(golden-eco=honeydew, golden-fox=nectarine) = 6개 알고리즘 계정.

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

전략 문서 HTML 버전은 `docs/strategy-pages/`, A/B 회고 절차는 `docs/ab-retro-playbook.md` 참조.

리포팅·적재 (Python/uv):

- `daily-report/`: 전 계정(현재 6개) 잔고를 Slack 보고 + Supabase `pb_*` 적재 (`Jenkinsfile` 보유).
- `slack-data-collector/`: Slack 리포트 이력 수집·정규화·DB 적재.

시각화·도구:

- `polymarket-dashboard/`: 전 계정 잔고/수익률 비교 대시보드 (Next.js/Cloudflare). → L3 `AGENTS.md` 참조.
- `streamlit_proj/`: "Golden Burger" 주식 차트 대시보드 (Streamlit).
- `cloud_run_proj/`: 나스닥·한국 ETF 이평선 신호 알리미.
- `legacy/`: 이평 추세매매 + 이메일·텔레그램 알림 (구버전, `requirements.txt`).
- `docs/`: 문서 자산.

## 데이터 흐름

봇(Jenkins 실행) → 거래·일일 잔고 → `daily-report`가 전 계정 스냅샷을 Slack 송신 + Supabase(`pb_algorithm_accounts`·`pb_daily_algorithm_balances`·`pb_daily_portfolio_totals`) 적재 → `polymarket-dashboard`가 Supabase를 조회해 비교 시각화.

## 공통 작업 원칙

- 각 하위 폴더는 독립 프로젝트로 취급한다. 한 폴더 작업이 다른 폴더에 영향을 주지 않게 한다.
- Python 프로젝트는 **uv** 표준을 따른다: `uv sync --frozen` 후 `uv run ...`. (`legacy`만 `requirements.txt` 예외.)
- Node 프로젝트(`polymarket-dashboard`)는 npm을 쓴다.
- 공통 유틸은 2개 이상 실제 사용 사례가 생긴 뒤 고려하고, 먼저 폴더 내부에서 단순 해결한다.

## 작업 전 확인

1. 워크스페이스 `REPOS.md`와 본 문서
2. 작업 대상 폴더의 `AGENTS.md`(있으면)
3. 대상 폴더의 `README.md`
4. 대상 폴더의 package/config 파일 (`pyproject.toml`, `package.json`, `config.yaml`)

## 공통 명령어

폴더별로 다르다. Python은 `uv run <entry>`(golden-* 는 `uv run polybot`), 대시보드는 `npm run <script>`. 상세는 각 폴더 README/AGENTS.md를 따른다.

## CI / 배포

- 전략 봇·`daily-report`: **Jenkins** 실행 (`daily-report/Jenkinsfile`). 루트에 GitHub Actions/GitLab CI 없음.
- `polymarket-dashboard`: Cloudflare Workers로 배포 — 트리거·운영 URL 등 상세는 L3 `AGENTS.md` 참조.

## 검증 기준

- 특정 폴더만 수정했다면 해당 폴더의 검증(lint/test/build)만 수행한다.
- 루트 공통 파일(`.gitignore`, `REPOS.md`)이나 Supabase `pb_*` 데이터 계약에 영향을 주는 변경은 영향 범위를 먼저 확인한다.

## 새 서브 프로젝트 추가 기준

1. 기존 폴더와 목적이 겹치지 않는가.
2. naming convention: 전략 봇은 과일 코드네임(`golden-*`), 인프라·도구는 역할/런타임 기반(`daily-report`, `*_proj`).
3. Python이면 uv, Node면 npm 스캐폴드를 맞춘다.
4. 독립 `README.md`와 필요 시 L3 `AGENTS.md`를 둔다.
5. `REPOS.md`와 본 인덱스에 등록한다.

## 주의사항

- 실거래 봇은 `config.yaml`의 `simulation_mode`와 `.env` 실키에 민감하다. 키 취급은 L1 보안 규칙을 따른다.
- 루트 `firebase-debug.log`는 추적되지 않는 잔여 로그다 (정리 권장, 임의 삭제는 하지 않음).
- `streamlit_proj`·`cloud_run_proj`의 기존 `CLAUDE.md`는 L1 `@AGENTS.md` 컨벤션과 다를 수 있다. 정리는 별도 작업으로 다룬다.
