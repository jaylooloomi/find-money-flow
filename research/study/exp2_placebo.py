"""痛點 2 多重檢定／偽陽性：how often the intraday stage passes a pair that cannot be in sync.

For each day, the 找法1 (官方產業) candidate pairs — those that already passed the daily R — go through
pair_check twice: as they are (A and B on the same 3 sessions), and as a placebo with B's bars taken
from the 3 sessions ending LAG trading days earlier (its own peak, its own index). The placebo pass rate is
the chance a pair passes with no common money at all; set against the real pass rate it estimates the
share of found pairs that are chance (an FDR estimate), at the current thresholds and at a few others.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import common as C
from openmarket.history.finder import add_excess, daily_series, intraday_features, known_candidates, pair_check
from openmarket.history.linkage import align_bars

LAG = 5  # trading days the placebo's B is moved back


def features(s, d, sessions, codes, p):
    rows = {r["code"]: r for r in s.day_rows(d) if r.get("kind") == "stock"}
    session_rows = {x: {r["code"]: r for r in rs} for x, rs in s.rows(sessions).items()}
    feats = {}
    for c in list(codes) + list(C.INDEXES):
        bars = s.yahoo_bars(c, sessions)
        if not bars.get(sessions[-1]):
            continue
        aligned = {x: align_bars(b, session_rows.get(x, {}).get(c, {})) for x, b in bars.items()}
        feats[c] = intraday_features(aligned, {x: session_rows.get(x, {}).get(c, {}) for x in sessions}, d, p, 5)
    add_excess(feats, rows, list(rows.values()), sessions, s.sector_intraday(sessions), "sector")
    return feats


def main() -> None:
    s = C.store()
    days = C.cached_days(s)
    base = C.FinderParams(groupings=("industry",))
    settings = {"default (0.4)": base, "excess_r=0.5": replace(base, excess_r=0.5), "excess_r=0.6": replace(base, excess_r=0.6),
                "excess 0.4 + peak 5": replace(base, peak_gap=5)}
    res = {k: {"real_pairs": 0, "real_pass": 0, "placebo_pairs": 0, "placebo_pass": 0} for k in settings}
    used = 0
    for i in range(LAG + 3, len(days)):
        d, back = days[i], days[i - LAG]
        sessions = days[i - 2:i + 1]
        b_sessions = days[i - LAG - 2:i - LAG + 1]
        rows = [r for r in s.day_rows(d) if r.get("kind") == "stock"]
        series = daily_series(s, d, base.daily_window)
        cands = known_candidates(rows, {"industry": C.industry_grouping}, series, base)
        pairs = {tuple(sorted((a, b))) for c in cands for a in c["members"] for b in c["members"] if a < b}
        if not pairs:
            continue
        codes = {x for pr in pairs for x in pr}
        fa = features(s, d, sessions, codes, base)
        fb = features(s, back, b_sessions, codes, base)
        used += 1
        for name, p in settings.items():
            for a, b in pairs:
                if a in fa and b in fa:
                    res[name]["real_pairs"] += 1
                    res[name]["real_pass"] += pair_check(fa[a], fa[b], p)["ok"]
                if a in fa and b in fb:
                    res[name]["placebo_pairs"] += 1
                    res[name]["placebo_pass"] += pair_check(fa[a], fb[b], p)["ok"]
        print(d, len(pairs), "pairs", flush=True)
    print(f"\n{used} days; B moved back {LAG} trading days for the placebo")
    print("setting                 real pass        placebo pass     est. chance share of found pairs")
    for name, r in res.items():
        rp = r["real_pass"] / r["real_pairs"] if r["real_pairs"] else 0
        pp = r["placebo_pass"] / r["placebo_pairs"] if r["placebo_pairs"] else 0
        print(f"{name:22s} {r['real_pass']:5d}/{r['real_pairs']:5d} {rp:6.1%}   {r['placebo_pass']:5d}/{r['placebo_pairs']:5d} {pp:6.1%}   {min(1, pp / rp) if rp else float('nan'):6.1%}")
    Path(__file__).with_name("out").mkdir(exist_ok=True)
    Path(__file__).with_name("out").joinpath("exp2.json").write_text(json.dumps({"lag": LAG, "days": used, "results": res}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
