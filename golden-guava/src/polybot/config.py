"""Strict, credential-free public config and explicit job/mode identities."""
from __future__ import annotations
from dataclasses import asdict,dataclass,field
from datetime import datetime
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Mapping
import yaml
from polybot_observability.config_contract import get_trading_config_mapping,validate_yaml_config_shape
from .source_digest import ROOT,compute_strategy_source_digest

STRATEGY_NAME='golden-guava'
RELEASE_STAGE='research_only'
RESEARCH_CONTRACT='guava-research-v1'
CREDENTIAL_KEYS=('POLYMARKET_PRIVATE_KEY','POLYMARKET_FUNDER_ADDRESS','POLYMARKET_SIGNATURE_TYPE',
    'POLYMARKET_API_KEY','POLYMARKET_API_SECRET','POLYMARKET_API_PASSPHRASE','CLOB_API_KEY','CLOB_SECRET','CLOB_PASSPHRASE')
FAMILIES=('soccer','mlb','nba','nfl','nhl')


@dataclass(frozen=True)
class RuntimeSpec:
    job_name:str
    jenkins_job:str
    mode:str
    shard:int|None
    arm:str|None=None

RUNTIMES={f'guava-research-{letter}-v1':RuntimeSpec(f'guava-research-{letter}-v1',f'polybot-sim-guava-{letter}','sim',index)
          for index,letter in enumerate('abcd')}
RUNTIMES.update({f'guava-live-{job}-{arm}-v1':RuntimeSpec(f'guava-live-{job}-{arm}-v1',f'polybot-{job}','live',None,arm)
                for job,arm in (('lion','a'),('wolf','b'))})


@dataclass(frozen=True)
class TradingConfig:
    lifecycle_mode:str='archive_only'
    cadence_seconds:int=60
    slot_phase_seconds:int=0
    cycle_budget_seconds:float=45
    network_stop_margin_seconds:float=7
    sport_families:list[str]=field(default_factory=lambda:list(FAMILIES))
    min_liquidity:float=5000
    min_volume:float=5000
    page_size:int=500
    max_pages_per_family:int=4
    book_batch_limit:int=240
    max_tokens_per_cycle:int=500
    followup_batch_limit:int=20
    min_free_gib:float=100
    max_disk_used_ratio:float=.85
    buy_amount_usdc:float=5
    max_buy_amount_usdc:float=5
    max_open_notional_usdc:float=100
    max_positions:int=20
    max_new_positions_per_cycle:int=1
    loss_limit_usdc:float=10
    minimum_scalable_notional_usdc:float=100
    depth_ladder_usdc:list[float]=field(default_factory=lambda:[5,10,15,20,25,30,50,75,100])
    fee_stress_rates:list[float]=field(default_factory=lambda:[0,.03,.05])
    study_start_utc:str='2026-09-06T01:17:54Z'
    study_end_utc:str='2026-10-06T01:17:54Z'


@dataclass(frozen=True)
class Config:
    trading:TradingConfig
    spec:RuntimeSpec
    config_hash:str
    strategy_source_digest:str
    root:Path

    @property
    def job_name(self):return self.spec.job_name
    @property
    def simulation_mode(self):return self.spec.mode=='sim'
    @property
    def db_path(self):return self.root/'data'/self.job_name/('trades_sim.db' if self.simulation_mode else 'trades.db')
    @property
    def expected_workspace(self):return Path('/Volumes/t7/jenkins')/self.spec.jenkins_job
    @property
    def cohort_key(self):return self.config_hash+':'+self.strategy_source_digest+':'+self.spec.mode+':'+self.job_name

    def public_snapshot(self):
        snapshot={'strategy_name':STRATEGY_NAME,'job_name':self.job_name,'jenkins_job':self.spec.jenkins_job,
            'mode':self.spec.mode,'simulation_mode':self.simulation_mode,
            'release_stage':RELEASE_STAGE,
            'data_contract':RESEARCH_CONTRACT if self.simulation_mode else 'guava-live-v1',
            'config_hash':self.config_hash,'strategy_source_digest':self.strategy_source_digest,
            'trading':asdict(self.trading),'shard_index':self.spec.shard,'shard_count':4,'arm':self.spec.arm}
        if self.simulation_mode:
            snapshot['slot_claim_policy']={'cadence_seconds':self.trading.cadence_seconds,
                'phase_seconds':self.trading.slot_phase_seconds,'window':'UTC_HALF_OPEN',
                'actual_timestamps_preserved':True}
        return snapshot


def _finite(value,label,minimum=0,strict=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(label+' must be a finite number')
    if value<minimum or (strict and value==minimum):raise ValueError(label+' outside bounds')


def _utc(value):
    if not isinstance(value,str):raise ValueError('UTC timestamp must be a string')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset().total_seconds()!=0:raise ValueError('explicit UTC required')
    return result


def validate(trading):
    if type(trading.slot_phase_seconds) is not int or not 0<=trading.slot_phase_seconds<60:
        raise ValueError('slot_phase_seconds must be an integer in 0..59')
    ints=('cadence_seconds','page_size','max_pages_per_family','book_batch_limit','max_tokens_per_cycle',
          'followup_batch_limit','max_positions','max_new_positions_per_cycle')
    for name in ints:
        value=getattr(trading,name)
        if isinstance(value,bool) or not isinstance(value,int) or value<=0:raise ValueError(name+' must be positive integer')
    for name in ('cycle_budget_seconds','network_stop_margin_seconds','min_free_gib','buy_amount_usdc',
                 'max_buy_amount_usdc','max_open_notional_usdc','loss_limit_usdc','minimum_scalable_notional_usdc'):
        _finite(getattr(trading,name),name,strict=True)
    for name in ('min_liquidity','min_volume','max_disk_used_ratio'):_finite(getattr(trading,name),name)
    if not 0<trading.network_stop_margin_seconds<trading.cycle_budget_seconds<=55:raise ValueError('invalid cycle budget')
    if trading.cadence_seconds!=60 or trading.page_size>500 or trading.book_batch_limit>250:raise ValueError('unsupported source/cadence envelope')
    if not 0<trading.max_disk_used_ratio<1:raise ValueError('invalid disk ratio')
    if not 5<=trading.buy_amount_usdc<=trading.max_buy_amount_usdc<=100:raise ValueError('invalid order amount envelope')
    if trading.max_open_notional_usdc<trading.buy_amount_usdc:raise ValueError('order exceeds open notional cap')
    if trading.max_new_positions_per_cycle>trading.max_positions:raise ValueError('cycle count exceeds position cap')
    if trading.lifecycle_mode not in ('active','close_only','archive_only'):raise ValueError('invalid lifecycle')
    if not isinstance(trading.sport_families,list) or not trading.sport_families or len(set(trading.sport_families))!=len(trading.sport_families) or any(x not in FAMILIES for x in trading.sport_families):
        raise ValueError('unsupported or duplicated sports')
    for name in ('depth_ladder_usdc','fee_stress_rates'):
        values=getattr(trading,name)
        if not isinstance(values,list) or not values:raise ValueError('nonempty numeric list required')
        for value in values:_finite(value,name)
        if values!=sorted(set(values)):raise ValueError('ladder must be sorted and unique')
    if min(trading.depth_ladder_usdc)<5 or max(trading.depth_ladder_usdc)>100 or 100 not in trading.depth_ladder_usdc:
        raise ValueError('depth study must include 100 and remain in 5..100')
    if max(trading.fee_stress_rates)>1:raise ValueError('invalid fee stress')
    if _utc(trading.study_start_utc)>=_utc(trading.study_end_utc):raise ValueError('empty study window')


def load_config(path=None,job_name='guava-research-a-v1',*,mode=None,environment=None,root=ROOT):
    env=os.environ if environment is None else environment
    if job_name not in RUNTIMES:raise ValueError('unknown Guava runtime')
    spec=RUNTIMES[job_name]
    if mode is not None and mode!=spec.mode:raise ValueError('job/mode mismatch; never reuse a live DB for research')
    if spec.mode=='sim' and any(key in env for key in CREDENTIAL_KEYS):raise ValueError('research credentials forbidden, including empty values')
    with Path(path or root/'config.yaml').open() as handle:payload=yaml.safe_load(handle)
    raw=dict(get_trading_config_mapping(payload))
    if 'simulation_mode' in payload and not isinstance(payload['simulation_mode'],bool):raise ValueError('simulation_mode must be boolean')
    if 'POLYBOT_LIFECYCLE_MODE' in env:raw['lifecycle_mode']=env['POLYBOT_LIFECYCLE_MODE']
    elif spec.mode=='live':raw['lifecycle_mode']='active'
    if 'POLYBOT_SLOT_PHASE_SECONDS' in env:
        phase=env['POLYBOT_SLOT_PHASE_SECONDS']
        if spec.mode!='sim' or not isinstance(phase,str) or not re.fullmatch(r'0|[1-5]?[0-9]',phase):
            raise ValueError('slot phase override requires research mode and integer 0..59')
        raw['slot_phase_seconds']=int(phase)
    trading=TradingConfig(**raw);validate_yaml_config_shape(payload,trading);validate(trading)
    if spec.mode!='sim' and trading.slot_phase_seconds!=0:raise ValueError('slot phase is research-only')
    if spec.mode=='sim' and trading.lifecycle_mode!='archive_only':raise ValueError('research requires archive_only')
    digest=compute_strategy_source_digest(root)
    public={'strategy_name':STRATEGY_NAME,'spec':asdict(spec),'trading':asdict(trading),'strategy_source_digest':digest}
    config_hash=hashlib.sha256(json.dumps(public,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return Config(trading,spec,config_hash,digest,Path(root))
