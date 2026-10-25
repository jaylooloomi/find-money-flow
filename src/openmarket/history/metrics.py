"""Group strength metrics over stored history. Groupings map a row to one or more group names."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, timedelta

from .store import HistoryStore

LIMIT_PCT = 9.5  # 台股漲跌幅限制 10%，≥9.5% 視為漲停（價格跳動造成的誤差）
WEEK_DAYS = 5
PERIOD_20 = 20  # bonus-13: 20-trading-day colour option for the history heatmap

Grouping = Callable[[dict], Iterable[str]]
METRICS = ("avg_pct", "cap_pct", "strong_ratio", "breadth", "limit_up", "share_z")  # share_z: money-flow spec


def industry_grouping(row: dict) -> list[str]:
    """Official industry; only common stocks (ETF/preferred/TDR are not an industry's members)."""
    return [row["industry"]] if row.get("industry") and row.get("kind") == "stock" else []


industry_grouping.cache_key = "industry"  # groupings with a cache_key may reuse per-day results

# C's benchmark rows: the whole listed (TWSE) / OTC (TPEx) market as one "group" each, then the
# TWSE 電子／金融／傳產 sectors (上市 only, like the official 類股指數 their change rows show)
BENCHMARKS = {"上市": "大盤（加權）", "上櫃": "櫃買（OTC）"}
SECTOR_BENCHMARKS = {"電子": "電子（上市）", "金融": "金融（上市）", "傳產": "傳產（上市）"}
ELECTRONICS = {"半導體業", "電腦及週邊設備業", "光電業", "通信網路業", "電子零組件業", "電子通路業", "資訊服務業", "其他電子業"}  # 電子工業類指數
BENCHMARK_HINTS = {
    "電子（上市）": "證交所 8 個電子產業（半導體、電腦週邊、光電、通信網路、電子零組件、電子通路、資訊服務、其他電子）",
    "金融（上市）": "證交所金融保險業",
    "傳產（上市）": "上市普通股扣掉電子與金融（＝未含金融電子指數的範圍）",
}


def market_grouping(row: dict) -> list[str]:
    """Common stocks by market, named as C's benchmark rows, plus the 上市 ones by sector."""
    if row.get("kind") != "stock":
        return []
    name = BENCHMARKS.get(row.get("market") or "")
    out = [name] if name else []
    if row.get("market") == "上市":
        industry = row.get("industry") or ""
        sector = "電子" if industry in ELECTRONICS else "金融" if industry == "金融保險業" else "傳產"
        out.append(SECTOR_BENCHMARKS[sector])
    return out


market_grouping.cache_key = "market+sector"


def daily_group_avgs(store: HistoryStore, dates: list[date], grouping: Grouping) -> list[tuple[dict, float | None]]:
    """Per day: ({group: equal-weight avg change_pct}, market avg over common stocks).

    Past days never change, so results are cached on the store per (grouping.cache_key, day);
    the cache is cleared whenever a day is (re)written. Groupings without cache_key are not cached."""
    key = getattr(grouping, "cache_key", None)
    cache = store.derived_cache
    # take the cached values once: save_day may clear the shared dict from another thread
    hits = {d: cache.get((key, d)) for d in dates} if key is not None else {}
    missing = [d for d in dates if hits.get(d) is None]
    computed: dict[date, tuple[dict, float | None]] = {}
    if missing:
        rows = store.change_rows(missing)
        for d in missing:
            sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
            msum, mcount = 0.0, 0
            for r in rows[d]:
                pct = r["change_pct"]
                if pct is None:
                    continue
                if r.get("kind") == "stock":
                    msum += pct
                    mcount += 1
                for g in grouping(r):
                    acc = sums[g]
                    acc[0] += pct
                    acc[1] += 1
            value = ({g: t / n for g, (t, n) in sums.items()}, msum / mcount if mcount else None)
            computed[d] = value
            if key is not None:
                cache[(key, d)] = value
    return [computed[d] if d in computed else hits[d] for d in dates]


def mapping_grouping(code_to_groups: dict[str, list[str]]) -> Grouping:
    """Grouping from a code → [group names] map (custom / concept groups; any security kind)."""
    return lambda row: code_to_groups.get(row["code"], [])


def _cap_weight(r: dict) -> float:
    cap, close, prev = r.get("market_cap"), r.get("close"), r.get("prev_close")
    if not cap or not close or not prev:
        return 0.0
    return cap / close * prev


def _leader(members: list[dict]) -> dict | None:
    """bonus-09: the member with the biggest gain that day (the stock leading the group)."""
    best = max((r for r in members if r.get("change_pct") is not None), key=lambda r: r["change_pct"], default=None)
    if best is None:
        return None
    return {"code": best["code"], "name": best["name"], "change_pct": best["change_pct"]}


def group_stats(rows: list[dict], threshold: float, grouping: Grouping = industry_grouping) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for g in grouping(r):
            groups[g].append(r)
    out = []
    for name, members in groups.items():
        pcts = [r["change_pct"] for r in members if r.get("change_pct") is not None]
        weighted = [(r["change_pct"], _cap_weight(r)) for r in members if r.get("change_pct") is not None]
        weighted = [(v, w) for v, w in weighted if w > 0]
        total_w = sum(w for _, w in weighted)
        up = sum(1 for r in members if (r.get("change") or 0) > 0)
        down = sum(1 for r in members if (r.get("change") or 0) < 0)
        amps = [(r["high"] - r["low"]) / r["prev_close"] * 100 for r in members if r.get("high") and r.get("low") and r.get("prev_close")]
        out.append({
            "name": name,
            "count": len(members),
            "avg_pct": sum(pcts) / len(pcts) if pcts else None,
            "cap_pct": sum(v * w for v, w in weighted) / total_w if total_w else None,
            "avg_amp": sum(amps) / len(amps) if amps else None,  # (high − low) / prev close, equal weight
            "strong_count": sum(1 for p in pcts if p >= threshold),
            "strong_ratio": sum(1 for p in pcts if p >= threshold) / len(pcts) * 100 if pcts else None,
            "limit_up": sum(1 for p in pcts if p >= LIMIT_PCT),
            "limit_ratio": sum(1 for p in pcts if p >= LIMIT_PCT) / len(pcts) * 100 if pcts else None,
            "limit_down": sum(1 for p in pcts if p <= -LIMIT_PCT),
            "up": up,
            "down": down,
            "breadth": up / (up + down) * 100 if up + down else None,
            "leader": _leader(members),  # bonus-09
            "turnover": sum(r.get("turnover_yuan") or 0 for r in members),
        })
    out.sort(key=lambda g: (g["strong_ratio"] is None, -(g["strong_ratio"] or 0), -(g["avg_pct"] or 0)))
    return out


def daily_group_stats(store: HistoryStore, dates: list[date], threshold: float, grouping: Grouping) -> list[list[dict]]:
    """group_stats for each day. Days are immutable once stored, so with a cache_key the results are
    kept in store.derived_cache (cleared by save_day): switching C between 10/20/60 days or between
    metrics then reuses the days already computed instead of recomputing every day."""
    key = getattr(grouping, "cache_key", None)
    cache = store.derived_cache
    # read the cached values once: save_day may clear the shared dict from another thread
    hits = {d: cache.get(("group_stats", key, d, threshold)) for d in dates} if key is not None else {}
    missing = [d for d in dates if hits.get(d) is None]
    by_day = store.rows(missing) if missing else {}
    out = []
    for d in dates:
        stats = hits.get(d)
        if stats is None:
            stats = group_stats(by_day[d], threshold, grouping)
            if key is not None:
                cache[("group_stats", key, d, threshold)] = stats
        out.append(stats)
    return out


def rotation(
    store: HistoryStore, days: int, metric: str, threshold: float, up_to: date | None = None,
    grouping: Grouping = industry_grouping,
) -> dict:
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {METRICS}")
    if metric == "share_z":
        out = share_z_rotation(store, days, up_to, grouping)
        bench = share_z_rotation(store, days, up_to, market_grouping)["groups"]
        out["benchmarks"] = _benchmark_rows(bench, out["dates"])
        return out
    dates = sorted(store.trading_dates(up_to=up_to, limit=days))
    groups = _rotation_rows(daily_group_stats(store, dates, threshold, grouping), dates, metric)
    # strongest recent groups first: mean of the last 3 values
    def recent(g):
        tail = [x for x in g["values"][-3:] if x is not None]
        return sum(tail) / len(tail) if tail else float("-inf")
    groups.sort(key=recent, reverse=True)
    bench = _rotation_rows(daily_group_stats(store, dates, threshold, market_grouping), dates, metric)
    if metric in ("avg_pct", "cap_pct"):
        _use_index_change(store, bench, dates)
    return {"dates": [d.isoformat() for d in dates], "metric": metric, "groups": groups, "benchmarks": _benchmark_rows(bench, [d.isoformat() for d in dates])}


def _rotation_rows(stats_by_day: list[list[dict]], dates: list[date], metric: str) -> list[dict]:
    table: dict[str, list] = defaultdict(lambda: [None] * len(dates))
    for i, stats in enumerate(stats_by_day):
        for g in stats:
            table[g["name"]][i] = g[metric]
    return [{"name": n, "values": v} for n, v in table.items()]


def _use_index_change(store: HistoryStore, bench: list[dict], dates: list[date]) -> None:
    """The change rows show the official index move (加權／櫃買指數, 電子／金融／未含金融電子類指數)
    where the index is stored; a sector day without its index stays empty, never the stocks' average."""
    index = store.index_rows()
    by_name = {g["name"]: g for g in bench}
    for name in SECTOR_BENCHMARKS.values():  # sectors: index only
        if name in by_name:
            by_name[name]["values"] = [None] * len(dates)
    for market, name in {**BENCHMARKS, **SECTOR_BENCHMARKS}.items():
        row = by_name.setdefault(name, {"name": name, "values": [None] * len(dates)})
        if row not in bench:
            bench.append(row)
        for i, d in enumerate(dates):
            ix = index.get(d.isoformat(), {}).get(market)
            if ix and ix.get("close") is not None and ix.get("change") is not None and ix["close"] != ix["change"]:
                row["values"][i] = ix["change"] / (ix["close"] - ix["change"]) * 100


def _benchmark_rows(bench: list[dict], dates: list[str]) -> list[dict]:
    """The benchmark rows in a fixed order (大盤, 櫃買, 電子, 金融, 傳產), all-null when one has no data;
    the sector rows carry a `hint` saying what they cover."""
    by_name = {g["name"]: g for g in bench}
    names = [*BENCHMARKS.values(), *SECTOR_BENCHMARKS.values()]
    rows = [by_name.get(n, {"name": n, "values": [None] * len(dates)}) for n in names]
    return [{**r, "hint": BENCHMARK_HINTS[r["name"]]} if r["name"] in BENCHMARK_HINTS else r for r in rows]


def day_stocks(store: HistoryStore, day: date, grouping: Grouping | None = None) -> list[dict]:
    """One day's rows in the live-snapshot Stock shape (so the treemap code is reused).

    With a grouping, `industry` is replaced by the group name and a stock that belongs to
    several groups appears once per group; stocks in no group are left out."""
    dates = store.trading_dates(up_to=day, limit=PERIOD_20 + 1)
    if not dates or dates[0] != day:
        return []
    week_day = dates[WEEK_DAYS] if len(dates) > WEEK_DAYS else None
    p20_day = dates[PERIOD_20] if len(dates) > PERIOD_20 else None  # bonus-13
    rows = store.rows([d for d in (day, week_day, p20_day) if d is not None])
    week_ago = {r["code"]: r["close"] for r in rows.get(week_day, [])} if week_day else {}
    p20_ago = {r["code"]: r["close"] for r in rows.get(p20_day, [])} if p20_day else {}
    out = []
    for r in rows[day]:
        names = grouping(r) if grouping else [r["industry"]]
        if not names:
            continue
        prev, close = r["prev_close"], r["close"]
        base = week_ago.get(r["code"])
        out.append({
            "code": r["code"], "name": r["name"], "market": r["market"], "industry": r["industry"],
            "kind": r["kind"], "price": close, "change": r["change"], "change_pct": r["change_pct"],
            "week_pct": (close / base - 1) * 100 if close and base else None,
            "period_20": (close / p20_ago[r["code"]] - 1) * 100 if close and p20_ago.get(r["code"]) else None,
            "amplitude_pct": (r["high"] - r["low"]) / prev * 100 if r["high"] and r["low"] and prev else None,
            "open": r["open"], "high": r["high"], "low": r["low"], "prev_close": prev,
            "volume_lots": r["volume_shares"] / 1000 if r["volume_shares"] is not None else None,
            "turnover_100m": r["turnover_yuan"] / 1e8 if r["turnover_yuan"] is not None else None,
            "market_cap": r["market_cap"],
        })
        if grouping:
            base_row = out.pop()
            out.extend({**base_row, "industry": n, "kind": "stock"} for n in names)
    return out


def movers(
    store: HistoryStore, to: date, mode: str, value: float, start: date | None = None,
    grouping: Grouping = industry_grouping,
) -> dict:
    """Stocks that rose together, grouped.

    mode=day:    change_pct on `to` >= value
    mode=range:  cumulative return from the close before `start` to `to` >= value
    mode=streak: consecutive up days ending at `to` >= value
    """
    if mode == "day":
        rows = store.rows([to]).get(to, [])
        picked = [(r, r["change_pct"]) for r in rows if grouping(r) and r.get("change_pct") is not None and r["change_pct"] >= value]
        label = "漲幅"
    elif mode == "range":
        if start is None or start > to:
            raise ValueError("range mode needs start <= to")
        dates = [d for d in store.trading_dates(up_to=to) if d >= start]
        before = store.trading_dates(up_to=start, limit=2)
        base_day = next((d for d in before if d < start), None)
        if not dates or base_day is None:
            return {"mode": mode, "groups": []}
        data = store.rows([base_day, to])
        base = {r["code"]: r["close"] for r in data[base_day]}
        picked = []
        for r in (r for r in data[to] if grouping(r)):
            b = base.get(r["code"])
            if r["close"] and b:
                ret = (r["close"] / b - 1) * 100
                if ret >= value:
                    picked.append((r, ret))
        label = "區間漲幅"
    elif mode == "streak":
        dates = store.trading_dates(up_to=to, limit=60)
        data = store.rows(dates)
        by_code: dict[str, list] = defaultdict(list)
        for d in dates:  # newest first
            for r in data[d]:
                by_code[r["code"]].append(r)
        latest = {r["code"]: r for r in data.get(to, [])}
        picked = []
        for code, r in latest.items():
            if not grouping(r):
                continue
            streak = 0
            for row in by_code[code]:
                if (row.get("change") or 0) > 0:
                    streak += 1
                else:
                    break
            if streak >= value:
                picked.append((r, float(streak)))
        label = "連漲天數"
    else:
        raise ValueError("mode must be day, range or streak")

    groups: dict[str, list] = defaultdict(list)
    for r, metric in picked:
        for g in grouping(r):
            groups[g].append({"code": r["code"], "name": r["name"], "value": metric, "change_pct": r.get("change_pct"), "close": r.get("close")})
    out = [{"name": n, "stocks": sorted(s, key=lambda x: -x["value"])} for n, s in groups.items()]
    out.sort(key=lambda g: (-len(g["stocks"]), -max(x["value"] for x in g["stocks"])))
    return {"mode": mode, "label": label, "groups": out}


# ---- bonus-02: single-stock history for the history-page info card ----

def stock_history(store: HistoryStore, code: str, to: date, days: int = 20) -> dict | None:
    """The stock's row on `to` plus its closes over the last `days` trading days (oldest first)."""
    rows = store.stock_rows(code, to, days)
    if not rows or rows[-1]["date"] != to.isoformat():
        return None
    last = rows[-1]
    first_close = next((r["close"] for r in rows if r["close"]), None)
    return {
        "code": code,
        "name": last["name"],
        "industry": last["industry"],
        "market": last["market"],
        "date": last["date"],
        "close": last["close"],
        "change": last["change"],
        "change_pct": last["change_pct"],
        "high": last["high"],
        "low": last["low"],
        "volume_lots": last["volume_shares"] / 1000 if last["volume_shares"] is not None else None,
        "turnover_100m": last["turnover_yuan"] / 1e8 if last["turnover_yuan"] is not None else None,
        "market_cap": last["market_cap"],
        "period_pct": (last["close"] / first_close - 1) * 100 if last["close"] and first_close else None,
        "series": [{"date": r["date"], "close": r["close"], "change_pct": r["change_pct"], "open": r.get("open"), "high": r.get("high"),
                    "low": r.get("low"), "volume_lots": r["volume_shares"] / 1000 if r.get("volume_shares") is not None else None} for r in rows],
    }


# ---- bonus-04: cumulative group index for the comparison chart ----

MARKET = "全市場"


def group_index(
    store: HistoryStore, days: int, up_to: date | None = None, names: list[str] | None = None,
    top: int = 5, grouping: Grouping = industry_grouping,
) -> dict:
    """Cumulative return (%) of each group's equal-weight daily average, compounded over the last
    `days` trading days, plus the whole market (common stocks) as a benchmark.

    `names` picks the groups; otherwise the `top` groups by period return are returned."""
    dates = sorted(store.trading_dates(up_to=up_to, limit=days))
    per_day = daily_group_avgs(store, dates, grouping)
    daily: dict[str, list] = defaultdict(lambda: [None] * len(dates))
    market_daily: list = [None] * len(dates)
    for i, (avgs, market) in enumerate(per_day):
        for g, v in avgs.items():
            daily[g][i] = v
        market_daily[i] = market

    def compound(values: list) -> list:
        level, out = 1.0, []
        for v in values:
            if v is not None:
                level *= 1 + v / 100
            out.append(round((level - 1) * 100, 4))
        return out

    series = [{"name": n, "values": compound(v)} for n, v in daily.items()]
    if names:
        wanted = set(names)
        series = [x for x in series if x["name"] in wanted]
    else:
        series = sorted(series, key=lambda x: x["values"][-1] if x["values"] else 0, reverse=True)[:top]
    return {"dates": [d.isoformat() for d in dates], "series": series, "market": compound(market_daily)}


# ---- bonus-10: multi-period group returns for the ranking table ----

PERIODS = (5, 20, 60)


def group_periods(store: HistoryStore, up_to: date, grouping: Grouping = industry_grouping,
                  periods: tuple[int, ...] = PERIODS) -> dict:
    """{group: {"perf_5": %, "perf_20": %, "perf_60": %}} — equal-weight daily returns compounded
    over the last N trading days up to `up_to` (same basis as the E chart). None if fewer days exist."""
    dates = sorted(store.trading_dates(up_to=up_to, limit=max(periods)))
    daily: dict[str, list] = defaultdict(lambda: [None] * len(dates))
    for i, (avgs, _market) in enumerate(daily_group_avgs(store, dates, grouping)):
        for g, v in avgs.items():
            daily[g][i] = v
    out: dict[str, dict] = {}
    for g, values in daily.items():
        perf = {}
        for p in periods:
            if len(values) < p:
                perf[f"perf_{p}"] = None
                continue
            level = 1.0
            for v in values[-p:]:
                if v is not None:
                    level *= 1 + v / 100
            perf[f"perf_{p}"] = round((level - 1) * 100, 4)
        out[g] = perf
    return out


# ---- bonus-18: rank change vs the previous trading day -----------------------------------

def group_rank_change(store: HistoryStore, day: date, grouping: Grouping = industry_grouping) -> dict[str, dict]:
    """{group: {"rank": n, "rank_change": +up / -down / None}} ranking groups by equal-weight average
    change; rank 1 = strongest. rank_change is None for the first stored day or a new group."""
    dates = sorted(store.trading_dates(up_to=day, limit=2))
    if not dates or dates[-1] != day:
        return {}
    per_day = daily_group_avgs(store, dates, grouping)

    def ranks(avgs: dict) -> dict[str, int]:
        ordered = sorted(avgs.items(), key=lambda kv: kv[1], reverse=True)
        return {name: i + 1 for i, (name, _) in enumerate(ordered)}

    today = ranks(per_day[-1][0])
    prev = ranks(per_day[0][0]) if len(dates) == 2 else {}
    return {
        g: {"rank": r, "rank_change": prev[g] - r if g in prev else None}
        for g, r in today.items()
    }



# ---- money flow: turnover share (+ its z-score), CLV money flow, turnover rate, 三大法人 ---------

FLOW_WINDOW = 20
STD_FLOOR = 0.05  # percentage points: thin groups would otherwise divide by ~0


def clv(r: dict) -> float:
    """Close location value in [-1, 1]: +1 closes at the high, -1 at the low, 0 flat or no range."""
    h, lo, c = r.get("high"), r.get("low"), r.get("close")
    if h is None or lo is None or c is None or h <= lo:
        return 0.0
    return ((c - lo) - (h - c)) / (h - lo)


def flow_day(rows: list[dict], grouping: Grouping, inst: dict[str, float]) -> dict:
    """One day: market turnover and per-group sums (common stocks, each code once per group)."""
    market = 0.0
    groups: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        if r.get("kind") != "stock":
            continue
        t = r.get("turnover_yuan") or 0.0
        market += t
        close = r.get("close")
        cap = r.get("market_cap") or 0.0
        for g in grouping(r):
            if (g, r["code"]) in seen:
                continue
            seen.add((g, r["code"]))
            acc = groups.setdefault(g, {"turnover": 0.0, "mf": 0.0, "volume": 0.0, "shares": 0.0, "cap": 0.0, "inst": 0.0})
            acc["turnover"] += t
            acc["mf"] += clv(r) * t
            acc["volume"] += r.get("volume_shares") or 0.0
            if cap and close:
                acc["shares"] += cap / close
            acc["cap"] += cap
            if close and r["code"] in inst:
                acc["inst"] += inst[r["code"]] * close
    return {"market": market, "groups": groups}


def daily_group_flows(store: HistoryStore, dates: list[date], grouping: Grouping) -> list[dict]:
    key = getattr(grouping, "cache_key", None)
    cache = store.derived_cache
    # per-day institutional version: backfilling one day must not invalidate every other day
    hits = {d: cache.get(("flows", key, d, store.inst_version(d))) for d in dates} if key is not None else {}
    missing = [d for d in dates if hits.get(d) is None]
    rows = store.rows(missing) if missing else {}
    inst = store.inst_net(missing) if missing else {}
    out = []
    for d in dates:
        day = hits.get(d)
        if day is None:
            day = flow_day(rows[d], grouping, inst.get(d, {}))
            if key is not None:
                cache[("flows", key, d, store.inst_version(d))] = day
        out.append(day)
    return out


def share_of(day: dict, name: str) -> float:
    g = day["groups"].get(name)
    return g["turnover"] / day["market"] * 100 if g and day["market"] else 0.0


def zscore(value: float, history: list[float]) -> float | None:
    if len(history) < 3:
        return None
    mean = sum(history) / len(history)
    var = sum((x - mean) ** 2 for x in history) / len(history)
    return (value - mean) / max(var ** 0.5, STD_FLOOR)


def group_flows(store: HistoryStore, day: date, grouping: Grouping = industry_grouping) -> dict[str, dict]:
    """{group: {share, z5, z20, mf, turnover_rate, inst_net, inst_ratio}} for `day` (see the spec)."""
    dates = sorted(store.trading_dates(up_to=day, limit=FLOW_WINDOW + 1))
    if not dates or dates[-1] != day:
        return {}
    flows = daily_group_flows(store, dates, grouping)
    today, prev = flows[-1], flows[:-1]
    has_inst = day in store.inst_dates()
    out = {}
    for name, g in today["groups"].items():
        share = share_of(today, name)
        hist = [share_of(p, name) for p in prev]
        out[name] = {
            "share": share,
            "z5": zscore(share, hist[-5:]),
            "z20": zscore(share, hist),
            "mf": g["mf"] / g["turnover"] * 100 if g["turnover"] else None,
            "turnover_rate": g["volume"] / g["shares"] * 100 if g["shares"] else None,
            "inst_net": g["inst"] if has_inst else None,
            "inst_ratio": g["inst"] / g["cap"] * 100 if has_inst and g["cap"] else None,
        }
    return out


def flow_baseline(store: HistoryStore, grouping: Grouping = industry_grouping, before: date | None = None) -> dict:
    """Share mean/std per group over the latest 5 and 20 stored days before `before` (the live
    z-score baseline: the day the live quotes show must not be part of its own baseline)."""
    up_to = before - timedelta(days=1) if before else None
    dates = sorted(store.trading_dates(up_to=up_to, limit=FLOW_WINDOW))
    if not dates:
        return {"as_of": None, "groups": {}}
    flows = daily_group_flows(store, dates, grouping)
    names = {n for f in flows for n in f["groups"]}

    def stats(xs: list[float]) -> tuple[float, float]:
        m = sum(xs) / len(xs)
        return m, max((sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5, STD_FLOOR)

    out = {}
    for n in names:
        hist = [share_of(f, n) for f in flows]
        m5, s5 = stats(hist[-5:])
        m20, s20 = stats(hist)
        out[n] = {"m5": m5, "s5": s5, "m20": m20, "s20": s20}
    return {"as_of": dates[-1].isoformat(), "groups": out}


def share_z_rotation(store: HistoryStore, days: int, up_to: date | None, grouping: Grouping) -> dict:
    """C matrix metric: each day's 20-day turnover-share z-score per group."""
    dates = sorted(store.trading_dates(up_to=up_to, limit=days + FLOW_WINDOW))
    flows = daily_group_flows(store, dates, grouping)
    shown = list(range(max(0, len(dates) - days), len(dates)))
    names = {n for i in shown for n in flows[i]["groups"]}
    groups = []
    for n in names:
        values = []
        for i in shown:
            hist = [share_of(flows[j], n) for j in range(max(0, i - FLOW_WINDOW), i)]
            z = zscore(share_of(flows[i], n), hist) if n in flows[i]["groups"] else None
            values.append(round(z, 2) if z is not None else None)
        groups.append({"name": n, "values": values})

    def recent(g):
        tail = [x for x in g["values"][-3:] if x is not None]
        return sum(tail) / len(tail) if tail else float("-inf")

    groups.sort(key=recent, reverse=True)
    return {"dates": [dates[i].isoformat() for i in shown], "metric": "share_z", "groups": groups}
