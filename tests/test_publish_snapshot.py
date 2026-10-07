import json
import re

from test_reports_build import NOW, make_world

from polylab import registry, settings
from polylab.publish import snapshot

CONTRACT = settings.REPO_ROOT / "docs" / "contracts" / "dashboard-json.md"
FREE_FORM = {"variant", "params", "evidence", "by_sport"}  # keys whose inner keys are data, not schema


def contract_examples() -> dict[str, object]:
    text = CONTRACT.read_text()
    out = {}
    for m in re.finditer(r"## (latest/[^\n]+|reports)\n(?:.*?\n)*?```json\n(.*?)```", text, re.S):
        name = m.group(1).strip()
        body = m.group(2)
        if name == "reports":
            continue
        out[name] = json.loads(body)
    return out


def assert_shape(expected, actual, where="$"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: expected object, got {type(actual).__name__}"
        for key, sub in expected.items():
            if key == "...":
                continue
            assert key in actual, f"{where}.{key} missing"
            if key in FREE_FORM or actual[key] is None:
                continue
            assert_shape(sub, actual[key], f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{where}: expected list"
        if expected and isinstance(expected[0], (dict, list)):
            for i, item in enumerate(actual):
                assert_shape(expected[0], item, f"{where}[{i}]")


def test_contract_examples_parse():
    ex = contract_examples()
    assert {"latest/overview.json", "latest/strategies/<id>.json", "latest/research.json",
            "latest/transactions_24h.json"} <= set(ex)


def test_objects_match_contract(tmp_path, monkeypatch):
    paths, reg = make_world(tmp_path, monkeypatch)
    objs = snapshot.build_objects(paths, now=NOW, use_jenkins=False, variants=registry.load_all(reg, include_off=True))
    ex = contract_examples()
    assert_shape(ex["latest/overview.json"], objs["latest/overview.json"])
    assert_shape(ex["latest/strategies/<id>.json"], objs["latest/strategies/watermelon-cat.json"])
    assert_shape(ex["latest/research.json"], objs["latest/research.json"])
    assert_shape(ex["latest/transactions_24h.json"], objs["latest/transactions_24h.json"])
    ov = objs["latest/overview.json"]
    assert ov["portfolio"]["total_equity_usdc"] is None  # accounts module absent -> null, never 0
    s = ov["strategies"][0]
    assert s["ladder"]["status"] == "hold" and s["open_positions"] == 1
    assert ov["portfolio"]["accounts"][0]["alias"] == "cat"  # credentials missing -> nulls, no network
    blob = json.dumps(objs)
    assert "0x" not in blob  # no addresses anywhere


def test_publish_uploads_and_skips_unchanged_reports(tmp_path, monkeypatch):
    paths, reg = make_world(tmp_path, monkeypatch)
    reports = tmp_path / "repo" / "reports"
    (reports / "daily").mkdir(parents=True)
    (reports / "daily" / "2026-09-22-evening.md").write_text("# r")
    (reports / "index.json").write_text("[]")
    (reports / "context" / "latest").mkdir(parents=True)
    (reports / "context" / "latest" / "meta.json").write_text("{}")

    class FakeStorage:
        def __init__(self):
            self.paths = []

        def upload(self, path, body, content_type="application/json"):
            self.paths.append(path)

    st = FakeStorage()
    snapshot.publish(paths, st, now=NOW, use_jenkins=False, reports_dir=reports)
    assert "latest/overview.json" in st.paths and "latest/transactions_24h.json" in st.paths
    assert "reports/daily/2026-09-22-evening.md" in st.paths and "reports/index.json" in st.paths
    assert not any("context" in p for p in st.paths)
    st2 = FakeStorage()
    snapshot.publish(paths, st2, now=NOW, use_jenkins=False, reports_dir=reports)
    assert not any(p.startswith("reports/") for p in st2.paths)


def test_storage_headers(monkeypatch):
    seen = {}

    class R:
        ok, status_code = True, 200

    def fake_post(url, data=None, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        return R()
    monkeypatch.setattr(snapshot.requests, "post", fake_post)
    snapshot.Storage("https://x.supabase.co", "k").upload("dev/a.json", b"{}")
    assert seen["url"] == "https://x.supabase.co/storage/v1/object/polylab/dev/a.json"
    assert seen["headers"]["x-upsert"] == "true" and seen["headers"]["Authorization"] == "Bearer k"


def test_dump_writes_non_finite_floats_as_null():
    # pending maker entries carry NaN price/cost from pandas; one NaN made JSON.parse reject overview.json
    out = json.loads(snapshot._dump({"a": float("nan"), "b": [float("inf"), 1.5], "c": {"d": -float("inf")}}))
    assert out == {"a": None, "b": [None, 1.5], "c": {"d": None}}
