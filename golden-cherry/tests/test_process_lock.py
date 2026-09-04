from types import SimpleNamespace

import polybot.bot as bot_module
from polybot.bot import PolymarketBot
from polybot.utils.process_lock import DatabaseRunLock


def test_database_run_lock_is_nonblocking_and_db_scoped(tmp_path):
    db_a = tmp_path / "a" / "trades.db"
    db_b = tmp_path / "b" / "trades.db"

    with DatabaseRunLock(db_a) as first:
        assert first.acquired is True
        assert first.owner["pid"]
        with DatabaseRunLock(db_a) as overlap:
            assert overlap.acquired is False
            assert overlap.owner["pid"] == first.owner["pid"]
        with DatabaseRunLock(db_b) as independent:
            assert independent.acquired is True

    with DatabaseRunLock(db_a) as after_release:
        assert after_release.acquired is True


def test_busy_run_skips_before_audit_or_api_calls(monkeypatch, tmp_path):
    class BusyLock:
        def __init__(self, db_path):
            self.path = tmp_path / "trades.db.run.lock"
            self.acquired = False
            self.owner = {
                "pid": 4321,
                "acquired_at": "2026-09-04T00:00:00+00:00",
            }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(bot_module, "DatabaseRunLock", BusyLock)
    monkeypatch.setattr(
        bot_module.RunAudit,
        "start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("busy run must not create audit rows")
        ),
    )
    bot = object.__new__(PolymarketBot)
    bot.config = SimpleNamespace(
        db_path=tmp_path / "trades.db",
        job_name="default",
    )
    bot.gamma = SimpleNamespace(
        sweep_attestations=SimpleNamespace(
            clear=lambda: (_ for _ in ()).throw(AssertionError("API path touched"))
        )
    )
    bot.clob = SimpleNamespace(
        reconcile_order_ledger=lambda: (_ for _ in ()).throw(
            AssertionError("reconciliation path touched")
        )
    )

    result = bot.run()

    assert result["skipped"] is True
    assert result["skip_reason"] == "db_process_lock_busy"
    assert result["lock_owner_pid"] == 4321
