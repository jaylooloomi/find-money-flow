"""Step 1 cross-checks on data/raw/etf/ (nothing is changed or filled in):

1. MoneyDJ's 市價 × split factor against FinMind's raw close, every day of every ETF
   (both are the TWSE close; a mismatch means the 淨值 rows are misaligned too).
2. Each FinMind cash dividend against the 淨值 drop on its ex-date: is MoneyDJ's 淨值 a plain one
   (drops by the dividend, so we add the dividend back) or already adjusted?
3. The ETFs without a 淨值: do they trade at all?

Run: .venv/Scripts/python research/etf_premium/validate.py          (1-3)
     .venv/Scripts/python research/etf_premium/validate.py --mops   (4, after fetch_mops.py)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "etf"


def read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    codes = sorted(p.stem for p in (RAW / "nav").glob("*.csv"))
    bad_px, checked, div_rows = [], 0, []
    for code in codes:
        nav = {r["date"]: r for r in read(RAW / "nav" / f"{code}.csv")}
        px = {r["date"].replace("-", ""): r for r in read(RAW / "price" / f"{code}.csv")}
        if not nav:
            p = list(px.values())
            print(f"無淨值 {code}: 價格 {len(p)} 天，最後 {p[-1]['date'] if p else '—'}，近 60 日均量 {sum(float(r['Trading_Volume']) for r in p[-60:]) / 1000 / max(1, len(p[-60:])):.0f} 張")
            continue
        days = sorted(nav)
        # the split factor: the most common raw/adjusted ratio per segment; checked day by day below
        for d in days:
            r = px.get(d)
            if not r or float(r["Trading_Volume"] or 0) == 0:
                continue
            adj, close = float(nav[d]["price_adj"]), float(r["close"])
            f = round(close / adj) if adj else 0
            checked += 1
            if not f or abs(close - adj * f) > 0.011 * max(1, f):
                bad_px.append((code, d, close, adj))
        pos = {d: i for i, d in enumerate(days)}
        for r in read(RAW / "dividend" / f"{code}.csv"):
            cash, ex = float(r.get("CashEarningsDistribution") or 0), (r.get("CashExDividendTradingDate") or "").replace("-", "")
            if cash <= 0 or ex not in pos or pos[ex] == 0:
                if cash > 0:
                    div_rows.append((code, ex, cash, None, None))
                continue
            prev = days[pos[ex] - 1]
            close_prev = float(px[prev]["close"]) if prev in px else None
            f = round(close_prev / float(nav[prev]["price_adj"])) if close_prev else 1
            drop = (float(nav[prev]["nav_adj"]) - float(nav[ex]["nav_adj"])) * f
            div_rows.append((code, ex, cash, round(drop, 3), round(float(nav[prev]["nav_adj"]) * f, 2)))
    print(f"市價比對：{checked} 個 ETF-交易日，不一致 {len(bad_px)}：{bad_px[:10]}")
    ok = [x for x in div_rows if x[3] is not None]
    miss = [x for x in div_rows if x[3] is None]
    # a plain 淨值 drops by about the dividend (± the day's market move, ~ 1-2% of 淨值)
    near = [x for x in ok if abs(x[3] - x[2]) <= 0.02 * x[4] + 0.05]
    print(f"配息 {len(div_rows)} 筆；除息日有淨值 {len(ok)}，淨值跌幅 ≈ 配息（±2% 淨值）{len(near)}；除息日不在淨值資料 {len(miss)}：{miss[:8]}")
    for x in ok[:6] + [x for x in ok if x not in near][:8]:
        print("  ", x)


if __name__ == "__main__" and "--mops" not in sys.argv:
    main()


def mops_check() -> None:
    """4. The official 淨值／收盤 (公開資訊觀測站) against MoneyDJ's 淨值 and FinMind's close, every ETF-day both have."""
    sys.stdout.reconfigure(encoding="utf-8")
    off: dict[tuple[str, str], tuple[str, str]] = {}
    for sub in ("mops", "mops_active"):
        for p in sorted((RAW / sub).glob("*.csv")):
            for r in read(p):
                if r["nav_date"] == p.stem:
                    off[(r["code"], p.stem)] = (r["nav"], r["close"])
    days = sorted({d for _, d in off})
    print(f"官方淨值：{len(days)} 天（{days[0] if days else '—'}～{days[-1] if days else '—'}），{len({c for c, _ in off})} 檔，{len(off)} 筆")
    nav_n = nav_bad = px_n = px_bad = 0
    bad = []
    for code in sorted({c for c, _ in off}):
        mdj = {r["date"]: r for r in read(RAW / "nav" / f"{code}.csv")}
        fm = {r["date"].replace("-", ""): r for r in read(RAW / "price" / f"{code}.csv")}
        for (c, d), (nav, close) in off.items():
            if c != code:
                continue
            nav = float(nav)
            m = mdj.get(d)
            f = fm.get(d)
            if m and f and float(f["close"] or 0) and float(m["price_adj"] or 0):
                k = round(float(f["close"]) / float(m["price_adj"]))  # the split factor that day
                nav_n += 1
                if abs(float(m["nav_adj"]) * k - nav) > 0.006 * max(1, k) + 1e-9:
                    nav_bad += 1
                    bad.append((code, d, nav, round(float(m["nav_adj"]) * k, 4)))
            if f and float(f["Trading_Volume"] or 0) and close not in ("", "--", "-"):
                px_n += 1
                if abs(float(f["close"]) - float(close)) > 0.0051:
                    px_bad += 1
    print(f"官方淨值 vs MoneyDJ：{nav_n} 筆比對，差 > 0.005 元的 {nav_bad} 筆：{bad[:10]}")
    print(f"官方收盤 vs FinMind：{px_n} 筆比對，不一致 {px_bad} 筆")


if __name__ == "__main__" and "--mops" in sys.argv:
    mops_check()
