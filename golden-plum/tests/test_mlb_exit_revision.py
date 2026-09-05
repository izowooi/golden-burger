from types import SimpleNamespace

import pytest

from polybot.config import load_config
from polybot.strategy.trader import Trader
from tests.test_config import _credentials
from tests.test_trader import _Repo, _Clob


def test_mlb_exit_revision_does_not_rewrite_soccer_or_old_trade_thresholds(monkeypatch):
    _credentials(monkeypatch)
    king=load_config('config.yaml','plum-live-king-90-1m-v1',simulation_mode=False)
    queen=load_config('config.yaml','plum-live-queen-95-1m-v1',simulation_mode=False)
    for cfg,tp in [(king,.90),(queen,.95)]:
        e=cfg.trading.entry
        assert (e.prob_min,e.prob_max,e.trend_observations,e.stop_loss_delta,e.take_profit_price)==(.75,.78,3,.15,tp)
        assert cfg.trading.drawdown_guard_enabled is True
    mlb=load_config('config.yaml','plum-live-king-mlb-90-1m-v1',simulation_mode=False)
    trader=Trader(_Repo(),_Clob(),mlb.trading,simulation_mode=False)
    prior=SimpleNamespace(condition_id='old',buy_confirmed_vwap=.55,
                          take_profit_price_at_buy=.90,stop_loss_delta_at_buy=.15)
    assert trader._exit_thresholds(prior)[:2]==pytest.approx((.90,.40))
    new=SimpleNamespace(condition_id='new',buy_confirmed_vwap=.55,
                        take_profit_price_at_buy=.65,stop_loss_delta_at_buy=.12)
    assert trader._exit_thresholds(new)[:2]==pytest.approx((.65,.43))
