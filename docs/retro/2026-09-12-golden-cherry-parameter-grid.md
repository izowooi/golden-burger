# Golden Cherry 진입·TP·SL·Trailing 재생 — 2026-09-12

## 결론

| 항목 | 현행 | 수치상 최대 | **운영 추천 후보** |
|---|---:|---:|---:|
| 진입 | 연속 `.75–.88` | `.76–.78` + `.80–.82` | **`.76–.78` + `.80–.82` 두 구간** |
| TP | `+10%` | 없음 | **`+20%`** |
| SL | `-8%` | `-5%` | **`-8%` 유지** |
| trailing | `5%` | `15%` | **`15%`** |
| 금액 | `$5` | — | **`$5` 유지** |

수치만 최대화한 조합은 low+middle에서 `TP 없음 / SL -5% / trailing 15%`이며 94개 완결
episode에서 `+$18.964825`, ROI `+4.04%`였다. 그러나 37건을 resolution까지 보유하고 54건을
손절하는 구조다. 실제 live resolution 성분이 큰 음수였다는 점과 5분 cadence의 gap을 고려하면
이를 live 최적값으로 채택하는 것은 위험하다.

운영 추천 후보는 `TP +20% / SL -8% / trailing 15%`다. low+middle 96 episode 중 94개가
완결됐고 `+$15.883698`, ROI `+3.38%`였다. 전반부 `+$9.930108`/`+4.41%`, 후반부
`+$5.953590`/`+2.43%`로 시간 분할 양쪽이 양수였다. SL은 그대로 두고 TP와 trailing을
넓히는 조합이라 한 번에 세 축을 바꾸는 최종 배포보다는 별도 prospective A/B arm으로
검증하는 것이 적절하다.

## 왜 166일 전체를 임의 파라미터로 재생하지 않았는가

Yellow live DB는 2026-03-30부터 166일을 보존하지만 `market_snapshots`가 0행이다. 실제
청산 뒤 가격 경로를 저장하지 않았으므로 “TP가 +20%였으면 이후 어디서 팔렸는가”를 166일
전체에 대해 복원할 수 없다. 실제 live가 선택한 청산까지만 알고 그 이후 경로가 없기 때문에,
이를 억지로 계산하면 resolution이나 다른 DB의 시계를 사후 결합한 편향된 결과가 된다.

완전한 full-depth 경로를 가진 현재 prospective 자료는 Cherry Shadow의
`2026-09-04T16:00:00Z` 이후 구간이다. 최신 DB를 scan → plan → sync → verify → pin했고,
pin SHA-256은 `266f19c6329305bca8429319421bd7ca0f2443ccd589f73cecf041866da751a0`다.

## 재생 범위와 방법

- valid episode 142건, event cluster 97개
- `$5` full-depth bid path 8,553개
- proven resolution 97개
- TP 8값: `3/5/8/10/12/15/20%/없음`
- SL 8값: `-5/-8/-10/-12/-15/-20/-30%/없음`
- trailing 7값: `3/5/8/10/15/20%/없음`
- 진입 universe당 448개 조합
- 동일 event의 여러 시장은 같은 전반부 또는 후반부에만 배치
- 실행 우선순위는 live와 동일하게 SL → TP → trailing → resolution
- 실제 체결이 아니라 당시 표시된 전량 bid VWAP 반사실

FAILED run이 policy exit를 publish한 episode 3건이 발견됐다. 이 행은 공식 analyzer에서 valid
exit로 인정되지 않지만 이후 runtime 상태에는 영향을 줄 수 있다. grid에서는 해당 episode
3건을 전부 제외했다. analyzer가 이 수를 명시적으로 보고하도록 계측을 보완했다.

## 진입 구간별 결과

| 진입 구간 | 완결/전체 | 현행 TP10/SL08/T05 | 추천 TP20/SL08/T15 | 판정 |
|---|---:|---:|---:|---|
| `.76–.78` | 36/37 | `+$6.946686` (`+3.86%`) | `+$7.509762` (`+4.17%`) | 유지 후보 |
| `.80–.82` | 58/59 | `+$3.479434` (`+1.20%`) | `+$8.373937` (`+2.89%`) | 유지 후보 |
| `.84–.86` | 45/46 | `-$8.284594` (`-3.68%`) | `-$5.763091` (`-2.56%`) | **제외** |
| low+middle | 94/96 | `+$10.426120` (`+2.22%`) | **`+$15.883698` (`+3.38%`)** | 추천 모집단 |
| 세 구간 전체 | 139/142 | `+$2.141526` (`+0.31%`) | `+$10.120608` (`+1.46%`) | high 혼입으로 열위 |

`.84–.86`은 448개 조합 중 전반부와 후반부가 모두 양수인 조합이 0개였다. 진입 상한을
단순히 `.82`로 바꾸면 아직 관측하지 않은 `.78–.80`과 `.82` 경계의 시장까지 포함된다.
따라서 실제 적용에는 연속 threshold가 아니라 두 개의 명시적 allowed band가 필요하다.

## Live 적용 판정

이 문서의 최초 grid 계산 시점에는 Yellow live 설정을 변경하지 않았다. 이후 사용자가
2026-09-13 추천 후보를 수락해 별도 prospective A/B로 배포했다.

1. 사용자는 최적값 추천을 요청했고 즉시 live 배포를 요청하지 않았다.
2. Shadow 경로는 8일이며 166일 전체 경로가 아니다.
3. grid 448개를 탐색했으므로 성과가 사후 선택으로 부풀 수 있다.
4. 현재 prospective entry 수집 종료일은 2026-10-04다.

따라서 다음 배포안은 `$5` prospective A/B다. Control은 현행 `TP10/SL08/trailing05`,
Treatment는 명시적 `.76–.78 + .80–.82`, `TP20/SL08/trailing15`로 둔다. 금액 증액은 이
검증 뒤에도 보류한다. 기존 `polybot-yellow` scheduler와 현재 포지션 관리는 계속 유지한다.

실제 배포는 두 band 자체를 서로 다른 계좌에서 비교하도록 구성됐다. Yellow `.76–.78`,
Blue `.80–.82`이며 공통 TP20/SL08/trailing15다. 상세 계약과 회고 프롬프트는
[2026-09-13 live A/B preregistration](2026-09-13-golden-cherry-live-ab-preregistration.md)에 있다.

## 로컬 자료 격리

현재 Jenkins config에서 다른 전략으로 재사용됐거나 disabled인 로컬 epoch 15개를 삭제하지
않고 다음 위치로 이동했다.

`daily-rsync/data/sources/macmini-m5/jobs/_remove/20260912T142002Z`

총 크기는 약 295 GiB다. 원래 상대 경로, 크기, 이동 근거는 `MANIFEST.txt`에 있다. White의
과거 Watermelon, Gold의 과거 Coconut, Silver의 과거 Plum, Shadow-one의 과거 Strawberry,
disabled Grey/Shadow/Bear/Tiger 등이 포함된다. 동일 filesystem 안의 이동이므로 디스크
여유 공간은 늘지 않는다. 사용자가 내용을 확인할 때까지 삭제하지 않는다.

Shadow sync를 위해 전략 자료 대신 재생성 가능한 `uv`, `pip`, Playwright browser cache를
정리했다. 이후 최신 6.17 GiB DB sync와 pin을 만들었고, 최종 여유 공간은 50 GiB floor 위를
유지했다.
