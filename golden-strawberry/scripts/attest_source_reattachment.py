#!/usr/bin/env python3
"""Explicit maintenance, not a periodic command; never changes a source DB."""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

from polybot.bot import exclusive_job_run_lock
from polybot.followup_config import load_followup_config
from polybot.source_reattachment import attest_device_reattachment


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--old-device',type=int,required=True)
    parser.add_argument('--expected-content-sha256',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    config=load_followup_config(simulation_mode=True,job_name='strawberry-shadow-one-followup-v2a')
    lock=config.db_path.parent/'.strawberry-followup-v2a.lock'
    with exclusive_job_run_lock(lock):
        with sqlite3.connect(f'file:{config.db_path}?mode=ro',uri=True) as connection:
            connection.row_factory=sqlite3.Row
            rows=connection.execute('SELECT * FROM source_anchors').fetchall()
            if len(rows)!=1:raise RuntimeError('exactly one original source anchor is required')
            anchor=dict(rows[0])
        result=attest_device_reattachment(config.trading.v1_source.db_path,anchor,
            old_device=args.old_device,expected_content_sha256=args.expected_content_sha256,apply=args.apply)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
