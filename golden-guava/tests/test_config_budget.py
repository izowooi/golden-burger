from dataclasses import replace
import pytest
from polybot.config import TradingConfig,load_config,validate,CREDENTIAL_KEYS
from polybot.budget import Budget,BudgetExceeded


def test_default_isolated_research():
    config=load_config(environment={})
    assert config.simulation_mode and config.db_path.name=='trades_sim.db'
    assert config.spec.shard==0 and config.spec.jenkins_job=='polybot-sim-guava-a'
    assert config.trading.minimum_scalable_notional_usdc==100
    assert not any(k in str(config.public_snapshot()) for k in CREDENTIAL_KEYS)


@pytest.mark.parametrize('key',CREDENTIAL_KEYS)
def test_research_rejects_even_empty_credentials_before_network(key):
    with pytest.raises(ValueError,match='credentials forbidden'):load_config(environment={key:''})


def test_lifecycle_and_job_mode_are_bound():
    with pytest.raises(ValueError):load_config(environment={'POLYBOT_LIFECYCLE_MODE':'active'})
    with pytest.raises(ValueError):load_config(job_name='guava-live-lion-a-v1',mode='sim',environment={})
    assert load_config(job_name='guava-live-lion-a-v1',mode='live',environment={}).trading.lifecycle_mode=='active'


@pytest.mark.parametrize('change',[{'min_liquidity':float('nan')},{'cycle_budget_seconds':60},
    {'page_size':True},{'max_positions':1.5},{'buy_amount_usdc':100,'max_buy_amount_usdc':5},
    {'depth_ladder_usdc':[100,5]},{'fee_stress_rates':[0,True]},{'study_end_utc':'2020-01-01T00:00:00Z'},
    {'study_end_utc':'2030-01-01T00:00:00'},{'sport_families':['soccer','esports']}])
def test_invalid_config(change):
    with pytest.raises((ValueError,TypeError)):validate(replace(TradingConfig(),**change))


def test_budget_reserves_cleanup_without_process_kill():
    clock=[0.0];budget=Budget(45,margin=7,monotonic=lambda:clock[0])
    assert budget.require()==38
    clock[0]=38
    with pytest.raises(BudgetExceeded):budget.require()
    assert budget.require_commit()==7
    clock[0]=45
    with pytest.raises(BudgetExceeded):budget.require_commit()
