# Cat/Dog NFL 계좌 통합 v5 — 운영 배포 전

운영자 요청으로 Cat/Dog에 NFL whole-game direct moneyline을 추가한다. 정규시즌,
플레이오프, Super Bowl은 NFL tag450/sport10/root-series10187/team-league nfl이 정확히
일치할 때 포함한다. NCAA/quarter/half/prop/future/advancement는 제외한다.

NFL은 독립 runtime DB와 `catdog_nfl` profile을 사용한다. A 진입0.96/B0.99, 상한0.999,
손절 max(0.70, 매수가-0.30), 최대 관측6h, 현재 목표5 USDC다. 같은 초기 숫자여도
축구·MLB profile과 다른 객체이므로 이후 종목별 수정한다. 수익성 검증값은 아니다.

Cat/Dog 계좌 잠금은 축구·MLB·NFL 세 DB를 합산한다. open20·최근60초 BUY5 한도를 종목마다
새로 주지 않는다. 실행 선두 종목을 세 slot마다 순환하고 앞 종목 실패 시 뒤 종목 보유 관리도
실행한다. 미확정 체결·수동 보유·수량·수수료 보호는 유지한다.

배포 전 자동 예약을 멈추고 기존 runtime의 `prepare-account --live --job <soccer-runtime>`로
누락된 NFL DB만 생성한다. 기존 축구/MLB DB는 검증만 하고 복사·초기화·종료하지 않는다.
빈 신규 DB가 없으면 신규 BUY는 실패 방어하지만 기존 종목 보유 관리는 계속한다.
이 파일과 로컬 테스트는 실제 배포·체결·1분 내 실행을 의미하지 않는다.
