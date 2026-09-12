"""Project same-cohort recorder daily shards without merging source databases."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime
from heapq import merge
from pathlib import Path

CONTRACT = "sports-price-recorder-1m-v1"
PRIMARY_RUNTIME = "coconut-sports-recorder-1m-v1"
REPLICA_RUNTIME = "coconut-sports-recorder-silver-1m-v1"
RUNTIMES = frozenset({PRIMARY_RUNTIME, REPLICA_RUNTIME})
RUNTIME = PRIMARY_RUNTIME


def export_group(exporter, reader_path, sources, output, start, end, index, data_root):
    spec = importlib.util.spec_from_file_location("sports_recorder_reader", reader_path)
    if spec is None or spec.loader is None:
        raise ValueError("통합 수집기 reader가 없습니다.")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    days, paths, before, constituents, path_days = set(), [], {}, [], {}
    identities = {(s["source"], s["jenkins_job"], s["runtime_job"]) for s in sources}
    runtime = sources[0]["runtime_job"]
    if len(identities) != 1 or runtime not in RUNTIMES:
        raise ValueError("같은 수집기 runtime의 일별 자료만 연결할 수 있습니다.")
    for source in sources:
        path = Path(source["local_path"]).resolve()
        if not path.is_relative_to(data_root.resolve()) or not path.is_file():
            raise ValueError("로컬 수집기 DB 경로가 잘못됐습니다.")
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise ValueError("동기화된 snapshot만 읽을 수 있습니다.")
        digest = exporter.sha256(path)
        if digest != source["local_sha256"]:
            raise ValueError("수집기 DB checksum이 catalog와 다릅니다.")
        with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            records = connection.execute("SELECT * FROM collection_contracts").fetchall()
            if len(records) != 1:
                raise ValueError("수집기 계약이 하나가 아닙니다.")
            contract = dict(records[0])
            if contract["data_contract"] != CONTRACT or contract["runtime_job"] != runtime:
                raise ValueError("과거 Coconut epoch는 이 수집기와 연결할 수 없습니다.")
            day = contract["database_utc_date"]
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError("수집기 UTC 날짜가 표준 형식이 아닙니다.")
            if day in days:
                raise ValueError("같은 UTC 날짜의 DB가 두 개입니다. 중복 snapshot을 제외하세요.")
            for cycle in connection.execute("SELECT slot_utc FROM cycles"):
                slot = datetime.fromisoformat(cycle[0].replace("Z", "+00:00"))
                if slot.tzinfo is None or slot.astimezone(UTC).date().isoformat() != day:
                    raise ValueError("수집기 slot의 UTC 날짜가 shard 날짜와 다릅니다.")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("수집기 SQLite 무결성 검사 실패")
            days.add(day)
        paths.append(path)
        path_days[path] = day
        before[path] = digest
        constituents.append(
            {
                "source_key": source["source_key"],
                "sha256": digest,
                "database_utc_date": day,
                "basename": path.name,
                "synced_at": source["synced_at"],
            }
        )
    by_basename = {path.name: path for path in paths}
    if len(by_basename) != len(paths):
        raise ValueError("같은 파일 이름의 수집기 source가 둘입니다. 출처를 먼저 구분하세요.")
    carryover_verified, carryover_boundary = 0, 0
    for path in paths:
        for carry in reader.carryovers(path, before[path]):
            name = carry["source_shard"]
            if not (
                name.startswith("trades_sim_")
                and name.endswith(".db")
                and len(name) == len("trades_sim_20260908.db")
            ):
                raise ValueError("경기 인계의 부모는 동결된 일별 shard여야 합니다.")
            parent_day = date.fromisoformat(name[len("trades_sim_") : -3]).isoformat()
            if parent_day >= path_days[path]:
                raise ValueError("경기 인계의 부모 날짜가 자식보다 앞서야 합니다.")
            parent = by_basename.get(carry["source_shard"])
            if parent is None:
                carryover_boundary += 1
                continue
            if parent == path or path_days[parent] != parent_day:
                raise ValueError("경기 인계 부모의 파일·UTC 날짜가 일치하지 않습니다.")
            with sqlite3.connect(parent.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                original = conn.execute(
                    "SELECT * FROM tracked_events WHERE event_id=?", (carry["event_id"],)
                ).fetchone()
                if original is None or dict(original) != json.loads(carry["state_json"]):
                    raise ValueError("일별 DB의 경기 인계 근거가 일치하지 않습니다.")
            carryover_verified += 1
    source = {
        "id": hashlib.sha256(json.dumps(sorted(identities)).encode()).hexdigest(),
        "strategy": "golden-coconut",
        "jenkins_job": sources[0]["jenkins_job"],
        "runtime_job": runtime,
        "collector_role": "PRIMARY" if runtime == PRIMARY_RUNTIME else "REPLICA",
        "data_contract": CONTRACT,
        "synced_at": min(s["synced_at"] for s in sources),
        "constituents": sorted(constituents, key=lambda x: x["database_utc_date"]),
        "selected_parent_carryovers_verified": carryover_verified,
        "carryovers_before_selected_history": carryover_boundary,
    }
    configs, runs, terminals, seen = {}, {}, defaultdict(list), set()

    def rows():
        streams = [
            reader.iter_rows(path, before[path], start=start, end=end, include_depth=True)
            for path in paths
        ]
        for raw in merge(*streams, key=lambda r: (exporter.timestamp(r["timestamp"]), r["id"])):
            row = dict(raw)
            if row["id"] in seen:
                raise ValueError("일별 DB 사이에 중복 관측이 있습니다.")
            seen.add(row["id"])
            config = row["config"]
            if config.get("observation_mode") != "SCHEDULED":
                raise ValueError("PROBE·관측 모드 미상 자료는 정규 경기 수집에 포함하지 않습니다.")
            if (
                config["job_name"] != runtime
                or config.get("simulation_mode") is not True
                or config.get("lifecycle_mode") != "archive_only"
                or config.get("data_contract") != CONTRACT
            ):
                raise ValueError("관측 runtime이 DB 계약과 다릅니다.")
            normalized_config = {
                "config_hash": row["config_hash"],
                "strategy_source_digest": row["strategy_source_digest"],
                "mode": "sim",
                "data_contract": CONTRACT,
                "observation_mode": "SCHEDULED",
            }
            row["resolved_cohort"] = normalized_config
            run = {
                "config_hash": row["config_hash"],
                "mode": "sim",
                "job_name": runtime,
                "strategy_source_digest": row["strategy_source_digest"],
                "status": "SUCCESS" if row["run_status"] == "SUCCEEDED" else "FAILED",
                "started_at": row.get("reference_at") or row["timestamp"],
                "finished_at": row["published_at"],
            }
            if row["run_id"] in runs:
                prior = runs[row["run_id"]]
                if any(prior[k] != run[k] for k in run if k != "started_at"):
                    raise ValueError("한 실행의 cohort·종료 근거가 일치하지 않습니다.")
                run["started_at"] = min(prior["started_at"], run["started_at"])
            runs[row["run_id"]] = run
            row["title"] = row.get("title") or row["event_id"]
            row["slot"] = row.get("slot") or row["id"]
            row["book"] = row.get("book") or {}
            row["point_in_time_market_fields"] = row.get("market_fields") or {}
            row["event_set_complete"] = int(row.get("raw_point_in_time_identity_proven") is True)
            # Preserve the producer's display proof without upgrading it to an
            # independent venue-role audit. The raw outcome label stays primary.
            row.update(
                legacy_result_kind=row.get("result_kind"),
                legacy_role_semantics="RECORDER_SOURCE_TEAM_IDENTITY",
                role_evidence_scope="RECORDER_SAME_OBSERVATION",
                role_verification_status="PRODUCER_REPORTED_EXPLICIT_ORDERING",
                role_evidence={"source_contract": CONTRACT, "observation_id": row["id"]},
            )
            yield row

    for path in paths:
        for terminal in reader.iter_terminals(path, before[path]):
            if exporter.timestamp(terminal["published_at"]) < exporter.timestamp(
                end
            ) and exporter.timestamp(terminal["observed_at"]) < exporter.timestamp(end):
                terminals[(terminal["condition_id"], terminal["token_id"])].append(
                    {
                        "payout": terminal["payout"],
                        "observed_at": terminal["observed_at"],
                        "source": CONTRACT,
                        "config_hash": terminal.get("config_hash"),
                        "strategy_source_digest": terminal.get("strategy_source_digest"),
                        "job_name": terminal.get("job_name"),
                        "observation_mode": terminal.get("observation_mode"),
                    }
                )
    exporter.export_rows(
        source, output, start, end, index, rows(), configs, runs, terminals, include_depth=True
    )
    for path in paths:
        if exporter.sha256(path) != before[path]:
            raise ValueError("일별 수집기 자료가 읽는 도중 바뀌었습니다.")
