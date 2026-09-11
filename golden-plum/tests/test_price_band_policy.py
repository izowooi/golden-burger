from types import SimpleNamespace
from datetime import datetime
from dataclasses import replace

import pytest
from sqlalchemy import text

from polybot.config import TradingConfig, load_config
from polybot.strategy.scanner import evaluate_trend_confirmation
from polybot.strategy.trader import Trader, _orphan_episode_contract_matches
from .test_scanner import _scanner, _triad, _walk, _walks, NOW
from .test_trader import _Repo, _Clob, _candidate, _set_kickoff_cycle, _FixedDatetime
from polybot.strategy import trader as trader_module
from .test_config import _credentials


def price_config():
    config = TradingConfig()
    return replace(config, entry=replace(config.entry, trend_observations=1,
                   trend_min_cumulative_move=0, trend_max_pullback=0))


def test_first_quote_does_not_require_history_rise_or_crossing():
    config = price_config()
    row = SimpleNamespace(id=1, probability=.77, timestamp=datetime(2026,9,6), source_elapsed_minutes=9)
    signal, reason = evaluate_trend_confirmation([row], current_snapshot_id=1, config=config)
    assert reason == 'price_band_confirmed'
    assert signal.snapshot_ids == (1,) and signal.elapsed_seconds == 0
    row.probability = .79
    assert evaluate_trend_confirmation([row], current_snapshot_id=1, config=config)[0] is None


def test_scanner_does_not_buy_a_non_leader_merely_because_it_is_in_band(tmp_path):
    markets = _triad()
    walks = _walks(.95)
    walks['yes-HOME'] = _walk('yes-HOME', .61)
    session, repo, scanner, gamma, clob = _scanner(tmp_path, markets, walks)
    scanner.config = replace(scanner.config, entry=replace(price_config().entry,
                            prob_min=.60, prob_max=.63))
    gamma.set_sweep(1, NOW)
    scanner.save_market_snapshots(markets, now=NOW)
    candidates = scanner.scan_buy_candidates(markets, now=NOW)
    assert candidates == []
    # Existing run index bounds the UPDATE; no full historical book scan.
    plan = session.execute(text("EXPLAIN QUERY PLAN UPDATE market_snapshots SET event_set_complete=1 WHERE event_cycle_id='fixture' AND run_id='fixture'")).all()
    assert any('INDEX' in str(row) and 'run' in str(row) for row in plan)
    session.close()


def test_scanner_accepts_the_unique_current_leader_with_one_observation(tmp_path):
    markets = _triad()
    walks = _walks(.72)
    session, _repo, scanner, gamma, _clob = _scanner(tmp_path, markets, walks)
    scanner.config = replace(
        scanner.config,
        entry=replace(price_config().entry, prob_min=.70, prob_max=.73),
    )
    gamma.set_sweep(1, NOW)
    scanner.save_market_snapshots(markets, now=NOW)
    candidates = scanner.scan_buy_candidates(markets, now=NOW)
    assert len(candidates) == 1
    assert candidates[0]['token_id'] == 'no-AWAY'
    assert len(candidates[0]['trend_snapshot_ids']) == 1
    session.close()


def test_execution_and_orphan_validation_accept_single_real_snapshot(monkeypatch):
    monkeypatch.setattr(trader_module, 'datetime', _FixedDatetime)
    repo, clob = _Repo(), _Clob(vwap=.75, best_bid=.74, best_ask=.75)
    config = price_config()
    old_history = repo.get_recent_token_snapshots('fixture', limit=3)
    repo.get_recent_token_snapshots = lambda token, limit: old_history[-1:] if limit == 1 else pytest.fail('history gate reintroduced')
    candidate = _candidate()
    candidate.update(trend_start_snapshot_id=11, trend_middle_snapshot_id=11,
                     trend_snapshot_ids=[11], trend_prices=[.75], trend_cumulative_move=0,
                     trend_max_pullback=0, trend_elapsed_seconds=0)
    trader = Trader(repo, clob, config, simulation_mode=False)
    _set_kickoff_cycle(trader)
    assert trader.execute_buy(candidate) == 7
    assert repo.created[0]['prior_snapshot_id_at_entry'] is None
    episode = SimpleNamespace(trend_start_snapshot_id=11, trend_middle_snapshot_id=11,
        entry_snapshot_id=11, trend_observations=1, trend_cumulative_move=0,
        trend_max_pullback=0, trend_elapsed_seconds=0, exact_vwap=.75, source_elapsed_minutes=20)
    assert _orphan_episode_contract_matches(episode, config)
    episode.trend_middle_snapshot_id = 10
    assert not _orphan_episode_contract_matches(episode, config)


@pytest.mark.parametrize('account,arm',[('king','a'),('queen','b')])
def test_nfl_live_profile_requires_a_future_explicit_approval(monkeypatch, account, arm):
    _credentials(monkeypatch)
    with pytest.raises(ValueError, match='unsupported Golden Plum runtime job'):
        load_config('config.yaml', f'plum-live-{account}-nfl-price-{arm}-v9', simulation_mode=False)
