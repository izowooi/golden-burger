#!/usr/bin/env python3
"""Separate accountless recorder entrypoint; never changes or runs v7."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,fcntl,json,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from polybot.recorder_config import load_config,RUNTIME,slot_start_utc
from polybot.recorder_store import RecorderStore
from polybot.recorder import Recorder
from polybot.recorder_workspace import verify_workspace

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('config','run','probe'))
    parser.add_argument('--job',default=RUNTIME);parser.add_argument('--simulate',action='store_true');parser.add_argument('--live',action='store_true')
    parser.add_argument('--output-dir');args=parser.parse_args()
    config=load_config(job_name=args.job,simulate=args.simulate,live=args.live)
    if args.command=='config':print(json.dumps(config.snapshot(),sort_keys=True));return
    if args.command=='run':
        verify_workspace(config);path=config.db_path
    else:
        if not args.output_dir:raise ValueError('probe requires a new explicit local output directory')
        directory=Path(args.output_dir).resolve();directory.mkdir(exist_ok=False,parents=True);path=directory/'trades_sim.db'
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.parent/'.coconut-cycle.lock'
    if lock.is_symlink():raise ValueError('unsafe writer lock')
    with lock.open('a') as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        now=datetime.now(timezone.utc);store=RecorderStore(path,slot_start_utc(now,config.slot_phase_seconds).date().isoformat(),runtime_job=config.job_name)
        try:
            result=Recorder(config,store).run(now,force_discovery=args.command=='probe',probe=args.command=='probe')
            if args.command=='probe':result['probe_only']=True
            print(json.dumps(result,sort_keys=True))
            if result['status']=='FAILED':raise SystemExit(1)
        finally:store.close()
if __name__=='__main__':main()
