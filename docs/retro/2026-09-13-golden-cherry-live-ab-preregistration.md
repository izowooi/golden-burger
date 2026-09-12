# Golden Cherry narrow-band live A/B — 2026-09-13

## 시작과 모집단

- 공통 성과 cutoff: `2026-09-12T16:45:39Z` inclusive. 두 arm이 TTL 5분 최종 config로
  한 cycle씩 성공한 뒤의 시각이다.
- 자동 종료일 없음. 사용자가 중단 또는 교정을 지시할 때까지 신규 진입을 계속한다.
- 체크포인트: 24시간, 3일, 7일. 체크포인트는 자동 중단이나 과거 파라미터 복귀가 아니다.
- 전략: `golden-cherry`, YES-only Resolution Momentum
- 공통 universe: Gamma 누적 거래량 `$5,000`, 유동성 `$125,000`, 기준시각까지
  `(0h,120h]`, 스포츠 pregame/in-play와 비스포츠

| Arm | Jenkins | Runtime DB | 진입 확률 | 계좌 |
|---|---|---|---:|---|
| A | `polybot-yellow` | `cherry-live-yellow-076-078-v1` | `.76–.78` | 기존 Yellow 계좌 |
| B | `polybot-blue` | `cherry-live-blue-080-082-v1` | `.80–.82` | 기존 Cherry/Blue 계좌 |

두 arm의 확률 구간이 배타적이므로 같은 경기의 paired randomized A/B가 아니다. band별
prospective cohort 비교이며, event-cluster ROI와 종목·pregame/in-play 구성을 함께 보정한다.

## 공통 처치

- 주문액 `$5`
- TP `+20%`
- SL `-8%`
- 최고가 대비 trailing `15%`
- GTC BUY exact fill lifecycle, zero-fill TTL `5분`
- 최대 포지션 10, open 원금 `$50`, cycle 신규 1건
- exact-economic 신규진입 floor `-$30`
- 5분 schedule `4-59/5 * * * *`
- GTC accepted/live는 체결이 아니다. `order_fills.status='CONFIRMED'`만 실제 BUY/SELL로 센다.

Yellow의 과거 `default` DB는 매 cycle 먼저 close-only로 실행한다. 과거 5개 노출과
2026-09-13 전환 직전 생성된 pending BUY를 원래 TP10/SL08/trailing05로 계속 대사·관리하고,
새 A arm의 성과·capacity에는 합치지 않는다. Blue는 새 external T7 workspace와 새 runtime DB를
사용한다.

## 최초 배포 증거

- Yellow `#57234` 수동 SUCCESS, `#57235` 자연 SUCCESS
- Blue rename 전 이름 `polybot-cherry`; rename 후 최초 `#59704`는 SSH host-key mismatch로
  FAILURE. Yellow와 동일한 Jenkins Git credential과 HTTPS monorepo URL로 교정했다.
- Blue `#59705` 수동 SUCCESS, `#59706` 자연 SUCCESS
- Yellow 최초 config `629f85a0c7ee…`; 공통 zero-fill TTL을 5분으로 맞춘 최종 config
  `0aea174d10ff…`
- Blue 최초 config `0a432a8e2838…`; 같은 TTL 적용 후 최종 config `21c82f321ad3…`
- Yellow 첫 pre-cutoff Alabama 후보는 GTC LIVE zero-fill이며 common cutoff 이전 trade라 A/B
  성과에서 제외한다. 기존 노출로서 대사·TTL 취소·체결 여부는 별도 보고한다.
- Blue 최초 두 cycle은 eligible candidate 0, 주문 0이며 정상적인 빈 모집단이다.

## Simulation 수집

기존 `polybot-cherry-shadow/cherry-shadow-resolution-v2`가 이미 두 narrow band와 full-depth
가격 경로를 5분마다 수집한다. 따라서 disabled `polybot-grey`를 다시 사용하지 않는다.
Grey를 켜면 같은 자료의 독립 replica가 아니라 과거 Golden Peach runtime이 재개되므로 이
A/B의 교차 검증이 되지 않는다.

## 회고 프롬프트

다음 문장을 그대로 사용한다.

```text
Golden Cherry narrow-band live A/B를 배포 시각부터 현재까지 상세 회고해주세요.

대상:
- polybot-yellow / cherry-live-yellow-076-078-v1 / 진입 .76-.78
- polybot-blue / cherry-live-blue-080-082-v1 / 진입 .80-.82
- 공통: $5, TP20%, SL8%, trailing15%, pending BUY TTL 5분
- 성과 cutoff: UTC [2026-09-12T16:45:39Z, now)

두 job을 각각 daily-rsync scan → plan → sync → verify → pin하고 verified pinned DB와 로그만 사용해주세요.
Yellow의 과거 default close-only DB와 cutoff 이전 Alabama pending BUY는 신규 A/B 성과에서 제외하되,
기존 포지션 관리·체결·취소 상태는 별도 운영표로 보고해주세요.

CONFIRMED BUY/SELL과 exact one-hot resolution만 실제 손익으로 계산하고 accepted/live GTC,
trades.realized_pnl, 요청 가격·수량으로 체결이나 손익을 추정하지 마세요. 수수료 누락,
partial fill, pending, UNFILLED, QUARANTINED, 미연결 intent와 resolution evidence gap을 확인해주세요.

두 진입 band는 동일 경기 paired A/B가 아니므로 arm별 발견 시장, 후보, 주문, confirmed fill,
종목 구성, pregame/in-play, 진입 VWAP, 보유시간과 event-cluster ROI를 비교해주세요. simulation
Cherry Shadow에서 기대한 성과와 live fill rate·실현손익이 얼마나 일치하는지도 계산해주세요.

도표 최상단에 Yellow 합계, Blue 합계, 전체 종합 손익을 표시하고, 경기/시장별 진입 시각,
질문, 종목, phase, midpoint, 요청가, confirmed BUY VWAP·수량·수수료, exit reason,
confirmed SELL 또는 resolution, 실제 손익을 보여주세요.

5분 cadence의 SUCCESS/FAILED, p50·p95·최대 gap, 10분 초과 gap도 보고해주세요. Yellow와 Blue의
모집단 차이가 band 차이인지 실행 장애인지 구분하고 다음 판정을 내려주세요:
1. 두 arm을 계속 live 유지할 수 있는가
2. .84-.86 제외 가설이 계속 지지되는가
3. TP20/SL8/trailing15가 current TP10/SL8/trailing5 shadow 대조군보다 나은가
4. GTC zero-fill 때문에 simulation과 live가 벌어지는가
5. 파라미터 변경 또는 증액이 가능한가

회고만으로 job을 중단하거나 과거 파라미터로 자동 복귀하지 마세요. 치명적인 구현 버그만
증거 보존 후 수정·테스트·commit·push·Jenkins 재배포하고, 전략 파라미터는 임의 변경하지 마세요.
```
