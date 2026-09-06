from contextlib import contextmanager
import sqlite3

from polybot.db.repository import ResearchRepository


def test_indexed_prior_vwap_matches_global_history_and_skips_null_latest():
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        CREATE TABLE outcome_observations(token_id TEXT, observed_at TEXT, run_id TEXT);
        CREATE INDEX outcome_token_time_idx ON outcome_observations(token_id,observed_at);
        CREATE TABLE signal_decisions(run_id TEXT, token_id TEXT, threshold REAL,
            decided_at TEXT, entry_vwap REAL, UNIQUE(run_id,token_id,threshold));
    ''')
    # Receipt order need not equal decision time. Keep the latest non-null
    # decision even if a later book request failed or receipt time differs.
    for run, token, observed, decided, price in [
        ('a', 'one', '03', '01', .94),
        ('b', 'one', '01', '02', .96),
        ('c', 'one', '04', '03', None),
        ('d', 'two', '02', '02', .98),
        ('e', 'empty', '02', '02', None),
    ]:
        connection.execute('INSERT INTO outcome_observations VALUES(?,?,?)', (token,observed,run))
        for threshold in [.95,.96,.97,.98,.99]:
            connection.execute('INSERT INTO signal_decisions VALUES(?,?,?,?,?)', (run,token,threshold,decided,price))
    repo = object.__new__(ResearchRepository)
    statements = []
    @contextmanager
    def connect():
        yield connection
    repo.connect = connect
    expected = repo.latest_entry_vwaps()
    connection.set_trace_callback(statements.append)
    assert repo.latest_entry_vwaps(['one','two','one','empty','missing']) == expected == {'one':.96,'two':.98}
    queries = [q for q in statements if q.lstrip().startswith('SELECT')]
    assert len(queries) == 4
    for query in queries:
        plan = [str(row) for row in connection.execute('EXPLAIN QUERY PLAN '+query)]
        assert any('SEARCH o USING INDEX outcome_token_time_idx' in row for row in plan)
        assert any('SEARCH d USING INDEX' in row and 'run_id=? AND token_id=?' in row for row in plan)
        assert not any('SCAN ' in row for row in plan)
    statements.clear()
    assert repo.latest_entry_vwaps([]) == {}
    assert not statements
    connection.close()
