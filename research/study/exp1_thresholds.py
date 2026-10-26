"""痛點 1 魔法數字：fixed thresholds across 56 trading days — how much the number of groups swings, and
with what (the market's move and its intraday range).

Grid (one threshold changed at a time from the defaults): 扣指數後 R 0.3 / 0.4 / 0.5, 高點差 5 / 8 / 15 分,
日 R 0.7 / 0.8 / 0.85. 找法1 uses 官方產業 only (the other groupings need their loaders). Output:
research/out/exp1.json and a summary on stdout.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import common as C

GRID = {
    "default": {},
    "excess_r=0.3": {"excess_r": 0.3},
    "excess_r=0.5": {"excess_r": 0.5},
    "peak_gap=5": {"peak_gap": 5},
    "peak_gap=15": {"peak_gap": 15},
    "daily_r=0.7": {"daily_r": 0.7, "new_daily_r": 0.7},
    "daily_r=0.85": {"daily_r": 0.85, "new_daily_r": 0.85},
}


def main() -> None:
    s = C.store()
    days = C.cached_days(s)[3:]  # three cached sessions behind each day
    index = s.index_rows()
    out = {"days": [d.isoformat() for d in days], "market": {}, "counts": {}}
    for d in days:
        row = index.get(d.isoformat(), {}).get("上市") or {}
        prev = (row.get("close") or 0) - (row.get("change") or 0)
        out["market"][d.isoformat()] = {
            "chg": round(row["change"] / prev * 100, 2) if prev and row.get("change") is not None else None,
            "range": round((row["high"] - row["low"]) / prev * 100, 2) if prev and row.get("high") and row.get("low") else None,
        }
    t0 = time.time()
    for name, params in GRID.items():
        for method in ("known", "new"):
            key = f"{method}:{name}"
            out["counts"][key] = []
            for d in days:
                r = C.run(s, d, method, **params)
                out["counts"][key].append(len(r["groups"]))
            print(f"{key:24s} done ({time.time() - t0:.0f}s)", flush=True)
    Path(__file__).with_name("out").mkdir(exist_ok=True)
    Path(__file__).with_name("out").joinpath("exp1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    chg = [out["market"][d]["chg"] for d in out["days"]]
    rng = [out["market"][d]["range"] for d in out["days"]]
    print("\nmethod:setting            mean   sd    cv   zero  max   r(|大盤漲跌|)  r(大盤振幅)")
    for key, xs in out["counts"].items():
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
        ok = [(x, abs(c), g) for x, c, g in zip(xs, chg, rng) if c is not None and g is not None]
        r1 = C.pearson([a for a, _, _ in ok], [b for _, b, _ in ok])
        r2 = C.pearson([a for a, _, _ in ok], [g for _, _, g in ok])
        print(f"{key:24s} {m:6.1f} {sd:5.1f} {sd / m if m else 0:5.2f} {sum(1 for x in xs if x == 0):4d} {max(xs):4d}   {r1 if r1 is not None else float('nan'):6.2f}        {r2 if r2 is not None else float('nan'):6.2f}")


if __name__ == "__main__":
    main()
