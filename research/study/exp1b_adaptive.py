"""痛點 1, a way out: the excess-R threshold set each day from that day's own noise instead of a fixed 0.4.

Each day: 2,000 random cross-industry pairs of the 300 most traded (exp2b's control) give the day's
distribution of excess R by chance; the threshold is its Q-th percentile (so the chance pass rate stays
about 1 − Q whatever the market does). Then N (找法1 官方產業 and 找法2) runs with that threshold. Compared
with the fixed 0.4 (exp1's default): the spread of the daily threshold, the number of groups a day, its
swing (cv) and zero days, and how both follow the market's move.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import common as C
from exp2_placebo import features

Q = 0.995
SAMPLE = 2000


def main() -> None:
    s = C.store()
    days = C.cached_days(s)[3:]
    all_days = C.cached_days(s)
    base = C.FinderParams(groupings=("industry",))
    r = C.rng()
    index = s.index_rows()
    rows_out = []
    for d in days:
        i = all_days.index(d)
        sessions = all_days[i - 2:i + 1]
        rows = [x for x in s.day_rows(d) if x.get("kind") == "stock" and (x.get("turnover_yuan") or 0) > 0]
        rows.sort(key=lambda x: -(x.get("turnover_yuan") or 0))
        top = rows[:300]
        cross = set()
        while len(cross) < SAMPLE:
            a, b = r.sample(top, 2)
            if a.get("industry") != b.get("industry"):
                cross.add(tuple(sorted((a["code"], b["code"]))))
        f = features(s, d, sessions, {c for pr in cross for c in pr}, base)
        rs = [C.pair_r(f[a].excess, f[b].excess) for a, b in cross if a in f and b in f and f[a].excess and f[b].excess]
        thr = C.quantile([x for x in rs if x is not None], Q)
        thr = round(max(0.2, min(0.8, thr)), 3) if thr is not None else base.excess_r
        known = len(C.run(s, d, "known", excess_r=thr)["groups"])
        new = len(C.run(s, d, "new", excess_r=thr)["groups"])
        row = index.get(d.isoformat(), {}).get("上市") or {}
        prev = (row.get("close") or 0) - (row.get("change") or 0)
        chg = round(row["change"] / prev * 100, 2) if prev and row.get("change") is not None else None
        rows_out.append({"date": d.isoformat(), "threshold": thr, "known": known, "new": new, "market": chg})
        print(d, f"thr={thr:.3f} known={known} new={new} 大盤={chg}", flush=True)
    Path(__file__).with_name("out").joinpath("exp1b.json").write_text(json.dumps(rows_out, ensure_ascii=False, indent=1), encoding="utf-8")
    fixed = json.loads(Path(__file__).with_name("out").joinpath("exp1.json").read_text(encoding="utf-8"))

    def summary(xs, label):
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
        mk = [abs(x["market"]) if x["market"] is not None else None for x in rows_out]
        ok = [(a, b) for a, b in zip(xs, mk) if b is not None]
        rr = C.pearson([a for a, _ in ok], [b for _, b in ok])
        print(f"{label:28s} mean {m:5.1f}  sd {sd:5.1f}  cv {sd / m if m else 0:4.2f}  zero {sum(1 for x in xs if x == 0):3d}  max {max(xs):3d}  r(|大盤|) {rr if rr is not None else float('nan'):+.2f}")

    ths = [x["threshold"] for x in rows_out]
    print(f"\nadaptive threshold: min {min(ths):.3f}  median {C.quantile(ths, .5):.3f}  max {max(ths):.3f}")
    summary(fixed["counts"]["known:default"], "fixed 0.4  找法1")
    summary([x["known"] for x in rows_out], f"adaptive p{Q * 100:g} 找法1")
    summary(fixed["counts"]["new:default"], "fixed 0.4  找法2")
    summary([x["new"] for x in rows_out], f"adaptive p{Q * 100:g} 找法2")


if __name__ == "__main__":
    main()
