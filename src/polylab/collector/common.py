"""Collector building blocks shared by discover / poll / stream / backfill.

Scope rules, market classification, outcome-token side mapping, idempotent core.db upserts,
order-book metrics and game-clock normalisation live here so every entry point applies them identically.

Token `side` convention (read by strategies/analysis):
  2-outcome team markets (MLB/NBA/NFL/NHL moneyline, spreads): side = home | away of the outcome team.
  soccer 3-way moneyline = three Yes/No markets: the Yes token's side is home | away | draw (the market's
  subject), the No token's side is `no`. The draw market has market_type `draw`.
  totals (market_type `total`, `line` = goals/points): over | under.
  soccer both-teams-to-score (market_type `btts`): yes | no.
  soccer team to score (market_type `team_to_score`, Gamma `soccer_team_totals` line 0.5 "<Team> O/U 0.5"):
    the Over token's side is home | away (the scoring team, like the soccer moneyline Yes token), Under = `no`.
  Anything unmatched falls back to yes | no | NULL.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from polylab.api.gamma import json_list, parse_time

SPORT_TAGS = {"soccer": 100350, "mlb": 100381, "nba": 745, "nfl": 450, "nhl": 899}

# Gamma /sports `ordering`: which team is listed first in titles, outcomes and the `score` string.
TITLE_FIRST = {"soccer": "home", "mlb": "away", "nba": "away", "nfl": "away", "nhl": "away"}

# Soccer competitions in scope (codes = Gamma /sports `sport`, also the event slug prefix and teams[].league).
# The single shared constant for collection (discover/backfill) AND the default analysis filter.
# Evidence (median whole-game moneyline volume of the latest 4 finished games, probed 2026-10-01, api-sources.md):
# epl 1.79M, ucl 1.53M, unl 1.51M, lal 0.68M, sea 0.60M, bun 0.45M, uel 0.43M, fl1 0.36M, mls 0.09M,
# fifwc 44.7M (2026 World Cup). euc (Euro) has no 2026 games but is the same tier. Friendlies (fif), qualifiers
# (ewq/ueq/uef/afcq), Conference League, domestic cups and every other domestic league are out of scope.
MAJOR_SOCCER_LEAGUES = frozenset({
    "epl", "lal", "bun", "sea", "fl1",            # Europe top-5
    "mls",
    "ucl", "uel",                                 # UEFA Champions League, Europa League
    "fifwc", "euc", "unl",                        # FIFA World Cup, UEFA Euro, UEFA Nations League
})

# Minutes after scheduled start we still treat a not-yet-flagged game as possibly running.
MAX_GAME_SECONDS = {"soccer": 3 * 3600, "mlb": 6 * 3600, "nba": 4 * 3600, "nfl": 5 * 3600, "nhl": 4 * 3600}
# Nominal duration used to bound history windows when no finish time is known.
NOMINAL_GAME_SECONDS = {"soccer": 2 * 3600, "mlb": 3 * 3600 + 1800, "nba": 2 * 3600 + 1800,
                        "nfl": 3 * 3600 + 1800, "nhl": 2 * 3600 + 1800}


@dataclass(frozen=True)
class CollectorConfig:
    sports: tuple[str, ...] = tuple(SPORT_TAGS)
    soccer_leagues: frozenset[str] = MAJOR_SOCCER_LEAGUES
    soccer_min_volume_major: float = 1_000.0      # combined 3-way moneyline volume, allowlisted leagues
    soccer_min_volume_other: float = float("inf")  # other soccer leagues: excluded unless an env override sets a floor
    line_min_volume: float = 50_000.0             # US sports game totals/spreads (per market)
    goal_min_volume: float = 1_000.0              # soccer totals / btts / team_to_score (per market)
    soccer_total_lines: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5)
    lookback_s: int = 6 * 3600
    lookahead_s: int = 120 * 3600                 # discover horizon (pre-game strategies trade up to 120h out)
    poll_lead_s: int = 60 * 60                    # 1-minute poll/stream start this long before kickoff (goal-over enters up to 60 min pre-game)
    pregame_horizon_s: int = 120 * 3600           # low-frequency pre-game snapshots for games starting within
    pregame_every_s: int = 600                    # ... at most once per 10 minutes (poll.run_once throttle)
    history_since: int = 1769904000               # 2026-02-01T00:00:00Z
    extra: dict[str, Any] = field(default_factory=dict)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def load_config() -> CollectorConfig:
    """Defaults above, overridable with env vars (Jenkins / launchd can set them):
    POLYLAB_SPORTS=soccer,mlb  POLYLAB_SOCCER_LEAGUES=epl,lal (or +code to extend)
    POLYLAB_SOCCER_MIN_VOLUME_MAJOR, POLYLAB_SOCCER_MIN_VOLUME_OTHER (unset = other leagues excluded),
    POLYLAB_LINE_MIN_VOLUME (US totals/spreads), POLYLAB_GOAL_MIN_VOLUME (soccer totals/btts/team_to_score),
    POLYLAB_SOCCER_TOTAL_LINES=0.5,1.5,2.5."""
    sports = tuple(s for s in os.environ.get("POLYLAB_SPORTS", ",".join(SPORT_TAGS)).split(",") if s in SPORT_TAGS)
    leagues = set(MAJOR_SOCCER_LEAGUES)
    raw = os.environ.get("POLYLAB_SOCCER_LEAGUES")
    if raw:
        items = [x.strip().lower() for x in raw.split(",") if x.strip()]
        if all(x.startswith("+") for x in items):
            leagues |= {x[1:] for x in items}
        else:
            leagues = {x.lstrip("+") for x in items}
    return CollectorConfig(
        sports=sports or tuple(SPORT_TAGS),
        soccer_leagues=frozenset(leagues),
        soccer_min_volume_major=_env_float("POLYLAB_SOCCER_MIN_VOLUME_MAJOR", 1_000.0),
        soccer_min_volume_other=_env_float("POLYLAB_SOCCER_MIN_VOLUME_OTHER", float("inf")),
        line_min_volume=_env_float("POLYLAB_LINE_MIN_VOLUME", 50_000.0),
        goal_min_volume=_env_float("POLYLAB_GOAL_MIN_VOLUME", 1_000.0),
        soccer_total_lines=_env_lines("POLYLAB_SOCCER_TOTAL_LINES", CollectorConfig.soccer_total_lines),
    )


def _env_lines(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    try:
        return tuple(sorted(float(x) for x in os.environ[name].split(",") if x.strip()))
    except (KeyError, ValueError):
        return default


def now() -> int:
    return int(time.time())


def minute(ts: float) -> int:
    t = int(ts)
    return t - t % 60


# ---------------------------------------------------------------- classification

def league_code(event: dict | None, market: dict | None = None) -> str | None:
    """Competition code: teams[].league, else the event slug prefix ('mls-nyr-stl-2026-09-26' -> 'mls')."""
    for t in (event or {}).get("teams") or []:
        if t.get("league"):
            return str(t["league"]).lower()
    slug = (event or {}).get("slug") or (event or {}).get("ticker") or (market or {}).get("slug") or ""
    return slug.split("-", 1)[0].lower() if slug else None


def soccer_game_included(league: str | None, moneyline_volume: float, cfg: CollectorConfig) -> bool:
    if league and league in cfg.soccer_leagues:
        return moneyline_volume >= cfg.soccer_min_volume_major
    return moneyline_volume >= cfg.soccer_min_volume_other


def is_soccer_draw(m: dict) -> bool:
    q = (m.get("question") or "").lower()
    g = (m.get("groupItemTitle") or "").lower()
    return "end in a draw" in q or g.startswith("draw")


# Gamma sportsMarketType values of the few game-level markets collected beyond the moneyline (observed on
# EPL/La Liga/MLS/UCL events 2026-10-01, api-sources.md). Soccer spreads, halves, corners, exact score,
# player props etc. are out of scope.
EXTRA_SPORTS_MARKET_TYPES = {
    "soccer": ("totals", "both_teams_to_score", "soccer_team_totals"),
    "us": ("totals", "spreads"),
}
EXTRA_MARKET_TYPES = ("total", "spread", "btts", "team_to_score")


def extra_sports_market_types(sport: str) -> tuple[str, ...]:
    return EXTRA_SPORTS_MARKET_TYPES["soccer" if sport == "soccer" else "us"]


def market_type_of(m: dict, sport: str | None = None) -> str | None:
    """moneyline | draw | total | spread | btts | team_to_score for game-level markets; None = out of scope."""
    smt = m.get("sportsMarketType")
    if smt == "moneyline":
        return "draw" if is_soccer_draw(m) else "moneyline"
    if smt == "totals":
        return "total"
    if smt == "spreads":
        return None if sport == "soccer" else "spread"
    if smt == "both_teams_to_score":
        return "btts"
    if smt == "soccer_team_totals" and _float(m.get("line")) == 0.5:
        return "team_to_score"          # "<Team> O/U 0.5": Over == that team scores at least once
    return None


def extra_market_included(m: dict, market_type: str | None, sport: str, cfg: "CollectorConfig") -> bool:
    """Volume floor (current Gamma volume) + line gate for the non-moneyline game markets."""
    vol = market_volume(m)
    if sport == "soccer":
        if market_type == "total":
            line = _float(m.get("line"))
            if line == 0.5:
                # O/U 0.5 is the 0:0 study market (goal-over-all buys it up to 3 days pre-game, when volume is
                # still tiny), so it is always collected for in-scope (major) games regardless of volume.
                return True
            return line is not None and line in cfg.soccer_total_lines and vol >= cfg.goal_min_volume
        return market_type in ("btts", "team_to_score") and vol >= cfg.goal_min_volume
    return market_type in ("total", "spread") and vol >= cfg.line_min_volume


def extra_min_volume(sport: str, cfg: "CollectorConfig") -> float:
    return cfg.goal_min_volume if sport == "soccer" else cfg.line_min_volume


def extra_game_key(m: dict, known_keys: Iterable[str] | dict, by_game_id: dict[str, str]) -> str | None:
    """games.game_key of an extra market. Soccer lines sit in the child "<game> - More Markets" event whose
    `parentEventId` is the main event id (= game_key); `gameId` is missing on about half of the closed child
    events (observed 2026-10-01), so it is only the last resort. US lines sit in the main event itself."""
    ev = (m.get("events") or [{}])[0]
    for k in (ev.get("parentEventId"), ev.get("id")):
        if k is not None and str(k) in known_keys:
            return str(k)
    gid = ev.get("gameId")
    return by_game_id.get(str(gid)) if gid is not None else None


def market_volume(m: dict) -> float:
    for k in ("volumeNum", "volume"):
        try:
            if m.get(k) is not None:
                return float(m[k])
        except (TypeError, ValueError):
            continue
    return 0.0


_norm_re = re.compile(r"[^a-z0-9]+")


def _norm(s: str | None) -> str:
    return _norm_re.sub(" ", (s or "").lower()).strip()


def split_title(title: str | None) -> tuple[str | None, str | None]:
    """'A vs. B - More Markets' -> ('A', 'B')."""
    if not title:
        return None, None
    base = title.split(" - ")[0]
    parts = re.split(r"\s+vs\.?\s+", base, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None, None
    return parts[0].strip(), parts[1].strip()


@dataclass
class Teams:
    home: str | None
    away: str | None
    home_aliases: tuple[str, ...] = ()
    away_aliases: tuple[str, ...] = ()
    first_is_home: bool = True          # title / outcome / score order


def teams_of(event: dict, sport: str) -> Teams:
    """Home/away from Gamma event.teams[].ordering; fallback: title order + sport ordering rule."""
    first_side = TITLE_FIRST.get(sport, "home")
    t1, t2 = split_title(event.get("title"))
    home = away = None
    ha: list[str] = []
    aa: list[str] = []
    for t in event.get("teams") or []:
        names = [x for x in (t.get("name"), t.get("alias"), t.get("abbreviation")) if x and x != "[REDACTED]"]
        if t.get("ordering") == "home":
            home = names[0] if names else home
            ha = names
        elif t.get("ordering") == "away":
            away = names[0] if names else away
            aa = names
    first_is_home = first_side == "home"
    if t1 and (home or away):
        # decide from the title which listed team is home (title order == score order)
        if _matches(t1, ha) and not _matches(t1, aa):
            first_is_home = True
        elif _matches(t1, aa) and not _matches(t1, ha):
            first_is_home = False
    if home is None and away is None and t1:
        home, away = (t1, t2) if first_is_home else (t2, t1)
    ha_t = tuple(dict.fromkeys(ha + ([home] if home else []) + ([t1 if first_is_home else t2] if t1 else [])))
    aa_t = tuple(dict.fromkeys(aa + ([away] if away else []) + ([t2 if first_is_home else t1] if t1 else [])))
    return Teams(home, away, tuple(x for x in ha_t if x), tuple(x for x in aa_t if x), first_is_home)


def _matches(label: str | None, names: Iterable[str]) -> bool:
    lab = _norm(label)
    if not lab:
        return False
    for n in names:
        nn = _norm(n)
        if not nn:
            continue
        if lab == nn:
            return True
        if len(nn) >= 3 and len(lab) >= 3 and (nn in lab or lab in nn):
            return True
    return False


def team_side(label: str | None, teams: Teams) -> str | None:
    h = _matches(label, teams.home_aliases)
    a = _matches(label, teams.away_aliases)
    if h and not a:
        return "home"
    if a and not h:
        return "away"
    # both/neither: try exact normalised equality to break ties
    lab = _norm(label)
    if any(_norm(n) == lab for n in teams.home_aliases):
        return "home"
    if any(_norm(n) == lab for n in teams.away_aliases):
        return "away"
    return None


def soccer_subject(m: dict) -> str | None:
    g = m.get("groupItemTitle")
    if g and not g.lower().startswith("draw"):
        return g
    mt = re.match(r"will (.+?) win", m.get("question") or "", flags=re.I)
    return mt.group(1) if mt else None


def team_total_subject(m: dict) -> str | None:
    """'Arsenal FC O/U 0.5' (groupItemTitle) -> 'Arsenal FC'. The question names both teams, so never use it."""
    g = m.get("groupItemTitle") or ""
    mt = re.match(r"(.+?)\s+O/U\s+[\d.]+\s*$", g, flags=re.I)
    return mt.group(1).strip() if mt else None


def token_rows(m: dict, market_type: str, teams: Teams) -> list[dict]:
    """[{token_id, outcome_index, outcome_label, side}] aligned with clobTokenIds[i] / outcomes[i]."""
    tokens = [str(t) for t in json_list(m.get("clobTokenIds"))]
    labels = [str(x) for x in json_list(m.get("outcomes"))]
    out = []
    yes_no = [lab.lower() for lab in labels] == ["yes", "no"]
    for i, tok in enumerate(tokens):
        label = labels[i] if i < len(labels) else None
        side: str | None
        low = (label or "").lower()
        if market_type == "total" and low in ("over", "under"):
            side = low
        elif market_type == "team_to_score" and low in ("over", "under"):
            side = "no" if low == "under" else team_side(team_total_subject(m), teams)
        elif yes_no:
            if low == "no":
                side = "no"
            elif market_type == "draw":
                side = "draw"
            elif market_type == "moneyline":
                side = team_side(soccer_subject(m), teams) or "yes"
            else:
                side = "yes"
        else:
            side = team_side(label, teams)
        out.append({"token_id": tok, "outcome_index": i, "outcome_label": label, "side": side})
    return out


def gamma_winner(m: dict) -> int | None:
    """Winning outcome index: closed + umaResolutionStatus resolved + exactly one outcomePrices == 1."""
    if not m.get("closed") or m.get("umaResolutionStatus") != "resolved":
        return None
    try:
        prices = [float(p) for p in json_list(m.get("outcomePrices"))]
    except (TypeError, ValueError):
        return None
    winners = [i for i, p in enumerate(prices) if p >= 0.999]
    return winners[0] if len(winners) == 1 else None


def parse_score(score: Any, first_is_home: bool) -> tuple[int | None, int | None]:
    """Gamma/sports `score` 'a-b' is in title order (fixture: NBA 'Wizards vs. Nets' 113-127, Nets won)."""
    if not isinstance(score, str):
        return None, None
    mt = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", score)
    if not mt:
        return None, None
    a, b = int(mt.group(1)), int(mt.group(2))
    return (a, b) if first_is_home else (b, a)


def game_status(event: dict) -> str:
    if event.get("ended") or event.get("closed") or str(event.get("period") or "").upper() in ("VFT", "FT", "F", "FINAL"):
        return "ended"
    if event.get("live"):
        return "live"
    return "scheduled"


# ---------------------------------------------------------------- game clock

PERIOD_RULES = {  # periods, minutes per period, minutes per overtime period
    "nba": (4, 12.0, 5.0),
    "nfl": (4, 15.0, 10.0),
    "nhl": (3, 20.0, 5.0),
}


def _clock_minutes(clock: str | None) -> float | None:
    if not clock:
        return None
    mt = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?:\.\d+)?\s*", str(clock))
    if not mt:
        return None
    return int(mt.group(1)) + int(mt.group(2)) / 60.0


def game_minute(sport: str, period: Any, elapsed: Any, status: Any = None) -> float | None:
    """Normalised game time, or None when not derivable.

    soccer: minutes played incl. stoppage ('45+2' -> 47; HT -> 45; 'elapsed' from the feed).
    nba/nfl/nhl: elapsed regulation minutes = (period-1)*L + (L - clock_remaining); overtime k adds
      n*L + (k-1)*OT + (OT - remaining). Without a clock, the start of the period. HT -> 2*L.
      (`elapsed` for these sports is assumed to be the countdown clock 'MM:SS' — unverified live.)
    mlb: NOT minutes — innings completed: top n -> n-1, middle/bottom n -> n-0.5, end n -> n.
    """
    p = str(period or "").strip().upper()
    if p in ("FT", "VFT", "FINAL", "F", "POST", "NS", "PRE", "SCHEDULED", "AET", "PEN", "END"):
        return None
    if sport == "soccer":
        if p == "HT":
            return 45.0
        mt = re.fullmatch(r"\s*(\d+)\s*(?:\+\s*(\d+))?'?\s*", str(elapsed or ""))
        if mt:
            return float(int(mt.group(1)) + int(mt.group(2) or 0))
        return None
    if sport == "mlb":
        mt = re.search(r"(TOP|BOT|BOTTOM|MID|MIDDLE|END|T|B|M|E)?\s*(\d+)", p)
        if not mt:
            return None
        n = int(mt.group(2))
        half = (mt.group(1) or "T")[0]
        return {"T": n - 1.0, "M": n - 0.5, "B": n - 0.5, "E": float(n)}.get(half, n - 1.0)
    if sport in PERIOD_RULES:
        n_periods, length, ot_len = PERIOD_RULES[sport]
        if p in ("HT", "HALFTIME", "HALF"):
            return 2 * length
        rem = _clock_minutes(elapsed)
        ot = re.fullmatch(r"(\d*)\s*OT\s*(\d*)", p)
        if ot:
            k = int(ot.group(1) or ot.group(2) or 1)
            base = n_periods * length + (k - 1) * ot_len
            return base + (ot_len - rem if rem is not None and rem <= ot_len else 0.0)
        mt = re.search(r"(\d+)", p)
        if not mt:
            return None
        k = int(mt.group(1))
        if k > n_periods:
            base = n_periods * length + (k - n_periods - 1) * ot_len
            return base + (ot_len - rem if rem is not None and rem <= ot_len else 0.0)
        base = (k - 1) * length
        return base + (length - rem if rem is not None and rem <= length else 0.0)
    return None


# ---------------------------------------------------------------- order books

def _levels(rows: Any, reverse: bool) -> list[tuple[float, float]]:
    out = []
    for r in rows or []:
        try:
            p, s = float(r["price"]), float(r["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if s > 0:
            out.append((p, s))
    out.sort(key=lambda x: x[0], reverse=reverse)
    return out


def _imb(bids: list[tuple[float, float]], asks: list[tuple[float, float]], n: int) -> float | None:
    b = sum(s for _, s in bids[:n])
    a = sum(s for _, s in asks[:n])
    return (b - a) / (b + a) if (a + b) > 0 else None


def book_metrics(book: dict) -> dict:
    """Best bid/ask, mid, spread, visible depth (USD = price*size), size imbalance l1/l5/l10, top-10 levels."""
    bids = _levels(book.get("bids"), reverse=True)
    asks = _levels(book.get("asks"), reverse=False)
    bb = bids[0][0] if bids else None
    ba = asks[0][0] if asks else None
    mid = (bb + ba) / 2 if bb is not None and ba is not None else None
    levels = {"b": [[p, s] for p, s in bids[:10]], "a": [[p, s] for p, s in asks[:10]]}
    ts = book.get("timestamp")
    try:
        exchange_ts = int(int(ts) / 1000) if ts is not None else None
    except (TypeError, ValueError):
        exchange_ts = None
    return {
        "best_bid": bb, "best_ask": ba,
        "mid": round(mid, 6) if mid is not None else None,
        "spread": round(ba - bb, 6) if bb is not None and ba is not None else None,
        "bid_depth_usd": round(sum(p * s for p, s in bids), 2),
        "ask_depth_usd": round(sum(p * s for p, s in asks), 2),
        "imb_l1": _imb(bids, asks, 1), "imb_l5": _imb(bids, asks, 5), "imb_l10": _imb(bids, asks, 10),
        "levels_z": zlib.compress(json.dumps(levels, separators=(",", ":")).encode(), 6),
        "exchange_ts": exchange_ts,
        "crossed": bb is not None and ba is not None and bb > ba,
    }


def decode_levels(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob))


# Polymarket shows last trade instead of the midpoint when the spread is wider than 10c; we likewise
# refuse to call such a book's mid a price (it is usually a 0.01/0.99 placeholder book).
MAX_SPREAD_FOR_MID = 0.10


def mid_is_price(metrics: dict) -> bool:
    return (metrics.get("mid") is not None and metrics.get("spread") is not None
            and not metrics.get("crossed") and metrics["spread"] <= MAX_SPREAD_FOR_MID + 1e-9)


# ---------------------------------------------------------------- core.db upserts (idempotent)

def upsert_game(conn: sqlite3.Connection, g: dict, ts: int | None = None) -> None:
    ts = ts or now()
    conn.execute(
        """
        INSERT INTO games(game_key, sport, league, event_slug, title, home_team, away_team, start_time,
                          polymarket_game_id, sportradar_game_id, status, home_score, away_score, ended_at,
                          first_seen, updated_at, source, meta)
        VALUES(:game_key, :sport, :league, :event_slug, :title, :home_team, :away_team, :start_time,
               :polymarket_game_id, :sportradar_game_id, :status, :home_score, :away_score, :ended_at,
               :ts, :ts, :source, :meta)
        ON CONFLICT(game_key) DO UPDATE SET
            league=COALESCE(excluded.league, games.league),
            event_slug=COALESCE(excluded.event_slug, games.event_slug),
            title=COALESCE(excluded.title, games.title),
            home_team=COALESCE(excluded.home_team, games.home_team),
            away_team=COALESCE(excluded.away_team, games.away_team),
            start_time=COALESCE(excluded.start_time, games.start_time),
            polymarket_game_id=COALESCE(excluded.polymarket_game_id, games.polymarket_game_id),
            sportradar_game_id=COALESCE(excluded.sportradar_game_id, games.sportradar_game_id),
            status=CASE WHEN games.status IN ('ended','cancelled') AND excluded.status NOT IN ('ended','cancelled')
                        THEN games.status
                        WHEN games.status = 'live' AND excluded.status = 'scheduled' THEN games.status
                        ELSE COALESCE(excluded.status, games.status) END,
            home_score=COALESCE(excluded.home_score, games.home_score),
            away_score=COALESCE(excluded.away_score, games.away_score),
            ended_at=COALESCE(games.ended_at, excluded.ended_at),
            meta=COALESCE(excluded.meta, games.meta),
            updated_at=excluded.updated_at
        """,
        {**{k: None for k in ("league", "event_slug", "title", "home_team", "away_team", "start_time",
                               "polymarket_game_id", "sportradar_game_id", "status", "home_score",
                               "away_score", "ended_at", "meta")}, **g, "ts": ts},
    )


def upsert_market(conn: sqlite3.Connection, m: dict, ts: int | None = None) -> None:
    ts = ts or now()
    row = {**{k: None for k in ("market_id", "event_id", "game_key", "sports_market_type", "line", "question", "slug",
                                "group_item_title", "volume", "liquidity", "fee_schedule", "neg_risk", "created_at",
                                "end_date", "resolved_outcome_index", "resolved_at", "resolution_source")},
           "closed": 0, **m, "ts": ts}
    conn.execute(
        """
        INSERT INTO markets(condition_id, market_id, event_id, game_key, market_type, sports_market_type, line,
                            question, slug, group_item_title, volume, liquidity, fee_schedule, neg_risk, created_at,
                            end_date, closed, resolved_outcome_index, resolved_at, resolution_source, updated_at)
        VALUES(:condition_id, :market_id, :event_id, :game_key, :market_type, :sports_market_type, :line,
               :question, :slug, :group_item_title, :volume, :liquidity, :fee_schedule, :neg_risk, :created_at,
               :end_date, :closed, :resolved_outcome_index, :resolved_at, :resolution_source, :ts)
        ON CONFLICT(condition_id) DO UPDATE SET
            market_id=COALESCE(excluded.market_id, markets.market_id),
            event_id=COALESCE(excluded.event_id, markets.event_id),
            game_key=COALESCE(excluded.game_key, markets.game_key),
            market_type=excluded.market_type,
            sports_market_type=COALESCE(excluded.sports_market_type, markets.sports_market_type),
            line=COALESCE(excluded.line, markets.line),
            question=COALESCE(excluded.question, markets.question),
            slug=COALESCE(excluded.slug, markets.slug),
            group_item_title=COALESCE(excluded.group_item_title, markets.group_item_title),
            volume=COALESCE(excluded.volume, markets.volume),
            liquidity=COALESCE(excluded.liquidity, markets.liquidity),
            fee_schedule=COALESCE(excluded.fee_schedule, markets.fee_schedule),
            neg_risk=COALESCE(excluded.neg_risk, markets.neg_risk),
            created_at=COALESCE(markets.created_at, excluded.created_at),
            end_date=COALESCE(excluded.end_date, markets.end_date),
            closed=MAX(markets.closed, excluded.closed),
            resolved_outcome_index=COALESCE(markets.resolved_outcome_index, excluded.resolved_outcome_index),
            resolved_at=COALESCE(markets.resolved_at, excluded.resolved_at),
            resolution_source=COALESCE(markets.resolution_source, excluded.resolution_source),
            updated_at=excluded.updated_at
        """,
        row,
    )


def upsert_tokens(conn: sqlite3.Connection, condition_id: str, tokens: list[dict]) -> None:
    for t in tokens:
        conn.execute(
            """
            INSERT INTO tokens(token_id, condition_id, outcome_index, outcome_label, side)
            VALUES(?,?,?,?,?)
            ON CONFLICT(token_id) DO UPDATE SET
                outcome_label=COALESCE(excluded.outcome_label, tokens.outcome_label),
                side=COALESCE(excluded.side, tokens.side)
            """,
            (t["token_id"], condition_id, t["outcome_index"], t["outcome_label"], t["side"]),
        )


def market_row(m: dict, *, game_key: str, market_type: str) -> dict:
    """Gamma market -> markets row (resolution filled when Gamma already shows a winner)."""
    ev = (m.get("events") or [{}])[0]
    fee = {k: m.get(k) for k in ("feeSchedule", "feeType", "feesEnabled", "makerBaseFee", "takerBaseFee")
           if m.get(k) is not None}
    winner = gamma_winner(m)
    line = m.get("line")
    try:
        line = float(line) if line is not None else None
    except (TypeError, ValueError):
        line = None
    return {
        "condition_id": m["conditionId"],
        "market_id": str(m.get("id")) if m.get("id") is not None else None,
        "event_id": str(ev.get("id")) if ev.get("id") is not None else None,
        "game_key": game_key,
        "market_type": market_type,
        "sports_market_type": m.get("sportsMarketType"),
        "line": line,
        "question": m.get("question"),
        "slug": m.get("slug"),
        "group_item_title": m.get("groupItemTitle"),
        "volume": market_volume(m),
        "liquidity": _float(m.get("liquidityNum", m.get("liquidity"))),
        "fee_schedule": json.dumps(fee, separators=(",", ":")) if fee else None,
        "neg_risk": 1 if m.get("negRisk") else 0,
        "created_at": parse_time(m.get("createdAt")),
        "end_date": parse_time(m.get("endDate")),
        "closed": 1 if m.get("closed") else 0,
        "resolved_outcome_index": winner,
        "resolved_at": (parse_time(m.get("closedTime")) or parse_time(m.get("umaEndDate"))) if winner is not None else None,
        "resolution_source": "gamma_outcome_prices" if winner is not None else None,
    }


def game_row(event: dict, sport: str, *, source: str, fallback_start: int | None = None) -> dict:
    teams = teams_of(event, sport)
    hs, as_ = parse_score(event.get("score"), teams.first_is_home)
    status = game_status(event)
    meta = {k: event.get(k) for k in ("seriesSlug", "eventWeek", "period", "score", "elapsed") if event.get(k) not in (None, "")}
    if event.get("eventMetadata"):
        meta["eventMetadata"] = event["eventMetadata"]
    abbrs = {t.get("ordering"): t.get("abbreviation") for t in event.get("teams") or [] if t.get("ordering")}
    if abbrs:
        meta["abbr"] = abbrs
    ended_at = parse_time(event.get("finishedTimestamp")) if status == "ended" else None
    return {
        "game_key": str(event["id"]),
        "sport": sport,
        "league": league_code(event) or sport,
        "event_slug": event.get("slug"),
        "title": event.get("title"),
        "home_team": teams.home,
        "away_team": teams.away,
        "start_time": parse_time(event.get("startTime")) or fallback_start,
        "polymarket_game_id": str(event["gameId"]) if event.get("gameId") is not None else None,
        "sportradar_game_id": str(event["sportradarGameId"]) if event.get("sportradarGameId") else None,
        "status": status,
        "home_score": hs if status == "ended" or event.get("live") else None,
        "away_score": as_ if status == "ended" or event.get("live") else None,
        "ended_at": ended_at,
        "source": source,
        "meta": json.dumps(meta, separators=(",", ":"), default=str) if meta else None,
    }


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- selections

def active_tokens(conn: sqlite3.Connection, ts: int, lead_s: int = 15 * 60) -> list[sqlite3.Row]:
    """Tokens of open markets whose game is live, or scheduled to start within `lead_s`
    (and not flagged ended; unflagged games stay active up to MAX_GAME_SECONDS after start)."""
    max_len = max(MAX_GAME_SECONDS.values())
    rows = conn.execute(
        """
        SELECT t.token_id, t.condition_id, t.side, m.market_type, m.game_key, g.sport, g.status, g.start_time,
               g.polymarket_game_id
        FROM games g
        JOIN markets m ON m.game_key = g.game_key AND m.closed = 0
        JOIN tokens t ON t.condition_id = m.condition_id
        WHERE COALESCE(g.status, 'scheduled') NOT IN ('ended', 'cancelled')
          AND g.ended_at IS NULL
          AND (g.status = 'live' OR (g.start_time <= :hi AND g.start_time >= :lo))
        """,
        {"hi": ts + lead_s, "lo": ts - max_len},
    ).fetchall()
    return [r for r in rows if r["status"] == "live"
            or (r["start_time"] or 0) >= ts - MAX_GAME_SECONDS.get(r["sport"], max_len)]


def pregame_tokens(conn: sqlite3.Connection, ts: int, lead_s: int, horizon_s: int) -> list[sqlite3.Row]:
    """Moneyline/draw tokens (both outcomes) — plus soccer Total 0.5 tokens, which goal-over-all buys up to 3 days
    pre-game — of open markets whose game starts in (ts+lead, ts+horizon] and is not live yet: the low-frequency
    pre-game set (the <=lead window is covered every minute)."""
    return conn.execute(
        """
        SELECT t.token_id, t.condition_id, t.side, m.market_type, m.game_key, g.sport, g.status, g.start_time
        FROM games g
        JOIN markets m ON m.game_key = g.game_key AND m.closed = 0
             AND (m.market_type IN ('moneyline', 'draw') OR (m.market_type = 'total' AND m.line = 0.5))
        JOIN tokens t ON t.condition_id = m.condition_id
        WHERE COALESCE(g.status, 'scheduled') = 'scheduled' AND g.ended_at IS NULL
          AND g.start_time > :lo AND g.start_time <= :hi
        """,
        {"lo": ts + lead_s, "hi": ts + horizon_s},
    ).fetchall()


# ---------------------------------------------------------------- quality events

class QualityLog:
    """Aggregates quality issues per (kind, ref) and writes one row each with a count + worst value,
    so a crossed book every minute does not flood quality_events."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str | None], dict] = {}

    def add(self, kind: str, ref: str | None, value: float | None = None, **detail: Any) -> None:
        key = (kind, ref)
        item = self.items.setdefault(key, {"count": 0})
        item["count"] += 1
        if value is not None:
            item["max"] = max(item.get("max", value), value)
        for k, v in detail.items():
            item.setdefault(k, v)

    def flush(self, conn: sqlite3.Connection) -> int:
        ts = now()
        for (kind, ref), detail in self.items.items():
            conn.execute("INSERT INTO quality_events(ts, kind, ref, detail) VALUES(?,?,?,?)",
                         (ts, kind, ref, json.dumps(detail, separators=(",", ":"), default=str)))
        n = len(self.items)
        self.items.clear()
        return n

    def __len__(self) -> int:
        return len(self.items)


def write_price_bars(conn: sqlite3.Connection, rows: Iterable[tuple[str, int, str, float, int | None]]) -> int:
    """rows: (token_id, minute_ts, source, price, received_at). Later writes for the same key win."""
    rows = list(rows)
    conn.executemany(
        "INSERT OR REPLACE INTO price_bars(token_id, ts, source, price, received_at) VALUES(?,?,?,?,?)", rows)
    return len(rows)


def job_start(conn: sqlite3.Connection, job: str) -> int:
    cur = conn.execute("INSERT INTO job_runs(job, started_at) VALUES(?,?)", (job, now()))
    conn.commit()
    return int(cur.lastrowid)


def job_finish(conn: sqlite3.Connection, run_id: int, ok: bool, summary: dict) -> None:
    conn.execute("UPDATE job_runs SET finished_at=?, ok=?, summary=? WHERE id=?",
                 (now(), 1 if ok else 0, json.dumps(summary, separators=(",", ":"), default=str), run_id))
    conn.commit()
