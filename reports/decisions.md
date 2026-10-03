# 연구자 결정 기록

`reports/attention.md` 의 질문에 대한 연구자의 답. 회고는 이 파일을 읽어 해당 항목을 "사용자 결정"으로 닫고,
AI 는 이 결정을 전제로 판단한다(같은 질문을 다시 하지 않는다). GitHub 웹에서 직접 추가해도 된다. 형식:

    ## YYYY-MM-DD
    - `<attention 항목 id>` — 결정 내용

## 2026-10-02
- `ai:watermelon-soccer-late-collapse` — take-profit early 를 적용한다. 진입 조건 강화보다 조기 익절을 우선한다. 1분 주기로는 경기 막판 급락에서 손절이 체결되지 않으므로(아일랜드 0.92→0.17, 13분) 막판까지 보유하지 않는다.
- `ai:soccer-events-sample-too-small` — 현재 축적 속도로 축구의 경기 시간 구간별 득점 민감도 분석이 어렵다는 데 동의한다. 이 가설의 논문상 역할은 "막판 변동성이 커서 손절이 무력하므로 전략은 경기 막판까지 들고 가지 않고 조기 익절해야 한다"는 실거래 수익화 논리의 근거다. 과대/과소 평가를 증명해도 급락에는 손절로 대응할 수 없다는 점이 핵심이다.
- `ai:apricot-fruit-tick-inferior` — 백테스트로 더 좋은 파라미터가 확인되면 그 값으로 변경한다(실거래 20건 대기 없이 조기 조정 허용).

## 2026-10-03
- `ai:manual-nfl-scope` — NFL 실거래를 폐기한다. 데이터가 충분히 쌓여 수익이 나는 전략과 파라미터가 확보될 때까지 잠정 중단한다.
- `manual:nfl-scope` — NFL 을 watermelon·plum 실거래 범위에서 제외한다. NFL 전용 전략이나 기존 전략의 NFL 전용 분기 로직이 생기기 전까지 실거래하지 않는다.
- `policy:us-sports` — NBA·NHL(·NFL)은 수익을 내는 전략과 파라미터가 확인될 때까지 수집과 paper 시뮬레이션만 한다(`watermelon-us-paper`, `plum-us-paper`, `cherry-us-paper`). live 전환은 연구자만 결정한다.
