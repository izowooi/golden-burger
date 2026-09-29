# White 공용 원본 저장 전환

v6의 5종목·네이션스 리그 identity·1분 cadence·관측 범위와 논리적 자료 계약을 유지한다.
공개 HTTP/clock/event/full book 본문을 공용 market-data SQLite writer에 먼저 durable 저장하고
해당 원본의 SHA-256 참조만 recorder DB에 기록한다. cycle/claim/판단/fee attribution과
runtime의 소유권은 recorder에 남긴다. 서로 다른 request의 관측 시각이나 경기 수는 합치지 않는다.

운영 전환 시 `PUBLIC_MARKET_DATA_REQUIRED=1`과 외장 저장소의 socket/DB를 지정한다.
공용 저장 실패 시 inline 원본으로 조용히 fallback하지 않는다. 공용 원본을 정확한 bytes로
읽는 reader와 closure 검증을 함께 배포해야 한다. v6 과거 pin의 원본 checksum은 그대로
보존하고, 참조로 바꾼 derivative DB는 별도 source/destination checksum으로 검증한다.

TP/SL/실거래 주문을 추가하지 않으며 accountless collector를 유지한다.
