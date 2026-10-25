"""N · 資金連動搜尋: find stocks that one pool of money moved together.

Two searches share one funnel, cheapest test first (spec: memory linkage-finder-pending):

- 找法1 (known): inside every group of the known groupings (官方產業 / 自訂 / 概念股 / 細產業 / 新聞族群).
- 找法2 (new): a candidate pool from the day's rankings (漲幅, 週轉率, 成交比重暴增), then the found
  groups are matched back to the known groupings (已知 / 延伸 / 跨族群新組合).

Funnel per pair: daily correlation (local data) → intraday 5-minute correlation over the last few
sessions, limit-up-locked stretches left out → the intraday high (09:00–13:00) within a few minutes
of each other, not merely both at the 09:00 open. Pairs become groups only as dense cores (every
member passes with at least `dense` of the others), never by chaining A~B~C.

Every threshold is a FinderParams field: the user expects to tune them. Market-beta removal is
deliberately not applied yet (to be validated later).
"""

from __future__ import annotations

import json
import math
import operator
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from itertools import combinations

from .linkage import align_bars, amplitude_pct, limit_up_price, minutes, pearson, turnover_rate
from .store import HistoryStore

FINDER_VERSION = 11  # part of the cache key: bump when the search logic changes, so stored results are redone (5 was used by a reverted experiment)
EXCESS_R = 0.4  # 扣指數後分時 R: the funnel's default and the 🧾 扣指數 check alike (evidence.js EXCESS_R)
INTRADAY_BASES = ("raw", "market", "sector", "pca1")  # the 分時 R the funnel filters on: as is / 扣大盤 / 扣大盤＋類股 / 扣第 1 主成分
PEAK_END = 240  # the intraday high is taken over 09:00–13:00
OPEN_MINUTES = 2  # a high at 09:00–09:02 is the open, not a signal (unless locked limit-up)
BUCKETS = 54  # 5-minute buckets 09:00–13:25


@dataclass(frozen=True)
class FinderParams:
    min_amplitude: float = 3.0  # 找法1: only stocks whose 振幅 that day is above this %
    min_volume_lots: float = 300  # 找法1: and that traded at least this many 張 (1 張 = 1,000 shares)
    daily_window: int = 20  # 找法1: days of daily % changes
    daily_r: float = 0.8
    new_daily_window: int = 10  # 找法2: a new combination has little history yet
    new_daily_r: float = 0.8
    intraday_r: float = 0.5  # 5-minute returns (basis "raw")
    intraday_basis: str = "sector"  # INTRADAY_BASES: the funnel's 分時 R once each stock's index is taken out
    excess_r: float = EXCESS_R  # the threshold on that excess R (it runs well below the raw R)
    intraday_sessions: int = 3
    intraday_min_points: int = 20
    peak_gap: int = 8  # minutes between intraday highs
    start_gap: int | None = None  # 起漲時間 gap; None = shown, not required
    require_burst: bool = False  # need a 5-minute window where both traded ≥ burst_ratio × their median
    burst_ratio: float = 3.0
    dense: float = 0.7  # each member passes with ≥ this share of the group
    min_size: int = 2
    pool_top: int = 50  # 找法2: top N of each ranking
    new_min_volume_lots: float = 300  # 找法2: the pool only takes stocks that traded at least this many 張
    pool_sources: tuple[str, ...] = ("gain", "turnover", "share_z", "gap", "vol_ratio")
    share_window: int = 20  # days behind the 成交比重暴增 z-score
    vol_ratio_window: int = 5  # 量比: today's volume against the average of this many days before
    groupings: tuple[str, ...] = ("industry", "custom", "concept", "chain", "news")
    max_fetch: int = 400  # at most this many stocks get intraday bars (largest candidate sets first)
    # added 2026-10-26, off by default (OPTIONAL_DEFAULTS): 扣指數門檻 fixed or set each day from random pairs
    excess_r_mode: str = "fixed"  # EXCESS_R_MODES

    def key(self) -> str:
        # list-type settings are sets: the same groupings in another order are the same search; a setting added
        # later is left out while at its default, so the results stored before it keep their key
        fields = {k: sorted(v) if isinstance(v, (list, tuple)) else v for k, v in asdict(self).items()
                  if not (k in OPTIONAL_DEFAULTS and v == OPTIONAL_DEFAULTS[k])}
        return json.dumps({**fields, "v": FINDER_VERSION}, sort_keys=True, ensure_ascii=False)


EXCESS_R_MODES = ("fixed", "adaptive")
OPTIONAL_DEFAULTS = {"excess_r_mode": "fixed"}  # settings added after results were stored (see FinderParams.key)
ADAPTIVE_Q = 0.995  # 自適應: the excess R that only 0.5% of the day's random cross-industry pairs reach (research/exp1b)
ADAPTIVE_PAIRS = 3000
ADAPTIVE_BOUNDS = (0.25, 0.6)  # never looser or tighter than this, whatever the day's pairs say
ADAPTIVE_MIN_PAIRS = 300  # fewer usable random pairs: keep the fixed threshold


def adaptive_excess_r(feats: Mapping[str, "Intraday"], by_code: Mapping[str, dict], p: "FinderParams", seed: str) -> float | None:
    """The day's 扣指數門檻 from random pairs of the fetched stocks in different industries (which should not
    move together): their ADAPTIVE_Q quantile of excess R, kept within ADAPTIVE_BOUNDS. None with too few pairs."""
    import random

    codes = sorted(c for c, f in feats.items() if c in by_code and f.excess is not None)
    if len(codes) < 20:
        return None
    rng = random.Random(seed)
    rs = []
    for _ in range(ADAPTIVE_PAIRS):
        a, b = rng.sample(codes, 2)
        if by_code[a].get("industry") == by_code[b].get("industry"):
            continue
        r = series_r(feats[a].excess, feats[b].excess, p.intraday_min_points)
        if r is not None:
            rs.append(r)
    if len(rs) < ADAPTIVE_MIN_PAIRS:
        return None
    rs.sort()
    q = rs[min(len(rs) - 1, int(ADAPTIVE_Q * len(rs)))]
    return round(min(max(q, ADAPTIVE_BOUNDS[0]), ADAPTIVE_BOUNDS[1]), 3)


GROUPING_LABELS = {"industry": "官方產業", "custom": "自訂族群", "concept": "概念股", "chain": "細產業", "news": "新聞族群", "group": "集團"}


# ---- stage A: daily ------------------------------------------------------------------------------

def daily_series(store: HistoryStore, day: date, window: int) -> dict[str, list[float | None]]:
    """{code: daily % changes over the last `window` trading days up to `day`} for common stocks traded that day."""
    dates = sorted(store.trading_dates(up_to=day, limit=window))
    if not dates or dates[-1] != day:
        return {}
    by_day = store.change_rows(dates)
    today = {r["code"] for r in by_day[day] if r.get("change_pct") is not None}
    kinds = {r["code"]: r.get("kind") for r in store.day_rows(day)}
    out: dict[str, list[float | None]] = {}
    for i, d in enumerate(dates):
        for r in by_day[d]:
            c = r["code"]
            if c in today and kinds.get(c) == "stock":
                out.setdefault(c, [None] * len(dates))[i] = r["change_pct"]
    return out


def series_r(a: list, b: list, min_points: int) -> float | None:
    pts = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pts) < min_points:
        return None
    return pearson([x for x, _ in pts], [y for _, y in pts])


class PairR:
    """Daily R between any two stocks, each pair computed once (the same pair recurs across groupings).

    A stock with no missing day is standardised up front, so its R with another complete one is a
    single dot product; a pair with gaps goes through the plain Pearson on the shared days."""

    def __init__(self, series: Mapping[str, list]):
        self.series = series
        n = len(next(iter(series.values()))) if series else 0
        self.n = n
        self.need = max(3, n - 2)  # most of the window (all but 2 days)
        self.z: dict[str, list[float]] = {}
        for c, s in series.items():
            if n >= 3 and None not in s:
                m = sum(s) / n
                sd = (sum((x - m) ** 2 for x in s) / n) ** 0.5
                if sd > 0:
                    self.z[c] = [(x - m) / sd for x in s]
        self.memo: dict[tuple[str, str], float | None] = {}

    def __call__(self, a: str, b: str) -> float | None:
        key = (a, b) if a < b else (b, a)
        if key in self.memo:
            return self.memo[key]
        za, zb = self.z.get(a), self.z.get(b)
        if za is not None and zb is not None:
            r = sum(map(operator.mul, za, zb)) / self.n
        else:
            r = series_r(self.series[a], self.series[b], self.need)
        self.memo[key] = r
        return r


def daily_edges(codes: Iterable[str], series: Mapping[str, list] | PairR, threshold: float) -> dict[frozenset, float]:
    """Pairs with daily R ≥ threshold → {frozenset({a, b}): r}. Needs most of the window (all but 2 days)."""
    pr = series if isinstance(series, PairR) else PairR(series)
    codes = sorted({c for c in codes if c in pr.series})
    out = {}
    for a, b in combinations(codes, 2):
        r = pr(a, b)
        if r is not None and r >= threshold:
            out[frozenset((a, b))] = r
    return out


def dense_cores(edges: Iterable[frozenset], dense: float, min_size: int) -> list[list[str]]:
    """Groups where every member is linked to ≥ dense × (size − 1) of the others.

    Seeded from the best-connected stock: its connected component is peeled (the weakest member out)
    until dense; then the next seed. Members of a found core are not reused."""
    adj: dict[str, set[str]] = {}
    for e in edges:
        a, b = tuple(e)
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    left = set(adj)
    cores = []
    while left:
        seed = max(left, key=lambda v: (len(adj[v] & left), v))
        comp, todo = {seed}, [seed]
        while todo:
            v = todo.pop()
            for w in adj[v] & left:
                if w not in comp:
                    comp.add(w)
                    todo.append(w)
        group = set(comp)
        while len(group) >= min_size:
            need = math.ceil(dense * (len(group) - 1))
            worst = min(group, key=lambda v: (len(adj[v] & group), v))
            if len(adj[worst] & group) >= need:
                break
            group.discard(worst)
        if len(group) >= min_size and seed in group:
            cores.append(sorted(group))
            left -= group
        else:
            left.discard(seed)
    return cores


def known_candidates(rows: list[dict], groupings: Mapping[str, Callable[[dict], list[str]]], series: Mapping[str, list], p: FinderParams) -> list[dict]:
    """找法1 stage A: dense daily cores inside each known group (members with 振幅 > min_amplitude and
    at least min_volume_lots 張 that day); the same member set from several groupings is one
    candidate listing all its sources."""
    by_members: dict[tuple, dict] = {}
    pr = PairR(series)
    for gname, grouping in groupings.items():
        groups: dict[str, list[str]] = {}
        for r in rows:
            if (
                r.get("kind") == "stock"
                and r["code"] in series
                and (amplitude_pct(r) or 0) > p.min_amplitude
                and (r.get("volume_shares") or 0) / 1000 >= p.min_volume_lots
            ):
                for g in grouping(r):
                    groups.setdefault(g, []).append(r["code"])
        for g, codes in groups.items():
            if len(codes) < p.min_size:
                continue
            edges = daily_edges(codes, pr, p.daily_r)
            for core in dense_cores(edges, p.dense, p.min_size):
                key = tuple(core)
                c = by_members.setdefault(key, {"members": core, "sources": [], "daily": {}})
                c["sources"].append({"grouping": gname, "label": GROUPING_LABELS.get(gname, gname), "group": g})
                c["daily"].update({e: r for e, r in edges.items() if e <= set(core)})
    return list(by_members.values())


def share_z(store: HistoryStore, day: date, window: int) -> dict[str, float]:
    """成交比重暴增: each stock's share of the market's turnover today as a z-score vs the previous `window` days."""
    dates = sorted(store.trading_dates(up_to=day, limit=window + 1))
    if len(dates) < 3 or dates[-1] != day:
        return {}
    rows = store.rows(dates)
    shares: dict[str, list[float]] = {}
    for i, d in enumerate(dates):
        total = sum(r.get("turnover_yuan") or 0 for r in rows[d] if r.get("kind") == "stock")
        if not total:
            continue
        for r in rows[d]:
            if r.get("kind") == "stock":
                shares.setdefault(r["code"], [0.0] * len(dates))[i] = (r.get("turnover_yuan") or 0) / total * 100
    out = {}
    for c, s in shares.items():
        hist, now = s[:-1], s[-1]
        m = sum(hist) / len(hist)
        sd = (sum((x - m) ** 2 for x in hist) / len(hist)) ** 0.5
        out[c] = (now - m) / max(sd, 0.01)
    return out


def volume_ratio(store: HistoryStore, day: date, window: int) -> dict[str, float]:
    """量比: each stock's volume on `day` ÷ its average volume over the `window` trading days before
    (days it did not trade are left out of the average; no history → no ratio)."""
    dates = sorted(store.trading_dates(up_to=day, limit=window + 1))
    if len(dates) < 2 or dates[-1] != day:
        return {}
    rows = store.rows(dates)
    before: dict[str, list[float]] = {}
    for d in dates[:-1]:
        for r in rows[d]:
            if r.get("kind") == "stock" and (r.get("volume_shares") or 0) > 0:
                before.setdefault(r["code"], []).append(r["volume_shares"])
    out = {}
    for r in rows[day]:
        hist = before.get(r["code"])
        if r.get("kind") == "stock" and hist and r.get("volume_shares") is not None:
            out[r["code"]] = r["volume_shares"] / (sum(hist) / len(hist))
    return out


def gap_pct(r: Mapping) -> float | None:
    """開盤跳空: (開盤 − 昨收) ÷ 昨收, in %."""
    o, prev = r.get("open"), r.get("prev_close")
    return (o / prev - 1) * 100 if o and prev else None


def candidate_pool(rows: list[dict], z: Mapping[str, float], p: FinderParams, vr: Mapping[str, float] | None = None) -> dict[str, list[str]]:
    """找法2: top `pool_top` common stocks of each ranking (漲幅, 週轉率, 比重暴增, 開盤跳空, 量比) →
    {code: [sources]}; stocks under `new_min_volume_lots` 張 are left out first (two thin stocks
    moving together are no pool of money). `vr`: the 量比 of each stock (volume_ratio)."""
    vr = vr or {}
    stocks = [
        r for r in rows
        if r.get("kind") == "stock" and r.get("change_pct") is not None
        and (r.get("volume_shares") or 0) / 1000 >= p.new_min_volume_lots
    ]
    rankers = {
        "gain": ("漲幅", lambda r: r.get("change_pct")),
        "turnover": ("週轉率", turnover_rate),
        "share_z": ("比重暴增", lambda r: z.get(r["code"])),
        "gap": ("跳空", gap_pct),
        "vol_ratio": ("量比", lambda r: vr.get(r["code"])),
    }
    pool: dict[str, list[str]] = {}
    for src in p.pool_sources:
        label, key = rankers[src]
        ranked = sorted((r for r in stocks if key(r) is not None), key=lambda r: -key(r))[: p.pool_top]
        for r in ranked:
            pool.setdefault(r["code"], []).append(label)
    return pool


# ---- stage B/C: intraday -------------------------------------------------------------------------

@dataclass
class Intraday:
    """One stock's intraday features: 5-minute returns over the sessions (locked stretches left out),
    and for the shown day the high / 起漲 minutes, limit-up lock and volume bursts."""
    returns: list[float | None] = field(default_factory=list)
    peak: int | None = None
    start: int | None = None
    locked_at: int | None = None
    bursts: set[int] = field(default_factory=set)
    resolution: int = 1
    excess: list[float | None] | None = None  # returns − β × its index's (factors.factor_of); None without the index
    factor: str = ""  # that index: 半導體類 / 大盤 / 櫃買


def bucket_returns(bars: list[tuple], limit: float | None) -> list[float | None]:
    """5-minute close-to-close returns on the fixed 09:00–13:25 axis; a step where both closes sit at
    the limit-up price (locked) is None, so a flat locked line does not count as 'not moving together'."""
    close: dict[int, float] = {}
    for b in bars:
        m = minutes(b[0])
        if 0 <= m < BUCKETS * 5:
            close[m // 5] = b[3]
    out = []
    for i in range(BUCKETS - 1):
        x, y = close.get(i), close.get(i + 1)
        if not x or not y:
            out.append(None)
        elif limit and x >= limit - 0.001 and y >= limit - 0.001:
            out.append(None)
        else:
            out.append((y / x - 1) * 100)
    return out


def intraday_features(by_session: Mapping[date, list[tuple]], rows_by_session: Mapping[date, dict], day: date, p: FinderParams, resolution: int = 1) -> Intraday:
    """Bars (HH:MM, high, low, close[, open, volume]) per session, already aligned to official prices."""
    f = Intraday(resolution=resolution)
    # every session gets its 53 slots, a session without bars all None: the series of two stocks (and of
    # the index) stay aligned day by day even when one of them missed a day
    for s in sorted(set(by_session) | set(rows_by_session)):
        row = rows_by_session.get(s) or {}
        f.returns.extend(bucket_returns(by_session.get(s, []), limit_up_price(row.get("prev_close"))))
    bars = [b for b in by_session.get(day, []) if 0 <= minutes(b[0]) <= 270]
    row = rows_by_session.get(day) or {}
    if not bars:
        return f
    window = [b for b in bars if minutes(b[0]) <= PEAK_END]
    if window:
        hi = max(b[1] for b in window)
        f.peak = next(minutes(b[0]) for b in window if b[1] == hi)
        before = [b for b in window if minutes(b[0]) <= f.peak]
        lo = min(b[2] for b in before)
        f.start = max(minutes(b[0]) for b in before if b[2] == lo)  # the last touch of the low before the run
    limit = limit_up_price(row.get("prev_close"))
    if limit is not None:
        f.locked_at = next((minutes(b[0]) for b in bars if b[3] >= limit - 0.001), None)
    vol: dict[int, float] = {}
    for b in bars:
        if len(b) > 5 and b[5] is not None:
            vol[minutes(b[0]) // 5] = vol.get(minutes(b[0]) // 5, 0) + b[5]
    inner = sorted(v for k, v in vol.items() if 0 < k < BUCKETS and v > 0)  # the open / close auctions aside
    if inner:
        med = inner[len(inner) // 2]
        f.bursts = {k for k, v in vol.items() if 0 < k < BUCKETS and v >= p.burst_ratio * med}
    return f


def pair_check(a: Intraday, b: Intraday, p: FinderParams) -> dict:
    """Stage B/C for one pair → {ok, intraday_r, excess_r, peak_gap, start_gap, burst, reason}. The 分時 R
    gate is the raw R (basis "raw", ≥ intraday_r) or the R after each stock's index (≥ excess_r)."""
    r = series_r(a.returns, b.returns, p.intraday_min_points)
    ex = series_r(a.excess, b.excess, p.intraday_min_points) if a.excess is not None and b.excess is not None else None
    both_locked = a.locked_at is not None and b.locked_at is not None
    out = {"intraday_r": r, "excess_r": ex, "peak_gap": None, "start_gap": None, "burst": sorted(a.bursts & b.bursts), "ok": False, "reason": ""}
    raw = p.intraday_basis == "raw"
    gate, need = (r, p.intraday_r) if raw else (ex, p.excess_r)
    if not raw and (a.excess is None or b.excess is None) and not both_locked:
        out["reason"] = "沒有指數分時"
        return out
    if gate is None:
        if not both_locked:  # too few points (locked all day) only passes when both are locked
            out["reason"] = "分時資料不足"
            return out
    elif gate < need:
        out["reason"] = "分時相關不足" if raw else "扣指數後相關不足"
        return out
    if a.peak is None or b.peak is None:
        out["reason"] = "沒有高點時間"
        return out
    out["peak_gap"] = abs(a.peak - b.peak)
    if a.start is not None and b.start is not None:
        out["start_gap"] = abs(a.start - b.start)
    if max(a.peak, b.peak) <= OPEN_MINUTES and not both_locked:
        out["reason"] = "開盤即高點（假同步）"
        return out
    if out["peak_gap"] > p.peak_gap:
        out["reason"] = "高點時間差太大"
        return out
    if p.start_gap is not None and (out["start_gap"] is None or out["start_gap"] > p.start_gap):
        out["reason"] = "起漲時間差太大"
        return out
    if p.require_burst and not out["burst"]:
        out["reason"] = "沒有同時爆量"
        return out
    out["ok"] = True
    return out


def confirm(candidates: list[dict], feats: Mapping[str, Intraday], p: FinderParams, funnel: dict | None = None) -> list[dict]:
    """Stage B/C on each candidate's pairs; the passing pairs become dense cores again.
    `funnel`, when given, is filled with what happened to each distinct pair (for tuning thresholds):
    {pairs, no_bars, passed, rejected: {reason: count}}."""
    found = []
    seen = set()
    outcome: dict[frozenset, str] = {}  # pair → '' (passed) / reason / '沒有分 K'
    for c in candidates:
        checks = {}
        for a, b in combinations(c["members"], 2):
            e = frozenset((a, b))
            if a in feats and b in feats:
                checks[e] = pair_check(feats[a], feats[b], p)
                outcome[e] = "" if checks[e]["ok"] else checks[e]["reason"]
            else:
                outcome.setdefault(e, "沒有分 K")
        ok = [e for e, ch in checks.items() if ch["ok"]]
        for core in dense_cores(ok, p.dense, p.min_size):
            key = tuple(core)
            if key in seen:
                continue
            seen.add(key)
            inside = {e: ch for e, ch in checks.items() if e <= set(core)}
            found.append({**c, "members": core, "pairs": inside, "daily": {e: r for e, r in c.get("daily", {}).items() if e <= set(core)}})
    if funnel is not None:
        rejected: dict[str, int] = {}
        for why in outcome.values():
            if why and why != "沒有分 K":
                rejected[why] = rejected.get(why, 0) + 1
        funnel.update(
            pairs=len(outcome),
            no_bars=sum(1 for why in outcome.values() if why == "沒有分 K"),
            passed=sum(1 for why in outcome.values() if why == ""),
            rejected=dict(sorted(rejected.items(), key=lambda kv: -kv[1])),
        )
    return found


# ---- describing a found group -------------------------------------------------------------------

def match_known(members: list[str], rows_by_code: Mapping[str, dict], groupings: Mapping[str, Callable[[dict], list[str]]]) -> list[dict]:
    """The best-overlapping known group per grouping: [{grouping, label, group, overlap, outside}]."""
    out = []
    for gname, grouping in groupings.items():
        count: dict[str, list[str]] = {}
        for c in members:
            row = rows_by_code.get(c)
            for g in grouping(row) if row else []:
                count.setdefault(g, []).append(c)
        if not count:
            continue
        g, inside = max(count.items(), key=lambda kv: (len(kv[1]), kv[0]))
        out.append({"grouping": gname, "label": GROUPING_LABELS.get(gname, gname), "group": g, "overlap": len(inside) / len(members), "outside": [c for c in members if c not in inside]})
    out.sort(key=lambda m: -m["overlap"])
    return out


LAGGARDS_MAX = 5
LAGGARD_GROUP_MAX = 40  # a known group larger than this (半導體業) says nothing about who follows whom


def laggards(g: Mapping, rows: list[dict], groupings: Mapping[str, Callable[[dict], list[str]]]) -> dict | None:
    """補漲候選: the other stocks of the group's tightest known home — the smallest known group holding at
    least half of its members, LAGGARD_GROUP_MAX stocks at most — that lagged today (up less than half
    the group's average while it rose), the most traded first.
    {label, group, stocks: [{code, name, change_pct}]}; None when the group has no such home or none lag."""
    avg = g.get("avg_pct")
    if avg is None or avg <= 0:
        return None
    homes = []
    for m in g.get("matches", []):
        if m.get("overlap", 0) >= 0.5 and m.get("grouping") in groupings:
            grouping = groupings[m["grouping"]]
            those = [r for r in rows if r.get("kind") == "stock" and m["group"] in grouping(r)]
            if len(those) <= LAGGARD_GROUP_MAX:
                homes.append((len(those), m, those))
    if not homes:
        return None
    _, best, those = min(homes, key=lambda h: h[0])
    inside = {m["code"] for m in g.get("members", [])}
    lag = [r for r in those if r["code"] not in inside and r.get("change_pct") is not None
           and (r.get("volume_shares") or 0) > 0 and r["change_pct"] < avg / 2]
    lag.sort(key=lambda r: -(r.get("turnover_yuan") or 0))
    if not lag:
        return None
    return {"label": best["label"], "group": best["group"],
            "stocks": [{"code": r["code"], "name": r["name"], "change_pct": round(r["change_pct"], 2)} for r in lag[:LAGGARDS_MAX]]}


CHASE_VOL = 1.5  # a laggard trading ≥ this × its 5-day volume …
CHASE_R = 0.6  # … that usually moves with the group (mean daily R over 20 days) and still rose today


def flag_chasers(g: Mapping, vol_ratio: Mapping[str, float], series: Mapping[str, list]) -> dict:
    """The group's laggards with chase = True on the ones rising on volume that usually move with the group
    (research/exp8 C4: they fell back the next day, −1.63% vs the market, 22% up — a 跟風 that fails)."""
    lag = g.get("laggards")
    if not lag:
        return lag
    members = [m["code"] for m in g.get("members", [])]

    def mean_r(c):
        rs = [r for m in members if (r := series_r(series.get(c, []), series.get(m, []), 10)) is not None]
        return sum(rs) / len(rs) if rs else None

    out = []
    for s in lag["stocks"]:
        r = mean_r(s["code"])
        chase = (s.get("change_pct") or 0) > 0 and (vol_ratio.get(s["code"]) or 0) >= CHASE_VOL and r is not None and r >= CHASE_R
        out.append({**s, "chase": chase})
    return {**lag, "stocks": out}


def kind_of(matches: list[dict], size: int) -> str:
    """已知族群: all in one known group; 延伸: at least half and at least 2 of them in one (the rest
    are the stocks that 'should not' move with it); otherwise 跨族群新組合 (a lone member in some
    group, as in most pairs, is no known group at all)."""
    if not matches:
        return "跨族群新組合"
    best = matches[0]
    inside = size - len(best.get("outside", []))
    if best["overlap"] >= 0.999:
        return "已知族群"
    if best["overlap"] >= 0.5 and inside >= 2:
        return "已知族群的延伸"
    return "跨族群新組合"


def co_mentions(names: Mapping[str, str], titles: Iterable[str], limit: int = 3) -> list[str]:
    """Headlines naming at least two of the members (動機的證據)."""
    clean = {c: n.rstrip("*＊ ").replace("-KY", "") for c, n in names.items() if n}
    out = []
    for t in titles:
        hit = [c for c, n in clean.items() if len(n) >= 2 and n in t]
        if len(hit) >= 2 and t not in out:
            out.append(t)
            if len(out) >= limit:
                break
    return out


def money_size(members: list[str], rows_by_code: Mapping[str, dict], market_turnover: float) -> dict:
    """資金規模 of a found group: its turnover (億), that as a share of all common stocks' turnover
    that day, and the members' equal-weight average change."""
    rows = [rows_by_code[c] for c in members if c in rows_by_code]
    total = sum(r.get("turnover_yuan") or 0 for r in rows)
    pcts = [r["change_pct"] for r in rows if r.get("change_pct") is not None]
    return {
        "turnover_100m": round(total / 1e8, 2),
        "turnover_share": round(total / market_turnover * 100, 3) if market_turnover else None,
        "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
    }


def describe(g: dict, rows_by_code: Mapping[str, dict], feats: Mapping[str, Intraday], groupings: Mapping[str, Callable], titles: list[str], inst: Mapping[str, float], market_turnover: float = 0.0) -> dict:
    members = g["members"]
    fmt = lambda m: None if m is None else f"{9 + m // 60:02d}:{m % 60:02d}"  # noqa: E731
    pairs = g.get("pairs", {})
    vals = lambda key: [ch[key] for ch in pairs.values() if ch.get(key) is not None]  # noqa: E731
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    peaks = [feats[c].peak for c in members if c in feats and feats[c].peak is not None]
    starts = [feats[c].start for c in members if c in feats and feats[c].start is not None]
    burst = sorted(set.intersection(*[feats[c].bursts for c in members if c in feats]) if all(c in feats for c in members) else set())
    matches = match_known(members, rows_by_code, groupings)
    names = {c: rows_by_code[c]["name"] for c in members if c in rows_by_code}
    return {
        "members": [
            {
                "code": c,
                "name": names.get(c, c),
                "change_pct": rows_by_code.get(c, {}).get("change_pct"),
                "turnover_rate": turnover_rate(rows_by_code[c]) if c in rows_by_code else None,
                "amplitude_pct": amplitude_pct(rows_by_code[c]) if c in rows_by_code else None,
                "peak": fmt(feats[c].peak) if c in feats else None,
                "start": fmt(feats[c].start) if c in feats else None,
                "locked_at": fmt(feats[c].locked_at) if c in feats else None,
                "inst_net": inst.get(c),
            }
            for c in members
        ],
        "sources": g.get("sources", []),
        "pool": g.get("pool", {}),
        "daily_r": mean(list(g.get("daily", {}).values())),
        "intraday_r": mean(vals("intraday_r")),
        "excess_r": mean(vals("excess_r")),
        "factors": {c: feats[c].factor for c in members if c in feats and feats[c].factor},
        "peak_spread": (max(peaks) - min(peaks)) if len(peaks) >= 2 else None,
        "start_spread": (max(starts) - min(starts)) if len(starts) >= 2 else None,
        "together_bursts": [fmt(b * 5) for b in burst],
        "matches": matches[:3],
        "kind": kind_of(matches, len(members)),
        "news": co_mentions(names, titles),
        "inst_net": sum(v for c in members if (v := inst.get(c)) is not None) or None,
        **money_size(members, rows_by_code, market_turnover),
        "resolution": max((feats[c].resolution for c in members if c in feats), default=1),
    }


def inst_summary(groups: list[dict], inst: Mapping[str, float], rows_by_code: Mapping[str, dict]) -> list[dict]:
    """三大法人 of each group in the units investors read: net 張 (shares / 1000), ≈ 億元 (shares × close)
    and how many members were net bought / sold. Attached when a result is served, so 法人 published
    after the search (TWSE posts it around 15:00–16:30) still shows up; None while the day has none."""
    out = []
    for g in groups:
        nets = {m["code"]: inst[m["code"]] for m in g["members"] if m["code"] in inst}
        if not nets:
            out.append({**g, "inst": None})
            continue
        closes = {c: rows_by_code.get(c, {}).get("close") for c in nets}
        # 億元 only when every member with 法人 has a close: a missing one would understate it silently
        amount = sum(v * closes[c] for c, v in nets.items()) if all(closes.values()) else None
        out.append({
            **g,
            "members": [{**m, "inst_net": inst.get(m["code"])} for m in g["members"]],
            "inst_net": sum(nets.values()),
            "inst": {
                "lots": round(sum(nets.values()) / 1000),
                "amount_100m": None if amount is None else round(amount / 1e8, 2),
                "buyers": sum(1 for v in nets.values() if v > 0),
                "sellers": sum(1 for v in nets.values() if v < 0),
            },
        })
    return out


def margin_summary(groups: list[dict], today: Mapping[str, tuple]) -> list[dict]:
    """融資融券 of each group on the shown day, in 張: 融資／融券 增減 and 餘額, 券資比 (融券 ÷ 融資 餘額 %).
    Members get `margin_chg` (融資增減). `margin` is None while the day has none for any member."""
    out = []
    for g in groups:
        rows = {m["code"]: today[m["code"]] for m in g["members"] if m["code"] in today}
        if not rows:
            out.append({**g, "margin": None})
            continue
        mp, mb, sp, sb = (sum(r[i] for r in rows.values()) for i in range(4))
        out.append({
            **g,
            "members": [{**m, "margin_chg": (rows[m["code"]][1] - rows[m["code"]][0]) if m["code"] in rows else None} for m in g["members"]],
            "margin": {
                "margin_chg": round(mb - mp), "margin_bal": round(mb),
                "short_chg": round(sb - sp), "short_bal": round(sb),
                "short_ratio": round(sb / mb * 100, 2) if mb else None,
            },
        })
    return out


def margin_changes(by_day: Mapping[date, Mapping[str, tuple]]) -> dict[date, dict[str, float]]:
    """{day: {code: 融資增減}} from margin rows, for inst_streak (融資連增／連減)."""
    return {d: {c: r[1] - r[0] for c, r in rows.items()} for d, rows in by_day.items()}


INST_STREAK_DAYS = 20  # 法人連買／連賣: how many 法人 days back a streak is counted at most


def inst_streak(groups: list[dict], by_day: Mapping[date, Mapping[str, float]], days: list[date]) -> list[dict]:
    """法人連買／連賣: on how many consecutive 法人 days (`days` newest first, the shown day first) the
    group's summed 三大法人 net kept the same sign. Adds `inst_streak` {days, side: buy|sell} (None
    under 2 days, or when the shown day has no 法人 yet) to copies of the groups."""
    out = []
    for g in groups:
        codes = [m["code"] for m in g["members"]]
        side, n = None, 0
        for d in days:
            nets = [v for c in codes if (v := by_day.get(d, {}).get(c)) is not None]
            s = (sum(nets) > 0) - (sum(nets) < 0) if nets else 0
            if s == 0 or (side is not None and s != side):
                break
            side, n = s, n + 1
        out.append({**g, "inst_streak": {"days": n, "side": "buy" if side > 0 else "sell"} if n >= 2 else None})
    return out


# ---- persistence across days --------------------------------------------------------------------

SAME_GROUP = 0.5  # Jaccard overlap at which two days' groups count as the same combination


def persistence(groups: list[dict], earlier: Mapping[str, dict]) -> list[dict]:
    """For each found group, the earlier days (stored results by ISO date) whose result holds the same
    combination (member overlap ≥ SAME_GROUP). Adds `seen_days` (oldest first) and `checked_days`
    (how many earlier days had a stored result to compare with) to copies of the groups."""
    out = []
    for g in groups:
        mine = {m["code"] for m in g["members"]}
        seen = []
        for day in sorted(earlier):
            for other in earlier[day].get("groups", []):
                theirs = {m["code"] for m in other["members"]}
                if len(mine & theirs) / len(mine | theirs) >= SAME_GROUP:
                    seen.append(day)
                    break
        out.append({**g, "seen_days": seen, "checked_days": len(earlier)})
    return out


THEME_TERM = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9／/+&.\-（）()]+$")  # CJK, letters, digits, a few separators
THEMES_PER_STOCK = 3
THEMES_PER_GROUP = 4
THEME_TITLES = 8  # headlines kept per group theme (the news day stores at most 5 per keyword)


def news_themes(groups: list[dict], news_days: list[dict], dates: list[str] | None = None) -> list[dict]:
    """「為什麼在動」: the news keywords (of the stored news days, {term: {codes: {code: n}}}) that named
    each member, most mentions first. Adds member `themes` (top THEMES_PER_STOCK) and group `themes`
    [{term, count}] (how many members a keyword named, top THEMES_PER_GROUP) to copies of the groups."""
    by_code = themes_by_code(news_days)
    out = []
    for g in groups:
        members = []
        group_terms: dict[str, int] = {}
        for m in g["members"]:
            terms = sorted(by_code.get(m["code"], {}).items(), key=lambda kv: (-kv[1], kv[0]))[:THEMES_PER_STOCK]
            members.append({**m, "themes": [t for t, _ in terms]})
            for t, _ in terms:
                group_terms[t] = group_terms.get(t, 0) + 1
        top = sorted(group_terms.items(), key=lambda kv: (-kv[1], kv[0]))[:THEMES_PER_GROUP]
        names = [m["name"] for m in g["members"] if m.get("name")]
        themes = [{"term": t, "count": n, "titles": theme_titles(t, news_days, dates, names)} for t, n in top]
        out.append({**g, "members": members, "themes": themes})
    return out


def themes_by_code(news_days: list[dict]) -> dict[str, dict[str, int]]:
    """{code: {term: mentions}} over the stored news days (headline leftovers such as '音量🔊' dropped)."""
    by_code: dict[str, dict[str, int]] = {}
    for day in news_days:
        for term, info in day.items():
            if not THEME_TERM.match(term):
                continue
            for code, n in (info.get("codes") or {}).items():
                by_code.setdefault(code, {})
                by_code[code][term] = by_code[code].get(term, 0) + n
    return by_code


def pair_extras(codes: list[str], rows_by_code: Mapping[str, dict], groupings: Mapping[str, Callable],
                pct_by_day: Mapping[date, Mapping[str, float]], found: list[tuple[str, str, dict]],
                news_days: list[dict]) -> list[dict]:
    """「交集」of every pair of `codes` beyond what K already has: on how many of the days in
    `pct_by_day` both moved the same way (both up or both down), the groups they share, the days N
    put them in one group (`found`: (date, method, stored result)), and the news themes naming both."""
    themes = themes_by_code(news_days)
    groups_of = {c: {(g, name) for g, fn in groupings.items() for name in (fn(rows_by_code[c]) if c in rows_by_code else [])} for c in codes}
    in_n: dict[frozenset, list[dict]] = {}
    for day, method, res in found:
        for grp in res.get("groups", []):
            members = {m["code"] for m in grp.get("members", [])}
            for i, a in enumerate(codes):
                for b in codes[i + 1:]:
                    if a in members and b in members:
                        hits = in_n.setdefault(frozenset((a, b)), [])
                        if not any(h["date"] == day and h["method"] == method for h in hits):
                            hits.append({"date": day, "method": method})
    out = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            same = total = 0
            for pct in pct_by_day.values():
                x, y = pct.get(a), pct.get(b)
                if x is None or y is None:
                    continue
                total += 1
                same += (x > 0 and y > 0) or (x < 0 and y < 0)
            shared = sorted(groups_of[a] & groups_of[b], key=lambda gn: (list(groupings).index(gn[0]), gn[1]))
            common = set(themes.get(a, {})) & set(themes.get(b, {}))
            out.append({
                "a": a, "b": b,
                "same_dir": {"same": same, "total": total},
                "groups": [{"label": GROUPING_LABELS.get(g, g), "group": name} for g, name in shared],
                "finder": sorted(in_n.get(frozenset((a, b)), []), key=lambda h: h["date"], reverse=True),
                "themes": sorted(common, key=lambda t: -(themes[a][t] + themes[b][t]))[:5],
            })
    return out


def theme_titles(term: str, news_days: list[dict], dates: list[str] | None, names: list[str]) -> list[dict]:
    """The stored headlines of `term` (newest day first), those naming a member first; each
    {title, source, url, date, names: [the members it names]}."""
    hits, rest = [], []
    for i, day in enumerate(news_days):
        for h in (day.get(term) or {}).get("titles") or []:
            named = [n for n in names if n in h.get("title", "")]
            item = {**h, "date": dates[i] if dates and i < len(dates) else None, "names": named}
            (hits if named else rest).append(item)
    seen, out = set(), []
    for h in hits + rest:
        if h.get("title") in seen:
            continue
        seen.add(h.get("title"))
        out.append(h)
    return out[:THEME_TITLES]


# ---- 高點錯開但走勢一樣 (shown only: N's groups stay as they are) -----------------------------------

# 同爆量: at least BURST_CO same-minute bursts and BURST_X × chance; 3 and 3 since 2026-10-25 (methods/m6:
# random pairs 2.7%, against 41.8% at the former 2 and 2)
BURST_CO = 3
BURST_X = 3.0
SHAPE_DTW_MAX = 2.5  # the median DTW distance of N's own pairs (research/m2, 2026-10-25)
SHAPE_PAIRS_MAX = 10


def staggered_pairs(cands: list[dict], feats: Mapping[str, Intraday], p: FinderParams, by_code: Mapping[str, dict]) -> list[dict]:
    """可能分批推進: candidate pairs N drops only for 高點時間差太大 whose session shapes are as close as
    N's own pairs (DTW ≤ SHAPE_DTW_MAX) — the money may be one, pushed in steps. The closest first."""
    from .evidence import dtw, shape_path

    seen, out = set(), []
    paths: dict[str, list[float] | None] = {}
    for c in cands:
        for a, b in combinations(c["members"], 2):
            key = (a, b) if a < b else (b, a)
            if key in seen or a not in feats or b not in feats:
                continue
            seen.add(key)
            chk = pair_check(feats[a], feats[b], p)
            if chk["reason"] != "高點時間差太大":
                continue
            for x in key:
                if x not in paths:
                    paths[x] = shape_path(feats[x].returns[-(BUCKETS - 1):])
            pa, pb = paths[key[0]], paths[key[1]]
            if pa is None or pb is None:
                continue
            d = dtw(pa, pb)
            if d <= SHAPE_DTW_MAX:
                out.append({"members": [{"code": x, "name": by_code.get(x, {}).get("name", x), "change_pct": by_code.get(x, {}).get("change_pct")} for x in key],
                            "dtw": round(d, 2), "peak_gap": chk["peak_gap"], "excess_r": chk["excess_r"], "intraday_r": chk["intraday_r"]})
    out.sort(key=lambda x: x["dtw"])
    return out[:SHAPE_PAIRS_MAX]


# ---- 扣大盤／類股: each stock's returns once its index is taken out ------------------------------

def add_excess(feats: dict[str, Intraday], by_code: Mapping[str, dict], rows: list[dict], sessions: list[date],
               sectors: Mapping[date, Mapping[str, list]], basis: str) -> None:
    """Set .excess / .factor on the stocks' features: the 類股指數 (TWSE, stored per session) where
    factors.factor_of picks it, else 大盤／櫃買 (the index rows fetched with the candidates)."""
    from .evidence import residuals
    from .factors import MARKET_FACTORS, factor_of, industry_sizes, sector_returns

    sizes = industry_sizes(rows)
    names = set.intersection(*(set(sectors.get(s, {})) for s in sessions)) if sessions and all(s in sectors for s in sessions) else set()
    market_keys = {key for key, _ in MARKET_FACTORS.values()}
    cache: dict[str, list | None] = {}
    for code, f in feats.items():
        row = by_code.get(code)
        if row is None:
            continue  # the index rows themselves
        fac = factor_of(row, sizes, names, basis)
        if fac is None:
            continue
        key, label = fac
        if key not in cache:
            cache[key] = (feats[key].returns if key in feats else None) if key in market_keys else sector_returns(sectors, sessions, key)
        m = cache[key]
        if m is None or len(m) != len(f.returns):
            continue
        # all None when the stock had too few moves (locked limit-up for days): then the funnel says 分時資料不足,
        # as with the raw R — 沒有指數分時 is kept for a missing index
        f.excess, f.factor = residuals(f.returns, m), label


PCA1_LABEL = "第1主成分"
PCA1_ITER = 40


def first_component(series: list[list[float | None]]) -> list[float] | None:
    """The day's first principal component over time: each stock's returns standardised (a missing point is 0),
    the top right singular vector of that matrix by power iteration. None with fewer than 10 stocks."""
    rows = []
    for s in series:
        xs = [x for x in s if x is not None]
        if len(xs) < 20:
            continue
        m = sum(xs) / len(xs)
        sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
        if sd > 0:
            rows.append([(x - m) / sd if x is not None else 0.0 for x in s])
    if len(rows) < 10:
        return None
    t = len(rows[0])
    v = [1.0] * t
    for _ in range(PCA1_ITER):
        u = [sum(a * b for a, b in zip(r, v)) for r in rows]
        v = [sum(u[i] * rows[i][k] for i in range(len(rows))) for k in range(t)]
        norm = sum(x * x for x in v) ** 0.5
        if not norm:
            return None
        v = [x / norm for x in v]
    return v


def add_pca1(feats: dict[str, "Intraday"], by_code: Mapping[str, dict]) -> None:
    """扣第 1 主成分 (research/m1: 4–6 factors a day; taking out more than the first takes the groups' own
    signal too): .excess / .factor = each stock's residual on the day's first component of the fetched stocks."""
    from .evidence import residuals

    codes = [c for c in feats if c in by_code]
    n = {len(feats[c].returns) for c in codes}
    if len(n) != 1:
        return
    pc = first_component([feats[c].returns for c in codes])
    if pc is None:
        return
    for c in codes:
        feats[c].excess, feats[c].factor = residuals(feats[c].returns, pc), PCA1_LABEL


# ---- 資金一致性證據 of a found group (the same checks as K's 🔗 交集, pair by pair) ----------------

INDEX_ROWS = (("^TWII", "上市"), ("IX0043.TWO", "上櫃"))  # fetched with the candidates for the excess R


def majority(oks: list[bool | None]) -> bool | None:
    """A group passes a check when more than half of its pairs with data do; None without data."""
    known = [x for x in oks if x is not None]
    return None if not known else sum(known) * 2 > len(known)


def group_evidence(members: list[str], feats: Mapping[str, Intraday], minute_bars: Mapping[str, list], index_returns: Mapping[str, list],
                   markets: Mapping[str, str], pool: Mapping[str, list], series: Mapping[str, list], seed: str) -> dict:
    """{excess, baseline, burst, sync, samedir, shape}: each {ok, value…}, decided pair by pair (majority).
    shape (DTW of the session shapes ≤ SHAPE_DTW_MAX) is shown only: linkage.EVIDENCE_CHECKS leaves it out."""
    from .evidence import dtw, minute_evidence, percentile_of, residuals, shape_path

    def r_of(x: list, y: list, k: int = 20) -> float | None:
        pts = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
        return pearson([a for a, _ in pts], [b for _, b in pts]) if len(pts) >= k else None

    res = {c: feats[c].excess if c in feats and feats[c].excess is not None
           else residuals(feats[c].returns, index_returns[markets[c]]) if c in feats and markets.get(c) in index_returns else None
           for c in members}
    ex, base, burst, sync, same, shape = [], [], [], [], [], []
    v_ex, v_pct, v_co, v_exp, v_r0, v_off, v_same, v_dtw = [], [], [], [], [], [], [], []
    paths = {c: shape_path(feats[c].returns[-(BUCKETS - 1):]) if c in feats else None for c in members}
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            if res.get(a) and res.get(b):
                r = r_of(res[a], res[b])
                if r is not None:
                    v_ex.append(r)
                ex.append(None if r is None else r >= EXCESS_R)
            raw = r_of(feats[a].returns, feats[b].returns) if a in feats and b in feats else None
            pct = percentile_of(raw, pool, set(members), f"{seed}{a}{b}", min_points=20) if raw is not None else None
            if pct:
                v_pct.append(pct["pct"])
            base.append(None if not pct else pct["pct"] >= 90)
            mn = minute_evidence(minute_bars.get(a), minute_bars.get(b))
            if mn and mn["expected"] is not None:
                v_co.append(mn["co"])
                v_exp.append(mn["expected"])
                burst.append(mn["co"] >= BURST_CO and mn["co"] >= BURST_X * max(mn["expected"], 0.5))
            else:
                burst.append(None)
            if mn and mn["r0"] is not None and mn["r_off"] is not None:
                v_r0.append(mn["r0"])
                v_off.append(mn["r_off"])
                sync.append(mn["r0"] >= 0.3 and mn["r0"] - mn["r_off"] >= 0.1)
            else:
                sync.append(None)
            sa, sb = series.get(a), series.get(b)
            days = [(x, y) for x, y in zip(sa or [], sb or []) if x is not None and y is not None]
            if days:
                share = sum((x > 0 and y > 0) or (x < 0 and y < 0) for x, y in days) / len(days)
                v_same.append(share)
                same.append(share >= 0.75)
            else:
                same.append(None)
            if paths.get(a) is not None and paths.get(b) is not None:
                d = dtw(paths[a], paths[b])
                v_dtw.append(d)
                shape.append(d <= SHAPE_DTW_MAX)
            else:
                shape.append(None)
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    return {
        "excess": {"ok": majority(ex), "r": mean(v_ex)},
        "baseline": {"ok": majority(base), "pct": mean(v_pct)},
        "burst": {"ok": majority(burst), "co": sum(v_co) if v_co else None, "expected": round(sum(v_exp), 2) if v_exp else None},
        "sync": {"ok": majority(sync), "r0": mean(v_r0), "r_off": mean(v_off)},
        "samedir": {"ok": majority(same), "share": mean(v_same)},
        "shape": {"ok": majority(shape), "dtw": mean(v_dtw)},
    }


# ---- one full search ----------------------------------------------------------------------------

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="finder")

FetchBars = Callable[[list[dict], list[date], Callable[[], None]], Awaitable[dict[str, tuple[dict[date, list], int]]]]
Progress = Callable[[str, int, int], None]  # (stage, done, total)


async def run_search(
    store: HistoryStore,
    day: date,
    method: str,
    p: FinderParams,
    groupings: Mapping[str, Callable[[dict], list[str]]],
    fetch: FetchBars,
    titles: list[str] | None = None,
    progress: Progress | None = None,
) -> dict:
    """找法1 ('known') or 找法2 ('new') for `day`. `fetch(rows, sessions)` returns
    {code: ({session: bars}, resolution minutes)} — the only network step, run for stage-A survivors only."""
    import asyncio
    import time

    loop = asyncio.get_running_loop()

    async def off(fn, *args):  # a pool of N's own: the shared one is busy with start-up backfills
        return await loop.run_in_executor(_POOL, fn, *args)

    report = progress or (lambda stage, done, total: None)
    timing: dict[str, float] = {}
    report("daily", 0, 1)
    t = time.perf_counter()
    rows = [r for r in store.day_rows(day) if r.get("kind") == "stock"]
    by_code = {r["code"]: r for r in rows}
    if method == "known":
        series = await off(daily_series, store, day, p.daily_window)
        cands = await off(known_candidates, rows, groupings, series, p)
        pool_size = None
    else:
        z = await off(share_z, store, day, p.share_window)
        vr = await off(volume_ratio, store, day, p.vol_ratio_window) if "vol_ratio" in p.pool_sources else {}
        pool = candidate_pool(rows, z, p, vr)
        series = await off(daily_series, store, day, p.new_daily_window)
        edges = await off(daily_edges, list(pool), series, p.new_daily_r)
        cands = [
            {"members": core, "sources": [], "pool": {c: pool[c] for c in core}, "daily": {e: r for e, r in edges.items() if e <= set(core)}}
            for core in dense_cores(edges, p.dense, p.min_size)
        ]
        pool_size = len(pool)
    timing["daily"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    need: list[str] = []
    for c in sorted(cands, key=lambda c: -len(c["members"])):  # the largest candidate sets first
        for code in c["members"]:
            if code not in need:
                need.append(code)
    need = need[: p.max_fetch]
    sessions = sorted(store.trading_dates(up_to=day, limit=p.intraday_sessions))
    todo = [by_code[c] for c in need if c in by_code]
    n_stocks = len(todo)  # the progress counts stocks; the two indexes ride along
    if todo:  # 大盤／櫃買 too: their returns are taken out for the excess R (資金一致性證據)
        todo += [{"code": sym, "market": mk, "kind": "index"} for sym, mk in INDEX_ROWS]
    fetched = [0]

    def one_done() -> None:  # fetch calls this after each stock
        fetched[0] += 1
        report("fetch", min(fetched[0], n_stocks), n_stocks)

    report("fetch", 0, n_stocks)
    got = await fetch(todo, sessions, one_done) if todo else {}
    report("intraday", 0, 1)
    timing["fetch"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    session_rows = {s: {r["code"]: r for r in rs} for s, rs in store.rows(sessions).items()}
    feats = {}
    for code, (by_session, resolution) in got.items():
        aligned = {s: align_bars(b, session_rows.get(s, {}).get(code, {})) for s, b in by_session.items()}
        feats[code] = intraday_features(aligned, {s: session_rows.get(s, {}).get(code, {}) for s in sessions}, day, p, resolution)
    if p.intraday_basis == "pca1":
        add_pca1(feats, by_code)
    else:
        add_excess(feats, by_code, rows, sessions, store.sector_intraday(sessions), "market" if p.intraday_basis == "market" else "sector")
    adaptive = None
    if p.excess_r_mode == "adaptive" and p.intraday_basis != "raw":
        adaptive = adaptive_excess_r(feats, by_code, p, f"{day}{method}")
        if adaptive is not None:
            p = replace(p, excess_r=adaptive)  # this search only; the stored params say which threshold was used
    funnel: dict = {}
    found = confirm(cands, feats, p, funnel)
    shape_pairs = staggered_pairs(cands, feats, p, by_code)
    inst = store.inst_net([day]).get(day, {})
    market_turnover = sum(r.get("turnover_yuan") or 0 for r in rows)  # common stocks only (rows above)
    groups = [describe(g, by_code, feats, groupings, titles or [], inst, market_turnover) for g in found]
    # 資金一致性證據 per group: index returns, the day's 1-minute bars, the fetched stocks as the random baseline
    index_returns = {mk: feats[sym].returns for sym, mk in INDEX_ROWS if sym in feats}
    minute_bars = {code: by_session.get(day) for code, (by_session, resolution) in got.items() if resolution == 1 and by_session.get(day)}
    stock_feats = {c: f for c, f in feats.items() if c in by_code}
    pool_returns = {c: f.returns for c, f in stock_feats.items()}
    markets = {c: by_code[c].get("market") for c in stock_feats}
    for g, raw in zip(groups, found):
        codes = [m["code"] for m in g["members"]]
        g["evidence"] = group_evidence(codes, stock_feats, minute_bars, index_returns, markets, pool_returns, series, f"{day}{method}")
    groups.sort(key=lambda g: (-len(g["members"]), -(g["intraday_r"] or 0)))
    timing["intraday"] = round(time.perf_counter() - t, 2)
    return {
        "date": day.isoformat(),
        "method": method,
        "params": asdict(p),
        "groupings": [GROUPING_LABELS.get(g, g) for g in groupings],
        "shape_pairs": shape_pairs,
        "excess_r_used": p.excess_r if p.intraday_basis != "raw" else None, "excess_r_adaptive": adaptive,
        "stats": {"candidates": len(cands), "pool": pool_size, "fetched": sum(1 for c in got if c in by_code), "groups": len(groups), "seconds": timing, "funnel": funnel},
        "groups": groups,
    }
