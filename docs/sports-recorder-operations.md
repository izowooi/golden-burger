# 스포츠 공통 호가 수집기 운영

2026-09-08 UTC에 `polybot-white`를 Golden Coconut의 새 accountless 수집기로 전환했다.
기존 Watermelon·Coconut v7 자료와 새 자료는 별도 epoch로 보존한다.

## 현재 실행 계약

- 프로젝트: `golden-coconut`
- 별도 진입점: `scripts/sports_recorder.py`
- runtime: `coconut-sports-recorder-1m-v1`
- data contract: `sports-price-recorder-1m-v1`
- source commit: `8de7eb31bbe4729f23be95881b3d1fd88dd540a7`
- source digest: `2d5420b4c985e6fb0b976a64e939a70c28ad1ac2c31087588f58184c0b1c097c`
- config hash: `a34c245a1a8b25c2780a9aea852f50d7ca6b0f2a1a9287636776a73420d44ac8`

기존 `polybot run`은 v7 진입점이다. 새 수집기에 사용하지 않는다. 새 수집기도
credential·wallet·주문·실거래 경로가 없으며 `--live`를 거절한다.

```bash
cd golden-coconut
uv sync --frozen
uv run python scripts/sports_recorder.py config --simulate --job coconut-sports-recorder-1m-v1
```

운영 실행은 기존 Jenkins의 검증된 외장 workspace에서 같은 명령의 `config`를 `run`으로
바꾼다. 프로젝트 자체 venv를 사용하며, 과거 다른 프로젝트의 venv를 운영 의존성으로
사용하지 않는다. 코드·manifest·uv.lock을 함께 확인한다.

## 수집 범위와 시계

축구의 실제 HOME/DRAW/AWAY YES·NO 6개, MLB·NBA·NFL·NHL의 실제 두 팀 moneyline
2개를 수집한다. 가격·유동성·거래량은 수집 진입 조건이 아니다. 일정 10분 전부터
명시적 실제 종료 시각의 10분 뒤까지 1분 원본 호가·잔량·수수료 필드·source clock을
보존한다. 실제 종료 시각이 없으면 최초 explicit ended 수신 시각을 상한으로 표시한다.
일정상 종료 시각을 실제 종료로 쓰지 않는다. 가격 수집이 끝나도 검증 가능한 정산
metadata는 별도로 후속 확인한다.

Jenkins의 매분 예약이 실제로 `:00`에 시작된다고 가정하지 않는다. 실측 지터를 반영해
고정 phase 30초, 대기 없음으로 운영한다. 최대 cycle 50초·요청 42초와 다음 slot 끝까지의
남은 시간을 함께 적용한다. 늦은 실행과 부족한 예산은 실패·미시도로 남기며 백필하지 않는다.

파일은 `data/<runtime>/trades_sim.db`와 `trades_sim_YYYYMMDD.db`다. 물리 날짜의 소유자는
**claim한 slot의 UTC 날짜**다. 자정 직후 호출이 전날 `23:59:30` slot에 속할 수 있으며
HTTP receipt/publication은 다음 날일 수 있다. 자정을 포함해 조회할 때는 시작일 직전
shard도 함께 선택한다. 앱은 부모 인계 상태·날짜·원본 SHA를 검증하고 같은 source/config
cohort끼리만 연결한다.

PROBE는 별도 디렉토리·`observation_mode=PROBE`·별도 config hash로 기록한다.
정규 관측은 `SCHEDULED`다. 앱은 PROBE를 과거 경기 데이터로 편입하지 않는다.

## 배포 검증과 한계

수동 `#22409`는 12.736초, 자연 `#22410/#22411`은 0.708/1.310초로 성공했다.
앞선 늦은 수동 `#22406`은 요청 예산 부족으로 HTTP 0건 실패했고, 원 shell/timer를
복원한 뒤 올바른 slot에서 재시도했다. 이 실패 행도 보존했다.

운영용 frozen 환경의 별도 공개 API 시험에서는 5개 family의 cursor를 완주하고
축구 6개·MLB 2개·NFL 2개의 실제 full book과 원문 SHA·식별·시각을 검증했다.
NBA/NHL은 해당 시험에서 적격 book 대상이 없었다. 초기 정규 실행은 경기 window 밖이라
book 0개였으므로, 실제 경기 전체 수집률·WSS live clock·주말 최대 부하는 아직 별도 확인
대상이다. 실제 경기 검증 전에 Gold/Silver/Grey를 같은 자료라고 판단해 중단하지 않는다.

신규 27개 회귀 테스트와 패키지 build가 통과했다. 기존 v7은 이미 있던 frozen AGENTS.md
SHA 불일치가 있어 실제 원문을 바꾸지 않았다. 그 문서만 과거 동결본으로 맞춘 격리
복제본에서는 기존+신규 158개 테스트가 통과했다. 저장소의 `verify_strategy_contracts.py` 구조 검증도 29개 전략
전체에서 통과했다.

## 저장·검토

5분마다 일정 census를 갱신한다. 현재는 20 event/page, family당 최대 20 page,
응답당 32MiB를 사용한다. cap·cursor 미완주는 실패이며 전체 모집단 성공으로 표시하지 않는다.
시험의 압축 census 2.587MB를 하루 288회 반복하는 경우 약 745MB/day이며, 정규화 metadata·
book·index·실패 보존은 추가 용량이다. 150GiB 여유 공간 및 사용률 80% 미만 guard를 유지한다.

조회는 [로컬 경기 웹앱](sports-local-workbench.md), 실거래 대사는
[스포츠 리포팅 스킬](../.agents/skills/sports-trade-report/SKILL.md)을 사용한다.
DB 동기화는 `daily-rsync scan → plan → sync → verify → pin`의 기존 수동 절차다.
원본 정리·덮어쓰기·이전 epoch 병합이나 주문 실행은 수집기 운영에 포함하지 않는다.
