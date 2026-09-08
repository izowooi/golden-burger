#!/usr/bin/env python3
"""Export verified recorder rows; no network or source writes."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from polybot.recorder_export import iter_rows,iter_terminals

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--output',required=True);p.add_argument('--start');p.add_argument('--end');p.add_argument('--include-depth',action='store_true')
    args=p.parse_args();source=Path(args.db).resolve();output=Path(args.output).resolve()
    if output==source:raise ValueError('output cannot overwrite source')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:
        for row in iter_rows(source,args.sha256,start=args.start,end=args.end,include_depth=args.include_depth):f.write(json.dumps(row,ensure_ascii=False)+'\n')
    terminal_path=output.with_suffix(output.suffix+'.terminals.json')
    with terminal_path.open('x',encoding='utf-8') as f:json.dump(list(iter_terminals(source,args.sha256,end=args.end)),f,ensure_ascii=False)
if __name__=='__main__':main()
