"""Exact T7 routing, using the existing independently pinned shared volume."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
from uuid import UUID,uuid4

MOUNT=Path('/Volumes/t7')
SENTINEL=MOUNT/'.golden-raspberry-volume'
HOST_PIN=Path('/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid')
PROFILE='golden-raspberry-apfs-v1'
MARKER='.daily-rsync-workspace.json'


class WorkspaceError(RuntimeError):pass


def _no_symlinks(path):
    path=Path(path)
    if not path.is_absolute():raise WorkspaceError('absolute trusted path required')
    current=Path(path.anchor)
    for part in path.parts[1:]:
        current/=part
        if current.is_symlink():raise WorkspaceError('symlink in trusted path')
        if not current.exists():break


def _disk_info(path):
    result=subprocess.run(['/usr/sbin/diskutil','info','-plist',str(path)],
        capture_output=True,check=True,timeout=4)
    return plistlib.loads(result.stdout)


def _device_id(path):return Path(path).stat().st_dev


def _regular(path):
    _no_symlinks(path)
    if not path.is_file():raise WorkspaceError('trusted identity file absent')


def _read_sentinel(path):
    values={}
    for line in path.read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep:
            if key in values:raise WorkspaceError('duplicate sentinel identity')
            values[key]=value
    return values


def verify_workspace(config,workspace,*,write_marker=False,environment=None):
    env=os.environ if environment is None else environment
    workspace=Path(workspace)
    if workspace!=config.expected_workspace:raise WorkspaceError('workspace differs from runtime identity')
    if env.get('JOB_NAME') not in (None,config.spec.jenkins_job):raise WorkspaceError('Jenkins job/runtime mismatch')
    if env.get('WORKSPACE') not in (None,str(workspace)):raise WorkspaceError('Jenkins WORKSPACE mismatch')
    for path in (MOUNT,SENTINEL,HOST_PIN,workspace,config.root,config.db_path):_no_symlinks(path)
    if not MOUNT.is_dir() or not workspace.is_dir():raise WorkspaceError('mounted workspace required; never create fallback')
    if workspace.resolve()!=workspace or config.root.resolve()!=workspace/'golden-guava':
        raise WorkspaceError('runtime code is outside its approved external workspace')
    info=_disk_info(MOUNT)
    if (info.get('FilesystemType','').lower()!='apfs' or info.get('Internal') is not False
        or info.get('MountPoint')!=str(MOUNT)):
        raise WorkspaceError('exact external APFS mount not proven')
    try:volume=str(UUID(info.get('VolumeUUID',''))).upper()
    except (ValueError,TypeError,AttributeError):raise WorkspaceError('invalid volume identity') from None
    _regular(SENTINEL);_regular(HOST_PIN)
    sentinel=_read_sentinel(SENTINEL)
    if (sentinel.get('profile')!=PROFILE or sentinel.get('volume_uuid','').upper()!=volume
        or HOST_PIN.read_text().strip().upper()!=volume):raise WorkspaceError('trusted volume UUID changed')
    device=_device_id(MOUNT)
    if device==_device_id(Path('/')) or device==_device_id(HOST_PIN) or device!=_device_id(workspace):
        raise WorkspaceError('workspace/host pin device boundary is invalid')
    expected_db=config.root/'data'/config.job_name/('trades_sim.db' if config.simulation_mode else 'trades.db')
    if config.db_path!=expected_db or not config.db_path.resolve().is_relative_to(workspace):
        raise WorkspaceError('DB escapes exact runtime path')
    existing=config.db_path
    while not existing.exists():existing=existing.parent
    if _device_id(existing)!=device:raise WorkspaceError('DB is not on the trusted volume')
    if config.db_path.exists() and not config.db_path.is_file():raise WorkspaceError('invalid DB file target')
    usage=shutil.disk_usage(workspace)
    if usage.free<config.trading.min_free_gib*1024**3 or usage.used/usage.total>=config.trading.max_disk_used_ratio:
        raise WorkspaceError('external disk safety headroom exhausted')
    payload={'schema_version':1,'job':config.spec.jenkins_job,'workspace':str(workspace)}
    marker=workspace/MARKER
    _no_symlinks(marker)
    if marker.exists():
        _regular(marker)
        if json.loads(marker.read_text())!=payload:raise WorkspaceError('existing marker belongs to another identity')
    elif write_marker:
        temp=workspace/(MARKER+'.tmp.'+uuid4().hex)
        descriptor=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            with os.fdopen(descriptor,'w') as handle:
                json.dump(payload,handle,sort_keys=True);handle.flush();os.fsync(handle.fileno())
            os.replace(temp,marker)
            directory=os.open(workspace,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:temp.unlink(missing_ok=True)
    else:raise WorkspaceError('explicit first-deployment marker creation required')
    if _device_id(marker)!=device:raise WorkspaceError('volume changed while checking marker')
    return {'status':'ok','workspace':str(workspace),'job':config.spec.jenkins_job,
        'runtime_job':config.job_name,'free_bytes':usage.free,'used_ratio':usage.used/usage.total,
        'volume_profile':PROFILE,'marker':str(marker)}
