"""痛點 3 扣指數：does it matter that each stock gets its own β (today's code) rather than β = 1
(plain subtraction, the review's reading)?

Per day (the last 15 cached): the 200 most traded stocks; each one's β to its factor (factors.factor_of:
類股／大盤／櫃買), the R of random cross-industry pairs and of the 找法1 candidate pairs under
  est-β  — r − β·m (finder.add_excess, what N uses)
  β=1    — r − m
Lower random R with the found R kept = the cleaner way to take the index out.
"""

from __future__ import annotations

from pathlib import Path

import common as C
from exp2_placebo import features
from openmarket.history.evidence import beta
from openmarket.history.factors import MARKET_FACTORS, factor_of, industry_sizes, sector_returns
from openmarket.history.finder import daily_series, known_candidates


def main() -> None:
    s = C.store()
    days = C.cached_days(s)[-15:]
    all_days = C.cached_days(s)
    base = C.FinderParams(groupings=("industry",))
    r = C.rng()
    betas, out = [], {"est": {"rand": [], "cand": []}, "one": {"rand": [], "cand": []}}
    for d in days:
        i = all_days.index(d)
        sessions = all_days[i - 2:i + 1]
        rows = [x for x in s.day_rows(d) if x.get("kind") == "stock" and (x.get("turnover_yuan") or 0) > 0]
        rows.sort(key=lambda x: -(x.get("turnover_yuan") or 0))
        by_code = {x["code"]: x for x in rows}
        top = rows[:200]
        series = daily_series(s, d, base.daily_window)
        cands = known_candidates(rows, {"industry": C.industry_grouping}, series, base)
        cand = {tuple(sorted((a, b))) for c in cands for a in c["members"] for b in c["members"] if a < b}
        rand = set()
        while len(rand) < 1500:
            a, b = r.sample(top, 2)
            if a.get("industry") != b.get("industry"):
                rand.add(tuple(sorted((a["code"], b["code"]))))
        codes = {x for pr in cand | rand for x in pr}
        f = features(s, d, sessions, codes, base)
        sizes = industry_sizes(rows)
        sectors = s.sector_intraday(sessions)
        names = set.intersection(*(set(sectors.get(x, {})) for x in sessions)) if all(x in sectors for x in sessions) else set()
        minus_one = {}
        for c in codes:
            if c not in f or c not in by_code:
                continue
            fac = factor_of(by_code[c], sizes, names)
            if fac is None:
                continue
            key = fac[0]
            m = f[key].returns if key in {k for k, _ in MARKET_FACTORS.values()} and key in f else sector_returns(sectors, sessions, key)
            if not m or len(m) != len(f[c].returns):
                continue
            b = beta(f[c].returns, m)
            if b is not None:
                betas.append(b)
            minus_one[c] = [x - y if x is not None and y is not None else None for x, y in zip(f[c].returns, m)]
        for kind, pairs in (("rand", rand), ("cand", cand)):
            for a, b in pairs:
                if a in f and b in f and f[a].excess and f[b].excess:
                    out["est"][kind].append(C.pair_r(f[a].excess, f[b].excess))
                if a in minus_one and b in minus_one:
                    out["one"][kind].append(C.pair_r(minus_one[a], minus_one[b]))
        print(d, flush=True)
    bs = sorted(betas)
    print(f"\nβ of {len(bs)} stock-days: p10 {C.quantile(bs, .1):.2f}  median {C.quantile(bs, .5):.2f}  p90 {C.quantile(bs, .9):.2f}  (β = 1 would be every one)")
    for name, label in (("est", "estimated β (now)"), ("one", "β = 1 (plain minus)")):
        rr = [x for x in out[name]["rand"] if x is not None]
        cc = [x for x in out[name]["cand"] if x is not None]
        print(f"{label:22s} random cross-industry R mean {C.mean(rr):+.3f} p95 {C.quantile(rr, .95):+.3f} | candidates R mean {C.mean(cc):+.3f}  share ≥ 0.4 {sum(1 for x in cc if x >= 0.4) / len(cc):.1%}")
    Path(__file__).with_name("out").joinpath("exp3.txt").write_text(str({k: {kk: len(v) for kk, v in vv.items()} for k, vv in out.items()}), encoding="utf-8")


if __name__ == "__main__":
    main()
