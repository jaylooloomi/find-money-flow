"""大神做法 1：PCA + 隨機矩陣理論 instead of 扣類股／大盤.

Per day: the 150 most traded common stocks with cached 5-minute bars on the 3 sessions (T = 159
returns). Correlation matrix → eigenvalues; how many stand above the Marchenko–Pastur edge λ₊. Then the
pair R of four versions of the returns:
  raw        — as they are
  beta-1     — today's method: each stock less β × its 類股／大盤／櫃買 (factors.factor_of)
  pca-1      — less the first principal factor (the market mode)
  pca-k      — less every factor above λ₊
For each: the spread of random pairs (mean, 95th percentile) and where the pairs N passes today
(pair_check at the defaults) sit in that spread — a better method keeps the found pairs high while the
random pairs fall towards 0.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import common as C  # noqa: E402
import linalg as L  # noqa: E402
from openmarket.history.finder import add_excess, bucket_returns, daily_series, intraday_features, known_candidates, pair_check  # noqa: E402
from openmarket.history.linkage import align_bars, limit_up_price  # noqa: E402

N_STOCKS = 150
DAYS = 8


def day_data(s, d, sessions):
    rows = [r for r in s.day_rows(d) if r.get("kind") == "stock" and (r.get("turnover_yuan") or 0) > 0]
    rows.sort(key=lambda r: -(r.get("turnover_yuan") or 0))
    by_code = {r["code"]: r for r in rows}
    session_rows = {x: {r["code"]: r for r in rs} for x, rs in s.rows(sessions).items()}
    p = C.FinderParams(groupings=("industry",))
    feats, raw = {}, {}
    for r in rows[: N_STOCKS * 2]:
        c = r["code"]
        bars = s.yahoo_bars(c, sessions)
        if len(bars) < 3 or not all(bars.get(x) for x in sessions):
            continue
        aligned = {x: align_bars(b, session_rows.get(x, {}).get(c, {})) for x, b in bars.items()}
        feats[c] = intraday_features(aligned, {x: session_rows.get(x, {}).get(c, {}) for x in sessions}, d, p, 5)
        raw[c] = feats[c].returns
        if len(raw) >= N_STOCKS:
            break
    for c in C.INDEXES:
        bars = s.yahoo_bars(c, sessions)
        if bars:
            feats[c] = intraday_features({x: bars.get(x, []) for x in sessions}, {x: {} for x in sessions}, d, p, 5)
    add_excess(feats, by_code, rows, sessions, s.sector_intraday(sessions), "sector")
    # the pairs today's N passes among the 找法1 candidates (both members in the pool)
    series = daily_series(s, d, p.daily_window)
    cands = known_candidates(rows, {"industry": C.industry_grouping}, series, p)
    found = {tuple(sorted((a, b))) for cd in cands for a in cd["members"] for b in cd["members"]
             if a < b and a in raw and b in raw and pair_check(feats[a], feats[b], p)["ok"]}
    return raw, feats, found


def stats(series: dict, found: set, pairs: list):
    rs = [C.pair_r(series[a], series[b]) for a, b in pairs]
    rs = [r for r in rs if r is not None]
    fr = [C.pair_r(series[a], series[b]) for a, b in found]
    fr = [r for r in fr if r is not None]
    pct = [sum(1 for r in rs if r < x) / len(rs) for x in fr] if rs else []
    return {"random_mean": C.mean(rs), "random_p95": C.quantile(rs, 0.95), "found_mean": C.mean(fr), "found_pct": C.mean(pct), "found_n": len(fr)}


def main() -> None:
    s = C.store()
    days = C.cached_days(s)[-DAYS:]
    all_days = C.cached_days(s)
    report = []
    t0 = time.time()
    for d in days:
        i = all_days.index(d)
        sessions = all_days[i - 2:i + 1]
        raw, feats, found = day_data(s, d, sessions)
        codes = list(raw)
        z = L.standardize([raw[c] for c in codes])
        vals, vecs = L.jacobi_eigen(L.corr_matrix(z))
        lam = L.mp_upper(len(codes), len(z[0]))
        k = sum(1 for v in vals if v > lam)
        versions = {
            "raw": raw,
            "beta-1": {c: feats[c].excess for c in codes if feats[c].excess is not None},
            "pca-1": dict(zip(codes, L.remove_factors(z, vecs, 1))),
            f"pca-k": dict(zip(codes, L.remove_factors(z, vecs, k))),
        }
        pairs = [(a, b) for i2, a in enumerate(codes) for b in codes[i2 + 1:]]
        row = {"date": d.isoformat(), "n": len(codes), "t": len(z[0]), "lambda_plus": round(lam, 2), "top": [round(v, 2) for v in vals[:8]],
               "k_above": k, "found_pairs": len(found), "versions": {}}
        for name, ser in versions.items():
            ok_pairs = [(a, b) for a, b in pairs if a in ser and b in ser]
            row["versions"][name] = stats(ser, {pr for pr in found if pr[0] in ser and pr[1] in ser}, ok_pairs)
        report.append(row)
        print(d, f"n={len(codes)} λ+={lam:.2f} λ1={vals[0]:.1f} k={k} found={len(found)} ({time.time() - t0:.0f}s)", flush=True)
        for name, st in row["versions"].items():
            print(f"   {name:7s} random mean {st['random_mean']:+.3f} p95 {st['random_p95']:+.3f} | found mean {st['found_mean'] if st['found_mean'] is not None else float('nan'):+.3f} pct {st['found_pct'] if st['found_pct'] is not None else float('nan'):.3f}", flush=True)
    Path(__file__).with_name("out").mkdir(exist_ok=True)
    Path(__file__).with_name("out").joinpath("m1.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
