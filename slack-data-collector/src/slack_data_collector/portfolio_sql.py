from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def export_upsert_sql(
    portfolio_directory: Path, sql_directory: Path, batch_size: int = 100
) -> list[Path]:
    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다.")
    accounts = json.loads(
        (portfolio_directory / "algorithm_accounts.json").read_text(encoding="utf-8")
    )
    totals = _read_json_lines(portfolio_directory / "portfolio_totals.jsonl")
    balances = _read_json_lines(portfolio_directory / "algorithm_balances.jsonl")
    sql_directory.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    accounts_path = sql_directory / "10_algorithm_accounts.sql"
    accounts_path.write_text(_accounts_sql(accounts), encoding="utf-8")
    paths.append(accounts_path)

    for index, batch in enumerate(_batches(totals, batch_size), start=1):
        path = sql_directory / f"20_portfolio_totals_{index:03d}.sql"
        path.write_text(_totals_sql(batch), encoding="utf-8")
        paths.append(path)

    for index, batch in enumerate(_batches(balances, batch_size), start=1):
        path = sql_directory / f"30_algorithm_balances_{index:03d}.sql"
        path.write_text(_balances_sql(batch), encoding="utf-8")
        paths.append(path)
    return paths


def _accounts_sql(records: list[dict[str, Any]]) -> str:
    values = ",\n".join(
        "("
        + ", ".join(
            [
                _quote(record["account_id"]),
                _quote(record["jenkins_name"]),
                _quote(record["algorithm_code"]),
                _number_or_null(record["instance_no"]),
                str(int(record["sort_order"])),
            ]
        )
        + ")"
        for record in records
    )
    return f"""insert into public.pb_algorithm_accounts
  (account_id, jenkins_name, algorithm_code, instance_no, sort_order)
values
{values}
on conflict (account_id) do update set
  jenkins_name = excluded.jenkins_name,
  algorithm_code = excluded.algorithm_code,
  instance_no = excluded.instance_no,
  sort_order = excluded.sort_order;
"""


def _totals_sql(records: list[dict[str, Any]]) -> str:
    values = ",\n".join(
        "("
        + ", ".join(
            [
                _quote(record["report_date"]),
                _numeric(record["total_value"]),
                _numeric(record["position_value"]),
                _numeric(record["cash_value"]),
                _quote(record["currency"]),
                _quote(record["reported_at"]),
                _numeric(record["source_message_ts"]),
            ]
        )
        + ")"
        for record in records
    )
    return f"""insert into public.pb_daily_portfolio_totals
  (report_date, total_value, position_value, cash_value, currency, reported_at, source_message_ts)
values
{values}
on conflict (report_date) do update set
  total_value = excluded.total_value,
  position_value = excluded.position_value,
  cash_value = excluded.cash_value,
  currency = excluded.currency,
  reported_at = excluded.reported_at,
  source_message_ts = excluded.source_message_ts,
  updated_at = now()
where excluded.source_message_ts >= public.pb_daily_portfolio_totals.source_message_ts;
"""


def _balances_sql(records: list[dict[str, Any]]) -> str:
    values = ",\n".join(
        "("
        + ", ".join(
            [
                _quote(record["report_date"]),
                _quote(record["account_id"]),
                _numeric(record["total_value"]),
                _numeric(record["position_value"]),
                _numeric(record["cash_value"]),
                _quote(record["currency"]),
                _quote(record["reported_at"]),
                _numeric(record["source_message_ts"]),
            ]
        )
        + ")"
        for record in records
    )
    return f"""insert into public.pb_daily_algorithm_balances
  (report_date, account_id, total_value, position_value, cash_value, currency, reported_at, source_message_ts)
values
{values}
on conflict (report_date, account_id) do update set
  total_value = excluded.total_value,
  position_value = excluded.position_value,
  cash_value = excluded.cash_value,
  currency = excluded.currency,
  reported_at = excluded.reported_at,
  source_message_ts = excluded.source_message_ts,
  updated_at = now()
where excluded.source_message_ts >= public.pb_daily_algorithm_balances.source_message_ts;
"""


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _batches(
    records: list[dict[str, Any]], batch_size: int
) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(records), batch_size):
        yield records[start : start + batch_size]


def _quote(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _numeric(value: Any) -> str:
    text = str(value)
    if not text or any(character not in "+-0123456789." for character in text):
        raise ValueError(f"잘못된 numeric 값입니다: {value!r}")
    return text


def _number_or_null(value: Any) -> str:
    return "null" if value is None else str(int(value))
