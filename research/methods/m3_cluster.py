"""大神做法 3：階層聚類 instead of 兩兩配對 + 七成密集核心.

Per day (the last 10 cached): the stocks of the 找法1 (官方產業) candidates; their excess-R matrix (today's
β residuals) turned into distances d = √((1 − R) / 2); average-linkage clustering stopped where the
average R falls below 0.4 (d = 0.548). Set against N's groups of the same day (offline, defaults):
how many clusters, how well each N group is matched (best Jaccard), the members clustering adds or
drops, and how many cluster pairs would fail N's other tests (peak gap, opening peak) — clustering on R
alone keeps no timing rule.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import common as C  # noqa: E402
import linalg as L  # noqa: E402
from openmarket.history.finder import add_excess, daily_series, intraday_features, known_candidates, pair_check  # noqa: E402
from openmarket.history.linkage import align_bars  # noqa: E402

CUT_R = 0.4


def main() -> None:
    s = C.store()
    all_days = C.cached_days(s)
    p = C.FinderParams(groupings=("industry",))
    cut = math.sqrt((1 - CUT_R) / 2)
    report = []
    for d in all_days[-10:]:
        i = all_days.index(d)
        sessions = all_days[i - 2:i + 1]
        rows = [x for x in s.day_rows(d) if x.get("kind") == "stock"]
        by_code = {x["code"]: x for x in rows}
        series = daily_series(s, d, p.daily_window)
        cands = known_candidates(rows, {"industry": C.industry_grouping}, series, p)
        codes = sorted({c for cd in cands for c in cd["members"]})
        session_rows = {x: {r["code"]: r for r in rs} for x, rs in s.rows(sessions).items()}
        feats = {}
        for c in codes + list(C.INDEXES):
            bars = s.yahoo_bars(c, sessions)
            if bars.get(sessions[-1]):
                aligned = {x: align_bars(b, session_rows.get(x, {}).get(c, {})) for x, b in bars.items()}
                feats[c] = intraday_features(aligned, {x: session_rows.get(x, {}).get(c, {}) for x in sessions}, d, p, 5)
        add_excess(feats, by_code, rows, sessions, s.sector_intraday(sessions), "sector")
        codes = [c for c in codes if c in feats and feats[c].excess]
        n = len(codes)
        dist = [[0.0] * n for _ in range(n)]
        for a in range(n):
            for b in range(a + 1, n):
                rr = C.pair_r(feats[codes[a]].excess, feats[codes[b]].excess)
                dist[a][b] = dist[b][a] = math.sqrt((1 - rr) / 2) if rr is not None else 1.0
        clusters = [[codes[k] for k in cl] for cl in L.average_linkage(dist, cut) if len(cl) >= 2]
        groups = [[m["code"] for m in g["members"]] for g in C.run(s, d, "known")["groups"]]
        match = []
        for g in groups:
            best = max(clusters, key=lambda cl: len(set(cl) & set(g)) / len(set(cl) | set(g)), default=[])
            jac = len(set(best) & set(g)) / len(set(best) | set(g)) if best else 0.0
            match.append({"group": g, "cluster": best, "jaccard": round(jac, 2)})
        fail = total = 0
        for cl in clusters:
            for a in range(len(cl)):
                for b in range(a + 1, len(cl)):
                    total += 1
                    fail += not pair_check(feats[cl[a]], feats[cl[b]], p)["ok"]
        row = {"date": d.isoformat(), "stocks": n, "clusters": len(clusters), "cluster_sizes": sorted((len(c) for c in clusters), reverse=True)[:10],
               "n_groups": len(groups), "match": match, "cluster_pairs": total, "cluster_pairs_failing_n_tests": fail}
        report.append(row)
        print(d, f"stocks={n} clusters={len(clusters)} N groups={len(groups)} mean jaccard={C.mean([m['jaccard'] for m in match]) or 0:.2f} cluster pairs failing N's tests {fail}/{total}", flush=True)
    Path(__file__).with_name("out").mkdir(exist_ok=True)
    Path(__file__).with_name("out").joinpath("m3.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
