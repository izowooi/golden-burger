"""Track 2 manual ledger: report section, markdown, dashboard JSON, attention rules, predictions, publish guard."""

import pytest
from test_manual_ledger import ADDR, KICK, T0, FakeClient, world  # noqa: F401
from test_publish_snapshot import assert_shape, contract_examples

from polylab import settings
from polylab.autopilot import attention, gitops
from polylab.manual import predictions, report, sync
from polylab.publish import snapshot

NOW = T0 + 10_000


def synced(paths, acct):
    sync.sync_account(acct, paths, client=FakeClient(), now=NOW)


def test_report_section_track2_money(world):  # noqa: F811
    paths, acct = world
    synced(paths, acct)
    sec = report.report_section(paths, NOW - 86400, NOW, "daily", NOW)
    t = sec["totals"]
    # Track 2 = game-linked: a (redeemed win), b (win), c (loss), g (loss); h open; d/e non-sports; f quarantined
    assert (t["settled"], t["wins"], t["losses"], t["sold"]) == (4, 2, 2, 0)
    assert t["win_rate"] == 0.5
    expected = (800 - 761.9) + (543.915 - 502.01846) - 90.45 - 40.4
    assert t["realized_pnl"]["all"] == pytest.approx(expected, abs=1e-3)
    assert t["open"] == 1 and t["unrealized_pnl"] == pytest.approx(95 - 92.368, abs=1e-3)
    assert t["open_cost_usdc"] == pytest.approx(92.37, abs=0.01) and t["unrealized_unknown"] == 0
    assert t["fees_unknown"] == 0 and t["fees_usdc"] == pytest.approx(1.9 + 2.0185 + 0.45 + 0.4, abs=1e-3)
    assert sec["other"] == {"settled": 2, "realized_pnl": pytest.approx(1.12), "open": 0}
    assert sec["quarantined"] == 1
    acc = sec["accounts"][0]
    assert acc["account"] == "연구자A" and acc["credits_usdc"] == {"MAKER_REBATE": 0.5}   # rebate outside P&L
    assert acc["bankroll_usdc"] == 1000.0
    bands = {b["band"]: b for b in sec["by_stake"]}
    assert bands["600+"]["n"] == 1 and bands["300–600"]["n"] == 1 and bands["50–150"]["n"] == 1
    sides = {x["side"] for x in sec["trades"]}
    assert {"BUY", "RESOLVE"} <= sides
    assert all(ADDR not in str(v) for v in sec.values())


def test_render_and_snapshot(world):  # noqa: F811
    paths, acct = world
    synced(paths, acct)
    sec = report.report_section(paths, NOW - 7 * 86400, NOW, "weekly", NOW)
    md = "\n".join(report.render_lines(sec, "weekly"))
    assert "## 수동 AI 베팅 (트랙 2)" in md and "연구자A" in md and "O/U 0.5 Over" in md
    assert "AI 예측 vs 결과" not in md                    # no predictions in the window -> no subsection
    snap = report.snapshot(sec, "2026-10-01T00:00:00Z")
    assert_shape(contract_examples()["latest/manual.json"], snap)
    assert ADDR[2:] not in str(snap).lower()


def test_no_ledgers_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path / "rt"))
    monkeypatch.setattr(settings, "SECRETS_DIR", tmp_path / "nosecrets")
    paths = settings.paths()
    assert settings.watch_accounts() == []
    out = sync.run(paths, client=FakeClient())
    assert len(out) == 1 and "predictions" in out[0]
    assert report.report_section(paths, 0, NOW, "daily", NOW) is None
    assert report.render_lines(None) == []
    assert report.snapshot(None, "x")["accounts"] == []
    assert report.attention_items({"manual": None}, "r") == []


def test_attention_loss_and_drawdown(world):  # noqa: F811
    paths, acct = world
    acct = settings.WatchAccount(alias="owner", address=ADDR, label="연구자A", bankroll_usdc=600.0)
    synced(paths, acct)
    sec = report.report_section(paths, NOW - 86400, NOW, "daily", NOW + 3600)
    ids = [a["id"] for a in sec["alerts"]]
    assert sum(i.startswith("manual_loss:") for i in ids) == 2
    # Track 2 realised -50.81 (the +1.12 non-game bet is excluded) > -60 (10% of 600) -> no drawdown item yet
    assert not any(i.startswith("manual_drawdown") for i in ids)
    acct2 = settings.WatchAccount(alias="other", address=ADDR, bankroll_usdc=300.0)
    synced(paths, acct2)
    sec = report.report_section(paths, NOW - 86400, NOW, "daily", NOW)
    assert "manual_drawdown:other" in [a["id"] for a in sec["alerts"]]
    items = attention._manual_items({"manual": sec}, "reports/daily/x.md")
    sev = {i["id"]: i["severity"] for i in items}
    assert sev["manual_drawdown:other"] == "warn"
    assert all(v == "info" for k, v in sev.items() if k.startswith("manual_loss:"))
    later = report.report_section(paths, NOW, NOW + 10 * 86400, "daily", NOW + 10 * 86400)
    assert not any(a["id"].startswith("manual_loss:") for a in later["alerts"])   # lookback over -> auto-resolve


PRED_MD = """# 2026-09-21 예측

| 경기 | 엔진(claude/chatgpt) | P(0:0) | 순위 | 메모 |
|---|---|---|---|---|
| Korea Republic vs Uruguay | Claude | 7% | 1 | 홈 강세 |
| Spain - Italy | GPT-5 | 0.11 | 2 | |
| 그리스 vs 네덜란드 | chatgpt | 9 | 3 | 한글 이름 |
|  | claude | 5% | 4 | 빈 경기 |
"""


def test_predictions_parse_and_match(world, tmp_path):  # noqa: F811
    paths, acct = world
    synced(paths, acct)
    d = tmp_path / "preds"
    d.mkdir()
    (d / "2026-09-21.md").write_text(PRED_MD)
    (d / "README.md").write_text("| 경기 | x |\n|---|---|\n| a | b |\n")
    assert predictions.sync(paths, d) == {"files": 1, "rows": 3, "skipped": 1}
    rows = predictions.load(paths)
    assert [(r["engine"], r["p00"], r["rank"]) for r in rows] == [("claude", 0.07, 1), ("chatgpt", 0.11, 2),
                                                                 ("chatgpt", 0.09, 3)]
    sec = report.report_section(paths, NOW - 7 * 86400, NOW, "weekly", NOW)
    pred = sec["predictions"]
    games = {r["game"]: r for r in pred["rows"]}
    assert games["Korea Republic vs Uruguay"]["zero_zero"] is False
    assert games["Korea Republic vs Uruguay"]["market_p00_at_entry"] == pytest.approx(1 - 0.9192612816, abs=1e-4)
    assert games["Spain - Italy"]["zero_zero"] is True and games["Spain - Italy"]["result"] == "resolved_loss"
    assert [u["game"] for u in pred["unmatched"]] == ["그리스 vs 네덜란드"]
    md = "\n".join(report.render_lines(sec, "weekly"))
    assert "거래와 연결되지 않은 예측 1건" in md
    later = report.report_section(paths, NOW + 20 * 86400, NOW + 27 * 86400, "weekly", NOW + 27 * 86400)
    assert later["predictions"] is None                       # weekly table only covers its own window


def test_predictions_parse_tolerant():
    rows, skipped = predictions.parse_text("no table here\n| a | b |\n|--|--|\n| x | y |\n")
    assert rows == [] and skipped == 0
    assert predictions.parse_p("약 12.5 %") == 0.125 and predictions.parse_p("abc") is None
    assert predictions.same_game("Korea vs Uruguay", "Korea Republic vs. Uruguay")
    assert not predictions.same_game("Korea vs Japan", "Korea Republic vs. Uruguay")


def test_watch_address_is_a_publish_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SECRETS_DIR", tmp_path)
    (tmp_path / "watch.env").write_text(f"WATCH_OWNER__ADDRESS={ADDR}\n")
    assert ADDR[2:].lower() in gitops.secret_values()
    storage = snapshot.Storage("https://example.invalid", "k")
    with pytest.raises(RuntimeError, match="secret"):
        storage.upload("latest/manual.json", f'{{"x": "{ADDR.upper()}"}}'.encode())


def test_build_and_render_include_manual(world):  # noqa: F811
    from polylab.reports import build, render
    paths, acct = world
    synced(paths, acct)
    r = build.build("daily", paths, now=NOW, slot="morning", use_jenkins=False, variants=[])
    assert r["manual"]["totals"]["settled"] == 4
    md = render.render(r)
    assert "## 수동 AI 베팅 (트랙 2)" in md
    objs = snapshot.build_objects(paths, now=NOW, use_jenkins=False, variants=[])
    assert objs["latest/manual.json"]["totals"]["settled"] == 4


def test_manual_error_keeps_attention_items_open(world):  # noqa: F811
    from polylab.reports import build
    paths, acct = world
    rep = build.build("daily", paths, now=NOW, slot="morning", use_jenkins=False, variants=[])
    _, fams = attention.rule_items(rep, kind="daily", now=NOW, paths=paths, ai_enabled=False)
    assert "manual" in fams
    rep["manual"] = {"error": "OperationalError: boom"}
    items, fams = attention.rule_items(rep, kind="daily", now=NOW, paths=paths, ai_enabled=False)
    assert "manual" not in fams and not [i for i in items if i["id"].startswith("manual")]
