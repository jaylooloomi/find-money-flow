from datetime import date

import pytest

from openmarket.history.finder import (
    FinderParams,
    Intraday,
    bucket_returns,
    candidate_pool,
    co_mentions,
    confirm,
    daily_edges,
    dense_cores,
    intraday_features,
    kind_of,
    known_candidates,
    match_known,
    pair_check,
)

P = FinderParams(intraday_basis="raw")  # the rules on the raw R; the excess-R gate has its own test


def e(a, b):
    return frozenset((a, b))


def test_dense_cores_do_not_chain():
    # A-B-C-D in a line: no member links to ≥70% of a 3+ group, so only pairs survive
    line = [e("A", "B"), e("B", "C"), e("C", "D")]
    cores = dense_cores(line, 0.7, 3)
    assert cores == []
    # a full triangle plus a hanger-on: the triangle is the core
    edges = [e("A", "B"), e("B", "C"), e("A", "C"), e("C", "D")]
    assert dense_cores(edges, 0.7, 3) == [["A", "B", "C"]]
    # two separate pairs
    assert sorted(dense_cores([e("A", "B"), e("X", "Y")], 0.7, 2)) == [["A", "B"], ["X", "Y"]]


def test_daily_edges_threshold_and_missing_days():
    s = {"A": [1, 2, 3, 4, 5], "B": [1.1, 2.1, 2.9, 4.2, 5], "C": [5, 1, 4, 2, 3], "D": [1, None, None, None, 5]}
    edges = daily_edges("ABCD", s, 0.8)
    assert set(edges) == {e("A", "B")}  # C uncorrelated; D misses too many days


def test_known_candidates_merges_sources():
    rows = [{"code": c, "kind": "stock", "industry": "X", "high": 105, "low": 100, "prev_close": 100, "volume_shares": 500_000} for c in "ABC"]
    s = {"A": [1, 2, 3, 4, 5], "B": [1, 2, 3, 4, 5.5], "C": [1.2, 2, 3.1, 4, 5]}
    groupings = {"industry": lambda r: [r["industry"]], "custom": lambda r: ["題材"]}
    (c,) = known_candidates(rows, groupings, s, P)
    assert c["members"] == ["A", "B", "C"]
    assert [x["grouping"] for x in c["sources"]] == ["industry", "custom"]
    assert len(c["daily"]) == 3
    # 振幅 filter: C moved only 2% that day, so it is not a member
    rows[2].update(high=101, low=99)
    (c,) = known_candidates(rows, groupings, s, P)
    assert c["members"] == ["A", "B"]
    assert known_candidates(rows, groupings, s, FinderParams(min_amplitude=1.5))[0]["members"] == ["A", "B", "C"]
    # volume filter: B traded only 200 張 (< 300)
    rows[1]["volume_shares"] = 200_000
    assert known_candidates(rows, groupings, s, FinderParams(min_amplitude=1.5))[0]["members"] == ["A", "C"]
    assert known_candidates(rows, groupings, s, FinderParams(min_amplitude=1.5, min_volume_lots=100))[0]["members"] == ["A", "B", "C"]


def test_bucket_returns_skip_locked_stretch():
    # prev 100, limit 110: 09:00 105, 09:05 110 (locks), 09:10 110 (locked), 09:15 110
    bars = [("09:00", 105, 105, 105), ("09:05", 110, 110, 110), ("09:10", 110, 110, 110), ("09:15", 110, 110, 110)]
    r = bucket_returns(bars, 110.0)
    assert r[0] == pytest.approx((110 / 105 - 1) * 100)
    assert r[1] is None and r[2] is None  # flat at the limit: not counted


def test_intraday_features_peak_start_lock_bursts():
    row = {"prev_close": 100.0}
    bars = [
        ("09:00", 101, 99, 100, 100, 5000),  # the open auction: not a burst bucket
        ("09:05", 100, 98, 98.5, 100, 100),
        ("09:10", 99, 97, 98, 98, 100),  # low 97: the run starts here
        ("09:15", 104, 98, 104, 98, 900),  # burst (≥ 3 × median 100)
        ("09:20", 110, 104, 110, 104, 120),  # limit 110 reached
        ("13:10", 110, 110, 110, 110, 100),
    ]
    f = intraday_features({date(2026, 1, 2): bars}, {date(2026, 1, 2): row}, date(2026, 1, 2), P)
    assert (f.peak, f.start, f.locked_at) == (20, 10, 20)
    assert f.bursts == {3}


def mk(peak, start=None, locked=None, rets=None, bursts=()):
    return Intraday(returns=rets if rets is not None else [], peak=peak, start=start, locked_at=locked, bursts=set(bursts))


def test_pair_check_rules():
    up = [0.1, 0.3, -0.2, 0.5, 0.1] * 6  # 30 points
    same = [x * 1.1 for x in up]
    other = [(-1) ** i * 0.3 for i in range(30)]
    assert pair_check(mk(60, rets=up), mk(66, rets=same), P)["ok"]
    assert pair_check(mk(60, rets=up), mk(80, rets=same), P)["reason"] == "高點時間差太大"
    assert pair_check(mk(60, rets=up), mk(62, rets=other), P)["reason"] == "分時相關不足"
    assert pair_check(mk(0, rets=up), mk(1, rets=same), P)["reason"] == "開盤即高點（假同步）"
    # both locked at the open with no usable returns: counts as moving together
    assert pair_check(mk(0, locked=0), mk(1, locked=1), P)["ok"]
    # optional requirements
    p = FinderParams(start_gap=5, require_burst=True, intraday_basis="raw")
    assert pair_check(mk(60, 10, rets=up), mk(62, 30, rets=same), p)["reason"] == "起漲時間差太大"
    assert pair_check(mk(60, 10, rets=up, bursts={3}), mk(62, 12, rets=same, bursts={4}), p)["reason"] == "沒有同時爆量"
    assert pair_check(mk(60, 10, rets=up, bursts={3}), mk(62, 12, rets=same, bursts={3}), p)["ok"]


def test_confirm_keeps_dense_cores_of_passing_pairs():
    up = [0.1, 0.3, -0.2, 0.5, 0.1] * 6
    feats = {"A": mk(60, rets=up), "B": mk(62, rets=up), "C": mk(64, rets=up), "D": mk(150, rets=up)}
    funnel = {}
    (g,) = confirm([{"members": ["A", "B", "C", "D", "Z"], "sources": [], "daily": {}}], feats, P, funnel)
    assert g["members"] == ["A", "B", "C"]  # D peaked 90 minutes later
    # 10 pairs: A/B/C pass with each other (3), D fails the high gap with A/B/C (3), Z has no bars (4)
    assert funnel == {"pairs": 10, "no_bars": 4, "passed": 3, "rejected": {"高點時間差太大": 3}}
    assert set(g["pairs"]) == {e("A", "B"), e("A", "C"), e("B", "C")}


def test_candidate_pool_and_match_known():
    rows = [
        {"code": "1", "kind": "stock", "change_pct": 9.9, "close": 10, "market_cap": 1000, "volume_shares": 10, "industry": "X"},
        {"code": "2", "kind": "stock", "change_pct": 1.0, "close": 10, "market_cap": 1000, "volume_shares": 50, "industry": "X"},
        {"code": "3", "kind": "stock", "change_pct": 5.0, "close": 10, "market_cap": 1000, "volume_shares": 1, "industry": "Y"},
        {"code": "E", "kind": "etf", "change_pct": 20.0, "close": 10, "market_cap": 1000, "volume_shares": 99, "industry": ""},
    ]
    pool = candidate_pool(rows, {"3": 9.0}, FinderParams(pool_top=1, new_min_volume_lots=0))
    assert pool == {"1": ["漲幅"], "2": ["週轉率"], "3": ["比重暴增"]}
    # the volume floor (張 = 1,000 shares): only "2" traded ≥ 0.05 張 here
    assert candidate_pool(rows, {"3": 9.0}, FinderParams(pool_top=1, new_min_volume_lots=0.05)) == {"2": ["漲幅", "週轉率"]}
    by = {r["code"]: r for r in rows}
    m = match_known(["1", "2", "3"], by, {"industry": lambda r: [r["industry"]]})
    assert m[0]["group"] == "X" and m[0]["overlap"] == pytest.approx(2 / 3) and m[0]["outside"] == ["3"]
    assert kind_of(m, 3) == "已知族群的延伸"  # 2 of 3 in X
    assert kind_of([{"overlap": 1.0, "outside": []}], 2) == "已知族群"
    assert kind_of([{"overlap": 0.5, "outside": ["b"]}], 2) == "跨族群新組合"  # a pair with one member in a group
    assert kind_of([{"overlap": 0.4, "outside": ["a", "b", "c"]}], 5) == "跨族群新組合" and kind_of([], 2) == "跨族群新組合"


def test_money_size():
    from openmarket.history.finder import money_size

    rows = {"A": {"turnover_yuan": 3e9, "change_pct": 9.9}, "B": {"turnover_yuan": 1e9, "change_pct": 4.1}}
    assert money_size(["A", "B", "Z"], rows, 4e11) == {"turnover_100m": 40.0, "turnover_share": 1.0, "avg_pct": 7.0}
    assert money_size(["A"], rows, 0)["turnover_share"] is None


def test_persistence_same_combination_on_earlier_days():
    from openmarket.history.finder import persistence

    def grp(*codes):
        return {"members": [{"code": c} for c in codes]}

    today = [grp("A", "B"), grp("C", "D", "E"), grp("X", "Y")]
    earlier = {
        "2026-10-20": {"groups": [grp("A", "B"), grp("C", "D")]},  # A+B again; C+D overlaps 2/3
        "2026-10-21": {"groups": [grp("A", "Z")]},  # A+Z vs A+B: 1/3, not the same
        "2026-10-19": {"groups": [grp("B", "A")]},
    }
    out = persistence(today, earlier)
    assert out[0]["seen_days"] == ["2026-10-19", "2026-10-20"] and out[0]["checked_days"] == 3
    assert out[1]["seen_days"] == ["2026-10-20"]
    assert out[2]["seen_days"] == []
    assert "seen_days" not in today[0]  # the input is not changed


def test_news_themes_per_member_and_group():
    from openmarket.history.finder import news_themes

    news = [
        {"被動元件": {"codes": {"2327": 3, "2492": 1}}, "AI": {"codes": {"2327": 1}}},
        {"被動元件": {"codes": {"2492": 2}}, "漲價": {"codes": {"2492": 1, "6173": 1}}, "其他": {"codes": {}}, "音量🔊": {"codes": {"2327": 9}}},
    ]
    (g,) = news_themes([{"members": [{"code": "2327"}, {"code": "2492"}, {"code": "9999"}]}], news)
    assert [m["themes"] for m in g["members"]] == [["被動元件", "AI"], ["被動元件", "漲價"], []]
    assert [{k: t[k] for k in ("term", "count")} for t in g["themes"]] == [{"term": "被動元件", "count": 2}, {"term": "AI", "count": 1}, {"term": "漲價", "count": 1}]


def test_co_mentions():
    names = {"2327": "國巨*", "2492": "華新科", "6173": "信昌電"}
    titles = ["被動元件大漲 國巨、華新科領軍", "國巨法說會", "信昌電與華新科同步攻頂", "國巨、華新科領軍"]
    assert co_mentions(names, titles) == ["被動元件大漲 國巨、華新科領軍", "信昌電與華新科同步攻頂", "國巨、華新科領軍"]


def test_params_key_changes_with_any_threshold():
    assert FinderParams().key() != FinderParams(daily_r=0.75).key()
    assert FinderParams().key() == FinderParams().key()
    # the same groupings / pool sources in another order are the same search
    assert FinderParams(groupings=("custom", "industry")).key() == FinderParams(groupings=("industry", "custom")).key()
    assert FinderParams(pool_sources=("share_z", "gain")).key() == FinderParams(pool_sources=("gain", "share_z")).key()
    assert FinderParams(groupings=("custom",)).key() != FinderParams(groupings=("industry",)).key()


def test_finder_api_stored_only_never_starts_a_search(tmp_path):
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 3, 2)
    s.save_day(d, [{"code": "A", "name": "a", "market": "上市", "industry": "X", "kind": "stock", "close": 10, "prev_close": 10, "change": 0, "change_pct": 0}], datetime(2026, 3, 2, 15))
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        body = client.get("/api/history/finder", params={"date": d.isoformat(), "method": "known", "stored_only": 1, "groupings": "industry"}).json()
        assert body["stored"] is False and body["groups"] == []
        assert s.finder(d, "known", FinderParams(groupings=("industry",)).key() + "|industry") is None  # nothing computed or saved


def test_store_batch_bars_and_results(tmp_path):
    from datetime import datetime

    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    d1, d2 = date(2026, 1, 2), date(2026, 1, 5)
    s.save_yahoo_1m_many([("A", d1, [("09:00", 1, 1, 1, 1, 10)]), ("B", d1, []), ("A", d2, [("09:00", 2, 2, 2, 2, 20)])])
    got = s.yahoo_1m_many(["A", "B", "C"], [d1, d2])
    assert got[("A", d1)] == [("09:00", 1, 1, 1, 1, 10)] and got[("B", d1)] == [] and ("C", d1) not in got
    s.save_finder(d1, "known", "k", {"groups": [1]})
    assert s.finder(d1, "known", "k") == {"groups": [1]} and s.finder(d1, "new", "k") is None
    s.save_day(d1, [], datetime(2026, 1, 2, 15))
    assert s.finder(d1, "known", "k") is None  # a rewritten day drops its results


def test_run_search_end_to_end(tmp_path):
    import asyncio
    import random
    from datetime import datetime, timedelta

    from openmarket.history.finder import run_search
    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    rnd = random.Random(3)
    days = [date(2026, 3, 2) + timedelta(days=i) for i in range(28) if (date(2026, 3, 2) + timedelta(days=i)).weekday() < 5][:20]
    price = {c: 100.0 for c in "ABCDX"}
    for d in days:
        common = rnd.uniform(-4, 4)
        rows = []
        for c in "ABCDX":
            move = common + rnd.uniform(-0.3, 0.3) if c in "ABCD" else rnd.uniform(-4, 4)
            prev = price[c]
            price[c] = round(prev * (1 + move / 100), 2)
            rows.append({"code": c, "name": f"n{c}", "market": "上市", "industry": "X" if c != "X" else "Y", "kind": "stock",
                         "close": price[c], "prev_close": prev, "change": price[c] - prev, "change_pct": (price[c] / prev - 1) * 100,
                         "open": prev, "high": max(prev, price[c]) * 1.001, "low": min(prev, price[c]) * 0.999,
                         "volume_shares": 1e6, "turnover_yuan": 1e8, "market_cap": 1e10 * (1 + "ABCDX".index(c))})
        s.save_day(d, rows, datetime(2026, 3, 1))
    day = days[-1]
    sessions = days[-3:]
    peaks = {"A": 60, "B": 65, "C": 60, "D": 150}  # 5-minute bars; D peaks 90 minutes later

    async def fetch(rows, sess, done):
        out = {}
        assert [r["code"] for r in rows if r.get("kind") == "index"] == ["^TWII", "IX0043.TWO"]  # asked for the excess R
        for r in rows:
            if r.get("kind") == "index":
                continue  # no index bars here: the excess R stays 'no data'
            by = {}
            for si, ss in enumerate(sess):
                bars, p = [], 100.0
                for m in range(0, 270, 5):
                    p *= 1 + (0.002 if (m // 5 + si) % 3 else -0.003)
                    hi = p * (1.05 if ss == day and m == peaks[r["code"]] else 1.0)
                    bars.append((f"{9 + m // 60:02d}:{m % 60:02d}", hi, p * 0.999, p, p, 1000.0))
                by[ss] = bars
            out[r["code"]] = (by, 5)
            done()
        return out

    seen = []
    res = asyncio.run(run_search(s, day, "known", FinderParams(daily_r=0.8, min_amplitude=0, min_volume_lots=0, intraday_basis="raw"), {"industry": lambda r: [r["industry"]]}, fetch, progress=lambda *a: seen.append(a)))
    assert seen[0] == ("daily", 0, 1) and ("fetch", 4, 4) in seen and seen[-1] == ("intraday", 0, 1)
    assert res["stats"]["candidates"] == 1
    (g,) = res["groups"]
    assert [m["code"] for m in g["members"]] == ["A", "B", "C"]  # D dropped on the intraday-high gap
    assert g["kind"] == "已知族群" and g["peak_spread"] == 5 and g["daily_r"] > 0.8


def test_pair_r_fast_path_matches_pearson():
    from openmarket.history.finder import PairR, series_r

    s = {"A": [1.0, -2, 3, 0.5, -1], "B": [0.8, -1.5, 2.0, 1.0, -0.2], "C": [1.0, None, 3, 0.5, -1]}
    pr = PairR(s)
    assert pr("A", "B") == pytest.approx(series_r(s["A"], s["B"], 3))
    assert pr("B", "A") == pr("A", "B")
    assert pr("A", "C") == pytest.approx(series_r(s["A"], s["C"], 3))  # the gap path


def test_finder_api_stored_result_gets_history_and_themes(tmp_path):
    """Through the endpoint: a stored result comes back with 🔁 and 📰 attached (catches name clashes in api.py)."""
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 3, 2)
    s.save_day(d, [{"code": "A", "name": "a", "market": "上市", "industry": "X", "kind": "stock", "close": 10, "prev_close": 10, "change": 0, "change_pct": 0}], datetime(2026, 3, 2, 15))
    s.save_news_day(d, {"題材甲": {"mentions": 2, "codes": {"A": 2}, "titles": []}}, 2)
    p = FinderParams(groupings=("industry",))
    s.save_finder(d, "known", p.key() + "|industry", {"date": d.isoformat(), "method": "known", "groups": [{"members": [{"code": "A", "name": "a"}, {"code": "B", "name": "b"}]}], "stats": {}})
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/history/finder", params={"date": d.isoformat(), "method": "known", "groupings": "industry"})
        assert r.status_code == 200
        (g,) = r.json()["groups"]
        assert g["checked_days"] == 0 and [{k: t[k] for k in ("term", "count")} for t in g["themes"]] == [{"term": "題材甲", "count": 1}]
        assert g["members"][0]["themes"] == ["題材甲"]


def test_finder_api_themes_skip_news_older_than_a_week(tmp_path):
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 3, 20)
    s.save_day(d, [{"code": "A", "name": "a", "market": "上市", "industry": "X", "kind": "stock", "close": 10, "prev_close": 10, "change": 0, "change_pct": 0}], datetime(2026, 3, 20, 15))
    s.save_news_day(date(2026, 3, 2), {"舊題材": {"mentions": 1, "codes": {"A": 1}, "titles": []}}, 1)  # 18 days before
    p = FinderParams(groupings=("industry",))
    s.save_finder(d, "known", p.key() + "|industry", {"date": d.isoformat(), "method": "known", "groups": [{"members": [{"code": "A", "name": "a"}]}], "stats": {}})
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        (g,) = client.get("/api/history/finder", params={"date": d.isoformat(), "method": "known", "groupings": "industry"}).json()["groups"]
        assert g["themes"] == [] and g["members"][0]["themes"] == []


def test_inst_summary_in_lots_and_amount():
    from openmarket.history.finder import inst_summary

    groups = [{"members": [{"code": "A"}, {"code": "B"}, {"code": "C"}]}, {"members": [{"code": "X"}]}]
    inst = {"A": 2_000_000.0, "B": -500_000.0, "C": 0.0}
    rows = {"A": {"close": 100.0}, "B": {"close": 50.0}, "C": {"close": 10.0}}
    g, empty = inst_summary(groups, inst, rows)
    assert g["inst"] == {"lots": 1500, "amount_100m": round((2e6 * 100 - 5e5 * 50) / 1e8, 2), "buyers": 1, "sellers": 1}
    assert g["inst_net"] == 1_500_000.0 and g["members"][1]["inst_net"] == -500_000.0
    assert empty["inst"] is None  # 法人 not published for these yet
    assert "inst" not in groups[0]  # copies, the stored result is untouched
    no_close = inst_summary(groups[:1], inst, {"A": {"close": 100.0}, "B": {}, "C": {"close": 10.0}})[0]
    assert no_close["inst"]["amount_100m"] is None and no_close["inst"]["lots"] == 1500


def test_inst_streak_counts_same_sign_days_of_the_group():
    from openmarket.history.finder import inst_streak

    d = [date(2026, 10, 22), date(2026, 10, 21), date(2026, 10, 20), date(2026, 10, 19)]  # newest first
    by_day = {
        d[0]: {"A": 100.0, "B": -30.0},  # group +70
        d[1]: {"A": 50.0},  # +50 (B missing that day: the members that have data)
        d[2]: {"A": -10.0, "B": 40.0},  # +30
        d[3]: {"A": -100.0, "B": 0.0},  # −100: the streak ends
    }
    groups = [{"members": [{"code": "A"}, {"code": "B"}]}, {"members": [{"code": "X"}]}]
    g, none = inst_streak(groups, by_day, d)
    assert g["inst_streak"] == {"days": 3, "side": "buy"} and none["inst_streak"] is None
    sold = inst_streak([{"members": [{"code": "A"}]}], {d[0]: {"A": -1.0}, d[1]: {"A": -2.0}, d[2]: {"A": 3.0}}, d)[0]
    assert sold["inst_streak"] == {"days": 2, "side": "sell"}
    assert inst_streak(groups, by_day, d[:1])[0]["inst_streak"] is None  # one day is not a streak
    assert inst_streak(groups, by_day, [])[0]["inst_streak"] is None  # the shown day has no 法人 yet


def test_finder_api_attaches_inst_and_stops_the_streak_at_a_day_without_inst(tmp_path):
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore
    from openmarket.sources.institutional import InstRow

    s = HistoryStore(tmp_path / "h.sqlite")
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)]
    row = lambda c, close: {"code": c, "name": c.lower(), "market": "上市", "industry": "X", "kind": "stock", "close": close, "prev_close": close, "change": 0, "change_pct": 0}  # noqa: E731
    for d in days:
        s.save_day(d, [row("A", 100.0), row("B", 50.0)], datetime(d.year, d.month, d.day, 15))
    buy = lambda a, b: [InstRow("A", a, 0, 0, a), InstRow("B", b, 0, 0, b)]  # noqa: E731
    s.save_inst_day(days[0], buy(1000, 1000), datetime(2026, 3, 2, 17))  # buy
    # 3/3: no 法人 fetched at all — the streak must not jump over it
    s.save_inst_day(days[2], buy(3000, -1000), datetime(2026, 3, 4, 17))  # net buy
    s.save_inst_day(days[3], buy(2_000_000, -500_000), datetime(2026, 3, 5, 17))  # net buy
    key = FinderParams(groupings=("industry",)).key() + "|industry"
    s.save_finder(days[3], "known", key, {"date": days[3].isoformat(), "method": "known", "groups": [{"members": [{"code": "A", "name": "a"}, {"code": "B", "name": "b"}]}], "stats": {}})
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        g = client.get("/api/history/finder", params={"date": days[3].isoformat(), "method": "known", "stored_only": 1, "groupings": "industry"}).json()["groups"][0]
    assert g["inst"] == {"lots": 1500, "amount_100m": round((2e6 * 100 - 5e5 * 50) / 1e8, 2), "buyers": 1, "sellers": 1}
    assert [m["inst_net"] for m in g["members"]] == [2_000_000, -500_000]
    assert g["inst_streak"] == {"days": 2, "side": "buy"}  # 3/5 and 3/4 only: 3/3 has no 法人, so 3/2 does not count
    assert s.finder(days[3], "known", key)["groups"][0].get("inst") is None  # attached when served, never stored


def test_finder_api_splits_inst_into_foreign_trust_dealer(tmp_path):
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore
    from openmarket.sources.institutional import InstRow

    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 3, 2)
    row = lambda c: {"code": c, "name": c.lower(), "market": "上市", "industry": "X", "kind": "stock", "close": 10.0, "prev_close": 10.0, "change": 0, "change_pct": 0}  # noqa: E731
    s.save_day(d, [row("A"), row("B"), row("C")], datetime(2026, 3, 2, 15))
    s.save_inst_day(d, [InstRow("A", 1_500_000, -200_000, 10_000, 1_310_000), InstRow("B", 500_400, 0, -30_000, 470_400)], datetime(2026, 3, 2, 17))
    assert s.inst_split(d)["A"] == (1_500_000, -200_000, 10_000)
    key = FinderParams(groupings=("industry",)).key() + "|industry"
    members = lambda *cs: [{"code": c, "name": c.lower()} for c in cs]  # noqa: E731
    s.save_finder(d, "known", key, {"date": d.isoformat(), "method": "known", "groups": [{"members": members("A", "B")}, {"members": members("C")}], "stats": {}})
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        ab, c = client.get("/api/history/finder", params={"date": d.isoformat(), "method": "known", "stored_only": 1, "groupings": "industry"}).json()["groups"]
    assert ab["inst_split"] == {"foreign": 2000, "trust": -200, "dealer": -20}
    assert c["inst_split"] is None  # no 法人 rows for C
    assert ab["trust_streak"] is None  # one day is not a streak


def test_finder_api_trust_streak_counts_投信_alone(tmp_path):
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore
    from openmarket.sources.institutional import InstRow

    s = HistoryStore(tmp_path / "h.sqlite")
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    row = {"code": "A", "name": "a", "market": "上市", "industry": "X", "kind": "stock", "close": 10.0, "prev_close": 10.0, "change": 0, "change_pct": 0}
    for d in days:
        s.save_day(d, [row], datetime(d.year, d.month, d.day, 15))
    # 外資 sells every day (法人合計 sells) while 投信 buys three days running
    for d in days:
        s.save_inst_day(d, [InstRow("A", -900_000, 100_000, 0, -800_000)], datetime(d.year, d.month, d.day, 17))
    key = FinderParams(groupings=("industry",)).key() + "|industry"
    s.save_finder(days[-1], "known", key, {"date": days[-1].isoformat(), "method": "known", "groups": [{"members": [{"code": "A", "name": "a"}]}], "stats": {}})
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        g = client.get("/api/history/finder", params={"date": days[-1].isoformat(), "method": "known", "stored_only": 1, "groupings": "industry"}).json()["groups"][0]
    assert g["inst_streak"] == {"days": 3, "side": "sell"}
    assert g["trust_streak"] == {"days": 3, "side": "buy"}


def test_store_inst_queries_can_be_limited_to_some_codes(tmp_path):
    from datetime import datetime

    from openmarket.history.store import HistoryStore
    from openmarket.sources.institutional import InstRow

    s = HistoryStore(tmp_path / "h.sqlite")
    d1, d2 = date(2026, 3, 2), date(2026, 3, 3)
    for d in (d1, d2):
        s.save_inst_day(d, [InstRow("A", 1, 2, 0, 3), InstRow("B", 4, 5, 0, 9), InstRow("C", 7, 8, 0, 15)], datetime(d.year, d.month, d.day, 17))
    assert s.inst_net([d1, d2], ["A", "C"]) == {d1: {"A": 3, "C": 15}, d2: {"A": 3, "C": 15}}
    assert s.inst_trust([d2], {"B"}) == {d2: {"B": 5}}
    assert s.inst_net([d1]) == {d1: {"A": 3, "B": 9, "C": 15}}  # no codes: every stock, as before
    assert s.inst_net([d1], []) == {d1: {}} and s.inst_net([], ["A"]) == {}


def test_stock_card_endpoint_carries_inst(tmp_path):
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore
    from openmarket.sources.institutional import InstRow

    s = HistoryStore(tmp_path / "h.sqlite")
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    row = {"code": "A", "name": "a", "market": "上市", "industry": "X", "kind": "stock", "close": 10.0, "prev_close": 10.0, "change": 0, "change_pct": 0, "high": 10.0, "low": 10.0, "open": 10.0}
    for d in days:
        s.save_day(d, [row], datetime(d.year, d.month, d.day, 15))
    for d, f in zip(days, (-1000, 2_000_000, 1_500_400)):
        s.save_inst_day(d, [InstRow("A", f, 300_000, -20_000, f + 280_000)], datetime(d.year, d.month, d.day, 17))
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        inst = client.get("/api/history/stock/A", params={"to": "2026-03-04", "days": 2}).json()["inst"]
        assert inst == {"foreign": 1500, "trust": 300, "dealer": -20, "total": 1780, "streak": {"days": 3, "side": "buy"}, "trust_streak": {"days": 3, "side": "buy"}}
        s.save_day(date(2026, 3, 5), [row], datetime(2026, 3, 5, 15))  # 法人 not out yet for 3/5
        assert client.get("/api/history/stock/A", params={"to": "2026-03-05", "days": 2}).json()["inst"] is None


def test_margin_summary_and_endpoints(tmp_path):
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.finder import margin_summary
    from openmarket.history.store import HistoryStore
    from openmarket.sources.margin import MarginRow

    g, none = margin_summary([{"members": [{"code": "A"}, {"code": "B"}]}, {"members": [{"code": "X"}]}], {"A": (100, 150, 10, 5), "B": (200, 180, 0, 20)})
    assert g["margin"] == {"margin_chg": 30, "margin_bal": 330, "short_chg": 15, "short_bal": 25, "short_ratio": round(25 / 330 * 100, 2)}
    assert [m["margin_chg"] for m in g["members"]] == [50, -20] and none["margin"] is None

    s = HistoryStore(tmp_path / "h.sqlite")
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    row = {"code": "A", "name": "a", "market": "上市", "industry": "X", "kind": "stock", "close": 10.0, "prev_close": 10.0, "change": 0, "change_pct": 0, "high": 10.0, "low": 10.0, "open": 10.0}
    for d in days:
        s.save_day(d, [row], datetime(d.year, d.month, d.day, 15))
    s.save_margin_day(days[0], [MarginRow("A", 100, 90, 0, 0)], datetime(2026, 3, 2, 21))  # 融資減
    s.save_margin_day(days[1], [MarginRow("A", 90, 95, 0, 0)], datetime(2026, 3, 3, 21))  # 增
    s.save_margin_day(days[2], [MarginRow("A", 95, 120, 2, 6)], datetime(2026, 3, 4, 21))  # 增
    key = FinderParams(groupings=("industry",)).key() + "|industry"
    s.save_finder(days[2], "known", key, {"date": days[2].isoformat(), "method": "known", "groups": [{"members": [{"code": "A", "name": "a"}]}], "stats": {}})
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        fg = client.get("/api/history/finder", params={"date": days[2].isoformat(), "method": "known", "stored_only": 1, "groupings": "industry"}).json()["groups"][0]
        card = client.get("/api/history/stock/A", params={"to": days[2].isoformat(), "days": 2}).json()["margin"]
    assert fg["margin"] == {"margin_chg": 25, "margin_bal": 120, "short_chg": 4, "short_bal": 6, "short_ratio": 5.0}
    assert fg["margin_streak"] == {"days": 2, "side": "buy"}  # 融資連增 2 天 (3/3, 3/4); 3/2 decreased
    assert card == {**fg["margin"], "streak": {"days": 2, "side": "buy"}}


def test_theme_titles_put_the_headlines_naming_a_member_first():
    from openmarket.history.finder import news_themes

    day1 = {"PCB": {"codes": {"3354": 1}, "titles": [
        {"title": "PCB族群走高 欣興領軍", "source": "A", "url": "https://a/1"},
        {"title": "PCB族群紅綠互見 律勝連飆", "source": "B", "url": "https://b/2"},
    ]}}
    day2 = {"PCB": {"codes": {"3354": 2}, "titles": [{"title": "PCB族群紅綠互見 律勝連飆", "source": "B", "url": "https://b/2"}, {"title": "律勝再攻 PCB族群", "source": "C", "url": "https://c/3"}]}}
    (g,) = news_themes([{"members": [{"code": "3354", "name": "律勝"}, {"code": "1", "name": "甲"}]}], [day1, day2], ["2026-10-21", "2026-10-20"])
    titles = g["themes"][0]["titles"]
    assert [t["title"] for t in titles] == ["PCB族群紅綠互見 律勝連飆", "律勝再攻 PCB族群", "PCB族群走高 欣興領軍"]  # naming 律勝 first, no repeats
    assert titles[0]["names"] == ["律勝"] and titles[0]["date"] == "2026-10-21" and titles[1]["date"] == "2026-10-20"
    assert titles[2]["names"] == [] and titles[2]["url"] == "https://a/1"


def test_pool_ranks_gap_and_volume_ratio(tmp_path):
    from datetime import datetime

    from openmarket.history.finder import gap_pct, volume_ratio
    from openmarket.history.store import HistoryStore

    assert gap_pct({"open": 105, "prev_close": 100}) == pytest.approx(5.0) and gap_pct({"open": None, "prev_close": 100}) is None
    s = HistoryStore(tmp_path / "h.sqlite")
    days = [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]
    vols = {"A": (1000, 3000, 8000), "B": (5000, 5000, 5000), "C": (0, 0, 2000)}  # C: no trading before → no ratio
    for i, d in enumerate(days):
        s.save_day(d, [{"code": c, "name": c, "market": "上市", "industry": "X", "kind": "stock", "close": 10.0, "prev_close": 10.0,
                        "change": 0, "change_pct": 0, "volume_shares": v[i]} for c, v in vols.items()], datetime(d.year, d.month, d.day, 15))
    vr = volume_ratio(s, days[-1], 2)
    assert vr == {"A": pytest.approx(8000 / 2000), "B": pytest.approx(1.0)}
    rows = [
        {"code": "A", "kind": "stock", "change_pct": 1, "close": 10, "market_cap": 1, "volume_shares": 8000, "open": 10.1, "prev_close": 10},
        {"code": "B", "kind": "stock", "change_pct": 2, "close": 10, "market_cap": 1, "volume_shares": 5000, "open": 10.6, "prev_close": 10},
    ]
    p = FinderParams(pool_top=1, new_min_volume_lots=0, pool_sources=("gap", "vol_ratio"))
    assert candidate_pool(rows, {}, p, vr) == {"B": ["跳空"], "A": ["量比"]}
    assert candidate_pool(rows, {}, p) == {"B": ["跳空"]}  # no 量比 given: that ranking is empty


def test_pair_extras_same_direction_groups_finder_and_themes():
    from openmarket.history.finder import pair_extras

    rows = {"A": {"industry": "半導體業", "code": "A"}, "B": {"industry": "半導體業", "code": "B"}, "C": {"industry": "航運業", "code": "C"}}
    groupings = {"industry": lambda r: [r["industry"]], "custom": lambda r: ["記憶體"] if r["code"] in ("A", "B") else []}
    pct = {date(2026, 3, 2): {"A": 1, "B": 2, "C": -1}, date(2026, 3, 3): {"A": -1, "B": -3, "C": -1}, date(2026, 3, 4): {"A": 1, "B": -1, "C": 0}, date(2026, 3, 5): {"A": 2}}
    found = [("2026-03-04", "known", {"groups": [{"members": [{"code": "A"}, {"code": "B"}]}]}),
             ("2026-03-03", "new", {"groups": [{"members": [{"code": "A"}, {"code": "B"}, {"code": "C"}]}]}),
             ("2026-03-04", "known", {"groups": [{"members": [{"code": "B"}, {"code": "A"}]}]})]  # the same day twice: once
    news = [{"PCB": {"codes": {"A": 2, "B": 1}}, "航運": {"codes": {"C": 1}}, "AI": {"codes": {"A": 1, "B": 1}}}]
    ab, ac, bc = pair_extras(["A", "B", "C"], rows, groupings, pct, found, news)
    assert (ab["a"], ab["b"]) == ("A", "B")
    assert ab["same_dir"] == {"same": 2, "total": 3}  # 3/5: B has no row; 3/4: opposite
    assert ab["groups"] == [{"label": "官方產業", "group": "半導體業"}, {"label": "自訂族群", "group": "記憶體"}]
    assert ab["finder"] == [{"date": "2026-03-04", "method": "known"}, {"date": "2026-03-03", "method": "new"}]
    assert ab["themes"] == ["PCB", "AI"]  # more mentions first
    assert ac["same_dir"] == {"same": 1, "total": 3} and ac["groups"] == [] and ac["themes"] == []  # a 0% day is neither up nor down
    assert ac["finder"] == [{"date": "2026-03-03", "method": "new"}]


def test_group_evidence_majority_of_pairs():
    import random

    from openmarket.history.finder import Intraday, group_evidence, majority

    assert majority([True, True, False]) is True and majority([True, False]) is False and majority([None, None]) is None
    rnd = random.Random(5)
    idx = [rnd.gauss(0, 1) for _ in range(150)]
    flow = [rnd.gauss(0, 1) for _ in range(150)]
    feats = {c: Intraday(returns=[m + f + rnd.gauss(0, 0.3) for m, f in zip(idx, flow)]) for c in ("A", "B")}
    pool = {f"P{i}": [rnd.gauss(0, 1) for _ in range(150)] for i in range(40)}
    series = {"A": [1, 2, -1, -2, 1], "B": [2, 1, -1, -3, 2]}
    ev = group_evidence(["A", "B"], feats, {}, {"上市": idx}, {"A": "上市", "B": "上市"}, pool, series, "s")
    assert ev["excess"]["ok"] is True and ev["excess"]["r"] > 0.8
    assert ev["baseline"]["ok"] is True and ev["baseline"]["pct"] >= 95
    assert ev["burst"]["ok"] is None and ev["sync"]["ok"] is None  # no 1-minute bars: no data, not a ✗
    assert ev["samedir"] == {"ok": True, "share": 1.0}
    assert ev["shape"]["ok"] in (True, False) and ev["shape"]["dtw"] is not None  # shown, not among the counted checks
    from openmarket.history.linkage import EVIDENCE_CHECKS, evidence_yes
    assert "shape" not in EVIDENCE_CHECKS and evidence_yes({"evidence": {"shape": {"ok": True}}}) == 0


def test_branch_snapshot_skips_stored_members(tmp_path):
    import time as _t
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 3, 2)
    s.save_day(d, [{"code": c, "name": c, "market": "上市", "industry": "X", "kind": "stock", "close": 10.0, "prev_close": 10.0, "change": 0, "change_pct": 0} for c in ("A", "B")], datetime(2026, 3, 2, 15))
    s.save_finder(d, "known", "k", {"groups": [{"members": [{"code": "A"}, {"code": "B"}]}]})
    for c in ("A", "B"):  # already stored: the snapshot has nothing to fetch (no network here)
        s.save_branches(c, d, {"buy": [], "sell": []}, datetime(2026, 3, 2, 16))
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post("/api/branches/snapshot")
        for _ in range(50):
            st = client.get("/api/branches/status").json()["snapshot"]
            if st["last"]:
                break
            _t.sleep(0.05)
        assert st["total"] == 0 and st["errors"] == 0  # every day's members stored: nothing to do
        assert client.get("/api/branches/status").json()["queue"]["today"] == 0  # not a single HiStock page


def test_factor_choice_and_sector_returns():
    from openmarket.history.factors import factor_of, industry_sizes, sector_index_name, sector_returns

    assert sector_index_name("半導體業") == "半導體類指數"
    assert sector_index_name("水泥工業") == "水泥類指數"
    assert sector_index_name("電機機械") == "電機機械類指數"
    rows = [{"market": "上市", "industry": "半導體業", "kind": "stock"}] * 15 + [{"market": "上市", "industry": "水泥工業", "kind": "stock"}] * 7
    sizes = industry_sizes(rows)
    names = {"半導體類指數", "水泥類指數"}
    assert factor_of({"market": "上市", "industry": "半導體業"}, sizes, names) == ("半導體類指數", "半導體類")
    assert factor_of({"market": "上市", "industry": "水泥工業"}, sizes, names) == ("^TWII", "大盤")  # 7 stocks: the index is the group itself
    assert factor_of({"market": "上櫃", "industry": "半導體業"}, sizes, names) == ("IX0043.TWO", "櫃買")  # TPEx has no per-industry intraday
    assert factor_of({"market": "上市", "industry": "半導體業"}, sizes, names, "market") == ("^TWII", "大盤")
    d1, d2 = date(2026, 1, 2), date(2026, 1, 5)
    closes = {d1: {"X": [100, 101, None, 103]}, d2: {"X": [100, 100, 100, 99]}}
    r = sector_returns(closes, [d1, d2], "X")
    assert r[0] == pytest.approx(1.0) and r[1] is None and r[3] == 0 and r[5] == pytest.approx(-1.0)
    assert sector_returns({d1: closes[d1]}, [d1, d2], "X") is None  # a session missing: no half index


def test_pair_check_on_the_excess_r():
    up = [0.1, 0.3, -0.2, 0.5, 0.1] * 6
    noise = [(-1) ** i * 0.3 for i in range(30)]
    p = FinderParams()  # basis "sector", excess_r 0.4
    same = mk(60, rets=up)
    same.excess = up
    twin = mk(62, rets=[x * 1.1 for x in up])
    twin.excess = [x * 1.1 for x in up]
    out = pair_check(same, twin, p)
    assert out["ok"] and out["excess_r"] == pytest.approx(1.0)
    # the same raw R, but once the index is out nothing is left in common
    apart = mk(62, rets=[x * 1.1 for x in up])
    apart.excess = noise
    assert pair_check(same, apart, p)["reason"] == "扣指數後相關不足"
    assert pair_check(same, apart, FinderParams(intraday_basis="raw"))["ok"]  # the raw R still passes
    assert pair_check(same, mk(62, rets=up), p)["reason"] == "沒有指數分時"


def test_parse_sector_intraday_buckets():
    from openmarket.sources.sector_intraday import parse_sector_intraday

    payload = {"stat": "OK", "fields": ["時間", "發行量加權股價指數", "水泥類指數"], "data": [
        ["09:00:00", "100.00", "50.00"],  # the open: no bucket
        ["09:04:55", "101.00", "51.00"],
        ["09:05:00", "102.00", "52.00"],  # bucket 0 closes at 09:05:00
        ["09:05:05", "103.00", "53.00"],
        ["13:30:00", "110.00", "55.00"],
    ]}
    out = parse_sector_intraday(payload)
    assert out["水泥類指數"][0] == 52.0 and out["水泥類指數"][1] == 53.0 and out["水泥類指數"][53] == 55.0
    assert len(out["發行量加權股價指數"]) == 54
    assert parse_sector_intraday({"stat": "很抱歉"}) is None


def test_a_missing_session_keeps_the_days_aligned():
    d1, d2 = date(2026, 1, 2), date(2026, 1, 5)
    bars = [(f"{9 + k // 12:02d}:{(k % 12) * 5:02d}", 101, 99, 100 + k % 3, 100, 10) for k in range(54)]
    rows = {d1: {"prev_close": 100}, d2: {"prev_close": 100}}
    full = intraday_features({d1: bars, d2: bars}, rows, d2, P)
    gap = intraday_features({d2: bars}, rows, d2, P)  # no bars on d1 (suspended, or none at Yahoo)
    assert len(gap.returns) == len(full.returns) == 2 * 53
    assert all(x is None for x in gap.returns[:53]) and gap.returns[53:] == full.returns[53:]


def test_branch_backfill_takes_earlier_days_but_keeps_the_reserve(tmp_path):
    import time as _t
    from datetime import datetime

    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig
    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    old, new = date(2026, 3, 2), date(2026, 3, 3)
    for d in (old, new):
        s.save_day(d, [{"code": c, "name": c, "market": "上市", "industry": "X", "kind": "stock", "close": 10.0, "prev_close": 10.0, "change": 0, "change_pct": 0} for c in ("A", "B")], datetime(2026, 3, 3, 15))
        s.save_finder(d, "known", "k", {"groups": [{"members": [{"code": "A"}, {"code": "B"}]}]})
    for c in ("A", "B"):
        s.save_branches(c, new, {"buy": [], "sell": []}, datetime(2026, 3, 3, 16))  # the latest day is done

    class FakeQueue:  # no HiStock here: counts the pages asked for
        def __init__(self, used):
            self.today, self.asked = used, []

        def status(self):
            return {"today": self.today, "cap": 300, "blocked": False}

        async def fetch(self, code, day):
            self.asked.append((code, day))
            self.today += 1
            return {"buy": [], "sell": []}

    def run(used):
        app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
        app.state.branch_queue = q = FakeQueue(used)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            client.post("/api/branches/snapshot")
            for _ in range(100):
                if client.get("/api/branches/status").json()["snapshot"]["last"]:
                    break
                _t.sleep(0.05)
        return q.asked

    assert run(300 - 60) == []  # only the reserve left: the backfill waits for tomorrow
    assert run(0) == [("A", old), ("B", old)]  # the earlier day's members
    assert run(0) == []  # stored now



def test_laggards_are_the_known_group_s_stocks_left_behind():
    from openmarket.history.finder import laggards

    rows = [
        {"code": "A", "name": "甲", "kind": "stock", "industry": "X", "change_pct": 9.9, "volume_shares": 1, "turnover_yuan": 9},
        {"code": "B", "name": "乙", "kind": "stock", "industry": "X", "change_pct": 8.0, "volume_shares": 1, "turnover_yuan": 8},
        {"code": "C", "name": "丙", "kind": "stock", "industry": "X", "change_pct": 1.0, "volume_shares": 1, "turnover_yuan": 5},
        {"code": "D", "name": "丁", "kind": "stock", "industry": "X", "change_pct": 6.0, "volume_shares": 1, "turnover_yuan": 7},  # kept up
        {"code": "E", "name": "戊", "kind": "stock", "industry": "Y", "change_pct": 0.0, "volume_shares": 1, "turnover_yuan": 6},  # another group
        {"code": "F", "name": "己", "kind": "stock", "industry": "X", "change_pct": -2.0, "volume_shares": 0, "turnover_yuan": 0},  # did not trade
    ]
    groupings = {"industry": lambda r: [r["industry"]]}
    g = {"members": [{"code": "A"}, {"code": "B"}], "avg_pct": 8.95, "matches": [{"grouping": "industry", "label": "官方產業", "group": "X", "overlap": 1.0}]}
    assert laggards(g, rows, groupings) == {"label": "官方產業", "group": "X", "stocks": [{"code": "C", "name": "丙", "change_pct": 1.0}]}
    assert laggards({**g, "avg_pct": -3}, rows, groupings) is None  # a falling group: nothing to catch up with
    assert laggards({**g, "matches": [{**g["matches"][0], "overlap": 0.4}]}, rows, groupings) is None  # no real home
    big = rows + [{"code": f"Z{i}", "name": "z", "kind": "stock", "industry": "X", "change_pct": 0, "volume_shares": 1} for i in range(40)]
    assert laggards(g, big, groupings) is None  # 45 stocks in X: too broad to mean anything



def test_finder_days_any_version_takes_the_same_thresholds_newest_first(tmp_path):
    import json
    from datetime import datetime

    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    key = lambda v, r=0.4: json.dumps({"excess_r": r, "v": v}, sort_keys=True) + "|industry"  # noqa: E731
    s.save_finder(date(2026, 8, 3), "known", key(9), {"groups": [{"tag": "old"}]})
    s.save_finder(date(2026, 8, 4), "known", key(9), {"groups": [{"tag": "old"}]})
    s.save_finder(date(2026, 8, 4), "known", key(10), {"groups": [{"tag": "new"}]})
    s.save_finder(date(2026, 8, 4), "known", key(10, 0.5), {"groups": [{"tag": "other"}]})  # other thresholds
    got = s.finder_days("known", key(10), any_version=True)
    assert [(d.isoformat(), r["groups"][0]["tag"]) for d, r in got] == [("2026-08-03", "old"), ("2026-08-04", "new")]
    assert [d.isoformat() for d, _ in s.finder_days("known", key(10))] == ["2026-08-04"]
    assert [d.isoformat() for d, _ in s.finder_days("known", key(10), any_version=True, min_version=10)] == ["2026-08-04"]



def test_finder_days_takes_a_day_stored_without_news(tmp_path):
    import json

    from openmarket.history.store import HistoryStore

    s = HistoryStore(tmp_path / "h.sqlite")
    head = json.dumps({"excess_r": 0.4, "v": 10}, sort_keys=True)
    s.save_finder(date(2026, 8, 3), "known", head + "|chain,industry,news", {"groups": [{"tag": "with news"}]})
    s.save_finder(date(2026, 8, 4), "known", head + "|chain,industry", {"groups": [{"tag": "no news that day"}]})
    s.save_finder(date(2026, 8, 5), "known", head + "|group,industry", {"groups": [{"tag": "集團 ticked"}]})
    got = s.finder_days("known", head + "|chain,industry,news", any_version=True, groupings=("industry", "chain", "news"))
    assert [r["groups"][0]["tag"] for _, r in got] == ["with news", "no news that day"]  # not the one with 集團



def test_flag_chasers_marks_the_laggard_rising_on_volume_that_moves_with_the_group():
    from openmarket.history.finder import flag_chasers

    base = [0.5, -0.3, 1.0, -0.8, 0.2, 0.9, -0.4, 0.3, -1.1, 0.6, 0.7, -0.2]
    series = {"A": base, "B": [x * 1.1 for x in base], "C": base[:], "D": [(-1) ** i for i in range(12)]}
    g = {"members": [{"code": "A"}, {"code": "B"}], "laggards": {"label": "細產業", "group": "X",
         "stocks": [{"code": "C", "change_pct": 1.0}, {"code": "D", "change_pct": 1.0}, {"code": "E", "change_pct": -1.0}]}}
    out = flag_chasers(g, {"C": 2.0, "D": 2.0, "E": 3.0}, series)
    assert [s["chase"] for s in out["stocks"]] == [True, False, False]  # D does not move with them; E fell


def test_optional_settings_keep_the_stored_key_at_their_default():
    from dataclasses import replace

    from openmarket.history.finder import FinderParams

    k = FinderParams().key()
    assert "excess_r_mode" not in k  # results stored before the setting existed keep their key
    assert replace(FinderParams(), excess_r_mode="adaptive").key() != k


def test_adaptive_excess_r_from_cross_industry_random_pairs():
    import random

    from openmarket.history.finder import ADAPTIVE_BOUNDS, FinderParams, Intraday, adaptive_excess_r

    rnd = random.Random(1)
    feats, by_code = {}, {}
    for i in range(60):
        f = Intraday(returns=[rnd.gauss(0, 1) for _ in range(150)])
        f.excess = f.returns
        feats[f"S{i}"] = f
        by_code[f"S{i}"] = {"industry": f"I{i % 6}"}
    thr = adaptive_excess_r(feats, by_code, FinderParams(), "seed")
    assert ADAPTIVE_BOUNDS[0] <= thr <= ADAPTIVE_BOUNDS[1]  # noise alone: about the 99.5% of |r| for 150 points, clamped
    assert adaptive_excess_r(dict(list(feats.items())[:10]), by_code, FinderParams(), "seed") is None  # too few stocks
