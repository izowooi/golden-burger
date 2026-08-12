# 011 — Jenkins Clean 옵션 및 전략 잡 구성 감사 — 2026-08-12

작성일: 2026-08-12

대상: Jenkins의 22개 전략 잡 전체, 특히 `polybot-queen` / `polybot-king`

## 0. 결론

```text
Queen/King clean 제거: 확인 완료
Queen/King 실제 timer: H/5 확인 완료
Queen/King DB lineage 지속: 첫 자연 실행으로 확인 완료
동일한 clean 실수: polybot-cat, polybot-dog 2개에서 발견
나머지 20개 clean: 없음
Jenkins 수정: 수행하지 않음
DB/log 재동기화: 수행하지 않음(이번 요청은 구성·실행 확인 범위)
```

Queen과 King의 수정은 정상 반영됐다. 두 잡 모두 실제 `TimerTrigger`가
`H/5 * * * *`이고, Git SCM의 `CleanBeforeCheckout`가 없어졌으며,
`concurrentBuild=false`다. 운영자가 실행한 수동 build와 뒤이은 timer build가 성공했고,
두 번째 cycle이 첫 cycle의 snapshot을 읽은 사실도 console에서 확인됐다.

같은 Clean 실수는 `polybot-cat`, `polybot-dog`에 남아 있다. 두 잡 모두 현재 timer가 없어
지금 당장 10분마다 데이터를 지우지는 않지만, timer를 다시 켜면 checkout 때마다
`git clean -fdx`가 실행되어 Papaya DB와 bot log가 매번 새로 만들어진다.

## 1. Evidence boundary

- Jenkins: `http://192.168.50.23:8080`
- Config·build 관측: 2026-08-12 21:04–21:14 KST
- 방법: `$inspect-jenkins-job`의 익명 read-only API/config/민감정보 redaction을 사용하고,
  config XML의 cleanup extension·timer·concurrency를 구조적으로 재검사했다.
- 감사 대상: 사용자가 지정한 22개 전략 잡. Jenkins 전체 30개 중 report, disk monitor,
  crawler 등 비전략 잡은 제외했다.
- 이번 작업에서는 Jenkins 설정, workspace, DB, log를 수정하지 않았다.
- 성과 회고가 아니라 배포·운영 구성 감사이므로 `daily-rsync`는 실행하지 않았다.

## 2. Queen / King 수정 확인

| 항목 | Queen 24h | King 12h |
|---|---|---|
| Jenkins job | `polybot-queen` | `polybot-king` |
| Runtime | `queen-live-24h` | `queen-live-12h` |
| 실제 TimerTrigger | `H/5 * * * *` | `H/5 * * * *` |
| `CleanBeforeCheckout` | 없음 | 없음 |
| shell cleanup | 없음 | 없음 |
| Concurrent build | false | false |
| Config SHA-256 | `8f9ae3e31a23…` | `d082f139d4eb…` |
| 수동 재가동 | `#1702 SUCCESS`, 99.0초 | `#1701 SUCCESS`, 99.4초 |
| 첫 timer build | `#1703 SUCCESS`, 258.1초 | `#1702 SUCCESS`, 231.1초 |
| 두 번째 timer build | `#1704 SUCCESS`, 99.4초 | `#1703 SUCCESS`, 248.0초 |

수동 첫 cycle은 양쪽 모두 snapshot 54개를 저장했고 entry funnel에
`prior_snapshot_missing: 6`이 있었다. 첫 timer cycle은 같은 DB에 snapshot 73개를
추가했으며 `prior_snapshot_missing`이 사라지고 `first_crossing_already_observed: 6`으로
바뀌었다. 이는 checkout이 DB를 지우지 않았고 직전 snapshot lineage가 다음 cycle까지
보존됐다는 직접적인 운영 증거다. 두 cycle 모두 candidate/BUY는 0이었지만, 이 짧은 구간은
전략 성과나 gate 엄격도를 판단하는 표본이 아니다.

`polybot config --job ...` 출력에는 `Simulation: True`와 `trades_sim.db`가 보이지만,
실제 다음 명령 `polybot run --live`의 run audit는 `mode=live`, bot 초기화는
`simulation=False`였다. 현재 `config` subcommand 자체가 `--live`를 받지 않기 때문에 생기는
혼동이며, 실제 주문 cycle이 simulation으로 실행된 것은 아니다. 다만 이 preflight 출력은
live DB/mode를 검증하지 못한다.

## 3. 왜 15분 또는 자연 실행 2회를 말했는가

두 가지 목적이 섞여 있었다.

1. 전략 논리상 한 시장의 상향 교차는 `직전 persisted snapshot`과 `현재 snapshot` 두 점이
   있어야 판정된다. 새 DB의 첫 cycle은 baseline을 저장할 뿐이고, 두 번째 cycle부터
   `prior < 0.90 <= current`를 계산할 수 있다.
2. 운영 검증상 timer가 두 번 자연 발화하면 description만 바뀐 것이 아닌지, clean이 다시
   DB를 지우는지, 5분 runtime/queue가 실제로 반복 가능한지를 함께 확인할 수 있다.

따라서 **반드시 timer build만 두 번이어야 하는 것은 아니다.** 이번처럼 수동 1회가
성공적으로 snapshot을 저장했다면 그다음 자연 timer 1회만으로 lineage 지속은 검증할 수
있다. `15분`은 `H/5`에서 2–3회의 발화 기회를 확보하는 보수적인 관측창일 뿐 전략
parameter나 주문 선행조건이 아니다. 이번에는 console evidence만으로 이미 persistence를
확인했으므로 즉시 `daily-rsync`를 다시 할 필요는 없다.

## 4. 22개 전략 잡 감사 결과

| Jenkins job | 전략 / 모드 | 실제 timer | Cleanup | 판정 |
|---|---|---|---|---|
| `polybot-cat` | Papaya 24h / live | 없음 | **CleanBeforeCheckout** | 수정 필요, 현재 자동 실행 아님 |
| `polybot-dog` | Papaya 72h / live | 없음 | **CleanBeforeCheckout** | 수정 필요, 현재 자동 실행 아님 |
| `polybot-queen` | Queen 24h / live | `H/5` | 없음 | 정상 재가동 |
| `polybot-king` | Queen 12h / live | `H/5` | 없음 | 정상 재가동 |
| `golden-pomegranate` | accountless research | `H/15` | 없음 | 정상 |
| `polybot-cherry` | Elderberry / live | `H/5` | 없음 | 정상 |
| `polybot-eagle`, `polybot-fox` | Blueberry A/B / live | `*/5` | 없음 | 정상 |
| `polybot-kiwi-a`~`d` | Kiwi A/B/C/D / simulate | 분산된 5분 | 없음 | 정상 |
| `polybot-lime`, `polybot-wolf`, `polybot-fruit` | Melon / live | `H/5` | 없음 | 정상 |
| `polybot-red` | Date / close-only | `H/5` | 없음 | 의도된 폐쇄 운용 |
| `polybot-bear`, `polybot-eco`, `polybot-tiger` | Quince / live | `H/5` | 없음 | 정상 |
| `polybot-shadow` | Blueberry / shadow | `H/5` | 없음 | 정상 |
| `polybot-yellow`, `polybot-orange` | Cherry / live | `H/5` | 없음 | 정상 |

추가 확인 사항:

- 22개 모두 `disabled=false`, `buildable=true`, `concurrentBuild=false`다.
- Cat/Dog 이외 20개에는 SCM/build-wrapper cleanup이 없고, 22개 어느 shell에도
  `git clean`, recursive `rm`, `find -delete`, `cleanWs`, `deleteDir`가 없다.
- 관측 시점 scheduled 잡의 최신 완료 build는 모두 `SUCCESS`였다. Dog의 최근 5개 중
  `#3331` 한 건은 `database or disk is full` 실패였으나 이후 `#3335 SUCCESS`다.
- `polybot-orange`의 실제 실행은 live `main.py run --yes-only`다. `--simulate` 줄은
  주석이므로 simulation으로 분류하면 안 된다.
- 같은 환경변수를 중복 선언한 잡은 있지만 모두 같은 값이었고 상충하는 중복값은 0개다.

## 5. 별도로 발견한 위험과 개선 후보

### CRITICAL — 익명 config와 inline signer key의 조합

16개 live/close-only 잡이 private key와 funder를 Jenkins shell에 inline으로 저장하고,
Jenkins `config.xml`은 로그인 없이 읽힌다. Inspector 결과는 값을 가렸지만 원본 Jenkins
설정은 그렇지 않다. 대상은 `polybot-cat`, `polybot-dog`, `polybot-queen`, `polybot-king`,
`polybot-cherry`, `polybot-eagle`, `polybot-fox`, `polybot-lime`, `polybot-red`,
`polybot-bear`, `polybot-eco`, `polybot-wolf`, `polybot-fruit`, `polybot-tiger`,
`polybot-yellow`, `polybot-orange`다.

특히 Queen, King, Red, Orange는 첫 줄 shebang과 secret 참조 전 `set +x`도 없다.
Queen/King console은 실제 `/bin/sh -xe` 실행과 secret export echo를 보여 줬다. 이 signer는
노출된 것으로 간주해 key rotation과 Jenkins Credentials Binding 전환이 필요하다. 이번
요청은 확인만이므로 설정이나 key는 변경하지 않았다.

### HIGH — Cat / Dog는 live 메모와 달리 현재 정기 실행이 아님

두 잡은 description에 `H/10 * * * *`이 있지만 실제 `TimerTrigger`는 없다. 마지막 실행은
2026-08-05 수동 build다. Clean 제거 후 정기 운용 의도에 맞는 timer를 별도로 켜야 한다.

### MEDIUM — 5분 cadence의 runtime 여유가 작을 수 있음

Queen 첫 자연 build는 258.1초로 5분의 86%, King은 231.1초로 77%를 사용했다. 둘 다
현재는 300초 안에 끝났고 concurrency도 꺼져 있어 데이터 충돌은 없다. 다만 Queen 과거
p95가 약 297초였으므로 느린 cycle에서는 다음 timer가 queue에서 기다릴 수 있다. 이는
즉시 오류는 아니며 24시간 동안 queue backlog와 runtime p95를 관찰할 항목이다.

### LOW — 운영 일관성

- Cat, Dog, Red, Yellow, Orange는 `uv sync --frozen`이 아니라 `uv sync`를 사용한다.
- Eagle/Fox, Melon 3개, Quince 3개, Yellow에는 동일값 환경변수 중복 선언이 있다.
- 17개 잡의 SCM URL은 이전 이름 `izowooi/t1`이다. 현재 canonical
  `izowooi/golden-burger`와 같은 HEAD로 정상 redirect되지만 장기적으로 canonical URL이
  더 명확하다.

위 LOW 항목은 이번 Clean 사고나 현재 build 실패의 원인은 아니다.

## 6. 판정

Queen/King은 Clean 제거와 H/5 재가동이 실제로 적용됐고, 각각 두 번의 자연 timer cycle과
DB lineage 보존까지 확인됐다. 지금 추가 parameter를 바꿀 근거는 없다. 즉시 손봐야 할
같은 유형의 구성은 Cat/Dog의 `CleanBeforeCheckout` 두 건이며, fleet 전체에서 더 큰 별도
위험은 anonymous config에 노출된 inline signer credential이다.
