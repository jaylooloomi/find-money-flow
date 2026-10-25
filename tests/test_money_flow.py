import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from openmarket.config import TAIPEI
from openmarket.history.ingest import HistoryCollector, build_rows
from openmarket.history.metrics import (
    clv,
    flow_baseline,
    flow_day,
    group_flows,
    industry_grouping,
    mapping_grouping,
    rotation,
    zscore,
)
from openmarket.history.store import HistoryStore
from openmarket.models import ParseError, StockInfo
from openmarket.sources.institutional import InstRow, parse_tpex_inst, parse_twse_inst
from openmarket.sources.official import DailyRow

FIX = Path(__file__).parent / "fixtures"


# ---- formulas --------------------------------------------------------------------------------

def test_clv_and_zscore():
    assert clv({"high": 110, "low": 100, "close": 110}) == 1
    assert clv({"high": 110, "low": 100, "close": 100}) == -1
    assert clv({"high": 110, "low": 100, "close": 105}) == 0
    assert clv({"high": 100, "low": 100, "close": 100}) == 0
    assert clv({"high": None, "low": 1, "close": 1}) == 0
    assert zscore(5, [1, 1, 1, 1]) == pytest.approx((5 - 1) / 0.05)  # std floor
    assert zscore(3, [1, 2, 3]) == pytest.approx((3 - 2) / (2 / 3) ** 0.5)
    assert zscore(3, [1, 2]) is None  # too little history


def row(code, close, turnover, high, low, volume=1000.0, cap=1e10, kind="stock", industry="X"):
    return {"code": code, "kind": kind, "industry": industry, "close": close, "high": high, "low": low,
            "turnover_yuan": turnover, "volume_shares": volume, "market_cap": cap}


def test_flow_day_sums_per_group_once_per_code():
    rows = [row("a", 110, 100, 110, 100), row("b", 100, 300, 110, 100, industry="Y"), row("e", 10, 999, 10, 10, kind="etf")]
    grouping = mapping_grouping({"a": ["G", "G"], "b": ["G"]})
    out = flow_day(rows, grouping, {"a": 10})
    assert out["market"] == 400  # ETF excluded
    g = out["groups"]["G"]
    assert g["turnover"] == 400 and g["mf"] == 100 * 1 + 300 * -1 and g["inst"] == 10 * 110
    assert g["shares"] == pytest.approx(1e10 / 110 + 1e10 / 100)


# ---- store-backed metrics ----------------------------------------------------------------------

INFOS = {c: StockInfo(c, c, ind, "上市", "stock") for c, ind in (("1", "熱門"), ("2", "冷門"), ("3", "冷門"))}


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "h.sqlite")
    now = datetime(2026, 10, 23, 16, tzinfo=TAIPEI)
    days = [date(2026, 9, d) for d in range(1, 26) if date(2026, 9, d).weekday() < 5]
    for i, d in enumerate(days):
        hot_spike = i == len(days) - 1
        rows = [
            DailyRow("1", "1", "上市", 100, 1, 99, 101, 99, 1000, 9e8, 10**7),
            DailyRow("2", "2", "上市", 50, 1, 49, 50, 48, 1000, (5e8 if hot_spike else 1e8) * (1 + (i % 3) * 0.05), 10**7),
            DailyRow("3", "3", "上市", 20, 0, 20, 20, 20, 1000, 1e7, 10**7),
        ]
        s.save_day(d, build_rows(rows, INFOS, {}), now)
    s.days_list = days
    return s


def test_group_flows_flags_a_spike_and_inst_appears_when_stored(store):
    last = store.days_list[-1]
    out = group_flows(store, last, industry_grouping)
    cold = out["冷門"]
    assert cold["z20"] > 2 and cold["share"] > 30  # 5e8 of ~1.4e9
    assert out["熱門"]["z20"] < 0
    assert cold["mf"] is not None and cold["inst_net"] is None  # no institutional data yet
    assert cold["turnover_rate"] == pytest.approx(2000 / (2 * 10**7) * 100)
    store.save_inst_day(last, [InstRow("2", 1000, 0, 0, 1000)], datetime.now(TAIPEI))
    cold = group_flows(store, last, industry_grouping)["冷門"]
    assert cold["inst_net"] == 1000 * 50 and cold["inst_ratio"] > 0
    assert group_flows(store, date(2026, 9, 27), industry_grouping) == {}  # not a stored day


def test_baseline_and_rotation(store):
    base = flow_baseline(store, industry_grouping)
    assert base["as_of"] == store.days_list[-1].isoformat() and set(base["groups"]) == {"熱門", "冷門"}
    assert base["groups"]["冷門"]["m5"] > base["groups"]["冷門"]["m20"]  # the spike day is in the last 5
    before = flow_baseline(store, industry_grouping, before=store.days_list[-1])
    assert before["as_of"] == store.days_list[-2].isoformat()  # the live day is not its own baseline
    r = rotation(store, 5, "share_z", 5)
    assert r["metric"] == "share_z" and len(r["dates"]) == 5 and r["groups"][0]["name"] == "冷門"
    assert r["groups"][0]["values"][-1] > 2


# ---- 三大法人 sources + collector ----------------------------------------------------------------

def test_parse_institutional_fixtures():
    twse = parse_twse_inst(json.loads((FIX / "inst_twse_20261022.json").read_text(encoding="utf-8")))
    first = twse[0]
    assert first.code == "1101" and first.total == 33359013 and first.trust == 4726000
    assert first.foreign == 25549325 and first.dealer == 3083688
    tpex = parse_tpex_inst(json.loads((FIX / "inst_tpex_20261022.json").read_text(encoding="utf-8")))
    assert tpex[0].code == "00411A" and tpex[0].foreign == 751700 and tpex[0].dealer == 3610418 and tpex[0].total == 4362118
    assert parse_twse_inst({"stat": "查詢日期大於可查詢最大日期，請重新查詢!"}) is None
    assert parse_tpex_inst({"tables": [{"data": []}]}) is None
    with pytest.raises(ParseError):
        parse_twse_inst({"stat": "OK", "data": [["1101", "x"]]})


def test_collector_backfills_inst_and_waits_for_today(tmp_path):
    s = HistoryStore(tmp_path / "h.sqlite")
    now = datetime(2026, 10, 22, 17, tzinfo=TAIPEI)
    for d in (date(2026, 10, 21), date(2026, 10, 22)):
        s.save_day(d, build_rows([DailyRow("1", "1", "上市", 100, 1, 99, 101, 99, 1000, 9e8, 10**7)], INFOS, {}), now)
    asked = []

    async def fetch_inst(d):
        asked.append(d)
        return None if d == date(2026, 10, 22) else [InstRow("1", 5, 0, 0, 5)]

    async def no_sleep(_):
        return None

    c = HistoryCollector(store=s, fetch_twse=None, fetch_tpex=None, reference=lambda: ({}, {}), clock=lambda: now,
                         sleep=no_sleep, fetch_inst=fetch_inst)
    asyncio.run(c.collect_inst())
    assert sorted(asked) == [date(2026, 10, 21), date(2026, 10, 22)]
    assert s.inst_dates() == {date(2026, 10, 21)}  # today's empty answer is retried later
    assert s.inst_net([date(2026, 10, 21)])[date(2026, 10, 21)] == {"1": 5}


# ---- 融資融券 sources + collector ----------------------------------------------------------------

def test_parse_margin_fixtures():
    from openmarket.sources.margin import parse_tpex_margin, parse_twse_margin

    twse = {r.code: r for r in parse_twse_margin(json.loads((FIX / "twse_margin_20261022.json").read_text(encoding="utf-8")))}
    assert (twse["2330"].margin_prev, twse["2330"].margin_bal, twse["2330"].short_prev, twse["2330"].short_bal) == (35330, 35472, 6, 15)
    assert (twse["2492"].margin_prev, twse["2492"].margin_bal, twse["2492"].short_bal) == (17010, 18187, 1596)
    tpex = {r.code: r for r in parse_tpex_margin(json.loads((FIX / "tpex_margin_20261022.json").read_text(encoding="utf-8")))}
    assert (tpex["6148"].margin_prev, tpex["6148"].margin_bal, tpex["6148"].short_prev, tpex["6148"].short_bal) == (2494, 2667, 245, 395)
    assert parse_twse_margin({"stat": "很抱歉，沒有符合條件的資料", "tables": []}) is None  # holiday
    assert parse_tpex_margin({"stat": "ok", "tables": [{"data": []}]}) is None
    with pytest.raises(ParseError):
        parse_twse_margin({"stat": "OK", "tables": [{"title": "融資融券彙總 (全部)", "data": [["2330", "x"]]}]})


def test_collector_backfills_margin_and_waits_for_today(tmp_path):
    from openmarket.sources.margin import MarginRow

    s = HistoryStore(tmp_path / "h.sqlite")
    now = datetime(2026, 10, 22, 17, tzinfo=TAIPEI)
    for d in (date(2026, 10, 21), date(2026, 10, 22)):
        s.save_day(d, build_rows([DailyRow("1", "1", "上市", 100, 1, 99, 101, 99, 1000, 9e8, 10**7)], INFOS, {}), now)

    async def fetch_margin(d):
        return None if d == date(2026, 10, 22) else [MarginRow("1", 100, 120, 5, 3)]

    async def no_sleep(_):
        return None

    c = HistoryCollector(store=s, fetch_twse=None, fetch_tpex=None, reference=lambda: ({}, {}), clock=lambda: now,
                         sleep=no_sleep, fetch_margin=fetch_margin)
    asyncio.run(c.collect_margin())
    assert s.margin_dates() == {date(2026, 10, 21)}  # today's empty answer is retried later
    assert s.margin([date(2026, 10, 21)])[date(2026, 10, 21)] == {"1": (100, 120, 5, 3)}
    assert s.margin([date(2026, 10, 21)], ["2"]) == {date(2026, 10, 21): {}}


# ---- TWSE 類股指數 (C's 電子／金融／傳產 rows) -------------------------------------------------------

def test_parse_sector_indices_fixture():
    from openmarket.sources.sector_index import parse_sector_indices

    got = parse_sector_indices(json.loads((FIX / "twse_sector_20261022.json").read_text(encoding="utf-8")))
    assert got == {"電子": (2866.47, -61.72), "金融": (3742.82, 23.15), "傳產": (17328.46, -282.38)}
    assert parse_sector_indices({"stat": "OK"}) is None  # holiday: no tables
    assert parse_sector_indices({"stat": "查詢日期大於今日"}) is None


def test_collector_backfills_sectors_and_c_shows_them(tmp_path):
    from openmarket.history.metrics import rotation

    s = HistoryStore(tmp_path / "h.sqlite")
    now = datetime(2026, 10, 22, 17, tzinfo=TAIPEI)
    infos = {"E": StockInfo("E", "e", "半導體業", "上市", "stock"), "F": StockInfo("F", "f", "金融保險業", "上市", "stock"),
             "P": StockInfo("P", "p", "塑膠工業", "上市", "stock"), "O": StockInfo("O", "o", "半導體業", "上櫃", "stock")}
    for d in (date(2026, 10, 21), date(2026, 10, 22)):
        rows = [DailyRow(c, c, infos[c].market, 100, 1, 99, 101, 99, 1000, 9e8, 10**7) for c in infos]
        s.save_day(d, build_rows(rows, infos, {}), now)
    asked = []

    async def fetch_sectors(d):
        asked.append(d)
        return None if d == date(2026, 10, 22) else {"電子": (1010.0, 10.0), "金融": (99.0, -1.0), "傳產": (500.0, 0.0)}

    async def no_sleep(_):
        return None

    c = HistoryCollector(store=s, fetch_twse=None, fetch_tpex=None, reference=lambda: ({}, {}), clock=lambda: now,
                         sleep=no_sleep, fetch_sectors=fetch_sectors)
    asyncio.run(c.collect_sectors())
    assert s.sector_dates("電子") == {date(2026, 10, 21)}  # today's empty answer is retried later
    r = rotation(s, 2, "avg_pct", 5.0, up_to=date(2026, 10, 22))
    names = [b["name"] for b in r["benchmarks"]]
    assert names == ["大盤（加權）", "櫃買（OTC）", "電子（上市）", "金融（上市）", "傳產（上市）"]
    sector = {b["name"]: b for b in r["benchmarks"]}
    assert sector["電子（上市）"]["values"] == [1.0, None]  # the official index (10 / 1000); 10/22 not out: empty, not the stocks
    assert sector["金融（上市）"]["values"][0] == -1.0 and "金融保險業" in sector["金融（上市）"]["hint"]
    breadth = {b["name"]: b for b in rotation(s, 2, "breadth", 5.0, up_to=date(2026, 10, 22))["benchmarks"]}
    assert breadth["電子（上市）"]["values"][1] is not None  # other metrics: the sector's 上市 common stocks


def test_parse_daytrade_and_ratios(tmp_path):
    from openmarket.history.linkage import daytrade_ratios
    from openmarket.sources.daytrade import parse_daytrade

    payload = {"stat": "OK", "tables": [{"title": "當日沖銷交易統計資訊", "data": [["1", "2", "3", "4", "5", "6"]]},
                                        {"title": "115年10月22日 當日沖銷交易標的及成交量值", "data": [["2330", "台積電", "", "8,392,000", "1", "2"], ["2303", "聯電", "Y", "614,000", "1", "2"]]}]}
    assert parse_daytrade(payload) == {"2330": 8392000, "2303": 614000}
    assert parse_daytrade({"stat": "很抱歉，沒有符合條件的資料!"}) is None
    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 10, 22)
    s.save_day(d, build_rows([DailyRow("1", "1", "上市", 100, 1, 99, 101, 99, 10_000_000, 9e8, 10**7)], INFOS, {}), datetime(2026, 10, 22, 15, tzinfo=TAIPEI))
    s.save_daytrade_day(d, {"1": 4_500_000}, datetime(2026, 10, 22, 19, tzinfo=TAIPEI))
    assert daytrade_ratios(s, [d], ["1", "2"]) == {"1": {d: 45.0}, "2": {}}
    assert s.daytrade_dates() == {d}


def test_ex_rights_fill_the_missing_prev_close(tmp_path):
    from openmarket.sources.exright import parse_tpex_exright, parse_twse_exright

    twse = {"stat": "OK", "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價"],
            "data": [["115年08月03日", "2367", "燿華", "36.30", "36.10"]]}
    tpex = {"tables": [{"fields": ["除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價"], "data": [["115/08/03", "3306", "鼎天  ", "43.75", "42.05"]]}]}
    assert parse_twse_exright(twse) == {date(2026, 8, 3): {"2367": 36.1}}
    assert parse_tpex_exright(tpex) == {date(2026, 8, 3): {"3306": 42.05}}
    assert parse_twse_exright({"stat": "很抱歉"}) == {}

    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 8, 3)
    # "1": ex-dividend (TWSE 'X': no change), "2": an ordinary day
    s.save_day(d, build_rows([DailyRow("1", "1", "上市", 38.0, None, 36.1, 38.65, 36.0, 1e6, 3.8e7),
                              DailyRow("2", "2", "上市", 10.0, 0.5, 9.5, 10, 9.5, 1e6, 1e7)], INFOS, {}), datetime(2026, 8, 3, 15, tzinfo=TAIPEI))
    assert s.ex_rights_gaps() == [d]

    async def fetch(a, b):
        assert (a, b) == (d, d)
        return {d: {"1": 36.1, "2": 1.0}}

    c = HistoryCollector(store=s, fetch_twse=None, fetch_tpex=None, reference=lambda: ({}, {}), sleep=lambda _: asyncio.sleep(0), fetch_exrights=fetch)
    asyncio.run(c.collect_exrights())
    rows = {r["code"]: r for r in s.rows([d])[d]}
    assert rows["1"]["prev_close"] == 36.1 and rows["1"]["change"] == 1.9 and rows["1"]["change_pct"] == round((38 / 36.1 - 1) * 100, 4)
    assert rows["2"]["prev_close"] == 9.5  # a row that had its 昨收 is left alone
    assert s.ex_rights_gaps() == []  # looked up: not asked again


def test_tpex_daytrade_beside_twse(tmp_path):
    from openmarket.history.linkage import daytrade_ratios
    from openmarket.sources.daytrade import parse_tpex_daytrade

    payload = {"tables": [
        {"title": "現股當沖交易統計資訊", "fields": ["當日沖銷交易總成交股數"], "data": [["551,652,000"]]},
        {"title": "", "fields": ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記", "當日沖銷交易成交股數", "當日沖銷交易買進成交金額", "當日沖銷交易賣出成交金額"],
         "data": [["2", "乙", " ", "3,000,000", "1", "2"]]},
    ]}
    assert parse_tpex_daytrade(payload) == {"2": 3000000}
    assert parse_tpex_daytrade({"tables": [{"fields": ["證券代號", "當日沖銷交易成交股數"], "data": []}]}) is None
    s = HistoryStore(tmp_path / "h.sqlite")
    d = date(2026, 10, 22)
    s.save_day(d, build_rows([DailyRow("1", "1", "上市", 100, 1, 99, 101, 99, 10_000_000, 9e8), DailyRow("2", "2", "上櫃", 50, 1, 49, 51, 49, 6_000_000, 3e8)], INFOS, {}), datetime(2026, 10, 22, 15, tzinfo=TAIPEI))
    s.save_daytrade_day(d, {"1": 4_500_000}, datetime(2026, 10, 22, 19, tzinfo=TAIPEI))
    s.save_daytrade_tpex_day(d, parse_tpex_daytrade(payload), datetime(2026, 10, 22, 19, tzinfo=TAIPEI))
    assert daytrade_ratios(s, [d], ["1", "2"]) == {"1": {d: 45.0}, "2": {d: 50.0}}  # TWSE's row kept, TPEx's added
    assert s.daytrade_tpex_tried() == {d}


def test_quotes_api_change_and_volume_ratio(tmp_path):
    from fastapi.testclient import TestClient

    from openmarket.api import create_app
    from openmarket.config import AppConfig

    s = HistoryStore(tmp_path / "h.sqlite")
    days = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    for d, vol in zip(days, (1e6, 1e6, 3e6)):
        s.save_day(d, build_rows([DailyRow("1", "1", "上市", 10, -0.5, 10.5, 10.5, 10, vol, vol * 10)], INFOS, {}), datetime(2026, 10, 21, 15, tzinfo=TAIPEI))
    app = create_app(AppConfig(data_dir=tmp_path), start_polling=False, start_history=False, start_concepts=False, history_store=s)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        q = client.get("/api/history/quotes", params={"date": "2026-10-21", "codes": "1,9"}).json()["quotes"]
    assert q == {"1": {"change_pct": pytest.approx(-4.7619, abs=1e-3), "vol_ratio": 3.0,  # 3M ÷ the 1M average; "9" unknown
                       "gap_pct": 0.0, "intraday_pct": -4.76}}  # opened at 昨收 10.5, closed 10



def test_daytrade_group_average_as_shown():
    from openmarket.history.linkage import daytrade_group

    g = daytrade_group({"1": 50.0, "2": 49.9, "3": None})  # 49.95 → 50.0 as shown: 🔥; "3" has no data
    assert g == {"by_code": {"1": 50.0, "2": 49.9}, "avg": 50.0, "hot": True, "base": {}}
    assert daytrade_group({"1": 49.9, "2": 49.9})["hot"] is False
    assert daytrade_group({"1": None}) == {"by_code": {}, "avg": None, "hot": False, "base": {}}
    usual = daytrade_group({"1": 62.0, "2": 40.0}, {"1": [30.0, 32.0], "2": [], "3": [10.0]})
    assert usual["base"] == {"1": 31.0}  # "2" has no history, "3" no data today



def test_next_day_stats_hot_apart_from_the_rest():
    from openmarket.history.linkage import next_day_stats

    d1, d2 = date(2026, 8, 3), date(2026, 8, 4)
    rows = {d2: {"1": {"change_pct": -2.0, "open": 105, "close": 98, "prev_close": 100}, "2": {"change_pct": 4.0, "open": 101, "close": 104, "prev_close": 100},
                 "3": {"change_pct": 1.0, "open": 100, "close": 101, "prev_close": 100}}}
    out = next_day_stats([(d1, [["1", "2"], ["3"]])], lambda d, cs: "1" in cs, lambda d: rows.get(d, {}), {d1: d2}.get, lambda d: 1.0)
    assert out["hot"] == {"groups": 1, "days": 1, "stocks": 2, "gap": 3.0, "close": 1.0, "excess": 0.0,
                          "intraday": round(((98 / 105 - 1) * 100 + (104 / 101 - 1) * 100) / 2, 2), "fade": 50.0, "down": 50.0}
    assert out["rest"]["stocks"] == 1 and out["rest"]["fade"] == 0.0
    assert next_day_stats([(d2, [["1"]])], lambda d, cs: True, lambda d: {}, {}.get, lambda d: None)["hot"]["stocks"] == 0  # no next day



def test_hot_class_and_parents():
    from openmarket.history.linkage import hot_class, next_day_stats

    assert hot_class({"hot": False, "by_code": {}, "base": {}}) == "rest"
    assert hot_class({"hot": True, "by_code": {"1": 62.0, "2": 55.0}, "base": {"1": 25.0, "2": 30.0}}) == "surge"  # 2.48× and 1.83×
    assert hot_class({"hot": True, "by_code": {"1": 62.0}, "base": {"1": 60.0}}) == "habitual"
    assert hot_class({"hot": True, "by_code": {"1": 62.0}, "base": {}}) == "habitual"  # no usual known
    # 🔥 as the sum of its kinds, the days a union (the two kinds on different days)
    d1, d2, d3 = date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)
    row = {"change_pct": 1.0, "open": 100, "close": 101, "prev_close": 100}
    out = next_day_stats([(d1, [["1"]]), (d2, [["2"]])], lambda d, cs: "surge" if cs == ["1"] else "habitual",
                         lambda d: {"1": row, "2": row}, {d1: d2, d2: d3}.get, lambda d: 0.0, parents={"surge": "hot", "habitual": "hot"})
    assert out["hot"]["groups"] == 2 and out["hot"]["days"] == 2 and out["surge"]["days"] == 1 and out["habitual"]["days"] == 1



def test_evidence_classes_of_stored_groups():
    from openmarket.history.linkage import evidence_class, evidence_yes

    g = {"evidence": {"excess": {"ok": True}, "baseline": {"ok": True}, "burst": {"ok": None}, "sync": {"ok": False}, "samedir": {"ok": True}}}
    assert evidence_yes(g) == 3 and evidence_class(3) == "ev2"
    assert evidence_yes({}) == 0 and evidence_class(0) == "ev0" and evidence_class(5) == "ev4"



def test_pullbacks_first_touch_of_the_10_day_average_after_a_tight_sync():
    from datetime import timedelta

    from openmarket.history.linkage import is_tight_sync, pullbacks

    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(25)]
    up = {d: 100 + i for i, d in enumerate(days)}  # rising: always above its average
    dip = dict(up)
    dip[days[-1]] = 100  # falls under its 10-day average on the last day
    dip_early = dict(dip)
    dip_early[days[-3]] = 100  # … but had touched it two days before already
    closes = {"A": dip, "B": up, "C": dip_early}
    members = [{"code": c, "name": n} for c, n in (("A", "甲"), ("B", "乙"), ("C", "丙"))]
    got = pullbacks(days[-1], days, [(days[-5], members)], closes)
    assert [x["code"] for x in got] == ["A"]  # B never pulled back, C's first touch was earlier
    assert got[0]["with"] == ["乙", "丙"] and got[0]["gap_pct"] < 0
    assert pullbacks(days[-1], days, [(days[0], members)], closes) == []  # the sync is older than 10 days
    assert is_tight_sync({"excess_r": 0.65}) and not is_tight_sync({"excess_r": 0.5, "evidence": {}})
