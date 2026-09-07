"""Synthetic SQLite pins only; no polybot imports or network."""
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import guava_collection_health as health

START, END = "2026-09-06T00:00:00Z", "2026-09-06T00:01:00Z"
def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
def seal(p):
    p.with_name("manifest.json").write_text(packed({"pinned_path": str(p), "source": str(p.parent / "absent-source.db"), "created_at": END, "quick_check": ["ok"], "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}))
def change(p, sql, values=()):
    with sqlite3.connect(p) as c: c.execute(sql, values)
    seal(p)
def fixture(root, shard=0, *, family="mlb", eligible=True, empty=False):
    p = root.resolve() / "pinned" / str(shard) / "snapshot.db"; p.parent.mkdir(parents=True)
    job = list(health.RUNTIMES)[shard]
    config = {"shard_index": shard, "shard_count": 4, "trading": {"cadence_seconds": 60}}
    identity = dict(strategy_name="golden-guava", job_name=job, mode="sim", data_contract=health.CONTRACT, config_hash="c", strategy_source_digest="s")
    config.update(identity)
    cohort = hashlib.sha256(packed(identity).encode()).hexdigest()
    event = next(str(n) for n in range(100) if health.shard_for(str(n)) == shard)
    raw = packed({"id": event}).encode(); sha = hashlib.sha256(raw).hexdigest(); blob = gzip.compress(raw)
    tokens = [str(n) for n in range(6 if family == "soccer" else 2)] if eligible else []
    with sqlite3.connect(p) as c:
        c.executescript("""
        CREATE TABLE collection_contracts(job_name,mode,data_contract,strategy_name);
        CREATE TABLE strategy_configs(config_hash,strategy_source_digest,config_json,snapshot_sha256);
        CREATE TABLE run_audits(run_id TEXT PRIMARY KEY,started_at,strategy_name,job_name,mode,data_contract,config_hash,strategy_source_digest,cohort_key,contract_json);
        CREATE TABLE run_events(run_id REFERENCES run_audits(run_id),status,occurred_at);
        CREATE TABLE cycles(run_id REFERENCES run_audits(run_id),cohort_key,observed_at,summary_json);
        CREATE TABLE source_requests(run_id,payload_gzip,payload_sha256);
        CREATE TABLE events(run_id,event_id,cohort_key,sport_family,observed_at,eligible,exclusion_reason,expected_token_ids_json,raw_gzip,raw_sha256);
        CREATE TABLE book_attempts(run_id,event_id,token_id,status,raw_gzip,raw_sha256,condition_id);
        """)
        c.execute("INSERT INTO collection_contracts VALUES(?,?,?,?)", (job,"sim",health.CONTRACT,"golden-guava"))
        if not empty:
            c.execute("INSERT INTO strategy_configs VALUES(?,?,?,?)", ("c","s",packed(config),hashlib.sha256(packed(config).encode()).hexdigest()))
            c.execute("INSERT INTO run_audits VALUES(?,?,?,?,?,?,?,?,?,?)", ("r",START,*identity.values(),cohort,packed(config)))
            c.executemany("INSERT INTO run_events VALUES(?,?,?)", [("r","STARTED",START),("r","SUCCEEDED","2026-09-06T00:00:10Z")])
            c.execute("INSERT INTO cycles VALUES(?,?,?,?)", ("r",cohort,"2026-09-06T00:00:10Z",packed({"census_complete":True})))
            c.execute("INSERT INTO source_requests VALUES(?,?,?)", ("r",blob,sha))
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)", ("r",event,cohort,family,START,int(eligible),None if eligible else "not_live",packed(tokens),blob,sha))
            for t in tokens:
                book=packed({"asset_id":t,"market":"condition","bids":[{"price":.4,"size":10}],"asks":[{"price":.5,"size":10}]}).encode()
                c.execute("INSERT INTO book_attempts VALUES(?,?,?,?,?,?,?)", ("r",event,t,"OK",gzip.compress(book),hashlib.sha256(book).hexdigest(),"condition"))
    seal(p); return p

def test_complete_four_shards_readonly(tmp_path, monkeypatch):
    pins = [fixture(tmp_path,i,family="soccer" if i==0 else "mlb") for i in range(4)]
    original = [p.read_bytes() for p in pins]; connect = sqlite3.connect
    def guard(location, **kwargs):
        assert location.endswith("?mode=ro&immutable=1") and kwargs["uri"] is True
        return connect(location, **kwargs)
    monkeypatch.setattr(health.sqlite3,"connect",guard)
    r = health.analyze(pins,START,END)
    assert r["healthy"] and r["totals"]["expected_books"] == 12
    assert all(p.read_bytes()==b for p,b in zip(pins,original))
    assert r["storage"]["growth_bytes"] is None

def test_no_evidence_and_zero_eligible(tmp_path):
    assert len(health.analyze([],START,END)["required_shards"]) == 4
    pins=[fixture(tmp_path,i,eligible=False) for i in range(4)]
    r=health.analyze(pins,START,END); assert r["checks_passed"] and not r["healthy"]
    assert r["verdict"]=="NO_ELIGIBLE_EVIDENCE"
    p=fixture(tmp_path/"empty",empty=True)
    assert health.analyze([p],START,END)["required_shards"][0]["status"]=="NO_RUN_EVIDENCE"

@pytest.mark.parametrize("damage", ["missing", "gzip", "shard", "shape", "fk", "failed"])
def test_defects_reported(tmp_path, damage):
    pins=[fixture(tmp_path,i) for i in range(4)]; p=pins[0]
    sql={"missing":"DELETE FROM book_attempts WHERE token_id='0'", "gzip":"UPDATE book_attempts SET raw_gzip=X'0102'", "shard":"UPDATE events SET event_id='wrong'", "shape":"UPDATE events SET sport_family='soccer'", "fk":"INSERT INTO run_events VALUES('absent','FAILED','2026-09-06T00:00:10Z')", "failed":"UPDATE run_events SET status='FAILED' WHERE status='SUCCEEDED'"}[damage]
    change(p,sql); r=health.analyze(pins,START,END)
    assert not r["healthy"] and r["files"][0]["errors"]

def test_missing_book_is_not_zero_price_and_halfopen(tmp_path):
    pins=[fixture(tmp_path,i) for i in range(4)];p=pins[0]
    change(p,"UPDATE book_attempts SET status='MISSING',raw_gzip=NULL,raw_sha256=NULL WHERE token_id='0'")
    r=health.analyze(pins,START,END);assert r["verdict"]=="BOOK_GAPS" and r["totals"]["observed_books"]==7
    assert health.analyze(pins,END,"2026-09-06T00:02:00Z")["required_shards"][0]["status"]=="NO_RUN_EVIDENCE"
    change(p,"UPDATE run_events SET occurred_at=? WHERE status='SUCCEEDED'",(END,))
    pending=health.analyze(pins,START,END)
    assert pending["files"][0]["cadence"]["statuses"]=={"PENDING_AT_END":1} and pending["integrity_pass"]

def test_requested_day_denominator_and_raw_identity_empty_side(tmp_path):
    pins=[fixture(tmp_path,i) for i in range(4)];r=health.analyze(pins,START,"2026-09-07T00:00:00Z")
    assert not r["healthy"] and r["integrity_pass"] and r["files"][0]["cadence"]["success_ratio_requested_range"]==1/1440
    p=pins[0];raw=packed({"asset_id":"wrong","market":"wrong","bids":[],"asks":[]}).encode()
    change(p,"UPDATE book_attempts SET raw_gzip=?,raw_sha256=? WHERE token_id='0'",(gzip.compress(raw),hashlib.sha256(raw).hexdigest()))
    r=health.analyze(pins,START,END);assert not r["integrity_pass"] and r["totals"]["empty_bids"]==1

def test_source_splits_duplicate_slots_and_snapshot_validation(tmp_path):
    p=fixture(tmp_path)
    change(p,"INSERT INTO run_audits SELECT 'r2',started_at,strategy_name,job_name,mode,data_contract,config_hash,'other',cohort_key,contract_json FROM run_audits")
    r=health.analyze([p],START,END);assert len(r["files"][0]["cohorts"])==2
    assert r["files"][0]["cadence"]["duplicate_slots"]==1
    p.write_bytes(p.read_bytes()+b"changed");assert "checksum" in health.analyze([p],START,END)["files"][0]["errors"][0]

def test_unsafe_paths_and_output(tmp_path):
    p=fixture(tmp_path);alias=p.parent/"alias.db";alias.symlink_to(p)
    for bad in (alias,Path("relative.db"),p.parent/"latest"/p.name):assert health.analyze([bad],START,END)["files"][0]["errors"]
    Path(str(p)+"-wal").touch();assert "sidecars" in health.analyze([p],START,END)["files"][0]["errors"][0]
    out=tmp_path/"disguised.json";out.write_bytes(p.read_bytes())
    with pytest.raises(SystemExit):health.main(["--start",START,"--end",END,"--output",str(out)])
    with pytest.raises(ValueError):health.analyze([],"2026-09-06T09:00:00+09:00",END)
    with pytest.raises(ValueError):health.analyze([],END,START)
    output=tmp_path/"report.json"
    assert health.main(["--start",START,"--end",END,"--output",str(output)])==1
    assert len(json.loads(output.read_text())["required_shards"])==4


def add_phase_contract(p,phase=30,*,damage=None):
    with sqlite3.connect(p) as c:
        config=json.loads(c.execute('select config_json from strategy_configs').fetchone()[0])
        config['trading']['slot_phase_seconds']=phase
        config['slot_claim_policy']={'phase_seconds':phase,'cadence_seconds':60,'window':'UTC_HALF_OPEN','actual_timestamps_preserved':True}
        claim=health.claimed_window(START,phase)
        contract={**config,'claimed_window':claim}
        if damage=='start':contract['claimed_window']['start_utc']=START
        elif damage=='end':contract['claimed_window']['end_exclusive_utc']=END
        elif damage=='phase':contract['claimed_window']['phase_seconds']=0
        elif damage=='float_phase':contract['claimed_window']['phase_seconds']=float(phase)
        elif damage=='actual':contract['claimed_window']['actual_run_started_at']=END
        elif damage=='missing':del contract['claimed_window']
        elif damage=='other_config':contract={**contract,'shard_count':9}
        encoded=packed(config)
        c.execute('update strategy_configs set config_json=?,snapshot_sha256=?',(encoded,hashlib.sha256(encoded.encode()).hexdigest()))
        c.execute('update run_audits set contract_json=?',(packed(contract),))
        c.execute('update cycles set summary_json=?',(packed({'census_complete':True,'claimed_window':claim}),))
    seal(p)


def test_phase_windows_do_not_replace_legacy_utc_minute_denominator(tmp_path):
    p=fixture(tmp_path);add_phase_contract(p)
    result=health.analyze([p],START,END);file=result['files'][0];cohort=file['cohorts'][0]
    assert result['integrity_pass']
    assert file['cadence']['slots_intersecting_requested_range']==1
    assert file['cadence']['successful_slots']==1
    assert cohort['phase_cadence']['expected_phase_windows']==2
    assert cohort['phase_cadence']['observed_phase_windows']==1
    assert cohort['claimed_windows'][0]['basis']=='RECORDED'
    assert cohort['claimed_windows'][0]['window']['actual_run_started_at']=='2026-09-06T00:00:00.000000Z'


@pytest.mark.parametrize('damage',['start','end','phase','float_phase','actual','missing','other_config'])
def test_corrupt_claim_or_other_config_is_not_ignored(tmp_path,damage):
    p=fixture(tmp_path);add_phase_contract(p,damage=damage)
    result=health.analyze([p],START,END)
    assert not result['integrity_pass']
    assert any('CLAIMED_WINDOW' in e or 'CONFIG_HASH_OR_SNAPSHOT' in e for e in result['files'][0]['errors'])


def test_failed_phase_run_retains_reproducible_claim(tmp_path):
    p=fixture(tmp_path);add_phase_contract(p)
    with sqlite3.connect(p) as c:
        c.execute("delete from cycles")
        c.execute("update run_events set status='FAILED' where status='SUCCEEDED'")
    seal(p)
    result=health.analyze([p],START,END)
    claim=result['files'][0]['cohorts'][0]['claimed_windows'][0]
    assert claim['status']=='FAILED' and claim['basis']=='RECORDED'
    assert claim['window']==health.claimed_window(START,30)


def test_legacy_no_window_is_explicitly_derived_phase_zero(tmp_path):
    p=fixture(tmp_path);result=health.analyze([p],START,END)
    claim=result['files'][0]['cohorts'][0]['claimed_windows'][0]
    assert claim['basis']=='LEGACY_DERIVED_PHASE_ZERO'
    assert claim['window']['phase_seconds']==0
