"""Short stream windows retain distinct message receipts and one shared body."""
import gzip
import json

import pytest

from polybot_observability.market_data_index import (
    ReceiptContext, index_public_row, indexed_guava_stream_payload,
)
from polybot_observability.market_data_refs import PayloadReferences, externalize_row
from polybot_observability.market_data_store import PayloadStore


def test_per_message_clock_and_tokens_reuse_one_window_without_synthesizing_missing_times(tmp_path):
    window = {"window_start": "2026-09-30T01:00:00Z", "window_end": "2026-09-30T01:00:05Z",
              "truncated": True, "complete_trade_tape": False, "messages": [
        {"ordinal": 0, "received_at": "2026-09-30T01:00:00.125Z", "payload": {"asset_id": "a", "bids": []}},
        {"ordinal": 1, "received_at": "2026-09-30T01:00:02Z", "payload": {
            "market": "condition-not-event", "price_changes": [{"asset_id": "a", "price": "0.55"}, {"asset_id": "b", "price": "0.45"}]}},
        {"ordinal": 2, "payload": {"asset_id": "a", "price": "0.56"}},
    ]}
    row = {"run_id": "run", "request_id": "request", "source": "clob_market_ws",
           "method": "SUBSCRIBE", "path": "/ws/market", "received_at": window["window_end"],
           "payload_gzip": gzip.compress(json.dumps(window).encode(), mtime=0)}
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store)
        encoded = externalize_row("golden-guava", "source_requests", row, references=refs)
        context = ReceiptContext("source", "guava-research-a-v1", "polybot-sim-guava-a")
        assert index_public_row("golden-guava", "source_requests", row, encoded, references=refs, context=context) == 3
        observations = list(store.iter_observations(token_id="a"))
        assert [o.observed_at for o in observations] == ["2026-09-30T01:00:00.125Z", "2026-09-30T01:00:02Z"]
        assert [indexed_guava_stream_payload(o, window) for o in observations] == [f["payload"] for f in window["messages"][:2]]
        assert len(list(store.iter_observations(token_id="b"))) == 1
        assert not list(store.iter_observations(event_id="condition-not-event"))
        assert context.gap_counts["source_requests.messages:missing_per_message_receipt_time"] == 1
        stats = store.stats()
        assert stats["observation_count"] == 3
        assert len({o.payload_sha for o in store.iter_observations()}) == 1
        # Retry preserves the original window observation and exact frame identities.
        index_public_row("golden-guava", "source_requests", row, encoded, references=refs, context=context)
        assert store.stats() == stats
        changed = {**window, "messages": [dict(window["messages"][0], received_at=window["window_end"])]}
        with pytest.raises(ValueError, match="frame receipt"):
            indexed_guava_stream_payload(observations[0], changed)


def test_frames_can_be_indexed_without_a_window_end_and_provider_ids_are_not_event_ids(tmp_path):
    with PayloadStore(tmp_path / "public.db") as store:
        refs = PayloadReferences(store, store)
        row = {"run_id": "run", "request_id": "stream", "source": "clob_market_ws",
               "method": "SUBSCRIBE", "path": "/ws/market", "received_at": None,
               "payload_gzip": gzip.compress(b'{"messages":[{"ordinal":0,"received_at":"2026-09-30T01:00:01Z","payload":{"asset_id":"a"}}]}', mtime=0)}
        context = ReceiptContext("source", "guava-research-a-v1")
        encoded = externalize_row("golden-guava", "source_requests", row, references=refs)
        assert index_public_row("golden-guava", "source_requests", row, encoded, references=refs, context=context) == 1
        assert context.gap_counts["source_requests:missing_original_receipt_time"] == 1
        news = {**row, "request_id": "news", "source": "espn", "method": "GET", "path": "/scoreboard",
                "received_at": "2026-09-30T01:00:03Z",
                "payload_gzip": gzip.compress(b'{"provider_game_id":"provider-123","games":[{"id":"provider-123"}]}', mtime=0)}
        encoded = externalize_row("golden-guava", "source_requests", news, references=refs)
        assert index_public_row("golden-guava", "source_requests", news, encoded, references=refs, context=context) == 1
        assert not list(store.iter_observations(event_id="provider-123"))
