# White independent raw lifecycle v4b-r5

2026-09-07 prospective evidence 보완. r4의 종목·entry/stop/notional/기간·primary v4b
schema와 기존 signal/episode/path/resolution 경제행 계약은 유지한다. 신규 연구 DB
`shadow.db`의 data contract는 `watermelon-independent-raw-lifecycle-v1`이며
별도 application ID / CREATE-only schema다. 기존 primary DB는 ALTER/import/backfill/merge하지 않는다.

동일 live sweep에서 accepted strict whole-game event를 episode 생성 여부와 독립적인
indexed registry에 등록한다. Soccer HOME/DRAW/AWAY YES3, MLB/NBA/NFL/NHL direct2만
대상이며 YES/NO6으로 기존 White universe를 확장하지 않는다. 거래 eligibility나 $5 walk는
raw 보존 gate가 아니다. 기존 core의 순서와 판단 입력을 유지한 뒤 남은 shared network42초 /
cycle50초 예산에서 missing event를 by-ID 조회하고 current source metadata 뒤의 부족한
book을 수집한다. HTTP는 cooperative timeout이며 절대 wall deadline을 보장하지 않는다.
시간 초과와 부가 수집 실패는 FAILED/incomplete로 남기고 parent 성공으로 위장하지 않는다.

Gamma `/events?id=<exact integer>&limit=2`는 live/closed filter 없이 조회하며
[공식 list events API](https://docs.polymarket.com/api-reference/events/list-events)의
형식을 따른다. identifier/condition/token/outcome/결과 구조를 다시 검증하며 예전
metadata는 lookup anchor일 뿐 현재 OPEN, fee, clock의 증거가 아니다. OPEN/closed-unresolved/
빈 응답/명시적 종료/terminal을 구분하고 raw 원응답을 보존한다. source clock은 원문을
저장하고 soccer minute로 다른 종목의 period를 바꾸지 않는다. fee fallback을 관측 fee로
승격하지 않는다. explicit ended와 exact token one-hot/authoritative void를 분리한다.

Book 원본을 parent와 sidecar에서 두 개의 관측으로 복제하지 않는다. parent book은
verified parent pin의 run/request/token/snapshot/hash로만 참조한다. metadata가 core
book 요청보다 늦으면 해당 token도 별도 fresh raw book을 읽거나 budget-deferred로 남긴다.
expected/attempted/received, FULL/bid-only/ask-only/empty/missing/error/미시도를
구분하고 publication을 full-book completeness나 actual fill로 해석하지 않는다.

Parent SUCCEEDED와 sidecar atomic publication을 함께 요구한다. 두 DB는 별도 transaction이며
둘의 snapshot 시간이 같다고 가정하지 않는다. FAILED raw rows도 보존한다. 과거 metadata가
없는 1,916개 book을 이 prospective 보완으로 복원하거나 prior OPEN으로 승격하지 않는다.
새 source cohort를 구분하며 기존 연구의 수익성·threshold·live 승격을 변경하지 않는다.
