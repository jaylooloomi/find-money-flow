import { test } from 'node:test';
import assert from 'node:assert/strict';

import { FIELDS, PRESETS, BIG_MIN, DEFAULT_GROUPINGS, condText, isBig, factorText, evidenceRank, rowsHeight, themeNewsHtml, cardHtml, digestText, shownFilter, finderMarkIndex, finderMarkTitle, csvExport, defaultGroupName, filterHtml, funnelText, presetOf, filterOptions, matchesFilter, progressView, queryOf, statsText } from '../../static/js/finder.js';

test('queryOf sends only the fields of the chosen method; blank 起漲差 means not required', () => {
  const p = { daily_r: 0.75, daily_window: 20, new_daily_r: 0.85, pool_top: 30, intraday_r: 0.5, peak_gap: 8, start_gap: '', require_burst: true, groupings: ['custom', 'news'], pool_sources: ['gain'] };
  const known = new URLSearchParams(queryOf('known', p));
  assert.equal(known.get('method'), 'known');
  assert.equal(known.get('daily_r'), '0.75');
  assert.equal(known.get('new_daily_r'), null);
  assert.equal(known.get('start_gap'), '');
  assert.equal(known.get('require_burst'), 'true');
  assert.equal(known.get('groupings'), 'custom,news');
  const fresh = new URLSearchParams(queryOf('new', p));
  assert.equal(fresh.get('new_daily_r'), '0.85');
  assert.equal(fresh.get('daily_r'), null);
  assert.equal(fresh.get('pool_sources'), 'gain');
  assert.equal(new URLSearchParams(queryOf('new', { ...p, new_min_volume_lots: 500 })).get('new_min_volume_lots'), '500');
  assert.equal(new URLSearchParams(queryOf('known', { ...p, new_min_volume_lots: 500 })).get('new_min_volume_lots'), null);
});

const G = {
  kind: '已知族群的延伸',
  members: [
    { code: '1', name: '甲', change_pct: 9.9, peak: '09:30', locked_at: '09:30', turnover_rate: 12.3 },
    { code: '2', name: '乙', change_pct: 5, peak: '09:34' },
  ],
  sources: [],
  pool: { 1: ['漲幅', '週轉率'] },
  matches: [{ label: '自訂族群', group: '被動', overlap: 0.5, outside: ['2'] }],
  daily_r: 0.86, intraday_r: 0.61, peak_spread: 4, start_spread: 2, together_bursts: ['09:25'], inst_net: 3.2e8,
  news: ['甲、乙同步大漲'],
};

test('cardHtml: kind, closest grouping, members (outside one marked), facts and headline', () => {
  const html = cardHtml(G, 0);
  assert.match(html, /已知族群的延伸/);
  assert.match(html, /最接近 自訂族群：被動（50%）/);
  assert.match(html, /fd-chip out[^>]*>乙/);
  assert.match(html, /🔒/);
  assert.match(html, /日 R <b>0\.86<\/b>/);
  assert.match(html, /高點差 <b>4<\/b> 分/);
  assert.match(html, /同時爆量 09:25/);
  assert.match(html, /法人 \+32\.0 萬張/); // inst_net is shares: 3.2 億股 = 32 萬張
  assert.match(html, /甲、乙同步大漲/);
  assert.match(cardHtml({ ...G, inst_net: -4.5e6 }, 2), /法人 −4,500 張/);
  const inst = cardHtml({ ...G, inst: { lots: 12345, amount_100m: -3.456, buyers: 1, sellers: 2 } }, 2);
  assert.match(inst, /法人 <b class="up">\+1\.2 萬張<\/b>（≈ −3\.5 億）.*買超 1／賣超 2 檔/);
  assert.match(cardHtml({ ...G, inst_streak: { days: 4, side: 'buy' } }, 2), /<b class="up"[^>]*>法人連買 4 天<\/b>/);
  assert.match(cardHtml({ ...G, inst_streak: { days: 2, side: 'sell' } }, 2), /法人連賣 2 天/);
  assert.doesNotMatch(cardHtml(G, 2), /法人連/);
  const per = cardHtml({ ...G, members: G.members.map((m, k) => ({ ...m, inst_net: k ? -2_345_678 : 1_000_400 })) }, 2);
  assert.match(per, /title="[^"]*法人 \+1,000 張/);
  assert.match(per, /title="[^"]*法人 −2,346 張/);
  assert.doesNotMatch(cardHtml(G, 2), /title="[^"]*法人/); // no per-member 法人: nothing in the tooltip
  const share = cardHtml({ ...G, turnover_100m: 40, inst: { lots: 3219, amount_100m: -1.64, buyers: 2, sellers: 0 } }, 2);
  assert.match(share, /法人占成交 <b>4\.1%<\/b>/);
  assert.match(share, /法人淨賣超金額/);
  assert.match(cardHtml({ ...G, turnover_100m: 4000, inst: { lots: 1, amount_100m: 0.5, buyers: 1, sellers: 0 } }, 2), /法人占成交 <b>&lt;0\.1%|法人占成交 <b><0\.1%/);
  assert.doesNotMatch(cardHtml({ ...G, turnover_100m: 0, inst: { lots: 1, amount_100m: 0.5, buyers: 1, sellers: 0 } }, 2), /法人占成交/);
  assert.doesNotMatch(cardHtml({ ...G, turnover_100m: 40, inst: { lots: 1, amount_100m: null, buyers: 1, sellers: 0 } }, 2), /法人占成交/);
  const line = cardHtml({ ...G, inst: { lots: 10, amount_100m: 1, buyers: 1, sellers: 0 }, turnover_100m: 20, inst_streak: { days: 3, side: 'buy' } }, 2);
  assert.match(line, /<div class="fd-inst">🏦 法人 .*法人占成交 .*法人連買 3 天/); // one 🏦 line for 法人
  assert.doesNotMatch(line.match(/<div class="fd-facts">.*?<\/div>/s)[0], /法人/); // not mixed into the R / timing facts
  assert.doesNotMatch(cardHtml({ ...G, inst_net: null }, 2), /fd-inst/);
  assert.match(cardHtml({ ...G, trust_streak: { days: 3, side: 'buy' } }, 2), /<div class="fd-inst">🏦 .*<b class="up"[^>]*>投信連買 3 天<\/b>/);
  assert.doesNotMatch(cardHtml(G, 2), /投信連/);
  const mg = cardHtml({ ...G, margin: { margin_chg: 1234, margin_bal: 56000, short_chg: -20, short_bal: 1200, short_ratio: 2.14 }, margin_streak: { days: 3, side: 'buy' } }, 2);
  assert.match(mg, /<div class="fd-margin"[^>]*>💳 融資 <b class="up">\+1,234<\/b> 張 <span class="muted">（餘額 5\.6 萬）<\/span>　融券 <b class="down">−20<\/b> 張/);
  assert.match(mg, /券資比 2\.1%　<b class="up">融資連增 3 天<\/b>/);
  assert.doesNotMatch(cardHtml(G, 2), /fd-margin/);
  const split = cardHtml({ ...G, inst_split: { foreign: 1991, trust: 0, dealer: -19 } }, 2);
  assert.match(split, /外資 <b class="up">\+1,991<\/b>／投信 <b class="">0<\/b>／自營 <b class="down">−19<\/b> 張/);
  assert.doesNotMatch(cardHtml(G, 2), /外資/);
  const noAmount = cardHtml({ ...G, inst: { lots: 500, amount_100m: null, buyers: 1, sellers: 0 } }, 2);
  assert.match(noAmount, /法人 <b class="up">\+500 張<\/b><span/);
  assert.doesNotMatch(noAmount, /億）|NaN/);
  assert.match(cardHtml({ ...G, inst: { lots: -800, amount_100m: -0.5, buyers: 0, sellers: 2 } }, 2), /法人 <b class="down">−800 張<\/b>/);
  const money = cardHtml({ ...G, turnover_100m: 40, turnover_share: 1.234, avg_pct: 7 }, 4);
  assert.match(money, /成交值 <b>40\.00 億<\/b>（占全市場 <b>1\.23%<\/b>）/);
  assert.match(money, /平均漲幅 <b class="up">\+7\.00%<\/b>/);
  assert.doesNotMatch(html, /fd-money/); // older stored results without the fields
  assert.match(cardHtml({ ...G, seen_days: ['2026-10-20', '2026-10-21'], checked_days: 4 }, 5), /🔁 近 5 天第 3 次/);
  assert.match(cardHtml({ ...G, seen_days: [], checked_days: 4 }, 6), /🆕 首次/);
  assert.doesNotMatch(cardHtml({ ...G, seen_days: [], checked_days: 0 }, 7), /fd-seen/); // nothing to compare with
  const th = cardHtml({ ...G, themes: [{ term: '被動元件', count: 2 }, { term: 'AI', count: 1 }], members: [{ ...G.members[0], themes: ['被動元件', 'AI'] }, G.members[1]] }, 8);
  assert.match(th, /📰 題材：<span class="fd-theme" data-theme="0"[^>]*>被動元件（2）<\/span>、<span class="fd-theme" data-theme="1"[^>]*>AI（1）<\/span>/);
  assert.match(th, /<div class="fd-theme-news" hidden><\/div>/);
  assert.match(th, /title="1 甲[^"]*📰 被動元件、AI/);
  assert.doesNotMatch(html, /fd-themes/);
  assert.match(cardHtml({ ...G, sources: [{ label: '概念股', group: 'AI' }] }, 1), /概念股：AI/);
});

test('statsText summarises the run', () => {
  const t = statsText({ method: 'new', stats: { pool: 113, candidates: 28, fetched: 74, groups: 4, seconds: { total: 2.5 } }, cached: false, missing_groupings: ['chain'] });
  assert.match(t, /候選池 113 檔/);
  assert.match(t, /找到 4 群/);
  assert.match(t, /2\.5 秒/);
  assert.match(t, /尚未載入：細產業/);
});

test('progressView: stages map onto one bar', () => {
  assert.equal(progressView(null).pct, 3);
  assert.match(progressView({ running: true, stage: 'daily', seconds: 1 }).text, /日相關/);
  const mid = progressView({ running: true, stage: 'fetch', done: 50, total: 100, seconds: 4.2 });
  assert.equal(mid.pct, 15 + 38);
  assert.match(mid.text, /抓分 K 50／100 檔　4 秒/);
  assert.equal(progressView({ running: true, stage: 'intraday' }).pct, 95);
});

test('filter chips: 找法1 by source grouping (a group can count twice), 找法2 by kind', () => {
  const known = {
    method: 'known',
    groupings: ['官方產業', '自訂族群', '細產業'],
    groups: [
      { kind: '已知族群', sources: [{ label: '官方產業' }, { label: '自訂族群' }] },
      { kind: '已知族群', sources: [{ label: '細產業' }] },
      { kind: '已知族群', sources: [{ label: '官方產業' }] },
    ],
  };
  assert.deepEqual(filterOptions(known).map((o) => [o.key, o.n]), [['all', 3], ['官方產業', 2], ['自訂族群', 1], ['細產業', 1]]);
  assert.equal(known.groups.filter((g) => matchesFilter(g, '細產業', 'known')).length, 1);
  assert.equal(known.groups.filter((g) => matchesFilter(g, 'all', 'known')).length, 3);
  const fresh = { method: 'new', groups: [{ kind: '跨族群新組合' }, { kind: '已知族群' }, { kind: '跨族群新組合' }] };
  assert.deepEqual(filterOptions(fresh).slice(0, 4).map((o) => [o.label, o.n]), [['全部', 3], ['已知族群', 1], ['延伸', 0], ['跨族群新組合', 2]]);
  // 🏦 法人買超／賣超: offered once 法人 is attached, for either method, without recomputing
  const withInst = { ...known, groups: known.groups.map((g, k) => ({ ...g, inst: { lots: [500, -20, 0][k] } })) };
  assert.deepEqual(filterOptions(withInst).slice(-2).map((o) => [o.key, o.label, o.n]), [['@inst+', '🏦 法人買超', 1], ['@inst-', '🏦 法人賣超', 1]]);
  assert.equal(withInst.groups.filter((g) => matchesFilter(g, '@inst+', 'known')).length, 1);
  assert.equal(matchesFilter({ kind: '已知族群' }, '@inst-', 'new'), false); // no 法人 yet: neither
  assert.equal(shownFilter(withInst, '@inst-'), '@inst-');
  assert.equal(shownFilter(known, '@inst-'), 'all'); // the day has no 法人: back to 全部
  assert.equal(csvExport({ ...withInst, date: '2026-10-22', groups: withInst.groups.map((g) => ({ ...g, members: [{ code: '1', name: '甲' }] })) }, '@inst+')[2].length, 1);
  // 找法2: chips by the ranking the members came in from
  const pooled = { method: 'new', params: { pool_sources: ['gain', 'turnover', 'gap'] }, groups: [
    { kind: '跨族群新組合', pool: { 1: ['漲幅', '跳空'], 2: ['週轉率'] } },
    { kind: '已知族群', pool: { 3: ['跳空'] } },
  ] };
  assert.deepEqual(filterOptions(pooled).slice(-3).map((o) => [o.key, o.label, o.n]), [['@src:gain', '📈 漲幅', 1], ['@src:turnover', '📈 週轉率', 1], ['@src:gap', '📈 開盤跳空', 2]]);
  assert.equal(pooled.groups.filter((g) => matchesFilter(g, '@src:turnover', 'new')).length, 1);
  assert.ok(!filterOptions(pooled).some((o) => o.key === '@src:vol_ratio')); // not used in that search: no chip
  assert.match(csvExport({ ...pooled, date: '2026-10-22', groups: pooled.groups.map((g) => ({ ...g, members: [{ code: '1', name: '甲' }] })) }, '@src:gap')[0], /_開盤跳空\.csv$/);
  const tipCard = cardHtml({ ...G, sources: [], matches: [{ label: '官方產業', group: '電子零組件業', overlap: 0.5, outside: [] }, { label: '概念股', group: '被動元件', overlap: 0.4, outside: [] }] }, 1);
  assert.match(tipCard, /<span class="fd-title" title="最接近 官方產業：電子零組件業（重疊 50%）\n最接近 概念股：被動元件（重疊 40%）">/);
  const rel = { ...known, groups: known.groups.map((g, k) => ({ ...g, relations: k === 0 ? [{ src_name: '甲', dst_name: '乙', pct: 30, method: '權益法', seats: [] }] : [] })) };
  const { tip: relTip, ...relChip } = filterOptions(rel).at(-1);
  assert.deepEqual(relChip, { key: '@rel', label: '🏢 有持股關係', n: 1 });
  assert.match(relTip, /轉投資/);
  assert.ok(!filterOptions(known).some((o) => o.key === '@rel')); // none: no chip
  assert.match(cardHtml({ ...G, relations: rel.groups[0].relations }, 1), /<div class="fd-rel"[^>]*>🏢 <b>甲<\/b> 持有 <b>乙<\/b> 30%（權益法）/);
  assert.doesNotMatch(cardHtml(G, 1), /fd-rel/);
  const strong = { evidence: { excess: { ok: true }, baseline: { ok: true }, burst: { ok: true }, sync: { ok: true }, samedir: { ok: true } } };
  const evd = { ...known, groups: [{ ...known.groups[0], ...strong }, { ...known.groups[1], evidence: { excess: { ok: false } } }, known.groups[2]] };
  const { tip: evTip, ...evChip } = filterOptions(evd).at(-1);
  assert.deepEqual(evChip, { key: '@ev', label: '🧾 證據強', n: 1 });
  assert.match(evTip, /至少 5 項 ✓.*70%/); // the tooltip says what 證據強 means
  assert.match(filterHtml(evd, 'all'), /title="資金一致性證據夠多[^"]*（1 組）"/);
  assert.ok(filterOptions(known).every((o) => o.tip)); // every chip has one
  assert.ok(evidenceRank(evd.groups[0]) > evidenceRank(evd.groups[1]));
  const hot = cardHtml({ ...G, daytrade: { by_code: { 1: 53.6, 2: 47 }, avg: 50.3, hot: true } }, 1);
  assert.match(hot, /<div class="fd-dt"[^>]*>🔥 當沖比高（平均 50\.3%）：甲 53\.6%、乙 47\.0%/); // the average decides, shown as computed
  assert.doesNotMatch(cardHtml({ ...G, daytrade: { by_code: { 1: 10 }, hot: false } }, 1), /fd-dt/);
  const brCard = cardHtml({ ...G, branches: { common: [{ name: '美商高盛', ranks: { 1: 2, 2: 2 }, nets: {} }], ok: true, have: 2 } }, 1);
  assert.match(brCard, /<div class="fd-br"[^>]*>🏦 <b>美商高盛<\/b> 甲（第 2）、乙（第 2）/);
  assert.doesNotMatch(cardHtml(G, 1), /fd-br/);
  const html = filterHtml(fresh, '跨族群新組合');
  assert.match(html, /data-filter="跨族群新組合" class="on"/);
  assert.match(html, /data-filter="已知族群的延伸" disabled/);
});

test('defaultGroupName: date + members, legal characters, 30 characters at most', () => {
  assert.equal(defaultGroupName({ members: [{ name: '國巨*' }, { name: '華新科' }] }, '2026-10-22'), 'N1022 國巨+華新科');
  const long = defaultGroupName({ members: Array.from({ length: 8 }, (_, i) => ({ name: `公司名稱${i}號'` })) }, '2026-10-22');
  assert.equal(long.length, 30);
  assert.ok(long.endsWith('…'));
  assert.ok(!/['"\\/,<>]/.test(long));
  assert.match(cardHtml({ ...G }, 3), /data-save="3"/);
});

test('csvExport: one row per member of the cards on screen, with the group figures and thresholds', () => {
  const d = {
    method: 'known', date: '2026-10-22',
    params: { min_amplitude: 3, min_volume_lots: 300, daily_r: 0.8, daily_window: 20, intraday_r: 0.5, peak_gap: 8 },
    groups: [
      { ...G, kind: '已知族群', sources: [{ label: '細產業', group: '被動元件／電容器' }], turnover_100m: 40, turnover_share: 1.2, avg_pct: 7 },
      { ...G, kind: '已知族群', sources: [{ label: '官方產業', group: '半導體業' }] },
    ],
  };
  const [name, header, rows] = csvExport(d);
  assert.equal(name, '資金連動搜尋_找法1_2026-10-22.csv');
  assert.equal(rows.length, 4); // 2 groups × 2 members
  assert.equal(header.length, rows[0].length);
  assert.deepEqual(rows[0].slice(0, 7), ['2026-10-22', '找法1 · 已知分類', 1, '已知族群', '細產業:被動元件／電容器', '1', '甲']);
  assert.match(rows[0][rows[0].length - 1], /振幅>3% 成交量≥300張 日R≥0\.8\(20日\) 分時R≥0\.5 高點差≤8分/);
  const only = csvExport(d, '官方產業');
  assert.equal(only[2].length, 2);
  assert.equal(only[2][0][2], 2); // keeps the card number it has on screen
  assert.match(only[0], /_官方產業\.csv$/);
  assert.equal(csvExport({ ...d, groups: [] }), null);
  // the 🏦 line goes into the CSV too
  const rich = { ...d, groups: [{ ...d.groups[0], members: [{ code: '1', name: '甲', inst_net: 1_234_567 }], inst: { lots: 1235, amount_100m: 2, buyers: 1, sellers: 0 }, inst_split: { foreign: 1000, trust: 200, dealer: 35 }, inst_streak: { days: 3, side: 'buy' }, trust_streak: { days: 2, side: 'sell' } }] };
  const [rname, rh, rr] = csvExport(rich, '@inst+');
  const col = (k) => rr[0][rh.indexOf(k)];
  assert.deepEqual(['個股法人(張)', '群組法人(張)', '群組法人(億)', '法人占成交%', '外資(張)', '投信(張)', '自營(張)', '法人連續', '投信連續'].map(col), [1235, 1235, 2, 5, 1000, 200, 35, '連買3', '連賣2']);
  assert.equal(rh[rh.length - 1], '條件');
  const mrich = { ...rich, groups: [{ ...rich.groups[0], members: [{ code: '1', name: '甲', margin_chg: 50 }], margin: { margin_chg: 50, margin_bal: 100, short_chg: -3, short_bal: 9, short_ratio: 9 }, margin_streak: { days: 2, side: 'sell' } }] };
  const [, mh, mr] = csvExport(mrich);
  assert.deepEqual(['個股融資增減(張)', '群組融資增減(張)', '群組融券增減(張)', '券資比%', '融資連續'].map((k) => mr[0][mh.indexOf(k)]), [50, 50, -3, 9, '連減2']);
  assert.match(rname, /_法人買超\.csv$/);
  assert.equal(rows[0][header.indexOf('外資(張)')], ''); // no 法人 attached: blank, not 0
});

test('funnelText: what removed the pairs, most first, then how many passed', () => {
  const d = { stats: { groups: 2, funnel: { pairs: 40, no_bars: 3, passed: 5, rejected: { 分時相關不足: 20, 高點時間差太大: 12 } } } };
  assert.equal(funnelText(d), '漏斗：進入分時比對 40 對 → 刷掉：分時相關不足 20、高點時間差太大 12、沒有分 K 3 → 三關都過 5 對 → 2 群');
  assert.equal(funnelText({ stats: {} }), ''); // older stored results
});

test('presets only set the linkage thresholds; the current values map back to one or to 自訂', () => {
  const mine = { min_amplitude: 4, min_volume_lots: 500, daily_r: 0.8, new_daily_r: 0.8, intraday_r: 0.5, excess_r: 0.4, peak_gap: 8 };
  assert.equal(presetOf(mine), 'standard');
  const loose = { ...mine, ...PRESETS.loose.values };
  assert.equal(presetOf(loose), 'loose');
  assert.equal(loose.min_amplitude, 4); // untouched
  assert.equal(loose.min_volume_lots, 500);
  assert.equal(presetOf({ ...mine, intraday_r: 0.45 }), null); // a hand edit = 自訂
  assert.equal(presetOf({ ...mine, intraday_r: '0.5' }), 'standard'); // form values may be strings
});

test('finderMarkIndex / finderMarkTitle: B marks the groups N found, by source grouping and name', () => {
  const res = { groups: [
    { members: [{ name: '甲' }, { name: '乙' }], daily_r: 0.86, intraday_r: 0.61, peak_spread: 3, sources: [{ label: '官方產業', group: '半導體業' }, { label: '自訂族群', group: 'IC' }] },
    { members: [{ name: '丙' }, { name: '丁' }], daily_r: 0.9, intraday_r: null, peak_spread: 0, sources: [{ label: '官方產業', group: '半導體業' }] },
  ] };
  const idx = finderMarkIndex(res);
  assert.equal(idx.get('官方產業|半導體業').length, 2);
  assert.equal(idx.get('自訂族群|IC').length, 1);
  assert.equal(idx.get('細產業|半導體業'), undefined);
  const title = finderMarkTitle(idx.get('官方產業|半導體業'));
  assert.match(title, /甲、乙（日R 0\.86、分時R 0\.61、高點差 3 分）/);
  assert.match(title, /丙、丁（日R 0\.90、分時R —/);
  assert.equal(finderMarkIndex(null).size, 0);
});

test('digestText: counts per method from stored results, 🔁 for repeats, nothing when none is stored', () => {
  const known = { groups: [{ seen_days: ['2026-10-19'] }, { seen_days: [] }, {}] };
  const fresh = { groups: [{ seen_days: [] }] };
  assert.equal(digestText(known, fresh), '💡 資金連動：找法1 3 群（🔁 1）／找法2 1 群');
  assert.equal(digestText(known, { stored: false, groups: [] }), '💡 資金連動：找法1 3 群（🔁 1）');
  assert.equal(digestText({ groups: [] }, null), '💡 資金連動：找法1 0 群');
  assert.equal(digestText({ stored: false, groups: [] }, null), '');
});

test('shownFilter: a saved chip the day has no cards for falls back to 全部 (so the CSV matches the screen)', () => {
  const d = { method: 'new', groups: [{ kind: '已知族群' }] };
  assert.equal(shownFilter(d, '已知族群'), '已知族群');
  assert.equal(shownFilter(d, '跨族群新組合'), 'all');
  assert.equal(shownFilter(d, undefined), 'all');
  assert.equal(csvExport({ ...d, date: '2026-10-22', params: {}, groups: [{ kind: '已知族群', members: [{ code: '1', name: 'a' }] }] }, shownFilter(d, '跨族群新組合'))[2].length, 1);
});

test('themeNewsHtml: the headlines behind a 題材, links only for http(s), names and escaping', () => {
  const html = themeNewsHtml({ term: 'PCB', titles: [
    { title: 'PCB族群 <律勝> 連飆', source: '鉅亨', url: 'https://news.example/1', date: '2026-10-20', names: ['律勝'] },
    { title: '別的', source: 'X', url: 'javascript:alert(1)', date: null, names: [] },
  ] });
  assert.match(html, /📰 PCB/);
  assert.match(html, /<a href="https:\/\/news\.example\/1" target="_blank" rel="noopener noreferrer">PCB族群 &lt;律勝&gt; 連飆<\/a> <span class="fd-named">律勝<\/span> <small class="muted">10-20 · 鉅亨<\/small>/);
  assert.doesNotMatch(html, /javascript:/); // a non-http url is shown as plain text
  assert.match(themeNewsHtml({ term: 'X', titles: [] }), /沒有存下這個題材的標題/);
  assert.equal(themeNewsHtml(undefined), '');
});

test('rowsHeight: two rows of cards, from their tops; null when everything fits', () => {
  assert.equal(rowsHeight([0, 0, 0, 280, 280, 280, 540, 540]), 530); // row 3 starts at 540, minus the 10px gap
  assert.equal(rowsHeight([540, 0, 280, 0]), 530); // any order
  assert.equal(rowsHeight([0, 0, 280]), null); // two rows only: no limit
  assert.equal(rowsHeight([]), null);
  assert.equal(rowsHeight([0, 300, 610, 900], 3, 8), 892);
});

test('the card shows the excess R beside the raw one, and which index each member was measured against', () => {
  const g = { members: [{ code: '1', name: '甲' }, { code: '2', name: '乙' }, { code: '3', name: '丙' }], factors: { 1: '半導體類', 2: '半導體類', 3: '櫃買' } };
  assert.equal(factorText(g), '半導體類：甲、乙；櫃買：丙');
  assert.equal(factorText({ members: g.members }), '—');
});

test('只看 3 檔以上 keeps the groups of more than two members (cards and CSV)', () => {
  const m = (n) => Array.from({ length: n }, (_, i) => ({ code: String(i + 1), name: `股${i + 1}` }));
  const pair = { members: m(2) };
  const trio = { members: m(3) };
  assert.equal(BIG_MIN, 3);
  assert.ok(isBig(pair, false) && isBig(trio, true) && !isBig(pair, true));
  const d = { date: '2026-10-22', method: 'known', params: {}, groups: [pair, trio] };
  assert.equal(csvExport(d, 'all')[2].length, 5); // 2 + 3 member rows
  assert.equal(csvExport(d, 'all', true)[2].length, 3); // the trio only
});

test('condText: the 條件 in a few words, for 盤後報告', () => {
  const p = { daily_r: 0.8, new_daily_r: 0.8, intraday_basis: 'sector', excess_r: 0.4, intraday_r: 0.5, peak_gap: 8, dense: 0.7 };
  assert.equal(condText(p), '日 R ≥ 0.8（找法2 0.8）、扣大盤＋類股後分時 R ≥ 0.4、高點差 ≤ 8 分、群內 0.7');
  assert.match(condText({ ...p, intraday_basis: 'raw' }), /、分時 R ≥ 0\.5、/);
});

test('集團 is offered to 找法1 but off by default (the precomputed results keep their key)', () => {
  assert.deepEqual(DEFAULT_GROUPINGS, ['industry', 'custom', 'concept', 'chain', 'news']);
  assert.match(queryOf('known', { groupings: [...DEFAULT_GROUPINGS, 'group'] }), /groupings=industry%2Ccustom%2Cconcept%2Cchain%2Cnews%2Cgroup/);
});

test('the 🔥 line shows each member’s usual 當沖比, marked when today is 2× or more', () => {
  const G2 = { members: [{ code: '1', name: '甲' }, { code: '2', name: '乙' }], sources: [], matches: [], kind: '已知族群' };
  const html = cardHtml({ ...G2, daytrade: { by_code: { 1: 62, 2: 45 }, avg: 53.5, hot: true, base: { 1: 30, 2: 40 } } }, 0);
  assert.match(html, /甲 62\.0%<small class="up">（平常 30%）<\/small>、乙 45\.0%<small class="muted">（平常 40%）<\/small>/);
});

test('🔥 隔日沖 chip: the groups whose members average ≥ 50% 當沖比', () => {
  const g = (hot) => ({ members: [{ code: '1', name: '甲' }], sources: [], matches: [], kind: '已知族群', daytrade: { by_code: {}, avg: hot ? 60 : 20, hot } });
  const d = { method: 'known', groupings: [], groups: [g(true), g(false)] };
  const chip = filterOptions(d).find((o) => o.key === '@dt');
  assert.equal(chip.n, 1);
  assert.match(chip.tip, /隔天常開高走低/);
  assert.ok(matchesFilter(d.groups[0], '@dt', 'known') && !matchesFilter(d.groups[1], '@dt', 'known'));
  assert.ok(!filterOptions({ ...d, groups: [g(false)] }).some((o) => o.key === '@dt')); // none: no chip
});

test('a 5-minute card says its peak-gap rule only resolves to 5 minutes', () => {
  const g = { members: [{ code: '1', name: '甲' }], sources: [], matches: [], kind: '已知族群', resolution: 5 };
  assert.match(cardHtml(g, 0), /title="這天只有 5 分 K：高點時間只到 5 分鐘精度[^"]*">5 分 K<\/span>/);
  assert.match(FIELDS.find((f) => f[0] === 'peak_gap')[4], /6～9 跟 5 效果一樣/);
});

test('N cards show the members’ 籌碼集中度 when 分點 is stored', () => {
  const g = { members: [{ code: '1', name: '甲' }, { code: '2', name: '乙' }], sources: [], matches: [], kind: '已知族群', concentration: { 1: 17.5, 2: -3 } };
  assert.match(cardHtml(g, 0), /🏦 籌碼集中度 甲 <span class="up">\+17\.5%<\/span>、乙 <span class="down">-3\.0%<\/span>/);
  assert.doesNotMatch(cardHtml({ ...g, concentration: null }, 0), /籌碼集中度/);
});

test('excess_r_mode: a 條件 select, shown in condText and sent in the query', () => {
  assert.ok(FIELDS.some(([k, , kind]) => k === 'excess_r_mode' && kind === 'sel'));
  const p = { daily_r: 0.8, new_daily_r: 0.8, intraday_basis: 'sector', excess_r: 0.4, excess_r_mode: 'adaptive', peak_gap: 8, dense: 0.7 };
  assert.match(condText(p), /扣大盤＋類股後分時 R ≥ 每日自適應門檻/);
  assert.equal(new URLSearchParams(queryOf('known', { ...p, groupings: ['industry'] })).get('excess_r_mode') ?? queryOf('known', p).get?.('excess_r_mode'), 'adaptive');
});

test('扣第 1 主成分 is one more 分時 R basis', async () => {
  const { BASES } = await import('../../static/js/finder.js');
  assert.equal(BASES.pca1, '扣第 1 主成分');
  assert.match(condText({ daily_r: 0.8, new_daily_r: 0.8, intraday_basis: 'pca1', excess_r: 0.4, peak_gap: 8, dense: 0.7 }), /扣第 1 主成分後分時 R ≥ 0\.4/);
});

test('組群方式: 七成規則 (default) or 社群偵測', async () => {
  const { GROUP_METHODS } = await import('../../static/js/finder.js');
  assert.deepEqual(Object.keys(GROUP_METHODS), ['dense', 'community']);
  const p = { daily_r: 0.8, new_daily_r: 0.8, intraday_basis: 'sector', excess_r: 0.4, peak_gap: 8, dense: 0.7 };
  assert.match(condText(p), /群內 0\.7$/);
  assert.match(condText({ ...p, group_method: 'community' }), /社群偵測$/);
});
