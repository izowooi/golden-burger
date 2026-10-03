"""Optional AI-prediction log for Track 2: `manual/predictions/YYYY-MM-DD.md` in the repo.

Expected (tolerant) markdown table:
    | 경기 | 엔진 | P(0:0) | 순위 | 메모 |
    | Greece vs Netherlands | claude | 7% | 1 | ... |
Header names are matched loosely (경기/game/match, 엔진/engine/model, P(0:0)/p00/확률, 순위/rank, 메모/memo/note);
P accepts `7%`, `0.07` or `7`. Rows that cannot be read are counted, never guessed. `sync` mirrors every file into
`<data>/manual/predictions.db` (replaced each run); `match` joins predictions to manual positions by team names and
date (best effort; unmatched rows are reported).
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from pathlib import Path

from polylab import settings
from polylab.analysis import _common as C
from polylab.db import connect, tx
from polylab.manual import ledger

PREDICTIONS_DIR = settings.REPO_ROOT / "manual" / "predictions"
FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
SCHEMA = ("""
    CREATE TABLE IF NOT EXISTS predictions (
        file_date TEXT NOT NULL,                -- KST date from the file name
        row_no INTEGER NOT NULL,
        game TEXT NOT NULL,
        engine TEXT,                            -- claude | chatgpt | other text as written
        p00 REAL,                               -- AI probability of a 0-0 result (0..1)
        rank INTEGER,
        memo TEXT,
        PRIMARY KEY (file_date, row_no)
    )""",)
COLUMNS = {"game": ("경기", "game", "match", "fixture"), "engine": ("엔진", "engine", "model", "ai"),
           "p00": ("p(0:0)", "p(0-0)", "p00", "0:0", "0-0", "확률", "prob"), "rank": ("순위", "rank"),
           "memo": ("메모", "memo", "note", "비고")}
STOP = {"vs", "v", "fc", "cf", "sc", "afc", "the", "de", "republic"}


def db_path(paths) -> Path:
    return ledger.manual_dir(paths) / "predictions.db"


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _column_map(header: list[str]) -> dict[str, int]:
    out = {}
    for i, h in enumerate(header):
        low = h.lower().replace(" ", "")
        for key, names in COLUMNS.items():
            if key not in out and any(n.replace(" ", "") in low for n in names):
                out[key] = i
                break
    return out


def parse_p(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%)?", text.replace(",", "."))
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2) or v > 1:
        v /= 100.0
    return round(v, 6) if 0 <= v <= 1 else None


def norm_engine(text: str | None) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    if "claude" in low:
        return "claude"
    if any(k in low for k in ("chatgpt", "gpt", "openai", "codex")):
        return "chatgpt"
    return low[:20]


def parse_text(text: str) -> tuple[list[dict], int]:
    """(rows, skipped). Every markdown table whose header names a game column is read."""
    rows, skipped, cmap = [], 0, None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            cmap = None
            continue
        cells = _cells(line)
        if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells):
            continue
        if cmap is None:
            m = _column_map(cells)
            cmap = m if "game" in m else None
            continue
        get = lambda k: cells[cmap[k]] if k in cmap and cmap[k] < len(cells) else None  # noqa: E731
        game = (get("game") or "").strip()
        if not game:
            skipped += 1
            continue
        rank = re.search(r"\d+", get("rank") or "")
        rows.append({"game": game[:200], "engine": norm_engine(get("engine")), "p00": parse_p(get("p00")),
                     "rank": int(rank.group()) if rank else None, "memo": (get("memo") or "")[:500] or None})
    return rows, skipped


def sync(paths, directory: Path = PREDICTIONS_DIR) -> dict:
    files = sorted(p for p in directory.glob("*.md") if FILE_RE.match(p.name)) if directory.exists() else []
    parsed, skipped = [], 0
    for f in files:
        rows, s = parse_text(f.read_text(errors="replace"))
        skipped += s
        parsed += [{**r, "file_date": FILE_RE.match(f.name).group(1), "row_no": i} for i, r in enumerate(rows)]
    conn = connect(db_path(paths), SCHEMA)
    try:
        with tx(conn):
            conn.execute("DELETE FROM predictions")
            conn.executemany("INSERT INTO predictions(file_date, row_no, game, engine, p00, rank, memo) "
                             "VALUES(:file_date, :row_no, :game, :engine, :p00, :rank, :memo)", parsed)
    finally:
        conn.close()
    return {"files": len(files), "rows": len(parsed), "skipped": skipped}


def load(paths) -> list[dict]:
    conn = C.open_ro(db_path(paths))
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM predictions ORDER BY file_date, row_no")]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ------------------------------------------------------------------ matching

def _tokens(text: str | None) -> set[str]:
    words = re.findall(r"\w+", (text or "").lower())
    return {w for w in words if w not in STOP and not w.isdigit()}


def _sides(text: str | None) -> list[set[str]]:
    parts = re.split(r"\s+(?:vs\.?|v\.?|-|–|:)\s+", (text or "").strip(), maxsplit=1, flags=re.I)
    return [_tokens(p) for p in parts if _tokens(p)]


def same_game(pred_game: str, title: str | None) -> bool:
    ps, ts = _sides(pred_game), _sides(title)
    if len(ps) == 2 and len(ts) == 2:
        return all(any(a & b for b in ts) for a in ps)
    a, b = _tokens(pred_game), _tokens(title)
    return bool(a and b) and len(a & b) / len(a | b) >= 0.5


def _kst_date(ts: int | None) -> dt.date | None:
    return dt.datetime.fromtimestamp(int(ts), C.KST).date() if ts else None


def match(preds: list[dict], positions: list[dict]) -> tuple[list[dict], list[dict]]:
    """(pairs, unmatched predictions). A prediction pairs with every Track 2 position on the same game whose
    kickoff (or entry when unknown) is within -1..+2 days of the file date."""
    pairs, unmatched = [], []
    for p in preds:
        day = dt.date.fromisoformat(p["file_date"])
        hits = []
        for pos in positions:
            when = _kst_date(pos.get("start_time") or pos.get("opened_at"))
            if when is None or not (-1 <= (when - day).days <= 2):
                continue
            if same_game(p["game"], pos.get("game_title")):
                hits.append(pos)
        if hits:
            pairs += [{"prediction": p, "position": h} for h in hits]
        else:
            unmatched.append(p)
    return pairs, unmatched
