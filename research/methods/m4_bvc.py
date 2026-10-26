"""大神做法 4：BVC (Easley, López de Prado & O'Hara 2012) instead of today's CLV estimate of 外盤.

On the 15 days with cached 1-minute bars, for the 300 most traded stocks each day, the day's buy share
  CLV — today's: each bar's volume × (close − low) / (high − low); a flat bar by its move from the bar before
  BVC — each bar's volume × Φ(ΔP / σ_ΔP), ΔP = close − the bar before's close, σ_ΔP the sd of the day's ΔP
How close they are (correlation, mean gap), and how they differ on the days that matter: limit-up days
(the stock locked) and big down days. No 逐筆內外盤 is published, so there is no ground truth here:
this shows where the two disagree, not which one is right.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import common as C  # noqa: E402
import linalg as L  # noqa: E402


def clv(bars, prev):
    buy = allv = 0.0
    before = prev
    for b in bars:
        h, l, c, v = b[1], b[2], b[3], b[5] if len(b) > 5 else None
        if v:
            w = (c - l) / (h - l) if h > l else (1 if c > before else 0 if c < before else 0.5)
            buy += v * w
            allv += v
        before = c
    return buy / allv * 100 if allv else None


def bvc(bars, prev):
    closes = [prev] + [b[3] for b in bars]
    dps = [closes[i + 1] - closes[i] for i in range(len(bars))]
    m = sum(dps) / len(dps) if dps else 0
    sd = math.sqrt(sum((x - m) ** 2 for x in dps) / len(dps)) if dps else 0
    if not sd:
        return None
    buy = allv = 0.0
    for b, dp in zip(bars, dps):
        v = b[5] if len(b) > 5 else None
        if v:
            buy += v * L.norm_cdf(dp / sd)
            allv += v
    return buy / allv * 100 if allv else None


def main() -> None:
    s = C.store()
    with s._conn() as c:
        days = sorted({r["date"] for r in c.execute("SELECT date FROM yahoo_1m GROUP BY date HAVING COUNT(*) >= 200")})
    pairs, locked, down = [], [], []
    from datetime import date as _d
    for ds in days:
        d = _d.fromisoformat(ds)
        rows = [x for x in s.day_rows(d) if x.get("kind") == "stock" and x.get("prev_close")]
        rows.sort(key=lambda x: -(x.get("turnover_yuan") or 0))
        for r in rows[:300]:
            bars = s.yahoo_1m(r["code"], d)
            if not bars:
                continue
            a, b = clv(bars, r["prev_close"]), bvc(bars, r["prev_close"])
            if a is None or b is None:
                continue
            pairs.append((a, b))
            if (r.get("change_pct") or 0) >= 9.4:
                locked.append((a, b))
            elif (r.get("change_pct") or 0) <= -5:
                down.append((a, b))
    gap = [b - a for a, b in pairs]
    out = {
        "stock_days": len(pairs), "corr": C.pearson([a for a, _ in pairs], [b for _, b in pairs]),
        "mean_clv": C.mean([a for a, _ in pairs]), "mean_bvc": C.mean([b for _, b in pairs]), "mean_gap_bvc_minus_clv": C.mean(gap),
        "abs_gap_p90": C.quantile([abs(x) for x in gap], 0.9),
        "limit_up_days": {"n": len(locked), "clv": C.mean([a for a, _ in locked]), "bvc": C.mean([b for _, b in locked])},
        "down_5pct_days": {"n": len(down), "clv": C.mean([a for a, _ in down]), "bvc": C.mean([b for _, b in down])},
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    Path(__file__).with_name("out").mkdir(exist_ok=True)
    Path(__file__).with_name("out").joinpath("m4.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
