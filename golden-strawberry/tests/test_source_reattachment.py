from __future__ import annotations
import hashlib
import json
from pathlib import Path

import pytest

from polybot import source_reattachment as recovery
from polybot.v1_source import V1SourceReader, anchor_sha256
from tests.followup_support import build_v1_handoff


@pytest.fixture
def source_case(config,followup_config,tmp_path,monkeypatch):
    build_v1_handoff(config)
    reader=V1SourceReader(followup_config.trading.v1_source)
    anchor=dict(reader.capture().anchor)
    source=reader.path
    attributes=recovery._attributes(source)
    old_device=attributes['device']+4
    anchor['source_file_fingerprint_sha256']=recovery._fingerprint(source,{**attributes,'device':old_device})
    anchor['anchor_sha256']=anchor_sha256(anchor)
    directory=tmp_path/'host-receipts';directory.mkdir(mode=0o700)
    monkeypatch.setattr(recovery,'_receipt_directory',lambda source,create:directory)
    monkeypatch.setattr(recovery,'_trusted_volume_uuid',lambda source:'PINNED-TEST-UUID')
    checksum=hashlib.sha256(source.read_bytes()).hexdigest()
    return source,reader,anchor,old_device,checksum,directory


def test_normal_cycle_does_not_approve_device_drift(source_case):
    source,reader,anchor,device,checksum,directory=source_case
    with pytest.raises(RuntimeError,match='no approved'):
        reader.validate_stored_anchor(anchor)
    assert list(directory.iterdir())==[]


def test_dry_run_does_not_publish_an_approval(source_case):
    source,reader,anchor,device,checksum,directory=source_case
    result=recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum)
    assert result['applied'] is False and result['content_hash_verified'] is False
    assert list(directory.iterdir())==[]


def test_content_attested_device_change_preserves_original_anchor_and_file(source_case,monkeypatch):
    source,reader,anchor,device,checksum,directory=source_case
    original=dict(anchor);before=source.read_bytes()
    result=recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum,apply=True)
    assert result['applied']
    receipt=list(directory.iterdir())[0]
    receipt_bytes=receipt.read_bytes()
    assert receipt.stat().st_mode&0o077==0
    # The recurring fast path must not rehash every byte or regenerate the seed.
    monkeypatch.setattr(reader,'capture',lambda **kw:pytest.fail('seed must not be recaptured'))
    assert reader.validate_stored_anchor(anchor)==original
    assert reader.last_device_reattachment['source_content_sha256']==checksum
    assert reader.last_device_reattachment['receipt_sha256']==hashlib.sha256(receipt_bytes).hexdigest()
    assert source.read_bytes()==before and anchor==original
    assert not any(source.with_name(source.name+x).exists() for x in ('-wal','-shm','-journal'))
    again=recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum,apply=True)
    assert again['already_attested'] and receipt.read_bytes()==receipt_bytes


def test_wrong_independent_checksum_never_writes_receipt(source_case):
    source,reader,anchor,device,checksum,directory=source_case
    with pytest.raises(RuntimeError,match='content differs'):
        recovery.attest_device_reattachment(source,anchor,old_device=device,
            expected_content_sha256='0'*64,apply=True)
    assert list(directory.iterdir())==[]


@pytest.mark.parametrize('change', ['inode','mtime_ns','size_bytes'])
def test_non_device_identity_changes_are_not_reattachment(source_case,change):
    source,reader,anchor,device,checksum,directory=source_case
    changed=recovery._attributes(source);changed[change]+=1
    with pytest.raises(RuntimeError):
        recovery._validate_device_change(source,anchor,changed,device)


def test_content_or_inode_changes_after_approval_still_fail(source_case):
    source,reader,anchor,device,checksum,directory=source_case
    recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum,apply=True)
    other=source.with_name('replacement.db')
    other.write_bytes(source.read_bytes())
    other.replace(source)
    with pytest.raises(RuntimeError):reader.validate_stored_anchor(anchor)


def test_different_volume_uuid_is_rejected(source_case,monkeypatch):
    source,reader,anchor,device,checksum,directory=source_case
    recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum,apply=True)
    monkeypatch.setattr(recovery,'_trusted_volume_uuid',lambda source:'OTHER-VOLUME')
    with pytest.raises(RuntimeError):
        reader.validate_stored_anchor(anchor)


@pytest.mark.parametrize('change', ['anchor_sha256','current_fingerprint_sha256','current_stat','content_hash_verified'])
def test_malformed_or_wrong_receipt_is_not_used(source_case,change):
    source,reader,anchor,device,checksum,directory=source_case
    recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum,apply=True)
    path=list(directory.iterdir())[0];payload=json.loads(path.read_text())
    payload[change]=False if change=='content_hash_verified' else 'wrong'
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError):reader.validate_stored_anchor(anchor)


def test_receipt_permissions_and_symlink_are_not_trusted(source_case):
    source,reader,anchor,device,checksum,directory=source_case
    recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum,apply=True)
    path=list(directory.iterdir())[0];path.chmod(0o644)
    with pytest.raises(RuntimeError,match='unsafe'):reader.validate_stored_anchor(anchor)


def test_later_device_renumbering_reuses_verified_stable_volume_identity(source_case,monkeypatch):
    from polybot import v1_source

    source,reader,anchor,device,checksum,directory=source_case
    recovery.attest_device_reattachment(source,anchor,old_device=device,
        expected_content_sha256=checksum,apply=True)
    current=recovery._attributes(source)
    renumbered={**current,'device':current['device']+8}
    monkeypatch.setattr(recovery,'_attributes',lambda path:renumbered)
    monkeypatch.setattr(v1_source,'_stat_payload',lambda path:renumbered)
    assert reader.validate_stored_anchor(anchor)==anchor
    assert reader.last_device_reattachment['observed_device']==renumbered['device']
    assert len(list(directory.iterdir()))==1


def test_frozen_preregistration_not_replaced_by_operational_amendment(project_root):
    from polybot.followup_source_digest import FOLLOWUP_PREREGISTRATION,FOLLOWUP_SOURCE_PATHS
    assert FOLLOWUP_PREREGISTRATION=='research/frozen-2026-08-24-followup-v2a/PREREGISTRATION.md'
    assert 'src/polybot/source_reattachment.py' in FOLLOWUP_SOURCE_PATHS
    assert 'research/amendment-2026-09-05-device-reattachment/OPERATIONS_AMENDMENT.md' in FOLLOWUP_SOURCE_PATHS
