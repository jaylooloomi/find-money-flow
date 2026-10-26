"""痛點 2, the harder control: same-day coincidence.

exp2's placebo moves B to other sessions, which also removes what a shared day does (the market, a
headline): it measures pure noise only. Here the control is pairs of the SAME day from DIFFERENT
industries that never went through the daily-R stage (random, 2,000 a day among the 300 most traded):
the share of them the intraday stage passes is the same-day coincidence rate, set against the found rate
of the real candidates (who share an industry and passed the daily R).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import common as C
from exp2_placebo import features
from openmarket.history.finder import daily_series, known_candidates, pair_check

SAMPLE = 2000


def main() -> None:
    s = C.store()
    days = C.cached_days(s)
    base = C.FinderParams(groupings=("industry",))
    settings = {"default (0.4)": base, "excess_r=0.5": replace(base, excess_r=0.5), "excess_r=0.6": replace(base, excess_r=0.6)}
    res = {k: {"cand": 0, "cand_pass": 0, "cross": 0, "cross_pass": 0} for k in settings}
    r = C.rng()
    for i in range(3, len(days)):
        d = days[i]
        sessions = days[i - 2:i + 1]
        rows = [x for x in s.day_rows(d) if x.get("kind") == "stock" and (x.get("turnover_yuan") or 0) > 0]
        rows.sort(key=lambda x: -(x.get("turnover_yuan") or 0))
        top = rows[:300]
        series = daily_series(s, d, base.daily_window)
        cands = known_candidates(rows, {"industry": C.industry_grouping}, series, base)
        cand_pairs = {tuple(sorted((a, b))) for c in cands for a in c["members"] for b in c["members"] if a < b}
        cross = set()
        while len(cross) < SAMPLE:
            a, b = r.sample(top, 2)
            if a.get("industry") != b.get("industry"):
                cross.add(tuple(sorted((a["code"], b["code"]))))
        codes = {x for pr in cand_pairs | cross for x in pr}
        f = features(s, d, sessions, codes, base)
        for name, p in settings.items():
            for key, pairs in (("cand", cand_pairs), ("cross", cross)):
                for a, b in pairs:
                    if a in f and b in f:
                        res[name][key] += 1
                        res[name][key + "_pass"] += pair_check(f[a], f[b], p)["ok"]
        print(d, len(cand_pairs), flush=True)
    print("\nsetting           candidates pass       same-day cross-industry random pass")
    for name, x in res.items():
        print(f"{name:16s} {x['cand_pass']:5d}/{x['cand']:6d} {x['cand_pass'] / max(1, x['cand']):6.2%}    {x['cross_pass']:5d}/{x['cross']:6d} {x['cross_pass'] / max(1, x['cross']):6.2%}")
    Path(__file__).with_name("out").joinpath("exp2b.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
