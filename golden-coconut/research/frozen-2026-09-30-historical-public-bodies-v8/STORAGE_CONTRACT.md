# Coconut historical public-body adapter v8

2026-09-30 source epoch `coconut-historical-public-bodies-v8`는 과거 Coconut
collector의 공개 JSON 쓰기·읽기를 공용 저장소 adapter에 연결한다.
기존 v7 research preregistration, data contract, registry, config YAML과
`EPOCHS.json`의 역사 cohort는 그대로 보존한다. 이 source epoch는 비활성 job을
활성화하거나 기존 DB의 run/config/source provenance를 변경하지 않는다.

- 실제 공개 원본 column만 exact bytes SHA-256 참조로 저장하며 durable public ACK
  이후 private row를 기록한다. JSON 내용·숫자·배열·NULL을 다시 계산하지 않는다.
- historical repository의 단건·batch insertion과 모든 payload reader 및 analyzer는
  같은 복원 adapter를 사용한다. raw scalar, classification, lifecycle 판단,
  threshold/episode/실험 상태는 이번 body adapter 범위에 포함하지 않는다.
- 현재 recorder의 `fee_json.fields`는 별도 White recorder source epoch에서 적용한다.
  이 문서나 historical manifest가 현재 White 설정의 권위가 되지 않는다.
- 이전 v7 `MANIFEST.sha256`는 변경하지 않고 새 manifest의 입력으로 보존한다.
  현재 source digest는 공용 adapter 코드 전체를 포함한다. 기존 config hash 계약은
  source digest도 입력에 포함하므로 이 코드로 생성하는 새 config hash는 달라진다.
  관측 금액·필터·cadence·simulation 설정은 바뀌지 않는다.
- 이 로컬 구현과 fixture 검증은 역사 DB의 운영 이전·정리·fleet 배포 증거가 아니다.
