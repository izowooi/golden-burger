# 세 실거래 전략 계약·확장 배포 확인 — 2026-09-03

## 결론

- 사용자가 구분한 전략 세 종류는 맞지만 실거래 Jenkins는 6개가 아니라 10개다.
- Watermelon Live는 축구 Cat/Dog, MLB Bear/Tiger, NHL Lion/Wolf의 여섯 job이다.
- Peach는 축구 Eco/Fruit의 두 job이고 Grey가 축구·MLB·NBA·NFL·NHL simulation을 수집한다.
- Plum은 축구 King/Queen의 두 job이고 Silver는 축구, Gold는 MLB·NFL·NBA simulation을
  수집한다.

## 의미 차이

Watermelon Live 축구는 HOME/DRAW/AWAY의 YES 세 결과만 직접 매수한다. MLB/NHL은
YES/NO 명제 여섯 개가 아니라 팀 이름으로 표시된 direct two-team moneyline 두 token을
비교한다. 확인된 손절 뒤에는 같은 event에서 반대 결과에 한 번 재진입할 수 있으므로,
“경기당 영원히 한 번”은 아니다. 각 개별 포지션은 한 번만 종결된다.

Peach 축구는 HOME/DRAW/AWAY 각각의 직접 YES와 직접 NO, 총 여섯 호가 중 유일한 선두를
고른다. 한 번이라도 체결되거나 체결 여부가 불명확한 BUY가 있으면 같은 event에 재진입하지
않는다. Plum 축구도 여섯 호가를 보지만 최고값만으로 즉시 사지 않는다. 같은 token의 세 번
연속 상승, 누적 +2%p, `[0.75,0.78]` 첫 상향 교차가 모두 필요하다.

## Golden Peach 확장 배포

배포 commit `d1e9c0148b3befdf073a3d042e83b9f258d37dd1`에서 다음을 반영했다.

- live는 기존 Eco +3%p / Fruit +5%p 축구 A/B와 `$5`를 유지했다.
- Grey에 축구 6호가와 MLB·NBA·NFL·NHL direct two-team moneyline의 독립 runtime/DB를
  추가했다. 비축구는 native clock과 종목별 수치 검증 전까지 simulation-only다.
- snapshot/trade/catalog에 sport family, league code/name, raw tags를 추가했다.
- 목표 주문액, 실제 선택액, 호가상 최대 완전 체결 가능액, 축소 이유를 저장한다.
- `$5–$1,000` 고정 사다리에서 한 번에 완전 체결 가능한 최대 금액 하나만 FOK로 제출한다.
  부분 노출을 여러 주문으로 합치지 않으며 `$5`도 불가능하면 주문하지 않는다.
- 기준 신호용 `$5`와 증액 가능 규모를 같은 fresh book으로 계산해 추가 네트워크 호출을
  만들지 않았다.

## 검증 증거

- Golden Peach test: 223 passed.
- 변경 Python 파일 Ruff check: passed.
- root strategy contract verifier: 28 strategies passed.
- Jenkins 수동 배포 build: Eco `#17881`, Fruit `#10095`, Grey `#6801`, White `#14535`
  SUCCESS.
- 1분 timer 복구 후 연속 자연 build: Eco `#17882–#17884`, Fruit `#10096–#10098`,
  Grey `#6802–#6804`, White `#14537–#14539` SUCCESS.
- 두 번째 자연 build runtime: Eco 5.054s, Fruit 5.068s, Grey 7.342s, White 11.212s.
- White의 고정 30초 shell 대기를 제거했다. 단독 수동 build는 26.202s였고 자연 build는
  11–12초대로 1분 주기 안에 종료됐다.
- Daily Rsync sync와 후속 verify는 네 job 모두 SUCCESS, checksum/SQLite 실패와 open
  artifact conflict가 0이었다. Golden Peach live DB의 open state도 두 arm 모두 0이었다.
- 배포 시각에는 허용 종목의 진행 중 경기가 없어 새 sport/capacity snapshot row는 아직
  없었다. 신규 DB·schema·runtime 실행은 확인했지만 실제 경기 호가 coverage는 다음 경기
  후 별도 확인해야 한다.

## Jenkins 설명

Cat, Dog, White와 이번 변경 과정에서 다시 저장한 Eco, Fruit, Grey의 설명을 UTF-8로
정상화했다. 최종 read-only 재조회에서 한글과 1분 timer가 함께 정상임을 확인했다.
