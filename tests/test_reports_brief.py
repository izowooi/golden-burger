import json

from test_reports_build import NOW, make_world

from polylab.analysis import _common as C
from polylab.autopilot import attention
from polylab.reports import brief, build, render, slack


def _opens():
    s = attention.merge(attention.empty_state(), [
        attention.item("dead_variant:x", "dead_variant", "decide", "decision_needed", "x 3일 이상 진입 0건"),
        attention.item("disk", "disk", "warn", "risk", "외장 디스크 여유 80GB"),
        attention.item("quality:live_gap", "quality", "info", "data_quality", "live_gap 60건")], {"disk"}, NOW)
    return attention.open_items(s)


def test_brief_lines_and_render_order(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    rep = build.build("daily", paths, now=NOW, slot="morning", use_jenkins=False)
    prev = {"kind": "daily", "name": "x", "at": C.iso(NOW - 8 * 3600), "live_pnl_all": rep["totals"]["all"] - 1.0}
    applied = [{"variant_id": "watermelon-cat", "change": "stake", "summary": "stake 5→10 USDC", "source": "ladder"}]
    rejected = [{"variant_id": "watermelon-cat", "change": "params", "reason": "cooldown"},
                {"variant_id": "watermelon-cat", "change": "params", "reason": "not applied (--no-apply)"}]
    lines = brief.build(rep, prev=prev, applied=applied, rejected=rejected, opens=_opens(), ai_items=[])
    assert 3 <= len(lines) <= 6
    assert lines[0].startswith("**손익** 지난 회고(") and "+1.00 USDC" in lines[0]
    assert "`watermelon-cat` stake 5→10 USDC (ladder)" in lines[1] and "거부된 제안 1건" in lines[1]
    assert "v2" in lines[1]  # param version created 1h ago, after the previous retro
    assert lines[2].startswith("**결정 필요 1건** x 3일") and f"[attention.md]({brief.ATTENTION_URL})" in lines[2]
    assert lines[3].startswith("**경고 1건** 외장 디스크") and lines[-1].startswith("**연구**")
    # first run (no previous retro): today's P&L, window changes
    first = brief.build(rep, prev=None, applied=[], rejected=[], opens=[], ai_items=[])
    assert first[0].startswith("**손익** live 실현 오늘") and first[2].startswith("**결정 필요** 없음")
    assert len(first) == 4
    rep["brief"] = lines
    md = render.render(rep)
    assert md.index("## 오늘의 브리프") < md.index("## 요약") and "- **손익**" in md


def test_brief_research_prefers_ai_finding_then_calibration():
    rep = {"research": {"calibration_top": [{"sport": "nhl", "phase": "final", "p_lo": 0.3, "p_hi": 0.4, "n": 53,
                                             "mean_price": 0.349, "win_rate": 0.491, "ci_lo": 0.361, "ci_hi": 0.621,
                                             "gap": 0.141, "significant": True}]}}
    assert "gap +0.141, n=53" in brief.research_line(rep, []) and "과소평가" in brief.research_line(rep, [])
    ai = [{"category": "research_finding", "title": "발견", "evidence_ref": "a.json"}]
    assert brief.research_line(rep, ai).startswith("**연구** 발견 (AI 판단")
    assert "없음" in brief.research_line({"research": {}}, [])


def test_slack_brief_block_first_and_escaped(tmp_path, monkeypatch):
    paths, _ = make_world(tmp_path, monkeypatch)
    rep = build.build("daily", paths, now=NOW, slot="morning", use_jenkins=False)
    _, plain = slack.report_blocks(rep, "u")
    assert "오늘의 브리프" not in json.dumps(plain, ensure_ascii=False)
    rep["brief"] = ["**연구** <!channel> a&b", f"**결정 필요** 없음 → [attention.md]({brief.ATTENTION_URL})"]
    _, blocks = slack.report_blocks(rep, "u")
    text = blocks[1]["text"]["text"]
    assert text.startswith("*오늘의 브리프*\n• *연구* &lt;!channel&gt; a&amp;b")
    assert f"<{brief.ATTENTION_URL}|attention.md>" in text and "<!channel>" not in text
