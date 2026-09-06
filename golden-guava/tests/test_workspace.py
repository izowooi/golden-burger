from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from polybot import workspace as w


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    mount=tmp_path/'volume';workspace=mount/'jenkins/polybot-sim-guava-a'
    root=workspace/'golden-guava';root.mkdir(parents=True)
    sentinel=mount/'sentinel';pin=tmp_path/'host-pin'
    uid='10524A4E-097F-4E31-B4A9-800952269E5F'
    sentinel.write_text('profile='+w.PROFILE+'\nvolume_uuid='+uid+'\n');pin.write_text(uid)
    monkeypatch.setattr(w,'MOUNT',mount);monkeypatch.setattr(w,'SENTINEL',sentinel);monkeypatch.setattr(w,'HOST_PIN',pin)
    monkeypatch.setattr(w,'_disk_info',lambda path:{'FilesystemType':'apfs','Internal':False,'MountPoint':str(mount),'VolumeUUID':uid})
    monkeypatch.setattr(w,'_device_id',lambda path:2 if Path(path).is_relative_to(mount) else 1)
    monkeypatch.setattr(w.shutil,'disk_usage',lambda path:SimpleNamespace(total=1000*1024**3,used=100*1024**3,free=900*1024**3))
    cfg=SimpleNamespace(expected_workspace=workspace,root=root,job_name='guava-research-a-v1',simulation_mode=True,
        db_path=root/'data/guava-research-a-v1/trades_sim.db',spec=SimpleNamespace(jenkins_job='polybot-sim-guava-a'),
        trading=SimpleNamespace(min_free_gib=100,max_disk_used_ratio=.85))
    return cfg,workspace,pin


def test_exact_volume_marker_and_no_db_creation(fixture):
    cfg,path,pin=fixture
    with pytest.raises(w.WorkspaceError,match='explicit first'):w.verify_workspace(cfg,path,environment={})
    assert w.verify_workspace(cfg,path,write_marker=True,environment={})['status']=='ok'
    assert not cfg.db_path.exists()
    assert w.verify_workspace(cfg,path,environment={})['status']=='ok'


def test_wrong_uuid_and_symlink_rejected(fixture):
    cfg,path,pin=fixture;pin.write_text('00000000-0000-0000-0000-000000000000')
    with pytest.raises(w.WorkspaceError,match='UUID'):w.verify_workspace(cfg,path,write_marker=True,environment={})
    assert not (path/w.MARKER).exists()


def test_missing_mount_never_creates_fallback(fixture,monkeypatch,tmp_path):
    cfg,path,pin=fixture;missing=tmp_path/'missing'
    monkeypatch.setattr(w,'MOUNT',missing)
    with pytest.raises(w.WorkspaceError):w.verify_workspace(cfg,path,write_marker=True,environment={})
    assert not missing.exists()


def test_wrong_marker_not_overwritten(fixture):
    cfg,path,pin=fixture;marker=path/w.MARKER;marker.write_text(json.dumps({'job':'different'}))
    with pytest.raises(w.WorkspaceError,match='another identity'):w.verify_workspace(cfg,path,write_marker=True,environment={})
    assert json.loads(marker.read_text())=={'job':'different'}


def test_low_space_and_job_mismatch(fixture,monkeypatch):
    cfg,path,pin=fixture
    with pytest.raises(w.WorkspaceError,match='job/runtime'):w.verify_workspace(cfg,path,write_marker=True,environment={'JOB_NAME':'polybot-yellow'})
    monkeypatch.setattr(w.shutil,'disk_usage',lambda path:SimpleNamespace(total=100,used=99,free=1))
    with pytest.raises(w.WorkspaceError,match='headroom'):w.verify_workspace(cfg,path,write_marker=True,environment={})
