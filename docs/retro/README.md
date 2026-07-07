# 전략 회고(포스트모템) 마스터 플레이북

> **용도**: 12개 전략 봇 각각을 한 달 주기로 회고해서, 로그·DB 데이터 기반의 **수치적 파라미터 교정안**을 받는 체계.
> **이메일 예약에 넣을 문구 예시**:
> "Claude Code에서: `docs/retro/README.md` 읽고, 지금 운용 중인 슬롯들의 회고를 실행해줘. 각 봇의 Jenkins env 블록은 내가 붙여넣을게."

## 1. 봇별 회고 가이드 인덱스

각 문서는 self-contained다 — 해당 문서 하나 + Jenkins env 블록만 있으면 AI가 회고를 완주할 수 있다.

| 전략 | 문서 | 비고 |
|---|---|---|
| golden-apple | [golden-apple.md](golden-apple.md) | 운영 2계정 (1)/(2) |
| golden-banana | [golden-banana.md](golden-banana.md) | 운영 |
| golden-cherry | [golden-cherry.md](golden-cherry.md) | 운영 + 변형 슬롯(0.85/0.95 yes-only) |
| golden-date | [golden-date.md](golden-date.md) | 테스트 (polybot-red) |
| golden-elderberry | [golden-elderberry.md](golden-elderberry.md) | 테스트 (polybot-cherry 워크스페이스 — 이름 주의) |
| golden-fig | [golden-fig.md](golden-fig.md) | 대기 (유니버스 스윕 수정 전 봉인) |
| golden-grape | [golden-grape.md](golden-grape.md) | 대기 |
| golden-honeydew | [golden-honeydew.md](golden-honeydew.md) | 테스트 (polybot-eco, 계정 golden-eco) |
| golden-lime | [golden-lime.md](golden-lime.md) | 대기 |
| golden-mango | [golden-mango.md](golden-mango.md) | 대기 |
| golden-nectarine | [golden-nectarine.md](golden-nectarine.md) | 테스트 (polybot-fox, 계정 golden-fox) + [max_positions 전용 회고](../nectarine-max-positions-retro.md) |
| golden-orange | [golden-orange.md](golden-orange.md) | 대기 |

## 2. 데이터 아키텍처 — 회고가 가능한 이유

```
각 봇의 trades.db  ── 실제 체결/청산 기록 (무엇을 얼마에 사고 팔았나)
       +
중앙 가격 아카이브 ── "그때 시장 전체가 어떻게 움직였나" (반사실 재생의 원료)
```

- **중앙 가격 아카이브 = nectarine DB의 `market_snapshots`**: 전 시장(유동성 ≥ $10k, ~1,400개)의
  YES 가격을 5분 간격, **60일 보존**. 모든 봇이 같은 시장 유니버스를 스캔하므로
  어떤 봇의 거래든 이 아카이브로 "다른 파라미터였다면?"을 재생할 수 있다.
  - 위치: `find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db"`
  - 보조 아카이브(이중화): honeydew DB (유동성 ≥ $15k, 60일)
- 자체 스냅샷이 없는 apple/cherry도 이 아카이브로 반사실 분석이 가능하다.
- Jenkins 콘솔 로그의 `제외 사유 요약 - reason: count` 라인은 "스캔 병목이 무엇이었나"의 보조 데이터.

### ⚠️ 데이터 유실 주의
DB는 Jenkins 워크스페이스 안에 있다. **"Wipe workspace"를 누르면 한 달치 데이터가 사라진다.**
주기 백업 권장 (맥미니 crontab 등):
```bash
rsync -a --include='*/' --include='trades.db' --exclude='*' \
  /Users/jongwoopark/.jenkins/workspace/ ~/polybot-db-backup/$(date +%Y%m%d)/
```

## 3. 3라운드 교정 프로세스

```
1차: 기본값(또는 임의값)으로 4주 운용
     → 회고 실행 → AI가 수치 근거와 함께 교정안 제시
2차: 제안 수치로 env 교체 후 4주 재테스트
     (cherry처럼 "기본 슬롯 + 제안값 슬롯" 병행 A/B가 이상적)
3차: 2차 결과로 재회고 → 수렴하면 채택, 악화면 롤백/폐기 판단
```

- 파라미터 변경은 전부 **Jenkins env**로만 한다 (코드 수정 불필요 — 각 봇 문서 §1의 env 표 참조).
- **라운드 이력 기록 규칙**: 라운드를 시작/변경할 때마다 해당 봇 회고 MD 맨 아래
  `## 운용 이력` 섹션에 날짜 + env 블록(키 제외)을 붙여넣는다. nectarine만 `cycle_stats`가
  설정값을 자동 기록하고, 나머지 봇은 이 수동 기록이 유일한 이력이다.

## 4. 회고 실행 방법 (한 달 뒤의 나에게)

1. Claude Code를 이 repo(`~/git/t1`)에서 연다.
2. 회고할 봇의 Jenkins job에서 **env export 블록을 복사** (PRIVATE_KEY 줄은 제외).
3. 프롬프트 (봇별 문서 §0에도 동일한 복붙 블록이 있다):

```
docs/retro/golden-<봇>.md 를 읽고 §3(실적 분석)과 §4(반사실 분석)를 실행한 뒤,
§6 표 형식으로 파라미터 교정안을 제시해줘.
- 상관 클러스터(같은 이벤트 파생 시장)는 이벤트 단위로 묶어서 세고,
  초기 백로그 코호트와 정상 신호 코호트를 분리해서 판단해줘.
- 표본이 부족한 결론에는 신뢰도 '낮음'을 명시해줘.
운용 env: [여기에 붙여넣기]
```

여러 봇을 한 번에 하려면: "docs/retro/README.md 의 인덱스에서 테스트 중인 슬롯 전부 회고해줘."

## 5. 모든 회고에 공통 적용되는 주의사항

- **체결 가정 낙관 편향**: 봇들은 GTC limit @ midpoint 접수 = 체결로 기록한다. 실제 지갑 잔고와
  DB가 다를 수 있으니, 회고 시작 시 `uv run tools/wind_down.py status --funder 0x...`로 대사한다.
- **`status = 'UNFILLED'` (2026-07-07 도입)**: 매도 시 지갑 잔고 0으로 거절되어 "매수 GTC가 체결된 적
  없음"이 확인된 유령 포지션. 봇이 자동으로 마킹하고 잔여 매수 주문도 취소한다. **P&L 집계에서 제외**하되,
  월간 UNFILLED 건수와 그 시장들의 이후 가격 흐름(대부분 즉시 반등 = 놓친 승자)은 체결 가정 편향의
  정량 지표이므로 회고 보고서에 반드시 포함하라. 실측 사례: 2026-07-07 fox 계정 DB 124 보유 중 실지갑 55개.
- **NO 가격 근사**: 아카이브 스냅샷은 YES 가격만 저장한다. NO ≈ 1−YES (스프레드 무시).
- **유니버스 편향**: 봇들의 시장 스윕은 "가장 오래된 활성 2100개"만 본다(offset 2100 캡).
  신규 생성 단기 시장이 빠져 있으므로, "시장이 없었다"는 결론은 전략 탓이 아닐 수 있다.
- **명목 n ≠ 유효 n**: 대선 출마 선언 계열 20개는 베팅 1개다. 이벤트 단위로 다시 세라.
- 해결(resolved)된 시장은 스냅샷이 끊긴다 — 최종 정산가는 trades 또는 0/1로 처리.

## 6. 기준 정보

- 체계 구축: 2026-07-07 (스냅샷 60일 보존: nectarine + honeydew, 나머지 봇은 자체 스냅샷 7일 유지)
- A/B 비교 절차(계정 간 비교)는 별도 문서: `docs/ab-retro-playbook.md`
- 전략 설계 문서: 각 `golden-*/STRATEGY.md`
