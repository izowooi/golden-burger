"""Standalone reports keep strategy evidence intact on migrated catalogs."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_reader import market_data_connect, public_references
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore
from tests.test_analyze_experiment import _database
from tests.test_analyze_shadow import _prospective_database


@pytest.mark.parametrize("script_name", ["analyze_experiment.py", "analyze_shadow.py"])
def test_public_catalog_cli_parity_and_read_only_paths(tmp_path, monkeypatch, script_name):
    monkeypatch.delenv("PUBLIC_MARKET_DATA_DB", raising=False)
    output = tmp_path / "report"
    script = Path(__file__).resolve().parents[1] / "scripts" / script_name
    command = [sys.executable, str(script), "--output-dir", str(output)]
    if script_name == "analyze_experiment.py":
        paths = [tmp_path / "a ?#%.db", tmp_path / "b ?#%.db"]
        for path, surge, job in zip(paths, (0.02, 0.05), ("blueberry-a", "blueberry-b")):
            _database(path, min_surge=surge, job_name=job)
        command += ["--arm-a", str(paths[0]), "--arm-b", str(paths[1]),
                    "--review-start", "2026-08-04T12:00:00Z", "--review-end", "2026-08-04T13:00:00Z"]
    else:
        paths = [tmp_path / "shadow ?#%.db"]
        _prospective_database(paths[0])
        command += ["--db", str(paths[0]), "--review-start", "2026-08-31T00:00:00Z",
                    "--review-end", "2026-09-05T00:00:00Z"]
    original = '["Yes", "No"]'
    for path in paths:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE market_catalog(condition_id PRIMARY KEY, outcomes_json)")
            connection.execute("INSERT INTO market_catalog VALUES('condition-one',?)", (original,))
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    baseline = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    expected = json.loads(Path(baseline.stdout.strip()).read_text())
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())

    public = tmp_path / "public ?#%.sqlite"
    with PayloadStore(public) as store:
        refs = PayloadReferences(reader=store, writer=store)
        for path in paths:
            destination = path.with_suffix(".shared.db")
            result = migrate_public_bodies(path, destination, strategy="golden-blueberry",
                                           source_sha256=before[path], references=refs)
            assert result["tables"]["market_catalog"]["externalized_cells"] == 1
            os.replace(destination, path)
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    public_before = public.read_bytes()
    actual = subprocess.run(command + ["--public-store", str(public)], check=True,
                            capture_output=True, text=True, timeout=30)
    payload = Path(actual.stdout.strip()).read_text()
    for path in paths:
        payload = payload.replace(after[path], before[path])
    assert json.loads(payload) == expected
    # These reports consume local scalar decisions/ledgers; only source catalog
    # arrays move. The same connection still reconstructs that catalog exactly.
    with public_references(public) as refs:
        connection = market_data_connect(paths[0].as_uri() + "?mode=ro", uri=True, references=refs)
        try:
            assert connection.execute("SELECT outcomes_json FROM market_catalog").fetchone()[0] == original
        finally:
            connection.close()
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in after.items())
    assert public.read_bytes() == public_before
    assert not (tmp_path / "a ").exists()

    missing = tmp_path / "missing.sqlite"
    failed = subprocess.run(command + ["--public-store", str(missing)], capture_output=True, text=True, timeout=30)
    assert failed.returncode != 0
    assert not missing.exists()
