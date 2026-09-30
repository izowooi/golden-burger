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
    def timeout(self, hashes):raise TimeoutError('temporary busy writer')
    monkeypatch.setattr(StoreClient,'get_many',timeout)
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


def test_health_probe_never_scans_payload_statistics(root, monkeypatch):
    def expensive_stats(self):
        raise AssertionError('full DB statistics must not be a health probe')
    monkeypatch.setattr(StoreClient, 'stats', expensive_stats)
    first = ensure(root)
    second = ensure(root)
    assert first['probe'] == second['probe'] == 'empty_read'
    assert first['pid'] == second['pid']


@pytest.mark.parametrize('startup,expected', [(8.0,5.0),(2.0,2.0)])
def test_health_probe_budget_allows_writer_queue_without_exceeding_startup(root,monkeypatch,startup,expected):
    seen=[]
    class Client:
        def __init__(self,socket,*,timeout):
            seen.append(timeout)
        def get_many(self,hashes):
            assert hashes == []
            return []
    monkeypatch.setattr(supervisor,'StoreClient',Client)
    monkeypatch.setattr(supervisor,'_owned_pid',lambda *args:98765)
    result=supervisor.ensure_service(storage_root=root,expected_volume_id='fixture-volume',
        startup_timeout=startup,min_free_gib=0,max_used_ratio=1)
    assert result == {'status':'RUNNING','pid':98765,'probe':'empty_read'}
    assert seen == [expected]


def test_owned_service_can_reply_after_one_second_of_queue_wait(root,monkeypatch):
    import socket
    from concurrent.futures import ThreadPoolExecutor
    from polybot_observability.market_data_client import _receive_frame, _send_frame, PROTOCOL_VERSION

    monkeypatch.setattr(supervisor,'_owned_pid',lambda *args:98765)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
        listener.bind(str(root/'market-data.sock'))
        listener.listen(1)
        listener.settimeout(5)
        def serve():
            connection,_=listener.accept()
            with connection:
                request=_receive_frame(connection,time.monotonic()+5)
                assert request['op']=='get_many' and request['hashes']==[]
                time.sleep(1.1)
                _send_frame(connection,{'v':PROTOCOL_VERSION,'ok':True,'result':[]},time.monotonic()+5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(serve)
            result=ensure(root)
            future.result(timeout=5)
    assert result['status']=='RUNNING' and result['pid']==98765
