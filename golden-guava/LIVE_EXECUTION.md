# Guava 실거래 연결부 — 구현과 한계

**현재는 연구 전용 배포다. 아래 테스트 통과는 Lion/Wolf의 실거래 활성화나 수익성
입증이 아니다.** 두 잡은 설정 검사만 실행한다. 네 연구 잡의 소스는 별도로 고정하며
미사용 live 모듈을 수정했다고 함께 교체하지 않는다.

## 서명 금액

SDK 1.1.0의 float 계산 문제를 인스턴스별 Decimal 계산으로 보완한다. 다른 전략이 쓰는
SDK 전역 함수·ROUNDING_CONFIG는 바꾸지 않고 실제 SDK의v2 서명은 그대로 사용한다.

- FOK BUY 원금은 정확한 cent 단위다. native tick0.1은3자리 주수, 나머지는 최대4자리 주수다.
- FOK SELL 보유량은0.01주 단위로 한 번만 내린다. 실제 signed maker 수량을 비교하고,
  proceeds 반올림으로 매도 하한을 낮추지 않는다.
- 원래 상한·사용 가격·native/signer tick·주수 정밀도·원래 maker/taker 정수를 POST 전에 보존한다.
- BUY 주수 내림 때문에 maker/taker 비율이 표시 제한 가격보다 미세하게 클 수 있다.
  비율을 상한과 완전히 같다고 하지 않는다. 원금·주수 내림 허용 폭과 **다음 더 나쁜 native
  가격 단위에는 도달하지 않는지**를 정수로 검사한다.
- 서명/소유권 오류나 원금 변경이면 제출하지 않는다. 가격·금액을 몰래 늘려 다시 서명하지 않는다.

기존 Watermelon/Peach/Plum은 같은 SELL 문제에 nextafter 보정을 사용한다. 이번 작업은
그 봇들을 수정하지 않고 Guava에 방어를 구현한다. 거래소 수락·체결은 별도 검증이 필요하다.

## 원장과 전체 조회

`Broker.execution_inventory()`는 검증된 전체 로컬 full snapshot을 반환하며 인증·네트워크·
서명·주문을 호출하지 않는다. 고립된 요청·소유권 불명·변형된 금액·DB/예산 실패는
부분 목록이나 정상 빈 결과로 바꾸지 않는다.
envelope/event는 UPDATE/DELETE 및 INSERT OR REPLACE를 방어하고, event hash를 재검사한다.
같은 event 재시도는 원래 본문·시각을 유지한다. 관리자에 의한 파일/트리거 교체까지 막는 장치는 아니다.

## 보유량·한도 계산

`position_state.reduce_positions`는 지갑/DB/SDK를 호출하지 않는 순수 계산기다.
검증된 전체 snapshot, 요청 ID 전체 목록, 명시적인 SELL→BUY 연결, 총계/경기/사이클/주문
한도를 요구한다. UI 보유분을 가져오거나 token이 같다는 이유로 매수·매도를 연결하지 않는다.

| 증거 | 처리 |
|---|---|
| 결과 불명/수량 대사 미완료 | 원래 BUY 원금 예약, 해당 경기·token의 신규 위험 차단 |
| terminal 부분 BUY | 확정 수량만 보유, 미체결 수량을 채우지 않음 |
| 진행 중 부분 SELL | 확정 매도만 차감, 아직 채워질 수 있는 주문 수량 예약 |
| terminal 부분 SELL + fee 미확인 | 취소된 수량 예약 해제, 비용 불확실성은 유지. 남은 확인 보유량의 보호 매도는 별도 판단 가능 |
| 증명된 종결 SELL 뒤0.01주 미만 잔여 | DUST: 경제적 보유/원가는 유지, 일반 거래 슬롯과 구분 |
| 미확인 주문180분 경과 | QUARANTINED 표지만 변경. 성공 체결·0보유·원금 해제로 바꾸지 않음 |
| 매수/매도 완결 및 잔여0 | 일반 슬롯 해제. 그 사이클에서 이미 쓴 진입 횟수는 유지 |

매수 예약과 보유를 중복 합산하지 않는다. 한 경기의 불확실성이 있어도 전체 한도를 지킨
다른 경기는 진행 가능하다. 누락·초과 매도·불명확한 소유권은 전체 계산을 거부한다.
`capacity(...).fits(5)`는 **원금/슬롯**만의 판단이다. 수수료 예산·지갑 가용액·손실 한도·
신호·규칙·주문 허용을 모두 통과한 것이 아니다. 보호 매도 가능 수량도 체결 보장이 아니다.

## 검증 명령

```bash
uv sync --frozen --extra dev --extra live
uv run --no-sync pytest
uv build
```

`tests/test_execution_real_sdk_amounts.py`는 DNS/socket을 차단하고 임시 SQLite와 메모리의
테스트 키만 사용한다. 실제 SDK 서명 type0~3, neg-risk, native tick, $5~$100 경계를 검사한다.
live extra가 없으면 명시적으로 skip된다. fake-only 테스트를 실제 SDK 검증으로 보고하지 않는다.

## 남은 통합

1. 수익 가설·A/B 차이·진입/익절/손절/해결/만료 정책.
2. 매수·매도 판단과 부모 매수 연결을 **POST 전에** 기록하는 저장소 및 재시작 복원.
3. 지갑·수동 보유 보호·fee 예산·손실 한도와 위 계산기를 연결하는 runner.
4. 미확인 POST 추적, 대사·취소·해결·상환, 시간 예산과 공정한 재시작.
5. 실제 응답의 fee 증거, backup/restore, 계정별 배포 이력.
6. Lion/Wolf에서 $5 실제 실행과 전체 Jenkins60초 미만 검증. 설정 검사로 대신하지 않는다.

사용자가 승인한 소액 실험과 수익성 입증·증액은 구분한다. 다만 없는 호가·잘못된 경기 매핑·
미완료 실행 로직으로 실험을 강행하거나 성공 체결을 만들어내지 않는다.

참고: [SDK 금액 계산](https://github.com/Polymarket/py-clob-client-v2/blob/v1.1.0/py_clob_client_v2/order_builder/builder.py),
[공식 수수료](https://docs.polymarket.com/trading/fees). 현재 수수료 설명은 과거 개별 fee 증거가 아니다.
