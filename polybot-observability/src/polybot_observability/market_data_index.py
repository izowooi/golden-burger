"""Publish explicitly identified public receipts for shared price-path discovery.

An index row proves receipt of bytes, not that a collector cycle was published,
an event passed a strategy gate, or an order filled. Such ownership stays local.
"""
from __future__ import annotations

import hashlib
import json
import os

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
    'event_observations': ('event_json',),
    'book_observations': ('book_gzip',),
    'clock_observations': ('raw_gzip',),
}
_PUBLIC_METADATA = ('run_id', 'request_id', 'request_kind', 'slot', 'ordinal', 'condition_id', 'status', 'http_status',
                    'raw_complete', 'started_at', 'requested_at', 'received_at', 'family', 'league')


def index_recorder_row(runtime: str, table: str, row: dict, encoded: dict, *,
                       references=None, source: str | None = None) -> None:
    if table not in _RECORDER_KEYS:
        return
    body_references = [(column, parse_reference(encoded.get(column))) for column in _BODY_COLUMNS[table]]
    body_references = [(column, reference) for column, reference in body_references if reference]
    if not body_references:
        return
    source = source or os.environ.get('PUBLIC_MARKET_DATA_SOURCE')
    if not source:
        raise ValueError('shared recorder requires PUBLIC_MARKET_DATA_SOURCE identity')
    codec = references or configured_references()
    writer = codec.writer
    if writer is None or not hasattr(writer, 'append_observations'):
        raise ValueError('shared recorder requires public observation indexing')
    # Rows without a source receipt timestamp remain in the local evidence DB;
    # never fabricate a price-path timestamp from a later publication or clock.
    observed_at = row.get('received_at')
    if not observed_at:
        return
    metadata = {key: row[key] for key in _PUBLIC_METADATA if row.get(key) is not None}
    metadata['evidence_scope'] = 'raw_receipt_not_cycle_or_trade_confirmation'
    metadata['source_table'] = table
    identity = [row[key] for key in _RECORDER_KEYS[table]]
    observations = []
    for column, (kind, digest) in body_references:
        key = hashlib.sha256(json.dumps([table, column, identity], separators=(',', ':')).encode()).hexdigest()
        details = {**metadata, 'encoding': 'gzip' if column.endswith('_gzip') else 'utf8-json',
                   'storage_type': kind}
        observations.append(Observation(
            observer=f'{source}/golden-coconut/{runtime}', observation_id=key,
            observed_at=observed_at, kind=table, payload_sha=digest,
            event_id=row.get('event_id'), token_id=row.get('token_id'),
            metadata_json=json.dumps(details, sort_keys=True, separators=(',', ':')),
        ))
    writer.append_observations(observations)
