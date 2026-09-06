# Golden Guava 검토 절차

먼저 [EVIDENCE_CONTRACT.md](EVIDENCE_CONTRACT.md)를 읽는다. 이 문서는 현재 연구단계의
점검 절차이며 실거래 배포나 수익성 통과를 의미하지 않는다.

`REVIEW_START`, `REVIEW_END`를 UTC 반개구간으로 정하고 실제 source cutoff와 구분한다.
각 새 Jenkins job과 golden-guava를 daily-rsync scan/plan/sync/verify로 확인한 절대 DB 경로만
읽는다. 과거 Kiwi/NHL DB, 다른코드/설정/수집기, 같은경기의여러가설을독립표본으로합치지않는다.

연구점검은 슬롯/cursor/shard,직접6/2token·호가/수수료/원본시각/독립경기자료매핑,
종료후추적,원자적게시·실패/누락,DB무결성·저장공간을확인한다.
H1의표현별가격괴리가매수한leg상승으로이어졌는지검사하고,
H2/H3의관측자료부족·경쟁설명과H4의불완전trade tape/queue를명시한다.
H5는경기결과공개와venue결의를구분하고,취소·void·정규시간/연장·지연·단일FOK실패를보존한다.

시간순학습/검증분리,코드/설정cohort,독립event,비용·미청산·갭·다중검정을함께평가한다.
수수료를추정한표시호가수익과actual CONFIRMED fill을합치지않는다.
실거래연결뒤에는별도`polybot-retro audit --strict`와exactfill/fee/잔량·소유권을확인하며,
현재연구전용단계의검사통과를실거래승격으로바꾸지않는다.
