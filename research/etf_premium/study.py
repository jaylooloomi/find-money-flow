"""The ETF 溢價 event study: events, forward returns, tests, robustness, the chart.

Data (data/raw/etf/, fetch.py and fetch_mops.py first):
- 淨值 and 收盤價: 公開資訊觀測站 (official; mops/ and mops_active/), or MoneyDJ with --nav moneydj (robustness)
- 成交量: FinMind; 配息: FinMind (an ex-date on a closed day moves to the next trading day)
- 加權報酬指數: TWSE MFI94U
Nothing is filled in: a day without a 淨值 or a trade is missing, and so is any return that needs it.

Run: .venv/Scripts/python research/etf_premium/study.py   → results/summary.md, main_table.csv, robustness.md,
     events.csv, median_returns.svg (and the appendix files)
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE.parents[1] / "data" / "raw" / "etf"
OUT = HERE / "results"
if "--ext" in sys.argv:  # the appendix: 2003 on, MoneyDJ 淨值 (the official pages are fetched for 2020 on only)
    RAW = RAW.parent / "etf_ext"
    OUT = OUT / "ext"
DOMESTIC_TYPES = {"國內成分證券指數股票型基金", "國內成分證券主動式交易所交易基金(股票)"}
WINDOWS = (5, 10, 20, 60, 120, 240)
GROUPS = ("溢價", "中性", "折價")
MIN_N = 30  # fewer events than this: 「樣本不足」


@dataclass(frozen=True)
class Spec:
    drop: float = -0.08  # 淨值 (含息) vs its 10-day high
    prem: float = 0.008  # 溢價組 from
    neutral: float = 0.003  # 中性組 within ±
    disc: float = -0.005  # 折價組 up to
    liq_lots: float = 5000  # 60-day average volume
    dedup: int = 20  # trading days
    start: str = "20200101"
    end: str = "99999999"
    active: bool = True  # include 主動式
    nav: str = "mops"  # or "moneydj"


@dataclass
class Etf:
    code: str
    days: list[str]  # trading days with a 淨值, oldest first
    nav: dict[str, float]  # split-adjusted to today's units
    close: dict[str, float]  # split-adjusted; only days it traded
    raw_prem: dict[str, float]  # 收盤 ÷ 淨值 − 1 on the same day's raw numbers
    div: dict[str, float]  # split-adjusted cash per unit, keyed by the effective ex-date
    vol60: dict[str, float]  # 60-day average volume (lots) ending that day; None when < 60 days of history
    active: bool = False
    splits: list[tuple[str, float]] = field(default_factory=list)


def read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(s: str | None) -> float | None:
    try:
        v = float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


# ---------------------------------------------------------------- loading


def load_calendar() -> tuple[list[str], dict[str, float]]:
    tr = {r["date"].replace("-", ""): float(r["tr_index"]) for r in read(RAW / "taiex_tr.csv")}
    return sorted(tr), tr


def load_mops() -> dict[str, dict[str, tuple[float, float | None]]]:
    """{code: {day: (淨值, 收盤 or None)}} from the official daily pages (raw units)."""
    out: dict[str, dict] = defaultdict(dict)
    for sub in ("mops", "mops_active"):
        for p in sorted((RAW / sub).glob("*.csv")):
            day = p.stem
            for r in read(p):
                nav = num(r["nav"])
                if nav and r["nav_date"] == day:  # a page can carry a stale 淨值 date: not that day's
                    out[r["code"]][day] = (nav, num(r["close"]))
    return out


def split_segments(code: str) -> list[tuple[str, float]]:
    """[(from day, factor)] where raw = MoneyDJ-adjusted × factor; the last segment is 1 (today's units)."""
    adj = {r["date"]: num(r["price_adj"]) for r in read(RAW / "nav" / f"{code}.csv")}
    px = {r["date"].replace("-", ""): num(r["close"]) for r in read(RAW / "price" / f"{code}.csv") if num(r.get("Trading_Volume"))}
    segs: list[tuple[str, float]] = []
    for d in sorted(set(adj) & set(px)):
        if not adj[d] or not px[d]:
            continue
        f = round(px[d] / adj[d])
        if f >= 1 and (not segs or f != segs[-1][1]):
            segs.append((d, float(f)))
    return segs


def load(spec: Spec, cal: list[str]) -> list[Etf]:
    uni = json.loads((RAW / "universe.json").read_text(encoding="utf-8"))
    mops = load_mops() if spec.nav == "mops" else {}
    out = []
    for x in uni:
        if x["基金類型"] not in DOMESTIC_TYPES:
            continue
        code = x["基金代號"]
        active = code.endswith("A")
        if active and not spec.active:
            continue
        segs = split_segments(code)
        factor = lambda d: next((f for s, f in reversed(segs) if d >= s), segs[0][1] if segs else 1.0)  # noqa: E731
        # 淨值／收盤 (raw units)
        if spec.nav == "mops":
            raw = mops.get(code, {})
        else:
            raw = {}
            for r in read(RAW / "nav" / f"{code}.csv"):
                n, c = num(r["nav_adj"]), num(r["price_adj"])
                if n:
                    raw[r["date"]] = (n * factor(r["date"]), c * factor(r["date"]) if c else None)
        prices = {r["date"].replace("-", ""): r for r in read(RAW / "price" / f"{code}.csv")}
        traded = {d for d, r in prices.items() if num(r.get("Trading_Volume"))}
        if not raw:
            continue
        days = sorted(d for d in raw if d in set(cal))
        nav = {d: raw[d][0] / factor(d) for d in days}
        close = {d: raw[d][1] / factor(d) for d in days if raw[d][1] and d in traded}
        prem = {d: raw[d][1] / raw[d][0] - 1 for d in days if raw[d][1] and d in traded}
        # 配息 on its effective ex-date: a closed day moves to the next day with a 淨值
        div: dict[str, float] = {}
        for r in read(RAW / "dividend" / f"{code}.csv"):
            cash, ex = num(r.get("CashEarningsDistribution")), (r.get("CashExDividendTradingDate") or "").replace("-", "")
            if not cash or not ex:
                continue
            eff = next((d for d in days if d >= ex), None)
            if eff:
                div[eff] = div.get(eff, 0) + cash / factor(eff)
        # 60-day average volume over the market's trading days (a day without a row counts 0)
        vol = [float(prices[d]["Trading_Volume"]) / 1000 if d in prices else 0.0 for d in cal]
        first = min(prices) if prices else None
        vol60, acc = {}, 0.0
        for i, d in enumerate(cal):
            acc += vol[i]
            if i >= 60:
                acc -= vol[i - 60]
            if first and i >= 59 and cal[i - 59] >= first:
                vol60[d] = acc / 60
        out.append(Etf(code, days, nav, close, prem, div, vol60, active, segs))
    return out


# ---------------------------------------------------------------- events and returns


def tr_nav(e: Etf) -> dict[str, float]:
    """The 淨值 with dividends reinvested (for the 下跌 condition): 1.0 on the first day."""
    out, v = {}, 1.0
    for i, d in enumerate(e.days):
        if i:
            p = e.days[i - 1]
            v *= (e.nav[d] + e.div.get(d, 0)) / e.nav[p]
        out[d] = v
    return out


def group_of(prem: float, s: Spec) -> str | None:
    if prem >= s.prem:
        return "溢價"
    if -s.neutral <= prem <= s.neutral:
        return "中性"
    if prem <= s.disc:
        return "折價"
    return None


def events(etfs: list[Etf], s: Spec, cal: list[str]) -> list[dict]:
    pos = {d: i for i, d in enumerate(cal)}
    out = []
    for e in etfs:
        t = tr_nav(e)
        last = -10**9
        for k, d in enumerate(e.days):
            if not (s.start <= d <= s.end):
                continue
            i = pos[d]
            window = [t[x] for x in cal[max(0, i - 9):i + 1] if x in t]
            if len(window) < 10:  # a gap in the 10 days: no condition (not filled in)
                continue
            dd = t[d] / max(window) - 1
            if dd > s.drop or d not in e.raw_prem:
                continue
            v = e.vol60.get(d)
            if v is None or v < s.liq_lots:
                continue
            g = group_of(e.raw_prem[d], s)
            if g is None or i - last < s.dedup:
                continue
            last = i
            out.append({"code": e.code, "date": d, "group": g, "premium_pct": round(e.raw_prem[d] * 100, 3),
                        "drawdown_pct": round(dd * 100, 2), "vol60_lots": round(v), "active": e.active, "_etf": e})
    return out


def forward(ev: dict, cal: list[str], tr: dict[str, float]) -> None:
    """Adds nav_N, price_N, excess_N, mdd_N (percent) for each window; missing when a needed day is."""
    e: Etf = ev["_etf"]
    pos = {d: i for i, d in enumerate(cal)}
    i0, d0 = pos[ev["date"]], ev["date"]
    for n in WINDOWS:
        for k in ("nav", "price", "excess", "mdd"):
            ev[f"{k}_{n}"] = None
        if i0 + n >= len(cal):
            continue
        span = cal[i0 + 1:i0 + n + 1]
        d1 = cal[i0 + n]
        cash = sum(e.div.get(d, 0) for d in span)
        if d1 in e.nav and all(d in e.nav for d in span):
            r = (e.nav[d1] + cash) / e.nav[d0] - 1
            ev[f"nav_{n}"] = round(r * 100, 3)
            ev[f"excess_{n}"] = round((r - (tr[d1] / tr[d0] - 1)) * 100, 3)
            # 最大回撤 of the 淨值 + dividends received so far, from the event day on
            peak, mdd, got = e.nav[d0], 0.0, 0.0
            for d in span:
                got += e.div.get(d, 0)
                v = e.nav[d] + got
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1)
            ev[f"mdd_{n}"] = round(mdd * 100, 3)
        if d0 in e.close and d1 in e.close:
            ev[f"price_{n}"] = round(((e.close[d1] + cash) / e.close[d0] - 1) * 100, 3)


# ---------------------------------------------------------------- statistics


def median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def mann_whitney(a: list[float], b: list[float]) -> tuple[float | None, float | None]:
    """Two-sided Mann-Whitney U, normal approximation with tie correction and continuity correction → (U of a, p)."""
    n1, n2 = len(a), len(b)
    if not n1 or not n2:
        return None, None
    allv = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks, i, ties = [0.0] * len(allv), 0, 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = r
        t = j - i + 1
        ties += t ** 3 - t
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, allv) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    n = n1 + n2
    var = n1 * n2 / 12 * ((n + 1) - ties / (n * (n - 1)))
    if var <= 0:
        return u1, None
    z = (abs(u1 - n1 * n2 / 2) - 0.5) / math.sqrt(var)
    return u1, math.erfc(max(z, 0) / math.sqrt(2))


def cluster_ci(a: list[tuple[str, float]], b: list[tuple[str, float]], reps: int = 2000, seed: int = 7) -> tuple[float | None, float | None]:
    """95% interval of median(a) − median(b), resampling event *dates* (events on one day share the market)."""
    if len(a) < 2 or len(b) < 2:
        return None, None
    rng = random.Random(seed)
    by_a, by_b = defaultdict(list), defaultdict(list)
    for d, v in a:
        by_a[d].append(v)
    for d, v in b:
        by_b[d].append(v)
    ka, kb = list(by_a), list(by_b)
    diffs = []
    for _ in range(reps):
        xa = [v for d in (rng.choice(ka) for _ in ka) for v in by_a[d]]
        xb = [v for d in (rng.choice(kb) for _ in kb) for v in by_b[d]]
        diffs.append(median(xa) - median(xb))
    diffs.sort()
    return diffs[int(0.025 * reps)], diffs[int(0.975 * reps) - 1]


def table(evs: list[dict]) -> list[dict]:
    rows = []
    for g in GROUPS:
        sub = [e for e in evs if e["group"] == g]
        for n in WINDOWS:
            nav = [e[f"nav_{n}"] for e in sub if e[f"nav_{n}"] is not None]
            px = [e[f"price_{n}"] for e in sub if e[f"price_{n}"] is not None]
            ex = [e[f"excess_{n}"] for e in sub if e[f"excess_{n}"] is not None]
            mdd = [e[f"mdd_{n}"] for e in sub if e[f"mdd_{n}"] is not None]
            rnd = lambda v: None if v is None else round(v, 2)  # noqa: E731
            rows.append({
                "group": g, "window": n, "events": len(nav), "dates": len({e["date"] for e in sub if e[f"nav_{n}"] is not None}),
                "flag": "樣本不足" if len(nav) < MIN_N else "",
                "nav_median": rnd(median(nav)), "nav_mean": rnd(sum(nav) / len(nav) if nav else None),
                "nav_win": rnd(100 * sum(v > 0 for v in nav) / len(nav) if nav else None),
                "price_median": rnd(median(px)), "price_win": rnd(100 * sum(v > 0 for v in px) / len(px) if px else None),
                "excess_median": rnd(median(ex)), "excess_win": rnd(100 * sum(v > 0 for v in ex) / len(ex) if ex else None),
                "mdd_median": rnd(median(mdd)),
            })
    return rows


def tests(evs: list[dict], key: str = "nav") -> list[dict]:
    rows = []
    for other in ("折價", "中性"):
        for n in WINDOWS:
            a = [(e["date"], e[f"{key}_{n}"]) for e in evs if e["group"] == "溢價" and e[f"{key}_{n}"] is not None]
            b = [(e["date"], e[f"{key}_{n}"]) for e in evs if e["group"] == other and e[f"{key}_{n}"] is not None]
            _, p = mann_whitney([v for _, v in a], [v for _, v in b])
            lo, hi = cluster_ci(a, b)
            ma, mb = median([v for _, v in a]), median([v for _, v in b])
            rows.append({"vs": other, "window": n, "n_prem": len(a), "n_other": len(b),
                         "median_diff": None if ma is None or mb is None else round(ma - mb, 2),
                         "ci_lo": None if lo is None else round(lo, 2), "ci_hi": None if hi is None else round(hi, 2),
                         "p": None if p is None else round(p, 4),
                         "flag": "樣本不足" if min(len(a), len(b)) < MIN_N else ""})
    return rows


def spearman(x: list[float], y: list[float]) -> float | None:
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    if len(x) < 3:
        return None
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def continuous(etfs: list[Etf], s: Spec, cal: list[str], tr: dict[str, float], reps: int = 1000) -> list[dict]:
    """Every 下跌 episode (first day of the condition, 20-day dedup, no group gaps): Spearman of the 折溢價
    against the forward 超額報酬, with a 95% interval resampling event dates."""
    loose = replace(s, prem=-1.0, neutral=0.0, disc=-1.0)  # every premium falls in 「溢價」: one event per episode
    evs = events(etfs, loose, cal)
    for e in evs:
        forward(e, cal, tr)
    rows = []
    rng = random.Random(11)
    for n in WINDOWS:
        pts = [(e["date"], e["premium_pct"], e[f"excess_{n}"]) for e in evs if e[f"excess_{n}"] is not None]
        rho = spearman([p for _, p, _ in pts], [r for _, _, r in pts])
        by = defaultdict(list)
        for d, p, r in pts:
            by[d].append((p, r))
        keys = list(by)
        boots = []
        for _ in range(reps):
            sample = [pr for d in (rng.choice(keys) for _ in keys) for pr in by[d]]
            v = spearman([p for p, _ in sample], [r for _, r in sample])
            if v is not None:
                boots.append(v)
        boots.sort()
        rows.append({"window": n, "events": len(pts), "rho": None if rho is None else round(rho, 3),
                     "ci_lo": round(boots[int(0.025 * len(boots))], 3) if boots else None,
                     "ci_hi": round(boots[int(0.975 * len(boots)) - 1], 3) if boots else None})
    return rows


# ---------------------------------------------------------------- output


def md_table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    fmt = lambda v: "—" if v is None or v == "" else (f"{v:+.2f}" if isinstance(v, float) else str(v))  # noqa: E731
    head = "| " + " | ".join(c for _, c in cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(fmt(r[k]) for k, _ in cols) + " |" for r in rows) + "\n"


MAIN_COLS = [("group", "組"), ("window", "N 日"), ("events", "事件數"), ("dates", "不同日期"), ("nav_median", "淨值含息中位數%"), ("nav_mean", "平均%"),
             ("nav_win", "勝率%"), ("price_median", "市價含息中位數%"), ("excess_median", "超額中位數%"), ("excess_win", "超額勝率%"),
             ("mdd_median", "最大回撤中位數%"), ("flag", "")]
TEST_COLS = [("vs", "溢價組 vs"), ("window", "N 日"), ("n_prem", "溢價 n"), ("n_other", "對照 n"), ("median_diff", "中位數差%"),
             ("ci_lo", "95% 下界"), ("ci_hi", "95% 上界"), ("p", "p 值"), ("flag", "")]


def svg_chart(rows: list[dict], path: Path) -> None:
    """三組 × 六視窗 的淨值含息中位數，一張折線圖（x 是視窗，等距）."""
    w, h, l, r, t, b = 640, 360, 56, 110, 24, 40
    vals = [x["nav_median"] for x in rows if x["nav_median"] is not None]
    lo, hi = min(vals + [0]), max(vals + [0])
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    X = lambda i: l + i * (w - l - r) / (len(WINDOWS) - 1)  # noqa: E731
    Y = lambda v: t + (hi - v) * (h - t - b) / (hi - lo)  # noqa: E731
    colors = {"溢價": "#e5484d", "中性": "#8b8d98", "折價": "#3e9b4f"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="12">',
             f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
             f'<line x1="{l}" x2="{w - r}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#999" stroke-dasharray="3 3"/>']
    step = 10 ** math.floor(math.log10(max(hi - lo, 1e-9))) / 1
    tick = math.ceil(lo / step) * step
    while tick <= hi:
        parts.append(f'<text x="{l - 6}" y="{Y(tick) + 4:.1f}" text-anchor="end" fill="#555">{tick:g}%</text>')
        tick += step
    for i, n in enumerate(WINDOWS):
        parts.append(f'<text x="{X(i):.1f}" y="{h - b + 18}" text-anchor="middle" fill="#555">{n} 日</text>')
    for g in GROUPS:
        pts = [(X(i), Y(x["nav_median"]), x) for i, n in enumerate(WINDOWS) for x in rows if x["group"] == g and x["window"] == n and x["nav_median"] is not None]
        if not pts:
            continue
        parts.append(f'<polyline fill="none" stroke="{colors[g]}" stroke-width="2" points="{" ".join(f"{a:.1f},{c:.1f}" for a, c, _ in pts)}"/>')
        for a, c, x in pts:
            hollow = x["events"] < MIN_N
            parts.append(f'<circle cx="{a:.1f}" cy="{c:.1f}" r="4" fill="{"#fff" if hollow else colors[g]}" stroke="{colors[g]}" stroke-width="2"/>')
        last = pts[-1]
        parts.append(f'<text x="{w - r + 8}" y="{last[1] + 4:.1f}" fill="{colors[g]}">{g}組</text>')
    parts.append(f'<text x="{l}" y="{t - 8}" fill="#333">淨值含息報酬中位數（空心點：事件數 &lt; {MIN_N}）</text></svg>')
    path.write_text("\n".join(parts), encoding="utf-8")


CONT_COLS = [("window", "N 日"), ("events", "事件數"), ("rho", "Spearman ρ"), ("ci_lo", "95% 下界"), ("ci_hi", "95% 上界")]


def write_reports(base: Spec, evs, main_rows, test_rows, rob, cont) -> None:
    """summary.md = the hand-written conclusion (conclusion.md, after reading the numbers) + the tables;
    robustness.md = every variant's counts and tests."""
    nl = "\n"
    head = (OUT / "conclusion.md").read_text(encoding="utf-8") if (OUT / "conclusion.md").exists() else "## 結論" + nl * 2 + "（尚未撰寫）" + nl
    counts = {g: sum(e["group"] == g for e in evs) for g in GROUPS}
    src = "公開資訊觀測站（官方）" if base.nav == "mops" else "MoneyDJ"
    meta = nl.join([
        "", "## 設定", "",
        f"- 淨值來源：{src}；期間 {base.start}～{base.end if base.end != '99999999' else '至今'}",
        f"- 下跌：含息淨值相對近 10 日最高 ≤ {base.drop:.0%}；溢價 ≥ {base.prem:+.1%}、中性 ±{base.neutral:.1%}、折價 ≤ {base.disc:+.1%}",
        f"- 近 60 日均量 ≥ {base.liq_lots:,.0f} 張（事件日往回算）；同一檔 {base.dedup} 個交易日內只取第一個事件",
        f"- 事件：溢價 {counts['溢價']}、中性 {counts['中性']}、折價 {counts['折價']}（明細 events.csv）",
        "- 報酬單位：%；「超額」= 淨值含息報酬 − 同期加權報酬指數；檢定：Mann-Whitney U（常態近似），"
        "95% 區間以「事件日期」為單位重抽（同一天的事件共用同一個市場，不能當獨立樣本）", ""])
    body = (head + meta + nl + "## 主表（main_table.csv）" + nl * 2 + md_table(main_rows, MAIN_COLS)
            + nl + "## 溢價組 vs 對照組（淨值含息報酬）" + nl * 2 + md_table(test_rows, TEST_COLS)
            + nl + "## 補充：把折溢價當連續變數（所有下跌事件，對超額報酬）" + nl * 2 + md_table(cont, CONT_COLS)
            + nl + "![三組中位數報酬](median_returns.svg)" + nl)
    (OUT / "summary.md").write_text(body, encoding="utf-8")
    parts = ["# 穩健性檢查" + nl * 2 + "每個變體只改一個設定，其餘同主設定。表內是溢價組 vs 對照組的淨值含息報酬中位數差（%）。" + nl]
    for name, mr, t_ in rob:
        c = {g: max((x["events"] for x in mr if x["group"] == g), default=0) for g in GROUPS}
        parts.append(nl + f"## {name}" + nl * 2 + f"事件數：溢價 {c['溢價']}、中性 {c['中性']}、折價 {c['折價']}" + nl * 2 + md_table(t_, TEST_COLS))
    (OUT / "robustness.md").write_text("".join(parts), encoding="utf-8")


def run(spec: Spec, etfs: list[Etf] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    cal, tr = load_calendar()
    etfs = etfs if etfs is not None else load(spec, cal)
    evs = events(etfs, spec, cal)
    for e in evs:
        forward(e, cal, tr)
    return evs, table(evs), tests(evs)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    nav_src = sys.argv[sys.argv.index("--nav") + 1] if "--nav" in sys.argv else "mops"
    base = Spec(nav=nav_src)
    cal, tr = load_calendar()
    etfs = load(base, cal)
    OUT.mkdir(exist_ok=True)
    evs, main_rows, test_rows = run(base, etfs)
    with (OUT / "main_table.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(main_rows[0]))
        w.writeheader()
        w.writerows(main_rows)
    with (OUT / "tests.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(test_rows[0]))
        w.writeheader()
        w.writerows(test_rows)
    cols = ["code", "date", "group", "premium_pct", "drawdown_pct", "vol60_lots", "active"] + [f"{k}_{n}" for n in WINDOWS for k in ("nav", "price", "excess", "mdd")]
    with (OUT / "events.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(evs)
    svg_chart(main_rows, OUT / "median_returns.svg")
    # robustness
    rob = []
    variants = [("溢價門檻 +0.5%", replace(base, prem=0.005)), ("溢價門檻 +1.2%", replace(base, prem=0.012)),
                ("下跌門檻 −5%", replace(base, drop=-0.05)), ("下跌門檻 −12%", replace(base, drop=-0.12)),
                ("2020–2022", replace(base, end="20221231")), ("2023–2024", replace(base, start="20230101", end="20241231")),
                ("2025–至今", replace(base, start="20250101")), ("排除主動式（附錄）", replace(base, active=False)),
                ("均量 ≥ 1,000 張（參考）", replace(base, liq_lots=1000))]
    for name, sp in variants:
        _, mr, tr_ = run(sp, [e for e in etfs if sp.active or not e.active])
        rob.append((name, mr, tr_))
    cont = continuous(etfs, base, cal, tr)
    write_reports(base, evs, main_rows, test_rows, rob, cont)
    json.dump({"base": base.__dict__, "events": len(evs), "robustness": [(n, m, t) for n, m, t in rob], "continuous": cont,
               "main": main_rows, "tests": test_rows}, (OUT / "study.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"事件 {len(evs)}：", {g: sum(e["group"] == g for e in evs) for g in GROUPS})
    print(md_table(main_rows, MAIN_COLS))
    print(md_table(test_rows, TEST_COLS))
    print("連續：", cont)
    for name, mr, t_ in rob:
        print("##", name, {g: max((x["events"] for x in mr if x["group"] == g), default=0) for g in GROUPS})
        print(md_table([x for x in t_ if x["window"] in (20, 60)], TEST_COLS))


if __name__ == "__main__":
    main()
