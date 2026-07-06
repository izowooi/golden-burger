# Polymarket Strategy Dashboard

Supabase에 적재된 Jenkins 전략 계좌들(현재 6개)의 잔고와 기간 수익률을 비교하는 로컬 대시보드입니다. Next.js App Router의 서버 Route Handler가 Supabase를 조회하므로 비밀키가 브라우저 번들에 포함되지 않습니다.

## 제공 기능

- `GOLDEN-APPLE (1)`, `GOLDEN-BANANA`, `GOLDEN-CHERRY`, `GOLDEN-APPLE (2)`, `GOLDEN-ECO`, `GOLDEN-FOX` 개별 표시/숨김
- 전체 기간 또는 최근 7일·30일·90일, 직접 지정한 기간 조회
- 전략별 총 잔고·포지션·현금 추이 비교
- 잔고 차트와 기간 시작점 대비 수익률 차트 전환
- 포트폴리오 및 전략별 시작 잔고, 종료 잔고, 손익, 단순 수익률 비교
- 데스크톱과 모바일 화면 대응

수익률은 선택 기간에 존재하는 첫 잔고와 마지막 잔고로 다음과 같이 계산합니다.

```text
(마지막 총 잔고 - 첫 총 잔고) / 첫 총 잔고 × 100
```

입금과 출금은 보정하지 않습니다. 자금 이동으로 값이 튄 날짜는 조회 기간에서 제외해야 합니다.

## 구조와 보안

```text
브라우저
  └─ GET /api/portfolio
       └─ Next.js 서버 전용 Supabase client
            ├─ pb_algorithm_accounts
            ├─ pb_daily_algorithm_balances
            └─ pb_daily_portfolio_totals
```

브라우저에는 Supabase 키를 제공하지 않습니다. 서버는 허용된 세 테이블과 컬럼만 조회하고 응답은 `private, no-store`로 반환합니다.

`SUPABASE_SECRET_KEY`는 RLS를 우회할 수 있는 서버 전용 자격 증명입니다. 다음 원칙을 지켜야 합니다.

- `.env.local`과 `.dev.vars`를 Git에 커밋하지 않습니다.
- 키 이름에 `NEXT_PUBLIC_`을 붙이지 않습니다. 이 접두어가 붙으면 값이 브라우저 코드에 포함될 수 있습니다.
- Secret key, legacy service role key를 클라이언트 컴포넌트에 사용하지 않습니다.
- 공개 배포 시에는 아래의 접근 통제 항목을 먼저 적용합니다.

현재 `.gitignore`는 `.env*`, `.dev.vars*`, `*.key`, `*.pem`을 제외하며 값 없는 예제 파일만 추적합니다.

## Supabase 키 준비

이 앱에는 Project URL과 현대식 Secret key 두 값만 필요합니다. anon key, publishable key, legacy service role JWT는 이 구조에서 사용하지 않습니다.

1. [Supabase Dashboard](https://supabase.com/dashboard)에 로그인하고 프로젝트를 선택합니다.
2. 왼쪽 메뉴의 **Project Settings → API Keys**로 이동합니다.
3. **Project URL**에서 URL을 복사합니다. 형식은 `https://<project-ref>.supabase.co`입니다.
4. **Secret keys**에서 `sb_secret_...` 형식의 키를 복사합니다. 필요하면 새 Secret key를 생성합니다.
5. 저장소 안의 예제 파일을 복사합니다.

```bash
cd polymarket-dashboard
cp .env.example .env.local
```

6. `.env.local`에 실제 값을 입력합니다.

```dotenv
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SECRET_KEY=sb_secret_your_server_only_key
```

키 체계에 관한 상세 내용은 [Supabase API key 문서](https://supabase.com/docs/guides/api/api-keys)에서 확인할 수 있습니다.

## 로컬 실행

요구 사항은 Node.js 22 이상과 npm입니다.

```bash
cd polymarket-dashboard
npm install
npm run dev
```

[http://localhost:3000](http://localhost:3000)을 열면 됩니다. 개발 서버의 기본 포트가 사용 중이면 Next.js가 다른 포트를 안내합니다.

코드 검증 명령은 다음과 같습니다.

```bash
npm run lint
npm run typecheck
npm run build
npm audit
```

## API

대시보드는 아래의 읽기 전용 endpoint를 사용합니다.

```text
GET /api/portfolio
GET /api/portfolio?start=2026-06-01&end=2026-06-23
```

`start`와 `end`는 선택 사항이며 `YYYY-MM-DD` 형식입니다. 현재 UI는 한 번에 전체 데이터를 받아 브라우저에서 즉시 기간을 전환합니다. 데이터가 수만 건 이상으로 증가하면 기간별 서버 조회 방식으로 전환하는 것이 적합합니다.

## Cloudflare 배포

이 프로젝트는 서버 전용 Secret key와 Route Handler가 필요하므로 정적 Cloudflare Pages가 아니라 **Cloudflare Workers + OpenNext** 구성을 사용합니다. Cloudflare도 풀스택 Next.js는 Workers를 권장하며, Pages의 Next.js 가이드는 정적 export 용도입니다.

### 1. 로컬 Workers 미리보기

```bash
cp .dev.vars.example .dev.vars
```

`.dev.vars`에 실제 Supabase 값을 넣은 뒤 실행합니다.

```bash
npm run preview
```

### 2. Cloudflare 인증과 Secret 등록

```bash
npx wrangler login
npx wrangler secret put SUPABASE_URL
npx wrangler secret put SUPABASE_SECRET_KEY
```

각 명령이 값을 요청하면 붙여 넣습니다. 값은 `wrangler.jsonc`에 기록되지 않습니다.

### 3. 배포

```bash
npm run deploy
```

배포 설정은 `wrangler.jsonc`와 `open-next.config.ts`에 있습니다. 공식 절차는 [Cloudflare Next.js Workers 가이드](https://developers.cloudflare.com/workers/framework-guides/web-apps/nextjs/)를 참고하세요.

Cloudflare Pages의 정적 export로 바꾸려면 Route Handler를 제거하고 publishable key와 엄격한 RLS 정책을 사용하도록 데이터 계층을 재설계해야 합니다. 현재 앱에서는 서버 키 보호를 우선해 이 방식을 지원하지 않습니다.

## 공개 배포 전 접근 통제

Secret key 자체는 서버에 숨겨지지만 `/api/portfolio`의 응답 데이터는 앱 URL에 접근 가능한 사람에게 보입니다. 개인용 공개 배포라면 다음 중 하나를 적용해야 합니다.

- Cloudflare Access로 앱 전체를 이메일 또는 IdP 로그인 뒤에 둡니다.
- Supabase Auth를 추가하고 Route Handler에서 세션을 검증합니다.
- 사설 네트워크나 Cloudflare Tunnel 내부에서만 제공합니다.

가장 간단한 현재 구성은 Cloudflare Zero Trust의 **Access → Applications**에서 배포 도메인을 Self-hosted application으로 등록하고 본인 계정만 허용하는 것입니다.

## 주요 파일

- `src/components/dashboard.tsx`: 필터, 차트, KPI, 상세 비교 UI
- `src/lib/analytics.ts`: 기간 필터와 수익률 계산
- `src/app/api/portfolio/route.ts`: Supabase 읽기 전용 API
- `src/lib/supabase/server.ts`: 서버 전용 Supabase client
- `.env.example`: 로컬 환경변수 템플릿
- `.dev.vars.example`: Cloudflare 로컬 미리보기 템플릿
- `wrangler.jsonc`: Workers 배포 설정

## 문제 해결

- **환경변수 누락 오류**: `.env.local`의 두 값과 변수명을 확인하고 개발 서버를 다시 시작합니다.
- **500 응답**: Secret key가 현재 프로젝트의 키인지, 세 테이블이 존재하는지 확인합니다.
- **빈 기간**: DB에 실제 보고 날짜가 있는 범위인지 확인합니다.
- **수익률 급등**: 입출금 미보정 결과입니다. 자금 이동일을 피해서 기간을 다시 지정합니다.
