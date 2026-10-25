import { test } from 'node:test';
import assert from 'node:assert/strict';

import { evidenceCount, evidenceHtml, evidenceItems, groupEvidenceHtml, groupEvidenceItems, isStrong } from '../../static/js/evidence.js';

const EXT = {
  same_dir: { same: 16, total: 20 },
  groups: [],
  finder: [{ date: '2026-10-22', method: 'known' }, { date: '2026-10-21', method: 'known' }],
  themes: ['PCB'],
  relations: [{ src_name: 'A', dst_name: 'B', pct: 30, method: '權益法', seats: [] }],
  group: null,
  baseline_intraday: { pct: 96, n: 300, median: 0.1 },
  baseline_daily: { pct: 80, n: 300, median: 0.2 },
  chips: { trust_days: 10, trust_both_buy: 4, trust_both_sell: 0, inst_a: 1, inst_b: -1, margin_a: 1, margin_b: 1 },
};

test('evidenceItems: each check as ✓／✗／— from the numbers', () => {
  const f = { ri: 0.62 };
  const ev = { excess: 0.55, minute: { co: 4, expected: 0.8, r0: 0.45, r_off: 0.2, bursts_a: 10, bursts_b: 12 } };
  const items = evidenceItems(f, EXT, ev);
  const by = Object.fromEntries(items.map((x) => [x.key, x.ok]));
  assert.deepEqual(by, { excess: true, baseline: true, burst: true, sync: true, repeat: true, chips: true, relation: true, samedir: true, branch: null, theme: true });
  assert.match(items.find((x) => x.key === 'excess').detail, /扣大盤／類股後分時 R 0\.55（原 0\.62）/);
  // the index each one was measured against, from K's payload
  const fac = (factors) => evidenceItems({ ri: 0.62 }, null, { excess: 0.55, factors }).find((x) => x.key === 'excess').detail;
  assert.match(fac(['半導體類', '半導體類']), /^扣半導體類後分時 R 0\.55/);
  assert.match(fac(['半導體類', '櫃買']), /^扣半導體類／櫃買後/);
  assert.match(items.find((x) => x.key === 'baseline').detail, /高於 96% 的同日隨機配對；日 R 高於 80%/);
  // weak numbers say ✗; missing data says —
  const weak = evidenceItems({ ri: 0.3 }, { ...EXT, finder: [], baseline_intraday: { pct: 40 }, chips: { trust_days: 10, trust_both_buy: 0, trust_both_sell: 1, inst_a: 1, inst_b: -1 } }, { excess: 0.1, minute: { co: 1, expected: 1.5, r0: 0.3, r_off: 0.35 } });
  const w = Object.fromEntries(weak.map((x) => [x.key, x.ok]));
  assert.deepEqual([w.excess, w.baseline, w.burst, w.sync, w.repeat, w.chips], [false, false, false, false, false, false]);
  const none = Object.fromEntries(evidenceItems({ ri: null }, null, { excess: null, minute: null }).map((x) => [x.key, x.ok]));
  assert.ok(Object.values(none).every((v) => v === null));
});

test('evidenceHtml: counts only the checks that had data', () => {
  const html = evidenceHtml([
    { key: 'a', ok: true, label: '甲', detail: 'x' },
    { key: 'b', ok: false, label: '乙', detail: 'y' },
    { key: 'c', ok: null, label: '丙', detail: '資料不足' },
  ]);
  assert.match(html, /🧾 資金一致性證據 <b>1<\/b>／2 項支持 <small class="muted">（1 項資料不足，不算）/);
  assert.match(html, /<b class="up">✓<\/b> 甲/);
  assert.match(html, /<b class="down">✗<\/b> 乙/);
  assert.match(html, /<li class="muted"><span class="muted">—<\/span> 丙/);
});

test('groupEvidenceItems / groupEvidenceHtml / isStrong: an N group\'s checks', () => {
  const g = {
    evidence: {
      excess: { ok: true, r: 0.55 }, baseline: { ok: true, pct: 96 }, burst: { ok: false, co: 1, expected: 2 },
      sync: { ok: null, r0: null, r_off: null }, samedir: { ok: true, share: 0.8 },
    },
    seen_days: ['2026-10-21'], checked_days: 4, evidence_chips: { ok: true, pairs: 1 },
    relations: [], rel_group: null, themes: [{ term: 'PCB', count: 2 }],
  };
  const items = groupEvidenceItems(g);
  const c = evidenceCount(items);
  assert.deepEqual(c, { yes: 5, known: 6 }); // 籌碼 ✓ is shown, not counted (methods/m6)
  assert.equal(isStrong(c), true);
  assert.equal(isStrong({ yes: 4, known: 4 }), false);
  const html = groupEvidenceHtml(g);
  assert.match(html, /class="fd-ev strong"/);
  assert.match(html, /🧾 證據 <b>5<\/b>\/6/);
  assert.doesNotMatch(html, /✓籌碼/); // not among the counted tags
  assert.match(html, /<span class="down">✗同爆量<\/span>/);
  assert.match(html, /扣指數：扣掉各自的類股／大盤後分時 R 0\.55/);
  assert.equal(groupEvidenceHtml({}), ''); // an old result without evidence: no line
});

test('evidenceItems: 同一個分點同時買超 from the 分點 pair', () => {
  const pick = (branch, branchErr = null) => evidenceItems({ ri: 0.5, branch, branchErr }, null, {}).find((x) => x.key === 'branch');
  assert.equal(pick({ shared: [{ name: '凱基-台北', a_rank: 1, b_rank: 2 }], strong: true }).ok, true);
  assert.equal(pick({ shared: [{ name: '元大', a_rank: 3, b_rank: 9 }], strong: false }).ok, false);
  assert.equal(pick({ shared: [], strong: false }).ok, false);
  assert.equal(pick(undefined).ok, null); // loading
  assert.equal(pick(null, '盤後才有分點資料').detail, '盤後才有分點資料');
});

test('groupEvidenceItems: 同分點 from the stored 分點, — until the snapshot has them', () => {
  const item = (branches) => groupEvidenceItems({ branches }).find((x) => x.key === 'branch');
  assert.equal(item({ common: [{ name: '高盛', ranks: { A: 1, B: 2 } }], ok: true }).ok, true);
  assert.match(item({ common: [{ name: '高盛', ranks: { A: 1, B: 2 } }], ok: true }).detail, /高盛 同時買超 2 檔/);
  assert.equal(item({ common: [], ok: false }).ok, false);
  assert.equal(item(null).ok, null);
  assert.match(item(null).detail, /收盤後快照/);
});

test('the base rate of each check rides along (shown only, the count is unchanged)', async () => {
  const { BASE_RATES, baseText, evidenceHtml } = await import('../../static/js/evidence.js');
  assert.equal(baseText('burst'), '隨機配對約 3% 也會出現'); // at ≥ 3 bursts and ≥ 3 × chance
  assert.equal(baseText('excess'), '隨機配對約 <1% 也會出現');
  assert.equal(baseText('theme'), ''); // not measured
  const html = evidenceHtml([{ key: 'burst', ok: true, label: '同爆量', detail: 'x' }, { key: 'theme', ok: true, label: '題材', detail: 'y' }]);
  assert.match(html, /同爆量 <small class="muted">x<\/small> <small class="muted ev-base"[^>]*>（隨機配對約 3% 也會出現）<\/small>/);
  assert.match(html, /<b>2<\/b>／2 項支持/); // still 2 of 2
  assert.ok(Object.values(BASE_RATES).every((v) => v > 0 && v < 1));
});

test('lagText: the cross-correlation peak in words', async () => {
  const { lagText } = await import('../../static/js/evidence.js');
  assert.equal(lagText(0, {}), '；峰值在同一分鐘（像同時下單）');
  assert.equal(lagText(2, { nameA: '甲', nameB: '乙' }), '；峰值錯開 2 分：甲 先動');
  assert.equal(lagText(-3, { nameA: '甲', nameB: '乙' }), '；峰值錯開 3 分：乙 先動');
  assert.equal(lagText(null, {}), '');
});

test('surgeMultiple: the group’s 當沖比 against its members’ usual', async () => {
  const { surgeMultiple, surgeText } = await import('../../static/js/evidence.js');
  assert.equal(surgeMultiple({ by_code: { 1: 60, 2: 40 }, base: { 1: 30, 2: 40 } }), 1.5);
  assert.equal(surgeMultiple({ by_code: { 1: 60 }, base: {} }), null);
  assert.match(surgeText({ by_code: { 1: 60 }, base: { 1: 30 } }), /<small class="up"[^>]*>（平常的 2\.0 倍）<\/small>/);
  assert.match(surgeText({ by_code: { 1: 60 }, base: { 1: 55 } }), /class="muted"/);
  assert.equal(surgeText(null), '');
});

test('形狀 (DTW) is shown, not counted', () => {
  const g = { evidence: { excess: { ok: true, r: 0.5 }, shape: { ok: true, dtw: 1.8 } } };
  const items = groupEvidenceItems(g);
  assert.match(items.find((x) => x.key === 'shape').detail, /DTW 距離 1\.80（≤ 2\.5 算相似）/);
  assert.deepEqual(evidenceCount(items), { yes: 1, known: 1 });
  assert.doesNotMatch(groupEvidenceHtml(g), /✓形狀/);
  assert.equal(groupEvidenceItems({ evidence: {} }).find((x) => x.key === 'shape').ok, null); // an older result: —
});
