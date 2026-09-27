# 현행 단일 White 스포츠 수집 운영

2026-09-27 사용자 지시로 White만 축구·MLB·NBA·NFL·NHL의 전체 raw를 1분 수집한다.
Silver는 Jenkins disabled·timer 없음으로 신규 중복 수집을 종료하며 기존 DB·로그·daily shard는 그대로 보존한다.

- 사용률 >=90% 또는 free<50GiB에서 중단한다. 기존150GiB reserve는~931GiB T7에서84%쯤 수집을 막으므로50GiB로 함께 조정했다.
- 외장 APFS/UUID/device/marker/symlink, writer lock, credential/order 금지 계약은 유지한다.
- v5 source/config cohort를 append-only로 기록하며 DB schema/runtime/data contract를 바꾸거나 과거 행을 migration/merge/delete하지 않는다.
- 현재 계약: research/frozen-2026-09-27-single-white-storage90-v5/PREREGISTRATION.md.
- 기존 AGENTS/README/STRATEGY와 v7 manifest는 역사 epoch의 hash 검증 대상이므로 변경하지 않았다. 현재 배치·수집 정책은 이 문서와 v5 resolved config, 실제 Jenkins 상태로 확인한다.
- /sports는 White 기본 표시·선택, Silver 과거 자료 표시·명시적인 historical import 허용.
- 검증은 recorder receipt/slot/schema/hash/clock/fee/terminal 품질 점검과 필요시 Gold/live/공식 일정 대사로 수행한다. 동일 두 복제본의 일치를 정확성 증명으로 해석하지 않는다.
