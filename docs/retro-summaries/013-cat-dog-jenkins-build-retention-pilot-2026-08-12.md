# 013 — Cat·Dog Jenkins build 14일 보존 pilot — 2026-08-12

작성일: 2026-08-12

대상: `polybot-cat`, `polybot-dog`

## 결론

두 Jenkins job에 Build Discarder를 다음 값으로 적용했다.

```text
daysToKeep = 14
numToKeep = -1
artifactDaysToKeep = -1
artifactNumToKeep = -1
```

이는 build 개수 제한 없이, 14일보다 오래된 Jenkins build record·console log를 삭제한다.
Jenkins job workspace의 전략 DB와 bot log에는 영향을 주지 않는다. 오래된 build를 별도
delete API로 즉시 지우지 않았고, Jenkins가 다음 build 종료 때 LogRotator를 실행하게 했다.

## 적용 방법과 안전 검증

- Jenkins: `http://192.168.50.23:8080`, version 2.461
- 적용 시각: 2026-08-12 21:39–21:40 KST
- 형식: `jenkins.model.BuildDiscarderProperty` + `hudson.tasks.LogRotator`
- CSRF crumb를 받은 뒤 두 `config.xml`에 POST했다.
- 적용 전 config SHA-256을 예상값과 대조하고, retention property가 이미 없는 것을 확인했다.
- dry-run에서 `<properties/>` 외에는 바뀌지 않는 것을 canonical XML로 검증했다.
- 저장 후 retention property를 제거한 canonical XML이 적용 전과 같음을 다시 검증했다.
- 둘 중 하나라도 실패하면 변경된 두 job을 원본 XML로 rollback하도록 실행했다. rollback은
  필요하지 않았고 두 POST 모두 HTTP 200이었다.

| Jenkins job | 적용 전 config SHA-256 | 적용 후 config SHA-256 | 다른 config 변경 |
|---|---|---|---|
| `polybot-cat` | `b9eb2f5cc9ce…` | `78ab812b0b8d…` | 없음 |
| `polybot-dog` | `e6b64d7f8a78…` | `a93bf7f8b348…` | 없음 |

두 잡 모두 적용 후에도 live `golden-papaya/papaya`, `H/10`, clean 없음,
`concurrentBuild=false`, 기존 shell·SCM을 유지했다.

## 실제 rotation 확인

- Cat의 설정 적용 후 첫 자연 timer `#3447`은 98.2초로 `SUCCESS`였다.
- 이 build 종료 직후 Cat의 `firstBuild`가 `#1`(2026-07-16 20:48 KST)에서
  `#2462`(2026-07-29 21:51 KST)로 이동했다. 실행 시각 기준 약 14일 경계와 일치하므로
  LogRotator의 실제 삭제 동작을 확인했다.
- Dog의 설정 적용 후 첫 자연 timer `#3339`도 96.7초로 `SUCCESS`였다.
- Dog의 `firstBuild`도 `#1`(2026-07-16 20:54 KST)에서 `#2354`
  (2026-07-29 21:56 KST)로 이동해 같은 14일 rotation을 확인했다.
- Jenkins job API의 기본 build 목록은 최신 100개만 반환하므로 목록 길이 100은 보존 상한을
  뜻하지 않는다. 실제 rotation 여부는 `firstBuild` 이동으로 판정했다.

## Pilot 판정

Cat·Dog 모두 14일 보존 정책의 저장, post-change 자연 build 성공, 실제 rotation을
확인했으므로 pilot은 성공이다. 나머지 전략 job에는 아직 적용하지 않았다.
