"""Frozen configuration for Cherry Shadow Resolution v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from . import DATA_CONTRACT, RUNTIME_JOB
from .safety import assert_shadow_boundary
from .source_digest import (
    PROJECT_ROOT,
    compute_strategy_source_digest,
    verify_preregistration,
)


PREREGISTRATION_ID = "cherry-shadow-resolution-v2-prereg-2026-09-05"
FROZEN_START = datetime(2026, 9, 4, 16, tzinfo=timezone.utc)
FROZEN_ENTRY_END = datetime(2026, 10, 4, 16, tzinfo=timezone.utc)
FROZEN_FOLLOWUP_END = datetime(2026, 11, 3, 16, tzinfo=timezone.utc)
FROZEN_BANDS = (
    ("control_low_076_078", 0.76, 0.78, "control"),
    ("primary_080_082", 0.80, 0.82, "primary_candidate"),
    ("control_high_084_086", 0.84, 0.86, "control"),
)
FROZEN_POLICIES = (
    ("hold_to_resolution", "primary", None, None, None),
    ("current_tp10_sl08_trail05", "current_control", 0.10, -0.08, 0.05),
    ("sensitivity_tp05_sl08_trail05", "sensitivity", 0.05, -0.08, 0.05),
    ("sensitivity_tp15_sl08_trail05", "sensitivity", 0.15, -0.08, 0.05),
    ("sensitivity_tp10_sl05_trail05", "sensitivity", 0.10, -0.05, 0.05),
    ("sensitivity_tp10_sl12_trail05", "sensitivity", 0.10, -0.12, 0.05),
    ("sensitivity_tp10_sl08_no_trailing", "sensitivity", 0.10, -0.08, None),
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _require_keys(
    value: Mapping[str, Any], expected: set[str], name: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{name} keys differ from frozen contract: "
            f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
        )


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _utc(value: Any, name: str) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class EntryBand:
    id: str
    low: float
    high: float
    role: str


@dataclass(frozen=True)
class ExitPolicy:
    id: str
    role: str
    take_profit: float | None
    stop_loss: float | None
    trailing: float | None


@dataclass(frozen=True)
class GammaSettings:
    base_url: str
    page_size: int
    max_pages: int
    min_liquidity: float
    min_total_volume: float
    gamma_probability_min: float
    gamma_probability_max: float
    entry_hours_min: float
    entry_hours_max: float


@dataclass(frozen=True)
class ClobSettings:
    base_url: str
    max_books_per_run: int


@dataclass(frozen=True)
class TransportSettings:
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    retry_max_seconds: float


@dataclass(frozen=True)
class ExperimentSettings:
    start_utc: datetime
    entry_end_utc: datetime
    followup_end_utc: datetime
    simulated_notional_usdc: float
    entry_bands: tuple[EntryBand, ...]
    exit_policies: tuple[ExitPolicy, ...]


@dataclass(frozen=True)
class ShadowConfig:
    simulation_mode: bool
    runtime_job: str
    data_contract: str
    preregistration_id: str
    preregistration_sha256: str
    strategy_source_digest: str
    config_hash: str
    cadence_minutes: int
    collection_budget_seconds: float
    gamma: GammaSettings
    clob: ClobSettings
    transport: TransportSettings
    experiment: ExperimentSettings
    db_path: Path

    def evidence_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["db_path"] = str(self.db_path)
        for key in ("start_utc", "entry_end_utc", "followup_end_utc"):
            value["experiment"][key] = getattr(self.experiment, key).isoformat()
        return value


def _parse_bands(value: Any) -> tuple[EntryBand, ...]:
    if not isinstance(value, list):
        raise ValueError("experiment.entry_bands must be a list")
    for row in value:
        _require_keys(
            _mapping(row, "entry band"),
            {"id", "low", "high", "role"},
            "entry band",
        )
    result = tuple(
        EntryBand(
            id=str(_mapping(row, "entry band").get("id") or ""),
            low=_number(row.get("low"), "entry band low"),
            high=_number(row.get("high"), "entry band high"),
            role=str(row.get("role") or ""),
        )
        for row in value
    )
    observed = tuple((row.id, row.low, row.high, row.role) for row in result)
    if observed != FROZEN_BANDS:
        raise ValueError("entry treatment bands differ from frozen preregistration")
    return result


def _optional_number(value: Any, name: str) -> float | None:
    return None if value is None else _number(value, name)


def _parse_policies(value: Any) -> tuple[ExitPolicy, ...]:
    if not isinstance(value, list):
        raise ValueError("experiment.exit_policies must be a list")
    for row in value:
        _require_keys(
            _mapping(row, "exit policy"),
            {"id", "role", "take_profit", "stop_loss", "trailing"},
            "exit policy",
        )
    result = tuple(
        ExitPolicy(
            id=str(_mapping(row, "exit policy").get("id") or ""),
            role=str(row.get("role") or ""),
            take_profit=_optional_number(row.get("take_profit"), "take_profit"),
            stop_loss=_optional_number(row.get("stop_loss"), "stop_loss"),
            trailing=_optional_number(row.get("trailing"), "trailing"),
        )
        for row in value
    )
    observed = tuple(
        (row.id, row.role, row.take_profit, row.stop_loss, row.trailing)
        for row in result
    )
    if observed != FROZEN_POLICIES:
        raise ValueError("exit policy grid differs from frozen preregistration")
    return result


def load_shadow_config(
    path: str | Path = PROJECT_ROOT / "shadow_config.yaml",
    job: str = RUNTIME_JOB,
    *,
    env: Mapping[str, str] | None = None,
) -> ShadowConfig:
    assert_shadow_boundary(env=env)
    if job != RUNTIME_JOB:
        raise ValueError(f"shadow job must be {RUNTIME_JOB}")
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    root = _mapping(raw, "config root")
    _require_keys(root, {"simulation_mode", "shadow"}, "config root")
    if root.get("simulation_mode") is not True:
        raise ValueError("Cherry shadow simulation_mode must remain true")
    shadow = _mapping(root.get("shadow"), "shadow")
    gamma_raw = _mapping(shadow.get("gamma"), "shadow.gamma")
    clob_raw = _mapping(shadow.get("clob"), "shadow.clob")
    transport_raw = _mapping(shadow.get("transport"), "shadow.transport")
    experiment_raw = _mapping(shadow.get("experiment"), "shadow.experiment")
    _require_keys(
        shadow,
        {
            "runtime_job", "data_contract", "preregistration_id",
            "cadence_minutes", "collection_budget_seconds", "gamma", "clob",
            "transport", "experiment",
        },
        "shadow",
    )
    _require_keys(
        gamma_raw,
        {
            "base_url", "page_size", "max_pages", "min_liquidity",
            "min_total_volume", "gamma_probability_min",
            "gamma_probability_max", "entry_hours_min", "entry_hours_max",
        },
        "shadow.gamma",
    )
    _require_keys(clob_raw, {"base_url", "max_books_per_run"}, "shadow.clob")
    _require_keys(
        transport_raw,
        {
            "connect_timeout_seconds", "read_timeout_seconds", "max_retries",
            "retry_base_seconds", "retry_max_seconds",
        },
        "shadow.transport",
    )
    _require_keys(
        experiment_raw,
        {
            "start_utc", "entry_end_utc", "followup_end_utc",
            "simulated_notional_usdc", "entry_bands", "exit_policies",
        },
        "shadow.experiment",
    )

    bands = _parse_bands(experiment_raw.get("entry_bands"))
    policies = _parse_policies(experiment_raw.get("exit_policies"))
    gamma = GammaSettings(
        base_url=str(gamma_raw.get("base_url") or "").rstrip("/"),
        page_size=_integer(gamma_raw.get("page_size"), "gamma.page_size"),
        max_pages=_integer(gamma_raw.get("max_pages"), "gamma.max_pages"),
        min_liquidity=_number(gamma_raw.get("min_liquidity"), "gamma.min_liquidity"),
        min_total_volume=_number(gamma_raw.get("min_total_volume"), "gamma.min_total_volume"),
        gamma_probability_min=_number(gamma_raw.get("gamma_probability_min"), "gamma_probability_min"),
        gamma_probability_max=_number(gamma_raw.get("gamma_probability_max"), "gamma_probability_max"),
        entry_hours_min=_number(gamma_raw.get("entry_hours_min"), "entry_hours_min"),
        entry_hours_max=_number(gamma_raw.get("entry_hours_max"), "entry_hours_max"),
    )
    clob = ClobSettings(
        base_url=str(clob_raw.get("base_url") or "").rstrip("/"),
        max_books_per_run=_integer(clob_raw.get("max_books_per_run"), "clob.max_books_per_run"),
    )
    transport = TransportSettings(
        connect_timeout_seconds=_number(transport_raw.get("connect_timeout_seconds"), "connect timeout"),
        read_timeout_seconds=_number(transport_raw.get("read_timeout_seconds"), "read timeout"),
        max_retries=_integer(transport_raw.get("max_retries"), "transport.max_retries"),
        retry_base_seconds=_number(transport_raw.get("retry_base_seconds"), "retry base"),
        retry_max_seconds=_number(transport_raw.get("retry_max_seconds"), "retry max"),
    )
    experiment = ExperimentSettings(
        start_utc=_utc(experiment_raw.get("start_utc"), "experiment.start_utc"),
        entry_end_utc=_utc(experiment_raw.get("entry_end_utc"), "experiment.entry_end_utc"),
        followup_end_utc=_utc(experiment_raw.get("followup_end_utc"), "experiment.followup_end_utc"),
        simulated_notional_usdc=_number(experiment_raw.get("simulated_notional_usdc"), "simulated notional"),
        entry_bands=bands,
        exit_policies=policies,
    )

    if str(shadow.get("runtime_job") or "") != RUNTIME_JOB:
        raise ValueError("runtime_job differs from registered shadow job")
    if str(shadow.get("data_contract") or "") != DATA_CONTRACT:
        raise ValueError("data_contract differs from registered shadow contract")
    if str(shadow.get("preregistration_id") or "") != PREREGISTRATION_ID:
        raise ValueError("preregistration_id differs from frozen identity")
    if _integer(shadow.get("cadence_minutes"), "cadence_minutes") != 5:
        raise ValueError("shadow cadence must remain five minutes")
    budget = _number(shadow.get("collection_budget_seconds"), "collection budget")
    if budget != 240:
        raise ValueError("collection budget must remain 240 seconds")
    if (
        gamma.base_url != "https://gamma-api.polymarket.com"
        or gamma.page_size != 100
        or gamma.max_pages != 1000
        or gamma.min_liquidity != 125000
        or gamma.min_total_volume != 5000
        or gamma.gamma_probability_min != 0.75
        or gamma.gamma_probability_max != 0.88
        or gamma.entry_hours_min != 0
        or gamma.entry_hours_max != 120
    ):
        raise ValueError("Gamma/Yellow universe envelope is frozen")
    if clob.base_url != "https://clob.polymarket.com" or clob.max_books_per_run != 160:
        raise ValueError("CLOB collection envelope is frozen")
    if not (
        0 < transport.connect_timeout_seconds <= 5
        and 0 < transport.read_timeout_seconds <= 15
        and 0 <= transport.max_retries <= 3
        and 0 <= transport.retry_base_seconds <= transport.retry_max_seconds <= 3
    ):
        raise ValueError("transport bounds exceed frozen safety limits")
    if (
        experiment.start_utc != FROZEN_START
        or experiment.entry_end_utc != FROZEN_ENTRY_END
        or experiment.followup_end_utc != FROZEN_FOLLOWUP_END
        or experiment.simulated_notional_usdc != 5
    ):
        raise ValueError("experiment dates/notional differ from preregistration")

    prereg_digest = verify_preregistration()
    source_digest = compute_strategy_source_digest()
    db_path = PROJECT_ROOT / "data" / RUNTIME_JOB / "trades_sim.db"
    evidence = {
        "simulation_mode": True,
        "runtime_job": RUNTIME_JOB,
        "data_contract": DATA_CONTRACT,
        "preregistration_id": PREREGISTRATION_ID,
        "preregistration_sha256": prereg_digest,
        "strategy_source_digest": source_digest,
        "cadence_minutes": 5,
        "collection_budget_seconds": budget,
        "gamma": asdict(gamma),
        "clob": asdict(clob),
        "transport": asdict(transport),
        "experiment": {
            **asdict(experiment),
            "start_utc": experiment.start_utc.isoformat(),
            "entry_end_utc": experiment.entry_end_utc.isoformat(),
            "followup_end_utc": experiment.followup_end_utc.isoformat(),
        },
    }
    config_hash = hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()
    return ShadowConfig(
        simulation_mode=True,
        runtime_job=RUNTIME_JOB,
        data_contract=DATA_CONTRACT,
        preregistration_id=PREREGISTRATION_ID,
        preregistration_sha256=prereg_digest,
        strategy_source_digest=source_digest,
        config_hash=config_hash,
        cadence_minutes=5,
        collection_budget_seconds=budget,
        gamma=gamma,
        clob=clob,
        transport=transport,
        experiment=experiment,
        db_path=db_path,
    )
