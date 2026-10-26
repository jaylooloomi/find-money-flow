"""大神做法 5：罕見度加權 (−log p) instead of every 🧾 check counting one.

On the 15 days with cached 1-minute bars: the pairs N passes (pair_check at the defaults, 找法1 官方產業
candidates) and 1,000 random same-day pairs of the 300 most traded. For the checks that can be worked out
offline for any pair — 扣指數 R ≥ 0.4, 同漲跌 ≥ 75% (20 days), 同爆量 and 同分鐘 (1-minute), 法人同方向
— their rate among random pairs (p, the background) and among found pairs; weight −log p and lift
(found rate ÷ p). A check with a high lift and a low p is worth more than one vote; one with lift ≈ 1
tells nothing. (分點／持股／題材 need data only N's members have, so they are not in here.)
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date as _d
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import common as C  # noqa: E402
from m1_pca_rmt import day_data  # noqa: E402
from openmarket.history.evidence import minute_evidence  # noqa: E402
from openmarket.history.finder import daily_series  # noqa: E402


def checks(a, b, feats, series, mins, inst):
    out = {}
    if feats[a].excess and feats[b].excess:
        r = C.pair_r(feats[a].excess, feats[b].excess)
        out["扣指數"] = None if r is None else r >= 0.4
    sa, sb = series.get(a), series.get(b)
    days = [(x, y) for x, y in zip(sa or [], sb or []) if x is not None and y is not None]
    out["同漲跌"] = (sum((x > 0 and y > 0) or (x < 0 and y < 0) for x, y in days) / len(days) >= 0.75) if days else None
    mn = minute_evidence(mins.get(a), mins.get(b))
    out["同爆量"] = (mn["co"] >= 2 and mn["co"] >= 2 * max(mn["expected"], 0.5)) if mn and mn["expected"] is not None else None
    out["同分鐘"] = (mn["r0"] >= 0.3 and mn["r0"] - mn["r_off"] >= 0.1) if mn and mn["r0"] is not None and mn["r_off"] is not None else None
    ia, ib = inst.get(a), inst.get(b)
    out["法人同向"] = (ia > 0) == (ib > 0) if ia and ib else None
    return out


def main() -> None:
    s = C.store()
    with s._conn() as c:
        mdays = sorted({r["date"] for r in c.execute("SELECT date FROM yahoo_1m GROUP BY date HAVING COUNT(*) >= 200")})
    all_days = C.cached_days(s)
    r = C.rng()
    tally = {"found": {}, "random": {}}
    n_found = n_rand = 0
    for ds in mdays:
        d = _d.fromisoformat(ds)
        if d not in all_days or all_days.index(d) < 2:
            continue
        i = all_days.index(d)
        raw, feats, found = day_data(s, d, all_days[i - 2:i + 1])
        codes = list(raw)
        series = daily_series(s, d, 20)
        mins = {c: s.yahoo_1m(c, d) for c in codes}
        inst = s.inst_net([d]).get(d, {})
        rand = {tuple(sorted(r.sample(codes, 2))) for _ in range(1000)}
        for kind, pairs in (("found", found), ("random", rand)):
            for a, b in pairs:
                for k, v in checks(a, b, feats, series, mins, inst).items():
                    t = tally[kind].setdefault(k, [0, 0])
                    if v is not None:
                        t[0] += v
                        t[1] += 1
            if kind == "found":
                n_found += len(pairs)
            else:
                n_rand += len(pairs)
        print(ds, len(found), flush=True)
    print(f"\n{n_found} found pairs, {n_rand} random pairs")
    print("check      random p    found rate   lift    weight −log p")
    out = {}
    for k in tally["random"]:
        pr = tally["random"][k][0] / tally["random"][k][1] if tally["random"][k][1] else None
        fr = tally["found"].get(k, [0, 0])
        fr = fr[0] / fr[1] if fr[1] else None
        w = -math.log(pr) if pr else None
        lift = fr / pr if pr and fr is not None else None
        out[k] = {"p_random": pr, "found_rate": fr, "lift": lift, "weight": w}
        print(f"{k:8s}  {pr if pr is not None else float('nan'):8.1%}   {fr if fr is not None else float('nan'):8.1%}   {lift if lift else float('nan'):5.1f}   {w if w else float('nan'):5.2f}")
    Path(__file__).with_name("out").mkdir(exist_ok=True)
    Path(__file__).with_name("out").joinpath("m5.json").write_text(json.dumps({"n_found": n_found, "n_random": n_rand, "checks": out}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
