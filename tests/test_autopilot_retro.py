import json

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
    assert any("gate" in r["reason"] for r in res["rejected"])
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
