from pathlib import Path
import pytest
from polybot.config import load_config

def credentials(monkeypatch):
 monkeypatch.setenv('POLYMARKET_PRIVATE_KEY','0x'+'1'*64);monkeypatch.setenv('POLYMARKET_FUNDER_ADDRESS','0x'+'2'*40)

@pytest.mark.parametrize(('job','policy'),[
 ('apricot-live-eco-mlb-tick50-hold-v1','resolution_hold'),
 ('apricot-live-fruit-mlb-tick50-tp99-v1','tp99_or_resolution')])
def test_tick50_jobs(monkeypatch,job,policy):
 credentials(monkeypatch);cfg=load_config('config.yaml',job,simulation_mode=False);t=cfg.trading
 assert t.sport_family=='mlb' and t.book_shape=='direct-two-team-moneyline'
 assert t.expected_result_kinds==('HOME','AWAY') and t.expected_token_count==2
 assert t.entry.exit_basis==policy and t.entry.max_source_minute==52
 assert t.entry.entry_tick_minute==50
 assert (t.entry.prob_min,t.entry.prob_max)==(.01,.999)
 assert t.buy_amount_usdc==10.0 and t.experiment_capital_usdc==100.0
 assert cfg.db_path==Path(f'data/{job}/trades.db')

def test_tick50_mlb_notional_is_job_frozen(monkeypatch):
 credentials(monkeypatch)
 monkeypatch.setenv('POLYBOT_BUY_AMOUNT','5')
 with pytest.raises(ValueError,match='MLB target notional'):
  load_config('config.yaml','apricot-live-eco-mlb-tick50-hold-v1',simulation_mode=False)

def test_only_registered_jobs_and_live_mode(monkeypatch):
 credentials(monkeypatch)
 with pytest.raises(ValueError,match='unsupported'):
  load_config('config.yaml','apricot-shadow-1m-v1',simulation_mode=False)
 for key in ('POLYMARKET_PRIVATE_KEY','POLYMARKET_FUNDER_ADDRESS'):
  monkeypatch.delenv(key)
 with pytest.raises(ValueError,match='frozen to live'):
  load_config('config.yaml','apricot-live-eco-mlb-tick50-hold-v1',simulation_mode=True)
