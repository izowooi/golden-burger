"""The same public receipts must produce the same research evidence after sharing."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import coconut_historical_grid as coconut
import conservative_sports_grid as grid
import sports_visual_data as visual
import watermelon_raw_sidecar as sidecar
import white_legacy_grid as legacy
import white_raw_grid as paired
from public_market_reader import public_references
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_refs import MissingMarketDataConfiguration, PayloadReferences
from polybot_observability.market_data_store import PayloadStore
import test_coconut_historical_grid as coconut_fixture
import test_conservative_sports_grid as grid_fixture
import test_white_legacy_grid as legacy_fixture
import test_white_raw_grid as paired_fixture


def externalize(database, store, strategy):
    destination = database.with_name(database.name + ".shared.db")
    result = migrate_public_bodies(
        database, destination, strategy=strategy, source_sha256=visual.sha256(database),
        references=PayloadReferences(reader=store, writer=store),
    )
    os.replace(destination, database)
    return sum(table.get("externalized_cells", 0) for table in result["tables"].values())


def test_raw_grid_and_visual_export_match_inline_and_shared_receipts(tmp_path):
    source, fixture = grid_fixture.raw_fixture_pin(tmp_path)
    source["runtime_job"] = "shadow"
    fixture.c.close()
    start, end = "2026-09-07T00:00:00Z", "2026-09-08T00:00:00Z"
    before_events, before_audit = grid.read_source(source, start, end)
    source_path = Path(source["local_path"])

    def exported(destination, refs):
        (destination / "events").mkdir(parents=True)
        index = {"cohorts": {}, "matches": [], "sources": []}
        visual.export_source(source, destination, start, end, index,
                             include_depth=True, references=refs)
        return index, {p.name: p.read_bytes() for p in (destination / "events").iterdir()}

    with public_references() as refs:
        before_index, before_fragments = exported(tmp_path / "inline", refs)
    public = tmp_path / "public.db"
    with PayloadStore(public) as store:
        assert externalize(source_path, store, "golden-peach") > 0
    source["local_sha256"] = visual.sha256(source_path)
    with public_references(public) as refs:
        after_events, after_audit = grid.read_source(source, start, end, references=refs)
        after_index, after_fragments = exported(tmp_path / "shared", refs)
    assert before_events == after_events
    assert {k: v for k, v in before_audit.items() if k != "sha256"} == {
        k: v for k, v in after_audit.items() if k != "sha256"
    }
    assert before_index["matches"] == after_index["matches"]
    assert before_index["cohorts"] == after_index["cohorts"]
    assert before_fragments == after_fragments
    with public_references() as empty, pytest.raises(MissingMarketDataConfiguration):
        grid.read_source(source, start, end, references=empty)


@pytest.mark.parametrize("family", ["coconut", "legacy"])
def test_historical_readers_keep_every_decoded_receipt(tmp_path, family):
    module = coconut_fixture if family == "coconut" else legacy_fixture
    adapter = coconut if family == "coconut" else legacy
    fixture = module.Fixture(tmp_path)
    try:
        fixture.event()
        source = fixture.source()
        baseline = adapter.read_source(source, module.START, module.END)
        public = tmp_path / "public.db"
        with PayloadStore(public) as store:
            assert externalize(fixture.path, store, "golden-coconut" if family == "coconut"
                               else "golden-watermelon") > 0
        source = fixture.source()
        with public_references(public) as refs:
            actual = adapter.read_source(source, module.START, module.END, references=refs)
        assert actual == baseline
    finally:
        fixture.c.close()


def test_paired_white_reader_preserves_parent_and_sidecar_body_types(tmp_path):
    case = paired_fixture.PairedAdapterTests()
    case.setUp()
    try:
        case.publish()
        parent_path = tmp_path / "trades_sim.db"
        raw_path = tmp_path / "sidecar.db"
        with sqlite3.connect(parent_path) as target:
            case.fixture.parent.backup(target)
            target.execute("PRAGMA application_id=1196903732")
        with sqlite3.connect(raw_path) as target:
            case.fixture.raw.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        source = {**case.source, "pinned": True, "local_path": str(parent_path),
                  "sidecar_path": str(raw_path), "local_sha256": visual.sha256(parent_path),
                  "sidecar_sha256": visual.sha256(raw_path)}
        start, end = "2026-09-07T00:00:00Z", "2026-09-08T00:00:00Z"
        baseline_events, baseline_audit = paired.read_source(source, start, end)
        with closing(sidecar.open_pin(parent_path, source["local_sha256"])) as parent:
            with closing(sidecar.open_pin(raw_path, source["sidecar_sha256"])) as raw:
                baseline_rows = list(sidecar.iter_rows(parent, raw, start, end))
        public = tmp_path / "public.db"
        with PayloadStore(public) as store:
            assert externalize(parent_path, store, "golden-watermelon") > 0
            assert externalize(raw_path, store, "golden-watermelon") > 0
        source.update(local_sha256=visual.sha256(parent_path), sidecar_sha256=visual.sha256(raw_path))
        with public_references(public) as refs:
            actual_events, actual_audit = paired.read_source(source, start, end, references=refs)
            with closing(sidecar.open_pin(parent_path, source["local_sha256"], references=refs)) as parent:
                with closing(sidecar.open_pin(raw_path, source["sidecar_sha256"], references=refs)) as raw:
                    actual_rows = list(sidecar.iter_rows(parent, raw, start, end))
        assert actual_events == baseline_events
        for audit in (actual_audit, baseline_audit):
            audit.pop("parent_sha256")
            audit.pop("sidecar_sha256")
        assert actual_audit == baseline_audit
        assert actual_rows == baseline_rows
    finally:
        case.tearDown()


def test_public_reader_context_honors_explicit_store_without_mutating_environment(tmp_path, monkeypatch):
    public = tmp_path / "public.db"
    with PayloadStore(public) as writer:
        digest = writer.put_many([b"receipt"])[0]
    monkeypatch.setenv("PUBLIC_MARKET_DATA_DB", str(tmp_path / "wrong.db"))
    with public_references(public) as references:
        assert references.reader.get_many([digest]) == [b"receipt"]
        owned_reader = references.reader
    assert os.environ["PUBLIC_MARKET_DATA_DB"].endswith("wrong.db")
    with pytest.raises(sqlite3.ProgrammingError):
        owned_reader.get_many([digest])
