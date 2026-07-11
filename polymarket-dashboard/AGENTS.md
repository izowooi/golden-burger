# L3 AGENTS.md — polymarket-dashboard

이 문서는 `polymarket-dashboard` 프로젝트에 적용되는 작업 지침이다.

- 상위 계층: L1 `/Users/izowooi/git/AGENTS.md`(워크스페이스 전역 규칙), L2 `../AGENTS.md`(golden-burger 모노레포 루트).
- 충돌 시 더 구체적인 본 문서를 우선한다.
- 전역 개발 철학·보안·Git·응답·문서화 규칙은 L1을 따르며 여기서 반복하지 않는다.

## 프로젝트 목적

형제 전략 봇들이 운영하는 알고리즘 계정(현재 6개: apple x2·banana·cherry + 테스트 슬롯 eco(honeydew)·fox(nectarine))의 잔고와 기간 수익률을 한 화면에서 비교하는 대시보드다. Supabase에 적재된 일일 스냅샷을 서버에서 읽어 시각화한다.

## 기술 스택

- Next.js 16 (App Router) + React 19 + TypeScript
- Supabase (`@supabase/supabase-js`, 서버 전용 조회)
- Recharts (잔고·수익률 차트)
- OpenNext + Cloudflare Workers 배포 (`@opennextjs/cloudflare`, `wrangler`)

## 구조

- `src/components/dashboard.tsx`: 필터·차트·KPI·상세 비교 UI (client component)
- `src/lib/analytics.ts`: 기간 필터와 수익률 계산
- `src/lib/data-quality.ts`: 최신 보고 시각, stale 판정, 계정별 결측일, 일일 합계 대사
- `src/lib/*.test.ts`: analytics·data-quality 단위 테스트 (Node test runner + tsx)
- `src/app/api/portfolio/route.ts`: Supabase 읽기 전용 API (Route Handler)
- `src/lib/supabase/server.ts`: 서버 전용 Supabase client
- `src/lib/types.ts`: 응답·도메인 타입
- `src/app/globals.css`: 전역 스타일 (다크 테마)
- `wrangler.jsonc`, `open-next.config.ts`: 배포 설정

## 실행 / 검증

요구: Node.js 22+, npm.

```bash
npm install
npm run dev        # http://localhost:3000
```

analytics와 데이터 품질 계산은 자동화 단위 테스트로 검증한다.

```bash
npm test
npm run lint
npm run typecheck
npm run build
npm audit
```

## 배포

Cloudflare Workers로 **커밋·푸시 시 자동 배포**된다. 운영 URL: https://poly.zowoo.uk/.

- push는 곧 운영 배포다. L1이 운영 배포를 승인 대상으로 규정하므로, push 전 `lint`·`typecheck`·`build` 통과를 확인하고 사용자 승인을 받은 뒤 push한다.
- 로컬 Workers 미리보기·수동 배포: `npm run preview`, `npm run deploy`.
- Secret은 코드에 두지 않고 `wrangler secret put`으로 등록한다.

## 데이터 / 외부 연동

- API는 Supabase 3개 테이블만 읽는다: `pb_algorithm_accounts`, `pb_daily_algorithm_balances`, `pb_daily_portfolio_totals`.
- `SUPABASE_SECRET_KEY`는 RLS를 우회하는 서버 전용 자격 증명이다. client component·브라우저 번들에 절대 노출하지 않는다.
- 환경변수 이름에 `NEXT_PUBLIC_` 접두어를 붙이지 않는다 (붙으면 브라우저로 노출).
- API 응답은 `private, no-store`로 반환한다.

## 환경변수

- `SUPABASE_URL`, `SUPABASE_SECRET_KEY` 두 값만 사용한다.
- 로컬: `.env.local` (`.env.example` 복사). Workers 미리보기: `.dev.vars` (`.dev.vars.example` 복사).
- 실제 값 파일은 커밋하지 않는다. 예제 파일만 추적한다.

## 도메인 규칙 (고정)

- 수익률 = (마지막 총 잔고 − 첫 총 잔고) / 첫 총 잔고 × 100.
- 선택 기간에 존재하는 첫 잔고와 마지막 잔고로 계산한다.
- 선택 계정 합산 KPI는 선택 계정 모두의 데이터가 존재하는 공통 관측일만 사용한다.
- **입출금은 보정하지 않는다.** 자금 이동으로 값이 튄 날짜는 조회 기간에서 제외한다.
- 위 계산식과 미보정 전제는 불변 규칙이다. 변경 시 `README.md`·`analytics.ts`·UI 안내 문구를 함께 갱신한다.

## 데이터 품질 규칙

- 최신 보고 시각은 `pb_daily_portfolio_totals.reported_at`의 최신값을 우선한다. total이 없을 때만 계정별 잔고의 최신 `reported_at`을 사용한다.
- API 조회 시각 기준 최신 보고가 36시간을 초과하면 stale로 표시한다.
- 계정별 결측일은 해당 계정의 첫 관측일부터 전체 최신 보고일까지의 calendar day 기준이다.
- 같은 날짜의 전체 total과 계정별 `total_value` 합계 차이가 $0.01을 초과하면 대사 불일치로 표시한다.
- 차트에는 calendar day를 채우고 결측값을 `null`로 유지한다. 결측 구간을 선으로 연결하지 않는다.

## 주의사항

- push = 운영 배포. build 미통과 상태로 push 금지.
- Secret key를 client component·로그·커밋에 노출 금지.
- 입출금 미보정 특성상 자금 이동일이 기간에 포함되면 수익률이 왜곡된다.
