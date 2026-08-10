---
name: inspect-jenkins-job
description: LAN 또는 사설망 Jenkins의 잡 이름을 받아 metadata, config.xml, Freestyle/Pipeline shell, SCM, trigger, 최신 build, redacted console log, workspace 목록을 read-only로 조회한다. Use when the user asks to inspect, 확인, 조회, diagnose, or summarize a Jenkins job/configuration/build log/workspace, especially jobs such as polybot-yellow that permit anonymous access. 민감한 inline environment value를 노출하지 않아야 할 때 사용한다.
---

# Inspect Jenkins Job

Jenkins HTTP API를 bundled script로 조회하고 민감한 값을 마스킹한 결과만 보고한다. raw
`config.xml`이나 raw console text를 직접 출력하지 않는다.

## 기본 흐름

1. 사용자가 지정한 Jenkins URL을 우선한다. 없으면 `JENKINS_URL`, 그것도 없으면
   `http://192.168.50.23:8080`을 사용한다.
2. 이 `SKILL.md`가 있는 디렉터리를 `skill_dir`로 잡는다.
3. 구성 요청에는 다음 명령을 실행한다.

```bash
python3 "$skill_dir/scripts/jenkins_job.py" \
  --base-url "<jenkins-url>" inspect "<job-full-name>"
```

4. 관측 UTC 시각, 익명 접근 상태, job 상태, 최근 build, config SHA-256, SCM, trigger,
   builder script, security finding을 요약한다.
5. 셸의 `cd` 대상과 실행 명령으로 전략·프로젝트 매핑을 판단하되, description이나 job
   이름만으로 추정하지 않는다.
6. description, 주석, 실제 environment override가 충돌하면 각각을 관측 사실로 분리해
   명시한다.

## Console log

사용자가 build log를 요청할 때만 실행한다. 기본은 최신 build의 마지막 80줄이다.

```bash
python3 "$skill_dir/scripts/jenkins_job.py" \
  --base-url "<jenkins-url>" log "<job-full-name>" \
  --build lastBuild --tail 80
```

숫자 build 또는 `lastCompletedBuild`, `lastSuccessfulBuild`, `lastFailedBuild`를 사용할 수
있다. 결과가 길면 tail을 늘리기 전에 필요한 범위를 좁힌다.

## Workspace

사용자가 workspace를 요청할 때 디렉터리 이름만 조회한다.

```bash
python3 "$skill_dir/scripts/jenkins_job.py" \
  --base-url "<jenkins-url>" workspace "<job-full-name>" \
  --max-entries 100
```

하위 디렉터리는 `--path "relative/directory"`로 조회한다. 파일 본문이나 credential
파일은 읽지 않는다.

## 안전 경계

- `PRIVATE`, `SECRET`, `TOKEN`, `PASSWORD`, `API_KEY`, `CREDENTIAL`, seed, mnemonic,
  funder address 계열 값은 항상 `[REDACTED]`로 유지한다.
- HTTP 오류 body, Jenkins credential ID, raw XML을 출력하지 않는다.
- 인증정보를 URL에 넣지 않는다. 익명 접근이 실패하면 우회하거나 credential을 찾지
  말고 실패 endpoint와 HTTP 상태만 보고한다.
- 이 스킬은 read-only다. build trigger, configure write, workspace file write/delete를
  수행하지 않는다. 향후 build 기능 요청은 운영 변경으로 분리하고 사용자 명시 승인,
  CSRF crumb, 최소권한 token, dry-run 가능한 안전 설계를 먼저 요구한다.
- 익명 `config.xml`과 inline secret이 함께 발견되면 CRITICAL로 보고하고 key rotation과
  Jenkins Credentials Binding 전환을 권고한다.
