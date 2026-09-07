"""Venue-role metadata must not change legacy slots, tokens, quotes, or eligibility."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

TOOLS=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sports_role_visual',TOOLS/'sports_visual_data.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

TEAMS=[{'id':1,'name':'Tampa Bay Rays','ordering':'away'},
       {'id':2,'name':'Texas Rangers','ordering':'home'}]
ROW={'sport_family':'mlb','outcome_side':'DIRECT','result_kind':'HOME','event_id':'event',
     'condition_id':'condition','token_id':'rays','outcome':'Tampa Bay Rays','midpoint':.695}


def annotate(row=ROW,teams=TEAMS,**kwargs):
    return v.direct_role_metadata(row,teams,scope='fixture',evidence_event_id='event',**kwargs)


def test_explicit_ordering_overlays_wrong_positional_role_without_mutation():
    before=deepcopy(ROW)
    result=annotate()
    assert result['verified_role']=='AWAY'
    assert result['verified_team_name']=='Tampa Bay Rays'
    assert result['legacy_result_kind']=='HOME'
    assert ROW==before
    assert annotate(teams=list(reversed(TEAMS)))['verified_role']=='AWAY'


def test_missing_or_nonunique_ordering_never_falls_back_to_home():
    for teams in ([{'name':t['name']} for t in TEAMS],
                  [{**t,'ordering':'home'} for t in TEAMS],None,[]):
        result=annotate(teams=teams)
        assert result['verified_role']=='UNKNOWN'
        assert result['legacy_result_kind']=='HOME'
        assert result['verified_team_name'] is None


def test_ambiguous_token_label_or_failed_identity_is_unknown():
    teams=[{**t,'alias':'Club'} for t in TEAMS]
    assert annotate(row={**ROW,'outcome':'Club'},teams=teams)['verified_role']=='UNKNOWN'
    assert annotate(identity_proven=False)['verified_role']=='UNKNOWN'
    assert v.direct_role_metadata(ROW,TEAMS,scope='fixture',evidence_event_id='another')['verified_role']=='UNKNOWN'


def test_soccer_propositions_are_not_reinterpreted_by_direct_two_team_overlay():
    assert annotate(row={**ROW,'sport_family':'soccer','outcome_side':'YES'})=={}


def test_conflicting_roles_remain_unknown_and_do_not_rewrite_the_legacy_slot():
    token={'token_id':'rays','result_kind':'HOME','label':'Tampa Bay Rays','payout':1.0}
    v._merge_role_metadata(token,{**ROW,**annotate()})
    before_numeric={k:token[k] for k in ('token_id','result_kind','label','payout')}
    flipped=[{**t,'ordering':'home' if t['ordering']=='away' else 'away'} for t in TEAMS]
    v._merge_role_metadata(token,{**ROW,**annotate(teams=flipped)})
    assert token['verified_role']=='UNKNOWN'
    assert token['role_verification_status']=='CONFLICTING_EXPLICIT_SOURCE_ROLES'
    v._merge_role_metadata(token,{**ROW,**annotate()})
    assert token['verified_role']=='UNKNOWN'
    assert {k:token[k] for k in before_numeric}==before_numeric


def white_fixture():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''
      CREATE TABLE orderbook_snapshots(snapshot_id TEXT,run_id TEXT,token_id TEXT,observed_at TEXT,best_bid REAL,best_ask REAL);
      CREATE TABLE outcome_observations(run_id TEXT,token_id TEXT,condition_id TEXT,event_id TEXT,outcome_label TEXT,outcome_index INTEGER,market_observation_id TEXT);
      CREATE TABLE market_observations(observation_id TEXT,event_title TEXT,question TEXT,normalized_json TEXT,classification_evidence_json TEXT,event_observation_id TEXT,eligible INTEGER,run_id TEXT,observed_at TEXT);
      CREATE TABLE event_observations(event_observation_id TEXT,event_id TEXT,observed_at TEXT,league_code TEXT,event_slug TEXT,teams_json TEXT,run_id TEXT);
      CREATE TABLE orderbook_levels(snapshot_id TEXT,side TEXT,price REAL,size REAL,level_index INTEGER);
    ''')
    norm={'tokens':['rays','rangers'],'labels':['Tampa Bay Rays','Texas Rangers'],'event_id':'event','condition_id':'condition'}
    classification={'sport_family':'mlb','result_kinds_by_index':['HOME','AWAY']}
    c.execute('INSERT INTO orderbook_snapshots VALUES(?,?,?,?,?,?)',('book','run','rays','2026-09-07T01:00:01Z',.69,.70))
    c.execute('INSERT INTO outcome_observations VALUES(?,?,?,?,?,?,?)',('run','rays','condition','event','Tampa Bay Rays',0,'market'))
    c.execute('INSERT INTO market_observations VALUES(?,?,?,?,?,?,?,?,?)',('market','Rays vs Rangers','winner',json.dumps(norm),json.dumps(classification),'event-observed',1,'run','2026-09-07T01:00:00Z'))
    c.execute('INSERT INTO event_observations VALUES(?,?,?,?,?,?,?)',('event-observed','event','2026-09-07T01:00:00Z','MLB','slug',json.dumps(TEAMS),'run'))
    c.executemany('INSERT INTO orderbook_levels VALUES(?,?,?,?,?)',[('book','BID',.69,100,0),('book','ASK',.70,100,0)])
    return c


def test_white_exact_observation_annotation_preserves_numeric_fields_and_stored_role():
    c=white_fixture()
    rows=list(v.white_rows(c,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z'))
    assert len(rows)==1
    row=rows[0]
    assert row['result_kind']==row['legacy_result_kind']=='HOME'
    assert row['verified_role']=='AWAY'
    assert row['token_id']=='rays' and row['timestamp']=='2026-09-07T01:00:01Z'
    assert row['best_bid']==.69 and row['best_ask']==.70
    assert v.depth_prices(row['book'],'rays')==(.7,.69)
    assert row['role_evidence']['event_observation_id']=='event-observed'
    norm=json.loads(c.execute('SELECT normalized_json FROM market_observations').fetchone()[0]);norm['tokens'][0]='wrong'
    c.execute('UPDATE market_observations SET normalized_json=?',(json.dumps(norm),))
    assert next(v.white_rows(c,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z'))['verified_role']=='UNKNOWN'


def test_legacy_direct_snapshots_explicitly_keep_unknown_venue_role():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('''
      CREATE TABLE market_snapshots(id INTEGER,condition_id TEXT,event_id TEXT,token_id TEXT,outcome TEXT,outcome_side TEXT,result_kind TEXT,sport_family TEXT,run_id TEXT,timestamp TEXT,book_json TEXT);
      CREATE TABLE market_catalog(condition_id TEXT,event_id TEXT,event_title TEXT,event_slug TEXT,question TEXT,league_code TEXT);
    ''')
    book={'token_id':'rays','asks':[{'price':.7,'size':100}],'bids':[{'price':.69,'size':100}]}
    c.execute('INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',(1,'condition','event','rays','Tampa Bay Rays','DIRECT','HOME','mlb','run','2026-09-07 01:00:01',json.dumps(book)))
    row=next(v.trading_rows(c,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z'))
    assert row['verified_role']=='UNKNOWN'
    assert row['role_evidence_scope']=='LEGACY_NO_POINT_IN_TIME_TEAM_ORDERING'
    assert row['result_kind']=='HOME' and row['outcome']=='Tampa Bay Rays'


def raw_direct_fixture():
    import hashlib
    spec=importlib.util.spec_from_file_location('role_raw_fixture',TOOLS/'tests/test_sports_raw_archive_data.py')
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    f=fixture.RawFixture();c=f.c
    cfg=json.loads(c.execute('SELECT config_json FROM strategy_configs').fetchone()[0]);cfg['trading']['sport_family']='mlb'
    c.execute('UPDATE strategy_configs SET config_json=?',(json.dumps(cfg),))
    c.execute("INSERT INTO run_audits VALUES('raw','shadow','sim','cfg','SUCCESS','2026-09-07 01:00:00','2026-09-07 01:00:02')")
    c.execute("INSERT INTO raw_book_cycles VALUES('raw','cfg',?,'shadow','mlb','full-sports-raw-v1','2026-09-07 01:00:00','2026-09-07 01:00:02','COMPLETE',1,2,2,'{}')",(fixture.DIGEST,))
    slots={'HOME:DIRECT':{'condition_id':'condition','token_id':'rays','outcome':'Tampa Bay Rays'},
           'AWAY:DIRECT':{'condition_id':'condition','token_id':'rangers','outcome':'Texas Rangers'}}
    payload={'contract':'full-sports-raw-v1','event':{'id':'event','title':'Rays vs Rangers','teams':TEAMS,'live':True,'ended':False,'active':True,'closed':False},
             'slots':slots,'market_context':[{'conditionId':'condition','active':True,'closed':False,'acceptingOrders':True,'enableOrderBook':True,'liquidityNum':10000,'volumeNum':10000,'feesEnabled':True,'feeSchedule':{'rate':.05,'exponent':1,'takerOnly':True}}],'terminal_proofs':[]}
    c.execute("INSERT INTO raw_event_observations VALUES('raw-event','raw','event','LIVE','current_discovery',2,2,'2026-09-07 01:00:00',?)",(json.dumps(payload),))
    for i,(slot,identity) in enumerate(slots.items()):
        ask=.7 if i==0 else .3
        book=json.dumps({'token_id':identity['token_id'],'asks':[{'price':ask,'size':100}],'bids':[{'price':ask-.01,'size':100}]})
        c.execute('INSERT INTO raw_book_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('book'+str(i),'raw','event',slot,'condition',identity['token_id'],'FULL','fixture','2026-09-07 01:00:00','2026-09-07 01:00:01',book,hashlib.sha256(book.encode()).hexdigest()))
    c.commit();return c


def test_raw_direct_ordering_annotations_keep_positional_slot_and_validity():
    c=raw_direct_fixture();rows=list(v.trading_rows(c,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z'))
    assert len(rows)==2
    rays=next(r for r in rows if r['token_id']=='rays')
    assert rays['raw_point_in_time_identity_proven'] and rays['raw_book_valid'] and rays['raw_entry_set_complete']
    assert rays['result_kind']==rays['legacy_result_kind']=='HOME'
    assert rays['verified_role']=='AWAY' and rays['verified_team_name']=='Tampa Bay Rays'
    assert rays['role_evidence_scope']=='SAME_RAW_EVENT_OBSERVATION_EXPLICIT_ORDERING'


def test_reader_annotations_reach_grid_snapshots_without_changing_slots(tmp_path):
    import sys
    sys.path.insert(0,str(TOOLS));import conservative_sports_grid as grid
    c=raw_direct_fixture();path=tmp_path/'raw.db'
    with sqlite3.connect(path) as target:c.backup(target)
    source={'id':'polybot-grey:role-fixture','strategy':'golden-peach','local_path':str(path),'local_sha256':v.sha256(path),'pinned':True}
    events,_=grid.read_source(source,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z')
    snap=next(s for s in events[0].groups[0].snaps if s.token=='rays')
    assert snap.slot==('HOME','DIRECT') and snap.verified_role=='AWAY'
    assert snap.verified_team_name==snap.outcome_label=='Tampa Bay Rays'
    assert events[0].groups[0].complete


def test_white_other_run_or_future_event_evidence_cannot_verify_a_role():
    for table,field,value in [('event_observations','run_id','other'),
                             ('market_observations','run_id','other'),
                             ('event_observations','observed_at','2026-09-07T01:01:00Z'),
                             ('market_observations','observed_at','2026-09-07T01:01:00Z')]:
        c=white_fixture();c.execute(f'UPDATE {table} SET {field}=?',(value,))
        row=next(v.white_rows(c,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z'))
        assert row['verified_role']=='UNKNOWN'
        assert row['result_kind']=='HOME' and row['token_id']=='rays' and row['best_bid']==.69


def test_same_role_with_conflicting_explicit_team_ids_is_sticky_unknown():
    row={**ROW,'outcome':'NY'}
    first=[{'id':1,'name':'New York Yankees','alias':'NY','ordering':'home'},TEAMS[0]]
    second=[{'id':2,'name':'New York Mets','alias':'NY','ordering':'home'},TEAMS[0]]
    # The away team has a distinct ID; only the NY team's source identity changes.
    first[1]={**first[1],'id':3};second[1]={**second[1],'id':3}
    a=annotate(row=row,teams=first);b=annotate(row=row,teams=second)
    assert a['verified_role']==b['verified_role']=='HOME'
    token={'result_kind':'HOME','label':'NY','token_id':'rays'}
    v._merge_role_metadata(token,{**row,**a});v._merge_role_metadata(token,{**row,**b})
    assert token['verified_role']=='UNKNOWN' and token['verified_team_name'] is None
    assert token['role_verification_status']=='CONFLICTING_EXPLICIT_TEAM_IDENTITIES'
    assert len(token['role_evidence']['conflicting_proofs'])==2
    v._merge_role_metadata(token,{**row,**a})
    assert token['verified_role']=='UNKNOWN' and token['result_kind']=='HOME'


def test_raw_role_observation_reference_is_not_claimed_as_http_receipt():
    c=raw_direct_fixture()
    row=next(v.trading_rows(c,'2026-09-07T00:00:00Z','2026-09-08T00:00:00Z'))
    assert row['role_evidence']['observed_at_basis']=='RAW_EVENT_CYCLE_REFERENCE_NOT_HTTP_RECEIPT'
    assert row['role_evidence']['available_by_publication_utc']=='2026-09-07 01:00:02'


def test_missing_ids_do_not_allow_conflicting_canonical_team_names_to_last_win():
    row={**ROW,'outcome':'NY'}
    a=[{'name':'New York Yankees','alias':'NY','ordering':'home'},TEAMS[0]]
    b=[{'name':'New York Mets','alias':'NY','ordering':'home'},TEAMS[0]]
    token={'result_kind':'HOME','label':'NY','token_id':'rays'}
    v._merge_role_metadata(token,{**row,**annotate(row=row,teams=a)})
    v._merge_role_metadata(token,{**row,**annotate(row=row,teams=b)})
    assert token['verified_role']=='UNKNOWN'
    assert token['role_verification_status']=='CONFLICTING_EXPLICIT_TEAM_IDENTITIES'
    # A stable explicit team ID can legitimately carry an updated display name.
    a[0]['id']=99;b[0]['id']=99
    token={'result_kind':'HOME','label':'NY','token_id':'rays'}
    v._merge_role_metadata(token,{**row,**annotate(row=row,teams=a)})
    v._merge_role_metadata(token,{**row,**annotate(row=row,teams=b)})
    assert token['verified_role']=='HOME'
