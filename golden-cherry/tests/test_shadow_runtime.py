from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from polybot.shadow import DATA_CONTRACT, RUNTIME_JOB
from polybot.shadow.analyzer import analyze_shadow_database
from polybot.shadow.clients import BookRead, GammaPage, GammaSweep, ResolutionRead
from polybot.shadow.collector import ShadowCollector
from polybot.shadow.config import load_shadow_config
from polybot.shadow.db import ShadowRepository
from polybot.shadow.safety import assert_shadow_boundary
from polybot.shadow.transport import CollectionBudgetExceeded, CollectionDeadline


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _market(
    condition: str,
    event: str,
    token: str,
    probability: float,
    *,
    phase: str,
) -> dict:
    value = {
        "id": f"market-{condition}",
        "conditionId": condition,
        "slug": f"slug-{condition}",
        "question": f"Will {condition} resolve Yes?",
        "events": [{"id": event, "slug": f"slug-{event}", "title": event, "category": "Test"}],
        "tags": [{"slug": "test-category", "label": "Test Category"}],
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps([token, f"{token}-no"]),
        "outcomePrices": json.dumps([str(probability), str(1 - probability)]),
        "endDate": "2026-09-08T00:00:00Z",
        "liquidity": 150_000,
        "volume": 10_000,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
    }
    if phase == "PRE_GAME":
        value.update(
            gameStartTime="2026-09-07T00:00:00Z",
            sportsMarketType="moneyline",
        )
    elif phase == "IN_PLAY":
        value.update(
            gameStartTime="2026-09-05T00:00:00Z",
            sportsMarketType="moneyline",
        )
    return value


def _page(markets) -> GammaPage:
    raw = json.dumps({"markets": markets, "next_cursor": None}).encode()
    return GammaPage(
        page_number=1,
        after_cursor=None,
        next_cursor=None,
        request_id="gamma-page",
        received_at="2026-09-06T00:00:01Z",
        response_sha256="a" * 64,
        raw=raw,
        markets=tuple(markets),
    )


class FakeGamma:
    def __init__(self, markets, *, complete=True, resolve=False):
        self.markets = markets
        self.complete = complete
        self.resolve = resolve

    def fetch_sweep(self, run_id):
        return GammaSweep((_page(self.markets),), self.complete)

    def fetch_resolution(self, run_id, condition_id):
        if not self.resolve:
            raise AssertionError("resolution should not be queried")
        token = {
            "condition-low": "token-low",
            "condition-primary": "token-primary",
            "condition-high": "token-high",
        }[condition_id]
        market = {
            "conditionId": condition_id,
            "closed": True,
            "outcomes": ["Yes", "No"],
            "clobTokenIds": [token, f"{token}-no"],
            "outcomePrices": ["1", "0"],
        }
        raw = json.dumps([market]).encode()
        return ResolutionRead(
            condition_id,
            "OBSERVED",
            f"resolution-{condition_id}",
            "2026-09-08T00:00:01Z",
            "b" * 64,
            raw,
            market,
        )


class FakeClob:
    prices = {
        "token-low": 0.77,
        "token-primary": 0.81,
        "token-high": 0.85,
    }

    def __init__(self, *, no_books=False):
        self.no_books = no_books
        self.requested = []

    def fetch_book(self, run_id, token_id):
        self.requested.append(token_id)
        if self.no_books:
            return BookRead(
                token_id, "NO_BOOK", f"book-{token_id}", None, None, None,
                None, "HTTP_404",
            )
        ask = self.prices[token_id]
        book = {
            "asset_id": token_id,
            "market": f"condition-for-{token_id}",
            "timestamp": "1788652800",
            "bids": [{"price": str(ask - 0.01), "size": "100"}],
            "asks": [{"price": str(ask), "size": "100"}],
        }
        raw = json.dumps(book).encode()
        return BookRead(
            token_id, "OBSERVED", f"book-{token_id}",
            "2026-09-06T00:00:02Z", "c" * 64, raw, book,
        )


def _config(tmp_path):
    return replace(
        load_shadow_config(ROOT / "shadow_config.yaml"),
        db_path=tmp_path / "shadow" / "trades_sim.db",
    )


def _markets():
    return [
        _market("condition-high", "event-c", "token-high", 0.85, phase="NON_SPORTS"),
        _market("condition-primary", "event-b", "token-primary", 0.81, phase="IN_PLAY"),
        _market("condition-low", "event-a", "token-low", 0.77, phase="PRE_GAME"),
        {
            **_market("condition-bad", "event-d", "token-bad", 0.81, phase="NON_SPORTS"),
            "clobTokenIds": "not-json",
        },
    ]


def test_shadow_config_is_registered_isolated_and_frozen(tmp_path):
    config = load_shadow_config(ROOT / "shadow_config.yaml")

    assert config.runtime_job == RUNTIME_JOB
    assert config.data_contract == DATA_CONTRACT
    assert config.simulation_mode is True
    assert config.db_path.name == "trades_sim.db"
    assert config.db_path.parent.name == RUNTIME_JOB
    assert config.db_path != ROOT / "data" / "default" / "trades.db"
    assert config.collection_budget_seconds == 240
    assert [band.id for band in config.experiment.entry_bands] == [
        "control_low_076_078",
        "primary_080_082",
        "control_high_084_086",
    ]
    assert len(config.experiment.exit_policies) == 7
    assert not config.db_path.exists()


def test_shadow_config_rejects_unregistered_knobs(tmp_path):
    path = tmp_path / "shadow-extra.yaml"
    path.write_text(
        (ROOT / "shadow_config.yaml").read_text(encoding="utf-8")
        + "\n  unregistered_override: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="extra"):
        load_shadow_config(path)


def test_shadow_config_evidence_is_idempotent_and_append_only(tmp_path):
    config = _config(tmp_path)
    repository = ShadowRepository(config.db_path, config)
    repository.record_config()
    repository.record_config()
    with repository.connect(read_only=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shadow_config_versions"
        ).fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with repository.connect() as connection:
            connection.execute(
                "UPDATE shadow_config_versions SET mode='shadow'"
            )


@pytest.mark.parametrize(
    "key",
    [
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_API_KEY",
        "CLOB_SECRET",
    ],
)
def test_shadow_rejects_credentials_even_when_empty(key):
    with pytest.raises(ValueError, match="credential-bearing"):
        assert_shadow_boundary(["run", "--shadow"], {key: ""})


def test_shadow_rejects_live_and_polybot_overrides_before_loading():
    with pytest.raises(ValueError, match="--live"):
        assert_shadow_boundary(["run", "--shadow", "--live"], {})
    with pytest.raises(ValueError, match="frozen"):
        assert_shadow_boundary(["run", "--shadow"], {"POLYBOT_BUY_AMOUNT": "5"})


def test_shadow_source_tree_has_no_order_sdk_or_submission_call():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/polybot/shadow").glob("*.py"))
    )
    assert "py_clob_client" not in source
    assert "post_order" not in source
    assert "create_order" not in source
    assert "create_and_post" not in source


def test_paired_entry_cells_preserve_phase_identity_and_full_books(tmp_path):
    config = _config(tmp_path)
    repository = ShadowRepository(config.db_path, config)
    repository.record_config()
    repository.record_run_event("run-1", "STARTED")
    clob = FakeClob()
    result = ShadowCollector(
        config,
        repository,
        FakeGamma(_markets()),
        clob,
        CollectionDeadline(240),
    ).collect("run-1", now=NOW)
    repository.record_run_event("run-1", "SUCCEEDED", result)

    assert result["cursor_complete"] is True
    assert result["raw_markets"] == 4
    assert result["eligible_candidates"] == 3
    assert result["episodes_opened"] == 3
    assert clob.requested == ["token-low", "token-primary", "token-high"]
    with repository.connect(read_only=True) as connection:
        phases = dict(
            connection.execute(
                "SELECT condition_id, time_stratum FROM shadow_market_observations "
                "WHERE eligibility_status='ELIGIBLE'"
            ).fetchall()
        )
        decisions = connection.execute(
            "SELECT COUNT(*) FROM shadow_cell_decisions"
        ).fetchone()[0]
        opened = connection.execute(
            "SELECT band_id, time_stratum FROM shadow_episodes ORDER BY band_id"
        ).fetchall()
        policies = connection.execute(
            "SELECT COUNT(*) FROM shadow_episode_policies"
        ).fetchone()[0]
        levels = connection.execute(
            "SELECT COUNT(*) FROM shadow_book_levels"
        ).fetchone()[0]
        excluded = connection.execute(
            "SELECT exclusion_reasons_json FROM shadow_market_observations "
            "WHERE condition_id='condition-bad'"
        ).fetchone()[0]
    assert phases == {
        "condition-low": "PRE_GAME",
        "condition-primary": "IN_PLAY",
        "condition-high": "NON_SPORTS",
    }
    assert decisions == 9
    assert {row[0] for row in opened} == {
        "control_low_076_078", "primary_080_082", "control_high_084_086"
    }
    assert policies == 21
    assert levels == 6
    assert "OUTCOME_PRICE_TOKEN_IDENTITY_UNALIGNED" in excluded


def test_followup_records_exact_token_resolution_for_every_frozen_policy(tmp_path):
    config = _config(tmp_path)
    repository = ShadowRepository(config.db_path, config)
    repository.record_config()
    repository.record_run_event("run-1", "STARTED")
    first = ShadowCollector(
        config, repository, FakeGamma(_markets()), FakeClob(), CollectionDeadline(240)
    ).collect("run-1", now=NOW)
    repository.record_run_event("run-1", "SUCCEEDED", first)

    repository.record_run_event("run-2", "STARTED")
    second = ShadowCollector(
        config,
        repository,
        FakeGamma([], resolve=True),
        FakeClob(no_books=True),
        CollectionDeadline(240),
    ).collect("run-2", now=datetime(2026, 9, 8, tzinfo=timezone.utc))
    repository.record_run_event("run-2", "SUCCEEDED", second)

    assert second["resolution_observations"] == 3
    assert second["policy_exits"] == 21
    with repository.connect(read_only=True) as connection:
        proofs = connection.execute(
            "SELECT DISTINCT resolution_status,evidence_basis,token_payout "
            "FROM shadow_resolution_observations"
        ).fetchall()
        exit_bases = connection.execute(
            "SELECT DISTINCT exit_kind,evidence_basis FROM shadow_policy_exits"
        ).fetchall()
    assert all(row[0] == "PROVEN" for row in proofs)
    assert all("unique_one_hot_exact_token" in row[1] for row in proofs)
    assert {row[2] for row in proofs} == {1.0}
    assert all(row[0] == "RESOLUTION" for row in exit_bases)

    analysis = analyze_shadow_database(
        config.db_path,
        start=datetime(2026, 9, 5, tzinfo=timezone.utc),
        end=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    assert analysis["run_health"]["valid_successful_runs"] == 2
    assert len(analysis["paired_cells"]) == 21
    assert analysis["winner_selected"] is False
    assert analysis["causal_claim"] is False
    assert all(cell["completed_episodes"] == 1 for cell in analysis["paired_cells"])


def test_incomplete_cursor_never_publishes_partial_success_sweep(tmp_path):
    config = _config(tmp_path)
    repository = ShadowRepository(config.db_path, config)
    repository.record_run_event("run-bad", "STARTED")
    collector = ShadowCollector(
        config,
        repository,
        FakeGamma(_markets(), complete=False),
        FakeClob(),
        CollectionDeadline(240),
    )

    with pytest.raises(RuntimeError, match="terminal cursor"):
        collector.collect("run-bad", now=NOW)
    with repository.connect(read_only=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shadow_market_sweeps"
        ).fetchone()[0] == 0


def test_deterministic_book_cap_records_explicit_candidate_reason(tmp_path):
    config = _config(tmp_path)
    config = replace(config, clob=replace(config.clob, max_books_per_run=1))
    repository = ShadowRepository(config.db_path, config)
    clob = FakeClob()
    result = ShadowCollector(
        config,
        repository,
        FakeGamma(_markets()[:3]),
        clob,
        CollectionDeadline(240),
    ).collect("run-cap", now=NOW)

    assert result["books_selected"] == 1
    assert result["capped_candidates"] == 2
    assert clob.requested == ["token-low"]
    with repository.connect(read_only=True) as connection:
        capped = connection.execute(
            "SELECT COUNT(*) FROM shadow_cell_decisions "
            "WHERE decision_status='BOOK_CAP_EXCLUDED'"
        ).fetchone()[0]
    assert capped == 6


def test_collection_deadline_is_cooperative_and_below_five_minutes():
    ticks = iter([10.0, 249.0, 250.0])
    deadline = CollectionDeadline(240, monotonic=lambda: next(ticks))
    assert deadline.remaining_seconds == 1.0
    with pytest.raises(CollectionBudgetExceeded):
        deadline.require()


def test_shadow_cli_rejects_live_before_config_or_database(monkeypatch):
    from polybot.main import main
    import polybot.shadow.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "load_shadow_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("config must not load after --live")
        ),
    )
    with pytest.raises(SystemExit) as error:
        main(["run", "--shadow", "--live"])
    assert error.value.code == 2


def test_shadow_runtime_failure_records_failed_and_never_success(
    monkeypatch, tmp_path
):
    import polybot.shadow.runtime as runtime_module

    config = _config(tmp_path)
    monkeypatch.setattr(
        runtime_module.ShadowCollector,
        "collect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("incomplete book evidence")
        ),
    )
    with pytest.raises(RuntimeError, match="incomplete book"):
        runtime_module.ShadowRuntime(config).run(now=NOW)

    repository = ShadowRepository(config.db_path, config)
    with repository.connect(read_only=True) as connection:
        events = dict(
            connection.execute(
                "SELECT event_type, COUNT(*) FROM shadow_run_events GROUP BY event_type"
            ).fetchall()
        )
    assert events == {"FAILED": 1, "STARTED": 1}


def test_shadow_runtime_busy_lock_skips_before_database_or_network(
    monkeypatch, tmp_path
):
    import polybot.shadow.runtime as runtime_module

    class BusyLock:
        def __init__(self, path):
            self.acquired = False
            self.owner = {"pid": 123, "acquired_at": "2026-09-06T00:00:00Z"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    config = _config(tmp_path)
    monkeypatch.setattr(runtime_module, "DatabaseRunLock", BusyLock)
    monkeypatch.setattr(
        runtime_module,
        "ShadowRepository",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("busy lock must skip before DB initialization")
        ),
    )

    result = runtime_module.ShadowRuntime(config).run(now=NOW)

    assert result["skipped"] is True
    assert result["reason"] == "shadow_db_process_lock_busy"
    assert not config.db_path.exists()
