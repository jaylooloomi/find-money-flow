"""Does evidence predict follow-through? N's stored groups (default 條件, both methods, ≥ v9, 60 days) and
their members' returns 1, 3 and 5 trading days later, less 大盤 over the same span:
  - by the number of ✓ among the server's 5 checks (≥4 / 2–3 / ≤1)
  - by each check alone (✓ against not ✓)
  - 🔥 (members' mean 當沖比 ≥ 50%) against the rest
with a bootstrap 90% interval on the mean (resampling groups, 2,000 draws), so a gap can be told from noise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("D:/git/open-market")  # the main checkout: tonight's store.finder_days / linkage helpers live there
sys.path.insert(0, str(ROOT / "src"))

import random  # noqa: E402

from openmarket.history.finder import FinderParams  # noqa: E402
from openmarket.history.linkage import EVIDENCE_CHECKS, daytrade_group, daytrade_ratios, evidence_yes  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402

HORIZONS = (1, 3, 5)


def main() -> None:
    s = HistoryStore(ROOT / "data/history.sqlite")
    p = FinderParams()
    days = sorted(s.trading_dates(limit=400))
    pos = {d: i for i, d in enumerate(days)}
    key = p.key() + "|" + ",".join(sorted(p.groupings))
    groups = {}  # (day, members) → group (the one with more ✓ of the two methods)
    for m in ("known", "new"):
        for d, res in s.finder_days(m, key, any_version=True, min_version=9, groupings=p.groupings):
            for g in res.get("groups", []):
                k = (d, tuple(sorted(x["code"] for x in g["members"])))
                if k not in groups or evidence_yes(g) > evidence_yes(groups[k]):
                    groups[k] = g
    codes = sorted({c for _, ms in groups for c in ms})
    closes = {}
    for d in days:
        for r in s.day_rows(d):
            if r["code"] in codes and r.get("close"):
                closes.setdefault(r["code"], {})[d] = r["close"]
    index = s.index_rows()
    idx = {d: (index.get(d.isoformat(), {}).get("上市") or {}).get("close") for d in days}
    gdays = sorted({d for d, _ in groups})
    ratios = daytrade_ratios(s, gdays, codes)

    def fwd(code, d, h):
        i = pos[d]
        if i + h >= len(days):
            return None
        a, b = closes.get(code, {}).get(d), closes.get(code, {}).get(days[i + h])
        ia, ib = idx.get(d), idx.get(days[i + h])
        if not (a and b and ia and ib):
            return None
        return (b / a - 1) * 100 - (ib / ia - 1) * 100

    rows = []
    for (d, ms), g in groups.items():
        if d not in pos:
            continue
        rets = {h: [x for c in ms if (x := fwd(c, d, h)) is not None] for h in HORIZONS}
        ev = g.get("evidence") or {}
        hot = daytrade_group({c: ratios.get(c, {}).get(d) for c in ms})["hot"]
        rows.append({"n": evidence_yes(g), "checks": {k: (ev.get(k) or {}).get("ok") is True for k in EVIDENCE_CHECKS}, "hot": hot,
                     "ret": {h: sum(v) / len(v) for h, v in rets.items() if v}})
    rng = random.Random(7)

    def summarize(sel, h):
        xs = [r["ret"][h] for r in sel if h in r["ret"]]
        if len(xs) < 5:
            return None
        means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(2000))
        return {"n": len(xs), "mean": round(sum(xs) / len(xs), 2), "lo": round(means[100], 2), "hi": round(means[1899], 2), "up": round(sum(1 for x in xs if x > 0) / len(xs) * 100)}

    out = {"groups": len(rows), "days": len(gdays), "by_count": {}, "by_check": {}, "hot": {}}
    for label, sel in (("≥4", [r for r in rows if r["n"] >= 4]), ("2–3", [r for r in rows if 2 <= r["n"] <= 3]), ("≤1", [r for r in rows if r["n"] <= 1])):
        out["by_count"][label] = {h: summarize(sel, h) for h in HORIZONS}
    for k in EVIDENCE_CHECKS:
        out["by_check"][k] = {"✓": {h: summarize([r for r in rows if r["checks"][k]], h) for h in HORIZONS},
                              "not": {h: summarize([r for r in rows if not r["checks"][k]], h) for h in HORIZONS}}
    out["hot"] = {"🔥": {h: summarize([r for r in rows if r["hot"]], h) for h in HORIZONS}, "rest": {h: summarize([r for r in rows if not r["hot"]], h) for h in HORIZONS}}

    def fmt(x):
        return "—" if not x else f"{x['mean']:+.2f}% [{x['lo']:+.2f}, {x['hi']:+.2f}] 上漲 {x['up']}% (n={x['n']})"

    print(f"{out['groups']} groups over {out['days']} days; group-mean return less 大盤, 90% bootstrap interval\n")
    for label, v in out["by_count"].items():
        print(f"✓ {label:4s} " + " | ".join(f"{h}日 {fmt(v[h])}" for h in HORIZONS))
    print()
    for k, v in out["by_check"].items():
        print(f"{k:8s} ✓: " + " | ".join(f"{h}日 {fmt(v['✓'][h])}" for h in HORIZONS))
        print(f"{'':8s} ✗: " + " | ".join(f"{h}日 {fmt(v['not'][h])}" for h in HORIZONS))
    print()
    for k, v in out["hot"].items():
        print(f"{k:5s} " + " | ".join(f"{h}日 {fmt(v[h])}" for h in HORIZONS))
    Path(__file__).with_name("out").joinpath("exp5.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
