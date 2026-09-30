"""Schema comparison must not hide changes inside quoted SQL literals."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from polybot_observability.market_data_sql_schema import canonical_sql_key, SQLSchemaKeyError
from polybot_observability import market_data_sql_schema as schema_keys


@pytest.fixture(autouse=True)
def clear_schema_cache():
    schema_keys.clear_canonical_sql_cache()
    yield
    schema_keys.clear_canonical_sql_cache()


def test_ascii_keyword_folding_and_comments_keep_token_boundaries():
    a="CREATE TABLE IF NOT EXISTS t ( value TEXT NOT NULL, n INTEGER DEFAULT 7 )"
    b="create\t/* note */ table -- note\n if /* gap */ not exists t(value text not null,n integer default 7)"
    assert canonical_sql_key(a)==canonical_sql_key(b)
    assert canonical_sql_key(a)==canonical_sql_key("CREATE TABLE t(value TEXT NOT NULL,n INTEGER DEFAULT 7)")
    assert canonical_sql_key(a,False)!=canonical_sql_key("CREATE TABLE t(value TEXT NOT NULL,n INTEGER DEFAULT 7)",False)
    assert canonical_sql_key('SELECT a/* comment */b')!=canonical_sql_key('SELECT ab')
    assert canonical_sql_key('SELECT a - - b')!=canonical_sql_key('SELECT a -- b')
    assert canonical_sql_key('SELECT a < = b')!=canonical_sql_key('SELECT a <= b')


@pytest.mark.parametrize('prefix',['CREATE TABLE','CREATE TEMP TABLE','CREATE TEMPORARY VIEW','CREATE UNIQUE INDEX','CREATE TRIGGER','CREATE VIRTUAL TABLE'])
def test_if_not_exists_removal_is_confined_to_real_create_prefix(prefix):
    assert canonical_sql_key(prefix+' IF NOT EXISTS thing')==canonical_sql_key(prefix+' thing')
    assert canonical_sql_key(prefix+' ifnotexists')!=canonical_sql_key(prefix+' thing')
    a="CREATE TABLE t(x TEXT DEFAULT 'IF NOT EXISTS', y TEXT DEFAULT 'ifnotexists')"
    b="CREATE TABLE t(x TEXT DEFAULT '', y TEXT DEFAULT '')"
    assert canonical_sql_key(a)!=canonical_sql_key(b)
    assert canonical_sql_key('SELECT IF NOT EXISTS thing')!=canonical_sql_key('SELECT thing')


@pytest.mark.parametrize('opener,closer',[("'","'"),('"','"'),('`','`'),('[',']')])
def test_all_quoted_regions_preserve_case_space_and_comment_characters(opener,closer):
    first='CREATE TABLE t(x DEFAULT '+opener+'A B/*literal*/--x'+closer+')'
    case='CREATE TABLE t(x DEFAULT '+opener+'a b/*literal*/--x'+closer+')'
    space='CREATE TABLE t(x DEFAULT '+opener+'AB/*literal*/--x'+closer+')'
    assert canonical_sql_key(first)!=canonical_sql_key(case)
    assert canonical_sql_key(first)!=canonical_sql_key(space)


def test_escaped_quote_regions_are_not_split_or_unescaped():
    sql='CREATE TABLE "table""name" (`column``name` TEXT DEFAULT \'we\'\'re\', [a b] TEXT)'
    tokens=canonical_sql_key(sql)
    assert ('quoted-identifier','"table""name"') in tokens
    assert ('backtick-identifier','`column``name`') in tokens
    assert ('string',"'we''re'") in tokens
    assert ('bracket-identifier','[a b]') in tokens
    with sqlite3.connect(':memory:') as c:
        c.execute(sql)
        assert canonical_sql_key(c.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchone()[0])==tokens


def test_unicode_identifier_case_is_not_unicode_casefolded():
    assert canonical_sql_key('CREATE TABLE Straße(x TEXT)')!=canonical_sql_key('CREATE TABLE STRASSE(x TEXT)')
    assert canonical_sql_key('CREATE TABLE Δ(x TEXT)')!=canonical_sql_key('CREATE TABLE δ(x TEXT)')
    assert canonical_sql_key('CREATE TABLE PrefixΔ(x TEXT)')==canonical_sql_key('create table prefixΔ(X text)')


def test_blob_adjacency_and_literal_bytes_are_preserved():
    assert canonical_sql_key("CREATE TABLE t(x DEFAULT x'AB00')")==canonical_sql_key("create table t(X default X'AB00')")
    assert canonical_sql_key("CREATE TABLE t(x DEFAULT x'AB00')")!=canonical_sql_key("CREATE TABLE t(x DEFAULT x 'AB00')")
    assert canonical_sql_key("CREATE TABLE t(x DEFAULT x'AB00')")!=canonical_sql_key("CREATE TABLE t(x DEFAULT x'ab00')")
    assert ('blob',"x''") in canonical_sql_key("CREATE TABLE t(x BLOB DEFAULT x'')")


def test_real_sqlite_check_case_change_is_detected():
    original="CREATE TABLE t(phase TEXT CHECK(phase IN ('PRESEASON','REGULAR')))"
    changed=original.replace("'PRESEASON'","'preseason'")
    assert canonical_sql_key(original)!=canonical_sql_key(changed)
    for sql,allowed in [(original,True),(changed,False)]:
        with sqlite3.connect(':memory:') as c:
            c.execute(sql)
            if allowed:c.execute("INSERT INTO t VALUES('PRESEASON')")
            else:
                with pytest.raises(sqlite3.IntegrityError):c.execute("INSERT INTO t VALUES('PRESEASON')")


def test_real_sqlite_quoted_default_space_change_is_detected():
    original="CREATE TABLE t(x TEXT DEFAULT 'a b')"
    changed="CREATE TABLE t(x TEXT DEFAULT 'ab')"
    assert canonical_sql_key(original)!=canonical_sql_key(changed)
    values=[]
    for sql in (original,changed):
        with sqlite3.connect(':memory:') as c:
            c.execute(sql);c.execute('INSERT INTO t DEFAULT VALUES')
            values.append(c.execute('SELECT x FROM t').fetchone()[0])
    assert values==['a b','ab']


@pytest.mark.parametrize('sql',[
    '', '-- comment only', '/* comment only */',
    "CREATE TABLE t(x DEFAULT 'unfinished)", 'CREATE TABLE "unterminated(x TEXT)',
    'CREATE TABLE `unterminated(x TEXT)', 'CREATE TABLE [unterminated(x TEXT)',
    'CREATE TABLE t(x TEXT) /* unterminated', 'CREATE TABLE t(x TEXT) */',
    'CREATE TABLE t(x TEXT', 'CREATE TABLE t(x TEXT))',
    "CREATE TABLE t(x DEFAULT x'1')", "CREATE TABLE t(x DEFAULT x'0G')",
    'CREATE TABLE t(x DEFAULT 12word)', 'CREATE TABLE t(x DEFAULT 1e+)',
    'CREATE TABLE t(x DEFAULT 0x)', 'CREATE TABLE t(x DEFAULT 1..2)',
    'CREATE TABLE t(x DEFAULT ?)', 'CREATE TABLE t(x DEFAULT @parameter)',
    'CREATE TABLE t(x DEFAULT :parameter)', 'CREATE TABLE t(x DEFAULT $parameter)',
    'CREATE TABLE t(x DEFAULT #unknown)', 'CREATE TABLE t(x DEFAULT !7)',
    'CREATE TABLE t(x TEXT)\x00', 'CREATE TABLE IF NOT EXISTS',
])
def test_unterminated_or_malformed_schema_text_is_rejected(sql):
    with pytest.raises(SQLSchemaKeyError):canonical_sql_key(sql)


def test_actual_public_scalar_projection_and_packet_definitions_compare_to_sqlite():
    from polybot_observability.market_data_scalars import SCALAR_TABLE_SQL,SCALAR_INDEX_SQL,SCALAR_TRIGGER_SQL
    from polybot_observability.market_data_projections import PROJECTION_TABLE_SQL,PROJECTION_INDEX_SQL,PROJECTION_TRIGGER_SQL
    from polybot_observability.market_data_private_packets import _SCHEMA as PACKET_SCHEMA
    definitions={**SCALAR_TABLE_SQL,**PROJECTION_TABLE_SQL,**SCALAR_INDEX_SQL,**PROJECTION_INDEX_SQL,
                 **SCALAR_TRIGGER_SQL,**PROJECTION_TRIGGER_SQL,**PACKET_SCHEMA}
    with sqlite3.connect(':memory:') as c:
        for name,sql in definitions.items():
            c.execute(sql)
            stored=c.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()[0]
            assert canonical_sql_key(sql)==canonical_sql_key(stored),name
            if sql.startswith('CREATE TABLE '):
                assert canonical_sql_key(sql.replace('CREATE TABLE ','CREATE TABLE IF NOT EXISTS ',1))==canonical_sql_key(stored)


def test_all_registered_raw_object_sql_is_lexically_supported():
    from polybot_observability.market_data_raw_profiles import raw_profile,BLACK_PROFILE_ID,WATERMELON_PROFILE_ID,COCONUT_PROFILE_ID
    for profile_id in (BLACK_PROFILE_ID,WATERMELON_PROFILE_ID,COCONUT_PROFILE_ID):
        profile=raw_profile(profile_id)
        statements=[p.source_sql for p in profile.tables.values()]
        statements += [sql for _,_,_,sql in profile.source_objects]
        for sql in statements:assert canonical_sql_key(sql)


def test_exact_input_cache_does_not_hide_literal_or_mode_changes(monkeypatch):
    original=schema_keys._tokens;calls=[]
    def observed(sql):calls.append(sql);return original(sql)
    monkeypatch.setattr(schema_keys,'_tokens',observed)
    sql="CREATE TABLE IF NOT EXISTS t(x DEFAULT 'A B')"
    old=canonical_sql_key(sql)
    assert canonical_sql_key(sql) is old
    changed=canonical_sql_key(sql.replace("'A B'","'AB'"))
    strict=canonical_sql_key(sql,False)
    assert changed!=old and strict!=old
    assert len(calls)==3
    assert schema_keys.canonical_sql_cache_info()['entries']==3


def test_giant_sql_is_validated_but_never_retained():
    sql="CREATE TABLE t(x DEFAULT '"+'x'*(schema_keys.CACHE_SQL_BYTE_LIMIT+1)+"')"
    assert canonical_sql_key(sql)
    assert schema_keys.canonical_sql_cache_info()['entries']==0
    assert schema_keys.canonical_sql_cache_info()['tracked_bytes']==0


def test_cache_combined_byte_and_entry_bounds(monkeypatch):
    monkeypatch.setattr(schema_keys,'CACHE_BYTE_LIMIT',6000)
    monkeypatch.setattr(schema_keys,'CACHE_ENTRY_LIMIT',3)
    for index in range(20):
        canonical_sql_key(f"CREATE TABLE t{index}(x DEFAULT 'value-{index}')")
        info=schema_keys.canonical_sql_cache_info()
        assert info['entries']<=3 and info['tracked_bytes']<=6000
    assert info['entries']>0
    schema_keys.clear_canonical_sql_cache()
    monkeypatch.setattr(schema_keys,'CACHE_BYTE_LIMIT',1)
    assert canonical_sql_key('CREATE TABLE t(x TEXT)')
    assert schema_keys.canonical_sql_cache_info()['entries']==0


def test_cache_is_thread_safe_and_returns_immutable_keys():
    values=[f"CREATE TABLE t{i%7}(x DEFAULT 'Value {i%7}')" for i in range(160)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(canonical_sql_key,values))
    assert all(result==canonical_sql_key(value) for result,value in zip(results,values))
    info=schema_keys.canonical_sql_cache_info()
    assert info['entries']==7 and 0<info['tracked_bytes']<=info['byte_limit']
    with pytest.raises(TypeError):results[0][0]=('word','corrupt')
