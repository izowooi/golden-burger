# 009 — Live strategy 시작일 확인 — 2026-08-12

작성일: 2026-08-12

대상: 계정 메모의 12개 Jenkins job과 현재 strategy/runtime

## 판정 기준

이 문서의 시작일은 Jenkins job 자체의 생성일이나 최초 simulation 날짜가 아니라,
**현재 `Jenkins job × strategy × runtime` 조합에서 처음으로 확인된 live cycle의 시작
시각**이다. 이 시각은 봇이 실제 주문 가능한 mode로 한 cycle을 시작했다는 뜻이며, 최초
주문·최초 체결일과는 다를 수 있다. 표기는 KST(`Asia/Seoul`)를 기준으로 한다.

## 결과

| 계정 메모명 | Jenkins job | Strategy / runtime | 최초 확인 live cycle (KST) | 근거 |
|---|---|---|---|---|
| eagle | `polybot-eagle` | `golden-blueberry/blueberry-live-a-2pp` (berry-2) | **2026-08-05 22:13:37** | 동기화 DB의 첫 live `run_audits` row |
| fox | `polybot-fox` | `golden-blueberry/blueberry-live-b-5pp` (berry-5) | **2026-08-05 22:13:47** | 동기화 DB의 첫 live `run_audits` row |
| cat | `polybot-cat` | `golden-papaya/papaya` (24h) | **2026-07-29 00:59:39** | Jenkins `#2335` sim 다음 `#2336`에서 첫 live `run_audit`; build 성공 |
| dog | `polybot-dog` | `golden-papaya/papaya` (72h) | **2026-07-29 00:59:56** | Jenkins `#2227` sim 다음 `#2228`에서 첫 live `run_audit`; build 성공 |
| queen | `polybot-queen` | `golden-queen/queen-live-24h` | **2026-07-24 22:08:54** | Jenkins `#1` sim 다음 `#2`에서 첫 live `run_audit`; build 성공 |
| king | `polybot-king` | `golden-queen/queen-live-12h` | **2026-07-24 22:15:18** | Jenkins `#1` sim 다음 `#2`에서 첫 live `run_audit`; 동기화 DB도 일치 |
| bear | `polybot-bear` | `golden-quince/polybot-quince-passive` | **2026-08-05 22:15:08** | 동기화 DB의 첫 live `run_audits` row |
| eco | `polybot-eco` | `golden-quince/polybot-quince-nearest` | **2026-08-05 22:15:21** | 동기화 DB의 첫 live `run_audits` row |
| tiger | `polybot-tiger` | `golden-quince/polybot-quince-cross` | **2026-08-05 22:21:37** | 동기화 DB의 첫 live `run_audits` row |
| fox (fruit 계정) | `polybot-fruit` | `golden-melon/polybot-melon-high` | **2026-08-05 22:14:21** | 동기화 DB의 첫 live `run_audits` row |
| lion | `polybot-lime` | `golden-melon/polybot-melon-mid` | **2026-08-05 22:14:35** | 동기화 DB의 첫 live `run_audits` row |
| wolf | `polybot-wolf` | `golden-melon/polybot-melon-low` | **2026-08-05 22:14:45** | 동기화 DB의 첫 live `run_audits` row |

2026-08-12 재조회 시 Blueberry·Melon·Quince 8개 job에는 실제 TimerTrigger가 있었고
live mode였다. 반면 `polybot-cat`, `polybot-dog`, `polybot-queen`, `polybot-king`은
enabled/buildable이지만 TimerTrigger가 없으며, 최신 build는 2026-08-05의 수동 이력이다.
따라서 이 네 날짜는 live 시작 이력이지 현재 정기 실행 중이라는 뜻은 아니다.

## 확인 방법

- 2026-08-12 20:35:26 KST에 12개 job의 익명 Jenkins config를
  `$inspect-jenkins-job` bundled reader로 다시 확인했다. 현재 `cd golden-*`, `--job`,
  `--live`가 위 매핑과 일치했다.
- Blueberry 2개, Melon 3개, Quince 3개는 2026-08-10에 `daily-rsync verify`를 통과한
  전략별 SQLite에서 `mode='live'`인 `run_audits.started_at`의 최솟값을 직접 조회했다.
  과거에 같은 Jenkins job이 다른 전략을 실행한 구간은 DB의 `strategy_name`과 runtime
  경계로 제외했다.
- Papaya·Queen 4개는 local DB coverage가 불완전하므로 Jenkins history의 인접 전환
  build를 사용했다. 각 경우 마지막 sim build와 첫 live build가 연속하며 첫 live
  `run_audit`도 `SUCCESS`로 끝났다.
- `polybot-cat`과 `polybot-dog`의 Papaya simulation 수집은 각각 2026-07-16
  20:48:26와 20:54:36 KST에 먼저 시작됐다. 그러나 계정 운영 메모에는 live 시작일인
  2026-07-29를 기록한다.
- `polybot-queen`과 `polybot-king`도 각각 2026-07-24 22:05:21와 22:10:37 KST에
  한 번의 `queen-sim` build가 먼저 있었고, 같은 날 live runtime으로 전환됐다.

## 메모 정정 사항

- 마지막 Melon low 팔의 Jenkins job은 `melon-low`가 아니라 **`polybot-wolf`**다.
  `polybot-melon-low`는 bot 내부 runtime 이름이다.
- 계정 메모에는 `fox`가 두 번 등장하므로, 운영 기록에서는 반드시 Jenkins job을 함께
  적어 `polybot-fox`와 `polybot-fruit`를 구분한다.
- 사용자가 제공한 이메일 주소는 개인 식별 정보이므로 Git에 남는 이 문서에는 반복하지
  않았다.
