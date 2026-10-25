// 分頁3 · 盤後報告: the day's N groups read as a story (A1) and how each one fared over the last days (A3).
// Built from N's stored results under the default 條件 (找法1 + 找法2, precomputed after the close), so
// opening it starts no search for past days. Pure functions first (node --test), the panel last.

import { escapeHtml, fmtPct } from './transform.js';
import { evidenceCount, groupEvidenceItems, isStrong, SURGE_X, surgeMultiple, surgeText } from './evidence.js';
import { mountStockView } from './stockview.js';

export const DUMP_VOL = 2; // 出貨: a member that fell on 2× its 5-day volume the day the group vanished
export const GONE_SHOWN = 12; // 消失 rows listed before 「其餘 N 組」
export const TRACK_DAYS = 5; // the 跨天追蹤 columns: the report day and the 4 trading days before it

const keyOf = (g) => g.members.map((m) => m.code).sort().join(',');
const namesOf = (g) => g.members.map((m) => m.name).join('、');

/** Both methods' groups of one day, one entry per member set (the one with more support kept), by support. */
export function mergeGroups(known, fresh) {
  const by = new Map();
  for (const [res, method] of [[known, 'known'], [fresh, 'new']]) {
    for (const g of res?.groups || []) {
      const ev = evidenceCount(groupEvidenceItems(g));
      const k = keyOf(g);
      const old = by.get(k);
      if (!old || ev.yes > old.ev.yes) by.set(k, { g, method, ev, key: k, strong: isStrong(ev) });
    }
  }
  return [...by.values()].sort((a, b) => b.ev.yes - a.ev.yes || b.ev.known - a.ev.known || b.g.members.length - a.g.members.length);
}

/**
 * 動機判讀: what the money looks like, from the evidence a group has — at most two readings, the
 * strongest first. [{label, text}]
 */
export function motiveOf(g, items) {
  const ok = (k) => items.find((x) => x.key === k)?.ok === true;
  const out = [];
  const br = g.branches?.common?.[0];
  if (ok('branch') && br) out.push({ label: '同分點布局', text: `${br.name} 同時買超 ${Object.keys(br.ranks).length} 檔，像同一批資金布局` });
  if (ok('relation')) out.push({ label: '集團／持股', text: g.rel_group ? `同屬以 ${g.rel_group.leader_name} 為首的集團` : '成員之間有轉投資或董監關係' });
  if (g.daytrade?.hot) {
    // research/exp7: only the ones well above their usual faded the next day; most 🔥 are day-traded this much anyway
    const x = surgeMultiple(g.daytrade);
    out.push(x != null && x >= SURGE_X
      ? { label: '隔日沖熱錢突然湧入', text: `當沖比是平常的 ${x.toFixed(1)} 倍，回測這類隔天常開高走低` }
      : { label: '當沖偏高（平常就高）', text: '這幾檔本來就常被當沖，回測隔天倒貨的傾向不明顯' });
  }
  if (ok('chips') || g.inst?.lots > 0) out.push({ label: '法人同步', text: g.inst?.lots > 0 ? `法人合計買超 ${g.inst.lots.toLocaleString()} 張` : '投信／法人同方向' });
  if (ok('theme')) out.push({ label: '題材輪動', text: `同題材：${g.themes.map((t) => t.term).slice(0, 3).join('、')}` });
  if (!out.length && (ok('burst') || ok('sync'))) out.push({ label: '一籃子下單', text: '同一分鐘同時爆量／同步，像程式或一籃子單' });
  if (!out.length) out.push({ label: '只有價格同步', text: '缺少資金面的證據，可能是巧合或同一則消息' });
  return out.slice(0, 2);
}

/** 明天觀察: what would confirm (or undo) the reading tomorrow. */
export function watchOf(g, streak) {
  const out = [];
  if (streak >= 1) out.push(`是否連續第 ${streak + 1} 天同步`);
  const br = g.branches?.ok && g.branches.common?.[0];
  if (br) out.push(`${br.name} 是否續買`);
  if (g.inst?.lots > 0) out.push('法人是否續買');
  if (g.daytrade?.hot && (surgeMultiple(g.daytrade) ?? 0) >= SURGE_X) out.push('開盤是否開高走低（隔日沖出貨）');
  return out.length ? out : ['是否再一起動'];
}

/**
 * 跨天追蹤: days oldest → newest (the last is the report day), each a merged list (null = not computed).
 * → rows [{key, names, cells: [yes|null|undefined per day], status, streak, members}] — the report day's
 * groups first (as merged), then the ones that were there yesterday and are gone today.
 */
export function trackRows(days) {
  const n = days.length;
  const at = days.map((list) => (list ? new Map(list.map((x) => [x.key, x])) : null));
  const today = days[n - 1] || [];
  const keys = [...today.map((x) => x.key)];
  for (const x of days[n - 2] || []) if (!keys.includes(x.key)) keys.push(x.key);
  return keys.map((k) => {
    const cells = at.map((m) => (m === null ? undefined : m.get(k)?.ev.yes ?? null)); // undefined: that day not computed
    const ref = at.map((m) => m?.get(k)).filter(Boolean).at(-1);
    let streak = 0;
    for (let i = n - 1; i >= 0 && cells[i] !== null && cells[i] !== undefined; i--) streak++;
    const before = cells.slice(0, -1).some((c) => c !== null && c !== undefined);
    const now = cells[n - 1];
    const prev = cells[n - 2];
    let status;
    if (now === null || now === undefined) status = prev !== null && prev !== undefined ? '消失（資金撤出？）' : '—';
    else if (streak >= 2) status = now > prev ? `連 ${streak} 天・證據變強` : `連 ${streak} 天`;
    else status = before ? '再度出現' : '新出現';
    return { key: k, names: namesOf(ref.g), members: ref.g.members, cells, status, streak: Math.max(0, streak - 1) };
  });
}

/**
 * The day a group vanished: did its members fall on heavy volume (爆量收黑 — Wyckoff's distribution, the
 * money leaving) or just stop moving together? quotes {code: {change_pct, vol_ratio}} of that day.
 * → '疑似出貨' / '不再同步' / null (no quotes).
 */
export function goneReading(members, quotes) {
  const q = members.map((m) => quotes?.[m.code]).filter((x) => x && x.change_pct != null);
  if (!q.length) return null;
  const dumped = q.filter((x) => x.change_pct < 0 && x.vol_ratio != null && x.vol_ratio >= DUMP_VOL).length;
  return dumped * 2 >= q.length ? '疑似出貨' : '不再同步';
}

/**
 * The 🔥 groups' next day: for each day of the window but the last, its 🔥 groups' members on the day
 * after (quotesByDay[next date]). → {groups, stocks, gap, close, intraday, fadeShare} (means in %, the
 * share of members that closed below their open), or null without any sample.
 */
export function hotNextDay(dates, per, quotesByDay) {
  const gaps = [];
  const closes = [];
  const intra = [];
  let groups = 0;
  for (let k = 0; k < dates.length - 1; k++) {
    const q = quotesByDay[dates[k + 1]];
    if (!q) continue;
    for (const x of per[k] || []) {
      if (!x.g.daytrade?.hot) continue;
      let any = false;
      for (const m of x.g.members) {
        const v = q[m.code];
        if (!v || v.change_pct == null) continue;
        any = true;
        closes.push(v.change_pct);
        if (v.gap_pct != null) gaps.push(v.gap_pct);
        if (v.intraday_pct != null) intra.push(v.intraday_pct);
      }
      if (any) groups++;
    }
  }
  if (!closes.length) return null;
  const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  return { groups, stocks: closes.length, gap: mean(gaps), close: mean(closes), intraday: mean(intra), fadeShare: intra.length ? (intra.filter((v) => v < 0).length / intra.length) * 100 : null };
}

/** 高點錯開但走勢一樣: the pairs N drops only for their highs being far apart, whose session shapes are
 *  as close as N's own (both methods' lists merged, closest first). Shown only — not groups, no evidence. */
export function shapeList(results) {
  const by = new Map();
  for (const res of results) for (const x of res?.shape_pairs || []) {
    const k = x.members.map((m) => m.code).sort().join(',');
    if (!by.has(k) || by.get(k).dtw > x.dtw) by.set(k, x);
  }
  return [...by.values()].sort((a, b) => a.dtw - b.dtw);
}

export function shapeHtml(results) {
  const list = shapeList(results);
  if (!list.length) return '';
  const rows = list
    .map((x) => `<li>${x.members.map((m) => `<button type="button" class="rp-stock" data-code="${escapeHtml(m.code)}">${escapeHtml(m.name)} <small class="${m.change_pct > 0 ? 'up' : m.change_pct < 0 ? 'down' : ''}">${fmtPct(m.change_pct)}</small></button>`).join(' ')}
      <small class="muted">高點差 ${x.peak_gap} 分、扣指數 R ${x.excess_r == null ? '—' : x.excess_r.toFixed(2)}、形狀距離 ${x.dtw.toFixed(2)}</small></li>`)
    .join('');
  return `<div class="rp-shape" title="這些配對只因「日內最高點差太多」沒被 N 收進群組，但兩檔整天的走勢形狀跟 N 找到的組合一樣像（DTW 距離 ≤ 2.5）：可能是同一批資金分批推進。只列出參考，不算群組、不算證據">
    <h3 class="rp-h">高點錯開但走勢一樣 <small class="muted">${list.length} 對；可能分批推進</small></h3><ul class="rp-hot-list">${rows}</ul></div>`;
}

export function highlightsHtml(lines, names = {}) {
  // names {name: code}: each known stock name in a line becomes a button (the chart area shows it)
  const known = Object.keys(names).filter(Boolean).sort((a, b) => b.length - a.length);
  const re = known.length ? new RegExp(known.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g') : null;
  const line = (t) => {
    if (!re) return escapeHtml(t);
    let out = '';
    let last = 0;
    for (const m of t.matchAll(re)) {
      out += escapeHtml(t.slice(last, m.index)) + `<button type="button" class="rp-stock rp-inline" data-code="${escapeHtml(names[m[0]])}">${escapeHtml(m[0])}</button>`;
      last = m.index + m[0].length;
    }
    return out + escapeHtml(t.slice(last));
  };
  return lines.length ? `<div class="rp-top"><b>今天重點</b><ul>${lines.map((t) => `<li>${line(t)}</li>`).join('')}</ul></div>` : '';
}

/** {name: code} of every stock the report mentions (groups, shape pairs, 回測 list), for highlightsHtml. */
export function nameIndex(list, shapes = [], pulls = []) {
  const out = {};
  for (const x of list) for (const m of x.members || []) out[m.name] = m.code;
  for (const s of shapes) for (const m of s.members || []) out[m.name] = m.code;
  for (const p of pulls) out[p.name] = p.code;
  return out;
}

/** The 🔥 groups, the furthest above their usual 當沖比 first (research/exp7: the likeliest to fade). */
export function hotByMultiple(list) {
  return list.filter((x) => x.g.daytrade?.hot).sort((a, b) => (surgeMultiple(b.g.daytrade) ?? 0) - (surgeMultiple(a.g.daytrade) ?? 0));
}

/** 🔥 隔日沖警示: today's 🔥 groups (risk, not evidence) and how such groups did the next day. */
export function hotHtml(list, stats, wide = null) {
  const hot = hotByMultiple(list);
  const p = (v) => (v == null ? '—' : `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${fmtPct(v)}</span>`);
  const rows = hot
    .map((x) => {
      const dt = x.g.daytrade;
      const cells = Object.entries(dt.by_code).map(([c, v]) => {
        const base = dt.base?.[c];
        return `${escapeHtml(x.g.members.find((m) => m.code === c)?.name || c)} ${v.toFixed(1)}%${base != null ? `<small class="muted">（平常 ${base.toFixed(0)}%）</small>` : ''}`;
      });
      return `<li>${escapeHtml(namesOf(x.g))}：平均 <b>${dt.avg.toFixed(1)}%</b>${surgeText(dt)}　${cells.join('、')}</li>`;
    })
    .join('');
  const s = stats
    ? `<p class="rp-line">前幾天的 🔥 組合（${stats.groups} 組、${stats.stocks} 檔次）隔天：開盤 ${p(stats.gap)}、收盤 ${p(stats.close)}、盤中（收盤 − 開盤）${p(stats.intraday)}，開高走低（收盤低於開盤）<b>${stats.fadeShare == null ? '—' : `${stats.fadeShare.toFixed(0)}%`}</b>。樣本少，只是統計觀察，不是交易建議。</p>`
    : '<p class="rp-line muted">前幾天沒有 🔥 組合可比對隔天表現。</p>';
  // every stored day under the same 條件: 🔥 against the rest, the bigger sample
  const w = wide?.stats;
  // every stored day under the same 條件, one table: 🔥 and its two kinds, the rest, and by evidence count
  const ev = w?.evidence || {};
  const tableRows = [
    ['🔥 全部', w?.hot],
    [`　突然湧入（≥ 平常 ${SURGE_X} 倍）`, w?.surge],
    ['　平常就這樣', w?.habitual],
    ['其他（非 🔥）', w?.rest],
    ['證據 ✓ ≥ 4 項', ev.ev4],
    ['證據 ✓ 2～3 項', ev.ev2],
    ['證據 ✓ ≤ 1 項', ev.ev0],
  ].filter(([, x]) => x && x.stocks);
  const pctCell = (v) => `<td>${p(v)}</td>`;
  const all = tableRows.length
    ? `<p class="rp-line muted">所有已存的日子（${wide.days} 天，${escapeHtml(wide.first || '')}～${escapeHtml(wide.last || '')}）成員的隔天表現：</p>
      <table class="grid rp-next" title="證據數＝伺服器端 5 項（扣指數、隨機、同爆量、同分鐘、同漲跌）的 ✓ 數，沒資料不算 ✓；同爆量、同分鐘要 1 分 K（約 30 天內），所以 ✓ ≥ 4 只會出現在最近的日子。研究（study 分支 exp5）：證據越少、後續越強">
      <thead><tr><th class="l">組合</th><th>組／檔次</th><th>開盤</th><th>收盤</th><th>扣大盤</th><th>開高走低</th><th>收黑</th></tr></thead><tbody>${tableRows
        .map(([label, x]) => `<tr><td class="l">${escapeHtml(label)}</td><td>${x.groups}／${x.stocks}</td>${pctCell(x.gap)}${pctCell(x.close)}${pctCell(x.excess)}<td>${x.fade == null ? '—' : `${x.fade.toFixed(0)}%`}</td><td>${x.down == null ? '—' : `${x.down.toFixed(0)}%`}</td></tr>`)
        .join('')}</tbody></table>`
    : '';
  return `<div class="rp-hot" title="有當沖資料的成員平均當沖比 ≥ 50%：隔日沖短線熱錢，隔天常開高走低；這是風險警示，不算在資金證據裡">
    <h3 class="rp-h">🔥 隔日沖警示 <small class="muted">${hot.length} 組；隔天常開高走低</small></h3>
    ${hot.length ? `<ul class="rp-hot-list">${rows}</ul>` : '<p class="rp-line muted">今天沒有 🔥 組合。</p>'}${s}${all}</div>`;
}

/** 🔥 突然湧入: a 🔥 group whose members day-trade at 2× their usual or more (on average). */
export function isSurge(g) {
  return !!g.daytrade?.hot && (surgeMultiple(g.daytrade) ?? 0) >= SURGE_X;
}

/**
 * 今天重點: at most five lines of what stands out today, from what the report already worked out —
 * 突然湧入 hot money, 疑似出貨, 證據強, a run of days, and the closest 高點錯開 pair. [text]
 */
export const LOCKED_MIN = 3; // 整群漲停: at least this many members …
export const LOCKED_SHARE = 0.6; // … and this share of them locked at limit-up
export const LOCKED_TIP = '這一群大部分成員鎖漲停：大量資金同時進場。鎖住的股票分時是一條平線，扣指數、隨機、同爆量、同分鐘這幾項證據算不出來或不準，所以證據數偏低、排在清單後面，不代表不重要';

/** 整群漲停: ≥ LOCKED_MIN members and ≥ LOCKED_SHARE of them locked at limit-up (N's 🔒). */
export function isLockedGroup(g) {
  const ms = g.members || [];
  return ms.length >= LOCKED_MIN && ms.filter((m) => m.locked_at).length >= LOCKED_SHARE * ms.length;
}

export function highlights(list, rows, shapes = [], pulls = []) {
  const out = [];
  const names = (xs) => xs.slice(0, 3).join('／') + (xs.length > 3 ? ` 等 ${xs.length} 組` : '');
  // the biggest money of the day first: whole groups locked at limit-up sink in the evidence order
  const locked = list.filter((x) => isLockedGroup(x.g)).sort((a, b) => (b.g.turnover_100m ?? 0) - (a.g.turnover_100m ?? 0));
  if (locked.length) {
    out.push(`🔒 整群漲停（大資金同時進場；證據數因鎖漲停偏低）：${locked.slice(0, 2).map((x) => `${x.g.members.slice(0, 4).map((m) => m.name).join('、')}${x.g.members.length > 4 ? ` 等 ${x.g.members.length} 檔` : ''}${x.g.turnover_100m != null ? `（${Math.round(x.g.turnover_100m)} 億）` : ''}`).join('／')}${locked.length > 2 ? ` 另 ${locked.length - 2} 群` : ''}`);
  }
  const surge = list.filter((x) => isSurge(x.g)).map((x) => namesOf(x.g));
  if (surge.length) out.push(`🔥 隔日沖熱錢突然湧入：${names(surge)}（歷史上這類隔天多半開高走低）`);
  const dumped = rows.filter((r) => r.status.includes('出貨')).map((r) => r.names);
  if (dumped.length) out.push(`疑似出貨（昨天的組合今天爆量收黑）：${names(dumped)}`);
  const strong = list.filter((x) => x.strong).map((x) => namesOf(x.g));
  if (strong.length) out.push(`證據強（同步得很整齊；回測 60 天：證據多的組合後續不一定較強）：${names(strong)}`);
  const runs = rows.filter((r) => r.streak >= 2 && r.cells.at(-1) != null).map((r) => `${r.names}（連 ${r.streak + 1} 天）`);
  if (runs.length) out.push(`連續出現：${names(runs)}`);
  if (pulls?.length) out.push(`回測 10 日線（同步族群的觀察名單）：${pulls.slice(0, 3).map((x) => x.name).join('、')}${pulls.length > 3 ? ` 等 ${pulls.length} 檔` : ''}`);
  if (shapes.length) out.push(`高點錯開但走勢一樣：${shapes.slice(0, 2).map((x) => x.members.map((m) => m.name).join('＋')).join('／')}${shapes.length > 2 ? ` 等 ${shapes.length} 對` : ''}`);
  return out.slice(0, 6);
}

/** The report as plain text (複製成文字: for notes or a chat). */
export function reportText(date, index, list, rows, hotStats = null, shapes = [], pulls = []) {
  const lines = [`${date} 盤後報告`];
  if (index.length) lines.push(index.map((b) => `${b.name} ${fmtPct(b.change_pct)}`).join('　'));
  lines.push(`找到 ${list.length} 組，證據強 ${list.filter((x) => x.strong).length} 組，連續出現 ${rows.filter((r) => r.streak >= 1 && r.cells.at(-1) != null).length} 組`);
  const top = highlights(list, rows, shapes, pulls);
  if (top.length) lines.push('', '今天重點：', ...top.map((t) => `・${t}`));
  const hot = hotByMultiple(list);
  if (hot.length) {
    lines.push('', `🔥 隔日沖警示（${hot.length} 組，隔天常開高走低）：`);
    for (const x of hot) lines.push(`   ${namesOf(x.g)}：平均當沖比 ${x.g.daytrade.avg.toFixed(1)}%`);
    if (hotStats) lines.push(`   前幾天的 🔥 組合隔天：開盤 ${fmtPct(hotStats.gap)}、收盤 ${fmtPct(hotStats.close)}、開高走低 ${hotStats.fadeShare == null ? '—' : `${hotStats.fadeShare.toFixed(0)}%`}（${hotStats.stocks} 檔次）`);
  }
  list.forEach((x, i) => {
    const items = groupEvidenceItems(x.g);
    const row = rows.find((r) => r.key === x.key);
    lines.push('', `${i + 1}. ${namesOf(x.g)}（證據 ${x.ev.yes}/${x.ev.known}${x.strong ? '・證據強' : ''}）`);
    for (const m of motiveOf(x.g, items)) lines.push(`   ${m.label}：${m.text}`);
    lines.push(`   ✓ ${items.filter((t) => t.ok === true).map((t) => t.short).join('、') || '—'}　✗ ${items.filter((t) => t.ok === false).map((t) => t.short).join('、') || '—'}`);
    const conc = Object.entries(x.g.concentration || {});
    if (conc.length) {
      const name = (code) => x.g.members.find((m) => m.code === code)?.name || code;
      lines.push(`   籌碼集中度：${conc.map(([c, v]) => `${name(c)} ${v > 0 ? '+' : ''}${v.toFixed(1)}%`).join('、')}`);
    }
    const lag = x.g.laggards;
    if (lag?.stocks?.length) lines.push(`   同族群未跟上（${lag.label}：${lag.group}）：${lag.stocks.map((s) => `${s.name} ${fmtPct(s.change_pct)}`).join('、')}`);
    lines.push(`   明天觀察：${watchOf(x.g, row?.streak || 0).join('；')}`);
  });
  if (shapes.length) {
    lines.push('', `高點錯開但走勢一樣（${shapes.length} 對，可能分批推進）：`);
    for (const x of shapes) lines.push(`   ${x.members.map((m) => m.name).join('＋')}：高點差 ${x.peak_gap} 分、扣指數 R ${x.excess_r == null ? '—' : x.excess_r.toFixed(2)}`);
  }
  // the vanished ones, 疑似出貨 (爆量收黑) apart from the ones that only stopped moving together
  const gone = rows.filter((r) => r.status.startsWith('消失'));
  const dumped = gone.filter((r) => r.status.includes('出貨'));
  if (dumped.length) lines.push('', `消失・疑似出貨：${dumped.map((r) => r.names).join('／')}`);
  const rest = gone.filter((r) => !r.status.includes('出貨'));
  if (rest.length) lines.push('', `消失：${rest.map((r) => r.names).join('／')}`);
  return lines.join('\n');
}

// ---- HTML ---------------------------------------------------------------------------------------

const CHASE_TIP = '落後股今天帶量上漲（量比 ≥ 1.5），而且平常跟這組高度連動（20 日相關 ≥ 0.6）。回測 60 天（study 分支 exp8）：這類隔天平均跑輸大盤 1.63%，只有 22% 收紅；前後半段分開驗證，隔天都一樣弱（勝率 23%、20%），但 5 天後方向不一致——只適合當「隔天別追」的提醒';

const CROSS_TIP = '同步那天，這檔同時和兩批「完全不同的股票」各自同步（不是同一群股票換個分類），像是多路資金都在找它。回測 60 天（research/money_flow/intersection.py）：這類在回測 10 日線時，10 天後中位數跑贏大盤 7.4%（勝率 82%，40 筆），其他只有 −1.0%（45%）；但 15 筆集中在 7/31 同一天，扣掉後中位數 +2.9%（73%）。前後半段都成立，樣本仍少。只是統計，不是交易建議';

/** 回測 10 日線觀察名單 (research/exp8): members of a tight-sync group of the last 10 days at or under
 *  their 10-day average for the first time today. Shown only, not a recommendation. */
export function pullbackHtml(res) {
  const list = res?.stocks || [];
  if (!list.length) return '';
  const rows = list
    .map((x) => `<li><button type="button" class="rp-stock" data-code="${escapeHtml(x.code)}">${escapeHtml(x.name)}</button>
      ${x.cross ? `<b class="up" title="${escapeHtml(CROSS_TIP)}">⭐ 跨組交集</b> ` : ''}<small class="muted">收 ${x.close}／10 日線 ${x.ma}（${fmtPct(x.gap_pct)}）；${escapeHtml(x.since.slice(5))} 與 ${escapeHtml(x.with.join('、'))} 同步</small></li>`)
    .join('');
  return `<div class="rp-pull" title="過去 10 個交易日內同步很強（證據 ✓ ≥ 4，或扣指數 R ≥ 0.6）的組合成員，今天第一次收在 10 日線以下：等下一波資金，而不是追同步當天。回測 60 天（study 分支 exp8）：在這種回測點買，10 天後平均跑贏大盤 4.31%（勝率 55%），同步當天就買只有 1.63%（42%）。只是統計，不是交易建議">
    <h3 class="rp-h">回測 10 日線 <small class="muted">${list.length} 檔；同步族群的觀察名單</small></h3><ul class="rp-hot-list">${rows}</ul></div>`;
}

/** 補漲候選: the same known group's stocks that have not followed yet (where the money may go next). */
export function laggardsHtml(g) {
  const l = g.laggards;
  if (!l?.stocks?.length) return '';
  const cells = l.stocks.map((s) => `<button type="button" class="rp-stock" data-code="${escapeHtml(s.code)}" title="${escapeHtml(s.code)}：開資訊卡">${escapeHtml(s.name)} <small class="${s.change_pct > 0 ? 'up' : s.change_pct < 0 ? 'down' : ''}">${fmtPct(s.change_pct)}</small>${s.chase ? ` <b class="down" title="${escapeHtml(CHASE_TIP)}">⚠ 帶量跟漲</b>` : ''}</button>`);
  return `<div class="rp-line" title="同一個已知分類裡、今天漲不到這組平均一半的股票（成交值大的先列）。回測（study 分支 exp6，60 天）：這些股票之後 5 天平均只跑贏大盤 0.74%，遠不如組合本身的 +2.22%——資金多半留在原本那幾檔，沒有明顯擴散。只是參考，不是買進建議"><span class="muted">同族群未跟上（${escapeHtml(l.label)}：${escapeHtml(l.group)}）</span> ${cells.join(' ')}</div>`;
}

/** 籌碼集中度 of the members with 分點 stored (the after-close snapshot), one line; '' without any. */
export function concHtml(g) {
  const c = g.concentration;
  if (!c || !Object.keys(c).length) return '';
  const name = (code) => g.members.find((m) => m.code === code)?.name || code;
  const cells = Object.entries(c).map(([code, v]) => `${escapeHtml(name(code))} <span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : ''}${v.toFixed(1)}%</span>`);
  return `<div class="rp-line" title="籌碼集中度＝（前 15 大買超分點買超 − 前 15 大賣超分點賣超）÷ 成交量；正數＝籌碼往大買家集中"><span class="muted">籌碼集中度</span> ${cells.join('、')}</div>`;
}

function groupHtml(x, i, row) {
  const items = groupEvidenceItems(x.g);
  const chips = items
    .filter((t) => t.ok !== null)
    .map((t) => `<span class="${t.ok ? 'up' : 'down'}" title="${escapeHtml(t.detail)}">${t.ok ? '✓' : '✗'}${escapeHtml(t.short)}</span>`)
    .join(' ');
  const members = x.g.members
    .map((m) => `<button type="button" class="rp-stock" data-code="${escapeHtml(m.code)}" title="${escapeHtml(m.code)}：看圖">${escapeHtml(m.name)} <small class="${m.change_pct > 0 ? 'up' : m.change_pct < 0 ? 'down' : ''}">${fmtPct(m.change_pct)}</small>${m.locked_at ? ' 🔒' : ''}</button>`)
    .join('');
  const motive = motiveOf(x.g, items).map((m) => `<b>${escapeHtml(m.label)}</b>：${escapeHtml(m.text)}`).join('；');
  return `<div class="rp-group">
    <div class="rp-gh"><span class="rp-n">${i + 1}</span>${members}<span class="rp-ev${x.strong ? ' strong' : ''}" title="資金一致性證據：有資料的項目中 ✓ 的數量">${x.strong ? '證據強 ' : '證據 '}${x.ev.yes}/${x.ev.known}</span>
      <span class="muted rp-src">${x.method === 'new' ? '找法2' : '找法1'}</span>${isLockedGroup(x.g) ? ` <b class="up rp-locked" title="${escapeHtml(LOCKED_TIP)}">🔒 整群漲停</b>` : ''}</div>
    <div class="rp-line"><span class="muted">動機判讀</span> ${motive}</div>${concHtml(x.g)}${laggardsHtml(x.g)}
    <div class="rp-line rp-chips">${chips || '<span class="muted">沒有證據資料</span>'}</div>
    <div class="rp-line"><span class="muted">明天觀察</span> ${escapeHtml(watchOf(x.g, row?.streak || 0).join('；'))}</div>
  </div>`;
}

/** Yesterday's group today: its members' mean 漲跌 and that less 大盤; null for a group new today. */
export function todayMove(row, n, quotes, market) {
  if (n < 2 || row.cells[n - 2] == null || !quotes) return null;
  const xs = row.members.map((m) => quotes[m.code]?.change_pct).filter((v) => v != null);
  if (!xs.length) return null;
  const avg = xs.reduce((a, b) => a + b, 0) / xs.length;
  return { avg, excess: market == null ? null : avg - market };
}

export function trackHtml(dates, rows) {
  if (!rows.length) return '<p class="hint">這幾天沒有找到群組。</p>';
  const cell = (c) => (c === undefined ? '<td class="muted" title="那天還沒計算">…</td>' : c === null ? '<td class="muted">—</td>' : `<td class="up">✓ ${c}</td>`);
  const moveCell = (t) => (t ? `<td class="${t.avg > 0 ? 'up' : t.avg < 0 ? 'down' : ''}" title="${t.excess == null ? '' : `扣大盤 ${fmtPct(t.excess)}`}">${fmtPct(t.avg)}${t.excess == null ? '' : `<small class="muted">（${fmtPct(t.excess)}）</small>`}</td>` : '<td class="muted">—</td>');
  const cls = (s) => (s.includes('出貨') ? 'down rp-dump' : s.startsWith('消失') ? 'muted' : s.includes('變強') || s.startsWith('連') ? 'up' : '');
  return `<table class="grid rp-track"><thead><tr><th class="l">群組</th>${dates.map((d) => `<th title="${escapeHtml(d)}">${Number(d.slice(8))}日</th>`).join('')}<th title="昨天就有的組合：成員今天的平均漲跌（括號＝扣掉大盤）">今天漲跌</th><th class="l">今天狀態</th></tr></thead><tbody>${rows
    .map((r) => `<tr><td class="l" title="${escapeHtml(r.names)}">${escapeHtml(r.names)}</td>${r.cells.map(cell).join('')}${moveCell(r.today)}<td class="l ${cls(r.status)}">${escapeHtml(r.status)}</td></tr>`)
    .join('')}</tbody></table>`;
}

// ---- panel --------------------------------------------------------------------------------------

/** 分頁3: refresh(date, days) with the trading days list (newest first) to find the 4 days before. */
export function mountReportPanel(container, { onStock, query, condText }) {
  const sec = document.createElement('section');
  sec.className = 'panel hReport';
  sec.innerHTML = `<div class="panel-h"><b>盤後報告</b><span class="muted" id="rpNote"></span>
      <button type="button" class="icon wide" id="rpCopy" style="margin-left:auto" title="把報告複製成純文字">複製成文字</button></div>
    <div class="rp-head"></div><div class="rp-viewer"></div>
    <div class="rp-body"><p class="hint">選一天看報告。</p></div>`;
  container.append(sec);
  const body = sec.querySelector('.rp-body');
  const headEl = sec.querySelector('.rp-head');
  // the chart area sits outside the parts redrawn as the data arrives, so a clicked stock stays drawn
  const viewer = mountStockView(sec.querySelector('.rp-viewer'), { onCard: (code) => onStock?.(code) });
  const state = { ticket: 0, text: '', allGone: false, last: null };

  async function result(date, method, storedOnly) {
    // 分頁2's 條件 (query): the result stored under exactly those (any change is another key, so a
    // changed 條件 computes again rather than reusing what the old ones found)
    const q = new URLSearchParams(query ? query(method) : { method });
    q.set('date', date);
    if (storedOnly) q.set('stored_only', '1');
    const r = await fetch(`/api/history/finder?${q}`, { cache: 'no-store' });
    const j = r.ok ? await r.json() : null;
    return j && (j.stored === false ? null : j);
  }

  async function refresh(date, days) {
    if (!date) return;
    const t = ++state.ticket;
    state.last = null; // nothing of the previous date may be drawn again (a late next-day reply)
    sec.querySelector('#rpNote').textContent = `找法1＋找法2，用分頁2 的條件：${condText ? condText() : '預設'}`;
    body.innerHTML = '<p class="hint">整理中…</p>';
    headEl.innerHTML = '';
    state.date = date;
    viewer.setDate(date);
    const i = days.indexOf(date);
    const dates = days.slice(i, i + TRACK_DAYS).reverse(); // oldest → the report day
    state.dayResults = [];
    const one = async (d, stored) => {
      const [known, fresh] = await Promise.all([result(d, 'known', stored), result(d, 'new', stored)]);
      if (d === date) state.dayResults = [known, fresh]; // the report day's own lists (高點錯開但走勢一樣)
      return known || fresh ? mergeGroups(known, fresh) : null;
    };
    let index;
    let per;
    // 回測 10 日線 for the report day (stored results only), fetched alongside
    state.pullbacks = null;
    fetch(`/api/history/pullbacks?${new URLSearchParams({ ...Object.fromEntries(new URLSearchParams(query ? query('known') : {})), date })}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((x) => {
        if (t !== state.ticket || !x) return;
        state.pullbacks = x;
        if (state.last?.[0] === date) draw(...state.last);
      })
      .catch(() => {});
    // 隔天表現 over every stored day under these 條件 (🔥 against the rest), fetched alongside
    state.wide = null;
    fetch(`/api/history/next-day?${new URLSearchParams(query ? query('known') : {})}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((w) => {
        if (t !== state.ticket || !w) return;
        state.wide = w;
        if (state.last?.[0] === date) draw(...state.last);
      })
      .catch(() => {});
    try {
      // the report day may compute; the days before only if stored (the rest is worked out below)
      [index, ...per] = await Promise.all([
        fetch(`/api/history/index-race?date=${encodeURIComponent(date)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ...dates.map((d) => one(d, d !== date)),
      ]);
    } catch (err) {
      if (t === state.ticket) body.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
      return;
    }
    if (t !== state.ticket) return;
    const bench = (index?.benchmarks || []).map((b) => ({ name: b.name.replace(/（.*）/, ''), change_pct: b.change_pct }));
    // the vanished groups' members on the report day: 爆量收黑 or not
    // the members on the report day of every group that was there the day before: the vanished ones'
    // 出貨 check, and how yesterday's groups did today
    const n = dates.length;
    const goneCodes = trackRows(per).filter((r) => n >= 2 && r.cells[n - 2] != null).flatMap((r) => r.members.map((m) => m.code));
    state.quotes = null;
    if (goneCodes.length) {
      try {
        const r = await fetch(`/api/history/quotes?date=${encodeURIComponent(date)}&codes=${encodeURIComponent([...new Set(goneCodes)].join(','))}`);
        state.quotes = r.ok ? (await r.json()).quotes : null;
      } catch {
        state.quotes = null;
      }
      if (t !== state.ticket) return;
    }
    await loadNextQuotes(t, dates, per);
    if (t !== state.ticket) return;
    draw(date, dates, per, bench, '');
    // the days before that N never computed (only the latest 5 are, after the close): one at a time, the
    // nearest first, the table filled in as each one lands; a new date stops it
    const missing = dates.map((d, k) => [d, k]).filter(([d, k]) => d !== date && per[k] === null).reverse();
    for (const [n, [d, k]] of missing.entries()) {
      draw(date, dates, per, bench, `往前計算 ${d.slice(5)}（${n + 1}/${missing.length}，舊日子一天約 1～2 分鐘）…`);
      try {
        per[k] = (await one(d, false)) || [];
      } catch {
        per[k] = [];
      }
      if (t !== state.ticket) return;
    }
    if (missing.length) {
      await loadNextQuotes(t, dates, per); // the days worked out may have 🔥 groups of their own
      if (t !== state.ticket) return;
      draw(date, dates, per, bench, '');
    }
  }

  // the members of each day's 🔥 groups on the trading day after (for hotNextDay)
  async function loadNextQuotes(t, dates, per) {
    const want = {};
    dates.slice(0, -1).forEach((d, k) => {
      const codes = (per[k] || []).filter((x) => x.g.daytrade?.hot).flatMap((x) => x.g.members.map((m) => m.code));
      if (codes.length) want[dates[k + 1]] = [...new Set(codes)];
    });
    state.nextQuotes = state.nextQuotes || {};
    for (const [d, codes] of Object.entries(want)) {
      try {
        const r = await fetch(`/api/history/quotes?date=${encodeURIComponent(d)}&codes=${encodeURIComponent(codes.join(','))}`);
        state.nextQuotes[d] = { ...(state.nextQuotes[d] || {}), ...(r.ok ? (await r.json()).quotes : {}) };
      } catch {
        /* that day stays without a sample */
      }
      if (t !== state.ticket) return;
    }
  }

  function draw(date, dates, per, bench, progress) {
    state.last = [date, dates, per, bench, progress];
    const list = per.at(-1) || [];
    const rows = trackRows(per);
    const market = bench.find((b) => b.name.startsWith('大盤'))?.change_pct ?? null;
    for (const r of rows) {
      r.today = todayMove(r, dates.length, state.quotes, market);
      if (r.status.startsWith('消失') && state.quotes) {
        const g = goneReading(r.members, state.quotes);
        if (g) r.status = g === '疑似出貨' ? '消失・疑似出貨（爆量收黑）' : '消失・不再同步';
      }
    }
    // the gone ones can outnumber today's many times over: the first few, the rest on demand
    const gone = rows.filter((r) => r.status.startsWith('消失'));
    const shownRows = state.allGone ? rows : rows.filter((r) => !r.status.startsWith('消失') || gone.indexOf(r) < GONE_SHOWN);
    const more = gone.length > GONE_SHOWN
      ? `<button type="button" class="icon wide rp-gone">${state.allGone ? '收起消失的組合' : `其餘 ${gone.length - GONE_SHOWN} 組消失的組合`}</button>`
      : '';
    const hotStats = hotNextDay(dates, per, state.nextQuotes || {});
    state.text = reportText(date, bench, list, rows, hotStats, shapeList(state.dayResults || []), state.pullbacks?.stocks || []);
    const strong = list.filter((x) => x.strong).length;
    const going = rows.filter((r) => r.streak >= 1 && r.cells.at(-1) != null).length;
    const metric = (label, value) => `<div class="rp-metric"><small>${label}</small><b>${value}</b></div>`;
    // 摘要 across the top; the day's groups (A1) on the left, 跨天追蹤 (A3) on the right
    headEl.innerHTML = `<div class="rp-metrics">${metric('找到群組', list.length)}${metric('證據強', strong)}${metric('連續出現', going)}${metric('大盤／櫃買', bench.map((b) => `<span class="${b.change_pct > 0 ? 'up' : 'down'}">${fmtPct(b.change_pct)}</span>`).join(' / ') || '—')}</div>`;
    const shapes = shapeList(state.dayResults || []);
    const pulls = state.pullbacks?.stocks || [];
    body.innerHTML = `${highlightsHtml(highlights(list, rows, shapes, pulls), nameIndex(list, shapes, pulls))}
      <div class="rp-cols"><div class="rp-left">
        ${hotHtml(list, hotStats, state.wide)}
        ${shapeHtml(state.dayResults || [])}
        ${pullbackHtml(state.pullbacks)}
        <h3 class="rp-h">${escapeHtml(date)} 找到的群組</h3>
        ${list.length ? list.map((x, k) => groupHtml(x, k, rows.find((r) => r.key === x.key))).join('') : '<p class="hint">這一天沒有找到群組（或 N 還沒算完，稍後再開）。</p>'}
      </div><div class="rp-right">
        <h3 class="rp-h">跨天追蹤 <small class="muted">格子＝那天的證據 ✓ 數；— 那天沒有這組；… 還沒計算</small></h3>
        ${progress ? `<p class="hint rp-progress">${escapeHtml(progress)}</p>` : ''}
        <div class="rp-track-wrap">${trackHtml(dates, shownRows)}${more}</div>
      </div></div>`;
  }

  body.addEventListener('click', (e) => {
    if (e.target.closest('.rp-gone') && state.last) {
      state.allGone = !state.allGone;
      draw(...state.last);
      return;
    }
    const b = e.target.closest('.rp-stock');
    if (!b || !state.date) return;
    // the name is the button's own text (before the % in <small>)
    const name = (b.childNodes[0]?.textContent || b.textContent).trim().split(/\s/)[0];
    viewer.show(b.dataset.code, name, state.date);
    viewer.el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
  sec.querySelector('#rpCopy').addEventListener('click', async (e) => {
    try {
      await navigator.clipboard.writeText(state.text);
      e.target.textContent = '已複製';
    } catch {
      e.target.textContent = '複製失敗';
    }
    setTimeout(() => (e.target.textContent = '複製成文字'), 1500);
  });
  return { refresh };
}
