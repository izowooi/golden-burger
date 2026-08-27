from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from polybot.config import CANONICAL_JOB, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_research_environment(monkeypatch):
    aliases = {
        "PRIVATE_KEY", "POLY_PRIVATE_KEY", "POLYGON_PRIVATE_KEY",
        "WALLET_PRIVATE_KEY", "FUNDER_ADDRESS", "POLY_FUNDER_ADDRESS",
        "WALLET_ADDRESS", "SIGNATURE_TYPE", "API_KEY", "API_SECRET",
        "API_PASSPHRASE", "SECRET_KEY", "ACCESS_TOKEN", "AUTH_TOKEN",
        "PASSPHRASE", "PK",
    }
    for key in list(__import__("os").environ):
        if key.startswith(("POLYMARKET_", "CLOB_", "POLYBOT_")) or key in aliases:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def config(tmp_path):
    loaded = load_config(PROJECT_ROOT / "config.yaml", CANONICAL_JOB, mode="sim")
    return replace(
        loaded,
        db_path=tmp_path / "data" / CANONICAL_JOB / "trades_sim.db",
    )


@pytest.fixture
def make_us_event(config):
    def factory(family: str, *, phase: str | None = None, minor: bool = False) -> dict[str, Any]:
        payload = config.registry.by_code[family].payload
        identity = payload["sport"]
        tag_ids = payload["required_event_tag_ids"]
        title = f"Official {identity['name']} Team A vs Team B"
        if phase == "PRESEASON":
            title += " Preseason"
        elif phase == "POSTSEASON":
            title += " Playoffs"
        if minor:
            title += " G League"
        event = {
            "id": f"{family}-event-1",
            "gameId": f"{family}-game-1",
            "title": title,
            "slug": title.casefold().replace(" ", "-"),
            "live": True,
            "ended": False,
            "active": True,
            "closed": False,
            "sport": {
                "id": identity["id"],
                "sport": identity["code"],
                "name": identity["name"],
                "primaryTagId": identity["primary_tag_id"],
                "series": identity["root_id"],
                "tags": ",".join(str(value) for value in tag_ids),
            },
            "tags": [
                {"id": value, "slug": f"tag-{value}"} for value in tag_ids
            ],
            "series": [{"id": identity["root_id"], "slug": f"{family}-root"}],
            "teams": [
                {"id": "a", "name": "Team A", "alias": "A", "abbreviation": "A"},
                {"id": "b", "name": "Team B", "alias": "B", "abbreviation": "B"},
            ],
            "volumeNum": 25000,
            "volume24hr": 5000,
            "liquidity": 15000,
            "liquidityNum": 16000,
        }
        return event
    return factory


@pytest.fixture
def make_us_market():
    def factory(family: str, *, token_prefix: str | None = None) -> dict[str, Any]:
        prefix = token_prefix or family
        return {
            "id": f"{family}-market-1",
            "conditionId": f"{family}-condition-1",
            "question": "Team A vs Team B",
            "sportsMarketType": "moneyline",
            "outcomes": ["Team A", "Team B"],
            "clobTokenIds": [f"{prefix}-token-a", f"{prefix}-token-b"],
            "outcomePrices": ["0.50", "0.50"],
            "negRisk": False,
            "active": True,
            "closed": False,
            "enableOrderBook": True,
            "acceptingOrders": True,
            "volumeNum": 20000,
            "volume24hr": 4000,
            "liquidity": 12000,
            "liquidityNum": 13000,
        }
    return factory


@pytest.fixture
def make_soccer_event(config):
    def factory() -> dict[str, Any]:
        league = config.registry.by_code["soccer"].payload["domestic_leagues"][0]
        common = config.registry.by_code["soccer"].payload["required_common_tag_ids"]
        tags = sorted(set([*common, *league["required_tag_ids"]]))
        return {
            "id": "soccer-event-1",
            "gameId": "soccer-game-1",
            "title": "Home FC vs Away FC",
            "slug": "epl-home-away",
            "live": True,
            "ended": False,
            "active": True,
            "closed": False,
            "sport": {
                "id": league["sport_id"],
                "sport": league["code"],
                "name": league["name"],
                "primaryTagId": league["primary_tag_id"],
                "series": league["series_id"],
                "tags": ",".join(str(value) for value in tags),
            },
            "tags": [{"id": value, "slug": f"tag-{value}"} for value in tags],
            "series": [{"id": league["series_id"], "slug": league["series_slug"]}],
            "seriesSlug": league["series_slug"],
            "teams": [
                {"id": "home", "name": "Home FC", "alias": "Home", "league": league["team_league"]},
                {"id": "away", "name": "Away FC", "alias": "Away", "league": league["team_league"]},
            ],
            "volumeNum": 50000,
            "volume24hr": 9000,
            "liquidity": 30000,
            "liquidityNum": 32000,
        }
    return factory


@pytest.fixture
def make_soccer_market():
    def factory(result: str = "Home FC", index: int = 1) -> dict[str, Any]:
        slug = result.casefold().replace(" ", "-")
        return {
            "id": f"soccer-market-{index}",
            "conditionId": f"soccer-condition-{index}",
            "question": f"Will {result} be the match result?",
            "groupItemTitle": result,
            "slug": f"soccer-{slug}-{index}",
            "sportsMarketType": "moneyline",
            "outcomes": ["Yes", "No"],
            "clobTokenIds": [f"soccer-token-{index}-yes", f"soccer-token-{index}-no"],
            "outcomePrices": ["0.50", "0.50"],
            "negRisk": True,
            "active": True,
            "closed": False,
            "enableOrderBook": True,
            "acceptingOrders": True,
            "volumeNum": 40000,
            "volume24hr": 8000,
            "liquidity": 25000,
            "liquidityNum": 26000,
        }
    return factory
