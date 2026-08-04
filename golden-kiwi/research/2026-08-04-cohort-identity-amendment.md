# Golden Kiwi cohort identity amendment — 2026-08-04

이 문서는 `research/frozen-2026-07-30/PREREGISTRATION.md`의 cohort identity 조항만
대체한다. 원본은 당시 연구 기록과 checksum 보존을 위해 수정하지 않는다.

Golden Kiwi의 collection cohort는 다음 네 값으로 식별한다.

```text
config_hash × strategy_source_digest × mode × job_name
```

`git_commit`은 실행 시점의 저장소 provenance로 계속 기록하지만 cohort 경계나 네 arm의
동일성 조건으로 사용하지 않는다. 모노레포의 다른 프로젝트 변경, 문서 수정, 운영상 필요한
후속 commit은 Golden Kiwi 실험 표본을 분할하지 않는다.

`strategy_source_digest`는 Golden Kiwi의 runtime·분석 코드와 실제 shared observability
runtime 파일의 경로와 바이트를 정렬해 계산한 SHA-256이다. 이 값이 달라지면 전략 판단이나
분석 의미가 달라질 수 있으므로 새 cohort로 분리한다. `polybot config`에서 앞 12자를 확인할
수 있으며 전체 값은 `strategy_configs.config_json`에 보존된다.

공식 분석 구간 밖의 기존 smoke run은 표본에 포함되지 않는다. 공식 구간 안에서는 각 arm이
단일 cohort이고 네 arm의 `strategy_source_digest`가 같아야 promotion evidence가 완전하다.
