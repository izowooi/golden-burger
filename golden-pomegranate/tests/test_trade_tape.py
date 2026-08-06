"""Bounded, exact-window public Data API trade observation contract."""

from __future__ import annotations

import json

import pytest
from requests.exceptions import ChunkedEncodingError

from polybot.api.data_client import (
    DataApiClient,
    canonical_trade_hash,
    sanitize_trade,
)
from polybot.config import DataApiConfig, GammaConfig


DISPLAY_FIELDS = {
    "title",
    "slug",
    "icon",
    "eventSlug",
    "name",
    "pseudonym",
    "bio",
    "profileImage",
    "profileImageOptimized",
}


def _trade(**overrides):
    row = {
        "proxyWallet": "0x0000000000000000000000000000000000000001",
        "side": "BUY",
        "asset": "asset-1",
        "conditionId": "condition-1",
        "size": 12.5,
        "price": 0.42,
        "timestamp": 1_786_000_000,
        "outcome": "Yes",
        "outcomeIndex": 0,
        "transactionHash": "0xtrade",
        "title": "display title",
        "slug": "display-slug",
        "icon": "https://display.invalid/icon.png",
        "eventSlug": "display-event",
        "name": "display name",
        "pseudonym": "display pseudonym",
        "bio": "display bio",
        "profileImage": "https://display.invalid/profile.png",
        "profileImageOptimized": "https://display.invalid/profile-small.png",
    }
    row.update(overrides)
    return row


class Response:
    status_code = 200
    headers = {}

    def __init__(self, rows):
        self.rows = rows
        self.content = json.dumps(rows, sort_keys=True).encode("utf-8")

    def json(self):
        return self.rows


class CallbackSession:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []
        self.headers = {}

    def get(self, url, *, params, timeout):
        copied = dict(params)
        self.calls.append((url, copied, timeout))
        value = self.callback(copied)
        if isinstance(value, BaseException):
            raise value
        return Response(value)


def _client(
    session,
    *,
    trade_limit=10_000,
    raw_payload_sink=None,
    evidence_sink=None,
    retry_max=1,
    monotonic=None,
    **data_overrides,
):
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return DataApiClient(
        DataApiConfig(trade_limit=trade_limit, **data_overrides),
        GammaConfig(
            max_retries=retry_max,
            retry_base_seconds=0,
            retry_max_seconds=1,
        ),
        session=session,
        evidence_sink=evidence_sink,
        raw_payload_sink=raw_payload_sink,
        **kwargs,
    )


def test_first_run_anchors_24h_back_but_fetches_one_bounded_catchup_chunk():
    session = CallbackSession(lambda _params: [])
    client = _client(session)
    now = 2_000_000

    result = client.fetch_incremental(
        watermark_epoch=None,
        now_epoch=now,
        cycle_number=1,
        run_id="run-1",
    )

    source_target_end = now - 300
    assert result.source_target_end_epoch == source_target_end
    assert result.target_start_epoch == source_target_end - 24 * 3600
    assert result.target_end_epoch == result.target_start_epoch + 3_600
    assert result.watermark_before_epoch is None
    assert result.watermark_advance_to_epoch == result.target_end_epoch
    assert result.status == "EMPTY"
    assert result.possible_gap is False
    assert session.calls[0][0].endswith("/trades")
    assert session.calls[0][1] == {
        "start": result.target_start_epoch,
        "end": result.target_end_epoch,
        "limit": 10_000,
        "offset": 0,
        "takerOnly": "true",
    }
    assert result.windows[0]["status"] == "EMPTY"
    assert result.windows[0]["row_count"] == 0


def test_persisted_backlog_advances_in_contiguous_bounded_chunks():
    session = CallbackSession(lambda _params: [])
    client = _client(session)
    source_target_end = 100_000

    first = client.fetch_incremental(
        watermark_epoch=10_000,
        now_epoch=source_target_end + 300,
        cycle_number=2,
        run_id="run-2",
    )
    second = client.fetch_incremental(
        watermark_epoch=first.watermark_advance_to_epoch,
        now_epoch=source_target_end + 300,
        cycle_number=3,
        run_id="run-3",
    )

    assert first.target_start_epoch == 10_000 - 1_800
    assert first.target_end_epoch == 10_000 + 3_600
    assert first.watermark_advance_to_epoch == first.target_end_epoch
    assert second.target_start_epoch == first.target_end_epoch - 1_800
    assert second.target_end_epoch == first.target_end_epoch + 3_600
    assert second.watermark_advance_to_epoch == second.target_end_epoch
    assert second.target_end_epoch < source_target_end
    assert first.source_target_end_epoch == source_target_end
    assert second.source_target_end_epoch == source_target_end


def test_failed_bootstrap_reuses_stable_baseline_instead_of_chasing_now():
    session = CallbackSession(lambda _params: ChunkedEncodingError("truncated"))
    client = _client(session)
    first_now = 2_000_000
    baseline = first_now - 300 - 24 * 3600

    first = client.fetch_incremental(
        watermark_epoch=None,
        bootstrap_start_epoch=baseline,
        now_epoch=first_now,
        cycle_number=1,
        run_id="run-1",
    )
    second = client.fetch_incremental(
        watermark_epoch=None,
        bootstrap_start_epoch=baseline,
        now_epoch=first_now + 900,
        cycle_number=2,
        run_id="run-2",
    )

    assert first.status == second.status == "ERROR"
    assert first.target_start_epoch == second.target_start_epoch == baseline
    assert first.target_end_epoch == second.target_end_epoch == baseline + 3_600
    assert first.source_target_end_epoch == first_now - 300
    assert second.source_target_end_epoch == first_now + 900 - 300
    assert first.watermark_advance_to_epoch is None
    assert second.watermark_advance_to_epoch is None


def test_later_run_uses_thirty_minute_overlap_and_canonical_dedupe():
    duplicate = _trade(timestamp=9_000)
    session = CallbackSession(lambda _params: [duplicate, dict(duplicate)])
    client = _client(session)

    result = client.fetch_incremental(
        watermark_epoch=10_000,
        now_epoch=10_500,
        cycle_number=2,
        run_id="run-2",
    )

    assert result.target_start_epoch == 10_000 - 1_800
    assert result.target_end_epoch == 10_500 - 300
    assert result.source_target_end_epoch == 10_500 - 300
    # Byte-identical rows may be distinct trades. Preserve within-window
    # multiplicity, then use stable occurrence IDs to dedupe overlap runs.
    assert len(result.trades) == 2
    assert len(result.memberships) == 2
    assert {trade["economic_row_hash"] for trade in result.trades} == {
        canonical_trade_hash(sanitize_trade(duplicate))
    }
    assert {trade["occurrence_index"] for trade in result.trades} == {0, 1}
    assert {membership["trade_id"] for membership in result.memberships} == {
        trade["trade_id"] for trade in result.trades
    }


def test_cap_window_recursively_splits_at_midpoint_before_advancing_watermark():
    def rows(params):
        start, end = params["start"], params["end"]
        if end - start > 1:
            return [
                _trade(timestamp=start, transactionHash=f"0x{start}-a"),
                _trade(timestamp=end, transactionHash=f"0x{end}-b"),
            ]
        return [_trade(timestamp=start, transactionHash=f"0x{start}")]

    session = CallbackSession(rows)
    client = _client(
        session,
        trade_limit=2,
        overlap_seconds=2,
        safety_lag_seconds=0,
    )

    result = client.fetch_incremental(
        watermark_epoch=2,
        now_epoch=4,
        cycle_number=1,
        run_id="run",
    )

    assert result.target_start_epoch == 0
    assert result.target_end_epoch == 4
    assert result.status == "SUCCESS"
    assert result.watermark_advance_to_epoch == 4
    assert result.possible_gap is False
    assert result.windows[0]["status"] == "SPLIT"
    leaf_windows = [
        window for window in result.windows if window["status"] == "COMPLETE"
    ]
    assert leaf_windows
    assert all(window["row_count"] < 2 for window in leaf_windows)
    assert max(window["split_depth"] for window in leaf_windows) > 0
    assert len(session.calls) > 1


def test_indivisible_single_timestamp_cap_is_fail_visible_and_freezes_watermark():
    session = CallbackSession(
        lambda params: [
            _trade(
                timestamp=params["start"],
                transactionHash=f"0x{params['start']}-a",
            ),
            _trade(
                timestamp=params["start"],
                transactionHash=f"0x{params['start']}-b",
            ),
        ]
    )
    client = _client(
        session,
        trade_limit=2,
        overlap_seconds=2,
        safety_lag_seconds=0,
    )

    result = client.fetch_incremental(
        watermark_epoch=0,
        now_epoch=1,
        cycle_number=1,
        run_id="run",
    )

    assert result.target_start_epoch == 0
    assert result.target_end_epoch == 1
    assert result.status == "POSSIBLE_GAP"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert any(window["status"] == "POSSIBLE_GAP" for window in result.windows)
    assert any(window["possible_gap"] == 1 for window in result.windows)


def test_two_inclusive_timestamp_buckets_split_without_overlap_or_false_gap():
    def rows(params):
        if params["start"] != params["end"]:
            return [
                _trade(timestamp=params["start"], transactionHash="0xleft"),
                _trade(timestamp=params["end"], transactionHash="0xright"),
            ]
        return [_trade(timestamp=params["start"], transactionHash="0xleaf")]

    session = CallbackSession(rows)
    client = _client(
        session,
        trade_limit=2,
        overlap_seconds=1,
        safety_lag_seconds=0,
    )

    result = client.fetch_incremental(
        watermark_epoch=1,
        now_epoch=1,
        cycle_number=1,
        run_id="run",
    )

    assert result.status == "SUCCESS"
    assert result.watermark_advance_to_epoch == 1
    leaves = [
        (window["start_epoch"], window["end_epoch"])
        for window in result.windows
        if window["status"] == "COMPLETE"
    ]
    assert leaves == [(0, 0), (1, 1)]


def test_contract_error_window_retains_http_and_sanitized_payload_lineage():
    raw_payloads = []

    def save_payload(**kwargs):
        raw_payloads.append(kwargs)
        return "payload-invalid"

    session = CallbackSession(
        lambda params: [
            _trade(timestamp=params["end"] + 1, transactionHash="0xoutside")
        ]
    )
    client = _client(session, raw_payload_sink=save_payload)

    result = client.fetch_incremental(
        watermark_epoch=10_000,
        now_epoch=10_500,
        cycle_number=2,
        run_id="run",
    )

    assert result.status == "ERROR"
    assert result.windows[-1]["request_id"]
    assert result.windows[-1]["raw_payload_id"] == "payload-invalid"
    assert raw_payloads[0]["request_id"] == result.windows[-1]["request_id"]
    assert "outside requested bounds" in result.windows[-1]["error_message"]


def test_transport_failure_is_an_error_observation_and_never_advances_watermark():
    session = CallbackSession(lambda _params: ChunkedEncodingError("truncated"))
    client = _client(session)

    result = client.fetch_incremental(
        watermark_epoch=10_000,
        now_epoch=10_500,
        cycle_number=2,
        run_id="run",
    )

    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.windows[-1]["status"] == "ERROR"
    assert result.windows[-1]["start_epoch"] == 8_200
    assert result.windows[-1]["end_epoch"] == 10_200
    assert "ChunkedEncodingError" in result.windows[-1]["error_message"]


def test_http_attempt_budget_stops_retries_and_is_fail_visible():
    evidence = []
    session = CallbackSession(lambda _params: ChunkedEncodingError("truncated"))
    client = _client(
        session,
        retry_max=6,
        evidence_sink=evidence.append,
        max_request_attempts_per_cycle=2,
        max_windows_per_cycle=10,
    )

    result = client.fetch_incremental(
        watermark_epoch=10_000,
        now_epoch=10_500,
        cycle_number=2,
        run_id="run",
    )

    assert len(session.calls) == 2
    assert len(evidence) == 2
    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.windows[-1]["status"] == "BUDGET_EXHAUSTED"
    assert "kind=request_attempts" in result.windows[-1]["error_message"]
    assert "request_attempts=2" in result.windows[-1]["error_message"]


def test_logical_window_budget_stops_recursive_split_without_watermark_advance():
    def capped_until_one_second(params):
        if params["end"] - params["start"] > 1:
            return [
                _trade(timestamp=params["start"], transactionHash="0xa"),
                _trade(timestamp=params["end"], transactionHash="0xb"),
            ]
        return [_trade(timestamp=params["start"], transactionHash="0xleaf")]

    session = CallbackSession(capped_until_one_second)
    client = _client(
        session,
        trade_limit=2,
        safety_lag_seconds=0,
        overlap_seconds=0,
        catchup_chunk_seconds=100,
        max_request_attempts_per_cycle=64,
        max_windows_per_cycle=2,
    )

    result = client.fetch_incremental(
        watermark_epoch=100,
        now_epoch=200,
        cycle_number=2,
        run_id="run",
    )

    assert len(session.calls) == 2
    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.windows[-1]["status"] == "BUDGET_EXHAUSTED"
    assert "kind=windows" in result.windows[-1]["error_message"]
    assert "windows_started=2" in result.windows[-1]["error_message"]


def test_runtime_budget_stops_before_next_split_request_and_records_usage():
    clock = [0.0]

    def capped_response(params):
        clock[0] = 2.0
        return [
            _trade(timestamp=params["start"], transactionHash="0xa"),
            _trade(timestamp=params["end"], transactionHash="0xb"),
        ]

    session = CallbackSession(capped_response)
    client = _client(
        session,
        trade_limit=2,
        safety_lag_seconds=0,
        overlap_seconds=0,
        catchup_chunk_seconds=100,
        max_request_attempts_per_cycle=64,
        max_windows_per_cycle=32,
        runtime_budget_seconds=1,
        monotonic=lambda: clock[0],
    )

    result = client.fetch_incremental(
        watermark_epoch=100,
        now_epoch=200,
        cycle_number=2,
        run_id="run",
    )

    assert len(session.calls) == 1
    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.windows[-1]["status"] == "BUDGET_EXHAUSTED"
    assert "kind=runtime_seconds" in result.windows[-1]["error_message"]
    assert "elapsed_seconds=2.000" in result.windows[-1]["error_message"]


def test_runtime_budget_rejects_slow_success_without_advancing_watermark():
    clock = [0.0]

    def slow_success(params):
        clock[0] = 2.0
        return [_trade(timestamp=params["start"])]

    session = CallbackSession(slow_success)
    client = _client(
        session,
        safety_lag_seconds=0,
        overlap_seconds=0,
        catchup_chunk_seconds=100,
        runtime_budget_seconds=1,
        monotonic=lambda: clock[0],
    )

    result = client.fetch_incremental(
        watermark_epoch=100,
        now_epoch=200,
        cycle_number=2,
        run_id="run",
    )

    assert len(session.calls) == 1
    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.windows[-1]["status"] == "BUDGET_EXHAUSTED"
    assert "kind=runtime_seconds" in result.windows[-1]["error_message"]


def test_clock_regression_fails_before_request_and_preserves_watermark():
    session = CallbackSession(
        lambda _params: pytest.fail("clock regression must not call the source")
    )
    client = _client(session)

    result = client.fetch_incremental(
        watermark_epoch=2_000,
        now_epoch=2_100,
        cycle_number=2,
        run_id="run",
    )

    assert result.target_end_epoch == 1_800
    assert result.source_target_end_epoch == 1_800
    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.windows[0]["status"] == "ERROR"
    assert "clock regression" in result.error_message
    assert session.calls == []


def test_out_of_window_trade_is_malformed_and_freezes_watermark():
    session = CallbackSession(lambda params: [_trade(timestamp=params["end"] + 1)])
    client = _client(session)

    result = client.fetch_incremental(
        watermark_epoch=10_000,
        now_epoch=10_500,
        cycle_number=2,
        run_id="run",
    )

    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.trades == ()
    assert result.memberships == ()
    assert "outside requested bounds" in result.error_message


def test_only_trade_fields_are_persistable_and_sanitized_raw_has_no_profile_data():
    raw_calls = []
    source_trade = _trade(timestamp=0)
    session = CallbackSession(
        lambda params: [{**source_trade, "timestamp": params["start"]}]
    )
    client = _client(
        session,
        raw_payload_sink=lambda **kwargs: raw_calls.append(kwargs) or "raw-1",
    )

    result = client.fetch_incremental(
        watermark_epoch=None,
        now_epoch=2_000_000,
        cycle_number=1,
        run_id="run",
    )

    stored = result.trades[0]
    assert DISPLAY_FIELDS.isdisjoint(stored)
    assert stored["conditionId"] == source_trade["conditionId"]
    assert stored["transactionHash"] == source_trade["transactionHash"]
    assert len(raw_calls) == 1
    raw_text = raw_calls[0]["content"].decode("utf-8")
    assert all(field not in raw_text for field in DISPLAY_FIELDS)
    assert "display title" not in raw_text
    assert raw_calls[0]["kind"] == "data_trades_sanitized_window"


def test_recursive_split_windows_preserve_parent_lineage():
    def rows(params):
        if params["end"] - params["start"] > 1:
            return [
                _trade(timestamp=params["start"], transactionHash="0xleft"),
                _trade(timestamp=params["end"], transactionHash="0xright"),
            ]
        return [_trade(timestamp=params["start"], transactionHash="0xleaf")]

    result = _client(
        CallbackSession(rows),
        trade_limit=2,
        overlap_seconds=0,
        safety_lag_seconds=0,
        catchup_chunk_seconds=4,
    ).fetch_incremental(
        watermark_epoch=0,
        now_epoch=4,
        cycle_number=1,
        run_id="run",
    )

    by_id = {window["window_id"]: window for window in result.windows}
    roots = [window for window in result.windows if window["split_depth"] == 0]
    assert len(roots) == 1
    assert roots[0]["parent_window_id"] is None
    for window in result.windows:
        if window["split_depth"] == 0:
            continue
        parent = by_id[window["parent_window_id"]]
        assert parent["status"] == "SPLIT"
        assert parent["split_depth"] == window["split_depth"] - 1


def test_canonical_hash_ignores_display_profile_changes_but_not_trade_changes():
    one = _trade(name="one", bio="one")
    two = _trade(name="two", bio="two")
    changed_trade = _trade(price=0.43)

    assert canonical_trade_hash(sanitize_trade(one)) == canonical_trade_hash(
        sanitize_trade(two)
    )
    assert canonical_trade_hash(sanitize_trade(one)) != canonical_trade_hash(
        sanitize_trade(changed_trade)
    )


def test_canonical_hash_normalizes_equivalent_numeric_source_representations():
    numeric = sanitize_trade(
        _trade(size=5, price=0.42, timestamp=1_786_000_000, outcomeIndex=0)
    )
    strings = sanitize_trade(
        _trade(
            size="5.0",
            price="0.420",
            timestamp="1786000000.0",
            outcomeIndex="0",
        )
    )

    assert canonical_trade_hash(numeric) == canonical_trade_hash(strings)


def test_canonical_hash_preserves_exact_decimal_distinctions():
    short = sanitize_trade(_trade(size="0.1", price="0.42"))
    precise_size = sanitize_trade(_trade(size="0.10000000000000001", price="0.42"))
    precise_price = sanitize_trade(_trade(size="0.1", price="0.42000000000000001"))

    assert canonical_trade_hash(short) != canonical_trade_hash(precise_size)
    assert canonical_trade_hash(short) != canonical_trade_hash(precise_price)


def test_fractional_timestamp_is_contract_error_and_freezes_watermark():
    session = CallbackSession(lambda params: [_trade(timestamp=params["start"] + 0.5)])
    client = _client(
        session,
        safety_lag_seconds=0,
        overlap_seconds=0,
        catchup_chunk_seconds=100,
    )

    result = client.fetch_incremental(
        watermark_epoch=100,
        now_epoch=200,
        cycle_number=2,
        run_id="run",
    )

    assert result.status == "ERROR"
    assert result.possible_gap is True
    assert result.watermark_advance_to_epoch is None
    assert result.windows[0]["status"] == "ERROR"
    assert "timestamp must be an integer" in result.windows[0]["error_message"]


def test_data_api_client_never_adds_authorization_headers():
    session = CallbackSession(lambda _params: [])
    _client(session)

    assert "Authorization" not in session.headers
    assert not any(name.startswith("POLY_") for name in session.headers)
    assert session.trust_env is False
