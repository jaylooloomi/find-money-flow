"""大神做法 2：Lead-lag 互相關 and DTW instead of 「高點差 ≤ 8 分」＋「同分鐘」.

On the days with cached 1-minute bars (15), for the pairs N passes (pair_check at the defaults, 找法1
官方產業 candidates) and for random pairs of the same pool:
  CCF  — ρ(τ) of the 1-minute returns for τ = −5…+5; where the peak sits, how sharp it is (ρ(0) − mean
         ρ(±1..3)); for pairs seen on several days, whether the same stock leads each time
  DTW  — distance between the two z-normalized 5-minute cumulative paths of the day (band 6 steps);
         how well it tells found pairs from random ones (AUC), and the candidate pairs the 8-minute
         peak rule throws out although their paths are as close as the found pairs' (the 「分批推進」 case)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import common as C  # noqa: E402
import linalg as L  # noqa: E402
from m1_pca_rmt import day_data  # noqa: E402
from openmarket.history.finder import pair_check  # noqa: E402

LAGS = range(-5, 6)


def minute_returns(bars) -> dict[int, float]:
    def m(t):
        return (int(t[:2]) - 9) * 60 + int(t[3:5])

    close = {m(b[0]): b[3] for b in bars if b[3]}
    return {t: close[t] / close[t - 1] - 1 for t in close if t - 1 in close and close[t - 1]}


def ccf(a: dict, b: dict) -> dict[int, float | None]:
    out = {}
    for k in LAGS:  # k > 0: a leads b by k minutes
        pts = [(a[t], b[t + k]) for t in a if t + k in b]
        out[k] = C.pearson([x for x, _ in pts], [y for _, y in pts]) if len(pts) >= 60 else None
    return out


def path(rets) -> list[float]:
    xs, acc = [], 0.0
    for r in rets[-53:]:  # the shown day's 53 five-minute returns
        acc += r or 0.0
        xs.append(acc)
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0
    return [(x - m) / sd for x in xs]


def auc(pos: list[float], neg: list[float]) -> float | None:
    """P(a found pair's distance < a random pair's)."""
    if not pos or not neg:
        return None
    wins = sum((1 if p < n else 0.5 if p == n else 0) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> None:
    s = C.store()
    with s._conn() as c:
        minute_days = sorted({r["date"] for r in c.execute("SELECT date FROM yahoo_1m GROUP BY date HAVING COUNT(*) >= 200")})
    all_days = C.cached_days(s)
    r = C.rng()
    peak_found, peak_random, sharp_found, sharp_random = [], [], [], []
    leaders: dict[tuple, list[int]] = {}
    dtw_found, dtw_random, rescued = [], [], []
    used = 0
    for ds in minute_days:
        d = next((x for x in all_days if x.isoformat() == ds), None)
        if d is None or all_days.index(d) < 2:
            continue
        i = all_days.index(d)
        raw, feats, found = day_data(s, d, all_days[i - 2:i + 1])
        if not found:
            continue
        used += 1
        codes = list(raw)
        mins = {}
        for code in codes:
            bars = s.yahoo_1m(code, d)
            if bars:
                mins[code] = minute_returns(bars)
        rand = [tuple(r.sample(codes, 2)) for _ in range(300)]
        for pairs, peaks, sharps in ((found, peak_found, sharp_found), (rand, peak_random, sharp_random)):
            for a, b in pairs:
                if a not in mins or b not in mins:
                    continue
                c = ccf(mins[a], mins[b])
                known = {k: v for k, v in c.items() if v is not None}
                if len(known) < len(LAGS):
                    continue
                top = max(known, key=lambda k: known[k])
                peaks.append(top)
                sharps.append(known[0] - sum(known[k] for k in (-3, -2, -1, 1, 2, 3)) / 6)
                if pairs is found and top != 0:
                    leaders.setdefault(tuple(sorted((a, b))), []).append(top if a < b else -top)
        p = C.FinderParams(groupings=("industry",))
        paths = {c: path(raw[c]) for c in codes}
        dtw_found += [L.dtw(paths[a], paths[b]) for a, b in found]
        dtw_random += [L.dtw(paths[a], paths[b]) for a, b in rand]
        # pairs failing only the 8-minute peak rule
        bar = C.quantile([L.dtw(paths[a], paths[b]) for a, b in found], 0.5)
        for a in codes:
            for b in codes:
                if a < b and (a, b) not in found and feats[a].peak is not None and feats[b].peak is not None:
                    chk = pair_check(feats[a], feats[b], p)
                    if chk["reason"] == "高點時間差太大" and L.dtw(paths[a], paths[b]) <= bar:
                        rescued.append({"date": ds, "pair": [a, b], "peak_gap": chk["peak_gap"], "excess_r": chk["excess_r"]})
        print(ds, "found", len(found), flush=True)

    def hist(xs):
        return {k: xs.count(k) for k in LAGS}

    out = {
        "days": used,
        "ccf_peak_found": hist(peak_found), "ccf_peak_random": hist(peak_random),
        "share_peak_at_0": {"found": peak_found.count(0) / len(peak_found) if peak_found else None,
                            "random": peak_random.count(0) / len(peak_random) if peak_random else None},
        "sharpness": {"found": C.mean(sharp_found), "random": C.mean(sharp_random)},
        "leader_pairs_seen_twice": {str(k): v for k, v in leaders.items() if len(v) >= 2},
        "dtw": {"found_median": C.quantile(dtw_found, 0.5), "random_median": C.quantile(dtw_random, 0.5), "auc": auc(dtw_found, dtw_random)},
        "rescued_by_dtw": rescued[:40], "rescued_count": len(rescued),
    }
    print(json.dumps({k: v for k, v in out.items() if k != "rescued_by_dtw"}, ensure_ascii=False, indent=1))
    Path(__file__).with_name("out").mkdir(exist_ok=True)
    Path(__file__).with_name("out").joinpath("m2.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
