"""Explicit Guava research/live routes; live implementation is not yet released."""
import argparse
import json
import logging
import os
import time
from .config import load_config


def main():
    parser=argparse.ArgumentParser(prog='polybot')
    parser.add_argument('command',choices=['config','run','status','verify-workspace'])
    parser.add_argument('--job',default='guava-research-a-v1')
    group=parser.add_mutually_exclusive_group();group.add_argument('--simulate',action='store_true');group.add_argument('--live',action='store_true')
    parser.add_argument('--write-marker',action='store_true')
    args=parser.parse_args()
    mode='live' if args.live else 'sim' if args.simulate else None
    config=load_config(job_name=args.job,mode=mode)
    if args.command=='config':result=config.public_snapshot()
    elif args.command=='verify-workspace':
        from .workspace import verify_workspace
        result=verify_workspace(config,os.environ.get('WORKSPACE',str(config.expected_workspace)),write_marker=args.write_marker)
    elif args.command=='status':
        if not config.simulation_mode:raise RuntimeError('live status implementation not yet released')
        from .evidence import Repository
        repo=Repository(config.db_path,config.public_snapshot(),read_only=True)
        try:result=repo.status()
        finally:repo.close()
    else:
        if not config.simulation_mode:raise RuntimeError('live hypothesis/adapter not yet released; do not deploy this route')
        from .runtime import run_research
        handler=logging.StreamHandler();formatter=logging.Formatter('%(asctime)sZ %(levelname)s %(message)s');formatter.converter=time.gmtime
        handler.setFormatter(formatter);logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
        result=run_research(config)
    print(json.dumps(result,sort_keys=True,ensure_ascii=False))


if __name__=='__main__':main()
