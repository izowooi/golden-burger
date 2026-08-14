# 020 — 실제 계좌 live fleet 구성·DB·로그 감사

- 감사일: 2026-08-14 KST
- 대상: 운영자 메모의 실제 계좌 Jenkins 16개
- 제외: Kiwi simulation, Pomegranate/Raspberry accountless research, Blueberry shadow
- 변경 원칙: Jenkins와 실거래 전략 코드는 read-only로 검사했다. 실제 주문·DB·job 구성은
  변경하지 않았다. 수집 중 발견한 Jenkins LogRotator race만 로컬 `daily-rsync`에 보완했다.

## 결론

메모의 16개 job은 현재 Jenkins에서 확인되는 credential-bearing live/close-only job 16개와
정확히 일치한다. 누락된 live job은 없고, 16개 모두 enabled·scheduled·non-concurrent이며
현재 config에 `CleanBeforeCheckout`, `git clean`, `rm -rf`, DB 삭제 명령은 없다. 최신 완료
build와 `RUN_AUDIT`도 모두 SUCCESS다.

그러나 **build 성공과 거래 정상은 같지 않다.** 다음 네 계열의 실제 lifecycle 문제가 있다.

1. `polybot-cherry`는 과거 CLOB intent 27건의 대사가 매 cycle 실패하고, 실제 청산 조건을
   충족한 보유 2건의 SELL이 같은 token/side 격리 때문에 최신 build에서도 차단됐다. 그
   cycle은 신규 BUY 1건을 더 제출했다. 신규 진입보다 청산 복구가 먼저다.
2. `polybot-orange`는 지갑에 없는 오래된 DB 오픈 행 63건(그중 HOLDING 55건)이 요청 원금
   `$44,600`으로 남아 `$5,000` 상한을 초과한다. 최근 29시간의 후보 3,656건이 모두
   차단됐고 최신 cycle도 후보 5건을 전부 차단했다. 조건이 엄격해서 거래가 없는 것이 아니다.
3. `polybot-eagle`, `polybot-fruit`, `polybot-lime`, `polybot-wolf`는 실제 BUY와 SELL이 각각
   5.71주로 정확히 체결됐고 현재 해당 wallet 노출도 없지만, `fee_rate_bps=0`인 fill의
   `fee_amount_usdc=NULL`을 fee 불명으로 오판하여 `PENDING_SELL`에 남는다. 같은 reader가
   Papaya·Queen·Quince에도 있어 아직 첫 exit 전인 6개 job에도 잠재 결함이다.
4. `polybot-yellow`와 `polybot-cherry`의 legacy order/fill 증거에는 strict CRITICAL/HIGH가
   남아 있다. 특히 Yellow의 현재 구간 COMPLETED 79건 중 exact BUY/SELL coverage는
   64건(81.0%)뿐이고, 13건은 수량 불일치, 2건은 confirmed SELL 자체가 없다. 단순 보고서
   품질뿐 아니라 일부 잔여 wallet exposure와 연결된다.

따라서 지금은 파라미터나 build 주기를 조정할 단계가 아니다. Cherry 청산 격리, Orange
stale exposure, zero-fee `PENDING_SELL`, legacy wallet/DB 대사를 먼저 복구해야 한다.

## 대상 확정과 Jenkins 구성

Jenkins 전체 job에서 실제 builder의 `cd golden-*`, credential 존재, 실행 mode를 다시
분류했다. 메모의 16개 외 live 계좌 job은 없었다. 메모 마지막 줄의 `melon-low`는 Jenkins
job명이 아니라 runtime 이름이며 실제 job은 `polybot-wolf`가 맞다.

| Jenkins job | Strategy / runtime | Mode | Timer | Build retention | 최신 완료 build | 판정 |
|---|---|---|---|---|---:|---|
| `polybot-red` | `golden-date/default` | close-only | `H/5` | 7일 | #54308 | SUCCESS지만 invalid-token ghost 반복 |
| `polybot-yellow` | `golden-cherry/default` | live | `H/5` | 7일 | #49010 | SUCCESS, fill/lifecycle 증거 불량 |
| `polybot-cherry` | `golden-elderberry/default` | live | `H/5` | 7일 | #52235 | SUCCESS지만 실제 SELL 격리 |
| `polybot-orange` | `golden-cherry/default` | live | `H/5` | 7일 | #54254 | SUCCESS지만 신규 BUY 전면 차단 |
| `polybot-eagle` | Blueberry `+2pp` | live | `*/5` | 미설정 | #7713 | SUCCESS, `PENDING_SELL` 1 |
| `polybot-fox` | Blueberry `+5pp` | live | `*/5` | 미설정 | #9507 | SUCCESS |
| `polybot-cat` | Papaya 24h | live | `H/10` | 14일 | #3665 | SUCCESS, 현 trade 0 |
| `polybot-dog` | Papaya 72h | live | `H/10` | 14일 | #3556 | SUCCESS, 현 trade 0 |
| `polybot-queen` | Queen 24h | live | `H/5` | 미설정 | #2136 | SUCCESS, 현 trade 0 |
| `polybot-king` | Queen 12h | live | `H/5` | 미설정 | #2135 | SUCCESS, 현 trade 0 |
| `polybot-bear` | Quince passive | live | `H/5` | 3일 | #7786 | SUCCESS, 현 trade 0 |
| `polybot-eco` | Quince nearest | live | `H/5` | 미설정 | #9676 | SUCCESS, 현 trade 0 |
| `polybot-tiger` | Quince cross | live | `H/5` | 3일 | #9288 | SUCCESS, 현 trade 0 |
| `polybot-fruit` | Melon high | live | `H/5` | 미설정 | #2523 | SUCCESS, `PENDING_SELL` 1 |
| `polybot-lime` | Melon mid | live | `H/5` | 미설정 | #9288 | SUCCESS, `PENDING_SELL` 1 |
| `polybot-wolf` | Melon low | live | `H/5` | 3일 | #8139 | SUCCESS, `PENDING_SELL` 1 |

최신 config SHA-256 prefix는 각각 Red `74eb50e46cc8`, Yellow `b3470e3fe4c2`,
Cherry `95e9ac259cad`, Orange `0c9e50b2bec2`, Eagle `48563ba6d598`, Fox
`d029a02085ee`, Cat `78ab812b0b8d`, Dog `a93bf7f8b348`, Queen `8f9ae3e31a23`,
King `d082f139d4eb`, Bear `7d5ef41c237f`, Eco `faa63c573963`, Tiger
`0e9bd7abe726`, Fruit `70a3a4b1d652`, Lime `29f130a53c5d`, Wolf
`9e614f53da9c`다.

14일 retention rollout은 아직 Cat/Dog pilot에만 적용돼 있다. Red/Yellow/Cherry/Orange는
7일, Bear/Tiger/Wolf는 3일이고 나머지 7개는 미설정이다. 이는 거래 오류는 아니지만,
`daily-rsync` 이전에 console이 삭제될 수 있다. 실제로 Wolf console 17개가 source에서
rotation된 상태로 확인됐다.

## 용량과 동기화 결과

- 동기화 전 local free: 약 245 GiB, 완료 후 244 GiB
- Mac Mini internal free: 약 84.5 GiB
- 16개 current live artifact plan 상한: 약 2.94 GiB
- 현재 `daily-rsync/data`: 11 GiB
- DB 종류: 16개 모두 `database_live`; simulation DB 0개
- sync: 16/16 SUCCESS, failed artifact 0
- verify: 16/16 SUCCESS, conflict 0
- Wolf의 `skipped_retention_deleted=17`만 존재하며 모두 과거 Jenkins console이다.

Cat 첫 sync에서는 14일 LogRotator가 scan/plan 직후 가장 오래된 console을 삭제했고,
macOS `openrsync --files-from`이 그 한 파일 때문에 console batch 전체를 비워 PARTIAL이 됐다.
DB와 bot log는 손상되지 않았다. `daily-rsync`가 전송 직전 파일 존재를 제한된 Jenkins
console 경로에서 재확인하고, 전송 중 사라진 파일만 explicit retention skip으로 기록하도록
보완했다. 이후 Cat/Dog/Wolf 실제 sync와 전체 verify가 성공했다.

## Evidence boundary

Evidence Contract의 일 단위 strict audit은 source cutoff가 완전히 덮는
`[2026-08-12T00:00:00Z, 2026-08-13T00:00:00Z)`에 실행했다. 현재 운용 검사는 모든
최신 cohort가 포함되는 `[2026-08-12T18:00:00Z, 2026-08-13T23:00:00Z)`에 별도 SQL과
console 분석을 적용했다. timezone은 UTC이며 KST는 UTC+9다.

각 DB는 SQLite online backup이고 `quick_check`, local/remote SHA-256, catalog verify를
통과했다. 아래 `source cutoff`보다 뒤의 상태는 이 snapshot으로 주장하지 않는다.

| Job | Source cutoff / sync finish (UTC) | Remote path | Verified local path | SHA-256 |
|---|---|---|---|---|
| Red | 23:15:46 / 23:22:54 | `/Users/jongwoopark/.jenkins/workspace/polybot-red/golden-date/data/default/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-red/strategies/golden-date/runtime/default/databases/latest/trades.db` | `68c96ee15be8954281e7d9534d03e7d146c69675acdd474031c2e5d32d7fc5c0` |
| Yellow | 23:28:43 / 23:36:10 | `/Users/jongwoopark/.jenkins/workspace/polybot-yellow/golden-cherry/data/default/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db` | `93ef61bc3c8fb7834068429ccd217ee3d0a940eaceba0478beab2ef0854c8b76` |
| Cherry | 23:21:30 / 23:29:42 | `/Users/jongwoopark/.jenkins/workspace/polybot-cherry/golden-elderberry/data/default/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cherry/strategies/golden-elderberry/runtime/default/databases/latest/trades.db` | `063a4dd8b64ae53ecab9c663bb6f24d5ada19153ddcf482890b0e357ecec31d1` |
| Orange | 23:35:40 / 23:42:37 | `/Users/jongwoopark/.jenkins/workspace/polybot-orange/golden-cherry/data/default/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-orange/strategies/golden-cherry/runtime/default/databases/latest/trades.db` | `0fc83b83caad2acb0d06e834aa9b257c528fc2c4daec16895d2d00f4f03eb1be` |
| Eagle | 00:16:16 / 00:19:24 | `/Users/jongwoopark/.jenkins/workspace/polybot-eagle/golden-blueberry/data/blueberry-live-a-2pp/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-eagle/strategies/golden-blueberry/runtime/blueberry-live-a-2pp/databases/latest/trades.db` | `b51f863a8e9660fbe7c7e6685a4fa3c47ff342ac07f32e91eb394fddb17c932c` |
| Fox | 00:16:13 / 00:21:58 | `/Users/jongwoopark/.jenkins/workspace/polybot-fox/golden-blueberry/data/blueberry-live-b-5pp/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-fox/strategies/golden-blueberry/runtime/blueberry-live-b-5pp/databases/latest/trades.db` | `764c37329346d19d4dca45f63de7c798fc2e6fa5d7808b70744092f8ba50e412` |
| Cat | 23:56:20 / 00:04:35 | `/Users/jongwoopark/.jenkins/workspace/polybot-cat/golden-papaya/data/papaya/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cat/strategies/golden-papaya/runtime/papaya/databases/latest/trades.db` | `5e0cc0d76c3b83a4c0df308dd71aedfdd05c544a72f6cdacd3d7c6181e262f41` |
| Dog | 23:58:00 / 00:11:13 | `/Users/jongwoopark/.jenkins/workspace/polybot-dog/golden-papaya/data/papaya/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-dog/strategies/golden-papaya/runtime/papaya/databases/latest/trades.db` | `c58a906f873fc1466f700ec2fdb581774c3f3e19353f86b5369b117e2c1d000b` |
| Queen | 00:10:51 / 00:14:01 | `/Users/jongwoopark/.jenkins/workspace/polybot-queen/golden-queen/data/queen-live-24h/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-queen/strategies/golden-queen/runtime/queen-live-24h/databases/latest/trades.db` | `167e7e0dc89e92b94c4539eebdb3784696f6c6ee21f89caae3eae3ec4efedff7` |
| King | 00:14:13 / 00:16:48 | `/Users/jongwoopark/.jenkins/workspace/polybot-king/golden-queen/data/queen-live-12h/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-king/strategies/golden-queen/runtime/queen-live-12h/databases/latest/trades.db` | `446b9d03c1ca90abf60fd914bdb847aaeed65c9b9e2182737cc9b72a5bdaffe8` |
| Bear | 00:21:14 / 00:24:42 | `/Users/jongwoopark/.jenkins/workspace/polybot-bear/golden-quince/data/polybot-quince-passive/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-quince/runtime/polybot-quince-passive/databases/latest/trades.db` | `5da6750b0e146d9d3b7ccf7680d5d437bdf4d927eec47e31c2c6d422c396a1c7` |
| Eco | 00:23:01 / 00:27:20 | `/Users/jongwoopark/.jenkins/workspace/polybot-eco/golden-quince/data/polybot-quince-nearest/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-eco/strategies/golden-quince/runtime/polybot-quince-nearest/databases/latest/trades.db` | `26810295f421212af3c219dcfce964e027d9076a0d60aa837634bbd3dc470569` |
| Tiger | 00:26:38 / 00:30:01 | `/Users/jongwoopark/.jenkins/workspace/polybot-tiger/golden-quince/data/polybot-quince-cross/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-tiger/strategies/golden-quince/runtime/polybot-quince-cross/databases/latest/trades.db` | `3bf84b6c2a22ed1445ffea57ffa77925bd53d4723e6db67e2888122ab9e8231a` |
| Fruit | 00:28:14 / 00:36:02 | `/Users/jongwoopark/.jenkins/workspace/polybot-fruit/golden-melon/data/polybot-melon-high/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-fruit/strategies/golden-melon/runtime/polybot-melon-high/databases/latest/trades.db` | `564ee1581bcfb75ee316ba6d822468016d2c426ada041ca25971808cc188a3ab` |
| Lime | 00:35:15 / 00:42:11 | `/Users/jongwoopark/.jenkins/workspace/polybot-lime/golden-melon/data/polybot-melon-mid/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-lime/strategies/golden-melon/runtime/polybot-melon-mid/databases/latest/trades.db` | `c1c5e41062c05de5f7c0d1f0e13c07cc720f98ea727a13a217f07174a16354bd` |
| Wolf | 00:41:14 / 00:47:39 | `/Users/jongwoopark/.jenkins/workspace/polybot-wolf/golden-melon/data/polybot-melon-low/trades.db` | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-wolf/strategies/golden-melon/runtime/polybot-melon-low/databases/latest/trades.db` | `a629fe5a9b33ed41404f1d82960d3d611ff37dd339c203ba58be78924079d494` |

00시가 표시된 행은 2026-08-14, 23시는 2026-08-13이다.

## 운영 구간 DB·로그 판정

### Cadence와 수집

- 5분 job의 정상 gap 중앙값은 5.0분, Cat/Dog은 10.0분이었다.
- 2026-08-13 09:46Z 부근 modern bot 여러 개에 공통 22분대 지연이 한 번 있었다.
  Blueberry 두 팔은 shared Gamma cache lock timeout으로 각 1 run이 FAILED했지만 다음
  timer부터 회복했다. 지속 장애는 아니다.
- 모든 modern `market_sweeps`는 조사 구간에서 `completed_at` 존재,
  `cursor_complete=true`, missing condition 0, duplicate raw 0이었다.
- simulation run/submission은 0이었다.
- Papaya·Queen·Quince의 0 trade는 scheduler나 DB 실패가 아니다. current cohort에서
  signal candidate가 없었다. Dog은 후보 4회 중 첫 주문 1회가 venue의 일시적
  `425 order manager not ready`로 실패했고 재발하지 않았다.
- `POST /auth/api-key` 400 뒤 `derive-api-key` 200으로 정상 초기화되는 fallback이 매 build
  ERROR level로 남는다. noisy log이지만 인증 실패로 끝난 것은 아니다.

### Legacy live jobs

`polybot-red`는 close-only라 신규 BUY는 0이다. 그러나 2026-07-25의 HOLDING 한 건은
confirmed BUY가 없고 wallet에도 없으며, invalid token/zero balance로 누적 5,536회 SELL을
시도했다. 최신 build도 invalid-token 오류를 1회 요청당 3개 로그로 남겼다. 더 중요한 것은
공개 wallet snapshot의 유효 position 약 70개/`$1,098`이 current Date DB에는 하나도
매핑되지 않았다는 점이다. 현재 close-only loop만으로 account wind-down이 완결된다고 볼
수 없다.

`polybot-yellow`는 조사 구간에 BUY 88, SELL/COMPLETED 79로 활발했다. 그러나 exact fill
coverage는 81.0%이고, 13건의 BUY/SELL 수량 불일치 중 일부는 venue quantization dust지만
2건은 약 6.6주 BUY 후 SELL 5주만 체결된 큰 잔량이다. confirmed SELL이 없는 COMPLETED도
2건이다. 최신 cycle에서 과거 order reconciliation 오류 16건이 계속된다. 기록상 오픈
6행 중 공개 wallet과 일치한 것은 1행뿐이었다.

`polybot-cherry`는 latest snapshot에 HOLDING 15행, unresolved submission 40건
(BUY 18, SELL 22)이 있다. 최신 cycle의 order reconciliation은 40개 확인 중 오류 27개였고,
청산 조건의 실제 보유 2건이 격리돼 SELL 0건으로 끝났다. 그 뒤 BUY 1건을 추가하여 DB
position은 16개가 됐다. 공개 wallet의 유효 15 token/약 `$169` 중 여러 token은 DB에서
이미 COMPLETED 또는 UNFILLED로도 기록돼 있어 상태가 일대일로 맞지 않는다.

`polybot-orange`는 current DB의 HOLDING 55 + QUARANTINED 8행 모두 공개 wallet에 없다.
confirmed BUY 이력 있는 14행과 fill 없는 49행으로 나뉜다. 반대로 wallet에는 약 `$548`의
유효 token 한 개가 있지만 current DB에 없다. DB를 다시 clean하면 이 노출은 더 찾기
어려워지므로 clean build는 해법이 아니다.

### Blueberry·Melon과 zero-fee 결함

Eagle과 Melon 3개 job의 각 한 건은 다음 조건이 모두 같다.

- BUY confirmed 5.71주
- SELL confirmed 5.71주
- order status `MATCHED`, reconciliation 완료
- public wallet에서 해당 token 0주
- fill의 `liquidity_role=TAKER`, `fee_rate_bps=0`, `fee_amount_usdc=NULL`
- trade status만 `PENDING_SELL`, `sell_confirmed_size=NULL`

repository가 `fee_amount_usdc`만 읽고 `fee_rate_bps=0`을 0 fee 증거로 사용하지 않아
`fee_complete=False`가 된다. 그래서 매 cycle 이미 완료된 SELL을 다시 검사하며 position
slot을 계속 사용한다. 같은 구현이 Golden Papaya, Queen, Quince에도 복제돼 있다.

Blueberry A/B의 cadence와 sweep은 정상이다. A는 조사 구간 1 BUY/1 SELL, B는 신규 체결
0이며 이는 +2pp/+5pp 처치 차이와 맞는다. 다만 Eagle `PENDING_SELL`과 Fox wallet의
current DB 미매핑 소액 position은 별도 정리가 필요하다.

### Strict evidence audit

닫힌 일 구간 strict 결과는 `CRITICAL 4 / HIGH 30 / MEDIUM 18`이다.

- CRITICAL 4개는 Golden Cherry(Yellow)와 Elderberry의
  `completed_trade_fill_gap`, `closed_trade_fill_quantity_mismatch`다.
- fill fee 미확정은 Yellow 53.1%, Elderberry 80.0%였다.
- Papaya/Queen/Quince의 `run_schedule_gap`, archive window, sweep/catalog HIGH는 clean
  restart가 요청한 UTC 일의 중간에 있었는데 하루 전체를 요구한 결과다. 현재 cohort의
  cursor-complete sweep과 cadence는 정상이며 이 HIGH를 parameter 결함으로 해석하지 않는다.
- audit의 `logs_missing`은 DB-only auditor에 log path를 연결하지 않은 MEDIUM이다. 실제 bot
  log와 Jenkins console은 별도로 동기화해 검사했다.

CRITICAL/HIGH가 있으므로 이 자료로 수익성, 승격 또는 파라미터 최적화를 주장하지 않는다.

## 공개 wallet 대사의 제한

Private key 없이 Polymarket 공개 positions API를 사용했고 주소·token·시장명은 기록하지
않았다. `redeemable=false`, 5주 이상만 본 시점 snapshot이므로 현재가와 건수는 변할 수
있다. 이 값은 P&L이 아니라 DB 상태 불일치를 찾는 안전 점검이다.

Cat/Dog/Queen/King/Bear에는 clean restart 이후 current DB trade가 0인데도 각각 약
`$4.49/$4.49/$8.97/$9.14/$4.49`의 non-redeemable wallet position이 current DB에 없다.
Fox/Lime/Wolf에도 약 `$12.03/$4.49/$4.61`의 미매핑 소액 position이 있다. 이전 strategy
epoch 또는 clean 전 position일 가능성이 높으나 현재 봇은 이를 관리하지 않는다.

## 권고 순서

1. **Cherry를 우선 보호한다.** 신규 BUY를 잠시 막고, remote DB에서 public wallet 및
   CLOB open-order catalog와 unresolved intent 40건을 dry-run 대사한다. 현재 청산 대상
   2건이 격리에서 풀리는 것을 먼저 확인해야 한다.
2. **Orange를 clean하지 않는다.** remote `reconcile_positions.py` dry-run을 다시 실행해
   63행 종결 대상과 미매핑 wallet position을 분리한 뒤, online backup과 확인 토큰을
   거쳐 stale DB 행만 정리한다.
3. **Red는 Date DB 한 행이 아니라 account-wide wind-down으로 본다.** current DB 밖의
   wallet position 70개를 만기/보유 의도별로 분류하고 별도 관리한다.
4. **zero-fee reader를 5개 source에 수정한다.** `fee_amount_usdc IS NULL AND
   fee_rate_bps = 0`만 exact zero fee로 인정하고, 비영(0이 아닌) rate에서 amount가 없으면
   계속 fail closed한다. 회귀 테스트 후 네 `PENDING_SELL`을 one-time repair한다.
5. Yellow/Elderberry는 wallet·fill ledger가 맞기 전 파라미터를 바꾸지 않는다. 특히
   COMPLETED인데 wallet 잔량이 있는 row와 stale intent를 먼저 종결한다.
6. 잔여 wallet position은 clean restart된 계정 전체에서 한 번에 inventory한다. 새 DB에
   없는 position을 무시한 채 추가 clean을 하면 관리 공백이 커진다.
7. 거래 복구 뒤 Jenkins retention을 의도한 14일로 통일한다.

이 감사에서는 위 조치를 자동 실행하지 않았다. 실계좌 DB mutation과 job 일시중단은 별도
승인·백업·재검증 단위로 수행해야 한다.

## 검증

- `daily-rsync locate`: 16개 모두 `analysis_ready=true`
- `daily-rsync verify`: 16/16 SUCCESS, failed 0, conflict 0
- `polybot-retro audit --strict`: 의도대로 exit 1, 보고서 생성
- `daily-rsync` lint: PASS
- `daily-rsync` tests: **111 passed**, unrelated Starlette deprecation warning 1개
- 최신 Jenkins redacted console: 16/16 `Finished: SUCCESS`, 16/16 `RUN_AUDIT 성공`
- cleanup 탐지: 16/16 false
- 민감한 주소·키·token ID: 보고서 및 커밋에 미포함
