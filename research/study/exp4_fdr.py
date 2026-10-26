"""痛點 2, a way out: control the false discovery rate over all the pairs tested in a day (Benjamini–
Hochberg) instead of one fixed R for every pair.

Each day, for the 找法1 (官方產業) candidate pairs (they passed the daily R): the excess R, its one-sided
p-value from the Fisher z with the effective sample size n_eff = n·(1 − ρ₁ρ₂)/(1 + ρ₁ρ₂) (ρ = each
series' lag-1 autocorrelation; 5-minute returns are not independent), then BH at q = 5% and 1% across the
day's pairs. Compared with the fixed excess R ≥ 0.4: how many pairs each lets through, the R the BH cut
lands on (it moves with the day and with how many pairs were tested), and the overlap.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import common as C
from exp2_placebo import features
from openmarket.history.finder import daily_series, known_candidates


def lag1(x):
    pts = [(a, b) for a, b in zip(x, x[1:]) if a is not None and b is not None]
    return C.pearson([a for a, _ in pts], [b for _, b in pts]) or 0.0 if len(pts) > 10 else 0.0


def p_value(r: float, n_eff: float) -> float:
    """One-sided P(R ≥ r) under no correlation, Fisher z."""
    if n_eff <= 3:
        return 1.0
    z = math.atanh(max(-0.999999, min(0.999999, r))) * math.sqrt(n_eff - 3)
    return 0.5 * math.erfc(z / math.sqrt(2))


def bh(ps: list[float], q: float) -> float | None:
    """The largest p that BH accepts at level q (None: none)."""
    m = len(ps)
    cut = None
    for k, p in enumerate(sorted(ps), 1):
        if p <= q * k / m:
            cut = p
    return cut


def main() -> None:
    s = C.store()
    all_days = C.cached_days(s)
    base = C.FinderParams(groupings=("industry",))
    tot = {"tested": 0, "fixed": 0, "bh5": 0, "bh1": 0, "both5": 0}
    cuts5 = []
    for i in range(3, len(all_days)):
        d = all_days[i]
        sessions = all_days[i - 2:i + 1]
        rows = [x for x in s.day_rows(d) if x.get("kind") == "stock"]
        series = daily_series(s, d, base.daily_window)
        cands = known_candidates(rows, {"industry": C.industry_grouping}, series, base)
        pairs = {tuple(sorted((a, b))) for c in cands for a in c["members"] for b in c["members"] if a < b}
        if not pairs:
            continue
        f = features(s, d, sessions, {x for pr in pairs for x in pr}, base)
        rec = []
        for a, b in pairs:
            if a in f and b in f and f[a].excess and f[b].excess:
                xa, xb = f[a].excess, f[b].excess
                pts = [(x, y) for x, y in zip(xa, xb) if x is not None and y is not None]
                if len(pts) < 20:
                    continue
                r = C.pearson([x for x, _ in pts], [y for _, y in pts])
                if r is None:
                    continue
                ra, rb = lag1(xa), lag1(xb)
                n_eff = len(pts) * (1 - ra * rb) / (1 + ra * rb)
                rec.append((r, p_value(r, n_eff)))
        if not rec:
            continue
        ps = [p for _, p in rec]
        c5, c1 = bh(ps, 0.05), bh(ps, 0.01)
        fixed = {k for k, (r, _) in enumerate(rec) if r >= 0.4}
        b5 = {k for k, (_, p) in enumerate(rec) if c5 is not None and p <= c5}
        b1 = {k for k, (_, p) in enumerate(rec) if c1 is not None and p <= c1}
        tot["tested"] += len(rec)
        tot["fixed"] += len(fixed)
        tot["bh5"] += len(b5)
        tot["bh1"] += len(b1)
        tot["both5"] += len(fixed & b5)
        if b5:
            cuts5.append(min(rec[k][0] for k in b5))
        print(d, f"tested {len(rec)} fixed≥0.4 {len(fixed)} BH5% {len(b5)} (R cut {min(rec[k][0] for k in b5) if b5 else float('nan'):.2f}) BH1% {len(b1)}", flush=True)
    print("\n", tot)
    print(f"BH 5% R cut over the days: min {min(cuts5):.2f} median {C.quantile(cuts5, .5):.2f} max {max(cuts5):.2f}")
    print(f"of the fixed-0.4 passes, {tot['both5'] / tot['fixed']:.0%} also pass BH 5%; BH 5% adds {tot['bh5'] - tot['both5']} pairs below 0.4")
    Path(__file__).with_name("out").joinpath("exp4.json").write_text(json.dumps({"totals": tot, "bh5_cuts": cuts5}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
