// 🧾 資金一致性證據: one ✓／✗ list per pair in K's 🔗 交集 (pure, node --test).
// Inputs: K's payload evidence (excess R matrix, 1-minute pair stats), pairFacts' numbers, and the
// /api/history/pairs entry (baselines, chips, N sightings, relations, themes). A check without data is
// shown as — and counted neither way.

import { escapeHtml } from './transform.js';

/** 扣指數後分時 R that counts: the same 0.4 as N's default 扣指數後相關 (finder.EXCESS_R). */
export const EXCESS_R = 0.4;

const r2 = (v) => (v === null || v === undefined ? '—' : v.toFixed(2));

/** Where the 1-minute cross-correlation peaks, in words (shown only): the same minute, or who moves first. */
export function lagText(lag, f) {
  if (lag === null || lag === undefined) return '';
  if (lag === 0) return '；峰值在同一分鐘（像同時下單）';
  const lead = lag > 0 ? f?.nameA || '前一檔' : f?.nameB || '後一檔';
  return `；峰值錯開 ${Math.abs(lag)} 分：${lead} 先動`;
}

/** [{key, ok: true|false|null, label, detail}] for one pair. */
export function evidenceItems(f, ext, ev) {
  const items = [];
  const add = (key, ok, label, detail) => items.push({ key, ok, label, detail });

  // 1. not just the index
  const ex = ev?.excess;
  const fac = (ev?.factors || []).filter(Boolean);
  const facText = fac.length ? (fac[0] === fac[1] ? `扣${fac[0]}` : `扣${fac.join('／')}`) : '扣大盤／類股';
  add('excess', ex === null || ex === undefined ? null : ex >= EXCESS_R,
    '扣掉大盤／類股後還是很像',
    ex === null || ex === undefined ? '資料不足' : `${facText}後分時 R ${r2(ex)}（原 ${r2(f.ri)}）`);

  // 2. not chance: random pairs of the same day and 漲幅 range
  const bi = ext?.baseline_intraday;
  const bd = ext?.baseline_daily;
  const base = bi || bd;
  add('baseline', base ? base.pct >= 90 : null,
    '比隨機配對更像',
    base
      ? [bi ? `分時 R 高於 ${bi.pct}% 的同日隨機配對` : '', bd ? `日 R 高於 ${bd.pct}%` : ''].filter(Boolean).join('；')
      : '資料不足');

  // 3. volume in the same minute
  const mn = ev?.minute;
  const co = mn && mn.expected !== null ? mn : null;
  add('burst', co ? co.co >= BURST_CO && co.co >= BURST_X * Math.max(co.expected, 0.5) : null,
    '同一分鐘一起爆量',
    co ? `同分鐘爆量 ${co.co} 次（隨機預期 ${co.expected.toFixed(1)} 次）` : '沒有 1 分 K（約 30 天前的日子沒有）');

  // 4. same minute, not one following the other
  const sync = mn && mn.r0 !== null && mn.r_off !== null ? mn : null;
  add('sync', sync ? sync.r0 >= 0.3 && sync.r0 - sync.r_off >= 0.1 : null,
    '同一分鐘同步（不是一前一後）',
    sync ? `1 分 K R ${r2(sync.r0)}，錯開 1～3 分最多 ${r2(sync.r_off)}${lagText(sync.peak_lag, f)}` : '沒有 1 分 K');

  // 5. found again on other days
  const days = ext ? new Set((ext.finder || []).map((h) => h.date)).size : null;
  add('repeat', days === null ? null : days >= 2, '不只一天', days === null ? '載入中' : `近 5 天 N 找到同一群 ${days} 天`);

  // 6. the same money behind both
  const ch = ext?.chips;
  if (ch) {
    const trust = ch.trust_days ? ch.trust_both_buy + ch.trust_both_sell : null;
    const inst = ch.inst_a !== null && ch.inst_b !== null && ch.inst_a !== 0 && ch.inst_b !== 0 ? ch.inst_a === ch.inst_b : null;
    const ok = trust !== null && trust >= 3 ? true : inst;
    const parts = [];
    if (ch.trust_days) parts.push(`近 ${ch.trust_days} 天投信同買 ${ch.trust_both_buy} 天、同賣 ${ch.trust_both_sell} 天`);
    if (inst !== null) parts.push(`今天法人${inst ? '同方向' : '方向相反'}`);
    add('chips', ok, '籌碼同一邊', parts.join('；') || '當天法人還沒公布');
  } else add('chips', null, '籌碼同一邊', '載入中');

  // 7. a structural reason
  const rel = ext ? (ext.relations?.length || 0) + (ext.group ? 1 : 0) : null;
  add('relation', rel === null ? null : rel > 0 ? true : null, '有持股／集團關係', rel ? '見下方 🏢' : '沒有（或還沒抓到）');

  // 8. day after day
  const sd = ext?.same_dir;
  add('samedir', sd?.total ? sd.same / sd.total >= 0.75 : null, '天天同漲同跌', sd?.total ? `近 ${sd.total} 天 ${sd.same} 天同方向` : '資料不足');

  // 8b. the same broker branch buying both (HiStock 分點): undefined while loading
  const br = f.branch;
  add('branch', br ? (br.strong ? true : br.shared.length ? false : false) : null,
    '同一個分點同時買超',
    br === undefined ? (f.branchErr || '載入中') : !br ? (f.branchErr || '沒有分點資料') : br.shared.length
      ? `${br.shared[0].name} 同時買超（第 ${br.shared[0].a_rank}／第 ${br.shared[0].b_rank}）${br.shared.length > 1 ? ` 等 ${br.shared.length} 個` : ''}`
      : '買超前 15 名沒有共同分點');

  // 9. one story
  add('theme', ext ? (ext.themes?.length ? true : null) : null, '同一個題材', ext?.themes?.length ? `📰 ${ext.themes.slice(0, 3).join('、')}` : '沒有共同題材');
  return items;
}

/**
 * How often each check also says ✓ for a random pair of the same day (the base rate): a ✓ that random
 * pairs get half the time says little, one they get 5% of the time says a lot. Measured on the cached
 * data, 2026-10-25 (branch night-shift/methods-2026-10-25, research/m5_weights.py: 10,557 random pairs of
 * the 300 most traded, 15 days); 隨機 is 10% by its own definition (above the 90th percentile).
 * Shown only — the ✓ count is unchanged.
 */
export const BASE_RATES = { excess: 0.007, baseline: 0.1, burst: 0.027, sync: 0.048, samedir: 0.058, chips: 0.513 };
// 同爆量 at ≥ 3 bursts and ≥ 3 × chance (finder.BURST_CO / BURST_X; methods/m6: random 2.7%, was 41.8% at 2 and 2)
export const BURST_CO = 3;
export const BURST_X = 3;
// 籌碼 tells little (methods/m6: lift ≤ 1.4 whichever way it is read): shown, not counted
export const INFO_ONLY = new Set(['chips', 'shape']); // 形狀: shown while its worth as a vote is measured
// 突然湧入 from this multiple of the usual 當沖比 (linkage.SURGE_X; research/exp7)
export const SURGE_X = 1.5;
export const baseText = (key) => (BASE_RATES[key] == null ? '' : `隨機配對約 ${BASE_RATES[key] < 0.01 ? '<1' : Math.round(BASE_RATES[key] * 100)}% 也會出現`);

/** A group's 當沖比 as a multiple of its members' usual (the 20 days before), averaged; null without both. */
export function surgeMultiple(dt) {
  const xs = Object.entries(dt?.by_code || {}).filter(([c]) => dt.base?.[c]).map(([c, v]) => v / dt.base[c]);
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}

// research/exp7 (60 days, 2026-10-25): the next day's 盤中 (close − open) by that multiple
export const SURGE_TIP = '當沖比是成員平常（前 20 個交易日）的幾倍。回測 60 天：≥ 1.3 倍隔天盤中平均 −0.88%（215 檔次）、≥ 1.5 倍 −1.50%（75）、≥ 2 倍 −2.42%（16）、≥ 3 倍 −3.66%（4）；倍數越高，隔天越常開高走低。只是統計，不是交易建議';
export function surgeText(dt) {
  const x = surgeMultiple(dt);
  return x == null ? '' : `<small class="${x >= 1.5 ? 'up' : 'muted'}" title="${escapeHtml(SURGE_TIP)}">（平常的 ${x.toFixed(1)} 倍）</small>`;
}

/** The ✓／✗ list with its count: 「9 項裡 6 項支持（2 項資料不足）」. */
export function evidenceHtml(items) {
  const counted = items.filter((x) => !INFO_ONLY.has(x.key));
  const yes = counted.filter((x) => x.ok === true).length;
  const no = counted.filter((x) => x.ok === false).length;
  const none = counted.length - yes - no;
  const mark = (ok, key) => (INFO_ONLY.has(key) ? '<span class="muted" title="參考，不計票">◦</span>' : ok === true ? '<b class="up">✓</b>' : ok === false ? '<b class="down">✗</b>' : '<span class="muted">—</span>');
  const rows = items
    .map((x) => `<li class="${x.ok === null || INFO_ONLY.has(x.key) ? 'muted' : ''}">${mark(x.ok, x.key)} ${escapeHtml(x.label)}${INFO_ONLY.has(x.key) ? '（參考，不計票）' : ''} <small class="muted">${escapeHtml(x.detail)}</small>${baseText(x.key) ? ` <small class="muted ev-base" title="這一項在同一天隨機挑兩檔時，本來就有這個比例會成立；越低越有參考價值（不影響 ✓ 計數）">（${escapeHtml(baseText(x.key))}）</small>` : ''}</li>`)
    .join('');
  return `<div class="ev-box"><div class="ev-h">🧾 資金一致性證據 <b>${yes}</b>／${yes + no} 項支持${none ? ` <small class="muted">（${none} 項資料不足，不算）</small>` : ''}</div><ul class="ev-list">${rows}</ul></div>`;
}

/** The same checks for an N group (each decided by most of its pairs on the server): [{key, ok, short, detail}]. */
export function groupEvidenceItems(g) {
  const ev = g.evidence || {};
  const pick = (k) => ev[k] || {};
  const items = [];
  const add = (key, ok, short, detail) => items.push({ key, ok: ok ?? null, short, detail });
  add('excess', pick('excess').ok, '扣指數', pick('excess').r === null || pick('excess').r === undefined ? '資料不足' : `扣掉各自的類股／大盤後分時 R ${r2(pick('excess').r)}`);
  add('baseline', pick('baseline').ok, '隨機', pick('baseline').pct === null || pick('baseline').pct === undefined ? '資料不足' : `分時 R 高於 ${Math.round(pick('baseline').pct)}% 的同日隨機配對`);
  add('burst', pick('burst').ok, '同爆量', pick('burst').co === null || pick('burst').co === undefined ? '沒有 1 分 K' : `同分鐘爆量 ${pick('burst').co} 次（隨機預期 ${pick('burst').expected}）`);
  add('sync', pick('sync').ok, '同分鐘', pick('sync').r0 === null || pick('sync').r0 === undefined ? '沒有 1 分 K' : `1 分 K R ${r2(pick('sync').r0)}，錯開 1～3 分 ${r2(pick('sync').r_off)}`);
  const seen = g.seen_days?.length;
  add('repeat', g.checked_days ? seen > 0 : null, '不只一天', g.checked_days ? `近 ${g.checked_days + 1} 天第 ${(seen || 0) + 1} 次` : '沒有前幾天的結果');
  add('chips', g.evidence_chips?.ok, '籌碼', g.evidence_chips?.pairs ? '投信同日買賣／今天法人同方向' : '法人還沒公布');
  const rel = (g.relations?.length || 0) + (g.rel_group ? 1 : 0);
  add('relation', rel ? true : null, '持股', rel ? '有持股／集團關係' : '沒有（或還沒抓到）');
  add('samedir', pick('samedir').ok, '同漲跌', pick('samedir').share === null || pick('samedir').share === undefined ? '資料不足' : `近期 ${Math.round(pick('samedir').share * 100)}% 的日子同方向`);
  add('shape', pick('shape').ok, '形狀', pick('shape').dtw === null || pick('shape').dtw === undefined ? '資料不足' : `日內走勢形狀的 DTW 距離 ${r2(pick('shape').dtw)}（≤ 2.5 算相似）`);
  const br = g.branches;
  add('branch', br ? br.ok : null, '同分點',
    br ? (br.common.length ? `${br.common[0].name} 同時買超 ${Object.keys(br.common[0].ranks).length} 檔` : '買超前 15 名沒有共同分點') : '分點還沒抓（收盤後快照）');
  add('theme', g.themes?.length ? true : null, '題材', g.themes?.length ? `📰 ${g.themes.map((t) => t.term).slice(0, 3).join('、')}` : '沒有共同題材');
  return items;
}

/** {yes, known}: how many checks support, out of those with data. */
export function evidenceCount(items) {
  items = items.filter((x) => !INFO_ONLY.has(x.key)); // shown, not counted
  const known = items.filter((x) => x.ok !== null).length;
  return { yes: items.filter((x) => x.ok === true).length, known };
}

/** A strong case: at least 5 checks, and 70% of those with data, support it. */
export const isStrong = ({ yes, known }) => yes >= 5 && known > 0 && yes / known >= 0.7;

/** N card's compact line: 「🧾 證據 6/8 ✓扣大盤 ✓隨機 ✗同爆量 …」, the full wording in the title. */
export function groupEvidenceHtml(g) {
  const items = groupEvidenceItems(g);
  const { yes, known } = evidenceCount(items);
  if (!known) return '';
  const tip = items.map((x) => `${x.ok === true ? '✓' : x.ok === false ? '✗' : '—'} ${x.short}：${x.detail}${baseText(x.key) ? `（${baseText(x.key)}）` : ''}`).join('\n');
  const tags = items
    .filter((x) => x.ok !== null && !INFO_ONLY.has(x.key))
    .map((x) => `<span class="${x.ok ? 'up' : 'down'}">${x.ok ? '✓' : '✗'}${escapeHtml(x.short)}</span>`)
    .join(' ');
  return `<div class="fd-ev${isStrong({ yes, known }) ? ' strong' : ''}" title="${escapeHtml(`資金一致性證據（每項看群內多數組合）\n${tip}`)}">🧾 證據 <b>${yes}</b>/${known} ${tags}</div>`;
}
