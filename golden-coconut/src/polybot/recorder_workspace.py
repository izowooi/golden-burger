"""Exact existing White workspace and the shared off-volume T7 trust anchor."""
from pathlib import Path
import json,plistlib,shutil,subprocess
from .recorder_config import PROJECT,RUNTIME

def verify_workspace(config):
    workspace=Path('/Volumes/t7/jenkins')/config.jenkins_job
    mount=Path('/Volumes/t7');sentinel=mount/'.golden-raspberry-volume'
    pin=Path('/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid')
    if PROJECT!=workspace/'golden-coconut' or workspace.is_symlink() or not workspace.is_dir():raise RuntimeError('unapproved recorder workspace')
    if workspace.resolve()!=workspace or mount.is_symlink() or not mount.is_dir():raise RuntimeError('unsafe external mount')
    info=plistlib.loads(subprocess.run(['/usr/sbin/diskutil','info','-plist',str(mount)],capture_output=True,check=True,timeout=5).stdout)
    if info.get('FilesystemType','').lower()!='apfs' or info.get('Internal') is not False or info.get('MountPoint')!=str(mount):raise RuntimeError('external APFS not proven')
    if sentinel.is_symlink() or pin.is_symlink() or not sentinel.is_file() or not pin.is_file():raise RuntimeError('missing T7 trust anchors')
    values=dict(line.split('=',1) for line in sentinel.read_text().splitlines() if '=' in line)
    uid=info.get('VolumeUUID')
    if values.get('profile')!='golden-raspberry-apfs-v1' or values.get('volume_uuid')!=uid or pin.read_text().strip()!=uid:raise RuntimeError('T7 UUID mismatch')
    if workspace.stat().st_dev!=mount.stat().st_dev or pin.stat().st_dev==mount.stat().st_dev:raise RuntimeError('T7 device identity mismatch')
    marker=workspace/'.daily-rsync-workspace.json'
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text())!={'schema_version':1,'job':config.jenkins_job,'workspace':str(workspace)}:raise RuntimeError('workspace marker mismatch')
    usage=shutil.disk_usage(workspace)
    if usage.free<config.min_free_gib*1024**3 or usage.used/usage.total>=config.max_used_ratio:raise RuntimeError('recorder storage gate')
    return {'workspace':str(workspace),'runtime':RUNTIME,'free_bytes':usage.free,'status':'ok'}
