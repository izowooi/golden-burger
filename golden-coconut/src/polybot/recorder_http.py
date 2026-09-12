"""Only public Gamma/book endpoints; raw response capture with bounded body size."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import gzip,hashlib,json,math,threading
from typing import Mapping
import requests
from .api.transport import PublicJsonTransport,PublicApiError,DeadlineExceeded
from .api.gamma_client import GammaClient,GammaFamilyPool
from .api.sports_client import SportsClockClient,ClockTarget
from .config import GammaConfig,SportsFeedConfig

MAX_BYTES=32*1024*1024
MAX_JSON_NODES=1_000_000
class RawTee:
    def __init__(self,raw,owner):self.raw,self.owner=raw,owner
    @property
    def decode_content(self):return self.raw.decode_content
    @decode_content.setter
    def decode_content(self,value):self.raw.decode_content=value
    def capture(self,chunk):
        remaining=MAX_BYTES-len(self.owner.body)
        self.owner.body.extend(chunk[:max(0,remaining)])
        if len(chunk)>remaining:self.owner.truncated=True;raise requests.RequestException('response body limit')
        return chunk
    def read1(self,*args,**kwargs):
        return self.capture(getattr(self.raw,'read1',self.raw.read)(*args,**kwargs))
    def stream(self,*args,**kwargs):
        for chunk in self.raw.stream(*args,**kwargs):yield self.capture(chunk)
    def __getattr__(self,key):return getattr(self.raw,key)
class CaptureSession(requests.Session):
    def __init__(self):super().__init__();self.body=bytearray();self.truncated=False
    def request(self,*args,**kwargs):
        self.body=bytearray();self.truncated=False
        response=super().request(*args,**kwargs);response.raw=RawTee(response.raw,self);return response

def strict_json(raw):
    def bad(_):raise ValueError('nonfinite JSON')
    obj=json.loads(raw.decode('utf-8'),parse_constant=bad);stack=[(obj,0)];nodes=0
    while stack:
        value,depth=stack.pop();nodes+=1
        if depth>30 or nodes>MAX_JSON_NODES:raise ValueError('JSON structural limit')
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('nonfinite scalar')
        if isinstance(value,dict):stack.extend((v,depth+1) for v in value.values())
        elif isinstance(value,list):stack.extend((v,depth+1) for v in value)
    return obj

class RecorderTransport(PublicJsonTransport):
    def request_json(self,method,url,**kwargs):
        if getattr(self,'access_denied',None) is not None and self.access_denied.is_set():raise PublicApiError('service access denied in this cycle')
        allowed=(url=='https://gamma-api.polymarket.com/events/keyset' or
                 url=='https://gamma-api.polymarket.com/events' or
                 (url.startswith('https://gamma-api.polymarket.com/events/') and url.rsplit('/',1)[1].isdigit()) or
                 url=='https://clob.polymarket.com/books')
        if not allowed or (method.upper()=='POST')!=(url=='https://clob.polymarket.com/books'):
            raise ValueError('recorder endpoint/method not allowed')
        response=super().request_json(method,url,**kwargs)
        try:strict_json(response.raw)
        except ValueError as error:raise PublicApiError(str(error),request_id=response.request_id) from error
        return response

class RecorderClient:
    def __init__(self,config,registry,store,budget):
        if config.max_response_bytes!=MAX_BYTES:raise ValueError('response cap config mismatch')
        self.config,self.registry,self.store,self.budget=config,registry,store,budget
        self.receipts={};self.responses={};self.lock=threading.Lock();self.transports={};self.access_denied=threading.Event()
        for family in registry.by_code:
            session=CaptureSession()
            def sink(row,session=session):self.record_receipt(row,bytes(session.body),not session.truncated)
            self.transports[family]=RecorderTransport(connect_timeout_seconds=3,read_timeout_seconds=5,
                attempt_wall_seconds=10,max_retries=2,retry_base_seconds=.25,retry_max_seconds=1,
                receipt_sink=sink,session=session)
            self.transports[family].access_denied=self.access_denied
        gc=GammaConfig('https://gamma-api.polymarket.com','/events/keyset','/events/{event_id}',config.gamma_page_size,
            config.max_pages_per_family,False,False,24,48,5,3,5,10,0,.25,1)
        self.gamma=GammaFamilyPool({f:GammaClient(gc,t) for f,t in self.transports.items()},max_workers=5)

    def record_receipt(self,row,body=b'',complete=False):
        if row.get('http_status') in (403,451):self.access_denied.set()
        request=row.get('logical_request_id') or row.get('request_id')
        ident=row.get('api_attempt_id') or request
        if not request or not ident:raise ValueError('receipt identity absent')
        raw_sha=hashlib.sha256(body).hexdigest() if body else None
        if row.get('status')=='SUCCESS' and body and row.get('response_sha256') not in (None,raw_sha):raise ValueError('capture hash mismatch')
        record={'attempt_id':ident,'run_id':row['run_id'],'request_id':request,
            'request_kind':row.get('request_kind','sports_clock'),'started_at':row.get('started_at') or row.get('observed_at'),
            'received_at':row.get('completed_at') or row.get('observed_at'),'status':row.get('status','UNKNOWN'),
            'http_status':row.get('http_status'),'sha256':raw_sha,'raw_gzip':gzip.compress(body,mtime=0) if body else None,
            'raw_complete':int(complete and row.get('status')=='SUCCESS'),'receipt_json':json.dumps(row,sort_keys=True)}
        with self.lock:self.receipts[request]=record
        with self.store.transaction() as c:self.store.insert(c,'requests',record)

    def discovery(self,run_id,slot):
        # Family sessions/threads remain isolated. Successful response bodies are
        # already durable even if a different family raises or exhausts budget.
        def fetch(family):
            try:return family.code,self.gamma.clients[family.code].fetch_family_events(run_id,family,budget=self.budget,slot_start=slot),None
            except Exception as error:return family.code,None,type(error).__name__
        with ThreadPoolExecutor(max_workers=5) as pool:return list(pool.map(fetch,self.registry.families))

    def event(self,run_id,event_id,family):
        if not str(event_id).isdigit():raise ValueError('event ID must be numeric')
        return self.gamma.fetch_event(run_id,str(event_id),family,budget=self.budget)

    def clocks(self,run_id,events):
        if self.access_denied.is_set():raise PublicApiError('service access denied in this cycle')
        targets={str(e['slug']).lower():ClockTarget(str(e['slug']).lower(),str(e['id']),
                 (str(e.get('gameId') or e.get('game_id')),) if e.get('gameId') or e.get('game_id') else ()) for e in events if e.get('slug')}
        client=SportsClockClient(SportsFeedConfig('wss://sports-api.polymarket.com/ws',3,2,10000),lambda row:self.record_receipt(row))
        return client.collect(run_id,targets,budget=self.budget)

    def books(self,run_id,tokens,*,groups=None):
        result={};transport=self.transports['soccer'];batches=[];chunk=[]
        groups=groups or [[token] for token in tokens]
        if [t for group in groups for t in group]!=tokens:raise ValueError('book group coverage mismatch')
        for group in groups:
            if len(group)>self.config.book_batch_limit:raise ValueError('event exceeds book batch')
            if chunk and len(chunk)+len(group)>self.config.book_batch_limit:batches.append(chunk);chunk=[]
            chunk.extend(group)
        if chunk:batches.append(chunk)
        for chunk in batches:
            try:
                response=transport.request_json('POST','https://clob.polymarket.com/books',request_kind='recorder_books',
                    run_id=run_id,budget=self.budget,json_body=[{'token_id':t} for t in chunk])
                payload=strict_json(response.raw)
                returned=[str(x.get('asset_id') or '') for x in payload if isinstance(x,dict)] if isinstance(payload,list) else []
                if not isinstance(payload,list) or len(returned)!=len(payload) or len(set(returned))!=len(returned) or not set(returned)<=set(chunk):
                    raise PublicApiError('malformed/duplicate/extra book assets',request_id=response.request_id)
                by_token=dict(zip(returned,payload))
                for token in chunk:result[token]={'status':'RECEIVED' if token in by_token else 'MISSING','raw':by_token.get(token),'request_id':response.request_id,'received_at':response.received_at}
            except Exception as error:
                for token in chunk:result[token]={'status':'NOT_ATTEMPTED' if isinstance(error,DeadlineExceeded) else 'ERROR','raw':None,'request_id':getattr(error,'request_id',None),'received_at':None,'reason':type(error).__name__}
        return result

    def close(self):
        for transport in self.transports.values():transport.session.close()
