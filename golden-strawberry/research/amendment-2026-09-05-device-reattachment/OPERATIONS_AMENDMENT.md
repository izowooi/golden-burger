# v2a 운영 복구: 동일 원본의 외장 장치 재연결

2026-09-05 운영자 요청에 따른 안전 복구다. 기존 v1/v2a의 데이터 계약, 가상 진입,
가격/시간 기준, cadence, 원본 anchor, seed와 원래 preregistration은 변경하지 않는다.
새 source/config 묶음과 복구 전후 공백은 분리 기록한다.

## 허용되는 유일한 파일 식별 변화

동일한 APFS volume의 OS device 번호는 재부팅/재연결 시 바뀔 수 있다. 정상 주기에서는
원래 anchor와 모든 항목이 일치해야 하며, device-only 변경도 기본적으로 계속 실패한다.
다음 조건을 갖춘 별도 유지보수 승인이 있을 때만 원래 anchor를 수정하지 않고 재연결을 인정한다.

1. 경로·inode·size·nanosecond mtime는 그대로이며 이전 device로 원래 fingerprint를 정확히 재현.
2. 기존 off-volume UUID pin·T7 sentinel·실제 APFS volume UUID와 workspace marker가 모두 일치.
3. 독립적으로 검증한 동결 사본을 근거로 제시한 원본 전체 SHA-256과 실제 전체 파일 해시가 일치.
4. 전체 해시 읽기 전후 stat 불변, v1 SQLite sidecar 없음.
5. 운영자가 `--apply`로 명시한 유지보수 명령이 T7 밖의0700디렉터리에0600 승인 receipt를
   원자적·덮어쓰기 없이 생성. 일반 run은 receipt를 만들거나 승인하지 않는다.
6. 이후 모든 cycle은 receipt의 원본 anchor·동일inode/size/mtime·trustedUUID와
   imported seed를 재검사. 다른 내용·inode·mtime·UUID·path 변화는 이 경로로 허용하지 않는다.

같은 trustedUUID의 OS device 번호만 다시 바뀌면 검증된 안정 식별값을 재사용한다. 재부팅할
때마다 같은 정상 원본을 다시 중지하지 않으며 그때 관측한 device/fingerprint는 따로 기록한다.
전체 내용 해시는 유지보수 때 한 번 계산한다. 매10분30GB전체읽기를 반복하지 않으며,
일반 cycle에서 검사한 receipt 원문과 SHA는 phase evidence에 남긴다. 과거 anchor를 UPDATE하거나
원본을 재작성/재시드하지 않는다. 과거 누락 호가를 채우거나 누락 구간을 정상 관측으로 세지 않는다.

이 수정은 후속 조회 SQL의 동일 결과 최적화와 함께 검증하며, 450초 요청 제한 및480초
정기 실행 기준은 완화하지 않는다. 첫 수동 실행과 이후 자연 실행이 성공한 뒤 재개를 확인한다.
