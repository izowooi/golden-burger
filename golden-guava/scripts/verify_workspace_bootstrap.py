#!/usr/bin/env python3
"""Standard-library-only external-volume check before uv/network/database work."""
import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--workspace',type=Path,required=True)
p.add_argument('--write-marker',action='store_true');a=p.parse_args()
jobs={'polybot-sim-guava-'+x:('guava-research-'+x+'-v1',True) for x in 'abcd'}
jobs.update({'polybot-lion':('guava-live-lion-a-v1',False),'polybot-wolf':('guava-live-wolf-b-v1',False)})
if a.job not in jobs:raise ValueError('unsupported exact Guava job')
runtime,sim=jobs[a.job];root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('guava_workspace_bootstrap',root/'src/polybot/workspace.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
cfg=SimpleNamespace(root=root,expected_workspace=Path('/Volumes/t7/jenkins')/a.job,
    db_path=root/'data'/runtime/('trades_sim.db' if sim else 'trades.db'),job_name=runtime,
    simulation_mode=sim,spec=SimpleNamespace(jenkins_job=a.job),
    trading=SimpleNamespace(min_free_gib=100,max_disk_used_ratio=.85))
print(json.dumps(module.verify_workspace(cfg,a.workspace,write_marker=a.write_marker)))
