"""Single-cycle accountless research runtime."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import shutil
from threading import Lock
from typing import Iterator
from uuid import uuid4

from .api.clob_client import ClobClient
from .api.gamma_client import GammaClient
from .api.sports_client import SportsClockClient
from .collector import Collector
from .config import BotConfig, assert_no_credentials, league_registry_payload
from .db.repository import ResearchRepository
from .run_audit import ResearchRunAudit
from .utils.retry import CycleBudget, PublicJsonTransport, iso_utc


@contextmanager
def exclusive_job_run_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another Golden Watermelon cycle is already running") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ResearchBot:
    def __init__(self, config: BotConfig) -> None:
        assert_no_credentials()
        self.config = config

    def run(self) -> dict[str, object]:
        assert_no_credentials()
        budget = CycleBudget.start(
            network_seconds=self.config.trading.network_budget_seconds,
            cycle_seconds=self.config.trading.cycle_budget_seconds,
        )
        run_id = uuid4().hex
        data_dir = self.config.db_path.parent
        usage = shutil.disk_usage(data_dir.parent if data_dir.parent.exists() else self.config.db_path.parents[2])
        storage = self.config.trading.storage
        used_ratio = (usage.total - usage.free) / usage.total
        if usage.free < storage.min_free_gib * 1024**3 or used_ratio >= storage.stop_used_ratio:
            raise RuntimeError("storage safety gate rejected this run")
        with exclusive_job_run_lock(data_dir / ".run.lock"):
            budget.assert_cycle_available("database preflight")
            repository = ResearchRepository(
                self.config.db_path,
                busy_timeout_ms=storage.busy_timeout_ms,
                data_contract=self.config.trading.data_contract,
                schema_profile=self.config.trading.schema_profile,
                universe_profile=self.config.trading.universe_profile,
                classifier_version=self.config.trading.classifier_version,
                league_mapping_sha256=self.config.trading.league_mapping_sha256,
                league_mapping_json=json.dumps(
                    league_registry_payload(
                        self.config.trading.gamma.league_mapping,
                        self.config.trading.gamma.cup_mapping,
                        self.config.trading.gamma.direct_sport_mapping,
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            repository.record_league_registry({
                "league_mapping_sha256": self.config.trading.league_mapping_sha256,
                "classifier_version": self.config.trading.classifier_version,
                "universe_profile": self.config.trading.universe_profile,
                "mapping_json": json.dumps(
                    league_registry_payload(
                        self.config.trading.gamma.league_mapping,
                        self.config.trading.gamma.cup_mapping,
                        self.config.trading.gamma.direct_sport_mapping,
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "first_seen_at": iso_utc(),
            })
            repository.record_config({
                "config_hash": self.config.config_hash,
                "strategy_source_digest": self.config.trading.strategy_source_digest,
                "preregistration_sha256": self.config.trading.experiment.preregistration_sha256,
                "job_name": self.config.job_name, "mode": "sim",
                "config_json": json.dumps(self.config.redacted_dict(), sort_keys=True, separators=(",", ":")),
                "first_seen_at": iso_utc(),
            })
            audit = ResearchRunAudit(repository, self.config, run_id)
            audit.start()
            transport: PublicJsonTransport | None = None
            try:
                gamma_config = self.config.trading.gamma
                receipt_lock = Lock()

                def record_api_request(row) -> None:
                    # Five family workers use separate HTTP sessions.  Serialize
                    # their short SQLite receipt commits for deterministic,
                    # contention-free evidence writes.
                    with receipt_lock:
                        repository.record_api_request(row)

                transport = PublicJsonTransport(
                    connect_timeout_seconds=gamma_config.connect_timeout_seconds,
                    read_timeout_seconds=gamma_config.read_timeout_seconds,
                    max_retries=gamma_config.max_retries,
                    retry_base_seconds=gamma_config.retry_base_seconds,
                    retry_max_seconds=gamma_config.retry_max_seconds,
                    receipt_sink=record_api_request,
                    budget=budget,
                )
                collector = Collector(
                    self.config, repository,
                    GammaClient(gamma_config, transport),
                    ClobClient(self.config.trading.orderbook, transport),
                    SportsClockClient(
                        self.config.trading.sports_feed,
                        record_api_request,
                    ),
                )
                result = collector.collect(run_id, budget=budget)
                budget.assert_cycle_available("post-collection persistence")
                metric = repository.record_storage_metric(run_id)
                result["database_check"] = repository.scheduled_database_check(
                    run_id
                )
                result["db_bytes"] = metric["db_bytes"]
                result["runtime_budget"] = budget.evidence()
                audit.succeed(result)
                return {"run_id": run_id, **result}
            except BaseException as error:
                audit.fail(error)
                raise
            finally:
                if transport is not None:
                    transport.close()
