"""How tightly a group's members move together (spec 2026-10-23-linkage-design.md).

- Daily: Pearson correlation of daily % changes between the top-N members by market cap, over the
  last 20 and 60 trading days; the group's R is the mean of the pairwise values.
- Intraday: correlation of 5-minute returns (within each session, never across the overnight gap)
  over the last 1–3 sessions, from the app's own recorded polls or, for days it did not record,
  Yahoo 5-minute bars.
- Peak timing (日內最高點時間): the minute each member first reaches its 09:00–13:00 high (recorded polls, else Yahoo
  1-minute bars for the last ~30 days, else the 5-minute bars); the spread (std, minutes) and how
  long after the leader (largest cap) the others peak.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import date

from .metrics import Grouping
from .store import HistoryStore

TOP_N = 8
MIN_POINTS = 10  # pairs with fewer common observations are left out
HIGH_R = 0.7
LOW_R = 0.3
SYNC_MIN = 10
SCATTER_MIN = 60


def pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5


def pair_matrix(series: list[list[float | None]], min_points: int = MIN_POINTS) -> tuple[list[list[float | None]], float | None]:
    """Pairwise correlations over positions where both series have values, and their mean."""
    n = len(series)
    m: list[list[float | None]] = [[None] * n for _ in range(n)]
    vals = []
    for i in range(n):
        m[i][i] = 1.0
        for j in range(i + 1, n):
            pts = [(x, y) for x, y in zip(series[i], series[j]) if x is not None and y is not None]
            r = pearson([x for x, _ in pts], [y for _, y in pts]) if len(pts) >= min_points else None
            m[i][j] = m[j][i] = round(r, 3) if r is not None else None
            if r is not None:
                vals.append(r)
    return m, (round(sum(vals) / len(vals), 3) if vals else None)


INTRADAY_HIGH_R = 0.5  # 5-minute returns are far noisier than daily ones
INTRADAY_LOW_R = 0.2


def verdict_r(r: float | None, high: float = HIGH_R, low: float = LOW_R) -> str:
    if r is None:
        return "資料不足"
    if r > high:
        return "群體圍攻"
    if r < low:
        return "單兵作戰"
    return "中度連動"


def verdict_timing(std_min: float | None) -> str:
    if std_min is None:
        return "資料不足"
    if std_min <= SYNC_MIN:
        return "高度同步"
    if std_min >= SCATTER_MIN:
        return "時間錯落"
    return "普通"


def top_members(rows: list[dict], grouping: Grouping, group: str, n: int = TOP_N) -> list[dict]:
    seen, members = set(), []
    for r in rows:
        if r.get("kind") == "stock" and group in grouping(r) and r["code"] not in seen:
            seen.add(r["code"])
            members.append(r)
    members.sort(key=lambda r: -(r.get("market_cap") or 0))
    return members[:n]


def turnover_rate(r: dict) -> float | None:
    """週轉率 %: shares traded / shares outstanding (market cap ÷ close)."""
    cap, close, vol = r.get("market_cap"), r.get("close"), r.get("volume_shares")
    if not cap or not close or vol is None:
        return None
    return vol / (cap / close) * 100


def amplitude_pct(r: dict) -> float | None:
    """振幅 %: (high − low) / previous close."""
    h, lo, prev = r.get("high"), r.get("low"), r.get("prev_close")
    return (h - lo) / prev * 100 if h and lo and prev else None


def daily_linkage(store: HistoryStore, day: date, codes: list[str], windows: Iterable[int] = (20, 60)) -> dict:
    windows = list(windows)
    dates = sorted(store.trading_dates(up_to=day, limit=max(windows)))
    by_day = store.change_rows(dates)
    pct = {d: {r["code"]: r["change_pct"] for r in by_day[d]} for d in dates}
    out = {}
    for w in windows:
        span = dates[-w:]
        series = [[pct[d].get(c) for d in span] for c in codes]
        matrix, mean = pair_matrix(series)
        out[f"r{w}"] = mean
        out[f"matrix{w}"] = matrix
    return out


def group_r20(store: HistoryStore, day: date, grouping: Grouping) -> dict[str, float | None]:
    """Mean pairwise 20-day R of every group's top members (the B column), cached per day."""
    key = ("link_r20", getattr(grouping, "cache_key", None), day)
    if key[1] is not None and key in store.derived_cache:
        return store.derived_cache[key]
    rows = store.day_rows(day)
    names = {g for r in rows if r.get("kind") == "stock" for g in grouping(r)}
    dates = sorted(store.trading_dates(up_to=day, limit=20))
    by_day = store.change_rows(dates)
    pct = {d: {r["code"]: r["change_pct"] for r in by_day[d]} for d in dates}
    out = {}
    for name in names:
        codes = [r["code"] for r in top_members(rows, grouping, name)]
        if len(codes) < 2:
            out[name] = None
            continue
        _, out[name] = pair_matrix([[pct[d].get(c) for d in dates] for c in codes])
    if key[1] is not None:
        store.derived_cache[key] = out
    return out


# ---- intraday ---------------------------------------------------------------------------------

def minutes(hhmm: str) -> int:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return h * 60 + m - 9 * 60


def bucket_closes(points: list[tuple[str, float]], size: int = 5) -> dict[int, float]:
    """Recorded polls → last price per `size`-minute bucket (bucket = minutes since 09:00 // size)."""
    out: dict[int, float] = {}
    for ts, price in points:
        out[minutes(ts) // size] = price
    return out


def returns(closes: Mapping[int, float], buckets: list[int], locked: float | None = None) -> list[float | None]:
    """Bucket-to-bucket % returns on a shared bucket axis (None where either end is missing). With
    `locked` (the limit-up level in the closes' own scale), a step whose two closes both sit there is
    None too: a flat locked line is not 'not moving together'."""
    out = []
    for a, b in zip(buckets, buckets[1:]):
        x, y = closes.get(a), closes.get(b)
        if locked and x and y and x >= locked * (1 - 1e-6) and y >= locked * (1 - 1e-6):
            out.append(None)
        else:
            out.append((y / x - 1) * 100 if x and y else None)
    return out


def locked_level(closes: Mapping[int, float], row: Mapping | None) -> float | None:
    """The limit-up level in the closes' own scale, or None when the session never reached limit-up.
    Taken as the session's top close when the official high touched the limit, so Yahoo's
    back-adjusted prices (a different scale from the official ones) still match."""
    if not closes or not row or not row.get("prev_close") or row.get("high") is None:
        return None
    limit = limit_up_price(row["prev_close"])
    if limit is None or row["high"] < limit - 0.001:
        return None
    return max(closes.values())


PEAK_END = 240  # minutes after 09:00: the intraday high is taken over 09:00–13:00


def peak_minute_from_ticks(points: list[tuple[str, float]]) -> int | None:
    points = [p for p in points if minutes(p[0]) <= PEAK_END]
    if not points:
        return None
    best = max(p for _, p in points)
    return next(minutes(ts) for ts, p in points if p == best)


def peak_minute_from_bars(bars: list[tuple]) -> int | None:
    """bars: (HH:MM, high, low, close); the bar start of the first bar reaching the 09:00–13:00 high."""
    bars = [b for b in bars if minutes(b[0]) <= PEAK_END]
    if not bars:
        return None
    best = max(b[1] for b in bars)
    return next(minutes(b[0]) for b in bars if b[1] == best)


# TWSE / TPEx stock tick sizes (升降單位) by price band
TICKS = ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0), (float("inf"), 5.0))


def tick_size(price: float) -> float:
    return next(t for top, t in TICKS if price < top)


def limit_up_price(prev_close: float | None) -> float | None:
    """The limit-up price: prev close × 1.1 rounded down to its tick. Compare prices with this, not
    with one rebuilt from the rounded % (487.5 → 536, but 9.95% rebuilds 536.006)."""
    if not prev_close:
        return None
    raw = prev_close * 1.1
    tick = tick_size(raw)
    return round(int(raw / tick + 1e-9) * tick, 2)


def limit_up_pct(prev_close: float | None) -> float | None:
    """% change at the limit-up price (so 9.5–10%, not 10)."""
    price = limit_up_price(prev_close)
    return None if price is None else round((price / prev_close - 1) * 100, 2)


def limit_down_pct(prev_close: float | None) -> float | None:
    """% change at the limit-down price: prev close × 0.9 rounded up to its tick."""
    if not prev_close:
        return None
    raw = prev_close * 0.9
    tick = tick_size(raw)
    price = math.ceil(raw / tick - 1e-9) * tick
    return round((price / prev_close - 1) * 100, 2)


def align_bars(bars: list[tuple], day_row: dict) -> list[tuple]:
    """Scale Yahoo bars back to the official prices of that day.

    Yahoo back-adjusts history for capital reductions / splits (信昌電 before 2026-10-19 comes 1/0.94
    too high), while % changes here are taken against the exchange's own previous close. When the
    official high and low both differ from Yahoo's by the same factor, every price is scaled by it."""
    hi, lo = day_row.get("high"), day_row.get("low")
    if not bars or not hi or not lo:
        return bars
    y_hi = max(b[1] for b in bars)
    y_lo = min(b[2] for b in bars)
    if not y_hi or not y_lo:
        return bars
    k_hi, k_lo = hi / y_hi, lo / y_lo
    if abs(k_hi - 1) < 0.003 or abs(k_hi - k_lo) / k_hi > 0.01:
        return bars  # same prices, or not one clean factor: leave them
    k = (k_hi + k_lo) / 2
    return [(b[0], *[round(x * k, 4) if x is not None else None for x in b[1:5]], *b[5:]) for b in bars]


def minute_bars(bars: list[tuple]) -> list[list]:
    """Yahoo bars (HH:MM, high, low, close[, open, volume]) → [[minute, open, high, low, close, volume]]."""
    out = []
    for b in bars:
        m = minutes(b[0])
        if not 0 <= m <= 270:
            continue
        o = b[4] if len(b) > 4 and b[4] is not None else b[3]
        out.append([m, o, b[1], b[2], b[3], b[5] if len(b) > 5 else None])
    return out


def tick_bars(points: list[tuple[str, float]]) -> list[list]:
    """Recorded polls → per-minute [[minute, open, high, low, close, None]] (polls carry no volume)."""
    by: dict[int, list] = {}
    for ts, p in points:
        m = minutes(ts)
        if not 0 <= m <= 270 or not p:
            continue
        b = by.get(m)
        if b is None:
            by[m] = [m, p, p, p, p, None]
        else:
            b[2], b[3], b[4] = max(b[2], p), min(b[3], p), p
    return [by[m] for m in sorted(by)]


def with_close_bar(bars: list[list], day_row: dict) -> list[list]:
    """End on 13:30 with the official close; its volume is what the bars left out of the day's total."""
    close = day_row.get("close")
    if not bars or close is None:
        return bars
    bars = [b for b in bars if b[0] < 270]
    vols = [b[5] for b in bars]
    total = day_row.get("volume_shares")
    rest = total - sum(vols) if total is not None and all(v is not None for v in vols) else None
    return bars + [[270, close, close, close, close, rest if rest is not None and rest > 0 else (0 if rest is not None else None)]]


def race_points(points: list[tuple], prev_close: float | None) -> list[list[float]]:
    """(HH:MM[:SS], high, low, close) bars or (ts, price, price, price) polls →
    [[minute since 09:00, close %, high %, low %]]: the last close, highest high and lowest low per
    minute. K's animated timeline replays these from 09:00 to 13:30; its moving high dot uses the
    highs (so it lands where peak_minute_from_* put the 日內最高點), the 振幅 so far the highs and lows."""
    if not prev_close:
        return []
    pct = lambda p: round((p / prev_close - 1) * 100, 2)  # noqa: E731
    by_minute: dict[int, list[float]] = {}
    for ts, high, low, close in points:
        m = minutes(ts)
        if not (0 <= m <= 270 and high and low and close):
            continue
        prev = by_minute.get(m)
        by_minute[m] = [pct(close), max(pct(high), prev[1]) if prev else pct(high), min(pct(low), prev[2]) if prev else pct(low)]
    return [[m, c, h, lo] for m, (c, h, lo) in sorted(by_minute.items())]


def peak_stats(peaks: list[int | None]) -> tuple[float | None, float | None]:
    """(std of peak minutes, mean delay of the others after the leader = peaks[0])."""
    known = [p for p in peaks if p is not None]
    if len(known) < 2:
        return None, None
    mean = sum(known) / len(known)
    std = (sum((p - mean) ** 2 for p in known) / len(known)) ** 0.5
    lag = None
    if peaks[0] is not None:
        others = [p - peaks[0] for p in peaks[1:] if p is not None]
        lag = sum(others) / len(others) if others else None
    return round(std, 1), (round(lag, 1) if lag is not None else None)


def fmt_minute(m: int | None) -> str | None:
    if m is None:
        return None
    return f"{9 + m // 60:02d}:{m % 60:02d}"


K5_DAYS = 5  # K's 5 日 K: candles shown per stock
K5_VR_DAYS = 5  # 量比 against the average volume of this many days before each candle
K5_AFTER = 2  # trading days after the shown day, when stored (marked `after`)


def daily_k(store: HistoryStore, day: date, codes: list[str], days: int = K5_DAYS, vr_days: int = K5_VR_DAYS, after: int = K5_AFTER) -> dict[str, list[dict]]:
    """The last `days` trading days up to `day` for each code, then up to `after` stored days past it, oldest
    first: {date, open, high, low, close, pct, lots (張), limit (closed at limit-up), vr (量比 vs the vr_days
    before; None without history), inst (三大法人 張; None when not published), after (past `day`)}."""
    later = sorted(x for x in store.trading_dates() if x > day)[:after]
    dates = sorted(store.trading_dates(up_to=day, limit=days + vr_days)) + later
    rows = store.rows(dates)
    wanted = set(codes)
    by_code: dict[str, dict[date, dict]] = {c: {} for c in codes}
    for d in dates:
        for r in rows[d]:
            if r["code"] in wanted:
                by_code[r["code"]][d] = r
    shown = [x for x in dates if x <= day][-days:] + later
    inst = store.inst_net(shown, codes)
    dtr = daytrade_ratios(store, shown, codes)
    out: dict[str, list[dict]] = {}
    for c in codes:
        series = []
        for d in shown:
            r = by_code[c].get(d)
            if not r or r.get("close") is None:
                continue
            before = [x["volume_shares"] for x in (by_code[c].get(p) for p in dates[: dates.index(d)][-vr_days:]) if x and x.get("volume_shares")]
            vol = r.get("volume_shares")
            limit = limit_up_price(r.get("prev_close"))
            net = inst.get(d, {}).get(c)
            series.append({
                "date": d.isoformat(),
                "open": r.get("open"), "high": r.get("high"), "low": r.get("low"), "close": r["close"],
                "pct": r.get("change_pct"),
                "lots": round(vol / 1000) if vol is not None else None,
                "limit": bool(limit and r["close"] >= limit - 0.001),
                "vr": round(vol / (sum(before) / len(before)), 2) if vol is not None and before else None,
                "inst": round(net / 1000) if net is not None else None,
                "after": d > day,
                "dt": dtr.get(c, {}).get(d),
            })
        out[c] = series
    return out


def index_k(store: HistoryStore, day: date, markets: list[str], days: int = K5_DAYS, after: int = K5_AFTER) -> dict[str, list[dict]]:
    """daily_k's shape for the 大盤／櫃買 rows, from the official index days: {market: [{date, open, high,
    low, close, pct, after, …}]} (no volume, 法人 or limit-up for an index)."""
    index = store.index_rows()
    later = sorted(x for x in store.trading_dates() if x > day)[:after]
    shown = sorted(store.trading_dates(up_to=day, limit=days)) + later
    out: dict[str, list[dict]] = {}
    for m in markets:
        series = []
        for d in shown:
            r = index.get(d.isoformat(), {}).get(m)
            if not r or r.get("close") is None or r.get("high") is None or r.get("low") is None:
                continue
            prev = r["close"] - (r.get("change") or 0)
            series.append({
                "date": d.isoformat(), "open": r.get("open"), "high": r["high"], "low": r["low"], "close": r["close"],
                "pct": (r["change"] / prev * 100) if r.get("change") is not None and prev else None,
                "lots": None, "limit": False, "vr": None, "inst": None, "after": d > day,
            })
        out[m] = series
    return out


DAYTRADE_HOT = 50.0  # (40 marked nearly every group on a busy day) 當沖比 (% of the day's traded shares) from which the move reads as 隔日沖 hot money


DAYTRADE_BASE_DAYS = 20  # 平常的當沖比: the average over this many trading days before


def daytrade_group(vals: Mapping[str, float | None], before: Mapping[str, list[float]] | None = None) -> dict:
    """A group's 當沖: {by_code, avg, hot, base}. Members without data are left out of the average; the
    average is rounded to 0.1% (as shown) before the DAYTRADE_HOT test, so 49.96 shows as 50.0% and is 🔥.
    base: each member's usual 當沖比 (the mean of `before`, the days before), to tell a sudden rush of
    hot money from a stock that is always day-traded this much (shown only; 🔥 still follows the 50%)."""
    known = {c: v for c, v in vals.items() if v is not None}
    avg = round(sum(known.values()) / len(known), 1) if known else None
    base = {c: round(sum(xs) / len(xs), 1) for c, xs in (before or {}).items() if c in known and xs}
    return {"by_code": known, "avg": avg, "hot": avg is not None and avg >= DAYTRADE_HOT, "base": base}


SURGE_X = 1.5  # 🔥 突然湧入: the members' 當沖比 at least this many times their usual (the 20 days before);
# 1.5 since 2026-10-25 (research/exp7: 75 stock-days faded −1.50% intraday next day; 2× had only 16)


def hot_class(group: Mapping) -> str:
    """A group's 當沖 as a class: 'rest' (not 🔥), 'surge' (🔥 and the members at SURGE_X × their usual or
    more, on average — hot money rushing in) or 'habitual' (🔥 but day-traded this much anyway)."""
    if not group["hot"]:
        return "rest"
    xs = [v / group["base"][c] for c, v in group["by_code"].items() if group["base"].get(c)]
    return "surge" if xs and sum(xs) / len(xs) >= SURGE_X else "habitual"


EVIDENCE_CHECKS = ("excess", "baseline", "burst", "sync", "samedir")  # what a stored N group carries (server-side)


def evidence_yes(group: Mapping) -> int:
    """✓ among a stored group's 5 server-side checks (a check without data counts as no ✓)."""
    ev = group.get("evidence") or {}
    return sum(1 for k in EVIDENCE_CHECKS if (ev.get(k) or {}).get("ok") is True)


def evidence_class(n: int) -> str:
    return "ev4" if n >= 4 else "ev2" if n >= 2 else "ev0"


def next_day_stats(results: list[tuple[date, list[list[str]]]], class_of, rows_on, next_of, index_pct, parents: Mapping[str, str] | None = None) -> dict:
    """隔天表現 of N's groups by class: results [(day, [member codes per group])], class_of(day, codes) →
    'hot'／'rest' or any other class name (True/False read as hot/rest), rows_on(day) → {code: row},
    next_of(day) → the next trading day or None, index_pct(day) → 大盤 % or None. Per class: {groups,
    stocks, days, gap, close, excess, intraday, fade, down} — means in %, fade = the share closing below
    the open, down = the share closing below 昨收; excess = close − 大盤. parents {class: parent}: a group
    also counts toward its parent class (🔥 as the sum of its kinds)."""
    acc: dict[str, dict] = {k: {"groups": 0, "days": set(), "gap": [], "close": [], "excess": [], "intraday": []} for k in ("hot", "rest")}
    for day, groups in results:
        nd = next_of(day)
        if nd is None:
            continue
        nxt = rows_on(nd)
        idx = index_pct(nd)
        for codes in groups:
            k = class_of(day, codes)
            k = "hot" if k is True else "rest" if k is False else k
            targets = [acc.setdefault(x, {"groups": 0, "days": set(), "gap": [], "close": [], "excess": [], "intraday": []})
                       for x in ([k, parents[k]] if parents and k in parents else [k])]
            got = False
            for c in codes:
                r = nxt.get(c)
                if not r or r.get("change_pct") is None:
                    continue
                got = True
                for a in targets:
                    a["close"].append(r["change_pct"])
                    if idx is not None:
                        a["excess"].append(r["change_pct"] - idx)
                    if r.get("open") and r.get("prev_close"):
                        a["gap"].append((r["open"] / r["prev_close"] - 1) * 100)
                    if r.get("open") and r.get("close"):
                        a["intraday"].append((r["close"] / r["open"] - 1) * 100)
            if got:
                for a in targets:
                    a["groups"] += 1
                    a["days"].add(day)
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    out = {}
    for k, a in acc.items():
        out[k] = {"groups": a["groups"], "days": len(a["days"]), "stocks": len(a["close"]), "gap": mean(a["gap"]), "close": mean(a["close"]),
                  "excess": mean(a["excess"]), "intraday": mean(a["intraday"]),
                  "fade": round(sum(1 for x in a["intraday"] if x < 0) / len(a["intraday"]) * 100, 1) if a["intraday"] else None,
                  "down": round(sum(1 for x in a["close"] if x < 0) / len(a["close"]) * 100, 1) if a["close"] else None}
    return out


def daytrade_ratios(store: HistoryStore, days: list[date], codes: list[str]) -> dict[str, dict[date, float]]:
    """{code: {day: 當沖比 %}} (上市 and 上櫃 alike)."""
    dt = store.daytrade(days, codes)
    vols = store.volumes(days, codes)  # these stocks' volume only: a 20-day window of every row was the slow part
    out: dict[str, dict[date, float]] = {c: {} for c in codes}
    for d in days:
        vol = vols.get(d, {})
        for c, sh in dt.get(d, {}).items():
            if c in out and vol.get(c):
                out[c][d] = round(sh / vol[c] * 100, 1)
    return out
