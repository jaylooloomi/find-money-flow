"""Step 1 report: what data/raw/etf/ actually holds, per ETF — nothing is filled in.

For each candidate: 淨值 days and range, trading days without a 淨值 (gaps), the split factor between FinMind's
raw price and MoneyDJ's adjusted one (must be one constant per segment), dividends, and the 60-day average
volume today. Writes results/coverage.csv and prints a summary.

Run: .venv/Scripts/python research/etf_premium/coverage.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE.parents[1] / "data" / "raw" / "etf"
OUT = HERE / "results"
DOMESTIC_TYPES = {"國內成分證券指數股票型基金", "國內成分證券主動式交易所交易基金(股票)"}


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def factors(prices: dict[str, float], adj: dict[str, float]) -> list[tuple[str, float]]:
    """[(first day, raw ÷ adjusted)] segments; a split shows as a new segment."""
    out: list[tuple[str, float]] = []
    for d in sorted(set(prices) & set(adj)):
        if not adj[d]:
            continue
        f = round(prices[d] / adj[d], 2)
        if not out or abs(f - out[-1][1]) > 0.02 * out[-1][1]:
            out.append((d, f))
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    uni = json.loads((RAW / "universe.json").read_text(encoding="utf-8"))
    rows = []
    for x in uni:
        if x["基金類型"] not in DOMESTIC_TYPES:
            continue
        code = x["基金代號"]
        nav = {r["date"]: float(r["nav_adj"]) for r in read(RAW / "nav" / f"{code}.csv") if r["nav_adj"] not in ("", "0", "0.0000")}
        adj = {r["date"]: float(r["price_adj"]) for r in read(RAW / "nav" / f"{code}.csv") if r["price_adj"] not in ("", "0", "0.0000")}
        px = read(RAW / "price" / f"{code}.csv")
        trade = {r["date"].replace("-", ""): float(r["close"]) for r in px if float(r.get("Trading_Volume") or 0) > 0 and float(r["close"] or 0) > 0}
        vols = [float(r["Trading_Volume"]) / 1000 for r in px][-60:]
        div = [r for r in read(RAW / "dividend" / f"{code}.csv") if float(r.get("CashEarningsDistribution") or 0) > 0]
        missing = sorted(d for d in trade if d not in nav)
        segs = factors(trade, adj)
        rows.append({
            "code": code, "name": x["基金簡稱"], "active": code.endswith("A"), "listed": x["上市日期"],
            "nav_days": len(nav), "nav_first": min(nav, default=""), "nav_last": max(nav, default=""),
            "trade_days": len(trade), "trade_days_without_nav": len(missing), "missing_sample": " ".join(missing[:5]),
            "split_segments": " ".join(f"{d}:{f}" for d, f in segs), "dividends": len(div),
            "avg_vol_60d_lots": round(sum(vols) / len(vols)) if vols else 0,
        })
    OUT.mkdir(exist_ok=True)
    with (OUT / "coverage.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    no_nav = [r for r in rows if not r["nav_days"]]
    gaps = [r for r in rows if r["nav_days"] and r["trade_days_without_nav"]]
    odd = [r for r in rows if len(r["split_segments"].split()) > 1]
    liquid = [r for r in rows if r["avg_vol_60d_lots"] >= 5000]
    print(f"候選 {len(rows)} 檔（主動式 {sum(r['active'] for r in rows)}）")
    print(f"抓不到淨值 {len(no_nav)}：{[r['code'] for r in no_nav]}")
    print(f"有交易日缺淨值 {len(gaps)}：{[(r['code'], r['trade_days_without_nav'], r['missing_sample']) for r in gaps]}")
    print(f"市價÷調整價 有分段（分割）{len(odd)}：{[(r['code'], r['split_segments']) for r in odd]}")
    print(f"今天近 60 日均量 ≥ 5000 張：{len(liquid)}：{[r['code'] for r in liquid]}")


if __name__ == "__main__":
    main()
