"""Short public market-stream windows, explicitly not a complete trade tape."""
from datetime import datetime,timezone
import json
import time
from uuid import uuid4
from websockets.sync.client import connect

URL='wss://ws-subscriptions-clob.polymarket.com/ws/market'


def utc():return datetime.now(timezone.utc).isoformat()


def collect_market_window(tokens,budget,sink,*,seconds=5.0,maximum_messages=256):
    if not tokens:return {'status':'NO_TOKENS','messages':[],'complete_trade_tape':False}
    available=budget.require()
    if available<seconds+4:
        return {'status':'NOT_ATTEMPTED_BUDGET','messages':[],'complete_trade_tape':False}
    request_id=uuid4().hex;started=utc();end=time.monotonic()+seconds
    receipt={'request_id':request_id,'source':'clob_market_ws','method':'SUBSCRIBE',
        'path':'/ws/market','params':{'assets_ids':sorted(set(tokens)),'window_seconds':seconds},
        'started_at':started,'received_at':None,'status':'STARTED','error_type':None}
    messages=[];total=0;truncated=False
    try:
        with connect(URL,open_timeout=min(2,budget.require()),close_timeout=1,proxy=None,
                     max_size=2*1024*1024,compression=None) as socket:
            socket.send(json.dumps({'type':'market','assets_ids':sorted(set(tokens)),
                                    'custom_feature_enabled':True}))
            while time.monotonic()<end:
                remaining=min(budget.require(),end-time.monotonic())
                if remaining<=0:break
                try:raw=socket.recv(timeout=remaining)
                except TimeoutError:break
                if isinstance(raw,bytes):raw=raw.decode('utf-8')
                if raw in ('PING','PONG'):
                    if raw=='PING':socket.send('PONG')
                    continue
                total+=len(raw.encode())
                if total>2*1024*1024:truncated=True;break
                payload=json.loads(raw,parse_float=str)
                messages.append({'received_at':utc(),'payload':payload,'ordinal':len(messages)})
                if len(messages)>=maximum_messages:truncated=True;break
        receipt['status']='TRUNCATED' if truncated else 'OK'
    except Exception as error:
        receipt['status']='FAILED';receipt['error_type']=type(error).__name__
    receipt['received_at']=utc()
    sink(receipt,{'messages':messages,'window_start':started,'window_end':receipt['received_at'],
        'truncated':truncated,'complete_trade_tape':False,'queue_position_observed':False})
    return {'status':receipt['status'],'request_id':request_id,'messages':messages,
        'window_start':started,'window_end':receipt['received_at'],'truncated':truncated,
        'complete_trade_tape':False,'error_type':receipt['error_type']}
