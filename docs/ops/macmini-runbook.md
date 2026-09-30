# Mac mini 운영 런북

## Jenkins
- 서비스: `~/Library/LaunchAgents/homebrew.mxcl.jenkins.plist` (LAN 접근을 위해 `--httpListenAddress=0.0.0.0`).
- **`brew services restart jenkins` 금지** — plist 를 formula 기본값(127.0.0.1)으로 다시 써서 LAN 에서 접속이 끊긴다.
  재시작은 `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/homebrew.mxcl.jenkins.plist && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/homebrew.mxcl.jenkins.plist`.
  원본 백업: `~/.polylab/homebrew.mxcl.jenkins.plist.bak`.
- 잡 정의는 `jenkins/jobs.yaml`, 반영은 MacBook 에서 `uv run python jenkins/sync_jobs.py --apply`.
- 익명 사용자는 잡 삭제 권한이 없다. 잡 삭제는 SSH 로 `~/.jenkins/jobs/<name>` 을 옮긴 뒤 위 방법으로 재시작.
- 레거시 잡 36개 보관: `~/.polylab/jenkins-legacy-jobs-20261001/` (빌드 로그에 키가 있을 수 있어 chmod 700).

## 외장하드와 macOS TCC
- launchd 로 뜬 Jenkins 의 자식 프로세스는 `/Volumes/t7` 파일을 `open()` 할 때 TCC 동의 대기로 **무기한 멈춘다**
  (`ls`, python, uv 모두). sshd 세션은 접근 가능하므로 모든 잡은 `ssh polylab-local /bin/zsh -s` 로 실행한다
  (`~/.ssh/config` 의 `polylab-local` = 127.0.0.1, 키 `~/.ssh/id_ed25519_polylab_local`).
- 같은 이유로 launchd LaunchAgent 로 daemon 을 띄우지 않는다. WS daemon 은 `polylab-stream` 잡이 59분 단위로 감독한다.
- 근본 해결(선택): 시스템 설정 → 개인정보 보호 → 전체 디스크 접근에 java·uv·python 추가.

## 비밀
- `~/.polylab/accounts.env`(계좌 16개), `services.env`(Slack·Supabase), `claude_oauth_token`, `builder/`(redeem 용 builder key). 모두 chmod 600.
- codex 는 `~/.codex` ChatGPT 로그인. 만료 시 `codex login --device-auth`.

## 킬스위치
- `touch /Volumes/t7/polylab/state/KILL` → 모든 신규 진입 중단(청산·대사는 계속). 해제는 파일 삭제.

## 자동 redeem
- tick 이 시간당 1회 실행한다(기본 ON, 끄려면 Jenkins 잡 env 또는 셸에 `POLYLAB_AUTO_REDEEM=0`).
- 대상은 **각 변형의 live 원장이 산 condition 뿐**이다. 레거시·수동 포지션은 건드리지 않는다(사용자가 UI 로 정리).
- SecureClient 는 항상 기존 funder 지갑(`wallet=`)으로만 만들고, 서명 타입과 지갑 분류가 다르면 거부한다.
  2026-10-01 cat 계좌로 감독 하 검증(지갑 일치, builder key `~/.polylab/builder/cat.json` 생성).

## 거래 범위
- 수집기는 연구용으로 넓은 리그를 모으지만, 전략은 `params.leagues`(축구: epl·bun·fl1·lal·sea·mls·unl·ucl·uel)와
  `min_game_volume_usd`(기본 20,000) 를 통과한 경기만 거래한다.
