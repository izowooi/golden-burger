from dataclasses import replace
import hashlib
import sqlite3
import subprocess
import sys
import zlib

import pytest

from polybot_observability import market_data_store
from polybot_observability.market_data_store import (
    CorruptPayloadError,
    MissingPayloadError,
    Observation,
    ObservationConflictError,
    PayloadReader,
    PayloadStore,
    StoreError,
    StoreLimitError,
)


def observation(digest, **changes):
    return replace(
        Observation(
            observer="white/basketball",
            observation_id="receipt-1",
            observed_at="2026-09-29T12:00:00.000Z",
            kind="orderbook",
            payload_sha=digest,
            event_id="event-123",
            token_id="token-456",
        ),
        **changes,
    )


def test_exact_bytes_are_deduplicated_without_json_normalization(tmp_path):
    path = tmp_path / "market.sqlite"
    values = [b"", b"\x00\xff\x80", b'{"p":0.50}', b'{ "p": 0.50 }', b"book" * 500]
    with PayloadStore(path) as store:
        hashes = store.put_many(values + [values[2], values[4]])
        assert hashes == [hashlib.sha256(value).hexdigest() for value in values + [values[2], values[4]]]
        assert store.get_many([hashes[4], hashes[0], hashes[2], hashes[4]]) == [
            values[4], values[0], values[2], values[4]
        ]
        stats = store.stats()
        assert stats["payload_count"] == len(values)
        assert stats["observation_count"] == 0
        assert stats["raw_bytes"] == sum(map(len, values))
        assert stats["stored_bytes"] < stats["raw_bytes"]

    with sqlite3.connect(path) as connection:
        rows = dict(connection.execute("SELECT sha256, codec FROM payloads"))
        assert rows[hashes[0]] == "identity"
        assert rows[hashes[1]] == "identity"
        assert rows[hashes[4]] == "zlib"
    with PayloadReader(path) as reader:
        assert reader.get_many(hashes) == values + [values[2], values[4]]
        assert reader.stats() == stats


def test_empty_batches_are_noops_and_closed_connections_reject_reads(tmp_path):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        assert store.put_many([]) == []
        assert store.get_many([]) == []
        store.append_observations([])
        assert store.stats() == {
            "payload_count": 0, "raw_bytes": 0, "stored_bytes": 0, "observation_count": 0,
        }
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.stats()


def test_committed_payload_survives_process_exit_without_close(tmp_path):
    path = tmp_path / "market.sqlite"
    code = """
import os
import sys
from polybot_observability.market_data_store import PayloadStore
store = PayloadStore(sys.argv[1])
digest = store.put_many([b'durable raw receipt'])[0]
print(digest, flush=True)
os._exit(37)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 37, result.stderr
    digest = result.stdout.strip()
    assert digest == hashlib.sha256(b"durable raw receipt").hexdigest()
    with PayloadStore(path) as reopened:
        assert reopened.get_many([digest]) == [b"durable raw receipt"]
        assert reopened.stats()["payload_count"] == 1


def test_acknowledged_write_is_visible_to_existing_independent_reader(tmp_path):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        with PayloadReader(store.path) as reader:
            assert reader.stats()["payload_count"] == 0
            hashes = store.put_many([b"new receipt"])
            assert reader.get_many(hashes) == [b"new receipt"]
            assert reader.stats()["payload_count"] == 1


def test_missing_payloads_are_reported_once_without_partial_results(tmp_path):
    first, second = "1" * 64, "2" * 64
    with PayloadStore(tmp_path / "market.sqlite") as store:
        existing = store.put_many([b"present"])[0]
        with pytest.raises(MissingPayloadError) as error:
            store.get_many([first, existing, second, first])
        assert error.value.hashes == [first, second]


@pytest.mark.parametrize(
    "codec,raw_size,body",
    [
        ("identity", 7, b"changed"),
        ("identity", 8, b"receipt"),
        ("zlib", 7, b"not compressed"),
        ("zlib", 7, zlib.compress(b"receipt")[:-1]),
        ("zlib", 7, zlib.compress(b"receipt") + b"extra"),
        ("zlib", 7, zlib.compress(b"receipt" * 1000)),
        ("zlib", 7, zlib.compress(b"receipt") + zlib.compress(b"receipt")),
    ],
)
def test_corruption_is_rejected_on_reads_and_duplicate_writes(tmp_path, codec, raw_size, body):
    path = tmp_path / "market.sqlite"
    with PayloadStore(path) as store:
        digest = store.put_many([b"receipt"])[0]
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE payloads SET codec=?, raw_size=?, body=? WHERE sha256=?",
                (codec, raw_size, body, digest),
            )
        with PayloadReader(path) as reader:
            with pytest.raises(CorruptPayloadError):
                reader.get_many([digest])
        with pytest.raises(CorruptPayloadError):
            store.put_many([b"new receipt", b"receipt"])
        assert store.stats()["payload_count"] == 1
        with pytest.raises(MissingPayloadError):
            store.get_many([hashlib.sha256(b"new receipt").hexdigest()])


def test_sqlite_failure_rolls_back_whole_payload_batch_and_writer_recovers(tmp_path):
    path = tmp_path / "market.sqlite"
    with PayloadStore(path) as store:
        rejected = hashlib.sha256(b"reject").hexdigest()
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TRIGGER injected_failure BEFORE INSERT ON payloads "
                f"WHEN NEW.sha256 = '{rejected}' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            store.put_many([b"first", b"reject"])
        assert store.stats()["payload_count"] == 0
        hashes = store.put_many([b"recovered"])
        assert store.get_many(hashes) == [b"recovered"]


def test_observation_receipts_keep_independent_provenance_and_exact_idempotency(tmp_path):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        digest = store.put_many([b"same public bytes"])[0]
        first = observation(digest)
        other_observer = replace(first, observer="strategy-a/replay")
        other_receipt = replace(first, observation_id="receipt-2")
        store.append_observations([first, other_observer, other_receipt, first])
        store.append_observations([first, other_receipt])
        assert store.stats()["payload_count"] == 1
        assert store.stats()["observation_count"] == 3
        with sqlite3.connect(store.path) as connection:
            actual = connection.execute(
                "SELECT observer, observation_id, observed_at, kind, event_id, token_id, "
                "payload_sha, metadata_json FROM observations ORDER BY observer, observation_id"
            ).fetchall()
        assert actual == sorted(item.row() for item in [first, other_observer, other_receipt])


@pytest.mark.parametrize(
    "change",
    [
        {"observed_at": "2026-09-29T12:00:01.000Z"},
        {"kind": "resolution"},
        {"event_id": "other-event"},
        {"token_id": None},
        {"metadata_json": "{ }"},
    ],
)
def test_conflicting_observation_rolls_back_entire_batch(tmp_path, change):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        digest = store.put_many([b"receipt"])[0]
        original = observation(digest)
        store.append_observations([original])
        with pytest.raises(ObservationConflictError):
            store.append_observations([
                replace(original, observation_id="would-be-new"), replace(original, **change),
            ])
        assert store.stats()["observation_count"] == 1
        store.append_observations([replace(original, observation_id="would-be-new")])
        assert store.stats()["observation_count"] == 2


def test_missing_observation_payload_rolls_back_and_existing_receipts_are_immutable(tmp_path):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        digest = store.put_many([b"receipt"])[0]
        first = observation(digest)
        with pytest.raises(MissingPayloadError) as error:
            store.append_observations([
                first, replace(first, observation_id="missing", payload_sha="f" * 64),
            ])
        assert error.value.hashes == ["f" * 64]
        assert store.stats()["observation_count"] == 0
        store.append_observations([first])
        with sqlite3.connect(store.path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="append only"):
                connection.execute("DELETE FROM observations")
            with pytest.raises(sqlite3.IntegrityError, match="append only"):
                connection.execute("UPDATE observations SET observed_at='changed'")
        assert store.stats()["observation_count"] == 1


@pytest.mark.parametrize("metadata", ["[]", "null", '{"x":NaN}', '{"x":Infinity}', "bad JSON"])
def test_invalid_observation_metadata_rejects_batch_before_any_write(tmp_path, metadata):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        digest = store.put_many([b"receipt"])[0]
        with pytest.raises(ValueError):
            store.append_observations([
                observation(digest), observation(digest, observation_id="bad", metadata_json=metadata),
            ])
        assert store.stats()["observation_count"] == 0


def test_batch_and_payload_limits_include_duplicate_read_expansion(tmp_path, monkeypatch):
    monkeypatch.setattr(market_data_store, "MAX_PAYLOAD_BYTES", 8)
    monkeypatch.setattr(market_data_store, "MAX_BATCH_BYTES", 12)
    monkeypatch.setattr(market_data_store, "MAX_BATCH_ITEMS", 3)
    with PayloadStore(tmp_path / "market.sqlite") as store:
        for batch in [[b"x" * 9], [b"x" * 7, b"y" * 6], [b""] * 4, (b"x",)]:
            with pytest.raises(StoreLimitError):
                store.put_many(batch)
        for value in ["text", bytearray(b"mutable"), memoryview(b"view")]:
            with pytest.raises(TypeError):
                store.put_many([b"valid", value])
        assert store.stats()["payload_count"] == 0
        digest = store.put_many([b"x" * 7])[0]
        with pytest.raises(StoreLimitError):
            store.get_many([digest, digest])
        with pytest.raises(StoreLimitError):
            store.get_many([digest] * 4)
        with pytest.raises(StoreLimitError):
            store.append_observations([observation(digest)] * 4)
        assert store.stats()["observation_count"] == 0


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "g" * 64, None, 42])
def test_invalid_hash_is_not_treated_as_missing_data(tmp_path, digest):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        with pytest.raises(ValueError):
            store.get_many([digest])


def test_reader_is_readonly_and_does_not_create_a_missing_database(tmp_path):
    path = tmp_path / "market.sqlite"
    with pytest.raises(FileNotFoundError):
        PayloadReader(path)
    assert not path.exists()
    with PayloadStore(path) as store:
        digest = store.put_many([b"receipt"])[0]
    with PayloadReader(path) as reader:
        assert not hasattr(reader, "put_many")
        assert not hasattr(reader, "append_observations")
        reader._connection.execute("PRAGMA query_only=OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader._connection.execute("DELETE FROM payloads")
        assert reader.get_many([digest]) == [b"receipt"]


def test_unrelated_database_is_rejected_without_changing_existing_schema(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE fills (quantity INTEGER)")
        connection.execute("INSERT INTO fills VALUES (3)")
    for constructor in [PayloadStore, PayloadReader]:
        with pytest.raises(StoreError, match="schema"):
            constructor(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM fills").fetchall() == [(3,)]
        assert connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == [("fills",)]


def test_observation_reader_orders_utc_instants_and_preserves_duplicate_receipts(tmp_path):
    path = tmp_path / "market.sqlite"
    with PayloadStore(path) as store:
        digest = store.put_many([b"same public receipt"])[0]
        records = [
            observation(digest, observation_id="before", observed_at="2026-09-29T20:59:59.999999+09:00"),
            observation(digest, observation_id="start", observed_at="2026-09-29T14:00:00.000100+02:00"),
            observation(digest, observation_id="inside", observed_at="2026-09-29T07:00:00.000101-05:00"),
            observation(digest, observation_id="end", observed_at="2026-09-29T12:00:00.000102Z"),
            observation(digest, observation_id="later", observed_at="2026-09-29T21:00:01+09:00"),
        ]
        store.append_observations(records[::-1])
    with PayloadReader(path) as reader:
        assert list(reader.iter_observations()) == records
        assert list(reader.iter_observations(
            start="2026-09-29T12:00:00.000100Z", end="2026-09-29T21:00:00.000102+09:00",
        )) == records[1:3]
        assert list(reader.iter_observations(end="2026-09-29T12:00:00.000100Z")) == records[:1]
        assert list(reader.iter_observations(start="2026-09-29T12:00:00.000102Z")) == records[3:]
        assert list(reader.iter_observations(
            start="2026-09-29T12:00:00.000100Z", end="2026-09-29T14:00:00.000100+02:00",
        )) == []
        assert reader.stats()["payload_count"] == 1
        assert reader.stats()["observation_count"] == len(records)


def test_observation_reader_combines_filters_and_preserves_provenance(tmp_path):
    path = tmp_path / "market.sqlite"
    with PayloadStore(path) as store:
        digest = store.put_many([b"same public receipt"])[0]
        first = observation(digest, metadata_json='{"venue":"public","sequence":1}')
        next_receipt = replace(first, observation_id="receipt-2")
        other_observer = replace(first, observer="z-replay")
        other_token = replace(first, observation_id="receipt-3", token_id="other-token")
        other_event = replace(first, observation_id="receipt-4", event_id="other-event")
        without_identifiers = replace(first, observation_id="receipt-5", token_id=None, event_id=None)
        records = [first, next_receipt, other_token, other_event, without_identifiers, other_observer]
        store.append_observations(records[::-1] + [first])
    with PayloadReader(path) as reader:
        assert list(reader.iter_observations(token_id=first.token_id, event_id=first.event_id)) == [
            first, next_receipt, other_observer,
        ]
        assert list(reader.iter_observations(
            token_id=first.token_id, event_id=first.event_id, observer=first.observer,
            start="2026-09-29T12:00:00Z", end="2026-09-29T12:00:01Z",
        )) == [first, next_receipt]
        assert list(reader.iter_observations(observer=other_observer.observer)) == [other_observer]
        assert list(reader.iter_observations(token_id="other-token")) == [other_token]
        assert list(reader.iter_observations(event_id="other-event")) == [other_event]
        assert list(reader.iter_observations(token_id="' OR 1=1 --")) == []
        assert list(reader.iter_observations()) == records


def test_observation_reader_applies_default_and_explicit_result_limits(tmp_path):
    path = tmp_path / "market.sqlite"
    with PayloadStore(path) as store:
        digest = store.put_many([b"receipt"])[0]
        records = [observation(digest, observation_id=f"receipt-{index:04d}") for index in range(1001)]
        store.append_observations(records[::-1])
    with PayloadReader(path) as reader:
        assert list(reader.iter_observations()) == records[:1000]
        assert list(reader.iter_observations(limit=1)) == records[:1]
        assert list(reader.iter_observations(limit=10_000)) == records
        assert reader.stats()["observation_count"] == 1001


@pytest.mark.parametrize("limit", [0, -1, 10_001, 1.0, True, "10", None])
def test_observation_reader_rejects_invalid_result_limits(tmp_path, limit):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        with pytest.raises(StoreLimitError, match="limit"):
            list(store.iter_observations(limit=limit))


@pytest.mark.parametrize(
    "timestamp",
    ["2026-09-29", "2026-09-29T12:00:00", "2026-09-29T12:00:00.000001", "invalid", "2026-02-30T12:00:00Z"],
)
def test_observation_rejects_naive_or_invalid_timestamps_before_writing_batch(tmp_path, timestamp):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        digest = store.put_many([b"receipt"])[0]
        with pytest.raises(ValueError):
            store.append_observations([
                observation(digest), observation(digest, observation_id="invalid", observed_at=timestamp),
            ])
        assert store.stats()["observation_count"] == 0


@pytest.mark.parametrize(
    "filters",
    [
        {"start": "2026-09-29T12:00:00"},
        {"end": "2026-09-29T12:00:00"},
        {"start": "invalid"},
        {"start": "2026-09-29T12:00:00Z", "end": "2026-09-29T13:00:00+02:00"},
        {"token_id": 123},
        {"event_id": ["event"]},
        {"observer": "x" * 1025},
    ],
)
def test_observation_reader_rejects_invalid_filters_and_reversed_utc_intervals(tmp_path, filters):
    with PayloadStore(tmp_path / "market.sqlite") as store:
        with pytest.raises(ValueError):
            list(store.iter_observations(**filters))


def test_observation_indexes_are_installed_on_reopen_and_queries_stay_readonly(tmp_path):
    path = tmp_path / "market.sqlite"
    with PayloadStore(path) as store:
        digest = store.put_many([b"receipt"])[0]
        record = observation(digest)
        store.append_observations([record])
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX observations_token_time")
        connection.execute("DROP INDEX observations_event_time")
    with PayloadStore(path):
        pass
    with PayloadReader(path) as reader:
        for name, columns in [
            ("observations_token_time", ["token_id", "observed_at"]),
            ("observations_event_time", ["event_id", "observed_at"]),
        ]:
            actual = reader._connection.execute(f"PRAGMA index_info({name})").fetchall()
            assert [row[2] for row in actual] == columns
        before = reader._connection.total_changes
        assert list(reader.iter_observations(token_id=record.token_id, event_id=record.event_id)) == [record]
        assert reader._connection.total_changes == before
        assert reader._connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader._connection.execute("CREATE TABLE accidental_write (id INTEGER)")
