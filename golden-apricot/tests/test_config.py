from pathlib import Path
import pytest
from polybot.config import load_config

def credentials(monkeypatch):
 monkeypatch.setenv('POLYMARKET_PRIVATE_KEY','0x'+'1'*64);monkeypatch.setenv('POLYMARKET_FUNDER_ADDRESS','0x'+'2'*40)

@pytest.mark.parametrize(('job','policy'),[
 ('apricot-live-eco-mlb-tick90-tp95-v2','absolute_tp_or_resolution'),
 ('apricot-live-fruit-mlb-tick90-tp95-v2','absolute_tp_or_resolution')])
def test_tick90_jobs(monkeypatch,job,policy):
 credentials(monkeypatch);cfg=load_config('config.yaml',job,simulation_mode=False);t=cfg.trading
 assert t.sport_family=='mlb' and t.book_shape=='direct-two-team-moneyline'
 assert t.expected_result_kinds==('HOME','AWAY') and t.expected_token_count==2
 assert t.entry.exit_basis==policy and t.entry.max_source_minute==92
 assert t.entry.entry_tick_minute==90
 assert (t.entry.prob_min,t.entry.prob_max)==(.90,.999)
 assert t.buy_amount_usdc==15.0 and t.experiment_capital_usdc==100.0
 assert t.max_positions==6 and t.max_event_positions==1
 assert t.drawdown_loss_limit_usdc==300.0
 assert t.entry.take_profit_delta==0.90
 assert t.experiment_entry_end_utc=="9999-12-31T23:59:59Z"
 assert t.experiment_followup_end_utc=="9999-12-31T23:59:59Z"
 assert cfg.db_path==Path(f'data/{job}/trades.db')

def test_tick90_mlb_fifteen_dollar_notional_is_job_frozen(monkeypatch):
 credentials(monkeypatch)
 monkeypatch.setenv('POLYBOT_BUY_AMOUNT','5')
 with pytest.raises(ValueError,match='MLB target notional'):
  load_config('config.yaml','apricot-live-eco-mlb-tick90-tp95-v2',simulation_mode=False)

def test_tick90_absolute_drawdown_limit_is_job_frozen(monkeypatch):
 credentials(monkeypatch)
 monkeypatch.setenv('POLYBOT_DRAWDOWN_LOSS_LIMIT_USDC','299')
 with pytest.raises(ValueError,match='economic-loss guard'):
  load_config('config.yaml','apricot-live-eco-mlb-tick90-tp95-v2',simulation_mode=False)

def test_only_registered_jobs_and_live_mode(monkeypatch):
 credentials(monkeypatch)
 with pytest.raises(ValueError,match='unsupported'):
  load_config('config.yaml','apricot-shadow-1m-v1',simulation_mode=False)
 for key in ('POLYMARKET_PRIVATE_KEY','POLYMARKET_FUNDER_ADDRESS'):
  monkeypatch.delenv(key)
 with pytest.raises(ValueError,match='frozen to live'):
  load_config('config.yaml','apricot-live-eco-mlb-tick90-tp95-v2',simulation_mode=True)
