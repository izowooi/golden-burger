"""O/U 0.5 lifecycle export (analysis.ou05.lifecycle): sign of overpricing, CI, thin-data flag, report section,
retro context file."""

from __future__ import annotations

import json

import pytest

from polylab.analysis import ou05
from polylab.reports import render


def _summary(n_poll=0, poll_from=None, poll_to=None, early=0.80, late=0.95):
    cal, ovr = [], []
    for band, mean in (("d1", early), ("h6", (early + late) / 2), ("m0", late)):
        cal.append({"band": band, "tier": "all", "n": 400, "n_poll": n_poll, "mean_over": mean, "over_rate": 0.90,
                    "ci_lo": 0.87, "ci_hi": 0.93, "gap": round(0.90 - mean, 4), "nil_rate": 0.10})
        ovr.append({"band": band, "tier": "all", "markets": 300, "minutes": 9000, "rows": 900,
                    "two_sided_share": 0.9, "spread": {"p50": 0.02}, "sum_ask": {"p50": 1.02}})
    return {"generated_at": "2026-10-05T00:00:00Z", "bands": [{"key": k, "label": k, "kind": "pre"} for k in
                                                              ("d1", "h6", "m0")],
            "scope": {"markets": 500, "resolved": 400, "poll_rows": 10, "history_rows": 10, "poll_from": poll_from,
                      "poll_to": poll_to},
            "overround": ovr, "calibration": cal}


def test_overpricing_sign_ci_and_verdict():
    lc = ou05.lifecycle(_summary())
    m0 = next(b for b in lc["bands"] if b["band"] == "m0")
    assert m0["overpricing"] == pytest.approx(0.05)                 # 0.95 price vs 0.90 realised: Over overpriced
    assert m0["overpricing_ci"] == [pytest.approx(0.02), pytest.approx(0.08)]
    assert m0["price_basis"] == "history_mid" and m0["spread_p50"] == 0.02
    [c] = [c for c in lc["checks"] if c["tier"] == "all"]
    assert c["verdict"] == "supports" and c["direction"] == "up" and c["early_band"] == "d1"
    assert c["cheapest_band"] == "d1" and c["dearest_band"] == "m0"
    rev = ou05.lifecycle(_summary(early=0.97, late=0.80))
    assert next(c for c in rev["checks"] if c["tier"] == "all")["verdict"] == "contradicts"
    assert next(c for c in lc["checks"] if c["tier"] == "major")["verdict"] == "insufficient"


def test_thin_data_flag():
    assert ou05.lifecycle(_summary())["data_status"]["thin"]
    ok = ou05.lifecycle(_summary(n_poll=400, poll_from="2026-09-01T00:00:00Z", poll_to="2026-10-01T00:00:00Z"))
    assert not ok["data_status"]["thin"] and ok["scope"]["poll_days"] == 30.0
    assert ok["bands"][0]["price_basis"] == "poll_mid"


def test_weekly_section_and_context_file(tmp_path):
    lc = ou05.lifecycle(_summary())
    lines = render.section_ou05_lifecycle({"kind": "weekly", "ou05_lifecycle": lc})
    text = "\n".join(lines)
    assert lines[0] == "## 0.5 Over 생애 과대평가" and "데이터가 아직 얇다" in text and "가설 지지" in text
    assert render.section_ou05_lifecycle({"kind": "daily", "ou05_lifecycle": lc}) == []
    assert "집계 없음" in "\n".join(render.section_ou05_lifecycle({"kind": "weekly", "ou05_lifecycle": None}))
    # cached summary -> lifecycle_cached
    paths = type("P", (), {"research_dir": tmp_path})()
    assert ou05.lifecycle_cached(paths) is None
    (tmp_path / "ou05" / "latest").mkdir(parents=True)
    (tmp_path / "ou05" / "latest" / "summary.json").write_text(json.dumps(_summary()))
    assert ou05.lifecycle_cached(paths)["schema"] == "polylab.ou05_lifecycle/v1"


def test_weekly_retro_context_pack_and_report_carry_lifecycle(tmp_path, monkeypatch):
    from test_autopilot_retro import FakeEngine, opts, setup

    from polylab.autopilot import retro

    paths, reg, env, calls = setup(tmp_path, monkeypatch)
    d = paths.research_dir / "ou05" / "latest"
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(_summary()))
    env.engines = lambda: [FakeEngine("claude", available=False), FakeEngine("codex", available=False)]
    res = retro.run_retro(opts("weekly"), env)
    [pack] = list((paths.state / "retro").glob("*-weekly"))
    lc = json.loads((pack / "ou05_lifecycle.json").read_text())
    assert lc["schema"] == "polylab.ou05_lifecycle/v1" and lc["bands"]
    md = (env.reports_dir / "weekly" / f"{res['name']}.md").read_text()
    assert "## 0.5 Over 생애 과대평가" in md
