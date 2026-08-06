# 2026-08-07 Golden Pomegranate 15분 cadence 확정 amendment

## 관측 근거

`pomegranate-hourly-v1`의 첫 bounded cycle(`#3`)은 3,030 markets와 6,060 outcomes를
25.597초에 수집했고 logical SQLite 크기는 49,168,384 bytes였다. Gamma와 CLOB은 각각
`SUCCESS`였지만 Data API `/trades?takerOnly=true`는 요청한 1시간 window를 무시하고 약 24시간
뒤의 global head 10,000 rows를 반환했다. 따라서 trade component는 `POSSIBLE_GAP`, watermark는
`NULL`이었다.

49.2MB에는 요청 범위 밖 10,000 rows와 per-cycle membership을 normalized fact로 확장한 비용이
포함돼 있다. 이 자료는 요청 window의 거래 evidence가 아니므로 현재 contract는 compressed
sanitized raw payload, request lineage, row/unique/duplicate count와 economic digest만 보존한다.
범위 밖 normalized fact와 membership은 저장하지 않으며 watermark는 계속 fail closed한다.

## cadence 판정

수정 전 크기를 그대로 사용하는 보수적 선형 상한은 다음과 같다.

| cadence | cycles/day | 상한/day | `1.2 × 120일` |
|---|---:|---:|---:|
| 60분 | 24 | 1.18GB | 170GB |
| 15분 | 96 | 4.72GB | 680GB |
| 5분 | 288 | 14.16GB | 2.04TB |

1TB volume은 free 150GiB와 80% stop을 함께 지켜야 하므로 5분 full-census polling은 기각한다.
60분은 스포츠와 단기 시장의 상태 변화를 지나치게 성기게 관측한다. 최종 기본값은 원
preregistration의 **15분**으로 복귀한다. bounds-violation compact evidence 적용 후 실제 증가량은
위 상한보다 작아야 하며 첫 3 cycle과 첫 complete UTC day에서 다시 측정한다.

## 새 cohort와 운영 gate

- runtime job: `pomegranate-15m-v2`
- Jenkins: `H/15 * * * *`
- resolved cadence: `15`
- bounded Gamma envelope: liquidity >= $10,000, cumulative volume >= $2,000,
  end horizon <= 120일
- 기존 `pomegranate-local`, `pomegranate-hourly-v1` DB와 혼합 금지
- `forecast_days_to_stop < 120` 또는 `1.2 × measured daily bytes × 120 > 680GB`이면 timer 중지

15분 polling도 1분 tick capture는 아니다. 골·한타처럼 초단위 event를 연구하려면 broad Gamma
census를 5분으로 가속하는 대신, 별도의 public WebSocket/tick collector를 독립 data contract와
storage budget으로 설계한다.
