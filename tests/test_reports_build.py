import json

import pytest
import yaml

from polylab import db, registry, settings
from polylab.reports import build, render, slack

NOW = 1_790_100_000  # 2026-09-22 ~ KST evening
START = NOW - 3 * 3600


def write_variant(reg_dir, vid="watermelon-cat", mode="live", stake=5.0, account="cat", **extra):
    data = {"id": vid, "family": "watermelon", "hypothesis": "late favourites underpriced", "account": account,
            "mode": mode, "sports": ["soccer"], "stake_usdc": stake,
            "params": {"prob_min": 0.92, "take_profit": 0.99, "max_minute": 85},
            "bounds": {"prob_min": [0.88, 0.97, 0.01], "take_profit": [0.95, 0.995, 0.01]},
            "limits": {"max_positions": 20, "daily_loss_stop_usdc": 20}}
    data.update(extra)
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / f"{vid}.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def make_world(tmp_path, monkeypatch, n_settled=3, now=NOW):
    """core.db with one live soccer game, a strategy ledger and a registry dir."""
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path / "rt"))
    monkeypatch.setattr(settings, "SECRETS_DIR", tmp_path / "secrets")
    reg = tmp_path / "strategies"
    monkeypatch.setattr(registry, "REGISTRY_DIR", reg)
    paths = settings.paths()
    core = db.core(paths)
    core.execute("INSERT INTO games(game_key, sport, league, title, home_team, away_team, start_time, status,"
                 " first_seen, updated_at, source) VALUES('g1','soccer','EPL','Arsenal vs Chelsea','Arsenal',"
                 "'Chelsea',?, 'live', ?, ?, 'discover')", (START, START, START))
    core.execute("INSERT INTO markets(condition_id, game_key, market_type, question, updated_at)"
                 " VALUES('c1','g1','moneyline','Arsenal vs Chelsea',?)", (START,))
    core.execute("INSERT INTO tokens VALUES('t1','c1',0,'Arsenal','home')")
    core.execute("INSERT INTO price_bars VALUES('t1', ?, 'poll_mid', 0.95, ?)", (now // 60 * 60 - 60, now))
    core.execute("INSERT INTO game_states(game_key, ts, received_at, source, game_minute, home_score, away_score)"
                 " VALUES('g1', ?, ?, 'ws_sports', 71, 1, 0)", (now - 120, now - 120))
    core.execute("INSERT INTO job_runs(job, started_at, finished_at, ok) VALUES('polylab-tick', ?, ?, 1)",
                 (now - 60, now - 50))
    core.commit()
    write_variant(reg)
    s = db.strategy(paths, "watermelon-cat")
    s.execute("INSERT INTO param_versions VALUES(1, ?, ?, 5, 'live', NULL, 'init', NULL)",
              (now - 10 * 86400, json.dumps({"prob_min": 0.92})))
    s.execute("INSERT INTO param_versions VALUES(2, ?, ?, 5, 'live', NULL, 'autopilot', 'test')",
              (now - 3600, json.dumps({"prob_min": 0.93})))
    for i in range(n_settled):
        closed = now - 1800 - i * 3600
        pnl = 0.35 if i % 4 else -1.2
        s.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, league, game_key,"
                  " condition_id, token_id, outcome_label, opened_at, game_minute_at_entry, entry_price, shares,"
                  " cost_usdc, status, closed_at, exit_reason, exit_price, proceeds_usdc, realized_pnl, settlement)"
                  " VALUES(?, 'live', 2, 5, 'soccer', 'EPL', 'g1', 'c1', 't1', 'Arsenal', ?, 70, 0.93, 5.37, 5.0,"
                  " 'resolved', ?, ?, 1.0, ?, ?, 'resolution')",
                  (f"p{i}", closed - 1200, closed, "resolution_win" if pnl > 0 else "resolution_loss", 5 + pnl, pnl))
        s.execute("INSERT INTO orders(intent_id, position_id, created_at, mode, side, token_id, condition_id,"
                  " order_type, usdc_amount, status, updated_at) VALUES(?,?,?, 'live','BUY','t1','c1','FOK',5,"
                  " 'confirmed', ?)", (f"i{i}", f"p{i}", closed - 1200, closed - 1200))
        s.execute("INSERT INTO fills VALUES(?,?,?, 'BUY', 0.93, 5.37, 0.01, 'CONFIRMED', NULL)",
                  (f"f{i}", f"i{i}", closed - 1195))
    # open position + an unfilled order in the window
    s.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, league, game_key,"
              " condition_id, token_id, outcome_label, opened_at, entry_price, shares, cost_usdc, status)"
              " VALUES('open1','live',2,5,'soccer','EPL','g1','c1','t1','Arsenal',?,0.93,5.37,5.0,'open')",
              (now - 600,))
    s.execute("INSERT INTO orders(intent_id, position_id, created_at, mode, side, token_id, condition_id,"
              " order_type, usdc_amount, limit_price, status, updated_at)"
              " VALUES('iu', NULL, ?, 'live','BUY','t1','c1','FOK',5,0.94,'failed',?)", (now - 300, now - 300))
    s.execute("INSERT INTO stake_events(ts, from_usdc, to_usdc, from_mode, to_mode, reason) VALUES(?,?,?,?,?,?)",
              (now - 20 * 86400, None, 5, None, "live", "init"))
    s.commit()
    return paths, reg


def test_daily_report_sections_and_money_rules(tmp_path, monkeypatch):
    paths, reg = make_world(tmp_path, monkeypatch)
    rep = build.build("daily", paths, now=NOW, slot="evening", use_jenkins=False)
    v = rep["variants"][0]
    assert v["live"]["trades"]["all"] == 3
    assert rep["totals"]["open_positions"] == 1
    # unrealised pnl shown separately, never in realised totals
    assert rep["open_positions"][0]["unrealized_pnl"] == pytest.approx(5.37 * 0.95 - 5.0, abs=1e-3)
    assert rep["totals"]["all"] == pytest.approx(v["live"]["pnl"]["all"])
    statuses = {t["status"] for t in rep["transactions"]}
    assert {"CONFIRMED", "UNFILLED", "RESOLVED"} <= statuses
    assert rep["tx_by_variant"]["watermelon-cat"]["unfilled"] == 1
    lad = rep["variants"][0]["ladder"]
    assert lad["status"] == "hold" and lad["trades_at_tier"] == 3 and lad["needed"] == 20
    md = render.render(rep)
    for heading in ("## 요약", "## 전략 변형 현황", "## 지난 24시간 거래 내역", "## 보유 포지션", "## 데이터 수집 상태",
                    "## 연구 하이라이트", "## 변경 사항", "## AI 회고"):
        assert heading in md
    assert "Arsenal vs Chelsea" in md and "정산 승" in md
    assert "prob_min 0.92→0.93" in md  # param change within window
    assert render.render(rep) == md  # deterministic


def test_no_token_note_in_report(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    rep = build.build("daily", paths, now=NOW, slot="dawn", use_jenkins=False)
    rep["ai"] = {"ran": False, "reason": "토큰 없음"}
    assert "AI 회고 생략(토큰 없음)" in render.render(rep)


def test_windows_and_names():
    assert build.report_name("monthly", 1_790_100_000, None)[0] == "2026-08"
    lo, hi = build.window("monthly", 1_790_100_000)
    assert hi - lo == 31 * 86400
    assert build.report_name("daily", NOW, "dawn")[0].endswith("-dawn")
    assert build.report_name("weekly", NOW, None)[0].startswith("2026-W")


def test_catchup_since_extends_window(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    rep = build.build("daily", paths, now=NOW, slot="dawn", use_jenkins=False, since=NOW - 2 * 86400)
    assert rep["window"]["since"] == build.C.iso(NOW - 2 * 86400)


def test_slack_scrub_and_blocks(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    rep = build.build("daily", paths, now=NOW, slot="morning", use_jenkins=False)
    text, blocks = slack.report_blocks(rep, "https://poly.zowoo.uk/reports/daily/x")
    assert blocks[0]["type"] == "header" and "poly.zowoo.uk" in json.dumps(blocks)
    assert slack.scrub("funder 0x" + "ab" * 20 + " ok") == "funder [REDACTED] ok"
    sent = []
    assert slack.post_report(rep, "u", poster=lambda t, b: sent.append((t, b)) or True)
    assert sent


def test_slack_post_prefers_bot_then_webhook(monkeypatch):
    calls = []

    class R:
        def __init__(self, ok, body):
            self.ok, self._b, self.status_code = ok, body, 200 if ok else 500

        def json(self):
            return self._b

    def fake_post(url, **kw):
        calls.append(url)
        return R(True, {"ok": False, "error": "channel_not_found"}) if "slack.com/api" in url else R(True, {})
    monkeypatch.setattr(slack.requests, "post", fake_post)
    ok = slack.post("hi", env={"SLACK_BOT_TOKEN": "x", "SLACK_CHANNEL_ID": "C1", "SLACK_WEBHOOK_URL": "https://h"})
    assert ok and calls == ["https://slack.com/api/chat.postMessage", "https://h"]
