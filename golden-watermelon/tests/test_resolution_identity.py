from datetime import timedelta
import json

import pytest

from polybot.api.gamma_client import MarketResolution
from polybot.resolution import aligned_winner_index, terminal_token_payouts
from test_collector import (
    NOW, FakeGamma, FakeClob, collector, configured, repository_for,
)


def gamma_evidence(tokens=("no", "yes")):
    return {"closed": True, "token_payouts": [
        {"token_id": tokens[0], "payout": 0.0},
        {"token_id": tokens[1], "payout": 1.0},
    ]}


def test_resolution_binds_winning_token_after_source_order_reversal():
    evidence = gamma_evidence()
    assert aligned_winner_index(evidence, ("yes", "no")) == 0
    assert terminal_token_payouts(evidence)["yes"] == 1.0


@pytest.mark.parametrize("tokens", [("unknown", "yes"), ("yes", "yes"), ("", "yes")])
def test_resolution_rejects_replaced_or_duplicate_tokens(tokens):
    with pytest.raises(ValueError):
        aligned_winner_index(gamma_evidence(tokens), ("yes", "no"))


def test_void_requires_exact_tokens_and_authoritative_resolution():
    evidence = {"closed": True, "resolution_kind": "VOID", "token_payouts": [
        {"token_id": "yes", "payout": 0.5}, {"token_id": "no", "payout": 0.5},
    ]}
    with pytest.raises(ValueError, match="authoritative"):
        aligned_winner_index(evidence, ("yes", "no"))
    evidence["uma_resolution_status"] = "resolved"
    assert aligned_winner_index(evidence, ("yes", "no")) is None


@pytest.mark.parametrize("evidence", [
    {"closed": True},
    {"closed": True, "token_payouts": []},
    {"closed": True, "token_payouts": [{"token_id": "yes", "payout": 1.0}]},
    {"closed": True, "token_payouts": [{"token_id": "yes", "payout": 0.0}, {"token_id": "no", "payout": 0.0}]},
])
def test_terminal_missing_or_zero_winner_evidence_is_rejected(evidence):
    with pytest.raises(ValueError):
        aligned_winner_index(evidence, ("yes", "no"))


class ReorderedGamma(FakeGamma):
    tokens = ("team-a-no", "team-a")

    def fetch_market_resolution(self, run_id, condition_id):
        return MarketResolution(
            condition_id, "RESOLVED", 1, (0.0, 1.0), ("No", "Yes"),
            self.tokens, {"closed": True}, "gamma-resolution",
            "2026-08-22T16:47:00Z", "d" * 64, b"[]",
        )


def test_collector_normalizes_resolution_and_retains_source_order(tmp_path):
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    collector(config, repository, FakeGamma(), FakeClob()).collect("entry", now=NOW)
    collector(config, repository, ReorderedGamma(), FakeClob()).collect(
        "resolution", now=NOW + timedelta(minutes=31)
    )
    with repository.connect() as c:
        row = c.execute("SELECT * FROM resolution_observations").fetchone()
        assert row["winner_index"] == 0
        evidence = json.loads(row["evidence_json"])
        assert evidence["source_winner_index"] == 1
        assert evidence["normalization_contract"] == "exact-token-payout-v1"
        assert evidence["token_payouts"][1]["token_id"] == "team-a"


def test_collector_rejects_wrong_resolution_pair_and_continues_followup(tmp_path):
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    collector(config, repository, FakeGamma(), FakeClob()).collect("entry", now=NOW)
    gamma = ReorderedGamma()
    gamma.tokens = ("unrelated-token", "team-a")
    result = collector(config, repository, gamma, FakeClob()).collect(
        "resolution", now=NOW + timedelta(minutes=31)
    )
    assert result["resolutions_added"] == 0
    assert len(repository.open_episodes()) == 1
    with repository.connect() as c:
        assert c.execute("SELECT count(*) FROM resolution_observations").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM resolution_attempts WHERE status='RESOLUTION_TOKEN_IDENTITY_MISMATCH'").fetchone()[0] == 1


def test_decimal_096_ask_opens_096_episode_without_threshold_retune(tmp_path):
    config = configured(tmp_path)
    repository = repository_for(config)
    collector(config, repository, FakeGamma(), FakeClob(ask=0.96)).collect("entry", now=NOW)
    with repository.connect() as c:
        thresholds = [row[0] for row in c.execute("SELECT threshold FROM hypothetical_episodes ORDER BY threshold")]
    assert thresholds == [0.95, 0.96]


def test_pre_game_live_flag_does_not_claim_missing_result_pair(tmp_path):
    from test_collector import market
    config = configured(tmp_path)
    repository = repository_for(config)
    gamma = FakeGamma(market(gameStartTime="2026-08-22T16:30:00Z"))
    result = collector(config, repository, gamma, FakeClob()).collect("early", now=NOW)
    assert result["eligible_outcomes"] == 0
    assert result["result_triad_gaps"] == 0
