import { test } from 'node:test';
import assert from 'node:assert/strict';
import { concHtml, goneReading, highlights, hotByMultiple, pullbackHtml, highlightsHtml, isSurge, shapeHtml, shapeList, hotHtml, hotNextDay, laggardsHtml, todayMove, mergeGroups, motiveOf, reportText, trackHtml, trackRows, watchOf } from '../../static/js/report.js';
import { groupEvidenceItems } from '../../static/js/evidence.js';

const m = (code, name = code) => ({ code, name, change_pct: 5 });
// an N group with the given checks passing (the server's pair majorities)
const grp = (codes, ok = {}, extra = {}) => ({
  members: codes.map((c) => m(c)),
  evidence: Object.fromEntries(['excess', 'baseline', 'burst', 'sync', 'samedir'].map((k) => [k, { ok: ok[k] ?? null }])),
  ...extra,
});

test('mergeGroups: one entry per member set across 找法1/2, the better supported kept, by support', () => {
  const a = grp(['1', '2'], { excess: true });
  const b = grp(['2', '1'], { excess: true, baseline: true, burst: true }); // the same pair, found by 找法2
  const c = grp(['3', '4'], { excess: true, baseline: true });
  const list = mergeGroups({ groups: [a, c] }, { groups: [b] });
  assert.deepEqual(list.map((x) => [x.key, x.method, x.ev.yes]), [['1,2', 'new', 3], ['3,4', 'known', 2]]);
  assert.deepEqual(mergeGroups(null, null), []);
});

test('motiveOf: the readings come from the evidence, strongest first, at most two', () => {
  const g = grp(['1', '2'], {}, { branches: { ok: true, common: [{ name: '美商高盛', ranks: { 1: 2, 2: 2 } }] }, inst: { lots: 1200 }, daytrade: { hot: true } });
  const r = motiveOf(g, groupEvidenceItems(g));
  assert.deepEqual(r.map((x) => x.label), ['同分點布局', '當沖偏高（平常就高）']); // no usual known: not 突然湧入
  const surge = grp(['1', '2'], {}, { daytrade: { hot: true, by_code: { 1: 60, 2: 60 }, base: { 1: 30, 2: 40 } } });
  assert.match(motiveOf(surge, groupEvidenceItems(surge))[0].text, /當沖比是平常的 1\.8 倍/); // 2× and 1.5×
  assert.match(r[0].text, /美商高盛 同時買超 2 檔/);
  const bare = grp(['1', '2']);
  assert.deepEqual(motiveOf(bare, groupEvidenceItems(bare)).map((x) => x.label), ['只有價格同步']);
  const basket = grp(['1', '2'], { burst: true });
  assert.deepEqual(motiveOf(basket, groupEvidenceItems(basket)).map((x) => x.label), ['一籃子下單']);
});

test('watchOf: what would confirm the reading tomorrow', () => {
  assert.deepEqual(watchOf(grp(['1', '2']), 0), ['是否再一起動']);
  const g = grp(['1', '2'], {}, { branches: { ok: true, common: [{ name: '凱基-台北', ranks: {} }] }, inst: { lots: 5 } });
  assert.deepEqual(watchOf(g, 2), ['是否連續第 3 天同步', '凱基-台北 是否續買', '法人是否續買']);
});

test('trackRows: streaks, new, back again, gone, and days not computed', () => {
  const day = (...groups) => mergeGroups({ groups }, null);
  const ab = (n) => grp(['A', 'B'], Object.fromEntries(['excess', 'baseline', 'burst', 'sync'].slice(0, n).map((k) => [k, true])));
  const cd = grp(['C', 'D'], { excess: true });
  const ef = grp(['E', 'F'], { excess: true });
  const gh = grp(['G', 'H'], { excess: true });
  // oldest → report day; day 2 not computed (null)
  const rows = trackRows([day(gh), null, day(ab(1), cd), day(ab(2), cd), day(ab(3), ef, gh)]);
  const by = Object.fromEntries(rows.map((r) => [r.names, r]));
  assert.equal(by['A、B'].status, '連 3 天・證據變強');
  assert.deepEqual(by['A、B'].cells, [null, undefined, 1, 2, 3]);
  assert.equal(by['A、B'].streak, 2);
  assert.equal(by['E、F'].status, '新出現');
  assert.equal(by['G、H'].status, '再度出現');
  assert.equal(by['C、D'].status, '消失（資金撤出？）');
  assert.deepEqual(rows.map((r) => r.names), ['A、B', 'E、F', 'G、H', 'C、D']); // today's first, then the gone
  const html = trackHtml(['2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22'], rows);
  assert.match(html, /<td class="muted" title="那天還沒計算">…<\/td>/);
  assert.match(html, /<td class="l muted">消失（資金撤出？）<\/td>/);
});

test('reportText: the copy-paste version', () => {
  const g = grp(['1', '2'], { excess: true, baseline: true }, { themes: [{ term: 'PCB' }] });
  g.members = [m('1', '華通'), m('2', 'TPK-KY')];
  const list = mergeGroups({ groups: [g] }, null);
  const text = reportText('2026-10-22', [{ name: '大盤', change_pct: -1.81 }], list, trackRows([list]));
  assert.match(text, /^2026-10-22 盤後報告\n大盤 -1\.81%/);
  assert.match(text, /1\. 華通、TPK-KY（證據 3\/3）/);
  assert.match(text, /題材輪動：同題材：PCB/);
  assert.match(text, /明天觀察：是否再一起動/);
});

test('concHtml: each member with 分點 stored, signed; nothing without', () => {
  const g = { members: [{ code: '1', name: '甲' }, { code: '2', name: '乙' }], concentration: { 1: 12.3, 2: -4 } };
  assert.match(concHtml(g), /籌碼集中度<\/span> 甲 <span class="up">\+12\.3%<\/span>、乙 <span class="down">-4\.0%<\/span>/);
  assert.equal(concHtml({ members: [] }), '');
  assert.equal(concHtml({ members: [], concentration: null }), '');
});

test('goneReading: 爆量收黑 by most members reads as 出貨; otherwise they just stopped moving together', () => {
  const ms = [{ code: '1' }, { code: '2' }];
  assert.equal(goneReading(ms, { 1: { change_pct: -5, vol_ratio: 2.4 }, 2: { change_pct: -3, vol_ratio: 2.1 } }), '疑似出貨');
  assert.equal(goneReading(ms, { 1: { change_pct: -5, vol_ratio: 2.4 }, 2: { change_pct: 1, vol_ratio: 0.8 } }), '疑似出貨'); // half of them
  assert.equal(goneReading(ms, { 1: { change_pct: -2, vol_ratio: 1.1 }, 2: { change_pct: 1, vol_ratio: 0.8 } }), '不再同步');
  assert.equal(goneReading(ms, {}), null);
});

test('laggardsHtml: the known group and its stocks left behind, each opening its card', () => {
  const g = { laggards: { label: '官方產業', group: '半導體業', stocks: [{ code: '3', name: '丙', change_pct: 0.5 }] } };
  assert.match(laggardsHtml(g), /同族群未跟上（官方產業：半導體業）<\/span> <button type="button" class="rp-stock" data-code="3"/);
  assert.equal(laggardsHtml({}), '');
});

test('reportText carries 籌碼集中度, 補漲候選 and 疑似出貨 too', () => {
  const g = grp(['1', '2'], { excess: true }, {
    concentration: { 1: 17.5 },
    laggards: { label: '細產業', group: '被動元件', stocks: [{ code: '3', name: '丙', change_pct: 0.5 }] },
  });
  g.members = [m('1', '甲'), m('2', '乙')];
  const list = mergeGroups({ groups: [g] }, null);
  const rows = [...trackRows([list]), { key: 'x', names: '戊、己', members: [], cells: [], status: '消失・疑似出貨（爆量收黑）', streak: 0 }];
  const text = reportText('2026-08-04', [], list, rows);
  assert.match(text, /籌碼集中度：甲 \+17\.5%/);
  assert.match(text, /同族群未跟上（細產業：被動元件）：丙 \+0\.50%/);
  assert.match(text, /消失・疑似出貨：戊、己/);
});

test('hotNextDay: the 🔥 groups of each day, their members the day after', () => {
  const hot = grp(['1', '2'], {}, { daytrade: { by_code: { 1: 60, 2: 55 }, avg: 57.5, hot: true, base: {} } });
  const cold = grp(['3', '4'], {}, { daytrade: { by_code: {}, avg: null, hot: false } });
  const per = [mergeGroups({ groups: [hot, cold] }, null), mergeGroups({ groups: [cold] }, null), []];
  const q = { '2026-08-04': { 1: { change_pct: -3, gap_pct: 2, intraday_pct: -5 }, 2: { change_pct: 1, gap_pct: 1, intraday_pct: 0.5 }, 3: { change_pct: 9 } } };
  const s = hotNextDay(['2026-08-03', '2026-08-04', '2026-08-05'], per, q);
  assert.deepEqual({ ...s }, { groups: 1, stocks: 2, gap: 1.5, close: -1, intraday: -2.25, fadeShare: 50 }); // cold's "3" is not counted
  assert.equal(hotNextDay(['a', 'b'], [[], []], {}), null);
  const html = hotHtml(per[0], s);
  assert.match(html, /🔥 隔日沖警示 <small class="muted">1 組/);
  assert.match(html, /平均 <b>57\.5%<\/b>/);
  assert.match(html, /開高走低（收盤低於開盤）<b>50%<\/b>/);
  assert.match(reportText('2026-08-03', [], per[0], trackRows([per[0]]), s), /🔥 隔日沖警示（1 組[\s\S]*開高走低 50%（2 檔次）/);
});

test('hotHtml: the stored days’ next day, 🔥 against the rest', () => {
  const wide = { days: 12, first: '2026-07-29', last: '2026-10-22', stats: {
    hot: { groups: 30, stocks: 70, gap: 2.1, close: -0.5, excess: -0.8, intraday: -2.4, fade: 64, down: 55 },
    rest: { groups: 80, stocks: 170, gap: 0.6, close: 0.3, excess: 0.1, intraday: -0.2, fade: 48, down: 45 } } };
  const html = hotHtml([], null, wide);
  assert.match(html, /所有已存的日子（12 天，2026-07-29～2026-10-22）/);
  assert.match(html, /<tr><td class="l">🔥 全部<\/td><td>30／70<\/td><td><span class="up">\+2\.10%<\/span><\/td><td><span class="down">-0\.50%<\/span><\/td><td><span class="down">-0\.80%<\/span><\/td><td>64%<\/td><td>55%<\/td><\/tr>/);
  assert.match(html, /其他（非 🔥）<\/td><td>80／170/);
});

test('todayMove: yesterday’s groups today, less 大盤; nothing for a group new today', () => {
  const row = { members: [{ code: '1' }, { code: '2' }], cells: [3, 4] };
  const q = { 1: { change_pct: 2 }, 2: { change_pct: -1 } };
  assert.deepEqual(todayMove(row, 2, q, 1), { avg: 0.5, excess: -0.5 });
  assert.equal(todayMove({ ...row, cells: [null, 4] }, 2, q, 1), null); // new today
  assert.deepEqual(todayMove(row, 2, q, null), { avg: 0.5, excess: null });
  const html = trackHtml(['2026-10-21', '2026-10-22'], [{ ...row, names: '甲、乙', status: '連 2 天', today: { avg: 0.5, excess: -0.5 } }]);
  assert.match(html, /<th title="[^"]*">今天漲跌<\/th>/);
  assert.match(html, /<td class="up" title="扣大盤 -0\.50%">\+0\.50%<small class="muted">（-0\.50%）<\/small><\/td>/);
});

test('shapeHtml: both lists merged, one entry a pair, closest first', () => {
  const p = (a, b, dtw) => ({ members: [{ code: a, name: a, change_pct: 1 }, { code: b, name: b, change_pct: 2 }], dtw, peak_gap: 60, excess_r: 0.55 });
  const html = shapeHtml([{ shape_pairs: [p('1301', '1326', 1.9), p('2383', '6213', 1.2)] }, { shape_pairs: [p('1326', '1301', 1.5)] }]);
  assert.match(html, /高點錯開但走勢一樣 <small class="muted">2 對/);
  assert.ok(html.indexOf('2383') < html.indexOf('1301')); // 1.2 before 1.5
  assert.match(html, /高點差 60 分、扣指數 R 0\.55、形狀距離 1\.50/); // the closer of the two entries for 1301/1326
  assert.equal(shapeHtml([null, {}]), '');
});

test('reportText lists 高點錯開但走勢一樣 too', () => {
  const p = { members: [{ code: '1301', name: '台塑' }, { code: '1326', name: '台化' }], dtw: 1.5, peak_gap: 60, excess_r: 0.65 };
  const shapes = shapeList([{ shape_pairs: [p] }]);
  assert.match(reportText('2026-08-28', [], [], [], null, shapes), /高點錯開但走勢一樣（1 對，可能分批推進）：\n   台塑＋台化：高點差 60 分、扣指數 R 0\.65/);
});

test('hotHtml splits 🔥 into 突然湧入 and 平常就這樣 when the server does', () => {
  const x = (n) => ({ groups: n, stocks: n * 2, gap: 1, close: -1, excess: -1, intraday: -2, fade: 60, down: 50 });
  const html = hotHtml([], null, { days: 10, first: 'a', last: 'b', stats: { hot: x(5), surge: x(2), habitual: x(3), rest: x(9) } });
  assert.match(html, /　突然湧入（≥ 平常 1\.5 倍）<\/td><td>2／4/);
  assert.match(html, /　平常就這樣<\/td><td>3／6/);
});

test('hotHtml shows the next day by evidence count when the server sends it', () => {
  const x = (n) => ({ groups: n, stocks: n, gap: 1, close: 0.5, excess: -0.1, intraday: -0.5, fade: 55, down: 50 });
  const html = hotHtml([], null, { days: 60, first: 'a', last: 'b', stats: { hot: x(3), rest: x(5), evidence: { ev4: x(2), ev0: x(7) } } });
  assert.match(html, /證據 ✓ ≥ 4 項<\/td><td>2／2/);
  assert.match(html, /證據 ✓ ≤ 1 項<\/td><td>7／7/);
  assert.doesNotMatch(html, /✓ 2～3 項/); // none in that class
});

test('highlights: what stands out today, five lines at most', () => {
  const hot = grp(['1', '2'], {}, { daytrade: { hot: true, by_code: { 1: 62, 2: 58 }, base: { 1: 25, 2: 30 }, avg: 60 } });
  hot.members = [m('1', '甲'), m('2', '乙')];
  const habit = grp(['3', '4'], {}, { daytrade: { hot: true, by_code: { 3: 60 }, base: { 3: 58 }, avg: 60 } });
  assert.ok(isSurge(hot) && !isSurge(habit));
  const list = mergeGroups({ groups: [hot, habit] }, null);
  const rows = [{ names: '戊、己', status: '消失・疑似出貨（爆量收黑）', streak: 0, cells: [1, null] }, { names: '庚、辛', status: '連 3 天', streak: 2, cells: [1, 1, 1] }];
  const shapes = [{ members: [{ name: '台塑' }, { name: '台化' }], dtw: 1.2 }];
  const top = highlights(list, rows, shapes);
  assert.equal(top[0], '🔥 隔日沖熱錢突然湧入：甲、乙（歷史上這類隔天多半開高走低）');
  assert.match(top[1], /疑似出貨.*戊、己/);
  assert.match(top.find((t) => t.startsWith('連續出現')), /庚、辛（連 3 天）/);
  assert.match(top.at(-1), /高點錯開但走勢一樣：台塑＋台化/);
  assert.ok(top.length <= 5);
  assert.match(highlightsHtml(top), /<div class="rp-top"><b>今天重點<\/b><ul><li>/);
  assert.equal(highlightsHtml([]), '');
  assert.match(reportText('2026-10-22', [], list, rows, null, shapes), /今天重點：\n・🔥 隔日沖熱錢突然湧入/);
});

test('hotByMultiple: the 🔥 groups furthest above their usual first', () => {
  const g = (codes, v, base) => grp(codes, {}, { daytrade: { hot: true, by_code: { [codes[0]]: v }, base: { [codes[0]]: base }, avg: v } });
  const list = mergeGroups({ groups: [g(['1', '2'], 60, 50), g(['3', '4'], 60, 20), grp(['5', '6'])] }, null);
  assert.deepEqual(hotByMultiple(list).map((x) => x.key), ['3,4', '1,2']); // 3× before 1.2×, the cold one out
});

test('the 證據強 highlight says what the backtest found', () => {
  const g = grp(['1', '2'], { excess: true, baseline: true, burst: true, sync: true, samedir: true });
  const top = highlights(mergeGroups({ groups: [g] }, null), []);
  assert.match(top.find((t) => t.startsWith('證據強')), /回測 60 天：證據多的組合後續不一定較強/);
});

test('pullbackHtml and the ⚠ on a laggard chasing on volume', () => {
  const html = pullbackHtml({ stocks: [{ code: '3037', name: '欣興', close: 865, ma: 880.5, gap_pct: -1.76, since: '2026-10-16', with: ['南電'] }] });
  assert.match(html, /回測 10 日線 <small class="muted">1 檔/);
  assert.match(html, /收 865／10 日線 880\.5（-1\.76%）；10-16 與 南電 同步/);
  assert.equal(pullbackHtml({ stocks: [] }), '');
  const lag = laggardsHtml({ laggards: { label: '細產業', group: 'X', stocks: [{ code: '1', name: '甲', change_pct: 1, chase: true }, { code: '2', name: '乙', change_pct: 0.2 }] } });
  assert.match(lag, /甲 <small class="up">\+1\.00%<\/small> <b class="down"[^>]*>⚠ 帶量跟漲<\/b>/);
  assert.equal((lag.match(/⚠/g) || []).length, 1);
  assert.match(highlights([], [], [], [{ name: '欣興' }]).join(), /回測 10 日線（同步族群的觀察名單）：欣興/);
});

test('pullbackHtml marks a 跨組交集', () => {
  const x = { code: '1', name: '甲', close: 10, ma: 11, gap_pct: -9.1, since: '2026-10-16', with: ['乙'] };
  assert.match(pullbackHtml({ stocks: [{ ...x, cross: true }] }), /⭐ 跨組交集<\/b>/);
  assert.doesNotMatch(pullbackHtml({ stocks: [x] }), /跨組交集/);
});

test('highlightsHtml: the names in 今天重點 are buttons for the chart area', async () => {
  const { highlightsHtml, nameIndex } = await import('../../static/js/report.js');
  const names = nameIndex([{ members: [{ code: '3037', name: '欣興' }, { code: '8046', name: '南電' }] }], [], [{ code: '2492', name: '華新科' }]);
  const html = highlightsHtml(['同步：欣興、南電 <x>', '回測：華新科'], names);
  assert.match(html, /<button type="button" class="rp-stock rp-inline" data-code="3037">欣興<\/button>、<button[^>]*data-code="8046">南電<\/button> &lt;x&gt;/);
  assert.match(html, /data-code="2492">華新科/);
  assert.equal(highlightsHtml(['只有文字'], {}), '<div class="rp-top"><b>今天重點</b><ul><li>只有文字</li></ul></div>');
});
