"""Native accountless producers preserve evidence through the reviewed RAW layout."""
from datetime import datetime, timezone
import json
import os
import shutil
import sqlite3

import pytest

from polybot.collector import ResearchCollector
from polybot.db import repository as repository_module
from polybot.db.repository import ResearchRepository
from polybot.run_audit import ResearchRunAudit
from polybot_observability import (
    market_data_index, market_data_levels, market_data_raw_links,
    market_data_refs, market_data_sqlite,
)
from polybot_observability.market_data_raw_links import (
    iter_raw_logical_rows, raw_layout_metadata, verify_raw_dependencies,
)
from polybot_observability.market_data_raw_profiles import POMEGRANATE_PROFILE_ID, raw_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadReader, PayloadStore
from test_collector_integration import (
    _config, _GetSession, _market, _PostSession, _trade,
)


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 8, 7, tzinfo=timezone.utc)
PROFILE = raw_profile(POMEGRANATE_PROFILE_ID)


def bind(monkeypatch, reader, writer=None, *, raw=True):
    references = PayloadReferences(reader=reader, writer=writer)
    for module in (
        market_data_refs, market_data_sqlite, market_data_index,
        market_data_raw_links, market_data_levels, repository_module,
    ):
        monkeypatch.setattr(module, "configured_references", lambda: references)
    monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "fixture-source")
    monkeypatch.setenv("JOB_NAME", "pomegranate-parent")
    monkeypatch.setenv("PUBLIC_MARKET_DATA_RAW", "1" if raw else "0")
    monkeypatch.setenv("GIT_COMMIT", "a" * 40)
    monkeypatch.setattr("polybot.run_audit.utc_now", lambda: NOW.isoformat())
    return references


def producer(tmp_path, *, clob_error=None):
    config = _config(tmp_path)
    repository = ResearchRepository(config.db_path, clock=lambda: NOW)
    repository.initialize()
    collector = ResearchCollector(config, repository=repository, now_epoch=lambda: 2_000_000)

    def gamma_response(url, params):
        if url.endswith("/markets/keyset"):
            return {"markets": [_market(1), _market(2)], "next_cursor": None}
        return [_market(1)]

    collector.gamma.session = _GetSession(gamma_response)
    collector.clob.session = _PostSession(clob_error)
    collector.data.session = _GetSession(
        lambda url, params: [_trade(timestamp=params["start"])]
    )
    audit = ResearchRunAudit.start(config, repository=repository)
    stats = collector.run_cycle(audit.run_id)
    audit.succeed(stats)
    return repository, collector, stats


def test_native_producer_logical_tables_guards_body_and_level_closure(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / "public.db") as store:
        references = bind(monkeypatch, store, store)
        repository, _, stats = producer(tmp_path)
        assert stats["markets_observed"] == 2
        assert stats["orderbooks_observed"] == 4
        with repository._read_connect() as connection:
            assert raw_layout_metadata(connection)["profile_id"] == POMEGRANATE_PROFILE_ID
            schema = repository._logical_schema(connection)
            assert len([row for row in schema if row[0] == "table"]) == 25
            assert len([row for row in schema if row[0] == "trigger"]) == 46
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
            verify_raw_dependencies(connection, references=references)
            assert connection.execute(
                "SELECT question,volume_total_raw,outcome_label FROM market_observations "
                "JOIN outcome_observations USING(observation_id) ORDER BY question,outcome_index LIMIT 1"
            ).fetchone()[:] == ("Question 1?", "1234.5", "Yes")
            trade = connection.execute("SELECT sanitized_trade_json FROM trade_observations").fetchone()[0]
            assert "name" not in json.loads(trade)
            level_count = connection.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0]
            assert level_count == 8
        with sqlite3.connect(repository.db_path) as physical:
            assert "question" not in {row[1] for row in physical.execute("PRAGMA table_info(market_observations)")}
            assert physical.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0] == 0
            for statement in (
                "UPDATE market_observations SET source_market_key='changed'",
                "DELETE FROM market_observations",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    physical.execute(statement)
            for table in PROFILE.tables:
                if table in ("resolution_watchlist", "prior_census_conditions"):
                    assert not physical.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)
                    ).fetchone()
        repository.initialize()  # Native source indexes must not be recreated over skeletons.
        manifest = repository._manifest_database_entry(repository.db_path, cadence_minutes=15, active=True)
        assert manifest["healthy"], manifest
        assert len(manifest["table_row_counts"]) == 25
        assert manifest["append_only_trigger_count"] == 46
        assert manifest["shared_dependencies"]["public_projections"]["receipt_count"] > 0
        assert manifest["shared_dependencies"]["public_bodies"]["payload_count"] > 0
        assert manifest["shared_dependencies"]["portable_requires_shared_closure"]
        status = repository.status()
        assert len(status["table_row_counts"]) == 25
        assert status["append_only_trigger_count"] == 46


def test_body_only_shared_levels_reopen_preserves_main_guards_and_exact_schema(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / "public.db") as store:
        bind(monkeypatch, store, store, raw=False)
        repository, _, _ = producer(tmp_path)
        with repository._read_connect() as connection:
            assert raw_layout_metadata(connection) is None
            assert connection.execute("SELECT COUNT(*) FROM main.orderbook_levels").fetchone()[0] == 0
            levels = [tuple(row) for row in connection.execute("SELECT * FROM orderbook_levels ORDER BY level_id")]
            assert len(levels) == 8
            schema = repository._logical_schema(connection)
        restarted = ResearchRepository(repository.db_path, clock=lambda: NOW)
        restarted.initialize()
        with restarted._read_connect() as connection:
            assert restarted._logical_schema(connection) == schema
            assert [tuple(row) for row in connection.execute("SELECT * FROM orderbook_levels ORDER BY level_id")] == levels
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        with restarted._connect() as connection:
            for action in ("UPDATE main.market_sweeps SET run_id=run_id", "DELETE FROM main.market_sweeps"):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    connection.execute(action)
            assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='orderbook_levels'").fetchone()[0] == 2


def test_native_rotation_preserves_runtime_and_carry_without_new_run(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / "public.db") as store:
        references = bind(monkeypatch, store, store)
        repository, collector, _ = producer(tmp_path)
        collector.gamma.session = _GetSession(
            lambda url, params: {"markets": [_market(1)], "next_cursor": None}
            if url.endswith("/markets/keyset") else [_market(2)]
        )
        audit = ResearchRunAudit.start(collector.config, repository=repository)
        audit.succeed(collector.run_cycle(audit.run_id))
        expected_watermark = repository.latest_trade_watermark()
        expected_census = repository.latest_census_conditions()
        with repository._read_connect() as connection:
            namespace = raw_layout_metadata(connection)["namespace"]
        monkeypatch.setenv("PUBLIC_MARKET_DATA_RAW", "0")
        repository = ResearchRepository(repository.db_path, clock=lambda: NEXT_DAY)
        archive = repository.rotate_if_utc_day_changed(NEXT_DAY)
        assert archive.name == "trades_sim_20260806.db"
        assert repository.latest_trade_watermark() == expected_watermark
        assert set(repository.latest_census_conditions()) == set(expected_census)
        with repository._read_connect() as connection:
            assert raw_layout_metadata(connection)["namespace"] == namespace
            assert connection.execute("SELECT COUNT(*) FROM research_run_events").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM resolution_watchlist").fetchone()[0] == 1
            verify_raw_dependencies(connection, references=references)
        with PayloadReader(tmp_path / "public.db") as reader:
            bind(monkeypatch, reader, raw=False)
            frozen = ResearchRepository(archive, immutable_reads=True)
            with frozen._read_connect() as connection:
                assert connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0] == 3
            manifest = frozen._manifest_database_entry(archive, cadence_minutes=15, active=False)
            assert manifest["healthy"], manifest
            assert manifest["shared_dependencies"]["public_projections"]["receipt_count"] > 0
            with pytest.raises(RuntimeError, match="immutable"):
                with frozen._connect():
                    pass


@pytest.mark.parametrize("change", ["source", "job", "authority"])
def test_native_reader_revalidates_owner_and_authority(tmp_path, monkeypatch, change):
    with PayloadStore(tmp_path / "public.db") as store:
        bind(monkeypatch, store, store)
        repository, _, _ = producer(tmp_path)
        if change == "source":
            monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "different-source")
        elif change == "job":
            monkeypatch.setenv("JOB_NAME", "different-parent")
        else:
            with PayloadStore(tmp_path / "other.db") as other:
                bind(monkeypatch, other, raw=False)
                with pytest.raises((ValueError, RuntimeError), match="authority"):
                    with repository._read_connect():
                        pass
            return
        with pytest.raises(RuntimeError, match="source/job/runtime"):
            with repository._read_connect():
                pass


@pytest.mark.parametrize("conflict", ["source", "job", "namespace"])
def test_immutable_selected_copy_uses_bound_owner_without_writer_or_runtime_environment(tmp_path, monkeypatch, conflict):
    with PayloadStore(tmp_path / "public.db") as store:
        bind(monkeypatch, store, store)
        repository, _, _ = producer(tmp_path)
        copy = tmp_path / "verified-research-copy.db"
        shutil.copyfile(repository.db_path, copy)
        before = copy.read_bytes()
        with PayloadReader(tmp_path / "public.db") as reader:
            bind(monkeypatch, reader)
            monkeypatch.delenv("PUBLIC_MARKET_DATA_SOURCE")
            monkeypatch.delenv("JOB_NAME")
            selected = ResearchRepository(copy, immutable_reads=True)
            validation = selected.validate_read_only_database()
            assert validation["healthy"], json.dumps(validation, indent=2)
            assert validation["shared_dependencies"]["raw_layout"]["profile_id"] == POMEGRANATE_PROFILE_ID
            with selected._read_connect() as connection:
                assert connection.execute("SELECT question FROM market_observations LIMIT 1").fetchone()[0]
            assert copy.read_bytes() == before
            assert not copy.with_name(copy.name + "-wal").exists()
            assert not copy.with_name(copy.name + "-shm").exists()
            if conflict == "source":
                monkeypatch.setenv("PUBLIC_MARKET_DATA_SOURCE", "unrelated-source")
            elif conflict == "job":
                monkeypatch.setenv("JOB_NAME", "unrelated-job")
            else:
                selected = ResearchRepository(copy, immutable_reads=True, raw_namespace="unrelated-owner")
            assert not selected.validate_read_only_database()["healthy"]


def test_native_duplicate_trade_retains_original_lexical_row_and_receipt(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / "public.db") as store:
        references = bind(monkeypatch, store, store)
        repository, _, _ = producer(tmp_path)
        with repository._read_connect() as connection:
            original = dict(next(iter_raw_logical_rows(connection, "trade_observations")))
            record_id = connection.execute("SELECT _public_record_id FROM main.trade_observations").fetchone()[0]
        changed = {key: value for key, value in original.items() if key != "__rowid__"}
        changed.update(size_raw="5.000", price_raw="0.4100", first_received_at=NEXT_DAY.isoformat())
        with repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            repository._insert_many(connection, "trade_observations", tuple(changed), [changed], or_ignore=True)
            connection.commit()
        with repository._read_connect() as connection:
            assert dict(next(iter_raw_logical_rows(connection, "trade_observations"))) == original
            assert connection.execute("SELECT _public_record_id FROM main.trade_observations").fetchone()[0] == record_id
            verify_raw_dependencies(connection, references=references)


def test_secondary_failure_keeps_complete_gamma_and_visible_gap(tmp_path, monkeypatch):
    from requests.exceptions import ChunkedEncodingError

    with PayloadStore(tmp_path / "public.db") as store:
        bind(monkeypatch, store, store)
        repository, _, stats = producer(tmp_path, clob_error=ChunkedEncodingError("fixture failure"))
        assert stats["markets_observed"] == 2
        with repository._read_connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM market_sweeps").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0] == 0
            assert connection.execute(
                "SELECT possible_gap FROM source_component_runs WHERE component='clob_books'"
            ).fetchone()[0] == 1


def test_activation_rejects_populated_inline_source(tmp_path, monkeypatch):
    bind(monkeypatch, None, raw=False)
    repository, _, _ = producer(tmp_path)
    with PayloadStore(tmp_path / "public.db") as store:
        bind(monkeypatch, store, store)
        configured = ResearchRepository(repository.db_path, clock=lambda: NOW)
        with pytest.raises(RuntimeError, match="offline derivative"):
            configured.initialize()
        with sqlite3.connect(repository.db_path) as connection:
            assert not raw_layout_metadata(connection)
            assert connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0] == 2


@pytest.mark.parametrize("failure", ["projection_ack", "receipt_ack"])
def test_failed_shared_ack_rolls_back_whole_native_gamma_bundle(tmp_path, monkeypatch, failure):
    with PayloadStore(tmp_path / "public.db") as store:
        bind(monkeypatch, store, store)
        if failure == "projection_ack":
            publish = store.put_public_projections

            def fail_projection(groups):
                values = list(groups)
                if any(value.kind == PROFILE.tables["outcome_observations"].kind for value in values):
                    raise ValueError("fixture projection ACK failure")
                return publish(values)

            monkeypatch.setattr(store, "put_public_projections", fail_projection)
        else:
            publish = store.append_projection_receipts

            def fail_receipt(receipts, *, authority_uuid):
                values = list(receipts)
                if any(value.origin_table == "outcome_observations" for value in values):
                    raise ValueError("fixture receipt ACK failure")
                return publish(values, authority_uuid=authority_uuid)

            monkeypatch.setattr(store, "append_projection_receipts", fail_receipt)
        with pytest.raises(ValueError, match="fixture .* ACK failure"):
            producer(tmp_path)
        repository = ResearchRepository(tmp_path / "data" / "integration" / "trades_sim.db")
        with repository._read_connect() as connection:
            for table in (
                "market_sweeps", "market_observations", "outcome_observations",
                "market_sweep_memberships", "market_metadata_versions", "raw_payloads",
            ):
                assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM api_requests").fetchone()[0] > 0


def test_shared_rotation_resumes_same_inode_handoff_and_rejects_collision(tmp_path, monkeypatch):
    with PayloadStore(tmp_path / "public.db") as store:
        references = bind(monkeypatch, store, store)
        repository, _, _ = producer(tmp_path)
        archive = repository.db_path.with_name("trades_sim_20260806.db")
        archive.write_bytes(b"distinct existing archive")
        with pytest.raises(FileExistsError, match="archive already exists"):
            repository.rotate_if_utc_day_changed(NEXT_DAY)
        assert archive.read_bytes() == b"distinct existing archive"
        archive.unlink()
        replace = os.replace

        def fail_replace(source, destination):
            if destination == repository.db_path:
                raise OSError("fixture interrupted handoff")
            return replace(source, destination)

        monkeypatch.setattr(repository_module.os, "replace", fail_replace)
        with pytest.raises(OSError, match="interrupted handoff"):
            repository.rotate_if_utc_day_changed(NEXT_DAY)
        assert os.path.samefile(repository.db_path, archive)
        monkeypatch.setattr(repository_module.os, "replace", replace)
        assert repository.rotate_if_utc_day_changed(NEXT_DAY) == archive
        assert not os.path.samefile(repository.db_path, archive)
        with repository._read_connect() as connection:
            verify_raw_dependencies(connection, references=references)
            assert connection.execute("SELECT COUNT(*) FROM prior_census_conditions").fetchone()[0] == 2
