import json
import os
from pathlib import Path
import signal
import tempfile
import time

import pytest

from polybot_observability import market_data_supervisor as supervisor
from polybot_observability.market_data_client import StoreClient


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix='md-sup-',dir='/tmp') as directory:
        root=Path(directory).resolve()
        (root/'.polybot-market-data-volume-id').write_text('fixture-volume\n')
        yield root
        metadata=root/'service-process.json'
        if metadata.exists():
            pid=supervisor._owned_pid(json.loads(metadata.read_text()),root/'public.db',root/'market-data.sock')
            if pid:
                os.kill(pid,signal.SIGTERM)
                for _ in range(50):
                    try:
                        exited,_=os.waitpid(pid,os.WNOHANG)
                    except ChildProcessError:
                        break
                    if exited:break
                    time.sleep(.02)


def ensure(root):
    return supervisor.ensure_service(storage_root=root,expected_volume_id='fixture-volume',min_free_gib=0,max_used_ratio=1)


def test_reuses_owned_service_and_recovers_only_after_actual_process_exit(root):
    first=ensure(root)
    assert first['status']=='STARTED'
    client=StoreClient(root/'market-data.sock')
    sha=client.put_many([b'public quote fixture'])[0]
    second=ensure(root)
    assert second['status']=='RUNNING' and second['pid']==first['pid']
    os.kill(first['pid'],signal.SIGKILL)
    os.waitpid(first['pid'],0)
    third=ensure(root)
    assert third['pid']!=first['pid']
    assert client.get_many([sha])==[b'public quote fixture']


def test_probe_timeout_does_not_restart_live_process(root,monkeypatch):
    first=ensure(root)
    def timeout(self):raise TimeoutError('temporary busy writer')
    monkeypatch.setattr(StoreClient,'stats',timeout)
    with pytest.raises(TimeoutError,match='busy writer'):
        ensure(root)
    assert json.loads((root/'service-process.json').read_text())['pid']==first['pid']
    assert supervisor._owned_pid({'pid':first['pid']},root/'public.db',root/'market-data.sock')==first['pid']


def test_daemon_does_not_inherit_credentials_or_jenkins_build_identity(monkeypatch):
    monkeypatch.setenv('POLYMARKET_PRIVATE_KEY','fixture-secret')
    monkeypatch.setenv('BUILD_ID','original-build')
    monkeypatch.setenv('JENKINS_NODE_COOKIE','original-node')
    env=supervisor._daemon_environment()
    assert 'POLYMARKET_PRIVATE_KEY' not in env
    assert env['BUILD_ID']!='original-build' and env['JENKINS_NODE_COOKIE']!='original-node'


def test_missing_root_is_not_created(tmp_path):
    root=tmp_path/'absent'
    with pytest.raises(FileNotFoundError):ensure(root)
    assert not root.exists()
