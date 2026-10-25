// N · 資金連動搜尋: groups of stocks one pool of money moved together (server: /api/history/finder).
// 找法1 searches inside the known groupings, 找法2 a pool from the day's rankings. Every threshold is
// tunable here and kept in localStorage. A result card opens K's view for its members
// (/api/history/linkage-codes). Pure HTML builders on top (node --test); panel wiring below.

import { addToGroup, createGroup } from './groups.js';
import { renderBody } from './linkage.js';
import { relationsHtml } from './relations.js';
import { evidenceCount, groupEvidenceHtml, groupEvidenceItems, isStrong, surgeText } from './evidence.js';
import { colorFor, escapeHtml, fmtPct, fmtTurnover, toCsv } from './transform.js';

export const METHODS = { known: '找法1 · 已知分類', new: '找法2 · 排行候選' };
const KIND_COLOR = { 已知族群: '#2c4a6b', 已知族群的延伸: '#6b5a1e', 跨族群新組合: '#b8301a' };
const GROUPINGS = { industry: '官方產業', custom: '自訂族群', concept: '概念股', chain: '細產業', news: '新聞族群', group: '集團' };
// on unless ticked otherwise: 集團 is offered, not on by default (the precomputed results use the server's
// default groupings, and a new one in the list would be another cache key for every day)
export const DEFAULT_GROUPINGS = ['industry', 'custom', 'concept', 'chain', 'news'];
const SOURCES = { gain: '漲幅', turnover: '週轉率', share_z: '比重暴增', gap: '開盤跳空', vol_ratio: '量比' };
// the label the server puts in g.pool for each ranking (finder.candidate_pool)
const POOL_LABELS = { gain: '漲幅', turnover: '週轉率', share_z: '比重暴增', gap: '跳空', vol_ratio: '量比' };

/** Tunable fields: [key, label, kind, method ('' = both), help]. Defaults come from the server. */
export const FIELDS = [
  ['min_amplitude', '振幅 >（%）', 'num', 'known', '只看當天振幅（最高 − 最低）÷ 昨收 超過這個 % 的股票'],
  ['min_volume_lots', '成交量 ≥（張）', 'int', 'known', '只看當天成交量至少這麼多張的股票（1 張＝1,000 股）'],
  ['daily_r', '日相關 ≥', 'num', 'known', '20 日（或下面的天數）日漲跌幅的相關係數'],
  ['daily_window', '日相關天數', 'int', 'known', ''],
  ['new_daily_r', '日相關 ≥', 'num', 'new', '新組合的歷史短，用較短的窗口'],
  ['new_daily_window', '日相關天數', 'int', 'new', ''],
  ['pool_top', '各排行取前', 'int', 'new', '漲幅、週轉率、比重暴增、開盤跳空、量比各取前 N 名'],
  ['vol_ratio_window', '量比天數', 'int', 'new', '量比＝今天成交量 ÷ 前 N 個交易日的平均量'],
  ['new_min_volume_lots', '成交量 ≥（張）', 'int', 'new', '候選池只收當天成交量至少這麼多張的股票（1 張＝1,000 股）'],
  ['intraday_basis', '分時 R 用', 'sel', '', '篩選用哪一種分時相關：原始＝直接算；扣大盤＝先扣掉大盤／櫃買；扣大盤＋類股＝上市股扣自己的類股指數（同產業上市少於 15 檔的改扣大盤），上櫃扣櫃買。扣得越多，越能排除「只是跟著大盤或類股漲」'],
  ['intraday_r', '分時相關 ≥', 'num', '', '原始的分時相關門檻（分時 R 用「原始」時才篩這個）：最近幾個交易日 5 分 K 報酬；鎖漲停的平線不計'],
  ['excess_r', '扣指數後相關 ≥', 'num', '', '扣掉大盤／類股之後剩下的分時相關門檻（分時 R 用「扣大盤」或「扣大盤＋類股」時篩這個）；扣過的 R 通常比原始 R 低很多'],
  ['excess_r_mode', '門檻方式', 'sel', '', '固定＝用上面的數字；每日自適應＝每天從不同產業的隨機配對算出「只有 0.5% 會達到」的扣指數 R 當門檻（限制在 0.25～0.6 之間）。研究（exp1b）：每天會落在 0.29～0.48，組數只稍微穩一點；門檻每天不同，跨天的組數不能直接比'],
  ['intraday_sessions', '分時天數', 'int', '', ''],
  ['peak_gap', '高點差 ≤（分）', 'int', '', '日內最高點（09:00–13:00）的時間差。高點時間的精度跟著分 K：1 分 K（約 30 天內）精確到分鐘；只有 5 分 K 的舊日子只會落在 0、5、10…分，所以 6～9 跟 5 效果一樣'],
  ['start_gap', '起漲差 ≤（分）', 'opt', '', '空白＝只顯示、不篩選'],
  ['require_burst', '要同時爆量', 'bool', '', '同一個 5 分鐘兩檔都爆量（≥ 3 倍自己的中位數）'],
  ['dense', '群內至少', 'num', '', '每檔要跟群內這個比例的人都符合條件（0.7＝七成）'],
  ['min_size', '最少幾檔', 'int', '', ''],
];

/** The thresholds that decide a group, in a few words (盤後報告's header). */
export function condText(p) {
  const thr = p.excess_r_mode === 'adaptive' ? '每日自適應門檻' : p.excess_r;
  const intraday = p.intraday_basis && p.intraday_basis !== 'raw' ? `${BASES[p.intraday_basis]}後分時 R ≥ ${thr}` : `分時 R ≥ ${p.intraday_r}`;
  return `日 R ≥ ${p.daily_r}（找法2 ${p.new_daily_r}）、${intraday}、高點差 ≤ ${p.peak_gap} 分、群內 ${p.dense}`;
}

export const BASES = { sector: '扣大盤＋類股', market: '扣大盤', raw: '原始' };
export const EXCESS_MODES = { fixed: '固定', adaptive: '每日自適應' };
const SEL_OPTIONS = { intraday_basis: BASES, excess_r_mode: EXCESS_MODES }; // kind 'sel': its options, the first the default

export function queryOf(method, params) {
  const q = new URLSearchParams({ method });
  for (const [key, , kind, m] of FIELDS) {
    if (m && m !== method) continue;
    const v = params[key];
    if (kind === 'opt') q.set(key, v === null || v === undefined || v === '' ? '' : String(v));
    else if (v !== undefined && v !== null && v !== '') q.set(key, String(v));
  }
  if (method === 'known' && params.groupings) q.set('groupings', params.groupings.join(','));
  if (method === 'new' && params.pool_sources) q.set('pool_sources', params.pool_sources.join(','));
  return q.toString();
}

const fmtR = (r) => (r === null || r === undefined ? '—' : r.toFixed(2));
const NAME_MAX = 30; // groups.py MAX_NAME_LEN

/**
 * Default name when a found group is saved as a 自訂族群: "N1022 華經+驊宏資…" — the date and the
 * members, cut to the server's 30-character limit, without the characters a group name may not hold.
 */
export function defaultGroupName(g, date) {
  const md = String(date || '').slice(5).replace('-', '');
  const names = g.members.map((m) => m.name.replace(/[*＊]/g, '')).join('+');
  const raw = `N${md} ${names}`.replace(/[/\\<>"',]/g, '');
  return raw.length > NAME_MAX ? `${raw.slice(0, NAME_MAX - 1)}…` : raw;
}

/** 三大法人 in 張 and ≈ 億元 (g.inst, attached when served); older results only have inst_net in shares. */
export function instText(g) {
  const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '');
  const lots = (n) => (Math.abs(n) >= 1e4 ? `${(Math.abs(n) / 1e4).toFixed(1)} 萬張` : `${Math.abs(n).toLocaleString()} 張`);
  const i = g.inst;
  if (i) {
    const cls = i.lots > 0 ? 'up' : i.lots < 0 ? 'down' : '';
    const who = `<span class="muted">（買超 ${i.buyers}／賣超 ${i.sellers} 檔）</span>`;
    const amount = i.amount_100m == null ? '' : `（≈ ${sign(i.amount_100m)}${Math.abs(i.amount_100m).toFixed(1)} 億）`;
    return `法人 <b class="${cls}">${sign(i.lots)}${lots(i.lots)}</b>${amount}${who}`;
  }
  return g.inst_net ? `法人 ${sign(g.inst_net)}${lots(Math.round(g.inst_net / 1000))}` : '';
}

/** 法人占成交: the 法人 net amount against the group's turnover — is it 法人 money that moves the group? */
export function instShareText(g) {
  const amt = g.inst?.amount_100m;
  if (amt == null || !(g.turnover_100m > 0)) return '';
  const pct = (Math.abs(amt) / g.turnover_100m) * 100;
  return `<span title="法人淨${amt >= 0 ? '買' : '賣'}超金額 ÷ 組合成交值：越高越像法人在推，越低代表主要是法人以外的資金">法人占成交 <b>${pct < 0.1 ? '<0.1' : pct.toFixed(1)}%</b></span>`;
}

/** 外資／投信／自營 of the group in 張 (g.inst_split, attached when served). */
export function instSplitText(g) {
  const s = g.inst_split;
  if (!s) return '';
  const n = (v) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString()}`;
  const part = (label, v) => `${label} <b class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${n(v)}</b>`;
  return `<span class="muted" title="三大法人拆開看：投信常是主題／作帳佈局，外資偏權值與大盤，自營多短線避險">（${part('外資', s.foreign)}／${part('投信', s.trust)}／${part('自營', s.dealer)} 張）</span>`;
}

/** 💳 融資融券 line (g.margin, attached when served): 融資 = leveraged, mostly retail money. */
export function marginText(g) {
  const m = g.margin;
  if (!m) return '';
  const n = (v) => `<b class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString()}</b>`;
  const bal = (v) => (Math.abs(v) >= 1e4 ? `${(v / 1e4).toFixed(1)} 萬` : v.toLocaleString());
  const s = g.margin_streak;
  const parts = [
    `融資 ${n(m.margin_chg)} 張 <span class="muted">（餘額 ${bal(m.margin_bal)}）</span>`,
    `融券 ${n(m.short_chg)} 張 <span class="muted">（餘額 ${bal(m.short_bal)}）</span>`,
    m.short_ratio != null ? `券資比 ${m.short_ratio.toFixed(1)}%` : '',
    s ? `<b class="${s.side === 'buy' ? 'up' : 'down'}">融資連${s.side === 'buy' ? '增' : '減'} ${s.days} 天</b>` : '',
  ].filter(Boolean);
  return `<div class="fd-margin" title="融資增加＝用借來的錢買（多半是散戶槓桿追價）；融券增加＝放空變多；券資比高代表軋空的可能">💳 ${parts.join('　')}</div>`;
}

/** The headlines behind one 📰 題材 (shown under the theme line when it is clicked). */
export function themeNewsHtml(theme) {
  if (!theme) return '';
  const items = (theme.titles || [])
    .map((h) => {
      const named = h.names?.length ? ` <span class="fd-named">${h.names.map((n) => escapeHtml(n)).join('、')}</span>` : '';
      const meta = [h.date ? h.date.slice(5) : '', h.source || ''].filter(Boolean).map((x) => escapeHtml(x)).join(' · ');
      const title = escapeHtml(h.title || '');
      const link = /^https?:\/\//.test(h.url || '') ? `<a href="${escapeHtml(h.url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : title;
      return `<li>${link}${named}${meta ? ` <small class="muted">${meta}</small>` : ''}</li>`;
    })
    .join('');
  return `<div class="fd-theme-h"><b>📰 ${escapeHtml(theme.term)}</b> <small class="muted">近 3 個新聞日的標題（先列有點名這組股票的）</small></div>${items ? `<ol>${items}</ol>` : '<p class="hint">沒有存下這個題材的標題。</p>'}`;
}

/** The card grid's height for `rows` rows, from the cards' offsetTop values (any order): up to just
 *  above the first card of row rows + 1, minus the grid gap; null when there are no more rows. */
export function rowsHeight(tops, rows = 2, gap = 10) {
  const starts = [...new Set(tops.map((t) => Math.round(t)))].sort((a, b) => a - b);
  return starts.length > rows ? starts[rows] - starts[0] - gap : null;
}

/** Sort key for 依證據: supporting checks, then their share of those with data. */
export function evidenceRank(g) {
  const { yes, known } = evidenceCount(groupEvidenceItems(g));
  return yes + (known ? yes / known : 0) / 10;
}

export function cardHtml(g, i) {
  const title =
    g.sources?.length
      ? g.sources.map((s) => `${escapeHtml(s.label)}：${escapeHtml(s.group)}`).join('／')
      : g.matches?.length
        ? `最接近 ${escapeHtml(g.matches[0].label)}：${escapeHtml(g.matches[0].group)}（${Math.round(g.matches[0].overlap * 100)}%）`
        : '對不到已知分類';
  // the title is cut with … on narrow cards: the tooltip has it in full (and every close grouping)
  const tip = g.sources?.length
    ? title
    : g.matches?.length
      ? g.matches.map((x) => `最接近 ${escapeHtml(x.label)}：${escapeHtml(x.group)}（重疊 ${Math.round(x.overlap * 100)}%）`).join('\n')
      : title;
  const outside = g.matches?.[0] && g.matches[0].overlap < 1 ? new Set(g.matches[0].outside) : new Set();
  const chips = g.members
    .map((m) => {
      const extra = [m.peak ? `高點 ${m.peak}` : '', m.locked_at ? `🔒${m.locked_at}` : '', m.start ? `起漲 ${m.start}` : ''].filter(Boolean).join('　');
      const pool = g.pool?.[m.code]?.length ? `（${g.pool[m.code].join('、')}）` : '';
      const themes = m.themes?.length ? `　📰 ${m.themes.join('、')}` : '';
      return `<span class="fd-chip${outside.has(m.code) ? ' out' : ''}" style="background:${colorFor(m.change_pct, 7)}" title="${escapeHtml(m.code)} ${escapeHtml(m.name)}${pool}　${extra}${m.turnover_rate != null ? `　週轉率 ${m.turnover_rate.toFixed(1)}%` : ''}${m.inst_net != null ? `　法人 ${m.inst_net > 0 ? '+' : m.inst_net < 0 ? '−' : ''}${Math.abs(Math.round(m.inst_net / 1000)).toLocaleString()} 張` : ''}${escapeHtml(themes)}${outside.has(m.code) ? '　（不在最接近的分類裡）' : ''}">${escapeHtml(m.name)} <small>${fmtPct(m.change_pct)}</small>${m.locked_at ? ' 🔒' : ''}</span>`;
    })
    .join('');
  const money = g.turnover_100m != null
    ? `<div class="fd-money">💰 成交值 <b>${fmtTurnover(g.turnover_100m)}</b>${g.turnover_share != null ? `（占全市場 <b>${g.turnover_share.toFixed(2)}%</b>）` : ''}　平均漲幅 <b class="${g.avg_pct > 0 ? 'up' : g.avg_pct < 0 ? 'down' : ''}">${fmtPct(g.avg_pct)}</b></div>`
    : '';
  const facts = [
    `日 R <b>${fmtR(g.daily_r)}</b>`,
    `分時 R <b>${fmtR(g.intraday_r)}</b>${g.excess_r != null ? `<span title="扣掉各自的指數後：${escapeHtml(factorText(g))}">（扣指數 <b>${fmtR(g.excess_r)}</b>）</span>` : ''}`,
    `高點差 <b>${g.peak_spread ?? '—'}</b> 分`,
    g.start_spread != null ? `起漲差 ${g.start_spread} 分` : '',
    g.together_bursts?.length ? `同時爆量 ${g.together_bursts.slice(0, 3).join('、')}` : '',
    g.resolution === 5 ? '<span class="muted" title="這天只有 5 分 K：高點時間只到 5 分鐘精度，高點差 ≤ 8 分實際等於 ≤ 5 分">5 分 K</span>' : '',
  ]
    .filter(Boolean)
    .join('　');
  // 🏦 everything about 法人 on one line of its own (who is buying), under 💰 (how much money)
  const instParts = [
    instText(g),
    instShareText(g),
    instSplitText(g),
    g.inst_streak ? `<b class="${g.inst_streak.side === 'buy' ? 'up' : 'down'}" title="組合合計三大法人買賣超，連續同方向的天數">法人連${g.inst_streak.side === 'buy' ? '買' : '賣'} ${g.inst_streak.days} 天</b>` : '',
    g.trust_streak ? `<b class="${g.trust_streak.side === 'buy' ? 'up' : 'down'}" title="只看投信：組合合計投信買賣超連續同方向的天數（投信連買常是主題／作帳佈局）">投信連${g.trust_streak.side === 'buy' ? '買' : '賣'} ${g.trust_streak.days} 天</b>` : '',
  ].filter(Boolean);
  const inst = instParts.length ? `<div class="fd-inst">🏦 ${instParts.join('　')}</div>` : '';
  const margin = marginText(g);
  const dt = g.daytrade?.hot
    ? `<div class="fd-dt" title="當沖成交股數 ÷ 成交股數（上市、上櫃都有）；有當沖資料的成員平均 ≥ 50% 才標 🔥（沒資料的成員不算進平均）。當沖比高多半是隔日沖等短線熱錢，跟有計畫的佈局不同（不算在 🧾 證據裡）">🔥 當沖比高${g.daytrade.avg != null ? `（平均 ${g.daytrade.avg.toFixed(1)}%）` : ''}${surgeText(g.daytrade)}：${Object.entries(g.daytrade.by_code)
        .map(([c, v]) => {
          const base = g.daytrade.base?.[c];
          // 平常: the 20 days before; ×2 or more is a sudden rush, not a stock that is always day-traded
          const usual = base != null ? `<small class="${base > 0 && v / base >= 2 ? 'up' : 'muted'}">（平常 ${base.toFixed(0)}%）</small>` : '';
          return `${escapeHtml(g.members.find((m) => m.code === c)?.name || c)} ${v.toFixed(1)}%${usual}`;
        })
        .join('、')}</div>`
    : '';
  const nameOf = (c) => g.members.find((m) => m.code === c)?.name || c;
  const br = g.branches?.common?.length
    ? `<div class="fd-br" title="券商分點（HiStock，收盤後快照）：在 2 檔以上成員的買超前 15 名都出現的分點；括號＝名次">🏦 ${g.branches.common
        .slice(0, 3)
        .map((e) => `<b>${escapeHtml(e.name)}</b> ${Object.keys(e.ranks).map((c) => `${escapeHtml(nameOf(c))}（第 ${e.ranks[c]}）`).join('、')}`)
        .join('；')}</div>`
    : '';
  // 籌碼集中度 of the members with 分點 stored ((top-15 buyers + top-15 sellers) ÷ volume)
  const conc = Object.entries(g.concentration || {});
  const concLine = conc.length
    ? `<div class="fd-br" title="籌碼集中度＝（前 15 大買超分點買超 − 前 15 大賣超分點賣超）÷ 成交量；正數＝籌碼往大買家集中">🏦 籌碼集中度 ${conc.map(([c, v]) => `${escapeHtml(nameOf(c))} <span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : ''}${v.toFixed(1)}%</span>`).join('、')}</div>`
    : '';
  const rel = g.relations?.length || g.rel_group ? `<div class="fd-rel" title="轉投資／法人董監（MoneyDJ 整理自公開資訊觀測站）：一起動有結構性的原因">🏢 ${relationsHtml(g.relations, g.rel_group)}</div>` : '';
  const news = g.news?.length ? `<div class="fd-news">📰 ${g.news.map((t) => escapeHtml(t)).join('<br>📰 ')}</div>` : '';
  const themes = g.themes?.length
    ? `<div class="fd-themes" title="近 3 個新聞日提到這幾檔的題材關鍵字（括號＝提到幾檔）；點題材看新聞標題">📰 題材：${g.themes
        .map((t, k) => `<span class="fd-theme" data-theme="${k}" role="button" tabindex="0">${escapeHtml(t.term)}${g.members.length > 1 ? `（${t.count}）` : ''}</span>`)
        .join('、')}</div><div class="fd-theme-news" hidden></div>`
    : '';
  const seen = g.seen_days?.length
    ? `<span class="fd-seen" title="前幾天也找到同一組合（成員重疊過半）：${g.seen_days.map((x) => x.slice(5)).join('、')}">🔁 近 ${g.checked_days + 1} 天第 ${g.seen_days.length + 1} 次</span>`
    : g.checked_days
      ? '<span class="fd-seen new" title="前幾天的結果裡沒有這個組合">🆕 首次</span>'
      : '';
  return `<div class="fd-card" data-i="${i}">
    <div class="fd-top"><span class="link-badge" style="background:${KIND_COLOR[g.kind] || '#3a3e48'}">${escapeHtml(g.kind)}</span>
      <span class="fd-title" title="${tip}">${title}</span>${seen}<span class="muted">${g.members.length} 檔</span>
      <button type="button" class="icon fd-save" data-save="${i}" title="把這群股票存成自訂族群，之後在 B／C／K 追蹤">＋族群</button></div>
    <div class="fd-chips">${chips}</div>${groupEvidenceHtml(g)}${dt}${money}${inst}${margin}${br}${concLine}${rel}
    <div class="fd-facts">${facts}</div>${themes}${news}
  </div>`;
}

/** Which index each member was measured against: "半導體類：甲、乙；櫃買：丙". */
export function factorText(g) {
  const by = new Map();
  for (const m of g.members) {
    const f = g.factors?.[m.code];
    if (f) by.set(f, [...(by.get(f) || []), m.name]);
  }
  return [...by].map(([f, names]) => `${f}：${names.join('、')}`).join('；') || '—';
}

const KINDS = ['已知族群', '已知族群的延伸', '跨族群新組合'];

export const BIG_MIN = 3; // 「只看 3 檔以上」: more than a pair
/** The 只看 3 檔以上 toggle: on → only groups of BIG_MIN members or more. */
export const isBig = (g, on) => !on || g.members.length >= BIG_MIN;

// the chips' tooltips: what each one keeps
const GROUPING_TIPS = {
  官方產業: '證交所／櫃買中心公布的產業別',
  自訂族群: '你自己建立的族群',
  概念股: '概念股清單（例如 AI 伺服器、CoWoS）',
  細產業: '櫃買中心產業鏈的細分類',
  新聞族群: '最近幾天新聞裡一起被提到的股票',
};
const KIND_TIPS = {
  已知族群: '成員全都在同一個已知分類裡（產業、概念股、自訂族群…）',
  已知族群的延伸: '大部分成員在同一個已知分類裡，另外多了分類外的股票',
  跨族群新組合: '成員不屬於同一個已知分類：資金把不相干的股票一起拉',
};
const EVIDENCE_TIP =
  '資金一致性證據夠多：卡片上的 🧾 清單（扣大盤、比隨機配對像、同分鐘爆量、同分鐘同步、不只一天、籌碼、持股關係、同漲跌、同分點、題材）' +
  '裡，至少 5 項 ✓，而且在有資料的項目中 ✓ 占 70% 以上。— 表示沒有資料，不算贊成也不算反對。證據越多，越不像巧合、越像有人有計畫地一起買';

/**
 * Filter chips over the found groups (no recompute): 找法1 by the grouping a group came from (a group
 * found in several groupings counts under each), 找法2 by kind. → [{key, label, n}], 全部 first.
 */
export function filterOptions(d) {
  const groups = d?.groups || [];
  const opts = [{ key: 'all', label: '全部', n: groups.length, tip: '這一天找到的所有組合' }];
  if (d?.method === 'new') {
    for (const k of KINDS) opts.push({ key: k, label: k === '已知族群的延伸' ? '延伸' : k, n: groups.filter((g) => g.kind === k).length, tip: KIND_TIPS[k] });
    // by the ranking the members came in from (a group counts when any member did)
    const used = d.params?.pool_sources?.length ? d.params.pool_sources : Object.keys(SOURCES);
    for (const k of Object.keys(SOURCES).filter((x) => used.includes(x))) {
      opts.push({ key: `@src:${k}`, label: `📈 ${SOURCES[k]}`, n: groups.filter((g) => matchesFilter(g, `@src:${k}`, 'new')).length, tip: `至少一檔成員是從「${SOURCES[k]}」排行前幾名進入候選池的` });
    }
  } else {
    for (const label of d?.groupings || []) {
      opts.push({ key: label, label, n: groups.filter((g) => g.sources?.some((s) => s.label === label)).length,
        tip: `從「${label}」裡找到的組合${GROUPING_TIPS[label] ? `（${GROUPING_TIPS[label]}）` : ''}；同一組可能同時出現在好幾種分類` });
    }
  }
  // by who is buying (both methods), once the day's 法人 is out
  if (groups.some((g) => g.inst)) {
    for (const [key, label, tip] of INST_FILTERS) opts.push({ key, label, n: groups.filter((g) => matchesFilter(g, key, d.method)).length, tip });
  }
  const related = groups.filter((g) => matchesFilter(g, '@rel', d.method)).length;
  if (related) opts.push({ key: '@rel', label: '🏢 有持股關係', n: related, tip: '成員之間有轉投資（權益法持股 ≥ 20%）、董監席位，或屬於同一個集團（MoneyDJ）' });
  const hot = groups.filter((g) => g.daytrade?.hot).length;
  if (hot) opts.push({ key: '@dt', label: '🔥 隔日沖', n: hot, tip: '有當沖資料的成員平均當沖比 ≥ 50%：隔日沖短線熱錢，隔天常開高走低（風險警示，不算資金證據）；盤後報告有這類組合過去幾天的隔天表現' });
  if (groups.some((g) => g.evidence)) opts.push({ key: '@ev', label: '🧾 證據強', n: groups.filter((g) => matchesFilter(g, '@ev', d.method)).length, tip: EVIDENCE_TIP });
  return opts;
}

const INST_FILTERS = [ // '@': never a grouping label
  ['@inst+', '🏦 法人買超', '這一群合計的三大法人（外資＋投信＋自營商）當天是買超'],
  ['@inst-', '🏦 法人賣超', '這一群合計的三大法人（外資＋投信＋自營商）當天是賣超'],
];

/** The chip actually in effect: the saved one if that day has cards for it, else 全部 (cards and CSV agree). */
export function shownFilter(d, saved) {
  return saved && filterOptions(d).some((o) => o.key === saved && o.n) ? saved : 'all';
}

export function matchesFilter(g, key, method) {
  if (!key || key === 'all') return true;
  if (key.startsWith('@src:')) return Object.values(g.pool || {}).some((from) => from.includes(POOL_LABELS[key.slice(5)]));
  if (key === '@rel') return !!(g.relations?.length || g.rel_group);
  if (key === '@ev') return isStrong(evidenceCount(groupEvidenceItems(g)));
  if (key === '@dt') return !!g.daytrade?.hot;
  if (key === '@inst+') return g.inst?.lots > 0;
  if (key === '@inst-') return g.inst?.lots < 0;
  return method === 'new' ? g.kind === key : !!g.sources?.some((s) => s.label === key);
}

export function filterHtml(d, current) {
  return `<div class="fd-filter seg">${filterOptions(d)
    .map((o) => `<button type="button" data-filter="${escapeHtml(o.key)}"${o.key === current ? ' class="on"' : ''}${o.n ? '' : ' disabled'}${o.tip ? ` title="${escapeHtml(`${o.tip}（${o.n} 組）`)}"` : ''}>${escapeHtml(o.label)} <small>${o.n}</small></button>`)
    .join('')}</div>`;
}

/**
 * CSV of the cards on screen (the chip filter applies): one row per member, with its group's figures
 * and the thresholds used, so runs with different settings can be compared in Excel.
 * → [file name, header, rows] or null when nothing is shown.
 */
export function csvExport(d, filter = 'all', big = false) {
  if (!d?.groups?.length) return null;
  const groups = d.groups.map((g, i) => [g, i + 1]).filter(([g]) => matchesFilter(g, filter, d.method) && isBig(g, big));
  if (!groups.length) return null;
  const p = d.params || {};
  const cond =
    d.method === 'new'
      ? `排行前${p.pool_top} 成交量≥${p.new_min_volume_lots}張 日R≥${p.new_daily_r}(${p.new_daily_window}日)`
      : `振幅>${p.min_amplitude}% 成交量≥${p.min_volume_lots}張 日R≥${p.daily_r}(${p.daily_window}日)`;
  const header = [
    '日期', '找法', '群組#', '類型', '來源／最接近分類', '代號', '名稱', '漲幅%', '週轉率%', '振幅%', '高點時間', '起漲時間', '鎖漲停',
    '群組日R', '群組分時R', '群組扣指數分時R', '扣的指數', '高點差(分)', '起漲差(分)', '群組成交值(億)', '占全市場%', '群組平均漲幅%',
    '個股法人(張)', '群組法人(張)', '群組法人(億)', '法人占成交%', '外資(張)', '投信(張)', '自營(張)', '法人連續', '投信連續',
    '個股融資增減(張)', '群組融資增減(張)', '群組融券增減(張)', '券資比%', '融資連續', '條件',
  ];
  const streak = (s) => (s ? `連${s.side === 'buy' ? '買' : '賣'}${s.days}` : '');
  const rows = groups.flatMap(([g, n]) => {
    const src = g.sources?.length ? g.sources.map((s) => `${s.label}:${s.group}`).join('／') : g.matches?.[0] ? `${g.matches[0].label}:${g.matches[0].group}(${Math.round(g.matches[0].overlap * 100)}%)` : '';
    const cut = (v) => (v == null ? '' : Math.round(v * 100) / 100);
    return g.members.map((m) => [
      d.date, METHODS[d.method] || d.method, n, g.kind, src, m.code, m.name, cut(m.change_pct), cut(m.turnover_rate), cut(m.amplitude_pct), m.peak || '', m.start || '', m.locked_at || '',
      g.daily_r ?? '', g.intraday_r ?? '', g.excess_r ?? '', g.factors?.[m.code] || '', g.peak_spread ?? '', g.start_spread ?? '', g.turnover_100m ?? '', g.turnover_share ?? '', g.avg_pct ?? '',
      m.inst_net == null ? '' : Math.round(m.inst_net / 1000), g.inst?.lots ?? '', g.inst?.amount_100m ?? '',
      g.inst?.amount_100m != null && g.turnover_100m > 0 ? cut((Math.abs(g.inst.amount_100m) / g.turnover_100m) * 100) : '',
      g.inst_split?.foreign ?? '', g.inst_split?.trust ?? '', g.inst_split?.dealer ?? '', streak(g.inst_streak), streak(g.trust_streak),
      m.margin_chg ?? '', g.margin?.margin_chg ?? '', g.margin?.short_chg ?? '', g.margin?.short_ratio ?? '',
      g.margin_streak ? `連${g.margin_streak.side === 'buy' ? '增' : '減'}${g.margin_streak.days}` : '', `${cond} ${p.intraday_basis && p.intraday_basis !== 'raw' ? `${BASES[p.intraday_basis]}後分時R≥${p.excess_r}` : `分時R≥${p.intraday_r}`} 高點差≤${p.peak_gap}分`,
    ]);
  });
  const label = Object.fromEntries(INST_FILTERS)[filter]?.replace('🏦 ', '') || (filter?.startsWith('@src:') ? SOURCES[filter.slice(5)] : filter); // no internal '@…' keys in file names
  const tag = filter && filter !== 'all' ? `_${label}` : '';
  return [`資金連動搜尋_${d.method === 'new' ? '找法2' : '找法1'}_${d.date}${tag}.csv`, header, rows];
}

export function statsText(d) {
  const s = d.stats || {};
  const sec = s.seconds?.total ?? Object.values(s.seconds || {}).reduce((a, x) => a + x, 0);
  const parts = [
    d.method === 'new' ? `候選池 ${s.pool} 檔` : '',
    `日相關通過 ${s.candidates} 組`,
    `抓分 K ${s.fetched} 檔`,
    `找到 ${s.groups} 群`,
    d.cached ? '（已存結果）' : `（${sec.toFixed(1)} 秒）`,
  ];
  if (d.missing_groupings?.length) parts.push(`尚未載入：${d.missing_groupings.map((g) => GROUPINGS[g] || g).join('、')}`);
  return parts.filter(Boolean).join('　·　');
}

/**
 * The funnel under the stats line: pairs reaching the intraday stage, what removed them (most first)
 * and how many passed — which threshold to loosen when nothing is found.
 */
export function funnelText(d) {
  const f = d?.stats?.funnel;
  if (!f?.pairs) return '';
  const why = Object.entries(f.rejected || {}).map(([k, n]) => `${k} ${n}`);
  if (f.no_bars) why.push(`沒有分 K ${f.no_bars}`);
  return `漏斗：進入分時比對 ${f.pairs} 對 → 刷掉：${why.join('、') || '無'} → 三關都過 ${f.passed} 對 → ${d.stats.groups} 群`;
}

/** Progress of a running search → {pct, text}: 日相關 0–15%, 抓分 K 15–90% (by stock), 比對 90–100%. */
export function progressView(p) {
  if (!p?.running) return { pct: 3, text: '準備中…' };
  const sec = p.seconds != null ? `　${p.seconds.toFixed(0)} 秒` : '';
  if (p.stage === 'daily') return { pct: 8, text: `① 計算日相關${sec}` };
  if (p.stage === 'fetch') {
    const frac = p.total ? p.done / p.total : 1;
    return { pct: 15 + Math.round(frac * 75), text: `② 抓分 K ${p.done}／${p.total} 檔${sec}` };
  }
  return { pct: 95, text: `③ 分時相關與高點比對${sec}` };
}

/**
 * One-click threshold sets. They only touch the linkage thresholds (daily R, intraday R, high gap);
 * 振幅, 成交量, groupings and pool settings stay as the user set them. 寬鬆 mostly loosens intraday R,
 * the test that removes the most pairs (10/22 funnel: 143 of 197).
 */
export const PRESETS = {
  strict: { label: '嚴格', values: { daily_r: 0.85, new_daily_r: 0.85, intraday_r: 0.6, excess_r: 0.5, peak_gap: 5 } },
  standard: { label: '標準', values: { daily_r: 0.8, new_daily_r: 0.8, intraday_r: 0.5, excess_r: 0.4, peak_gap: 8 } },
  loose: { label: '寬鬆', values: { daily_r: 0.7, new_daily_r: 0.75, intraday_r: 0.35, excess_r: 0.3, peak_gap: 15 } },
};

/** The preset the current values match exactly, or null (自訂). */
export function presetOf(params) {
  for (const [k, p] of Object.entries(PRESETS)) {
    if (Object.entries(p.values).every(([f, v]) => Number(params[f]) === v)) return k;
  }
  return null;
}

// ---- B's 🔗: N's stored 找法1 cards indexed by the group they came from -----------------

/** {`${grouping label}|${group}`: [found group]} from N's stored cards (a card can name several sources). */
export function finderMarkIndex(result) {
  const out = new Map();
  for (const g of result?.groups || []) {
    for (const s of g.sources || []) {
      const k = `${s.label}|${s.group}`;
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(g);
    }
  }
  return out;
}

export function finderMarkTitle(found) {
  const r = (v) => (v == null ? '—' : v.toFixed(2));
  return `N 資金連動搜尋（找法1）在這個族群找到共同注資：\n${found
    .map((g) => `${g.members.map((m) => m.name).join('、')}（日R ${r(g.daily_r)}、分時R ${r(g.intraday_r)}、高點差 ${g.peak_spread ?? '—'} 分）`)
    .join('\n')}`;
}

/**
 * The toolbar digest from N's stored results of the day (stored_only reads): '' when neither method
 * has a stored result, else e.g. '💡 資金連動：找法1 6 群（🔁 3）／找法2 1 群'.
 */
export function digestText(known, fresh) {
  const part = (label, d) => {
    if (!d || d.stored === false || !Array.isArray(d.groups)) return null;
    const again = d.groups.filter((g) => g.seen_days?.length).length;
    return `${label} ${d.groups.length} 群${again ? `（🔁 ${again}）` : ''}`;
  };
  const parts = [part('找法1', known), part('找法2', fresh)].filter(Boolean);
  return parts.length ? `💡 資金連動：${parts.join('／')}` : '';
}

// ---- panel --------------------------------------------------------------------------------------

const PREF_KEY = 'openmarket.history.finder';
const DEFAULTS = {
  min_amplitude: 3,
  min_volume_lots: 300,
  new_min_volume_lots: 300,
  daily_r: 0.8, daily_window: 20, new_daily_r: 0.8, new_daily_window: 10, pool_top: 50,
  intraday_basis: 'sector', intraday_r: 0.5, excess_r: 0.4, excess_r_mode: 'fixed', intraday_sessions: 3, peak_gap: 8, start_gap: '', require_burst: false, dense: 0.7, min_size: 2,
  vol_ratio_window: 5,
  groupings: DEFAULT_GROUPINGS, pool_sources: Object.keys(SOURCES),
};

export function mountFinderPanel(container, { onStock, onResult }) {
  const sec = document.createElement('section');
  sec.id = 'hFinderPanel';
  sec.className = 'panel hN';
  const pref = { method: 'known', params: { ...DEFAULTS }, filter: { known: 'all', new: 'all' }, expanded: false, byEvidence: false, big: false };
  try {
    const saved = JSON.parse(localStorage.getItem(PREF_KEY) || '{}');
    if (saved.method in METHODS) pref.method = saved.method;
    Object.assign(pref.params, saved.params || {});
    // saved before 開盤跳空／量比 existed: turn the two new rankings on once (later choices are kept)
    // saved with the first 扣指數 default (0.3): the one threshold is 0.4 now, as the 🧾 扣指數 check
    if ((saved.v || 1) < 3 && saved.params?.excess_r === 0.3) pref.params.excess_r = 0.4;
    if ((saved.v || 1) < 2 && Array.isArray(saved.params?.pool_sources)) {
      pref.params.pool_sources = [...new Set([...saved.params.pool_sources, 'gap', 'vol_ratio'])];
    }
    Object.assign(pref.filter, saved.filter || {});
    pref.expanded = !!saved.expanded;
    pref.byEvidence = !!saved.byEvidence;
    pref.big = !!saved.big;
  } catch {
    /* defaults */
  }
  const save = () => {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify({ ...pref, v: 3 }));
    } catch {
      /* ignore */
    }
  };
  sec.innerHTML = `<div class="panel-h"><b>N · 資金連動搜尋</b>
      <div class="seg" id="hFinderMethod">${Object.entries(METHODS).map(([k, l]) => `<button data-value="${k}">${l}</button>`).join('')}</div>
      <button type="button" class="icon wide" id="hFinderToggle">條件 ▾</button>
      <button type="button" class="icon wide" id="hFinderRun" title="用目前的條件重新計算這一天">重新計算</button>
      <button type="button" class="icon wide" id="hFinderCsv" title="匯出目前顯示的卡片（依上面的篩選；Excel 可開）">CSV</button>
      <span class="muted" id="hFinderStats" style="margin-left:auto"></span></div>
    <div class="fd-funnel muted" id="hFinderFunnel" title="每一關刷掉幾對：卡最多的那一關，就是要放寬的條件"></div>
    <div class="fd-form" id="hFinderForm" hidden></div>
    <div class="fd-progress" id="hFinderProgress" hidden><div class="fd-bar"><span></span></div><div class="fd-ptext"></div></div>
    <div id="hFinderBody" style="padding:8px 10px"><p class="hint">捲到這裡就會載入這一天的結果。</p></div>
    <div id="hFinderDetail" class="fd-detail" hidden></div>`;
  container.appendChild(sec);
  const body = sec.querySelector('#hFinderBody');
  const form = sec.querySelector('#hFinderForm');
  const detail = sec.querySelector('#hFinderDetail');
  const state = { ticket: 0, date: null, data: null, loadedKey: null, timer: null, visible: false, stopRace: () => {}, detailTicket: 0, poll: null };
  const bar = sec.querySelector('#hFinderProgress');
  function showProgress(p) {
    const v = progressView(p);
    bar.querySelector('.fd-bar span').style.width = `${v.pct}%`;
    bar.querySelector('.fd-ptext').textContent = v.text;
  }
  function startProgress(query) {
    clearInterval(state.poll);
    showProgress(null);
    // a stored result comes back at once: only show the bar when the search takes a moment
    const shown = setTimeout(() => (bar.hidden = false), 400);
    state.poll = setInterval(async () => {
      try {
        const r = await fetch(`/api/history/finder/progress?date=${encodeURIComponent(state.date)}&${query}`, { cache: 'no-store' });
        if (r.ok) showProgress(await r.json());
      } catch {
        /* keep the last state */
      }
    }, 500);
    return () => {
      clearTimeout(shown);
      clearInterval(state.poll);
      bar.hidden = true;
    };
  }

  function drawMethod() {
    for (const b of sec.querySelectorAll('#hFinderMethod button')) b.classList.toggle('on', b.dataset.value === pref.method);
    const p = pref.params;
    const fields = FIELDS.filter(([, , , m]) => !m || m === pref.method)
      .map(([key, label, kind, , help]) => {
        const v = p[key];
        const input =
          kind === 'sel'
            ? `<select class="field" data-key="${key}">${Object.entries(SEL_OPTIONS[key]).map(([k, l], i) => `<option value="${k}"${k === (v || Object.keys(SEL_OPTIONS[key])[0]) ? ' selected' : ''}>${l}</option>`).join('')}</select>`
            : kind === 'bool'
            ? `<input type="checkbox" data-key="${key}"${v ? ' checked' : ''}>`
            : `<input class="field" data-key="${key}" data-kind="${kind}" value="${escapeHtml(v ?? '')}" size="4">`;
        return `<label title="${escapeHtml(help)}">${escapeHtml(label)} ${input}</label>`;
      })
      .join('');
    const lists =
      pref.method === 'known'
        ? `<span class="fd-list">分類：${Object.entries(GROUPINGS).map(([k, l]) => `<label><input type="checkbox" data-list="groupings" value="${k}"${p.groupings.includes(k) ? ' checked' : ''}>${l}</label>`).join('')}</span>`
        : `<span class="fd-list">候選池：${Object.entries(SOURCES).map(([k, l]) => `<label><input type="checkbox" data-list="pool_sources" value="${k}"${p.pool_sources.includes(k) ? ' checked' : ''}>${l}</label>`).join('')}</span>`;
    const now = presetOf(p);
    const presets = `<span class="fd-presets seg" title="只改日相關、分時相關、高點差三個門檻">${Object.entries(PRESETS)
      .map(([k, x]) => `<button type="button" data-preset="${k}"${k === now ? ' class="on"' : ''}>${x.label}</button>`)
      .join('')}${now ? '' : '<button type="button" class="on" disabled>自訂</button>'}</span>`;
    form.innerHTML = `${presets}${fields}${lists}<button type="button" class="icon wide" data-act="defaults">恢復預設</button>`;
  }
  form.addEventListener('change', (e) => {
    const el = e.target;
    const p = pref.params;
    if (el.dataset.list) {
      const set = new Set(p[el.dataset.list]);
      if (el.checked) set.add(el.value);
      else set.delete(el.value);
      p[el.dataset.list] = [...set];
    } else if (el.dataset.key) {
      const k = el.dataset.key;
      if (el.type === 'checkbox') p[k] = el.checked;
      else if (el.tagName === 'SELECT') p[k] = el.value;
      else if (el.dataset.kind === 'opt') p[k] = el.value.trim() === '' ? '' : Number(el.value);
      else if (el.value.trim() !== '' && Number.isFinite(Number(el.value))) p[k] = Number(el.value);
    }
    save();
    drawMethod(); // the preset buttons follow the edited values (a hand edit becomes 自訂)
    schedule(true);
  });
  form.addEventListener('click', (e) => {
    const preset = e.target.closest('button[data-preset]')?.dataset.preset;
    if (preset) {
      Object.assign(pref.params, PRESETS[preset].values);
    } else if (e.target.dataset.act === 'defaults') {
      pref.params = { ...DEFAULTS };
    } else {
      return;
    }
    save();
    drawMethod();
    schedule(true);
  });
  sec.querySelector('#hFinderToggle').addEventListener('click', (e) => {
    form.hidden = !form.hidden;
    e.target.textContent = form.hidden ? '條件 ▾' : '條件 ▴';
  });
  sec.querySelector('#hFinderMethod').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-value]');
    if (!b || b.dataset.value === pref.method) return;
    pref.method = b.dataset.value;
    save();
    drawMethod();
    schedule(true);
  });
  sec.querySelector('#hFinderRun').addEventListener('click', () => load(true));
  sec.querySelector('#hFinderCsv').addEventListener('click', () => {
    const out = csvExport(state.data, state.data ? shownFilter(state.data, pref.filter[state.data.method]) : 'all', pref.big);
    if (!out) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([toCsv(out[1], out[2])], { type: 'text/csv;charset=utf-8' }));
    a.download = out[0];
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  const key = () => `${state.date}|${queryOf(pref.method, pref.params)}`;
  async function load(refresh = false) {
    if (!state.date || (!refresh && key() === state.loadedKey)) return;
    state.loadedKey = key();
    const t = ++state.ticket;
    sec.classList.add('loading');
    const query = queryOf(pref.method, pref.params);
    const stopProgress = startProgress(query);
    try {
      const resp = await fetch(`/api/history/finder?date=${encodeURIComponent(state.date)}&${query}${refresh ? '&refresh=1' : ''}`, { cache: 'no-store' });
      const data = await resp.json();
      if (t !== state.ticket) return;
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      state.data = data;
      if (!data.cached) onResult?.(); // a newly computed result (stored now) — let other panels re-read
      sec.querySelector('#hFinderStats').textContent = statsText(data);
      sec.querySelector('#hFinderFunnel').textContent = funnelText(data);
      drawCards();
    } catch (err) {
      if (t !== state.ticket) return;
      body.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
    } finally {
      if (t === state.ticket) {
        sec.classList.remove('loading');
        stopProgress();
      }
    }
  }

  // cards of the stored result, filtered by the chips (switching a chip never recomputes)
  function drawCards() {
    const data = state.data;
    if (!data) return;
    if (!data.groups.length) {
      body.innerHTML = '<p class="hint">這一天沒有符合全部條件的群組。可以在「條件」放寬門檻。</p>';
      closeDetail();
      return;
    }
    const f = shownFilter(data, pref.filter[data.method]);
    // the card keeps its index in data.groups (clicks look it up); 依證據 only changes the order shown
    const order = data.groups.map((g, i) => [g, i]);
    if (pref.byEvidence) order.sort((x, y) => evidenceRank(y[0]) - evidenceRank(x[0]) || x[1] - y[1]);
    const cards = order.map(([g, i]) => (matchesFilter(g, f, data.method) && isBig(g, pref.big) ? cardHtml(g, i) : '')).join('');
    const sortBtn = data.groups.some((g) => g.evidence)
      ? `<button type="button" class="icon wide fd-sort${pref.byEvidence ? ' on' : ''}" title="依資金一致性證據（支持項數）由多到少排">${pref.byEvidence ? '✓ ' : ''}依證據排序</button>`
      : '';
    const nBig = data.groups.filter((g) => matchesFilter(g, f, data.method) && isBig(g, true)).length;
    const bigBtn = `<button type="button" class="icon wide fd-big${pref.big ? ' on' : ''}" title="只顯示 ${BIG_MIN} 檔以上一起動的群組（目前的篩選下有 ${nBig} 組）：兩檔一起動比較容易是巧合，越多檔同步越像同一筆資金">${pref.big ? '✓ ' : ''}只看 ${BIG_MIN} 檔以上 <small>${nBig}</small></button>`;
    body.innerHTML = `<div class="fd-filter-row">${filterHtml(data, f)}${sortBtn}${bigBtn}</div><div class="fd-cards">${cards}</div>
      <div class="fd-more-row"><button type="button" class="icon wide fd-more" hidden></button><span class="hint">點卡片看這群股票的連動動畫與走勢（出現在卡片區下方）</span></div>`;
    fitRows();
    closeDetail();
  }
  // 卡片區最多兩排高，其他在區內捲動（「展開全部」可切換）；寬度改變時重新量
  function fitRows() {
    const grid = body.querySelector('.fd-cards');
    const btn = body.querySelector('.fd-more');
    if (!grid || !btn) return;
    grid.style.maxHeight = '';
    const cards = [...grid.querySelectorAll('.fd-card')];
    const h = rowsHeight(cards.map((c) => c.offsetTop), 2, parseFloat(getComputedStyle(grid).rowGap) || 10);
    btn.hidden = h === null;
    grid.classList.toggle('fd-clip', h !== null && !pref.expanded);
    if (h !== null && !pref.expanded) grid.style.maxHeight = `${h}px`;
    btn.textContent = pref.expanded ? '收合成兩排 ▴' : `展開全部 ${cards.length} 張 ▾`;
  }
  new ResizeObserver(() => fitRows()).observe(body);
  body.addEventListener('click', (e) => {
    if (!e.target.closest('.fd-more')) return;
    e.stopPropagation();
    pref.expanded = !pref.expanded;
    save();
    fitRows();
  }, true);
  body.addEventListener('click', (e) => {
    if (!e.target.closest('.fd-big') || !state.data) return;
    e.stopPropagation();
    pref.big = !pref.big;
    save();
    drawCards();
  }, true);
  body.addEventListener('click', (e) => {
    if (!e.target.closest('.fd-sort') || !state.data) return;
    e.stopPropagation();
    pref.byEvidence = !pref.byEvidence;
    save();
    drawCards();
  }, true);
  body.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-filter]');
    if (!b || !state.data) return;
    e.stopPropagation();
    pref.filter[state.data.method] = b.dataset.filter;
    save();
    drawCards();
  });

  function closeDetail() {
    state.detailTicket += 1;
    state.stopRace();
    detail.hidden = true;
    detail.innerHTML = '';
    for (const c of body.querySelectorAll('.fd-card')) c.classList.remove('on');
  }
  // ＋族群: save a found group as a 自訂族群 (the existing group API; creating one is idempotent)
  body.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-save]');
    if (!btn || !state.data) return;
    e.stopPropagation();
    const g = state.data.groups[Number(btn.dataset.save)];
    const name = window.prompt('存成自訂族群，名稱：', defaultGroupName(g, state.date));
    if (!name || !name.trim()) return;
    btn.disabled = true;
    btn.textContent = '儲存中…';
    try {
      await createGroup(name.trim());
      for (const m of g.members) await addToGroup(name.trim(), m.code);
      btn.textContent = '✓ 已存';
      btn.title = `已存成自訂族群「${name.trim()}」，可在「管理族群」修改`;
    } catch (err) {
      btn.disabled = false;
      btn.textContent = '＋族群';
      window.alert(`儲存失敗：${err.message}`);
    }
  }, true);
  // 📰 a theme on a card: toggle its headlines under the theme line (not the card's detail)
  body.addEventListener('click', (e) => {
    const chip = e.target.closest('.fd-theme[data-theme]');
    if (!chip || !state.data) return;
    e.stopPropagation();
    const card = chip.closest('.fd-card');
    const g = state.data.groups[Number(card.dataset.i)];
    const box = card.querySelector('.fd-theme-news');
    const open = !box.hidden && box.dataset.theme === chip.dataset.theme;
    for (const c of card.querySelectorAll('.fd-theme')) c.classList.toggle('on', !open && c === chip);
    box.hidden = open;
    box.dataset.theme = chip.dataset.theme;
    box.innerHTML = open ? '' : themeNewsHtml(g.themes?.[Number(chip.dataset.theme)]);
  }, true);
  body.addEventListener('click', async (e) => {
    if (e.target.closest('button[data-save]') || e.target.closest('.fd-theme-news')) return; // a headline link: not the detail
    const card = e.target.closest('.fd-card');
    if (!card || !state.data) return;
    const g = state.data.groups[Number(card.dataset.i)];
    const wasOn = card.classList.contains('on');
    closeDetail();
    if (wasOn) return;
    card.classList.add('on');
    card.scrollIntoView({ block: 'nearest' }); // inside the two-row grid
    const t = state.detailTicket;
    detail.hidden = false;
    detail.innerHTML = '<p class="hint">載入連動資料…</p>';
    try {
      const codes = g.members.map((m) => m.code).join(',');
      const resp = await fetch(`/api/history/linkage-codes?date=${encodeURIComponent(state.date)}&codes=${encodeURIComponent(codes)}`, { cache: 'no-store' });
      const d = await resp.json();
      if (t !== state.detailTicket) return;
      if (!resp.ok) throw new Error(d.detail || `HTTP ${resp.status}`);
      // the card's 🔁 and 📰 come along into the detail header
      const seen = card.querySelector('.fd-seen')?.outerHTML || '';
      const themes = (card.querySelector('.fd-themes')?.textContent || '').replace('；點題材看新聞標題', '');
      const inst = card.querySelector('.fd-inst')?.outerHTML || ''; // 🏦 who is buying, under the title
      detail.innerHTML = `<div class="panel-h"><b>${escapeHtml(g.members.map((m) => m.name).join('、'))}</b>${seen}${themes ? `<span class="fd-themes">${escapeHtml(themes)}</span>` : ''}<button type="button" class="icon" data-act="close" aria-label="關閉" style="margin-left:auto">✕</button></div>${inst}<div class="fd-detail-body"></div>`;
      state.stopRace = renderBody(detail.querySelector('.fd-detail-body'), d, '20', onStock, '點一列看那檔的分時走勢');
      detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); // right under the card grid
    } catch (err) {
      if (t === state.detailTicket) detail.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
    }
  });
  detail.addEventListener('click', (e) => {
    if (e.target.closest('[data-act="close"]')) closeDetail();
  });

  // follows the date, but only loads while on screen (results are precomputed after the close)
  const onScreen = () => {
    const r = sec.getBoundingClientRect();
    return r.bottom > 0 && r.top < innerHeight;
  };
  // now = the user changed the method or a threshold: load whether or not the panel is on screen
  function schedule(now = false) {
    clearTimeout(state.timer);
    if (now || state.visible || onScreen()) state.timer = setTimeout(load, now ? 300 : 700);
  }
  new IntersectionObserver((entries) => {
    state.visible = entries.some((x) => x.isIntersecting);
    schedule();
  }).observe(sec);
  container.addEventListener('scroll', () => (key() !== state.loadedKey ? schedule() : null), { passive: true });
  drawMethod();
  return {
    refresh(date) {
      state.date = date;
      schedule();
    },
    // the 條件 now in use, for 盤後報告: the same query (so the same stored result, or the same search)
    query: (method) => queryOf(method, pref.params),
    condText: () => condText(pref.params),
  };
}
