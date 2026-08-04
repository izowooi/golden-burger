# 2026-08-04 Golden Blueberry 기원 분석과 A/B 사전 등록

## 결론

운영자의 기억인 “3일 이내 확률 급등을 사고 해결 전에 판다”는 코드 계보상
`golden-cherry`(초기 Resolution Momentum)와 가장 가깝다. 2026-02-04에는 24시간 이내
집중과 스포츠 포함 변경이 있었고, 2026-02-05에 12~720시간으로 확장된 이력이 있다.
Blueberry는 현재 운영 Cherry를 그대로 복제하지 않고 원래 72시간 수렴 가설을 소액으로 다시
검정한다.

## 분석 원본

| 자료 | SHA-256 | 범위/용도 |
|---|---|---|
| `golden-cherry/data/default/trades.db` | `e7ee14001048ebc4a8fdd8d0d5cb3ee3ef5394877de371c62a7f3d4f27f6ed0e` | legacy 671 trade 진단 |
| synced Queen `trades.db` | `9ab9731e09ebb44a1ca153803a0dd4f0d79ebefa4ce6a0c76662b2a6f7a14a88` | 2026-07-24~29, 56,354 snapshot 희소성 점검 |

Cherry 사본은 daily-rsync verified catalog 원본이 아니므로 최종 성과 판정에는 사용하지 않는다.
또한 `trades.realized_pnl`은 requested price/size 기반 legacy 값이라 confirmed P&L이 아니다.

## 관찰

- Cherry 상태: COMPLETED 247, HOLDING 246, UNFILLED 102, QUARANTINED 76; order fill row 1,514.
- 주문액: 2026-03 $10 1건, 4월 $1,000 2건, 5월 $1,000 9건, 6월 $500~$5,000,
  7월 $100~$8,000. 소액에서 대형으로 단계 없이 이동했다.
- `$8,000` 주문 cohort의 주문액/표시 유동성 비율은 평균 `57.64%`였다. metadata 수치가
  실제 depth는 아니더라도, 해당 크기가 시장 규모에 비해 비정상적이었다는 직접 경고다.
- 이 사본에서는 스포츠 우위와 운영자가 기억하는 월 10%를 confirmed evidence로 재현하지
  못했다. 이는 기억이 틀렸다는 증명도, 전략이 맞다는 증명도 아니다.
- Queen 5일 snapshot에서 이전 `<0.85`에서 현재 `[0.85,0.93]`로 처음 들어온 fresh crossing은
  50개였다. 직전 대비 +2%p 이상은 34개, +5%p 이상은 14개였다. 유동성/거래량과 spread까지
  보수적으로 겹치면 표본은 훨씬 줄었다.

따라서 1주만으로 P&L winner를 고르면 안 되며, 30일 뒤에도 arm당 confirmed closed 20건을
못 채우면 연장한다.

## 설계 선택

| 선택 | 이유 |
|---|---|
| 진입 `[0.85,0.93]` | 최소 $5 주문의 5.1-share buffer와 목표까지의 여지를 동시에 확보 |
| 목표/손절 `0.97/0.78` | Melon과 공통 baseline을 유지해 새 knob를 늘리지 않음 |
| 72시간 | 운영자가 기억한 “3일 이내”를 직접 검정 |
| 스포츠 포함/in-play | 과거 수익 기억을 배제 근거로 쓰지 않고, 경기 중 급등 가설을 직접 수집 |
| A/B `+2%p/+5%p` | 실제 “치솟음”의 강도 하나만 처치 |
| 유동성·volume `$10k` | $5가 각 지표의 0.05% 이하; 지나친 gate로 표본을 소거하지 않음 |
| `$5` | `$1`은 CLOB 5-share 최소 미달; tail loss를 최소 실행 단위로 제한 |

## 사전 등록

- Arm A: first crossing의 `current - prior >=0.02`.
- Arm B: first crossing의 `current - prior >=0.05`.
- 최초 crossing이 B threshold를 못 넘으면 B에서는 reject로 기록하고 이후 recross로 대체하지
  않는다. 이것이 “강한 최초 급등”의 정의다.
- 두 arm에서 다른 config는 `min_surge` 하나뿐이다.
- 1주: 운영/coverage 점검만. 30일: confirmed fill/fee 기준 primary review.
- cohort는 `config_hash × strategy_source_digest × mode × job_name`; Git commit은 provenance.
- intermediate winner를 보고 threshold, 금액, 시간, category를 바꾸지 않는다.
- arm당 economic drawdown `-$30`이면 신규 진입 중단.

## 재현 명령

```bash
shasum -a 256 golden-cherry/data/default/trades.db
sqlite3 -readonly golden-cherry/data/default/trades.db \
  "select status,count(*) from trades group by status;"
sqlite3 -readonly golden-cherry/data/default/trades.db \
  "select substr(buy_timestamp,1,7),count(*),avg(buy_amount),min(buy_amount),max(buy_amount) from trades group by 1;"
```

Queen crossing 수치는 4.1GB verified backup의 read-only SQL replay에서 얻었다. 최종 회고에는
같은 DB 경로를 하드코딩하지 않고 daily-rsync catalog의 `verify`를 통과한 artifact 경로와
그 시점 checksum을 다시 기록한다.
