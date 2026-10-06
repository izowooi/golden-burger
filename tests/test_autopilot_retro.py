import json

import pytest

import yaml
from test_reports_build import NOW, make_world

from polylab import db
from polylab.autopilot import retro
from polylab.autopilot.runner import RunResult


class FakeEngine:
    def __init__(self, name, proposal=None, narrative="## 회고\n좋음", ok=True, available=True, review=None):
        self.name, self.proposal, self.narrative, self.ok, self._avail, self.review = \
            name, proposal, narrative, ok, available, review
        self.calls = []

    def available(self):
        return (True, "") if self._avail else (False, "토큰 없음")

    def run(self, prompt, cwd, timeout):
        self.calls.append(prompt[:40])
        if not self.ok:
            return RunResult(False, "usage limit reached", 1, True)
        if prompt.startswith("# polylab 주간 제안 second opinion"):
            (cwd / "review.json").write_text(json.dumps(self.review))
            return RunResult(True)
        assert (cwd / "MANIFEST.md").exists() and (cwd / "bounds.json").exists()
        (cwd / "narrative.md").write_text(self.narrative)
        (cwd / "proposal.json").write_text(json.dumps(self.proposal) if isinstance(self.proposal, dict)
                                           else (self.proposal or "not json"))
        return RunResult(True)


def setup(tmp_path, monkeypatch, n_settled=25):
    paths, reg = make_world(tmp_path, monkeypatch, n_settled=n_settled)
    s = db.strategy(paths, "watermelon-cat")
    s.execute("UPDATE param_versions SET created_at = created_at - 10 * 86400")
    s.commit()
    repo = tmp_path / "repo"
    calls = {"commit": [], "tests": [], "slack": [], "publish": []}
    env = retro.Env(paths=paths, repo=repo, registry_dir=reg, reports_dir=repo / "reports",
                    research_docs_dir=repo / "docs" / "research" / "monthly", inbox_dir=repo / "autopilot" / "inbox",
                    processed_dir=repo / "autopilot" / "processed",
                    run_tests=lambda r: calls["tests"].append(r) or (True, "ok"),
                    commit=lambda msg, push=True, repo=None: calls["commit"].append(msg) or (True, "pushed"),
                    pull=lambda r: (True, ""), publish=lambda p: calls["publish"].append(p),
                    post_slack=lambda rep, url: calls["slack"].append((rep["title"], url)), use_jenkins=False)
    return paths, reg, env, calls


def opts(kind="daily", **kw):
    return retro.Options(kind=kind, slot="morning" if kind == "daily" else None, now=NOW, analysis=False, **kw)


PARAM_PROPOSAL = {"schema": "polylab.proposal/v1", "summary": "tighten entry",
                  "changes": [{"variant_id": "watermelon-cat", "change": "params", "values": {"prob_min": 0.93},
                               "rationale": "late losses", "evidence": {"file": "metrics/watermelon-cat.json"}}]}


def test_no_engine_still_does_deterministic_work(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    env.engines = lambda: [FakeEngine("claude", available=False), FakeEngine("codex", available=False)]
    res = retro.run_retro(opts(), env)
    assert res["deterministic_ok"] and res["ok"] and res["ai"] is False
    md = (env.reports_dir / "daily" / f"{res['name']}.md").read_text()
    assert "AI 회고 생략(토큰 없음)" in md and "## 지난 24시간 거래 내역" in md
    index = json.loads((env.reports_dir / "index.json").read_text())
    assert index[0]["path"] == f"reports/daily/{res['name']}.md" and index[0]["ai"] is False
    meta = json.loads((env.reports_dir / "context" / "latest" / "meta.json").read_text())
    assert meta["generated_at"] and meta["engine"] is None
    assert calls["commit"] and calls["slack"] and calls["publish"]
    retro.record_state(paths, "daily", NOW, res)
    assert retro.catchup_since(paths, "daily", NOW + 2 * 86400) == NOW


def test_claude_rate_limited_falls_back_to_codex_and_applies(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    claude = FakeEngine("claude", ok=False)
    codex = FakeEngine("codex", proposal=PARAM_PROPOSAL)
    env.engines = lambda: [claude, codex]
    res = retro.run_retro(opts(), env)
    assert res["engine"] == "codex" and len(res["applied"]) == 1, res["rejected"]
    y = yaml.safe_load((reg / "watermelon-cat.yaml").read_text())
    assert y["params"]["prob_min"] == 0.93 and calls["tests"]
    changes = (env.reports_dir / "changes.md").read_text()
    assert "prob_min 0.92→0.93" in changes and "engine codex" in changes
    md = (env.reports_dir / "daily" / f"{res['name']}.md").read_text()
    assert "엔진: codex" in md and "적용: `watermelon-cat` params" in md


def test_invalid_proposal_falls_through_and_out_of_bounds_rejected(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    bad = {"schema": "polylab.proposal/v1", "changes": [
        {"variant_id": "watermelon-cat", "change": "stake", "values": {"stake_usdc": 10},
         "rationale": "winning", "evidence": {}}]}
    env.engines = lambda: [FakeEngine("claude", proposal="{broken"), FakeEngine("codex", proposal=bad)]
    res = retro.run_retro(opts(), env)
    assert res["engine"] == "codex" and not res["applied"]
    assert any("stake freeze" in r["reason"] for r in res["rejected"])   # owner 2026-10-06 `stake:freeze-5`
    assert yaml.safe_load((reg / "watermelon-cat.yaml").read_text())["stake_usdc"] == 5.0


def test_test_failure_reverts_yaml(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    before = (reg / "watermelon-cat.yaml").read_text()
    env.engines = lambda: [FakeEngine("claude", proposal=PARAM_PROPOSAL)]
    env.run_tests = lambda r: (False, "1 failed")
    res = retro.run_retro(opts(), env)
    assert not res["applied"] and (reg / "watermelon-cat.yaml").read_text() == before
    assert any("reverted" in r["reason"] for r in res["rejected"])


def test_all_engines_fail_marks_retro_not_ok(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    env.engines = lambda: [FakeEngine("claude", ok=False), FakeEngine("codex", ok=False)]
    res = retro.run_retro(opts(), env)
    assert res["deterministic_ok"] and not res["ok"] and "AI 실패" in res["error"]


def test_weekly_review_vetoes_and_new_paper_variant(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    prop = {"schema": "polylab.proposal/v1", "changes": [
        PARAM_PROPOSAL["changes"][0],
        {"variant_id": "watermelon-late", "change": "new_variant",
         "values": {"based_on": "watermelon-cat", "hypothesis": "late", "params": {"prob_min": 0.95}},
         "rationale": "calibration gap late soccer", "evidence": {}}]}
    review = {"summary": "첫 제안은 표본 부족", "reviews": [{"index": 0, "verdict": "reject", "comment": "표본 부족"},
                                                        {"index": 1, "verdict": "flag", "comment": "ok but small"}]}
    env.engines = lambda: [FakeEngine("claude", proposal=prop), FakeEngine("codex", review=review)]
    res = retro.run_retro(opts("weekly"), env)
    applied = {(a["variant_id"], a["change"]) for a in res["applied"]}
    assert applied == {("watermelon-late", "new_variant")}
    new = yaml.safe_load((reg / "watermelon-late.yaml").read_text())
    assert new["mode"] == "paper" and new["stake_usdc"] == 5.0
    assert any("second opinion" in r["reason"] for r in res["rejected"])
    md = (env.reports_dir / "weekly" / f"{res['name']}.md").read_text()
    assert "Second opinion" in md and "ok but small" in md


def test_inbox_proposals_validated_and_archived(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    env.inbox_dir.mkdir(parents=True)
    (env.inbox_dir / "p1.json").write_text(json.dumps({"schema": "polylab.proposal/v1", "changes": [
        {"variant_id": "watermelon-cat", "change": "mode", "values": {"mode": "live"}, "rationale": "x",
         "evidence": {}}]}))
    env.engines = lambda: []
    res = retro.run_retro(opts(), env)
    assert not list(env.inbox_dir.glob("*.json"))
    results = list(env.processed_dir.glob("*.result.json"))
    assert results and "not allowed" in results[0].read_text()
    assert res["rejected"][0]["source"] == "inbox:p1.json"


def test_monthly_writes_research_doc(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    env.engines = lambda: [FakeEngine("claude", proposal={"schema": "polylab.proposal/v1", "changes": []},
                                      narrative="해석 본문")]
    res = retro.run_retro(opts("monthly"), env)
    doc = (env.research_docs_dir / f"{res['name']}.md").read_text()
    assert "## 2. Calibration" in doc and "해석 본문" in doc and "## 6. 한계" in doc


@pytest.mark.usefixtures("unfrozen_ladder")
def test_deterministic_ladder_promotion_recorded(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    s = db.strategy(paths, "watermelon-cat")
    s.execute("UPDATE positions SET realized_pnl = 0.35 WHERE realized_pnl IS NOT NULL")
    s.execute("DELETE FROM stake_events")
    s.commit()
    env.engines = lambda: [FakeEngine("claude", proposal=PARAM_PROPOSAL)]  # same variant: AI change must yield
    res = retro.run_retro(opts(), env)
    sources = {(a["source"], a["change"]) for a in res["applied"]}
    assert ("ladder", "stake") in sources and not any(a["source"].startswith("ai") for a in res["applied"])
    assert yaml.safe_load((reg / "watermelon-cat.yaml").read_text())["stake_usdc"] == 10.0
    ev = s.execute("SELECT from_usdc, to_usdc, reason FROM stake_events").fetchall()
    assert [(r[0], r[1]) for r in ev] == [(5.0, 10.0)] and ev[0][2].startswith("promote")
    assert any("one change per variant" in r["reason"] for r in res["rejected"])


def test_commit_aborts_when_account_secret_is_staged(tmp_path, monkeypatch):
    import subprocess
    from polylab.autopilot import gitops
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    wallet = "0x" + "ab" * 20
    (secrets / "accounts.env").write_text(f"POLYBOT_X__POLYMARKET_FUNDER_ADDRESS={wallet}\nPOLYBOT_X__POLYMARKET_PRIVATE_KEY=0x{'cd' * 32}\n")
    monkeypatch.setattr(gitops.settings, "SECRETS_DIR", secrets)
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "reports" / "r.md").write_text(f"wallet {wallet.upper()[2:]}\n")
    ok, msg = gitops.commit_and_push("x", push=False, repo=repo, paths=("reports",))
    assert not ok and "secret" in msg
    assert subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True).returncode != 0  # no commit
    (repo / "reports" / "r.md").write_text("clean\n")
    ok, msg = gitops.commit_and_push("x", push=False, repo=repo, paths=("reports",))
    assert ok, msg


def fake_trades(n, pnl_fn, start=NOW - 100 * 86400):
    return [{"opened_at": start + i * 3600, "closed_at": start + i * 3600 + 600, "token_id": f"t{i}",
             "status": "resolved", "cost": 5.0, "pnl": pnl_fn(i)} for i in range(n)]


def test_weekly_backtest_backed_retune_without_live_trades(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch, n_settled=3)  # 3 < min_trades_params
    runs = []

    def fake_backtest(jobs, start, end, timeout):
        runs.append([(v.id, p) for v, p in jobs])
        assert end - start == 120 * 86400 and timeout > 0
        return [{"trades": fake_trades(60, lambda i: 0.05 if i % 10 else -1.0)},   # current: ROI -1%
                {"trades": fake_trades(50, lambda i: 0.06 if i % 12 else -1.0)}]   # proposed: better both halves

    env.backtest = fake_backtest
    prop = {**PARAM_PROPOSAL, "changes": [{**PARAM_PROPOSAL["changes"][0], "evidence": {"roi": 9.9}}]}
    env.engines = lambda: [FakeEngine("claude", proposal=prop), FakeEngine("codex", available=False)]
    res = retro.run_retro(opts("weekly"), env)
    assert runs == [[("watermelon-cat", {}), ("watermelon-cat", {"prob_min": 0.93})]]
    assert [a["variant_id"] for a in res["applied"]] == ["watermelon-cat"], res["rejected"]
    assert yaml.safe_load((reg / "watermelon-cat.yaml").read_text())["params"]["prob_min"] == 0.93
    changes = (env.reports_dir / "changes.md").read_text()
    assert "[backtest: n 60→50" in changes and "백테스트 근거" in changes and "H2 n 20" in changes


def test_backtest_backed_retune_rejected_when_replay_worse_or_daily_not_idle(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch, n_settled=3)
    env.backtest = lambda jobs, start, end, timeout: [
        {"trades": fake_trades(60, lambda i: 0.05 if i % 10 else -1.0)},
        {"trades": fake_trades(50, lambda i: 0.05 if i < 25 else (-1.0 if i % 5 == 0 else 0.05))}]
    env.engines = lambda: [FakeEngine("claude", proposal=PARAM_PROPOSAL), FakeEngine("codex", available=False)]
    res = retro.run_retro(opts("weekly"), env)
    assert not res["applied"] and any("backtest gate failed" in r["reason"] for r in res["rejected"])
    assert yaml.safe_load((reg / "watermelon-cat.yaml").read_text())["params"]["prob_min"] == 0.92

    env.backtest = lambda *a: (_ for _ in ()).throw(AssertionError("daily must not replay non-idle variants"))
    res = retro.run_retro(opts("daily"), env)
    assert not res["applied"] and any("idle" in r["reason"] for r in res["rejected"])


def test_split_evidence_uses_one_split_for_both_arms():
    cur = {"trades": fake_trades(4, lambda i: 1.0)}
    new = {"trades": fake_trades(2, lambda i: -1.0, start=NOW - 100 * 86400 + 3 * 3600)}
    ev = retro.split_evidence(cur, new, NOW - 120 * 86400, NOW)
    assert ev["split_ts"] == NOW - 100 * 86400 + 2 * 3600
    assert [h["n"] for h in ev["current"]["halves"]] == [2, 2]
    assert [h["n"] for h in ev["proposed"]["halves"]] == [0, 2] and ev["proposed"]["halves"][0]["roi"] is None
    assert retro.split_evidence({"error": "timeout"}, new, 0, 1)["error"] == "timeout"


def test_backtest_evidence_skipped_past_soft_deadline_and_change_rejected(tmp_path, monkeypatch):
    import time as _time

    from polylab.autopilot.validator import Context, Facts, Rules, validate
    from polylab.registry import load_all

    paths, reg, env, calls = setup(tmp_path, monkeypatch, n_settled=3)
    env.backtest = lambda *a: (_ for _ in ()).throw(AssertionError("must not replay past the soft deadline"))
    variants = {v.id: v for v in load_all(reg, include_off=True)}
    vctx = Context(variants=variants, facts={vid: Facts(trades_at_version=3) for vid in variants}, now=NOW)
    rules = Rules.for_kind("weekly")
    late = _time.time() - rules.backtest_soft_deadline_s + 30  # less than a minute left
    notes = retro.backtest_evidence([PARAM_PROPOSAL], vctx, rules, env, NOW, retro_started=late)
    assert notes and "time budget" in notes[0] and not vctx.backtests
    (d,) = validate(PARAM_PROPOSAL, vctx, rules)
    assert not d.accepted and "no retro-run backtest" in d.reason


def test_daily_retune_reaches_idle_sport_of_per_sport_variant(tmp_path, monkeypatch):
    """Per-sport variants name a sport on params changes, so the daily idle-only retune must read the sport's own
    idle flag (bug before 2026-10-06: only the variant-level flag was set and the replay never ran)."""
    paths, reg, env, calls = setup(tmp_path, monkeypatch, n_settled=3)
    d = yaml.safe_load((reg / "watermelon-cat.yaml").read_text())
    d["sports"] = {"soccer": {"mode": "live", "stake_usdc": 5.0}}
    (reg / "watermelon-cat.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    s = db.strategy(paths, "watermelon-cat")
    s.execute("DELETE FROM positions")
    s.commit()
    runs = []

    def fake_backtest(jobs, start, end, timeout):
        runs.append([(v.id, v.sports, p) for v, p in jobs])
        return [{"trades": fake_trades(60, lambda i: 0.05 if i % 10 else -1.0)},
                {"trades": fake_trades(50, lambda i: 0.06 if i % 12 else -1.0)}]

    env.backtest = fake_backtest
    prop = {"schema": "polylab.proposal/v1", "summary": "loosen entry",
            "changes": [{"variant_id": "watermelon-cat", "sport": "soccer", "change": "params",
                         "values": {"prob_min": 0.90}, "rationale": "idle"}]}
    env.engines = lambda: [FakeEngine("claude", proposal=prop), FakeEngine("codex", available=False)]
    res = retro.run_retro(opts("daily"), env)
    assert runs == [[("watermelon-cat", ["soccer"], {}),
                     ("watermelon-cat", ["soccer"], {"sport_overrides.soccer.prob_min": 0.90})]], res["rejected"]
    assert [a["variant_id"] for a in res["applied"]] == ["watermelon-cat"], res["rejected"]
    assert yaml.safe_load((reg / "watermelon-cat.yaml").read_text())["params"]["sport_overrides"]["soccer"]["prob_min"] == 0.90
