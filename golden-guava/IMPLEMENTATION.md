# 구현 인터페이스 — 작업 중

최종운영계약과동일하지않다. Live완료를선언하기전에STRATEGY의남은설정을고정하고전체검증한다.
Python/uv,별도venv,`src/polybot`패키지,공통observability를사용한다.

## 파일 소유와 초기 인터페이스

- 메인: config.py, public_clients.py, main.py, runtime.py, workspace.py, 실행/상태관리,
  pyproject/uv.lock/README/운영문서/전체통합.
- 별도worker: evidence.py 및그테스트. 다른파일수정금지.
- H1과거재생도구담당: 저장소tools의별도파일. 새runtime에암묵적으로sibling polybot을import하지않는다.

`Repository(path, contract:dict)`는호출전에workspace와writer잠금검증이끝났다는전제다.
`contract`는strategy_name=golden-guava,job_name,mode=sim,data_contract=guava-research-v1,
config_hash,strategy_source_digest를포함한다. 계약identity는DB에서검증하고secret포함시거부한다.

메서드:

- `start_run(run_id, started_at, config_snapshot)` — STARTED내구성기록.
- `record_request(run_id, receipt:dict, payload:dict|list|None)` — 경제원문gzip/hash와요청영수증.
  receipt: request_id,source,method,path,params,started_at,received_at,status,error_type.
- `previous_events()` — 같은config/source의현재event cache만반환(dict event_id→eventdict).
- `publish_cycle(run_id, observed_at, events:list[dict], books:list[dict], features:list[dict], summary:dict)`
  — 모든cycle/event/book/feature/cache/SUCCEEDED를하나의transaction으로저장.
- `fail_run(run_id, ended_at, error_type, phase)` — FAILED내구성기록. 이미게시된성공을실패로덮지않음.
- `status()` — 작은metadata/latest run/파일크기만,전체COUNT/quick_check금지.
- `close()` — 연결정리.

event dict: event_id,sport_family,league_code,observed_at,raw(원문dict),clock(원문dict),
eligible,exclusion_reason,expected_token_ids(list),cohort_key.
book dict: event_id,token_id,condition_id,result_kind,outcome_side,observed_at,request_id,
status,raw(dict|None),fee_evidence(dict),depth_metrics(list).
feature dict: event_id,hypothesis_id,observed_at,applicable,reason,metrics(dict).

raw数값을미체결/누락때0으로만들지않는다. 모든비정상응답/호가부재는상태와원인으로보존한다.
새book이없어도expected_token의attempt행을보존한다. 연구모듈은지갑/주문/포지션/P&Lclient를import하지않는다.
SQLite는append-only원자료와명시적latest_event_state cache만사용,외래키와transaction을유지한다.
큰검사는분석기/daily-rsync사본에서수행한다. checksum형식만보고검증했다고표현하지않는다.
