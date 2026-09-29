from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import replace

import pytest
from polybot_observability.market_data_bundle import closure_digest

from daily_rsync.config import load_config, validate_public_source
from daily_rsync.remote import PublicPayloadBundleLimitError, RemoteClient, RemoteCommandError


@pytest.fixture
def configured(app_config):
    return replace(
        app_config,
        remote_public_python="/remote/path with space/python",
        remote_public_db="/remote/public/public.db",
        remote_public_storage_root="/remote/public",
        remote_staging_root="/remote/staging",
    )


def response(config, hashes):
    return {
        "bundle_path": config.remote_staging_root + "/shared-payload-" + "a" * 32 + "/payloads.db",
        "manifest": {
            "contract": "public-payload-bundle-v1",
            "status": "VERIFIED",
            "payload_hashes": sorted(hashes),
            "closure_sha256": closure_digest(hashes),
            "file_sha256": "b" * 64,
            "payload_count": len(hashes),
            "raw_bytes": 1,
        },
        "source_identity": {
            "db_path": config.remote_public_db,
            "storage_root": config.remote_public_storage_root,
            "device": 42,
            "inode": 17,
        },
    }


def test_unconfigured_public_source_cannot_execute_or_create_store(app_config, monkeypatch):
    validate_public_source(app_config)
    monkeypatch.setattr(
        "daily_rsync.remote.subprocess.run",
        lambda *a, **k: pytest.fail("unconfigured source must not execute"),
    )
    with pytest.raises(ValueError, match="three canonical"):
        RemoteClient(app_config).export_public_payloads(["a" * 64])
    assert not app_config.public_store_path.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"remote_public_python": None},
        {"remote_public_python": "python3"},
        {"remote_public_db": "/outside/public.db"},
        {"remote_public_db": "/remote/public/../private.db"},
        {"remote_public_storage_root": "/remote/public/"},
    ],
)
def test_public_source_requires_canonical_complete_configuration(configured, changes):
    with pytest.raises(ValueError):
        validate_public_source(replace(configured, **changes), required=True)


def test_load_config_accepts_explicit_public_source_without_creating_local_store(
    configured,
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("DAILY_RSYNC_DATA_ROOT", raising=False)
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            f"{key} = {json.dumps(str(getattr(configured, key)))}"
            for key in (
                "data_root",
                "remote_public_python",
                "remote_public_db",
                "remote_public_storage_root",
            )
        )
    )
    result = load_config(path)
    assert result.remote_public_db == configured.remote_public_db
    assert result.public_store_path == result.data_root / "shared-market-data/public.db"
    assert not result.public_store_path.exists()


def test_export_uses_configured_cli_quoted_argv_and_bounded_hashes(configured, monkeypatch):
    hashes = [hashlib.sha256(b"raw public receipt").hexdigest()]
    expected = response(configured, hashes)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(expected), stderr="")

    monkeypatch.setattr("daily_rsync.remote.subprocess.run", run)
    assert RemoteClient(configured).export_public_payloads(hashes) == expected
    command, options = calls[0]
    words = shlex.split(command[-1])
    assert words[:4] == [
        configured.remote_public_python,
        "-m",
        "polybot_observability.market_data_bundle",
        "export",
    ]
    assert words[words.index("--db") + 1] == configured.remote_public_db
    assert json.loads(options["input"]) == {"hashes": hashes}


@pytest.mark.parametrize(
    "bad_path",
    [
        "/remote/staging/../private.db",
        "/remote/staging-not/shared-payload-" + "a" * 32 + "/payloads.db",
        "/remote/staging/shared-payload-" + "a" * 32 + "/private.db",
        "/remote/staging/payloads.db",
    ],
)
def test_remote_bundle_path_escape_is_rejected(configured, monkeypatch, bad_path):
    result = response(configured, ["a" * 64])
    result["bundle_path"] = bad_path
    monkeypatch.setattr(
        "daily_rsync.remote.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(result), ""),
    )
    with pytest.raises(RemoteCommandError):
        RemoteClient(configured).export_public_payloads(["a" * 64])


@pytest.mark.parametrize("change", ["hashes", "source", "raw_limit", "extra_identity"])
def test_remote_attestation_must_match_request_and_source(configured, monkeypatch, change):
    result = response(configured, ["a" * 64])
    if change == "hashes":
        result["manifest"]["payload_hashes"] = ["c" * 64]
    elif change == "source":
        result["source_identity"]["db_path"] = "/private.db"
    elif change == "raw_limit":
        result["manifest"]["raw_bytes"] = (256 << 20) + 1
    else:
        result["source_identity"]["unrequested"] = "private-marker"
    monkeypatch.setattr(
        "daily_rsync.remote.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(result), ""),
    )
    with pytest.raises(RemoteCommandError):
        RemoteClient(configured).export_public_payloads(["a" * 64])


def test_remote_limit_has_specific_error_and_other_stderr_is_not_echoed(configured, monkeypatch):
    failure = {"error": "BUNDLE_LIMIT", "message": "private-looking-marker"}
    monkeypatch.setattr(
        "daily_rsync.remote.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, "", json.dumps(failure)),
    )
    with pytest.raises(PublicPayloadBundleLimitError) as error:
        RemoteClient(configured).export_public_payloads(["a" * 64])
    assert "private-looking-marker" not in str(error.value)
    failure["error"] = "OTHER"
    with pytest.raises(RemoteCommandError) as error:
        RemoteClient(configured).export_public_payloads(["a" * 64])
    assert "private-looking-marker" not in str(error.value)


def test_cleanup_requires_exact_staged_path_and_sha(configured, monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"status":"REMOVED"}', "")

    monkeypatch.setattr("daily_rsync.remote.subprocess.run", run)
    client = RemoteClient(configured)
    client.cleanup_public_payloads(response(configured, ["a" * 64])["bundle_path"], "b" * 64)
    assert "--expected-sha" in shlex.split(commands[0][-1])
    with pytest.raises(RemoteCommandError):
        client.cleanup_public_payloads("/remote/private.db", "b" * 64)
    assert len(commands) == 1
