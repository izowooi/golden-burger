"""Publish explicitly identified public receipts for shared price-path discovery.

An index row proves receipt of bytes, not that a collector cycle was published,
an event passed a strategy gate, or an order filled. Such ownership stays local.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import zlib

from .market_data_refs import configured_references, parse_reference
from .market_data_store import Observation


_RECORDER_KEYS = {
    'requests': ('attempt_id',),
    'event_observations': ('run_id', 'event_id'),
    'book_observations': ('run_id', 'event_id', 'slot'),
    'clock_observations': ('run_id', 'request_id', 'ordinal'),
}
_BODY_COLUMNS = {
    'requests': ('raw_gzip',),
    'event_observations': ('event_json', 'clock_json'),
    'book_observations': ('book_gzip',),
    'clock_observations': ('raw_gzip',),
}
_PUBLIC_METADATA = ('run_id', 'request_id', 'request_kind', 'slot', 'ordinal', 'condition_id', 'status', 'http_status',
                    'raw_complete', 'started_at', 'requested_at', 'received_at', 'family', 'league')


def index_recorder_row(runtime: str, table: str, row: dict, encoded: dict, *,
                       references=None, source: str | None = None, context=None) -> int:
    if table not in _RECORDER_KEYS:
        return 0
    body_references = [(column, parse_reference(encoded.get(column))) for column in _BODY_COLUMNS[table]]
    body_references = [(column, reference) for column, reference in body_references if reference]
    if not body_references:
        return 0
    source = source or os.environ.get('PUBLIC_MARKET_DATA_SOURCE')
    if not source:
        raise ValueError('shared recorder requires PUBLIC_MARKET_DATA_SOURCE identity')
    codec = references or configured_references()
    writer = codec.writer
    if writer is None or not callable(getattr(writer, 'append_observations', None)):
        raise ValueError('shared recorder requires public observation indexing')
    metadata = {key: row[key] for key in _PUBLIC_METADATA if row.get(key) is not None}
    metadata['evidence_scope'] = 'raw_receipt_not_cycle_or_trade_confirmation'
    metadata['source_table'] = table
    identity = [row[key] for key in _RECORDER_KEYS[table]]
    observations = []
    for column, (kind, digest) in body_references:
        encoding = 'gzip' if column.endswith('_gzip') else 'utf8-json'
        observed_at, time_basis = row.get('received_at'), 'received_at'
        tokens, events = set(), set()
        if column == 'clock_json':
            value = _decode_public_json(codec.decode_many([encoded[column]])[0], encoding)
            tokens, events = _public_subjects(value)
            # WSS updates can precede/follow Gamma receipt independently. The
            # enclosing event receipt is never evidence of the clock's receipt.
            observed_at = value.get('received_at') if isinstance(value, dict) else None
            time_basis = 'clock_json.received_at'
        if not observed_at:
            if context is not None:
                context.gap(table + '.' + column, 'missing_original_receipt_time')
            continue
        key = hashlib.sha256(json.dumps([table, column, identity], separators=(',', ':')).encode()).hexdigest()
        # Existing White observation IDs were already published with these
        # exact fields. Preserve their immutable rows and subject sets on retry.
        details = {**metadata, 'encoding': encoding, 'storage_type': kind}
        if column == 'clock_json':
            details.update(source_column=column, receipt_time_basis=time_basis, received_at=observed_at)
            job_name = context.job_name if context is not None else os.environ.get('JOB_NAME')
            if job_name:
                details['job_name'] = job_name
        observations.append(Observation(
            observer=f'{source}/golden-coconut/{runtime}', observation_id=key,
            observed_at=observed_at, kind=table, payload_sha=digest,
            event_id=row.get('event_id'), token_id=row.get('token_id'),
            token_ids=tuple(sorted(tokens)), event_ids=tuple(sorted(events)),
            metadata_json=json.dumps(details, sort_keys=True, separators=(',', ':')),
        ))
    writer.append_observations(observations)
    if context is not None:
        context.indexed_count += len(observations)
    return len(observations)


# Generic body transformation and receipt indexing are separate contracts.
# Offline callers supply this context explicitly; live insertion hooks obtain
# source from the launcher and runtime from the collector's actual DB identity.

@dataclass
class ReceiptContext:
    source: str
    runtime: str
    job_name: str | None = None
    connection: object | None = None
    gap_counts: dict[str, int] = field(default_factory=dict)
    indexed_count: int = 0

    def __post_init__(self):
        values = (self.source, self.runtime)
        if self.job_name is not None:
            values += (self.job_name,)
        for value in values:
            if (not isinstance(value, str) or not value or len(value) > 256
                    or any(ord(char) < 32 for char in value)):
                raise ValueError('receipt source/runtime/job identity must be a bounded string')

    def gap(self, table: str, reason: str) -> None:
        key = table + ':' + reason
        self.gap_counts[key] = self.gap_counts.get(key, 0) + 1


@dataclass(frozen=True)
class ReceiptRule:
    column: str
    clock: str
    keys: tuple[str, ...]
    encoding: str
    body_basis: str


_HTTP = 'retained_public_response_bytes'
_PROJECTION = 'retained_public_source_projection'
_RECEIPT_RULES = {
    **{(strategy, 'raw_book_observations'): ReceiptRule('book_json', 'received_at', ('observation_id',), 'utf8-json', _PROJECTION)
       for strategy in ('golden-apricot', 'golden-peach', 'golden-plum')},
    ('golden-coconut', 'raw_payloads'): ReceiptRule('payload_gzip', 'observed_at', ('raw_payload_id',), 'gzip', _HTTP),
    ('golden-coconut', 'book_snapshots'): ReceiptRule('book_gzip', 'observed_at', ('book_snapshot_id',), 'gzip', _PROJECTION),
    ('golden-black', 'raw_payloads'): ReceiptRule('payload_gzip', 'observed_at', ('payload_id',), 'gzip', _HTTP),
    ('golden-watermelon', 'raw_payloads'): ReceiptRule('payload_gzip', 'observed_at', ('payload_id',), 'gzip', _HTTP),
    ('golden-watermelon', 'raw_events'): ReceiptRule('event_json', 'metadata_received_at', ('run_id', 'event_id'), 'utf8-json', _PROJECTION),
    ('golden-cherry', 'shadow_raw_payloads'): ReceiptRule('payload_gzip', 'source_received_at', ('payload_id',), 'gzip', _HTTP),
    ('golden-raspberry', 'raw_payloads'): ReceiptRule('payload_blob', 'recorded_at', ('payload_id',), 'gzip', _HTTP),
    ('golden-pomegranate', 'raw_payloads'): ReceiptRule('payload_blob', 'api_requests.completed_at', ('payload_id',), 'zlib', 'retained_public_response_after_existing_sanitization'),
    ('golden-pomegranate', 'resolution_observations'): ReceiptRule('raw_market_json', 'observed_at', ('resolution_observation_id',), 'utf8-json', _PROJECTION),
    ('golden-strawberry', 'raw_payloads'): ReceiptRule('payload_blob', 'source_received_at', ('payload_id',), 'gzip', _HTTP),
    ('golden-strawberry', 'compact_books'): ReceiptRule('book_blob', 'source_received_at', ('book_id',), 'gzip', _PROJECTION),
    ('golden-strawberry', 'resolution_observations'): ReceiptRule('market_blob', 'observed_at', ('resolution_observation_id',), 'gzip', _PROJECTION),
    ('golden-guava', 'source_requests'): ReceiptRule('payload_gzip', 'received_at', ('run_id', 'request_id'), 'gzip', _PROJECTION),
    ('golden-guava', 'events'): ReceiptRule('raw_gzip', 'source_requests.received_at', ('run_id', 'event_id'), 'gzip', _PROJECTION),
    ('golden-guava', 'book_attempts'): ReceiptRule('raw_gzip', 'source_requests.received_at', ('run_id', 'event_id', 'token_id'), 'gzip', _PROJECTION),
}
# Never index derived JSON beside the raw gzip, catalog field fragments, or
# mutable latest caches as additional physical source receipts.
_METADATA_KEYS = ('run_id', 'request_id', 'logical_request_id', 'payload_kind', 'kind',
                  'condition_id', 'sha256', 'payload_sha256', 'raw_sha256',
                  'book_sha256', 'canonical_sha256', 'raw_market_sha256', 'source_response_sha256')
_JSON_LIMIT = 64 << 20


def _rule_for(strategy, table, row):
    rule = _RECEIPT_RULES.get((strategy, table))
    if rule is not None and strategy == 'golden-watermelon' and table == 'raw_payloads' and 'kind' in row:
        # Independent lifecycle sidecar has a different raw_payloads schema.
        rule = ReceiptRule('payload_gzip', 'received_at', ('payload_id',), 'gzip', _HTTP)
    return rule if rule is not None and row.get(rule.column) is not None else None


def _request_receipt(connection, request_id):
    if connection is None or not request_id:
        return None
    try:
        return connection.execute(
            'SELECT run_id,completed_at FROM api_requests WHERE request_id=?', (request_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # Historical schema may lack a request-receipt table.


def collector_receipt_context(strategy: str, table: str, row: dict, connection,
                              *, runtime: str | None = None, references=None,
                              namespace: str | None = None) -> ReceiptContext | None:
    """Live-only source identity; never call this from offline migration.

    An explicit runtime or exact run-to-config relation identifies the child;
    Jenkins JOB_NAME is additional provenance and is not a runtime fallback.
    A native RAW writer may use its validated storage owner instead of launcher
    environment, but that owner must agree with the actual run and any launcher.
    """
    if _rule_for(strategy, table, row) is None:
        return None
    codec = references if references is not None else configured_references()
    if codec.writer is None:
        return None
    source = os.environ.get('PUBLIC_MARKET_DATA_SOURCE')
    job_name = os.environ.get('JOB_NAME')
    owner = None
    if namespace is not None:
        from .market_data_scalar_links import _namespace
        _namespace(namespace, strategy)
        owner = json.loads(namespace)
        if ((source is not None and source != owner['source'])
                or (job_name is not None and job_name != owner['jenkins_job'])):
            raise ValueError('shared raw receipt launcher differs from bound owner')
        source, job_name = owner['source'], owner['jenkins_job']
    if not source:
        raise ValueError('shared raw receipt requires PUBLIC_MARKET_DATA_SOURCE')
    run_id = row.get('run_id')
    if strategy == 'golden-pomegranate' and table == 'raw_payloads':
        request = _request_receipt(connection, row.get('request_id'))
        run_id = request[0] if request is not None else None
    if runtime is None:
        if strategy == 'golden-guava':
            query = 'SELECT DISTINCT job_name FROM run_audits WHERE run_id=? LIMIT 2'
        elif strategy == 'golden-cherry':
            query = ('SELECT DISTINCT c.runtime_job FROM shadow_run_events r '
                     'JOIN shadow_config_versions c USING(config_hash) WHERE r.run_id=? LIMIT 2')
        elif strategy == 'golden-watermelon' and ('kind' in row or table == 'raw_events'):
            query = 'SELECT DISTINCT job_name FROM raw_cycles WHERE run_id=? LIMIT 2'
        elif strategy in ('golden-pomegranate', 'golden-strawberry'):
            query = 'SELECT DISTINCT job_name FROM research_run_events WHERE run_id=? LIMIT 2'
        else:
            query = ('SELECT DISTINCT c.job_name FROM research_run_events r '
                     'JOIN research_config_versions c USING(config_hash) WHERE r.run_id=? LIMIT 2')
        candidates = connection.execute(query, (run_id,)).fetchall()
        if len(candidates) != 1 or not candidates[0][0]:
            raise ValueError('shared raw receipt runtime identity is missing or ambiguous')
        runtime = candidates[0][0]
    if owner is not None and runtime != owner['runtime']:
        raise ValueError('shared raw receipt runtime differs from bound owner')
    return ReceiptContext(source, runtime, job_name, connection)


def _decode_public_json(value, encoding):
    raw = value.encode('utf8') if isinstance(value, str) else value
    if not isinstance(raw, bytes):
        raise ValueError('public receipt body must be TEXT or BLOB')
    if encoding in ('gzip', 'zlib'):
        decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15)
        try:
            raw = decoder.decompress(raw, _JSON_LIMIT + 1)
        except zlib.error as error:
            raise ValueError('invalid compressed public receipt body') from error
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('compressed public receipt exceeds bounds or has trailing bytes')
    if len(raw) > _JSON_LIMIT:
        raise ValueError('public receipt JSON exceeds bound')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None  # HTTP errors/non-JSON receipts remain indexed as exact bytes.


def _public_subjects(value, *, root_event=False):
    tokens, events = set(), set()
    def add(target, item):
        if isinstance(item, int) and not isinstance(item, bool):
            item = str(item)
        if isinstance(item, str) and item and len(item) <= 256:
            target.add(item)
            if len(target) > 16384:
                raise ValueError('public receipt subject count exceeds bound')
    def walk(node, event=False, depth=0):
        if depth > 12:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, event, depth + 1)
        elif isinstance(node, dict):
            if event:
                add(events, node.get('id'))
            for key in ('event_id', 'eventId'):
                add(events, node.get(key))
            for key in ('asset_id', 'assetId', 'asset', 'token_id', 'tokenId'):
                add(tokens, node.get(key))
            ids = node.get('clobTokenIds')
            if isinstance(ids, str):
                try:
                    ids = json.loads(ids)
                except ValueError:
                    ids = None
            if isinstance(ids, list):
                for token in ids:
                    add(tokens, token)
            if isinstance(node.get('tokens'), list):
                for token in node['tokens']:
                    if isinstance(token, dict):
                        add(tokens, token.get('token_id'))
            for key in ('events', 'markets', 'data', 'books', 'payload'):
                child = node.get(key)
                if isinstance(child, (dict, list)):
                    walk(child, key == 'events' or (key == 'data' and event), depth + 1)
    walk(value, root_event)
    return tokens, events


def _index_guava_stream_frames(row, value, reference, *, writer, context):
    """Index exact retained message receipts without copying the window body."""
    if (row.get('source'), row.get('method'), row.get('path')) != (
            'clob_market_ws', 'SUBSCRIBE', '/ws/market'):
        return 0
    if not isinstance(value, dict) or not isinstance(value.get('messages'), list):
        context.gap('source_requests.messages', 'missing_retained_frames')
        return 0
    identity = [row.get('run_id'), row.get('request_id')]
    if any(not item for item in identity):
        raise ValueError('Guava stream source request identity is missing')
    if writer is None or not callable(getattr(writer, 'append_observations', None)):
        raise ValueError('shared Guava stream requires durable observation indexing')
    count, batch = 0, []
    for index, frame in enumerate(value['messages']):
        if (not isinstance(frame, dict) or type(frame.get('ordinal')) is not int
                or frame['ordinal'] != index or not isinstance(frame.get('payload'), (dict, list))):
            context.gap('source_requests.messages', 'invalid_retained_frame_identity')
            continue
        received = frame.get('received_at')
        if not received:
            context.gap('source_requests.messages', 'missing_per_message_receipt_time')
            continue
        tokens, _ = _public_subjects(frame['payload'])
        # Market-stream price changes have token IDs one level below the frame.
        # Provider/game IDs are not Polymarket event identities.
        payloads = frame['payload'] if isinstance(frame['payload'], list) else [frame['payload']]
        for payload in payloads:
            if isinstance(payload, dict) and isinstance(payload.get('price_changes'), list):
                changes, _ = _public_subjects(payload['price_changes'])
                tokens.update(changes)
        metadata = {
            'source_table': 'source_requests', 'source_column': 'payload_gzip',
            'run_id': identity[0], 'request_id': identity[1], 'encoding': 'gzip',
            'storage_type': reference[0], 'body_basis': 'retained_public_source_projection',
            'frame_contract': 'guava-market-stream-frame-v1', 'message_index': index,
            'message_ordinal': frame['ordinal'], 'receipt_time_basis': 'messages[].received_at',
            'evidence_scope': 'raw_receipt_not_cycle_or_trade_confirmation',
            'complete_trade_tape': False,
        }
        if context.job_name:
            metadata['job_name'] = context.job_name
        observation = Observation(
            observer=f'{context.source}/golden-guava/{context.runtime}',
            observation_id=hashlib.sha256(json.dumps(
                ['guava-market-stream-frame-v1', identity, index], separators=(',', ':')).encode()).hexdigest(),
            observed_at=received, kind='public_stream_frame:clob_market_ws', payload_sha=reference[1],
            token_id=next(iter(tokens)) if len(tokens) == 1 else None,
            token_ids=tuple(sorted(tokens)), metadata_json=json.dumps(metadata, sort_keys=True, separators=(',', ':')),
        )
        try:
            observation.row()
        except (TypeError, ValueError):
            context.gap('source_requests.messages', 'invalid_per_message_receipt_time')
            continue
        batch.append(observation)
        if len(batch) == 256:
            writer.append_observations(batch); count += len(batch); batch = []
    if batch:
        writer.append_observations(batch); count += len(batch)
    context.indexed_count += count
    return count


def indexed_guava_stream_payload(observation, window):
    """Read a frame from its exact acknowledged window; never use window-end time."""
    metadata = json.loads(observation.metadata_json)
    index = metadata.get('message_index')
    if (observation.kind != 'public_stream_frame:clob_market_ws'
            or metadata.get('frame_contract') != 'guava-market-stream-frame-v1'
            or type(index) is not int or index < 0
            or not isinstance(window, dict) or not isinstance(window.get('messages'), list)
            or index >= len(window['messages'])):
        raise ValueError('Guava stream observation frame identity differs')
    frame = window['messages'][index]
    if (not isinstance(frame, dict) or frame.get('ordinal') != metadata.get('message_ordinal')
            or frame.get('ordinal') != index or frame.get('received_at') != observation.observed_at
            or not isinstance(frame.get('payload'), (dict, list))):
        raise ValueError('Guava stream observation frame receipt differs')
    return frame['payload']


def index_public_row(strategy: str, table: str, row: dict, encoded: dict, *,
                     references, context: ReceiptContext) -> int:
    """Index one explicitly reviewed stored body after durable payload ACK.

    Missing historical clocks are counted as gaps, never replaced by write time.
    No private selection/economic/config fields are read into index metadata.
    """
    if strategy == 'golden-coconut' and table in _RECORDER_KEYS:
        return index_recorder_row(context.runtime, table, row, encoded, references=references,
                                  source=context.source, context=context)
    rule = _rule_for(strategy, table, row)
    if rule is None:
        return 0
    reference = parse_reference(encoded.get(rule.column))
    if reference is None:
        raise ValueError('public receipt index requires an acknowledged body reference')
    if (strategy == 'golden-coconut' and table == 'raw_payloads'
            and row.get('payload_kind') == 'SPORTS_CLOCK_MESSAGE'):
        # Retired collector stored the batch completion time on each WSS raw
        # frame. Its last matched updates cannot recover every frame receipt.
        context.gap(table, 'missing_per_message_receipt_time')
        return 0
    body = references.decode_many([encoded[rule.column]])[0]
    value = _decode_public_json(body, rule.encoding)
    frame_count = (_index_guava_stream_frames(row, value, reference,
                   writer=references.writer, context=context)
                   if strategy == 'golden-guava' and table == 'source_requests' else 0)
    run_id = row.get('run_id')
    if rule.clock == 'api_requests.completed_at':
        request = _request_receipt(context.connection, row.get('request_id'))
        observed_at = request[1] if request is not None else None
        run_id = request[0] if request is not None else None
    elif rule.clock == 'source_requests.received_at':
        request_id = row.get('request_id')
        if table == 'events' and isinstance(value, dict):
            lineage = value.get('_guava')
            request_id = lineage.get('request_id') if isinstance(lineage, dict) else None
        request = None
        if context.connection is not None and request_id:
            try:
                request = context.connection.execute(
                    'SELECT received_at FROM source_requests WHERE run_id=? AND request_id=?',
                    (run_id, request_id),
                ).fetchone()
            except sqlite3.OperationalError:
                pass
        observed_at = request[0] if request is not None else None
    else:
        observed_at = row.get(rule.clock)
    if not observed_at:
        context.gap(table, 'missing_original_receipt_time')
        return frame_count
    if table == 'raw_book_observations':
        # These three ORM schemas persist utc() as timezone-naive DateTime.
        # Interpret only this reviewed UTC column, not arbitrary legacy clocks.
        observed_at = datetime.fromisoformat(str(observed_at).replace('Z', '+00:00'))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        observed_at = observed_at.isoformat()
    identity = [row.get(key) for key in rule.keys]
    if any(value is None or value == '' for value in identity):
        raise ValueError('public receipt source primary key is missing')
    writer = references.writer
    if writer is None or not callable(getattr(writer, 'append_observations', None)):
        raise ValueError('shared raw receipt requires durable observation indexing')
    # The original retained compressed body is never recompressed for indexing.
    payload_kind = str(row.get('payload_kind') or row.get('kind') or '').upper()
    root_event = (table in ('events', 'raw_events') or 'EVENT' in payload_kind
                  or (strategy == 'golden-guava' and table == 'source_requests'
                      and str(row.get('path') or '').split('?')[0] in ('/events', '/events/keyset')))
    tokens, events = _public_subjects(value, root_event=root_event)
    # Only actual source identifiers, never condition_id→event_id or winners.
    token_id = row.get('token_id') if table in ('book_attempts', 'compact_books', 'raw_book_observations', 'book_snapshots') else None
    event_id = row.get('event_id') if table in ('book_attempts', 'events', 'raw_events', 'raw_book_observations') else None
    if strategy == 'golden-coconut' and table == 'book_snapshots' and context.connection is not None:
        # The canonical book has no event ID. Read only exact public identity
        # links from this run/cycle; never attach another cycle's token mapping.
        try:
            source_events = context.connection.execute(
                'SELECT DISTINCT m.event_id FROM outcome_observations o '
                'JOIN market_observations m ON m.market_observation_id=o.market_observation_id '
                'AND m.cycle_id=o.cycle_id AND m.run_id=o.run_id '
                'WHERE o.run_id=? AND o.cycle_id=? AND o.token_id=?',
                (run_id, row.get('cycle_id'), token_id),
            ).fetchall()
        except sqlite3.OperationalError:
            source_events = []
        events.update(str(item[0]) for item in source_events if item[0])
        if len(events) == 1:
            event_id = next(iter(events))
    if token_id is not None:
        tokens.add(str(token_id))
    if event_id is not None:
        events.add(str(event_id))
    metadata = {key: row[key] for key in _METADATA_KEYS if row.get(key) is not None}
    if run_id is not None:
        metadata['run_id'] = run_id
    metadata.update(source_table=table, source_column=rule.column,
                    encoding=rule.encoding, storage_type=reference[0],
                    body_basis=rule.body_basis, receipt_time_basis=rule.clock,
                    evidence_scope='raw_receipt_not_cycle_or_trade_confirmation')
    if context.job_name is not None:
        metadata['job_name'] = context.job_name
    key = hashlib.sha256(json.dumps([table, rule.column, identity], separators=(',', ':')).encode()).hexdigest()
    observation_kind = ('public_source_projection:' if strategy == 'golden-coconut'
                        and table == 'book_snapshots' else 'public_receipt:') + table
    observation = Observation(
        observer=f'{context.source}/{strategy}/{context.runtime}', observation_id=key,
        observed_at=observed_at, kind=observation_kind, payload_sha=reference[1],
        token_id=str(token_id) if token_id is not None else None,
        event_id=str(event_id) if event_id is not None else None,
        metadata_json=json.dumps(metadata, sort_keys=True, separators=(',', ':')),
        token_ids=tuple(sorted(tokens)), event_ids=tuple(sorted(events)),
    )
    observation.row()
    observation.subjects()
    writer.append_observations([observation])
    context.indexed_count += 1
    return 1 + frame_count
