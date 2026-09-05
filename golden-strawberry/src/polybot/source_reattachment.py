"""Explicit, content-verified device-only reattachment of the frozen v1 file.

The old DB anchor is never updated. A maintenance command hashes every source
byte against an independently verified expected checksum and writes a private
off-volume approval receipt. Normal cycles still pin inode/size/mtime and the
trusted APFS UUID; they do not hash the 30GB source on every invocation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Mapping
from uuid import uuid4

RECEIPT_DIRECTORY = Path('/Users/jongwoopark/.jenkins/golden-strawberry-source-reattachments')
PROOF = 'FROZEN_V1_IDENTICAL_CONTENT_DEVICE_REATTACHMENT_V1'


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _fingerprint(source: Path, attributes: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({'path': str(source), **attributes})).hexdigest()


def _stable_identity(source: Path, attributes: Mapping[str, Any], volume_uuid: str) -> str:
    return hashlib.sha256(_canonical({'path':str(source),'volume_uuid':volume_uuid,
        **{key:attributes[key] for key in ('inode','size_bytes','mtime_ns')}})).hexdigest()


def _attributes(source: Path) -> dict:
    s=source.stat()
    return {'device':s.st_dev, 'inode':s.st_ino, 'size_bytes':s.st_size, 'mtime_ns':s.st_mtime_ns}


def _digest_is_valid(value: Any) -> bool:
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def _trusted_volume_uuid(source: Path) -> str:
    verifier=source.parents[2]/'scripts/verify_external_workspace.py'
    spec=importlib.util.spec_from_file_location('strawberry_external_reattachment',verifier)
    if spec is None or spec.loader is None:
        raise RuntimeError('trusted workspace verifier unavailable')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.verify_external_workspace(
        mount_root=module.DEFAULT_MOUNT_ROOT, workspace=source.parents[3],
        expected_workspace=module.DEFAULT_EXPECTED_WORKSPACE, job='polybot-shadow-one',
        sentinel=module.DEFAULT_SENTINEL, host_uuid_pin=module.DEFAULT_HOST_UUID_PIN,
        write_marker=False,
    )
    # The verifier intentionally does not publish the UUID in its return value;
    # after it proves both existing pins match the real volume, use that pin.
    return module.DEFAULT_HOST_UUID_PIN.read_text(encoding='utf-8').strip()


def _receipt_directory(source: Path, *, create: bool) -> Path:
    directory=RECEIPT_DIRECTORY
    if directory.is_symlink() or directory.parent.resolve()!=directory.parent:
        raise RuntimeError('reattachment receipt directory is unsafe')
    if create:
        directory.mkdir(mode=0o700,parents=False,exist_ok=True)
    if not directory.is_dir() or directory.stat().st_dev==source.stat().st_dev:
        raise RuntimeError('reattachment approval must be stored off the data volume')
    if directory.stat().st_uid!=os.geteuid() or stat.S_IMODE(directory.stat().st_mode)&0o077:
        raise RuntimeError('reattachment receipt directory is not private')
    return directory


def _validate_device_change(source: Path, anchor: Mapping[str,Any], current: dict, old_device: int) -> str:
    from .v1_source import anchor_sha256

    if anchor_sha256(anchor)!=anchor['anchor_sha256']:
        raise RuntimeError('original source anchor checksum mismatch')
    if source.is_symlink() or source.resolve()!=source or not source.is_file():
        raise RuntimeError('source path is unsafe')
    if str(source)!=anchor['source_path'] or current['size_bytes']!=anchor['source_db_size_bytes'] or current['mtime_ns']!=anchor['source_db_mtime_ns']:
        raise RuntimeError('source path/size/mtime changed; device-only reattachment forbidden')
    if type(old_device) is not int or old_device<0 or old_device==current['device']:
        raise RuntimeError('no valid device-only change')
    old={**current,'device':old_device}
    if _fingerprint(source,old)!=anchor['source_file_fingerprint_sha256']:
        raise RuntimeError('inode or other source identity changed; old fingerprint not reproduced')
    if any(source.with_name(source.name+suffix).exists() for suffix in ('-wal','-shm','-journal')):
        raise RuntimeError('source sidecars forbid reattachment')
    return _trusted_volume_uuid(source)


def attest_device_reattachment(source: Path, anchor: Mapping[str,Any], *, old_device: int,
                              expected_content_sha256: str, apply: bool=False) -> dict:
    """One-time operator action; expected checksum must come from verified evidence."""
    if not _digest_is_valid(expected_content_sha256):
        raise ValueError('an independently verified SHA-256 is required')
    before=_attributes(source)
    volume_uuid=_validate_device_change(source,anchor,before,old_device)
    current_fingerprint=_fingerprint(source,before)
    stable_identity=_stable_identity(source,before,volume_uuid)
    plan={'proof':PROOF,'source_path':str(source),'anchor_sha256':anchor['anchor_sha256'],
          'original_fingerprint_sha256':anchor['source_file_fingerprint_sha256'],
          'current_fingerprint_sha256':current_fingerprint,'old_device':old_device,
          'current_stat':before,'volume_uuid':volume_uuid,'stable_identity_sha256':stable_identity,
          'source_content_sha256':expected_content_sha256}
    if not apply:
        return {**plan,'applied':False,'content_hash_verified':False}
    digest=hashlib.sha256()
    deadline=time.monotonic()+1800
    with source.open('rb') as handle:
        for chunk in iter(lambda:handle.read(8*1024*1024),b''):
            if time.monotonic()>=deadline:
                raise RuntimeError('full source attestation exceeded its 1800-second maintenance budget')
            digest.update(chunk)
    if digest.hexdigest()!=expected_content_sha256:
        raise RuntimeError('source content differs from the independently verified checksum')
    if _attributes(source)!=before or _validate_device_change(source,anchor,before,old_device)!=volume_uuid:
        raise RuntimeError('source identity changed during full content validation')
    directory=_receipt_directory(source,create=True)
    target=directory/f'{anchor["anchor_sha256"]}-{stable_identity}.json'
    payload={**plan,'content_hash_verified':True,'verified_at':datetime.now(timezone.utc).isoformat()}
    if target.exists():
        receipt=read_device_reattachment(source,anchor,before)
        return {'applied':False,'already_attested':True,'receipt':receipt}
    temporary=directory/f'.reattach-{uuid4().hex}.tmp'
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'wb') as handle:
            handle.write(_canonical(payload));handle.flush();os.fsync(handle.fileno())
        os.link(temporary,target)  # Atomic publication; never overwrite an approval.
        directory_fd=os.open(directory,os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return {'applied':True,'receipt':read_device_reattachment(source,anchor,before)}


def read_device_reattachment(source: Path, anchor: Mapping[str,Any], current: dict) -> dict:
    directory=_receipt_directory(source,create=False)
    fingerprint=_fingerprint(source,current)
    volume_uuid=_trusted_volume_uuid(source)
    stable_identity=_stable_identity(source,current,volume_uuid)
    path=directory/f'{anchor["anchor_sha256"]}-{stable_identity}.json'
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('no approved device reattachment receipt')
    s=path.stat()
    if s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)&0o077 or s.st_size>16384:
        raise RuntimeError('device reattachment receipt is unsafe')
    raw=path.read_bytes();payload=json.loads(raw)
    expected={'proof','source_path','anchor_sha256','original_fingerprint_sha256',
              'current_fingerprint_sha256','old_device','current_stat','volume_uuid',
              'source_content_sha256','content_hash_verified','verified_at','stable_identity_sha256'}
    if not isinstance(payload,dict) or set(payload)!=expected or payload['proof']!=PROOF or payload['content_hash_verified'] is not True:
        raise RuntimeError('device reattachment receipt contract mismatch')
    recorded=payload['current_stat']
    if (not isinstance(recorded,dict) or set(recorded)!={'device','inode','size_bytes','mtime_ns'}
        or any(type(value) is not int or value<0 for value in recorded.values())):
        raise RuntimeError('device reattachment receipt stat is invalid')
    if not _digest_is_valid(payload['source_content_sha256']):
        raise RuntimeError('receipt lacks a valid full-file content checksum')
    if (payload['source_path']!=str(source) or payload['anchor_sha256']!=anchor['anchor_sha256']
        or payload['original_fingerprint_sha256']!=anchor['source_file_fingerprint_sha256']
        or payload['stable_identity_sha256']!=stable_identity
        or _fingerprint(source,payload['current_stat'])!=payload['current_fingerprint_sha256']
        or any(payload['current_stat'][key]!=current[key] for key in ('inode','size_bytes','mtime_ns'))):
        raise RuntimeError('device reattachment receipt identity mismatch')
    verified=datetime.fromisoformat(payload['verified_at'])
    if verified.tzinfo is None or verified>datetime.now(timezone.utc):
        raise RuntimeError('device reattachment verification time is invalid')
    checked_uuid=_validate_device_change(source,anchor,current,payload['old_device'])
    if payload['volume_uuid']!=volume_uuid or checked_uuid!=volume_uuid or _attributes(source)!=current:
        raise RuntimeError('trusted volume/source changed after reattachment')
    return {**payload,'receipt_sha256':hashlib.sha256(raw).hexdigest(),
            'observed_file_fingerprint_sha256':fingerprint,'observed_device':current['device']}
