"""Offline helpers for the research scripts: N and its pieces on cached bars only (no network).

Reads the main checkout's data/history.sqlite (the 5-minute Yahoo cache holds ~930 stocks × 59 sessions,
07/29–10/22). Pure Python: the project has no numpy and the night shift adds no dependency.
"""

from __future__ import annotations

import asyncio
import math
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # research/<study|methods>/ → the repo
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.finder import FinderParams, run_search  # noqa: E402
from openmarket.history.metrics import industry_grouping  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402

DB = Path("D:/git/open-market/data/history.sqlite")
INDEXES = ("^TWII", "IX0043.TWO")


def store() -> HistoryStore:
    return HistoryStore(DB)


def cached_days(s: HistoryStore) -> list[date]:
    """Trading days with the 5-minute cache filled (≥ 800 stocks), oldest first."""
    with s._conn() as c:
        rows = c.execute("SELECT date, COUNT(*) AS n FROM yahoo_5m GROUP BY date HAVING n >= 800 ORDER BY date").fetchall()
    return [date.fromisoformat(r["date"]) for r in rows]


def offline_fetch(s: HistoryStore):
    """run_search's fetch from the 5-minute cache only: a stock without cached bars is left out."""

    async def fetch(rows, sessions, done=lambda: None):
        out = {}
        for r in rows:
            bars = s.yahoo_bars(r["code"], sessions)
            done()
            if bars.get(sessions[-1]):
                out[r["code"]] = ({x: bars.get(x, []) for x in sessions}, 5)
        return out

    return fetch


def run(s: HistoryStore, d: date, method: str = "known", **params) -> dict:
    """One N search on cached bars (industry grouping for 找法1)."""
    p = FinderParams(groupings=("industry",), **params)
    return asyncio.run(run_search(s, d, method, p, {"industry": industry_grouping}, offline_fetch(s)))


# ---- small linear algebra (pure Python) ----------------------------------------------------------

def pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def pair_r(a: list, b: list, min_points: int = 20) -> float | None:
    pts = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    return pearson([x for x, _ in pts], [y for _, y in pts]) if len(pts) >= min_points else None


def quantile(xs: list[float], q: float) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def rng(seed: int = 7) -> random.Random:
    return random.Random(seed)
