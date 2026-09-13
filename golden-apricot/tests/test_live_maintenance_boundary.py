from types import SimpleNamespace


def test_live_bot_never_runs_compact_maintenance_in_one_minute_cycle(monkeypatch):
    import polybot.bot as bot_module

    observed = {}

    def fake_init_database(path, requirements, **kwargs):
        observed.update(path=path, requirements=requirements, **kwargs)
        return SimpleNamespace(kw={})

    monkeypatch.setattr(bot_module, "init_database", fake_init_database)
    monkeypatch.setattr(bot_module, "GammaClient", lambda **kwargs: object())
    monkeypatch.setattr(bot_module, "ClobClientWrapper", lambda *args, **kwargs: object())
    config = SimpleNamespace(
        simulation_mode=False,
        db_path="live.db",
        api=object(),
        job_name="apricot-live-test",
        trading=SimpleNamespace(
            max_snapshot_gap_minutes=2.0,
            archive=SimpleNamespace(retention_days=60),
            sport_family="mlb",
            sport_profile_version="profile-v1",
            yes_only_mode=False,
            strategy_source_digest="a" * 64,
            preregistration_sha256="b" * 64,
            lifecycle_mode="active",
        ),
    )

    bot_module.PolymarketBot(config)

    assert observed["maintenance_on_start"] is False
    assert observed["enable_research_raw"] is False
