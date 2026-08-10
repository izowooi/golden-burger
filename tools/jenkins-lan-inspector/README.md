# Jenkins LAN Inspector

사설망 Jenkins 잡을 읽기 전용으로 조회하는 Codex plugin입니다. Jenkins REST API와
`config.xml`, `consoleText`, workspace directory listing을 사용하며, credential처럼 보이는
값은 출력 전에 강제로 마스킹합니다.

현재 기본 Jenkins 주소는 `http://192.168.50.23:8080`입니다. 다른 서버는
`--base-url` 또는 `JENKINS_URL`로 지정합니다.

## 제공 기능

- Freestyle/Pipeline 잡 metadata와 구성 요약
- SCM, branch, cron trigger, builder script 확인
- 최신 또는 지정 build의 redacted console tail
- workspace 디렉터리 목록 확인
- anonymous `config.xml`, inline secret, shell xtrace, plaintext HTTP 위험 탐지

build 실행과 Jenkins 구성 변경은 의도적으로 포함하지 않았습니다. 조회 기능에 mutation을
섞으면 잘못된 자연어 요청이 운영 build로 이어질 수 있으므로, build trigger는 최소권한
token과 CSRF crumb를 갖춘 별도 승인 흐름으로 추가해야 합니다.

## CLI

```bash
cd tools/jenkins-lan-inspector
uv run python skills/inspect-jenkins-job/scripts/jenkins_job.py inspect polybot-yellow
uv run python skills/inspect-jenkins-job/scripts/jenkins_job.py log polybot-yellow --tail 40
uv run python skills/inspect-jenkins-job/scripts/jenkins_job.py workspace polybot-yellow
```

구조화된 출력이 필요하면 subcommand 앞에 `--json`을 붙입니다.

```bash
uv run python skills/inspect-jenkins-job/scripts/jenkins_job.py \
  --json inspect polybot-yellow
```

## Codex에서 묻는 예시

- `$inspect-jenkins-job로 polybot-yellow 잡 구성을 확인해줘.`
- `$inspect-jenkins-job로 polybot-yellow의 최신 build 로그 마지막 100줄을 점검해줘.`
- `$inspect-jenkins-job로 polybot-yellow workspace 루트에 어떤 폴더가 있는지 알려줘.`
- `Jenkins job golden-queen의 cron, shell 명령, 최근 build 상태를 확인해줘.`

plugin을 새로 설치하거나 갱신한 뒤에는 새 대화에서 호출해야 스킬이 확실히 로드됩니다.

## 검증

```bash
uv sync --frozen
uv run python -m unittest discover -s tests -v
```

도구는 Jenkins 응답 원문을 디스크에 저장하지 않습니다. 그래도 Jenkins 자체가 익명
`config.xml` 또는 console log에 secret을 노출하고 있다면 이미 서버 측 유출 상태이므로,
해당 credential을 교체하고 Credentials Binding으로 이전해야 합니다.
