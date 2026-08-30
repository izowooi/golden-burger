# Golden Peach 과거 자료 재생 근거

## 증거 경계

- White 1분 DB: `watermelon-white-1m-v4a`, SHA-256
  `76aa717e3009b36f8d4783aa56311bcaadc974269b21bd686aca89152ee72839`, source cutoff
  `2026-08-30T06:31:38.842289Z`.
- Grey 5분 DB: `watermelon-grey-5m-v4a`, SHA-256
  `38c5f1a1f0055af526dc95ab49127bdc1dc1fb1366396829e0e371cea90d9edf`, source cutoff
  `2026-08-30T06:42:50.662167Z`.
- 두 DB 모두 `daily-rsync verify`와 SQLite `quick_check`를 통과한 로컬 사본을 사용했다.

## 방법

허용 축구 리그, source clock 0~10분, 같은 run의 complete HOME/DRAW/AWAY triad를 요구했다.
과거 collector는 각 명제의 YES full-depth book만 저장했기 때문에, 탐색 단계에서만 NO ask/bid를
YES 반대편 book의 보수적 complement로 합성했다. exact `$5` depth, spread, entry band를 적용한
뒤 각 event의 최초 선두를 고르고 이후 경로에서 TP/SL/후반 정책을 재생했다.

이 합성 NO는 실제 direct NO order-book이 아니므로 live 실행 근거의 질이 낮다. Golden Peach
Grey는 이 한계를 없애기 위해 여섯 direct book 모두를 원문 level로 저장한다.

재현 명령은 다음과 같다. 스크립트는 원본 DB를 read-only URI로 열고 SHA-256과
`PRAGMA quick_check`를 결과에 포함한다.

```bash
uv run --project golden-peach python \
  golden-peach/scripts/replay_watermelon_kickoff_leader.py \
  --db <verified-white-v4a-db> \
  --db <verified-grey-v4a-db>
```

## 결과

- White 1분 primary의 비교 가능한 entry event는 35건이며 선택된 선두는 35건 모두 합성 NO였다.
- White primary의 시험 grid 평균은 모두 음수였다. 가장 덜 나쁜 탐색 조합
  `TP +0.03 / SL -0.20`도 평균 `-0.287%`였고, 양수 28건/비양수 7건이라 작은 반복 이익이
  꼬리 손실을 넘지 못했다.
- 공통 SL `-0.10`에서 Eco 후보 `TP +0.03`은 평균 `-4.331%`, 중앙값 `+3.947%`,
  양수 22건/비양수 13건이었다. Fruit 후보 `TP +0.05`는 평균 `-6.332%`, 중앙값
  `-11.111%`, 양수 17건/비양수 18건이었다.
- Grey 5분 민감도 자료는 31건을 만들었고 역시 모두 합성 NO였다. `+0.03/-0.10` 평균
  `-3.881%`, `+0.05/-0.10` 평균 `-4.758%`였다. `+0.02/-0.20` 하나만 평균 `+0.429%`였지만
  서로 다른 cadence, 합성 NO, actual fill·fee 부재, 31건 탐색 grid라 승격 근거로 쓰지 않는다.

따라서 과거 자료는 Golden Peach의 수익성을 지지하지 않는다. 사용자가 선호한 작은 익절과
넓은 손절을 검증하되, SL을 `-0.10`으로 공통 고정하고 TP `+0.03`과 `+0.05`만 비교한다.
이 배치는 “추천 수익 전략”이 아니라 낮은 `$5` 단위의 반증 실험이며, 배포 후 직접 NO book과
confirmed fill·fee를 갖춘 단일 cohort만 최종 판단에 사용한다.
