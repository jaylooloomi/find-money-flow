"""Tightening 同爆量 and 籌碼 (the user's call, 2026-10-25): their rate among random same-day pairs and
among N's pairs under a few rules, on the 15 days with 1-minute bars."""

from __future__ import annotations

import sys
from datetime import date as _d
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402
from m1_pca_rmt import day_data  # noqa: E402
from openmarket.history.evidence import minute_evidence  # noqa: E402

BURST_RULES = {"now: co≥2 & ≥2×": (2, 2.0), "co≥3 & ≥3×": (3, 3.0), "co≥3 & ≥4×": (3, 4.0), "co≥4 & ≥4×": (4, 4.0), "co≥5 & ≥5×": (5, 5.0)}


def main() -> None:
    s = C.store()
    with s._conn() as c:
        mdays = sorted({r["date"] for r in c.execute("SELECT date FROM yahoo_1m GROUP BY date HAVING COUNT(*) >= 200")})
    all_days = C.cached_days(s)
    r = C.rng()
    burst = {k: {"found": [0, 0], "random": [0, 0]} for k in BURST_RULES}
    chips = {k: {"found": [0, 0], "random": [0, 0]} for k in ("trust≥3 days (5)", "trust≥3 days (20)", "today 法人 same")}
    for ds in mdays:
        d = _d.fromisoformat(ds)
        if d not in all_days or all_days.index(d) < 20:
            continue
        i = all_days.index(d)
        raw, feats, found = day_data(s, d, all_days[i - 2:i + 1])
        codes = list(raw)
        mins = {c: s.yahoo_1m(c, d) for c in codes}
        rand = {tuple(sorted(r.sample(codes, 2))) for _ in range(1000)}
        trust5 = s.inst_trust(all_days[i - 4:i + 1], codes)
        trust20 = s.inst_trust(all_days[i - 19:i + 1], codes)
        inst = s.inst_net([d]).get(d, {})
        for kind, pairs in (("found", found), ("random", rand)):
            for a, b in pairs:
                mn = minute_evidence(mins.get(a), mins.get(b))
                if mn and mn["expected"] is not None:
                    for k, (n, x) in BURST_RULES.items():
                        burst[k][kind][1] += 1
                        burst[k][kind][0] += mn["co"] >= n and mn["co"] >= x * max(mn["expected"], 0.5)
                for k, tr in (("trust≥3 days (5)", trust5), ("trust≥3 days (20)", trust20)):
                    both = sum(1 for day in tr.values() if day.get(a) and day.get(b) and (day[a] > 0) == (day[b] > 0))
                    chips[k][kind][1] += 1
                    chips[k][kind][0] += both >= 3
                ia, ib = inst.get(a), inst.get(b)
                if ia and ib:
                    chips["today 法人 same"][kind][1] += 1
                    chips["today 法人 same"][kind][0] += (ia > 0) == (ib > 0)
        print(ds, len(found), flush=True)
    for name, table in (("同爆量", burst), ("籌碼", chips)):
        print(f"\n{name}: rule  random rate  found rate  lift")
        for k, v in table.items():
            pr = v["random"][0] / v["random"][1] if v["random"][1] else float("nan")
            fr = v["found"][0] / v["found"][1] if v["found"][1] else float("nan")
            print(f"  {k:22s} {pr:7.1%}   {fr:7.1%}   {fr / pr if pr else float('nan'):5.1f}")


if __name__ == "__main__":
    main()
