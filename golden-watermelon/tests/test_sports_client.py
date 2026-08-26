from __future__ import annotations

from collections import deque

from polybot.api.sports_client import SportsClockClient
from polybot.config import SportsFeedConfig


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = deque(messages)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def recv(self, *, timeout):
        if not self.messages:
            raise TimeoutError
        return self.messages.popleft()

    def send(self, message):
        self.sent.append(message)


def config() -> SportsFeedConfig:
    return SportsFeedConfig(
        websocket_url="wss://sports-api.polymarket.com/ws",
        connect_timeout_seconds=5,
        receive_window_seconds=5,
        max_messages=100,
    )


def test_public_sports_snapshot_accepts_documented_slug_fallback(monkeypatch) -> None:
    websocket = FakeWebSocket(
        [
            "ping",
            '{"slug":"other","elapsed":"12:10"}',
            '{"slug":"ucl-a-b-2026-08-26","live":true,"ended":false,'
            '"score":"1-0","period":"2H","elapsed":"82:31",'
            '"last_update":"2026-08-26T20:12:00Z"}',
        ]
    )
    monkeypatch.setattr(
        "polybot.api.sports_client.connect", lambda *_args, **_kwargs: websocket
    )
    receipts = []
    batch = SportsClockClient(config(), receipts.append).collect(
        "run-1", {"1001": "ucl-a-b-2026-08-26"}
    )
    assert batch.status == "OBSERVED"
    assert batch.matched_count == 1
    assert batch.updates["ucl-a-b-2026-08-26"].payload["elapsed"] == "82:31"
    assert websocket.sent == ["pong"]
    assert receipts[0]["request_kind"] == "sports_clock_websocket_snapshot"
    assert receipts[0]["status"] == "OBSERVED"


def test_public_sports_snapshot_joins_observed_game_id_and_camel_event_state(
    monkeypatch,
) -> None:
    websocket = FakeWebSocket(
        [
            '{"gameId":999,"leagueAbbreviation":"lol","live":true}',
            '{"gameId":6088527,"leagueAbbreviation":"ucl",'
            '"homeTeam":"Team A","awayTeam":"Team B",'
            '"status":"inprogress","score":"1-0","period":"2H",'
            '"live":true,"ended":false,"eventState":{'
            '"type":"soccer","elapsed":"82:31",'
            '"updatedAt":"2026-08-26T20:12:00Z"}}',
        ]
    )
    monkeypatch.setattr(
        "polybot.api.sports_client.connect", lambda *_args, **_kwargs: websocket
    )
    batch = SportsClockClient(config(), lambda _receipt: None).collect(
        "run-operational", {"6088527": "ucl-a-b-2026-08-26"}
    )
    assert batch.status == "OBSERVED"
    update = batch.updates["ucl-a-b-2026-08-26"]
    assert update.game_id == "6088527"
    assert update.payload["elapsed"] == "82:31"
    assert update.payload["leagueAbbreviation"] == "ucl"
    assert update.payload["updatedAt"] == "2026-08-26T20:12:00Z"


def test_sports_snapshot_failure_is_evidence_not_an_unbounded_retry(
    monkeypatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("bounded failure")

    monkeypatch.setattr("polybot.api.sports_client.connect", fail)
    receipts = []
    batch = SportsClockClient(config(), receipts.append).collect(
        "run-2", {"2002": "uel-a-b-2026-08-27"}
    )
    assert batch.status == "FAILED"
    assert batch.error_type == "RuntimeError"
    assert receipts[0]["status"] == "FAILED"
    assert receipts[0]["response_bytes"] == 0
