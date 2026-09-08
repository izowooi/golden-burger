"""Regression for the observed r5 142-child follow-up queue starvation."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

from polybot.api.gamma_client import EventPage, EventSweep
from polybot.collector import Collector
from polybot.db.raw_repository import RawRepository
from polybot.research_raw import child_registry_corrections
from polybot.utils.retry import canonical_json, iso_utc
from test_collector import NOW, FakeSportsClock, configured, repository_for
from test_raw_followup_isolation import (
    TraceClob, TraceGamma, _request_receipt, _soccer_event, _sidecar_rows,
)


def root_event(index):
    event = _soccer_event()
    event['id'] = f'root-{index}'
    for market in event['markets']:
        market['conditionId'] = f'{index}-' + market['conditionId']
        market['clobTokenIds'] = json.dumps([f'{index}-' + token for token in json.loads(market['clobTokenIds'])])
    return event


def child_event(index):
    event = _soccer_event()
    event.update(id=f'child-{index}', parentEventId=123, title=f'Child {index}', markets=[])
    return event


class MultiGamma(TraceGamma):
    def __init__(self, trace, repository, events, *, now=NOW, discover=True):
        super().__init__(trace, now=now, repository=repository, discover=discover)
        self.events = events

    def fetch_live_events(self, run_id, *, observed_at):
        self.trace.append((run_id, 'core_sweep'))
        events = self.events if self.discover else []
        raw = canonical_json({'events': events, 'next_cursor': None}).encode()
        request = f'sweep-{run_id}'
        _request_receipt(self.repository, run_id=run_id, request_id=request,
            kind='gamma_live_events_keyset:soccer', raw=raw, started=observed_at, received=observed_at)
        return EventSweep((EventPage(1, request, iso_utc(observed_at), hashlib.sha256(raw).hexdigest(),
            raw, tuple(deepcopy(events)), None, None),), True)

    def fetch_event_by_id(self, run_id, event_id):
        self.source = next(e for e in self.events if e['id'] == event_id)
        return super().fetch_event_by_id(run_id, event_id)


def collect(config, parent, trace, events, run_id, now, *, discover=True):
    return Collector(config, parent,
        MultiGamma(trace, parent, events, now=now, discover=discover),
        TraceClob(trace, now=now, ask=.94, repository=parent), FakeSportsClock()
    ).collect(run_id, now=now)


def seed_r5_child_registry(raw, count=142):
    """r5 shape: immutable first child observation plus mutable empty-slot queue."""
    with raw.transaction() as c:
        for index in range(count):
            event = child_event(index)
            c.execute('INSERT INTO raw_tracked_events VALUES(?,?,?,?,?,?,?,?,?)',
                (event['id'], 'soccer', 'TRACKING', '[]', 'legacy', 'legacy', iso_utc(NOW), 12, None))
            raw.insert(c, 'raw_events', {
                'run_id': 'legacy', 'event_id': event['id'], 'family': 'soccer',
                'metadata_status': 'OBSERVED', 'metadata_received_at': iso_utc(NOW),
                'metadata_request_id': 'legacy-request', 'source_kind': 'PARENT_GAMMA_PAGE',
                'source_ref_json': '{}', 'event_json': canonical_json(event),
                'slots_json': '[]', 'identity_valid': 0, 'lifecycle_state': 'TRACKING',
                'terminal_json': None, 'missing_count': 12,
            })


def test_new_children_are_observed_without_consuming_followup_or_quote_targets(tmp_path):
    config = configured(tmp_path)
    parent = repository_for(config)
    trace = []
    events = [root_event(0), *[child_event(i) for i in range(142)]]
    try:
        first = collect(config, parent, trace, events, 'first', NOW)['raw_lifecycle']
        assert first['discovery_only_events'] == 142
        assert first['required_events'] == 1 and first['expected_tokens'] == 3
        assert first['status'] == 'PUBLISHED'
        second = collect(config, parent, trace, events, 'absent', NOW + timedelta(minutes=1), discover=False)['raw_lifecycle']
        assert second['followup_lookups'] == 1 and second['required_events'] == 1
        assert second['trusted_full_books'] == 3
        assert all(not call[2].startswith('child-') for call in trace if call[1] == 'raw_event')
        children = [e for e in _sidecar_rows(config, 'raw_events', 'first') if e['lifecycle_state'] == 'DISCOVERY_ONLY']
        assert len(children) == 142 and all(e['event_json'] and json.loads(e['slots_json']) == [] for e in children)
    finally:
        parent.close()


def test_legacy_142_child_backlog_does_not_starve_fifteen_later_whole_games(tmp_path):
    config = configured(tmp_path)
    parent = repository_for(config)
    raw = RawRepository(parent.path)
    seed_r5_child_registry(raw)
    before = [tuple(r) for r in raw.connection.execute("SELECT * FROM raw_events WHERE run_id='legacy' ORDER BY event_id")]
    schema = raw.connection.execute('PRAGMA schema_version').fetchone()[0]
    raw.close()
    trace, events = [], [root_event(i) for i in range(15)]
    try:
        result = collect(config, parent, trace, events, 'repair', NOW + timedelta(minutes=1))['raw_lifecycle']
        assert result['registry_reclassifications'] == 142
        assert result['required_events'] == 15 and result['expected_tokens'] == 45
        assert result['status'] == 'PUBLISHED' and result['trusted_full_books'] == 45
        absent = collect(config, parent, trace, events, 'absent', NOW + timedelta(minutes=2), discover=False)['raw_lifecycle']
        assert absent['registry_reclassifications'] == 0
        assert absent['followup_lookups'] == 15 and absent['trusted_full_books'] == 45
        assert absent['status'] == 'PUBLISHED'
        raw = RawRepository(parent.path)
        try:
            assert before == [tuple(r) for r in raw.connection.execute("SELECT * FROM raw_events WHERE run_id='legacy' ORDER BY event_id")]
            assert schema == raw.connection.execute('PRAGMA schema_version').fetchone()[0]
            assert raw.connection.execute("SELECT COUNT(*) FROM raw_tracked_events WHERE state='DISCOVERY_ONLY'").fetchone()[0] == 142
            repaired = raw.connection.execute("SELECT * FROM raw_events WHERE run_id='repair' AND metadata_status='DISCOVERY_ONLY_RECLASSIFIED'").fetchall()
            assert len(repaired) == 142
            assert all(json.loads(r['source_ref_json'])['registry_reclassification']['run_id'] == 'legacy' for r in repaired)
            assert raw.connection.execute("SELECT COUNT(*) FROM raw_books WHERE run_id='repair' AND event_id LIKE 'child-%'").fetchone()[0] == 0
        finally:
            raw.close()
    finally:
        parent.close()


def test_top_level_incomplete_triad_is_kept_and_recovers(tmp_path):
    config = configured(tmp_path)
    parent = repository_for(config)
    trace = []
    complete = root_event(0)
    partial = deepcopy(complete); partial['markets'] = partial['markets'][:1]
    try:
        collect(config, parent, trace, [partial], 'partial', NOW)
        raw = RawRepository(parent.path)
        try:
            row = raw.connection.execute('SELECT * FROM raw_tracked_events').fetchone()
            assert row['state'] == 'TRACKING' and row['slots_json'] == '[]'
            assert child_registry_corrections(raw, {}) == {}
        finally:
            raw.close()
        result = collect(config, parent, trace, [complete], 'recovered', NOW + timedelta(minutes=1), discover=False)['raw_lifecycle']
        assert result['trusted_full_books'] == 3 and result['required_events'] == 1
        assert result['registry_reclassifications'] == 0
    finally:
        parent.close()


def test_missing_first_proof_and_any_token_anchor_are_never_child_reclassified(tmp_path):
    raw = RawRepository(tmp_path / 'trades_sim.db')
    try:
        seed_r5_child_registry(raw, 2)
        with raw.transaction() as c:
            c.execute("UPDATE raw_tracked_events SET first_run_id='missing' WHERE event_id='child-0'")
            c.execute("UPDATE raw_tracked_events SET slots_json=? WHERE event_id='child-1'", ('[{"token_id":"known"}]',))
        assert child_registry_corrections(raw, {}) == {}
    finally:
        raw.close()


def resolved_root(index=0):
    event = root_event(index)
    event['ended'] = True
    for index, market in enumerate(event['markets']):
        market.update(closed=True, outcomePrices=json.dumps([1, 0] if index == 0 else [0, 1]))
    return event


def test_terminal_plus_another_api_error_does_not_stop_terminal_followup(tmp_path):
    import pytest
    config = configured(tmp_path)
    parent = repository_for(config)
    trace = []
    try:
        collect(config, parent, trace, [root_event(0), root_event(1)], 'seed', NOW)

        class MixedGamma(MultiGamma):
            def fetch_event_by_id(self, run_id, event_id):
                if event_id == 'root-1':
                    self.trace.append((run_id, 'raw_event', event_id))
                    return SimpleNamespace(status='ERROR', event=None, request_id=None,
                        received_at=None, response_sha256=None, raw=None)
                return super().fetch_event_by_id(run_id, event_id)

        now = NOW + timedelta(minutes=1)
        worker = Collector(config, parent, MixedGamma(trace, parent, [resolved_root(0), root_event(1)], now=now, discover=False),
                           TraceClob(trace, now=now, repository=parent), FakeSportsClock())
        with pytest.raises(RuntimeError, match='incomplete cycle'):
            worker.collect('failed', now=now)
        raw = RawRepository(parent.path)
        try:
            assert raw.connection.execute("SELECT status FROM raw_cycles WHERE run_id='failed'").fetchone()[0] == 'FAILED'
            terminal = raw.connection.execute("SELECT * FROM raw_events WHERE run_id='failed' AND event_id='root-0'").fetchone()
            assert terminal['terminal_json'] is not None
            assert terminal['lifecycle_state'] == 'WAIT_RESOLUTION'
            assert raw.connection.execute("SELECT state FROM raw_tracked_events WHERE event_id='root-0'").fetchone()[0] == 'WAIT_RESOLUTION'
        finally:
            raw.close()
    finally:
        parent.close()


def test_parent_postpublication_failure_requeues_and_success_confirms(tmp_path):
    from polybot.research_raw import ResearchRawCollector, event_identity, terminal_proof
    from test_raw_followup_isolation import _raw_cycle
    for parent_status in ('FAILED', 'SUCCEEDED'):
        config = configured(tmp_path / parent_status)
        parent = repository_for(config)
        raw = RawRepository(parent.path)
        event = resolved_root()
        slots, valid = event_identity(event, 'soccer', config.trading.gamma)
        assert valid
        proof = terminal_proof(event, slots, 'soccer')
        try:
            cycle = _raw_cycle('terminal', 'terminal-component')
            cycle.update(status='PUBLISHED', config_hash=config.config_hash,
                source_digest=config.trading.strategy_source_digest, job_name=config.job_name,
                expected_events=1, expected_tokens=0)
            with raw.transaction() as c:
                c.execute('INSERT INTO raw_tracked_events VALUES(?,?,?,?,?,?,?,?,?)',
                    ('root-0', 'soccer', 'TERMINAL_PENDING_PUBLICATION', canonical_json(slots),
                     'terminal', 'terminal', iso_utc(NOW), 0, 'EXACT_TERMINAL'))
                raw.insert(c, 'raw_cycles', cycle)
                raw.insert(c, 'raw_events', {'run_id':'terminal','event_id':'root-0','family':'soccer',
                    'metadata_status':'OBSERVED','metadata_received_at':iso_utc(NOW),
                    'metadata_request_id':'request','source_kind':'RAW_GAMMA_EVENT','source_ref_json':'{}',
                    'event_json':canonical_json(event),'slots_json':canonical_json(slots),
                    'identity_valid':1,'lifecycle_state':'RESOLVED','terminal_json':canonical_json(proof),'missing_count':0})
            for kind, at in [('STARTED', NOW), (parent_status, NOW + timedelta(seconds=15))]:
                parent.record_run_event({'event_id':kind,'run_id':'terminal','event_type':kind,
                    'observed_at':iso_utc(at),'config_hash':config.config_hash,
                    'strategy_source_digest':config.trading.strategy_source_digest,'detail_json':'{}'})
            collector = ResearchRawCollector(config, parent, None, None)
            checks, updates, retry, confirmed = collector._terminal_working_states(raw, iso_utc(NOW + timedelta(seconds=30)))
            assert len(checks) == 1 and checks[0]['exact_terminal_event_record']
            if parent_status == 'FAILED':
                assert retry['root-0']['state'] == 'WAIT_RESOLUTION' and not confirmed
            else:
                assert confirmed == {'root-0'} and not retry
                assert updates[0][0] == 'RESOLVED_CONFIRMED'
            # A later parent receipt cannot close an earlier observing cycle.
            _, _, retry, confirmed = collector._terminal_working_states(raw, iso_utc(NOW + timedelta(seconds=5)))
            assert 'root-0' in retry and not confirmed
        finally:
            raw.close()
            parent.close()
