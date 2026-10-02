import json

from test_autopilot_retro import FakeEngine, opts, setup
from test_publish_snapshot import assert_shape, contract_examples
from test_reports_build import NOW, START, make_world

from polylab import db
from polylab.analysis import _common as C
from polylab.autopilot import attention, retro
from polylab.publish import snapshot
from polylab.reports import build

DAY = 86400


def rule(id, rule_name="health", severity="warn", title="t"):
    return attention.item(id, rule_name, severity, "data_quality", title, "d", "reports/x.md")


def by_id(state):
    return {i["id"]: i for i in state["items"]}


# ------------------------------------------------------------------ merge

def test_merge_dedupes_and_auto_resolves_only_evaluated_families():
    daily = attention._families("daily", True)
    weekly = attention._families("weekly", True)
    s = attention.merge(attention.empty_state(), [rule("health:poll_stale"), rule("health:poll_stale"),
                                                  rule("params_7d", "params_7d", "info")], weekly, NOW)
    assert len(s["items"]) == 2 and all(i["status"] == "open" for i in s["items"])
    first = by_id(s)["health:poll_stale"]
    # same content again: one item, created/updated kept
    s = attention.merge(s, [rule("health:poll_stale"), rule("params_7d", "params_7d", "info")], weekly, NOW + 60)
    assert by_id(s)["health:poll_stale"]["updated_at"] == first["updated_at"]
    # daily run: poll_stale cleared -> resolved; weekly-only params_7d untouched
    s = attention.merge(s, [], daily, NOW + 3600)
    assert by_id(s)["health:poll_stale"]["status"] == "resolved"
    assert by_id(s)["health:poll_stale"]["resolution"] == "조건 해소 (자동)"
    assert by_id(s)["params_7d"]["status"] == "open"
    # condition returns -> reopened as a fresh item
    s = attention.merge(s, [rule("health:poll_stale", title="again")], daily, NOW + 7200)
    again = by_id(s)["health:poll_stale"]
    assert again["status"] == "open" and again["resolved_at"] is None and again["created_at"] == C.iso(NOW + 7200)
    # resolved items are pruned after 14 days
    s = attention.merge(s, [], daily | weekly, NOW + 3 * 3600)
    assert by_id(s)["params_7d"]["status"] == "resolved"
    s = attention.merge(s, [], daily, NOW + 15 * DAY)
    assert s["items"] == []


def test_open_items_sorted_by_severity_then_newest():
    s = attention.merge(attention.empty_state(), [rule("a", severity="info")], {"health"}, NOW)
    s = attention.merge(s, [rule("a", severity="info"), rule("b", severity="decide")], {"health"}, NOW + 10)
    s = attention.merge(s, [rule("a", severity="info"), rule("b", severity="decide"), rule("c", severity="info"),
                            rule("d", severity="critical")], {"health"}, NOW + 20)
    assert [i["id"] for i in attention.open_items(s)] == ["d", "b", "c", "a"]


def test_ai_items_expire_and_cannot_touch_rule_items():
    raw = {"items": [{"id": "kill_switch", "severity": "info", "category": "question", "title": "resolve it",
                      "evidence_ref": "metrics/x.json"}]}
    ai_items, _, _ = attention.sanitize_ai(raw, "daily")
    assert ai_items[0]["id"] == "ai:kill-switch"
    s = attention.merge(attention.empty_state(), [rule("kill_switch", severity="critical")] + ai_items, {"health"}, NOW)
    s = attention.merge(s, [rule("kill_switch", severity="critical")], {"health"}, NOW + 3600)
    assert by_id(s)["kill_switch"]["status"] == "open" and by_id(s)["ai:kill-switch"]["status"] == "open"
    s = attention.merge(s, [rule("kill_switch", severity="critical")], {"health"}, NOW + 8 * DAY)
    assert by_id(s)["ai:kill-switch"]["status"] == "resolved" and "만료" in by_id(s)["ai:kill-switch"]["resolution"]
    assert by_id(s)["kill_switch"]["status"] == "open"


# ------------------------------------------------------------------ AI validation

def test_sanitize_ai_caps_scrubs_and_downgrades():
    wallet = "0x" + "ab" * 20
    items = [{"id": "Hypo Contradicted!", "severity": "critical", "category": "research_finding",
              "title": "가설 반대 <!channel> " + wallet, "detail": "줄1\n# 줄2 <b>x</b>", "evidence_ref": ["metrics/a.json"]},
             {"severity": "decide", "category": "system_change", "title": "not allowed", "evidence_ref": "x"},
             {"severity": "info", "category": "question", "title": "no evidence"},
             {"severity": "bogus", "category": "question", "title": "q" * 300, "evidence_ref": "a.json"},
             {"severity": "warn", "category": "risk", "title": "third", "evidence_ref": "b.json"},
             {"severity": "warn", "category": "risk", "title": "fourth", "evidence_ref": "c.json"}]
    out, thesis, notes = attention.sanitize_ai({"items": items, "thesis_sentences": [
        {"text": "문장", "n": 100, "evidence_ref": "calibration_summary.json"}]}, "daily")
    assert len(out) == 3 and [i["title"] for i in out][2] == "third"
    first = out[0]
    assert first["id"] == "ai:hypo-contradicted" and first["severity"] == "warn" and first["source"] == "ai"
    assert "<" not in first["title"] and "[REDACTED]" in first["title"] and wallet not in json.dumps(out)
    assert "\n" not in first["detail"] and "<" not in first["detail"]
    assert out[1]["severity"] == "info" and len(out[1]["title"]) <= attention.TITLE_MAX
    assert thesis == []  # daily never carries thesis sentences
    assert any("초과" in n for n in notes) and any("근거 없음" in n for n in notes)


def test_sanitize_ai_thesis_gate_and_garbage_input():
    raw = {"thesis_sentences": [{"text": "ok", "n": 114, "evidence_ref": "calibration_summary.json"},
                                {"text": "small", "n": 12, "evidence_ref": "x.json"},
                                {"text": "no ref", "n": 200},
                                {"text": "bool n", "n": True, "evidence_ref": "x.json"}]}
    _, thesis, _ = attention.sanitize_ai(raw, "weekly")
    assert thesis == [{"text": "ok", "n": 114, "evidence_ref": "calibration_summary.json"}]
    for garbage in (None, [], "text", {"items": "x"}, {"items": [1, None]}):
        assert attention.sanitize_ai(garbage, "weekly")[:2] == ([], [])


# ------------------------------------------------------------------ deterministic rules

def test_rules_from_report_health_and_ledgers(tmp_path, monkeypatch):
    paths, reg = make_world(tmp_path, monkeypatch)
    s = db.strategy(paths, "watermelon-cat")
    s.execute("INSERT INTO stake_events(ts, from_usdc, to_usdc, from_mode, to_mode, reason) VALUES(?,5,10,'live','live',"
              "'promote: ok')", (NOW - DAY,))
    s.commit()
    rep = build.build("daily", paths, now=NOW, slot="morning", use_jenkins=False)
    rep["health"]["disk_free_gb"] = 80.0
    rep["health"]["collector"]["quality_24h"] = {"live_gap": 60, "poll_gap": 1}
    rep["health"]["loss_stops"] = [{"variant_id": "watermelon-cat", "pnl_today": -21.0, "stop": 20.0}]
    rejected = [{"variant_id": "watermelon-cat", "change": "params", "reason": "param cooldown: last change 1h ago",
                 "source": "ai:claude"},
                {"variant_id": "x\n</details>\n## boom", "change": "bogus", "reason": "variant_id must match",
                 "source": "ai:claude"}]
    ai = {"ran": True, "engine": "codex", "tried": [{"engine": "claude", "ok": False, "reason": "usage limit"},
                                                    {"engine": "codex", "ok": True, "reason": ""}]}
    items, fams = attention.rule_items(rep, kind="daily", now=NOW, paths=paths, rejected=rejected, ai=ai)
    ids = {i["id"]: i for i in items}
    assert ids[f"stake:watermelon-cat:{NOW - DAY}"]["severity"] == "info"
    assert "5→10" in ids[f"stake:watermelon-cat:{NOW - DAY}"]["title"]
    assert not any(i.startswith("stake:") and i.endswith(str(NOW - 20 * DAY)) for i in ids)  # init row ignored
    assert ids["disk"]["severity"] == "warn" and ids["quality:live_gap"]["severity"] == "info"
    assert "quality:poll_gap" not in ids and ids["loss_stop:watermelon-cat"]["category"] == "risk"
    assert "\n" not in ids["rejected:daily"]["detail"] and "<" not in ids["rejected:daily"]["detail"]
    assert "cooldown" in ids["rejected:daily"]["detail"] and ids["ai_fallback"]["category"] == "system_change"
    assert "params_7d" not in fams and "rejected:daily" in fams and "dead_variant:watermelon-cat" not in ids


def test_dead_variant_rule_guards(tmp_path, monkeypatch):
    paths, reg = make_world(tmp_path, monkeypatch)
    s = db.strategy(paths, "watermelon-cat")
    s.execute("DELETE FROM positions")
    s.commit()
    rep = build.build("daily", paths, now=NOW, slot="morning", use_jenkins=False)
    dead = attention._dead_variant_items(rep, paths, NOW)
    assert [i["id"] for i in dead] == ["dead_variant:watermelon-cat"] and dead[0]["severity"] == "decide"
    rep["variants"][0]["params"]["leagues"] = ["epl"]  # fixture league is 'EPL': case-insensitive
    assert attention._dead_variant_items(rep, paths, NOW)
    rep["variants"][0]["params"]["leagues"] = ["bun"]
    assert attention._dead_variant_items(rep, paths, NOW) == []
    rep["variants"][0]["params"].pop("leagues")
    rep["health"]["kill_switch"] = True
    assert attention._dead_variant_items(rep, paths, NOW) == []
    rep["health"]["kill_switch"] = False
    rep["variants"][0]["param_history"][0]["ts"] = NOW - DAY  # younger than 3 days
    assert attention._dead_variant_items(rep, paths, NOW) == []
    assert START > NOW - 3 * DAY


def test_paper_variant_rules():
    rep = {"variants": [{"id": "wm-late", "mode": "paper", "hypothesis": "h", "paper": {"trades": {"all": 3}, "roi": 0.1}},
                        {"id": "wm-old", "mode": "paper", "hypothesis": "h",
                         "paper": {"trades": {"all": 25}, "roi": 0.05, "win_rate": 0.8}}]}
    items = {i["id"]: i for i in attention._paper_items(rep, [{"variant_id": "wm-new", "change": "new_variant",
                                                                "summary": "new paper variant", "rationale": "gap"}])}
    assert items["paper:wm-late"]["severity"] == "info" and "3/20" in items["paper:wm-late"]["title"]
    assert items["paper_ready:wm-old"]["severity"] == "decide" and items["paper_ready:wm-old"]["category"] == "decision_needed"
    assert "paper:wm-new" in items


# ------------------------------------------------------------------ retro integration

class AttentionEngine(FakeEngine):
    def __init__(self, name, attention_payload, **kw):
        super().__init__(name, **kw)
        self.attention_payload = attention_payload

    def run(self, prompt, cwd, timeout):
        res = super().run(prompt, cwd, timeout)
        if not prompt.startswith("# polylab 주간 제안 second opinion"):
            assert (cwd / "attention_open.json").exists()
            (cwd / "attention.json").write_text(self.attention_payload if isinstance(self.attention_payload, str)
                                                else json.dumps(self.attention_payload))
        return res


def test_retro_without_ai_writes_inbox_and_brief(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    env.engines = lambda: [FakeEngine("claude", available=False)]
    res = retro.run_retro(opts(), env)
    assert res["deterministic_ok"] and not any("attention" in m and "skipped" in m for m in env.log)
    state = json.loads((env.reports_dir / "attention.json").read_text())
    assert state["schema"] == attention.SCHEMA and state["last_retro"]["name"] == res["name"]
    assert by_id(state)["ai_unavailable"]["status"] == "open"
    inbox = (env.reports_dir / "attention.md").read_text()
    assert "## 열린 항목" in inbox and "AI 회고 생략" in inbox
    md = (env.reports_dir / "daily" / f"{res['name']}.md").read_text()
    assert md.index("## 오늘의 브리프") < md.index("## 요약") and attention.ATTENTION_URL in md
    assert "논문에 쓸 수 있는 문장" not in md


def test_retro_ai_items_invalid_file_and_resolution(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    env.engines = lambda: [FakeEngine("claude", available=False)]
    retro.run_retro(opts(), env)
    finding = {"items": [{"id": "nhl-late-dog", "severity": "critical", "category": "research_finding",
                          "title": "NHL 막판 언더독 과소평가", "detail": "gap +0.14", "evidence_ref": "calibration_summary.json"}]}
    env.engines = lambda: [AttentionEngine("claude", finding, proposal={"schema": "polylab.proposal/v1", "changes": []})]
    res = retro.run_retro(retro.Options(kind="daily", slot="evening", now=NOW + 12 * 3600, analysis=False), env)
    state = json.loads((env.reports_dir / "attention.json").read_text())
    items = by_id(state)
    assert items["ai_unavailable"]["status"] == "resolved"
    assert items["ai:nhl-late-dog"]["severity"] == "warn" and items["ai:nhl-late-dog"]["source"] == "ai"
    md = (env.reports_dir / "daily" / f"{res['name']}.md").read_text()
    assert "**연구** NHL 막판 언더독 과소평가 (AI 판단" in md
    # invalid attention.json: no AI items, retro still fine
    env.engines = lambda: [AttentionEngine("claude", "{broken", proposal={"schema": "polylab.proposal/v1", "changes": []})]
    res = retro.run_retro(retro.Options(kind="daily", slot="dawn", now=NOW + 20 * 3600, analysis=False), env)
    assert res["ok"] and res["deterministic_ok"]
    assert by_id(json.loads((env.reports_dir / "attention.json").read_text()))["ai:nhl-late-dog"]["status"] == "open"


def test_weekly_thesis_section_only_when_evidence_sufficient(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    payload = {"items": [], "thesis_sentences": [{"text": "NFL 0.60–0.70 토큰은 과대평가되었다(95% CI 0.435–0.616).",
                                                  "n": 114, "evidence_ref": "calibration_summary.json"}]}
    env.engines = lambda: [AttentionEngine("claude", payload, proposal={"schema": "polylab.proposal/v1", "changes": []})]
    res = retro.run_retro(opts("weekly"), env)
    md = (env.reports_dir / "weekly" / f"{res['name']}.md").read_text()
    assert "## 논문에 쓸 수 있는 문장 (AI 초안)" in md and "n=114" in md
    assert md.index("## 오늘의 브리프") < md.index("## 논문에 쓸 수 있는 문장") < md.index("## 요약")
    payload["thesis_sentences"][0]["n"] = 12
    env.engines = lambda: [AttentionEngine("claude", payload, proposal={"schema": "polylab.proposal/v1", "changes": []})]
    res = retro.run_retro(retro.Options(kind="weekly", now=NOW + 7 * DAY, analysis=False), env)
    md = (env.reports_dir / "weekly" / f"{res['name']}.md").read_text()
    assert "논문에 쓸 수 있는 문장" not in md


def test_brief_failure_never_blocks_retro(tmp_path, monkeypatch):
    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    env.engines = lambda: []

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(attention, "update", boom)
    res = retro.run_retro(opts(), env)
    assert res["deterministic_ok"] and any("attention/brief skipped" in m for m in env.log)
    md = (env.reports_dir / "daily" / f"{res['name']}.md").read_text()
    assert "## 오늘의 브리프" not in md and "## 요약" in md


# ------------------------------------------------------------------ dashboard read model

def test_attention_snapshot_matches_contract(tmp_path):
    assert snapshot.attention_snapshot(tmp_path) == {"generated_at": None, "url": None, "open": [], "resolved": []}
    s = attention.merge(attention.empty_state(), [rule("health:poll_stale"), rule("disk", "disk")], {"health", "disk"}, NOW)
    s = attention.merge(s, [rule("disk", "disk")], {"health", "disk"}, NOW + 60)
    attention.save(tmp_path, s)
    snap = snapshot.attention_snapshot(tmp_path)
    assert [i["id"] for i in snap["open"]] == ["disk"] and [i["id"] for i in snap["resolved"]] == ["health:poll_stale"]
    assert_shape(contract_examples()["latest/attention.json"], snap)
    assert "reports" not in contract_examples()


def test_owner_decisions_resolve_items_and_stay_resolved(tmp_path):
    from polylab.autopilot import attention as A
    (tmp_path / "decisions.md").write_text(
        "# 기록\n\n## 2026-10-02\n- `ai:x` — take-profit early 적용\n- garbage line\n- `ai:y` - 첫 답\n\n## 2026-10-03\n- `ai:y` — 바뀐 답\n")
    dec = A.load_decisions(tmp_path)
    assert dec == {"ai:x": {"date": "2026-10-02", "decision": "take-profit early 적용"},
                   "ai:y": {"date": "2026-10-03", "decision": "바뀐 답"}}
    now = 1_790_900_000
    emitted = [{**A.item("ai:x", "ai", "warn", "risk", "t", "d"), "source": "ai"},
               {**A.item("ai:z", "ai", "info", "question", "t2", "d2"), "source": "ai"}]
    st = A.apply_decisions(A.merge(A.empty_state(), emitted, set(), now), dec, now)
    by = {i["id"]: i for i in st["items"]}
    assert by["ai:x"]["status"] == "resolved" and "사용자 결정 (2026-10-02)" in by["ai:x"]["resolution"]
    assert by["ai:z"]["status"] == "open"
    # the AI raising the same id again does not reopen it
    st2 = A.apply_decisions(A.merge(st, emitted, set(), now + 60), dec, now + 60)
    assert {i["id"]: i["status"] for i in st2["items"]}["ai:x"] == "resolved"
    assert A.load_decisions(tmp_path / "missing") == {}
