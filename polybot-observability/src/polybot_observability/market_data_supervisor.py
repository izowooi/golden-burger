"""Start/check the shared writer from the already-authorized Jenkins/SSH context.

Some macOS GUI LaunchAgents cannot access removable volumes. This supervisor
keeps the DB on its explicit external root, detaches a credential-free child,
and reuses the owned process across builds. A probe timeout is never proof that
a live process stopped, and never triggers an automatic kill/restart.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from .market_data_client import StoreClient, validate_capability_names


def _daemon_environment():
    return {
        'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
        'PYTHONUNBUFFERED': '1',
        'LANG': 'en_US.UTF-8',
        'BUILD_ID': 'polybot-public-market-data-service',
        'JENKINS_NODE_COOKIE': 'polybot-public-market-data-service',
    }


def _owned_pid(metadata, db: Path, socket: Path):
    if not isinstance(metadata, dict) or not isinstance(metadata.get('pid'), int):
        return None
    pid=metadata['pid']
    if pid<=1:
        return None
    result=subprocess.run(['ps','-p',str(pid),'-o','uid=','-o','command='],capture_output=True,text=True,timeout=2)
    if result.returncode:
        return None
    parts=result.stdout.strip().split(maxsplit=1)
    if len(parts)!=2 or parts[0]!=str(os.getuid()):
        return None
    args=shlex.split(parts[1])
    for flag, expected in (('-m','polybot_observability.market_data_service'),('--db',str(db)),('--socket',str(socket))):
        try:
            if args[args.index(flag)+1]!=expected:
                return None
        except (ValueError,IndexError):
            return None
    return pid


def _read_metadata(path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16384:
        raise ValueError('invalid public service process metadata')
    return json.loads(path.read_text())


def _atomic_metadata(path, value):
    temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def _probe(client):
    # stats scans every stored payload; liveness must not grow with DB size.
    if client.get_many([]) != []:
        raise RuntimeError('invalid public service empty-read response')


def ensure_service(*,storage_root: Path,expected_volume_id: str,
                   startup_timeout: float=8.0,min_free_gib: float=50,
                   max_used_ratio: float=.90, required_capabilities=()):
    required_capabilities = validate_capability_names(required_capabilities)
    root=Path(storage_root).expanduser().absolute()
    if root.resolve(strict=True)!=root or not root.is_dir():
        raise ValueError('public service storage root must already exist without symlinks')
    if len(root.parts)>2 and root.parts[1]=='Volumes':
        volume=Path(*root.parts[:3])
        if not volume.is_mount() or root.stat().st_dev!=volume.stat().st_dev:
            raise ValueError('public service external volume is absent')
    marker=root/'.polybot-market-data-volume-id'
    if marker.is_symlink() or not marker.is_file() or marker.read_text().strip()!=expected_volume_id:
        raise ValueError('public service volume identity mismatch')
    if not 0<startup_timeout<=30 or min_free_gib<0 or not 0<max_used_ratio<=1:
        raise ValueError('public service supervision limits are invalid')
    db=root/'public.db';socket=root/'market-data.sock'
    metadata_path=root/'service-process.json'
    client=StoreClient(socket,timeout=1.0)
    deadline=time.monotonic()+startup_timeout
    lock=os.open(root/'supervisor.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:
                    raise TimeoutError('another public service supervisor is active')
                time.sleep(.05)
        existing=_read_metadata(metadata_path)
        pid=_owned_pid(existing,db,socket)
        if pid:
            # If this raises, keep the actual process intact. The next build can
            # retry the same process after a transient busy interval.
            _probe(client)
            result = {'status':'RUNNING','pid':pid,'probe':'empty_read'}
            if required_capabilities:
                result['capabilities'] = sorted(client.require_capabilities(required_capabilities))
            return result
        try:
            _probe(client)
        except (FileNotFoundError,ConnectionRefusedError):
            pass
        else:
            raise RuntimeError('healthy public socket has no owned process metadata')
        log_dir=root/'logs';log_dir.mkdir(mode=0o700,exist_ok=True)
        arguments=[sys.executable,'-m','polybot_observability.market_data_service',
                   '--db',str(db),'--socket',str(socket),'--storage-root',str(root),
                   '--expected-volume-id',expected_volume_id,'--min-free-gib',str(min_free_gib),
                   '--max-used-ratio',str(max_used_ratio)]
        with (log_dir/'service.stdout.log').open('ab') as stdout, (log_dir/'service.stderr.log').open('ab') as stderr:
            process=subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                                     close_fds=True,start_new_session=True,cwd=root,
                                     env=_daemon_environment())
        metadata={'pid':process.pid,'started_at':datetime.now(timezone.utc).isoformat(),
                  'arguments':arguments,'source_root':str(Path(__file__).resolve().parents[1])}
        _atomic_metadata(metadata_path,metadata)
        while time.monotonic()<deadline:
            if process.poll() is not None:
                raise RuntimeError('public service exited during startup: '+str(process.returncode))
            try:
                _probe(client)
                result = {'status':'STARTED','pid':process.pid,'probe':'empty_read'}
                if required_capabilities:
                    result['capabilities'] = sorted(client.require_capabilities(required_capabilities))
                return result
            except (FileNotFoundError,ConnectionRefusedError,TimeoutError):
                time.sleep(.05)
        raise TimeoutError('public service is still starting; owned process was preserved')
    finally:
        fcntl.flock(lock,fcntl.LOCK_UN);os.close(lock)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage-root',type=Path,required=True)
    parser.add_argument('--expected-volume-id',required=True)
    parser.add_argument('--startup-timeout',type=float,default=8.0)
    parser.add_argument('--min-free-gib',type=float,default=50)
    parser.add_argument('--max-used-ratio',type=float,default=.90)
    parser.add_argument('--require-capability',action='append',default=[],
                        help='Require a service contract before admitting this build; repeatable')
    args=parser.parse_args(argv)
    print(json.dumps(ensure_service(storage_root=args.storage_root,
                                    expected_volume_id=args.expected_volume_id,
                                    startup_timeout=args.startup_timeout,
                                    min_free_gib=args.min_free_gib,max_used_ratio=args.max_used_ratio,
                                    required_capabilities=args.require_capability)))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
