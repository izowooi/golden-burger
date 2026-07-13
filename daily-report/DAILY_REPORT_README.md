# Polymarket Daily Portfolio Report System

> Supabase 일일 적재와 최신 Jenkins 설정은 [README.md](README.md)를 기준으로 합니다.

9개의 Polymarket 매매봇 계좌에 대한 일일 포트폴리오 리포트를 자동으로 생성하고 Slack 및 Supabase로 전송하는 시스템입니다.

## 📋 기능

- **다중 계좌 지원**: 숫자 슬롯 상한 없이 `ACCOUNT_<n>_*` 쌍을 동적으로 탐색하며 현재 9계정을 모니터링
- **포트폴리오 분석**:
  - 현재 포지션 수 및 총 가치
  - Supabase 일별 total snapshot 기준 7일 및 30일 P&L (기간 floor coverage 부족 시 `N/A`)
- **Slack 알림**: 시각화된 리포트를 Slack 채널로 자동 전송
- **Jenkins 자동화**: Cron 스케줄로 매일 자동 실행
- **에러 핸들링**: API 호출 실패 시 자동 재시도 및 에러 알림

## 🏗️ 아키텍처

```
daily_report.py (메인 스크립트)
    │
    ├─> DataAPIClient (Polymarket Data API)
    │   ├─> get_positions()             # limit=500 전체 pagination
    │   └─> get_equity_snapshot()       # authoritative cash/position/equity
    ├─> DailyEvidenceStore              # sanitized COMPLETE/FAILED evidence
    ├─> SupabasePortfolioWriter         # single-RPC atomic 9-account snapshot
    └─> SlackNotifier                   # DB 확정 후 exact 9-account v3 COMPLETE
```

## 📦 설치 및 설정

### 1. 의존성 설치

```bash
uv sync --frozen
```

### 2. 환경 변수 설정

`.env` 파일 또는 시스템 환경변수로 설정:

```bash
# 계좌 1 (golden-apple)
ACCOUNT_1_NAME=golden-apple
ACCOUNT_1_ADDRESS=0x1234567890abcdef1234567890abcdef12345678

# 계좌 2 (golden-banana)
ACCOUNT_2_NAME=golden-banana
ACCOUNT_2_ADDRESS=0xabcdefabcdefabcdefabcdefabcdefabcdefabcd

# 계좌 3 (golden-cherry)
ACCOUNT_3_NAME=golden-cherry
ACCOUNT_3_ADDRESS=0x9876543210fedcba9876543210fedcba98765432

# Slack Webhook URL
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### 3. 디렉토리 구조

```
project/
├── daily_report.py                    # 메인 실행 스크립트
├── Jenkinsfile                        # Jenkins 파이프라인 설정
├── JENKINS_SETUP.md                   # Jenkins 설정 가이드
├── golden-apple/
│   └── src/
│       └── polybot/
│           ├── api/
│           │   ├── data_api_client.py      # Data API 클라이언트
│           │   └── ...
│           └── notifications/
│               └── slack_notifier.py        # Slack 알림 모듈
├── golden-banana/                     # (동일 구조)
└── golden-cherry/                     # (동일 구조)
```

## 🚀 실행 방법

### 로컬 실행

```bash
# 환경 변수 로드
export ACCOUNT_1_NAME=golden-apple
export ACCOUNT_1_ADDRESS=0x...
export ACCOUNT_2_NAME=golden-banana
export ACCOUNT_2_ADDRESS=0x...
export ACCOUNT_3_NAME=golden-cherry
export ACCOUNT_3_ADDRESS=0x...
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# 스크립트 실행
python3 daily_report.py
```

또는 `.env` 파일 사용:

```bash
# .env 파일 생성 후
python3 daily_report.py
```

### Jenkins 실행

1. Jenkins 설정: [JENKINS_SETUP.md](JENKINS_SETUP.md) 참고
2. Jenkins Job 생성 및 Credentials 설정
3. 파이프라인 실행 또는 Cron 스케줄 대기

## 📊 리포트 예시

### Slack 메시지 형식

```
📊 Polymarket Portfolio - All Accounts
Consolidated daily report as of 2026-02-07 09:00:00

💰 Total Portfolio Value: $1,250.50
📈 Total Positions: 15 open
📅 7-Day P&L: +$125.30
📆 30-Day P&L: +$450.75

GOLDEN-APPLE
Value: $450.00    7d P&L: +$45.20

GOLDEN-BANANA
Value: $400.25    7d P&L: +$35.10

GOLDEN-CHERRY
Value: $400.25    7d P&L: +$45.00
```

### 단일 계좌 상세 리포트

```
📊 GOLDEN-APPLE Portfolio Report
Daily portfolio status as of 2026-02-07 09:00:00

💰 Total Value: $450.00        📈 Positions: 5 open
📅 7-Day P&L: +$45.20          📆 30-Day P&L: +$120.50
   (12 trades)                    (45 trades)
🔹 Realized P&L (7d): +$30.00  🔸 Unrealized P&L: +$15.20

🎯 Top Positions by P&L
• Yes: $150.00 (P&L: +$25.00)
• No: $100.00 (P&L: -$5.50)
• Yes: $200.00 (P&L: +$18.30)
```

## 🔍 주요 모듈 설명

### DataAPIClient

Polymarket Data API를 사용하여 계좌 정보를 조회하는 클라이언트입니다.

```python
from polybot.api.data_api_client import DataAPIClient

client = DataAPIClient()

# 포트폴리오 요약 조회
summary = client.get_portfolio_summary(address="0x...")

# 결과:
# {
#     "positions": [...],
#     "total_value": 450.00,
#     "num_positions": 5,
#     "pnl_7d": {
#         "realized_pnl": 30.00,
#         "unrealized_pnl": 15.20,
#         "total_pnl": 45.20,
#         "num_trades": 12
#     },
#     "pnl_30d": {...}
# }
```

**주요 메서드:**

- `get_positions(address)`: 현재 보유 포지션 조회
- `get_trades_by_address(address, after_timestamp)`: 거래 내역 조회
- `calculate_pnl_for_period(address, days_ago)`: legacy 현재 포지션 진단 helper. 일일 리포트의 기간 P&L 근거로 사용하지 않음
- `get_portfolio_summary(address)`: 완전한 포트폴리오 요약

### SlackNotifier

Slack Webhook을 통해 메시지를 전송하는 모듈입니다.

```python
from polybot.notifications.slack_notifier import SlackNotifier

slack = SlackNotifier(webhook_url="https://hooks.slack.com/...")

# 단일 계좌 리포트 전송
slack.send_portfolio_report("golden-apple", summary)

# 다중 계좌 통합 리포트 전송
slack.send_multi_account_report({
    "golden-apple": summary1,
    "golden-banana": summary2,
    "golden-cherry": summary3
})

# 에러 알림 전송
slack.send_error_notification("golden-apple", "API 호출 실패")
```

## ⚙️ 설정 옵션

### 계좌 변경

slot 탐색 자체에는 숫자 상한이 없지만 정상 리포트는 현재 9개 display name/stable
account ID 계약으로 고정됩니다. 추가·삭제·교체는 env만 수정하지 말고 Supabase catalog,
Slack collector, dashboard를 함께 migration해야 합니다. NAME/ADDRESS 중 한쪽만 설정된
slot은 오류입니다.

### P&L 계산 기간 변경

일일 리포트의 7d/30d는 `SupabasePortfolioWriter.get_period_pnl()`이 저장된 total
snapshot의 기간 floor(최대 +1일)와 현재 total을 비교합니다. 다른 기간을 추가하려면
표시 코드뿐 아니라 해당 기간의 baseline coverage와 dashboard 정의를 함께 변경합니다.

### 스케줄 변경

Jenkins에서 Cron 표현식 수정:

```groovy
triggers {
    cron('0 9 * * *')  // 매일 오전 9시
    // cron('0 9,18 * * *')  // 매일 오전 9시, 오후 6시
}
```

## 🐛 트러블슈팅

### 문제: "No module named 'polybot'"

**원인**: Python path 설정 문제

**해결책**:
```python
# daily_report.py 상단에 path 설정 확인
import sys
from pathlib import Path
project_root = Path(__file__).parent / "golden-apple"
sys.path.insert(0, str(project_root / "src"))
```

### 문제: Slack 메시지가 전송되지 않음

**확인 사항**:
1. `SLACK_WEBHOOK_URL` 환경변수 확인
2. Webhook URL 유효성 확인
3. 네트워크 연결 확인

**테스트**:
```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"Test message"}' \
  $SLACK_WEBHOOK_URL
```

### 문제: API Rate Limit 초과

**해결책**:
- 스크립트에 이미 자동 재시도 로직 포함
- 필요시 API 호출 간격 조정

### 문제: P&L 계산이 부정확함

**확인 사항**:
- Polymarket Data API의 `trades` 엔드포인트 응답 확인
- 거래 내역의 `side`, `price`, `size` 필드 검증
- 로그에서 실제 계산 과정 확인

## 📝 로그 확인

### 로그 파일

스크립트 실행 시 자동으로 생성:

```
daily_report_20260207.log
```

### 로그 레벨

- INFO: 일반 정보 (계좌 로드, API 호출 성공 등)
- WARNING: 경고 (일부 API 실패, 재시도 등)
- ERROR: 오류 (API 호출 실패, 설정 누락 등)
- CRITICAL: 치명적 오류 (프로그램 종료)

### Jenkins 로그

Jenkins Job → Build History → Console Output에서 실시간 로그 확인

## 🔒 보안

### ⚠️ 중요 사항

1. **Private Key는 저장하지 마세요**
   - 이 시스템은 읽기 전용입니다
   - Data API는 public API로 Private Key 불필요
   - Wallet Address만 필요

2. **Webhook URL 보호**
   - `.env` 파일을 `.gitignore`에 추가
   - Jenkins Credentials로 안전하게 관리
   - 절대 코드에 하드코딩하지 마세요

3. **Jenkins Credentials 사용**
   - 모든 민감한 정보는 Jenkins Credentials에 저장
   - 환경변수로 주입하여 사용
   - Console Output에서 자동 마스킹

## 📈 향후 개선 사항

- [ ] 이메일 알림 추가
- [ ] 웹 대시보드 구현
- [ ] 히스토리 데이터 저장 (DB 또는 파일)
- [ ] 더 상세한 분석 (시장별 수익률, 승률 등)
- [ ] 알림 조건 설정 (P&L이 특정 임계값 초과 시)
- [ ] 백테스팅 및 성과 분석 도구

## 📚 참고 자료

- [Polymarket API 문서](https://docs.polymarket.com/)
- [Jenkins 파이프라인 가이드](https://www.jenkins.io/doc/book/pipeline/)
- [Slack Incoming Webhooks](https://api.slack.com/messaging/webhooks)

## 💬 문의 및 지원

문제가 발생하거나 개선 사항이 있으면 이슈를 등록해주세요.

---

**Last Updated**: 2026-02-07
