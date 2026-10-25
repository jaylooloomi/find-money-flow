// History view: A (day heatmap), B (group ranking), C (rotation matrix), D (movers).
import {
  ROOT_ID,
  breadthColor,
  buildTreemapData,
  buildTreemapOption,
  colorFor,
  escapeHtml,
  fmtCap,
  fmtChange,
  fmtNum,
  fmtPct,
  fmtPrice,
  fmtTurnover,
  sortRows,
} from './transform.js';
import { MIN_FILTERS, groupMembers, limitTag, passesMin, toCsv } from './transform.js';
import { breadthBarHtml, breadthShare, marketBreadth } from './transform.js';
import { getGroups, loadGroups, onGroupsChange, openGroupPanel, setStockNames } from './groups.js';
import { mountIndexPanel } from './indexchart.js';
import { mountCountyMap } from './countymap.js';
import { mountWordCloud } from './wordcloud.js';
import { mountHistoryAiPanel } from './ainews.js';
import { mountNewsPanel } from './newsthemes.js';
import { digestText, finderMarkIndex, finderMarkTitle, mountFinderPanel } from './finder.js';
import { mountLinkagePanel } from './linkage.js';
import { mountReportPanel } from './report.js';
import { mountEtfStudy } from './etfstudy.js';
import { rotationInfoHtml } from './rotinfo.js';
import { groupText, relText } from './relations.js';

const PREFS_KEY = 'openmarket.history.v1';
const $ = (sel) => document.querySelector(sel);

const st = {
  days: [], // ok dates, newest first
  date: null,
  threshold: 5,
  sizeBy: 'cap',
  rotDays: 20,
  rotMetric: 'strong_ratio',
  moverMode: 'day',
  moverValue: { day: 5, range: 15, streak: 3 },
  from: null,
  groupSort: { key: 'strong_ratio', dir: 'desc' },
  grouping: 'industry', // industry | custom | concept
  zoom: null,
  // data
  stocks: [],
  groups: [],
  backfill: null,
};

let chart = null;
const csvData = { rotation: null, movers: null }; // bonus-07: last data shown in C / D
let started = false;
let daysTimer = null;
let indexPanel = null; // G: TAIEX + TPEx timeline (indexchart.js)
let aiPanel = null; // I: AI news explanations (ainews.js)
let newsPanel = null; // J: news-driven hot groups (newsthemes.js)
let linkPanel = null; // K: group linkage (linkage.js)
let reportPanel = null; // 分頁3: 盤後報告 (report.js)
const etfStudy = mountEtfStudy(document.querySelector('#historyTab4')); // 分頁4: ETF 溢價研究 (etfstudy.js)
let finderPanel = null; // N: 資金連動搜尋 (finder.js)
const finderMarks = { date: null, byKey: new Map(), ticket: 0 }; // B's 🔗: N's stored 找法1 result for the day
const countyPanel = { ctl: null, words: null, view: 'map', ticket: 0, cache: new Map() }; // H: county money map (countymap.js) or group word cloud (wordcloud.js)

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
    for (const k of ['threshold', 'sizeBy', 'rotDays', 'rotMetric', 'moverMode', 'groupSort', 'grouping']) if (p[k] !== undefined) st[k] = p[k];
    if (p.moverValue) Object.assign(st.moverValue, p.moverValue);
    if (st.groupSort?.key === 'rank_change') st.groupSort = { key: 'rank', dir: 'asc' }; // 名次 now sorts by the rank itself
  } catch {
    /* defaults */
  }
}

function savePrefs() {
  try {
    const { threshold, sizeBy, rotDays, rotMetric, moverMode, moverValue, groupSort, grouping } = st;
    localStorage.setItem(PREFS_KEY, JSON.stringify({ threshold, sizeBy, rotDays, rotMetric, moverMode, moverValue, groupSort, grouping }));
  } catch {
    /* ignore */
  }
}

async function getJson(url) {
  const resp = await fetch(url, { cache: 'no-store' });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// ---- days & status ----------------------------------------------------------

async function loadDays() {
  try {
    const data = await getJson('/api/history/days');
    const before = st.days.length;
    st.days = data.days.filter((d) => d.status === 'ok').map((d) => d.date);
    st.backfill = data.backfill;
    if (before && st.days.length !== before) loadRotation();
  } catch (err) {
    st.backfill = { error: err.message };
  }
  renderStatus();
  if (!st.date && st.days.length) await selectDate(hashWanted?.date || st.days[0]); // bonus-12: bookmarked day first
  else if (!st.days.length) showEmpty();
  // keep polling while the backfill is running (new days appear)
  clearTimeout(daysTimer);
  if (!st.days.length || st.backfill?.running) daysTimer = setTimeout(loadDays, 15000);
}

function renderStatus() {
  const b = st.backfill || {};
  const el = $('#hStatus');
  let cls = 'ok';
  let text = `${st.days.length} 個交易日`;
  if (b.running) {
    cls = 'loading';
    text = `回補中 ${b.done}/${b.total}（已有 ${st.days.length} 天）`;
  } else if (!st.days.length) {
    cls = 'loading';
    text = '等待第一次回補（需先載入產業分類）…';
  }
  if (b.error) {
    if (cls === 'ok') cls = 'stale';
    el.title = `最近錯誤：${b.error}`;
  } else el.title = '';
  el.className = `status ${cls}`;
  $('#hStatusText').textContent = text;
  if (st.days.length) {
    $('#hDate').min = st.days[st.days.length - 1];
    $('#hDate').max = st.days[0];
  }
}

function showEmpty() {
  $('#hEmpty').hidden = false;
  $('#hEmpty').textContent = '尚無歷史資料。首次啟動會在背景回補近一年（約 25 分鐘），完成的日子會陸續出現。';
}

/** Snap to the nearest trading day on or before `date`. */
function snap(date) {
  return st.days.find((d) => d <= date) || st.days[st.days.length - 1];
}

async function selectDate(date) {
  if (!st.days.length) return;
  st.date = snap(date);
  writeHash(); // bonus-12: synchronously, so the hash never lags behind overlapping loads
  $('#hDate').value = st.date;
  const i = st.days.indexOf(st.date);
  $('#hPrev').disabled = i >= st.days.length - 1;
  $('#hNext').disabled = i <= 0;
  if (!st.from || st.from >= st.date) st.from = st.days[Math.min(i + 4, st.days.length - 1)];
  $('#hFrom').value = st.from;
  $('#hBDate').textContent = st.date;
  await Promise.all([loadDay(), loadGroups_(), loadMovers()]);
  // heavy multi-day computations start only after the day's panels are in (they compete for the server)
  loadPeriods(); // bonus-10
  loadIndex(); // bonus-04
  renderRotationSelection();
  markCalendar(); // bonus-14
  indexPanel?.mark(st.date); // G
  aiPanel?.load(); // I: cached explanations for the day
  newsPanel?.load(); // J: that day's hot groups
  linkPanel?.refresh(st.date, st.grouping); // K follows the day
  finderPanel?.refresh(st.date); // N too (loads only while on screen)
  if (histTab.current === 3) reportPanel?.refresh(st.date, st.days); // 盤後報告 only while shown
}

// ---- A: day heatmap ---------------------------------------------------------

// Each loader drops responses that arrive after a newer request (fast date/grouping switches).
const tickets = { day: 0, groups: 0, rotation: 0, movers: 0 };

async function loadDay() {
  const t = ++tickets.day;
  const done = panelLoading('.hA', 'day', t);
  try {
    const data = await getJson(`/api/history/day/${st.date}?grouping=${st.grouping}`);
    if (t !== tickets.day) return;
    st.stocks = data.stocks.filter((s) => s.kind === 'stock');
    if (st.grouping === 'industry') setStockNames(st.stocks);
  } catch {
    if (t !== tickets.day) return;
    st.stocks = [];
  }
  done();
  renderChart();
}

function renderChart() {
  if (!chart) return;
  renderHistMinSelect(); // bonus-22
  const shown = histMin.key ? st.stocks.filter((s) => passesMin(s, histMin.key)) : st.stocks;
  const tree = buildTreemapData(shown, { sizeBy: st.sizeBy, colorBy: histColor.by }); // bonus-13
  chart.setOption(buildTreemapOption(tree), { notMerge: true });
  $('#hEmpty').hidden = tree.data.length > 0;
  if (!tree.data.length) {
    $('#hEmpty').textContent = customEmpty()
      ? '還沒有自訂族群（或族群內沒有股票）。按上方「管理族群」建立。'
      : histMin.key && st.stocks.length // bonus-22
        ? `${MIN_FILTERS[histMin.key].label} 的條件下沒有股票。`
        : '這一天沒有資料。';
  }
  if (st.zoom && tree.data.some((d) => d.name === st.zoom)) {
    chart.dispatchAction({ type: 'treemapRootToNode', seriesId: 'map', targetNodeId: 'ind:' + st.zoom });
  } else st.zoom = null;
  renderCrumb();
  renderHistBreadth(); // bonus-05
  renderHistColorSeg(); // bonus-13
  if (members.group) renderMembers(); // bonus-15
  renderCountyPanel(); // H follows the day shown in A
}

function zoomTo(name) {
  st.zoom = name;
  chart.dispatchAction({ type: 'treemapRootToNode', seriesId: 'map', targetNodeId: 'ind:' + name });
  renderCrumb();
}

function zoomOut() {
  st.zoom = null;
  chart.dispatchAction({ type: 'treemapRootToNode', seriesId: 'map', targetNodeId: ROOT_ID });
  renderCrumb();
}

function renderCrumb() {
  const el = $('#hCrumb');
  if (st.zoom) {
    el.innerHTML = `<button type="button" id="hCrumbRoot">全市場</button> › <b>${escapeHtml(st.zoom)}</b>`;
    $('#hCrumbRoot').addEventListener('click', zoomOut);
  } else el.innerHTML = '<span class="muted">點產業標題放大</span>';
}

// ---- B: group ranking -------------------------------------------------------

async function loadGroups_() {
  const t = ++tickets.groups;
  const done = panelLoading('.hB', 'groups', t);
  try {
    const data = await getJson(`/api/history/groups?date=${st.date}&threshold=${st.threshold}&grouping=${st.grouping}`);
    if (t !== tickets.groups) return;
    st.groups = data.groups.map((g) => ({ ...g, leader_pct: g.leader?.change_pct ?? null })); // bonus-09
    loadPeriods({ cachedOnly: true }); // bonus-10: fetch is deferred until A/B/D have loaded
    loadFlows(); // money flow columns
    loadFinderMarks(); // 🔗 from N's stored result (never starts a search)
    loadFinderDigest(); // 💡 toolbar line from the same stored results
  } catch {
    if (t !== tickets.groups) return;
    st.groups = [];
  }
  done();
  renderGroups();
}

function renderGroups() {
  $('#hStrongTh').textContent = `≥${st.threshold}%佔比`;
  const table = $('#hGroups');
  for (const th of table.querySelectorAll('th[data-sort]')) {
    if (th.dataset.sort === st.groupSort.key) th.setAttribute('aria-sort', st.groupSort.dir === 'asc' ? 'ascending' : 'descending');
    else th.removeAttribute('aria-sort');
  }
  const pct = (v, limit) => `<td class="c" style="background:${colorFor(v, limit)}">${fmtPct(v)}</td>`;
  table.tBodies[0].innerHTML = sortRows(filterGroupRows(st.groups), st.groupSort.key, st.groupSort.dir) // bonus-20
    .map(
      (g) => `<tr data-group="${escapeHtml(g.name)}"${g.name === st.zoom ? ' class="hl"' : ''}>
        <td class="l"><span class="clip" title="${escapeHtml(g.name)}">${g.z20 >= IGNITE_Z ? '<span title="成交比重暴增（Z ≥ 2）">🔥</span>' : ''}${finderMark(g.name)}${escapeHtml(g.name)}</span></td>
        ${rankCell(g)}
        <td class="muted">${g.count}</td>
        ${pct(g.avg_pct, 3)}
        ${ampCell(g.avg_amp)}
        ${pct(g.cap_pct, 3)}
        ${periodCells(g)}
        <td class="c" style="background:${colorFor(g.strong_ratio, 30)}">${g.strong_ratio === null ? '—' : g.strong_ratio.toFixed(0) + '%'}<small class="muted"> ${g.strong_count}</small></td>
        <td>${g.limit_up || ''}</td>
        <td class="c" style="background:${colorFor(g.limit_ratio, 20)}">${g.limit_ratio === null || g.limit_ratio === undefined ? '—' : g.limit_ratio.toFixed(0) + '%'}</td>
        <td class="c" style="background:${breadthColor(g.up, g.down)}">${g.up}/${g.down}</td>
        ${flowCells(g)}
        <td class="l">${g.leader ? `<a href="#" class="leader" data-code="${escapeHtml(g.leader.code)}">${escapeHtml(g.leader.name)} <span class="${g.leader.change_pct > 0 ? 'up' : g.leader.change_pct < 0 ? 'down' : ''}">${fmtPct(g.leader.change_pct)}</span></a>` : '—'}</td>
      </tr>`,
    )
    .join('');
}

// 平均振幅: amber, deeper the wider the day's range (10% and up is full)
function ampCell(v) {
  if (v === null || v === undefined) return '<td class="muted">—</td>';
  return `<td style="background:rgba(255,179,0,${Math.min(v / 10, 1) * 0.55})">${v.toFixed(2)}%</td>`;
}

// ---- B 🔗: groups where N (找法1, default thresholds) found stocks one pool of money moved together ----
function finderMark(name) {
  const found = finderMarks.date === st.date ? finderMarks.byKey.get(`${GROUPING_LABEL[st.grouping]}|${name}`) : null;
  return found ? `<span class="fd-mark" title="${escapeHtml(finderMarkTitle(found))}">🔗</span>` : '';
}

async function loadFinderMarks() {
  const t = ++finderMarks.ticket;
  const date = st.date;
  try {
    const data = await getJson(`/api/history/finder?date=${date}&method=known&stored_only=1`);
    if (t !== finderMarks.ticket) return;
    finderMarks.date = date;
    finderMarks.byKey = finderMarkIndex(data);
    renderGroups();
  } catch {
    /* no marks */
  }
}

// ---- 💡 toolbar digest: what N found today (stored results only), click to jump to N ----------
let digestTicket = 0;
async function loadFinderDigest() {
  let btn = $('#hFinderDigest');
  if (!btn) {
    btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'hFinderDigest';
    btn.className = 'icon wide fd-digest';
    btn.hidden = true;
    btn.title = 'N · 資金連動搜尋今天的結果（🔁＝前幾天也出現過的組合）；點一下到分頁2 看 N';
    btn.addEventListener('click', () => {
      setHistTab(2); // N lives on 分頁2
      $('#historyTab2').scrollTo({ top: 0, behavior: 'smooth' });
    });
    $('#hStatus').before(btn);
  }
  const t = ++digestTicket;
  const date = st.date;
  btn.hidden = true; // never leave the previous day's digest up while this one loads (or fails)
  try {
    const q = (m) => getJson(`/api/history/finder?date=${date}&method=${m}&stored_only=1`).catch(() => null);
    const [known, fresh] = await Promise.all([q('known'), q('new')]);
    if (t !== digestTicket || date !== st.date) return;
    const text = digestText(known, fresh);
    btn.textContent = text;
    btn.hidden = !text;
  } catch {
    btn.hidden = true;
  }
}

// ---- C: rotation ------------------------------------------------------------

function rotationColor(metric, v) {
  if (v === null || v === undefined) return colorFor(null, 1);
  if (metric === 'strong_ratio') return colorFor(v, 30);
  if (metric === 'breadth') return colorFor(v - 50, 50);
  if (metric === 'limit_up') return colorFor(v, 5);
  return colorFor(v, 3); // avg_pct: industry averages rarely exceed ±3%; share_z: ±3σ
}

function rotationText(metric, v) {
  if (v === null || v === undefined) return '';
  if (metric === 'limit_up') return v ? String(v) : '';
  if (metric === 'avg_pct' || metric === 'share_z') return v.toFixed(1);
  return v.toFixed(0);
}

/** A benchmark row's tooltip: what it holds under this metric (the sector rows also say what they cover). */
export function benchHint(g, metric) {
  if (!g.hint) return BENCH_HINT[metric] || '';
  const what = metric === 'avg_pct' || metric === 'cap_pct' ? '證交所官方類股指數漲跌幅' : `${(BENCH_HINT[metric] || '').replace('全市場普通股', '這個類別上市普通股')}`;
  return `${what}；範圍：${g.hint}`;
}

// what the 大盤 / 櫃買 rows hold under each metric (tooltip on the row name)
const BENCH_HINT = {
  avg_pct: '官方指數漲跌幅',
  cap_pct: '官方指數漲跌幅',
  strong_ratio: '全市場普通股的強勢佔比',
  breadth: '全市場普通股的上漲家數比',
  limit_up: '全市場普通股的漲停家數',
  share_z: '占上市櫃總成交值比重的 Z 值',
};

// C is below the fold and its first computation after a restart is the heaviest on the page (94 s
// once, slowing A/B/K/N meanwhile): ask for it only when C is near the screen; until then a request
// is just remembered and runs as soon as C scrolls within C_NEAR px. At start-up the panels above C
// are still placeholders, so the first decision waits C_SETTLE_MS.
const C_NEAR = 300;
const C_SETTLE_MS = 3000; // K / N / J fill in during the first seconds and push C down
const rotationLazy = { pending: false, watching: false, ready: false }; // ready: the panels above C are mounted

function rotationNear() {
  const el = document.querySelector('#historyView .hC');
  if (!el || el.offsetParent === null) return false;
  const r = el.getBoundingClientRect();
  return r.top < innerHeight + C_NEAR && r.bottom > -C_NEAR;
}

function loadRotation() {
  if (!rotationLazy.ready) {
    // start-up: K/N/J are not mounted yet, so C looks near the top; decide once they are
    rotationLazy.pending = true;
    return Promise.resolve();
  }
  if (rotationNear()) {
    rotationLazy.pending = false;
    return loadRotationNow();
  }
  rotationLazy.pending = true;
  // a request still in flight is for the old settings: drop it (and its dimmed 載入中 state)
  tickets.rotation += 1;
  document.querySelector('#historyView .hC')?.classList.remove('loading');
  if (!rotationLazy.watching) {
    rotationLazy.watching = true;
    const wake = () => {
      if (rotationLazy.pending && rotationNear()) {
        rotationLazy.pending = false;
        loadRotationNow();
      }
    };
    const el = document.querySelector('#historyView .hC');
    if (el) new IntersectionObserver(wake, { rootMargin: `${C_NEAR}px` }).observe(el);
    $('#historyView')?.addEventListener('scroll', wake, { passive: true }); // the observer can lag in background tabs
  }
  return Promise.resolve();
}

// C's ⓘ: what a cell means under the chosen metric (follows the metric and the 強勢門檻 while open)
function drawRotInfo() {
  const box = $('#hRotInfo');
  if (!box || box.hidden) return;
  box.innerHTML = rotationInfoHtml(st.rotMetric, st.threshold);
}
$('#hRotInfoBtn')?.addEventListener('click', () => {
  const box = $('#hRotInfo');
  box.hidden = !box.hidden;
  $('#hRotInfoBtn').setAttribute('aria-expanded', String(!box.hidden));
  $('#hRotInfoBtn').classList.toggle('on', !box.hidden);
  drawRotInfo();
});
$('#hRotInfo')?.addEventListener('click', (e) => {
  if (!e.target.closest('[data-act="close-info"]')) return;
  $('#hRotInfo').hidden = true;
  $('#hRotInfoBtn').classList.remove('on');
  $('#hRotInfoBtn').setAttribute('aria-expanded', 'false');
});

async function loadRotationNow() {
  drawRotInfo();
  const table = $('#hRotation');
  let data;
  const t = ++tickets.rotation;
  const done = panelLoading('.hC', 'rotation', t);
  try {
    data = await getJson(`/api/history/rotation?days=${st.rotDays}&metric=${st.rotMetric}&threshold=${st.threshold}&grouping=${st.grouping}`);
  } catch {
    if (t === tickets.rotation) table.innerHTML = '';
    done();
    return;
  }
  if (t !== tickets.rotation) return;
  done();
  csvData.rotation = data; // bonus-07
  const head = data.dates.map((d) => `<th data-date="${d}" title="${d}">${d.slice(5).replace('-', '/')}</th>`).join('');
  const bench = (data.benchmarks || []).map((g) => ({ ...g, bench: true }));
  const rows = [...bench, ...data.groups]
    .map(
      (g) =>
        `<tr${g.bench ? ` class="bench${g.hint ? ' sector' : ''}"` : ''}><td class="name"><span class="clip" title="${escapeHtml(g.bench ? `${g.name}：${benchHint(g, data.metric)}` : g.name)}">${escapeHtml(g.name)}</span></td>` +
        g.values
          .map(
            (v, i) =>
              `<td data-date="${data.dates[i]}" style="background:${rotationColor(data.metric, v)}" title="${escapeHtml(g.name)} ${data.dates[i]}：${v === null ? '—' : v.toFixed(2)}">${rotationText(data.metric, v)}</td>`,
          )
          .join('') +
        '</tr>',
    )
    .join('');
  table.innerHTML = `<thead><tr><th></th>${head}</tr></thead><tbody>${rows}</tbody>`;
  // 大盤 / 櫃買 stay pinned under the date header while the groups scroll
  let top = table.tHead.offsetHeight;
  for (const tr of table.querySelectorAll('tr.bench')) {
    for (const td of tr.cells) td.style.top = `${top}px`;
    top += tr.offsetHeight + 2; // border-spacing
  }
  renderRotationSelection();
  const wrap = table.parentElement;
  wrap.scrollLeft = wrap.scrollWidth; // newest on the right
}

function renderRotationSelection() {
  for (const el of document.querySelectorAll('#hRotation [data-date]')) el.classList.toggle('sel', el.dataset.date === st.date);
}

// ---- D: movers ----------------------------------------------------------------

async function loadMovers() {
  const mode = st.moverMode;
  const value = st.moverValue[mode];
  $('#hFromWrap').hidden = mode !== 'range';
  $('#hMoverUnit').textContent = mode === 'streak' ? '天' : '%';
  $('#hMoverValue').value = value;
  let url = `/api/history/movers?to=${st.date}&mode=${mode}&value=${value}&grouping=${st.grouping}`;
  if (mode === 'range') url += `&from=${st.from}`;
  let data;
  const t = ++tickets.movers;
  const done = panelLoading('.hD', 'movers', t);
  try {
    data = await getJson(url);
  } catch {
    if (t === tickets.movers) $('#hMovers').innerHTML = '<p class="hint">無法載入。</p>';
    done();
    return;
  }
  if (t !== tickets.movers) return;
  done();
  csvData.movers = data; // bonus-07
  const total = new Set(data.groups.flatMap((g) => g.stocks.map((x) => x.code))).size;
  $('#hMoverSummary').textContent = `${total} 檔，${data.groups.length} 個族群`;
  const unit = mode === 'streak' ? '天' : '%';
  $('#hMovers').innerHTML = data.groups.length
    ? data.groups
        .map(
          (g) => `<div class="mgroup"><b>${escapeHtml(g.name)}</b><span class="count">${g.stocks.length} 檔</span>
          <div class="tags">${g.stocks
            .map((s) => {
              const shown = mode === 'streak' ? `${s.value} 天` : `${s.value >= 0 ? '+' : ''}${s.value.toFixed(1)}%`;
              return `<a class="tag" data-code="${escapeHtml(s.code)}" href="https://histock.tw/stock/${encodeURIComponent(s.code)}" target="_blank" rel="noopener noreferrer"
                 style="background:${colorFor(s.change_pct, 7)}" title="${escapeHtml(s.code)} 當日 ${fmtPct(s.change_pct)}">${escapeHtml(s.name)}<small>${shown}</small></a>`;
            })
            .join('')}</div></div>`,
        )
        .join('')
    : `<p class="hint">沒有符合條件的股票（≥ ${value}${unit}）。</p>`;
}

// ---- controls -------------------------------------------------------------------

function renderSegs() {
  for (const seg of document.querySelectorAll('#historyView .seg[data-hgroup]')) {
    const current = String(st[seg.dataset.hgroup]);
    for (const b of seg.querySelectorAll('button')) {
      b.classList.toggle('on', b.dataset.value === current);
      b.setAttribute('aria-pressed', String(b.dataset.value === current));
    }
  }
}

function renderGrouping() {
  for (const b of $('#hGrouping').querySelectorAll('button')) {
    b.classList.toggle('on', b.dataset.value === st.grouping);
    b.setAttribute('aria-pressed', String(b.dataset.value === st.grouping));
  }
}

// third-party groupings that load in the background: concept (Yahoo) and chain (TPEx 細產業)
const LOADED_GROUPINGS = {
  concept: { url: '/api/concepts/status', label: '概念股分類', source: 'Yahoo 股市概念股分類', timer: null },
  news: { url: '/api/news-themes/status', label: '新聞族群', source: '新聞熱門族群（依當天新聞，每日更新）', timer: null },
  chain: { url: '/api/chains/status', label: '細產業分類', source: '櫃買中心產業價值鏈平台', timer: null },
  group: { url: '/api/relations/grouping-status', label: '集團分類', source: 'MoneyDJ 轉投資（權益法 ≥ 20%）與董監席位串起來的集團', timer: null },
};

async function refreshConceptStatus() {
  await Promise.all(Object.keys(LOADED_GROUPINGS).map(refreshLoadedGrouping));
}

async function refreshLoadedGrouping(name) {
  const g = LOADED_GROUPINGS[name];
  let status;
  try {
    status = await getJson(g.url);
  } catch {
    return;
  }
  const btn = $(`#hGrouping [data-value=${name}]`);
  btn.disabled = !status.ready;
  btn.title = status.ready
    ? `${g.source}：${status.count} 個族群（更新於 ${status.saved_at?.slice(0, 10)}）`
    : status.running
      ? `${g.label}下載中 ${status.done}/${status.total}…`
      : `${g.label}尚未載入${status.error ? '：' + status.error : ''}`;
  clearTimeout(g.timer);
  if (!status.ready) {
    g.timer = setTimeout(() => refreshLoadedGrouping(name), 10000);
    if (st.grouping === name) {
      st.grouping = 'industry';
      if (st.date) reloadGrouped();
      else renderGrouping();
    }
  }
}

function customEmpty() {
  return st.grouping === 'custom' && !getGroups().some((g) => g.codes.length);
}

/** Reload everything that depends on the grouping. */
async function reloadGrouped() {
  writeHash(); // bonus-12
  renderGrouping();
  if (customEmpty()) {
    $('#hEmpty').hidden = false;
    $('#hEmpty').textContent = '還沒有自訂族群（或族群內沒有股票）。按上方「管理族群」建立。';
  }
  if (!st.date) return;
  await Promise.all([loadDay(), loadGroups_(), loadMovers()]);
  loadPeriods(); // bonus-10
  loadRotation();
  loadIndex(); // bonus-04
}

function bind() {
  $('#hGrouping').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-value]');
    if (!b || b.disabled || b.dataset.value === st.grouping) return;
    st.grouping = b.dataset.value;
    st.zoom = null;
    idx.picked = []; // E: names picked under another grouping would not exist here
    members.group = null; // bonus-15: the picked group may not exist in the new grouping
    linkPanel?.hide();
    savePrefs();
    reloadGrouped();
  });
  $('#hManageGroups').addEventListener('click', () => openGroupPanel());
  onGroupsChange(() => {
    for (const key of [...periodState.cache.keys()]) if (key.endsWith('|custom')) periodState.cache.delete(key);
    if (st.grouping === 'custom') reloadGrouped();
  });
  $('#hPrev').addEventListener('click', () => {
    const i = st.days.indexOf(st.date);
    if (i < st.days.length - 1) selectDate(st.days[i + 1]);
  });
  $('#hNext').addEventListener('click', () => {
    const i = st.days.indexOf(st.date);
    if (i > 0) selectDate(st.days[i - 1]);
  });
  $('#hDate').addEventListener('change', (e) => e.target.value && selectDate(e.target.value));
  $('#hThreshold').addEventListener('change', (e) => {
    const v = Number(e.target.value);
    if (!(v >= 1 && v <= 10)) {
      e.target.value = st.threshold;
      return;
    }
    st.threshold = v;
    savePrefs();
    loadGroups_();
    loadRotation();
  });
  for (const seg of document.querySelectorAll('#historyView .seg[data-hgroup]')) {
    seg.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      const key = seg.dataset.hgroup;
      st[key] = key === 'rotDays' ? Number(b.dataset.value) : b.dataset.value;
      savePrefs();
      renderSegs();
      if (key === 'sizeBy') renderChart();
      else if (key === 'moverMode') loadMovers();
      else loadRotation();
    });
  }
  $('#hMoverValue').addEventListener('change', (e) => {
    const v = Number(e.target.value);
    if (!(v > 0)) {
      e.target.value = st.moverValue[st.moverMode];
      return;
    }
    st.moverValue[st.moverMode] = v;
    savePrefs();
    loadMovers();
  });
  $('#hFrom').addEventListener('change', (e) => {
    if (!e.target.value) return;
    st.from = e.target.value;
    loadMovers();
  });
  $('#hGroups').tHead.addEventListener('click', (e) => {
    const th = e.target.closest('th[data-sort]');
    if (!th) return;
    const key = th.dataset.sort;
    if (st.groupSort.key === key) st.groupSort.dir = st.groupSort.dir === 'asc' ? 'desc' : 'asc';
    else st.groupSort = { key, dir: key === 'name' || key === 'rank' ? 'asc' : 'desc' };
    savePrefs();
    renderGroups();
  });
  $('#hGroups').tBodies[0].addEventListener('click', (e) => {
    const leader = e.target.closest('a.leader[data-code]'); // bonus-09
    if (leader) {
      e.preventDefault();
      openHistCard(leader.dataset.code);
      return;
    }
    const tr = e.target.closest('tr[data-group]');
    if (!tr) return;
    zoomTo(tr.dataset.group);
    renderGroups();
  });
  $('#hRotation').addEventListener('click', (e) => {
    const cell = e.target.closest('[data-date]');
    if (cell) selectDate(cell.dataset.date);
  });
  document.addEventListener('keydown', (e) => {
    if ($('#historyView').hidden || e.target.matches('input')) return;
    if (e.key === 'ArrowLeft') $('#hPrev').click();
    else if (e.key === 'ArrowRight') $('#hNext').click();
    else if (e.key === 'Escape' && st.zoom) zoomOut();
  });
}

/** Called whenever the history view becomes visible. */
export async function showHistory() {
  if (!started) {
    started = true;
    loadPrefs();
    applyHashGrouping(); // bonus-12
    $('#hThreshold').value = st.threshold;
    await refreshConceptStatus();
    renderGrouping();
    loadGroups().catch(() => {});
    chart = echarts.init($('#hChart'));
    chart.on('click', (p) => {
      if (p.data?.stock) openHistCard(p.data.code);
      else if (p.data?.industry) zoomTo(p.data.name);
    });
    $('#hMovers').addEventListener('click', (e) => {
      const tag = e.target.closest('a.tag[data-code]');
      if (!tag || e.ctrlKey || e.metaKey) return; // ctrl/cmd-click still opens HiStock
      e.preventDefault();
      openHistCard(tag.dataset.code);
    });
    new ResizeObserver(() => chart.resize()).observe($('#hChart'));
    bind();
    renderSegs();
    renderCrumb();
    window.addEventListener('hashchange', onHashChange); // bonus-12
    setupRowResizer(); // A/B height can be dragged
    setupColResizer(); // and the A/B width ratio
    await loadDays();
    await applyHashDate(); // bonus-12
    loadIndex(); // bonus-04
    loadCalendar(); // bonus-14 (creates F synchronously, so G lands after it)
    indexPanel = mountIndexPanel($('#historyView'), (date) => {
      stopReplay(); // manual navigation ends a running replay
      selectDate(date);
    });
    await indexPanel.load();
    indexPanel.mark(st.date);
    mountCountyPanel();
    renderCountyPanel();
    aiPanel = mountHistoryAiPanel($('#historyView'), () => st.date);
    aiPanel.load();
    // K above J: the picked group's linkage right under B, the news-driven groups after it
    linkPanel = mountLinkagePanel($('#historyView'), { onStock: openHistCard });
    newsPanel = mountNewsPanel($('#historyView'), { getDate: () => st.date, onPick: pickNewsTheme, onStock: openHistCard });
    newsPanel.load();
    finderPanel = mountFinderPanel($('#historyTab2'), { // N on 分頁2 (it loads when its tab is shown)
      onStock: openHistCard,
      onResult: () => {
        // N just stored a result: B's 🔗 and the 💡 digest may change
        loadFinderMarks();
        loadFinderDigest();
      },
    });
    finderPanel.refresh(st.date);
    reportPanel = mountReportPanel($('#historyTab3'), { onStock: openHistCard, query: finderPanel.query, condText: finderPanel.condText });
    if (histTab.current === 3) reportPanel.refresh(st.date, st.days);
    setTimeout(() => {
      // once K/N/J have their content, C's real position decides (it loads when scrolled near)
      rotationLazy.ready = true;
      loadRotation();
    }, C_SETTLE_MS);
    return;
  }
  chart.resize();
  const pasted = parseHash(); // bonus-12: a bookmark pasted while in live mode
  if (pasted?.date || pasted?.grouping) await onHashChange();
  else writeHash();
  await loadDays();
  loadRotation();
  loadIndex(); // bonus-04
  loadCalendar(); // bonus-14
  indexPanel?.load().then(() => indexPanel.mark(st.date)); // G: pick up the day's new candle
}

// ---- bonus-02: stock card with a short price history ----------------------------

const CARD_DAYS = 20;
let cardChart = null;
let cardCode = null;

function cardEl() {
  let el = $('#hCard');
  if (!el) {
    el = document.createElement('aside');
    el.id = 'hCard';
    el.className = 'card';
    el.hidden = true;
    $('#historyView .hA .chart-wrap').appendChild(el);
  }
  return el;
}

function closeHistCard() {
  cardCode = null;
  cardEl().hidden = true;
  if (cardChart) {
    cardChart.dispose();
    cardChart = null;
  }
}

/** The card's 三大法人 rows (張): 合計 with its streak, then 外資／投信／自營; '' before 法人 is out. */
export function cardInstRows(i) {
  if (!i) return '';
  const n = (v) => `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString()}</span>`;
  const streak = (s, who) => (s ? `<small class="${s.side === 'buy' ? 'up' : 'down'}">${who}連${s.side === 'buy' ? '買' : '賣'} ${s.days} 天</small>` : '');
  return `<tr><td>法人</td><td>${n(i.total)} 張</td><td colspan="2">${[streak(i.streak, '法人'), streak(i.trust_streak, '投信')].filter(Boolean).join('　')}</td></tr>
      <tr><td colspan="4" class="muted">外資 ${n(i.foreign)}／投信 ${n(i.trust)}／自營 ${n(i.dealer)} 張</td></tr>`;
}

/** The card's 融資融券 rows (張): 增減 with 餘額, 券資比 and the 融資連增／連減 streak; '' when none. */
export function cardMarginRows(m) {
  if (!m) return '';
  const n = (v) => `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString()}</span>`;
  const s = m.streak ? `<small class="${m.streak.side === 'buy' ? 'up' : 'down'}">融資連${m.streak.side === 'buy' ? '增' : '減'} ${m.streak.days} 天</small>` : '';
  return `<tr><td>融資</td><td>${n(m.margin_chg)} 張</td><td>餘額</td><td>${m.margin_bal.toLocaleString()} 張</td></tr>
      <tr><td>融券</td><td>${n(m.short_chg)} 張</td><td>券資比</td><td>${m.short_ratio == null ? '—' : `${m.short_ratio.toFixed(1)}%`} ${s}</td></tr>`;
}

/** The card's 當沖比 row: today and the 5-day average, 🔥 when high (上市 and 上櫃). */
export function cardDaytradeRow(t) {
  if (!t) return '';
  return `<tr><td>當沖比</td><td class="${t.hot ? 'up' : ''}">${t.hot ? '🔥 ' : ''}${t.today.toFixed(1)}%</td><td>${t.days} 日平均</td><td>${t.avg5.toFixed(1)}%</td></tr>`;
}

/** The card's 🏢 block: the listed companies it holds / is held by, and its group (crawled slowly). */
export function cardRelationsHtml(r) {
  if (!r) return '';
  const list = (es) => es.map((e) => `<li>${relText(e)}</li>`).join('');
  const parts = [];
  if (r.group) parts.push(`<div>🏢 ${groupText(r.group)}</div>`);
  if (r.holds?.length) parts.push(`<div class="muted">持有／派任董監的上市櫃公司</div><ul>${list(r.holds)}</ul>`);
  if (r.held_by?.length) parts.push(`<div class="muted">被這些上市櫃公司持有</div><ul>${list(r.held_by)}</ul>`);
  if (!parts.length) return `<div class="muted">🏢 ${r.fetched_at ? '沒有上市櫃之間的持股／董監關係' : '轉投資／董監資料還沒抓到這檔（背景慢慢抓）'}</div>`;
  return parts.join('');
}

/** 外資分點 vs 官方外資: the same direction, or not (another account — 外資自營、境外帳戶 — at work). */
export function foreignLine(b) {
  const f = b.foreign_net;
  const o = b.foreign_official;
  if (f == null || o == null || (f === 0 && o === 0)) return '';
  const n = (v) => `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : ''}${Math.round(v).toLocaleString()}</span>`;
  const same = Math.sign(f) === Math.sign(o) || f === 0 || o === 0;
  const tip = '外資分點＝分點名單（買超、賣超各前 15 名）裡外資券商的合計買賣超；官方外資＝證交所／櫃買公布的外資買賣超。方向相反時，常是外資自營、境外帳戶或非外資客戶透過外資券商下單；只看前 15 名，不是全部分點';
  return `<div class="muted" title="${escapeHtml(tip)}">外資分點 ${n(f)} 張 vs 官方外資 ${n(o)} 張${same ? '' : ' <b class="down">方向相反</b>'}</div>`;
}

export const COST_TIP = '主力成本＝當天買超前 5 大分點的買進均價，依買超張數加權。收盤高於成本＝這批買家當天帳上獲利，低於＝套牢；只看這一天的進出，不是累積持股成本';
export const CONC_TIP = '籌碼集中度＝（前 15 大買超分點的買超 − 前 15 大賣超分點的賣超）÷ 當日成交量。正數＝大買家吃下的比大賣家倒出的多，籌碼往少數人集中；常見的看法是 > 10% 偏集中、< −10% 偏分散';

/** The card's 🏦 block: top 5 買超 / 賣超 分點 of the day (HiStock), 張, and its 籌碼集中度. */
export function cardBranchesHtml(b) {
  if (!b) return '';
  if (b.error) return `<span class="muted">🏦 ${escapeHtml(b.error)}</span>`;
  const streak = (r) => {
    const n = b.streaks?.[r.name];
    return n >= 2 ? ` <small class="up" title="這個分點連續 ${n} 個交易日都在買超名單（只算有存分點的日子）">連買 ${n} 天</small>` : '';
  };
  const list = (rows, sign) =>
    rows.slice(0, 5).map((r) => `<li>${escapeHtml(r.name)}${sign > 0 ? streak(r) : ''} <span class="${sign > 0 ? 'up' : 'down'}">${sign > 0 ? '+' : ''}${Math.round(r.net).toLocaleString()}</span>${r.avg ? ` <small class="muted">@${r.avg}</small>` : ''}</li>`).join('');
  const c = b.concentration;
  const conc = c == null ? '' : `　<span class="${c > 0 ? 'up' : c < 0 ? 'down' : ''}" title="${escapeHtml(CONC_TIP)}">籌碼集中度 ${c > 0 ? '+' : ''}${c.toFixed(1)}%</span>`;
  const gap = b.cost && b.close ? (b.close / b.cost - 1) * 100 : null;
  const cost = gap == null ? '' : `<div class="muted" title="${escapeHtml(COST_TIP)}">主力成本（前 5 大買超均價）<b>${b.cost.toFixed(2)}</b>，收盤 ${b.close} → <span class="${gap > 0 ? 'up' : gap < 0 ? 'down' : ''}">${gap > 0 ? '+' : ''}${gap.toFixed(1)}%</span>${gap > 0 ? '（主力帳上獲利）' : gap < 0 ? '（主力帳上套牢）' : ''}</div>`;
  return `<div class="muted">🏦 分點（HiStock）買超前 5／賣超前 5，張${conc}</div>${cost}${foreignLine(b)}<div class="card-br-cols"><ol>${list(b.buy || [], 1)}</ol><ol>${list(b.sell || [], -1)}</ol></div>`;
}

async function loadCardBranches(code, date) {
  try {
    const r = await getJson(`/api/history/branches?date=${encodeURIComponent(date)}&codes=${encodeURIComponent(code)}`);
    if (cardCode === code && $('#hCardBr')) $('#hCardBr').innerHTML = cardBranchesHtml(r.branches?.[code]);
  } catch {
    if ($('#hCardBr')) $('#hCardBr').innerHTML = '';
  }
}

async function loadCardRelations(code) {
  try {
    const r = await getJson(`/api/history/relations/${encodeURIComponent(code)}`);
    if (cardCode === code && $('#hCardRel')) $('#hCardRel').innerHTML = cardRelationsHtml(r);
  } catch {
    /* optional */
  }
}

async function openHistCard(code) {
  cardCode = code;
  let d;
  try {
    d = await getJson(`/api/history/stock/${encodeURIComponent(code)}?to=${st.date}&days=${CARD_DAYS}`);
  } catch {
    return;
  }
  if (cardCode !== code) return; // a newer click won
  const el = cardEl();
  const dir = d.change > 0 ? 'up' : d.change < 0 ? 'down' : '';
  el.innerHTML = `
    <h3>${escapeHtml(d.code)} ${escapeHtml(d.name)} <span class="sub">${escapeHtml(d.industry)} · ${escapeHtml(d.market)} · ${escapeHtml(d.date)}</span></h3>
    <div class="px"><b class="${dir}">${fmtPrice(d.close)}</b><span class="${dir}">${fmtChange(d.change)}（${fmtPct(d.change_pct)}）</span></div>
    <div id="hCardSpark" style="height:90px"></div>
    <table>
      <tr><td>近 ${d.series.length} 日</td><td>${fmtPct(d.period_pct)}</td><td>成交值</td><td>${fmtTurnover(d.turnover_100m)}</td></tr>
      <tr><td>最高</td><td>${fmtPrice(d.high)}</td><td>最低</td><td>${fmtPrice(d.low)}</td></tr>
      <tr><td>成交量</td><td>${fmtNum(d.volume_lots, 0)} 張</td><td>市值</td><td>${fmtCap(d.market_cap)}</td></tr>
      ${cardInstRows(d.inst)}
      ${cardMarginRows(d.margin)}
      ${cardDaytradeRow(d.daytrade)}
    </table>
    <div class="card-rel" id="hCardRel"></div>
    <div class="card-br" id="hCardBr"><span class="muted">🏦 分點載入中…</span></div>
    <div class="memb" data-memb="${escapeHtml(d.code)}"></div>
    <div class="btns">
      <a href="https://histock.tw/stock/${encodeURIComponent(d.code)}" target="_blank" rel="noopener noreferrer">HiStock ↗</a>
      <button type="button" id="hCardClose" aria-label="關閉">✕</button>
    </div>`;
  el.hidden = false;
  $('#hCardClose').addEventListener('click', closeHistCard);
  loadCardRelations(d.code);
  loadCardBranches(d.code, d.date);
  fillMembership(el);
  if (cardChart) cardChart.dispose();
  cardChart = echarts.init($('#hCardSpark'));
  const up = (d.period_pct ?? 0) >= 0;
  cardChart.setOption({
    animation: false,
    grid: { left: 2, right: 2, top: 6, bottom: 2 },
    xAxis: { type: 'category', show: false, data: d.series.map((p) => p.date) },
    yAxis: { type: 'value', show: false, scale: true },
    tooltip: { trigger: 'axis', confine: true, formatter: (ps) => `${ps[0].name}<br>${fmtPrice(ps[0].value)}` },
    series: [{
      type: 'line', data: d.series.map((p) => p.close), symbol: 'none', smooth: false,
      lineStyle: { color: up ? '#ff5c5c' : '#3fcf6f', width: 1.5 },
      areaStyle: { color: up ? 'rgba(255,92,92,.15)' : 'rgba(63,207,111,.15)' },
      markPoint: { symbol: 'circle', symbolSize: 6, data: [{ coord: [d.series.length - 1, d.close] }], itemStyle: { color: '#fff' }, label: { show: false } },
    }],
  });
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && cardCode && !$('#historyView').hidden) closeHistCard();
});

// ---- bonus-03: concept / custom group membership line on the card ----
async function fillMembership(card) {
  const slot = card.querySelector('[data-memb]');
  if (!slot) return;
  const code = slot.dataset.memb;
  let m;
  try {
    const resp = await fetch(`/api/membership/${encodeURIComponent(code)}`, { cache: 'no-store' });
    if (!resp.ok) return;
    m = await resp.json();
  } catch {
    return;
  }
  if (slot.dataset.memb !== code || !slot.isConnected) return;
  const MAX = 8;
  const line = (label, names) => {
    if (!names.length) return '';
    const shown = names.slice(0, MAX).map((n) => escapeHtml(n)).join('、');
    const more = names.length > MAX ? ` <span class="muted">等 ${names.length} 個</span>` : '';
    return `<div title="${escapeHtml(names.join('、'))}"><span class="muted">${label}</span> ${shown}${more}</div>`;
  };
  slot.innerHTML = line('概念：', m.concepts) + line('細產業：', m.chains || []) + line('自訂：', m.custom);
}

// ---- bonus-04: E · cumulative group return comparison -------------------------

const IDX_DAYS = [20, 60, 120];
const idx = { days: 20, picked: [], chart: null, ticket: 0 };

function indexSection() {
  let sec = $('#hIndex');
  if (sec) return sec;
  sec = document.createElement('section');
  sec.id = 'hIndex';
  sec.className = 'panel';
  sec.style.gridColumn = '1 / -1';
  sec.innerHTML = `
    <div class="panel-h"><b>E · 族群走勢比較</b>
      <div class="seg" id="hIdxDays">${IDX_DAYS.map((d) => `<button data-value="${d}">${d} 日</button>`).join('')}</div>
      <select class="field" id="hIdxAdd" aria-label="加入族群"><option value="">＋ 加入族群…</option></select>
      <span id="hIdxChips" class="gp-codes"></span>
      <button type="button" class="icon wide" id="hIdxReset" title="改回期間累積報酬前 5 名">前 5 名</button>
      <span class="muted">等權日報酬連乘；虛線為全市場</span>
    </div>
    <div style="height:340px"><div id="hIdxChart" style="height:100%"></div></div>`;
  $('#historyView').appendChild(sec);
  $('#hIdxDays').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-value]');
    if (!b) return;
    idx.days = Number(b.dataset.value);
    loadIndex();
  });
  $('#hIdxAdd').addEventListener('change', (e) => {
    const name = e.target.value;
    e.target.value = '';
    if (name && !idx.picked.includes(name)) {
      idx.picked.push(name);
      loadIndex();
    }
  });
  $('#hIdxChips').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-remove]');
    if (!b) return;
    idx.picked = idx.picked.filter((n) => n !== b.dataset.remove);
    loadIndex();
  });
  $('#hIdxReset').addEventListener('click', () => {
    idx.picked = [];
    loadIndex();
  });
  idx.chart = echarts.init($('#hIdxChart'));
  new ResizeObserver(() => idx.chart.resize()).observe($('#hIdxChart'));
  return sec;
}

async function loadIndex() {
  if (!st.date) return;
  indexSection();
  for (const b of $('#hIdxDays').querySelectorAll('button')) b.classList.toggle('on', Number(b.dataset.value) === idx.days);
  let url = `/api/history/index?days=${idx.days}&to=${st.date}&grouping=${st.grouping}`;
  if (idx.picked.length) url += `&groups=${encodeURIComponent(idx.picked.join(','))}`;
  const ticket = ++idx.ticket; // ignore responses that arrive after a newer request
  let d;
  try {
    d = await getJson(url);
  } catch {
    return;
  }
  if (ticket !== idx.ticket) return;
  const names = (st.groups || []).map((g) => g.name).sort((a, b) => a.localeCompare(b, 'zh-Hant'));
  $('#hIdxAdd').innerHTML = '<option value="">＋ 加入族群…</option>' +
    names.filter((n) => !idx.picked.includes(n)).map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
  $('#hIdxChips').innerHTML = idx.picked
    .map((n) => `<span>${escapeHtml(n)}<button type="button" data-remove="${escapeHtml(n)}" title="移除">✕</button></span>`)
    .join('');
  const pct = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  idx.chart.setOption({
    animation: false,
    color: ['#ff6b6b', '#ffb84d', '#ffe66d', '#6bcB77', '#4d96ff', '#c77dff', '#ff8fab', '#5ee7df', '#f9f871', '#a0a0ff'],
    grid: { left: 52, right: 150, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : pct(v)) },
    legend: { type: 'scroll', orient: 'vertical', right: 0, top: 10, textStyle: { color: '#ccc' } },
    xAxis: { type: 'category', data: d.dates.map((x) => x.slice(5).replace('-', '/')), axisLabel: { color: '#999' } },
    yAxis: { type: 'value', axisLabel: { color: '#999', formatter: (v) => `${v}%` }, splitLine: { lineStyle: { color: '#262a33' } } },
    series: [
      ...d.series.map((s) => ({ name: s.name, type: 'line', data: s.values, symbol: 'none', lineStyle: { width: 2 }, endLabel: { show: false } })),
      { name: '全市場', type: 'line', data: d.market, symbol: 'none', lineStyle: { width: 1.5, type: 'dashed', color: '#aaa' }, itemStyle: { color: '#aaa' } },
    ],
  }, { notMerge: true });
}

// ---- bonus-05: market breadth for the selected day (A panel header) ----
function renderHistBreadth() {
  let el = $('#mbarHist');
  if (!el) {
    el = document.createElement('span');
    el.id = 'mbarHist';
    $('#hCrumb').after(el);
  }
  // custom/concept groupings repeat a stock once per group: count each code once
  const seen = new Set();
  const unique = st.stocks.filter((s) => !seen.has(s.code) && seen.add(s.code));
  el.innerHTML = breadthBarHtml(marketBreadth(unique));
  renderIndexChange();
}

// ---- A header: the day's 加權 / 櫃買 change, to judge each group against the market ----------
const idxChange = { byDate: null, loading: null, at: 0 };

/** {date: {twse, tpex}} % change from the stored official index rows (close vs previous close). */
function indexChanges(days) {
  const pct = (r) => (r && r.close != null && r.change != null && r.close !== r.change ? (r.change / (r.close - r.change)) * 100 : null);
  return Object.fromEntries(days.map((d) => [d.date, { twse: pct(d.twse), tpex: pct(d.tpex) }]));
}

async function renderIndexChange() {
  let el = $('#hIdxChg');
  if (!el) {
    el = document.createElement('span');
    el.id = 'hIdxChg';
    el.className = 'idx-chg';
    el.title = '當天加權指數、櫃買指數漲跌幅：拿來和族群比強弱';
    $('#mbarHist').after(el);
  }
  // refetch at most every 10 minutes (a new day's index arrives after the close)
  if ((!idxChange.byDate || Date.now() - idxChange.at > 600_000) && !idxChange.loading) {
    idxChange.loading = getJson('/api/history/market-index?days=500')
      .then((data) => {
        idxChange.byDate = indexChanges(data.days);
        idxChange.at = Date.now();
      })
      .catch(() => {})
      .finally(() => {
        idxChange.loading = null;
      });
  }
  if (!idxChange.byDate) await idxChange.loading;
  const c = idxChange.byDate?.[st.date] || {};
  const part = (label, v) => `<span class="muted">${label}</span> <b class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v == null ? '—' : fmtPct(v)}</b>`;
  el.innerHTML = `${part('加權', c.twse)}　${part('櫃買', c.tpex)}`;
}

// ---- bonus-06: day-by-day replay ------------------------------------------------

const REPLAY_SPEEDS = [1, 2, 4]; // seconds per trading day
const replay = { on: false, timer: null, speed: 2 };

function replayControls() {
  if ($('#hPlay')) return;
  const wrap = document.createElement('span');
  wrap.className = 'replay';
  wrap.innerHTML = `<button class="icon wide" id="hPlay" title="從目前日期逐日播放到最新日" aria-label="播放">▶▶</button>
    <select class="field" id="hSpeed" aria-label="播放速度" title="每個交易日停留秒數">${REPLAY_SPEEDS.map((s) => `<option value="${s}">${s} 秒/日</option>`).join('')}</select>`;
  $('#hNext').after(wrap);
  $('#hSpeed').value = String(replay.speed);
  $('#hSpeed').addEventListener('change', (e) => (replay.speed = Number(e.target.value)));
  $('#hPlay').addEventListener('click', () => (replay.on ? stopReplay() : startReplay()));
  // any manual navigation stops the replay
  for (const id of ['#hPrev', '#hNext', '#hDate']) $(id).addEventListener(id === '#hDate' ? 'change' : 'click', () => stopReplay(), true);
  $('#hRotation').addEventListener('click', () => stopReplay(), true);
}

function renderReplayButton() {
  const b = $('#hPlay');
  if (!b) return;
  b.textContent = replay.on ? '⏸' : '▶▶';
  b.title = replay.on ? '暫停播放' : '從目前日期逐日播放到最新日';
  b.classList.toggle('on', replay.on);
}

async function startReplay() {
  if (!st.days.length) return;
  let i = st.days.indexOf(st.date);
  if (i <= 0) i = Math.min(20, st.days.length - 1); // at the newest day: start 20 trading days back
  replay.on = true;
  renderReplayButton();
  await selectDate(st.days[i]);
  scheduleReplayStep();
}

function scheduleReplayStep() {
  clearTimeout(replay.timer);
  if (!replay.on) return;
  replay.timer = setTimeout(async () => {
    const i = st.days.indexOf(st.date);
    if (!replay.on || i <= 0) return stopReplay();
    await selectDate(st.days[i - 1]);
    if (i - 1 <= 0) return stopReplay();
    scheduleReplayStep();
  }, replay.speed * 1000);
}

function stopReplay() {
  replay.on = false;
  clearTimeout(replay.timer);
  renderReplayButton();
}

document.addEventListener('DOMContentLoaded', replayControls);
if (document.readyState !== 'loading') replayControls();

// ---- bonus-07: CSV export of B / C / D ----------------------------------------------

const GROUPING_LABEL = { industry: '官方產業', custom: '自訂族群', concept: '概念股', chain: '細產業', news: '新聞族群' };

function downloadCsv(name, header, rows) {
  const blob = new Blob([toCsv(header, rows)], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

const CSV_EXPORTS = {
  hB: () => [
    `強勢族群_${st.date}_${GROUPING_LABEL[st.grouping]}.csv`,
    ['日期', '族群', '平均漲幅%', '平均振幅%', '市值加權%', `漲幅≥${st.threshold}%家數`, `≥${st.threshold}%佔比%`, '漲停', '漲停佔比%', '比重%', '比重Z(20日)', '資金流', '週轉率%', '法人淨買超(元)', '跌停', '上漲', '下跌', '家數', '成交值(元)'],
    sortRows(st.groups, st.groupSort.key, st.groupSort.dir).map((g) => [
      st.date, g.name, g.avg_pct, g.avg_amp, g.cap_pct, g.strong_count, g.strong_ratio, g.limit_up, g.limit_ratio, g.share, g.z20, g.mf, g.turnover_rate, g.inst_net, g.limit_down, g.up, g.down, g.count, g.turnover,
    ]),
  ],
  hC: () => {
    const d = csvData.rotation;
    if (!d) return null;
    return [
      `族群輪動_${d.metric}_${d.dates[d.dates.length - 1] || ''}_${GROUPING_LABEL[st.grouping]}.csv`,
      ['族群', ...d.dates],
      [...(d.benchmarks || []), ...d.groups].map((g) => [g.name, ...g.values]),
    ];
  },
  hD: () => {
    const d = csvData.movers;
    if (!d) return null;
    return [
      `集體上漲_${d.mode}_${st.date}_${GROUPING_LABEL[st.grouping]}.csv`,
      ['日期', '族群', '代號', '名稱', d.label || '數值', '當日漲幅%', '收盤'],
      d.groups.flatMap((g) => g.stocks.map((x) => [st.date, g.name, x.code, x.name, x.value, x.change_pct, x.close])),
    ];
  },
};

function addCsvButtons() {
  for (const [cls, build] of Object.entries(CSV_EXPORTS)) {
    const head = document.querySelector(`#historyView .${cls} .panel-h`);
    if (!head || head.querySelector('.csv-btn')) continue;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'icon wide csv-btn';
    b.textContent = 'CSV';
    b.title = '匯出目前顯示的資料（Excel 可開）';
    b.addEventListener('click', () => {
      const out = build();
      if (out && out[2].length) downloadCsv(...out);
    });
    head.appendChild(b);
  }
}

if (document.readyState !== 'loading') addCsvButtons();
else document.addEventListener('DOMContentLoaded', addCsvButtons);

// ---- bonus-10: 5/20/60-day group returns in the ranking (filled in asynchronously) ----

const periodState = { ticket: 0, cache: new Map() }; // key: date|grouping → {group: perf}

function periodCells(g) {
  const cell = (v, limit) => (v === undefined ? '<td class="muted">…</td>' : `<td class="c" style="background:${colorFor(v, limit)}">${fmtPct(v)}</td>`);
  return cell(g.perf_5, 8) + cell(g.perf_20, 15) + cell(g.perf_60, 25);
}

async function loadPeriods({ cachedOnly = false } = {}) {
  const key = `${st.date}|${st.grouping}`;
  const apply = (map) => {
    st.groups = st.groups.map((g) => ({ ...g, ...(map[g.name] || { perf_5: null, perf_20: null, perf_60: null }) }));
    renderGroups();
  };
  if (periodState.cache.has(key)) return apply(periodState.cache.get(key));
  if (cachedOnly) return;
  const ticket = ++periodState.ticket;
  let data;
  try {
    data = await getJson(`/api/history/periods?date=${st.date}&grouping=${st.grouping}`);
  } catch {
    return;
  }
  periodState.cache.set(key, data.groups);
  if (ticket === periodState.ticket && key === `${st.date}|${st.grouping}`) apply(data.groups);
}

// ---- bonus-12: bookmarkable view state in the URL hash (#history/<date>/<grouping>) ----

const GROUPINGS_OK = new Set(['industry', 'custom', 'concept', 'chain', 'news', 'group']);

function parseHash() {
  const m = /^#history(?:\/(\d{4}-\d{2}-\d{2}))?(?:\/(industry|custom|concept|chain|news|group))?(?:\/([1234]))?$/.exec(location.hash);
  return m ? { date: m[1] || null, grouping: m[2] || null, tab: m[3] ? Number(m[3]) : null } : null;
}

function writeHash() {
  if ($('#historyView').hidden || !st.date) return;
  const hash = `#history/${st.date}/${st.grouping}${histTab.current > 1 ? `/${histTab.current}` : ''}`;
  if (location.hash !== hash) history.replaceState(null, '', hash);
}

// ---- 歷史 分頁: 分頁1 = every panel (#historyView), 分頁2 = its own area; date and grouping shared ----

const TAB_KEY = 'openmarket.history.tab';
const histTab = { current: 1 };
try {
  const saved = Number(localStorage.getItem(TAB_KEY));
  if (saved === 2 || saved === 3 || saved === 4) histTab.current = saved;
} catch {
  /* default */
}

function setHistTab(tab) {
  histTab.current = tab === 2 || tab === 3 || tab === 4 ? tab : 1;
  try {
    localStorage.setItem(TAB_KEY, String(histTab.current));
  } catch {
    /* ignore */
  }
  const view = $('#historyView');
  view.classList.toggle('tab-off', histTab.current !== 1);
  view.dataset.tab = String(histTab.current);
  $('#historyTab2').hidden = view.hidden || histTab.current !== 2;
  $('#historyTab3').hidden = view.hidden || histTab.current !== 3;
  $('#historyTab4').hidden = view.hidden || histTab.current !== 4;
  for (const b of $('#hTabs').querySelectorAll('[data-tab]')) {
    const on = Number(b.dataset.tab) === histTab.current;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', String(on));
  }
  if (histTab.current === 1) window.dispatchEvent(new Event('resize')); // charts drawn while hidden get their size back
  // N is on 分頁2: its on-screen observer can miss a tab being shown, so tell it directly
  if (histTab.current === 2 && st.date) finderPanel?.refresh(st.date);
  if (histTab.current === 3 && st.date) reportPanel?.refresh(st.date, st.days);
  if (histTab.current === 4) etfStudy.refresh();
  writeHash();
}

$('#hTabs').addEventListener('click', (e) => {
  const b = e.target.closest('[data-tab]');
  if (b) setHistTab(Number(b.dataset.tab));
});

let hashWanted = null; // the bookmarked view, read once before the first load rewrites the hash

function applyHashGrouping() {
  const h = (hashWanted = parseHash());
  if (h?.grouping && GROUPINGS_OK.has(h.grouping)) st.grouping = h.grouping;
  // a bookmarked view says its tab (none = 分頁1); otherwise the last one used
  setHistTab(h ? h.tab || 1 : histTab.current);
}

async function applyHashDate() {
  hashWanted = null; // the first selectDate already used it
  writeHash();
}

async function onHashChange() {
  const h = parseHash();
  if (!h || $('#historyView').hidden) return;
  if ((h.tab || 1) !== histTab.current) setHistTab(h.tab || 1);
  const groupingChanged = !!h.grouping && GROUPINGS_OK.has(h.grouping) && h.grouping !== st.grouping;
  const dateChanged = !!h.date && h.date !== st.date;
  if (groupingChanged) {
    st.grouping = h.grouping;
    st.zoom = null;
    idx.picked = [];
    savePrefs();
    renderGrouping();
  }
  if (dateChanged) {
    await selectDate(h.date); // loads A/B/D/E with the (new) grouping
    if (groupingChanged) loadRotation();
  } else if (groupingChanged) await reloadGrouped();
}


// ---- bonus-13: colour the history heatmap by 1 / 5 / 20-day returns ---------------------

const histColor = { by: 'today' };
try {
  const saved = JSON.parse(localStorage.getItem('openmarket.history.color') || '""');
  if (['today', 'week', 'p20'].includes(saved)) histColor.by = saved;
} catch {
  /* default */
}

function renderHistColorSeg() {
  let seg = $('#hColorSeg');
  if (!seg) {
    seg = document.createElement('div');
    seg.className = 'seg';
    seg.id = 'hColorSeg';
    seg.title = '格子顏色依 1 日／5 日／20 日漲幅（收盤對 N 個交易日前收盤）';
    seg.innerHTML = '<button data-value="today">顏色：1日</button><button data-value="week">5日</button><button data-value="p20">20日</button>';
    document.querySelector('#historyView .hA .seg[data-hgroup="sizeBy"]').before(seg);
    seg.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-value]');
      if (!b || b.dataset.value === histColor.by) return;
      histColor.by = b.dataset.value;
      try {
        localStorage.setItem('openmarket.history.color', JSON.stringify(histColor.by));
      } catch {
        /* ignore */
      }
      renderChart();
    });
  }
  for (const b of seg.querySelectorAll('button')) b.classList.toggle('on', b.dataset.value === histColor.by);
}

// ---- bonus-14: F · daily market-breadth calendar --------------------------------------

function calendarSection() {
  let sec = $('#hCalendar');
  if (sec) return sec;
  sec = document.createElement('section');
  sec.id = 'hCalendar';
  sec.className = 'panel';
  sec.style.gridColumn = '1 / -1';
  sec.innerHTML = `<div class="panel-h"><b>F · 每日廣度月曆</b>
      <span class="muted">每格一個交易日；顏色＝當天上漲家數佔比（紅多漲、綠多跌）；點格子切換日期</span></div>
    <div id="hCalBody" style="padding:8px 10px;display:grid;grid-template-columns:70px 1fr;gap:3px 8px;font-size:11px"></div>`;
  $('#historyView').appendChild(sec);
  $('#hCalBody').addEventListener('click', (e) => {
    const cell = e.target.closest('[data-date]');
    if (!cell) return;
    stopReplay(); // manual navigation ends a running replay
    selectDate(cell.dataset.date);
  });
  return sec;
}

async function loadCalendar() {
  calendarSection();
  let data;
  try {
    data = await getJson('/api/history/calendar');
  } catch {
    return;
  }
  const months = new Map();
  for (const d of data.days) {
    const m = d.date.slice(0, 7);
    if (!months.has(m)) months.set(m, []);
    months.get(m).push(d);
  }
  $('#hCalBody').innerHTML = [...months]
    .reverse()
    .map(([m, days]) => {
      const cells = days
        .map((d) => {
          const share = breadthShare(d.up, d.down);
          const title = `${d.date}：上漲 ${d.up}／下跌 ${d.down}${share === null ? '' : `（${share.toFixed(0)}%）`}，平均 ${fmtPct(d.avg_pct)}`;
          return `<span data-date="${d.date}" title="${title}" style="display:inline-flex;align-items:center;justify-content:center;width:26px;height:22px;margin:0 2px 2px 0;border-radius:3px;cursor:pointer;color:#fff;background:${breadthColor(d.up, d.down)}">${Number(d.date.slice(8))}</span>`;
        })
        .join('');
      return `<div class="muted" style="align-self:center">${m}</div><div>${cells}</div>`;
    })
    .join('');
  markCalendar();
}

function markCalendar() {
  for (const el of document.querySelectorAll('#hCalBody [data-date]')) {
    el.style.outline = el.dataset.date === st.date ? '2px solid #fff' : '';
  }
}

// ---- bonus-15: member table for the group picked in B ---------------------------------

const members = { group: null, sort: { key: 'change_pct', dir: 'desc' } };

function membersBox() {
  let box = $('#hMembers');
  if (box) return box;
  box = document.createElement('div');
  box.id = 'hMembers';
  box.hidden = true;
  box.style.cssText = 'border-top:1px solid var(--line);max-height:230px;overflow:auto;flex-shrink:0';
  document.querySelector('#historyView .hB').appendChild(box);
  box.addEventListener('click', (e) => {
    const th = e.target.closest('th[data-msort]');
    if (th) {
      const key = th.dataset.msort;
      members.sort = members.sort.key === key
        ? { key, dir: members.sort.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'name' || key === 'code' ? 'asc' : 'desc' };
      renderMembers();
      return;
    }
    if (e.target.closest('#hMembersClose')) {
      members.group = null;
      renderMembers();
      return;
    }
    const tr = e.target.closest('tr[data-code]');
    if (tr) openHistCard(tr.dataset.code);
  });
  return box;
}

function renderMembers() {
  const box = membersBox();
  if (!members.group) {
    box.hidden = true;
    return;
  }
  const list = sortRows(groupMembers(st.stocks, members.group), members.sort.key, members.sort.dir);
  const th = (key, label, cls = '') => {
    const aria = members.sort.key === key ? ` aria-sort="${members.sort.dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th data-msort="${key}" class="${cls}"${aria}>${label}</th>`;
  };
  const cell = (v, limit) => `<td class="c" style="background:${colorFor(v, limit)}">${fmtPct(v)}</td>`;
  box.innerHTML = `<div class="panel-h" style="position:sticky;top:0;background:var(--panel);z-index:2"><b>${escapeHtml(members.group)}</b>
      <span class="muted">${list.length} 檔成分股 · ${escapeHtml(st.date || '')} · 點股票看卡片</span><span class="spacer"></span>
      <button type="button" class="icon" id="hMembersClose" aria-label="關閉">✕</button></div>
    <table class="grid"><thead><tr>${th('code', '代號', 'l')}${th('name', '名稱', 'l')}${th('change_pct', '漲幅')}${th('week_pct', '5日')}${th('period_20', '20日')}${th('turnover_100m', '成交值')}</tr></thead>
    <tbody>${list
      .map((s) => `<tr data-code="${escapeHtml(s.code)}"><td class="l">${escapeHtml(s.code)}</td><td class="l">${escapeHtml(s.name)}${limitTag(s.change_pct) ? ` <span class="muted">${limitTag(s.change_pct)}</span>` : ''}</td>${cell(s.change_pct, 7)}${cell(s.week_pct, 15)}${cell(s.period_20, 25)}<td>${fmtTurnover(s.turnover_100m)}</td></tr>`)
      .join('')}</tbody></table>`;
  box.hidden = false;
}

// pick a group from B (the existing B click handler also zooms A to it)
document.addEventListener('click', (e) => {
  const tr = e.target.closest('#hGroups tbody tr[data-group]');
  if (!tr || e.target.closest('a.leader')) return;
  members.group = tr.dataset.group;
  renderMembers();
  linkPanel?.show(members.group, st.date, st.grouping); // K: linkage of the picked group
}, true); // capture: the B handler re-renders the rows, detaching the clicked one


// ---- bonus-18: rank change vs the previous trading day -----------------------------
function rankCell(g) {
  if (g.rank == null) return '<td class="muted">—</td>';
  const ch = g.rank_change;
  const delta = ch == null ? '' : ch > 0 ? ` <span class="up">▲${ch}</span>` : ch < 0 ? ` <span class="down">▼${-ch}</span>` : ' <span class="muted">–</span>';
  return `<td title="今日名次 ${g.rank}">${g.rank}${delta}</td>`;
}

// ---- bonus-20: filter the B ranking by group name ------------------------------------
const groupFilter = { text: '' };

function filterGroupRows(rows) {
  ensureGroupFilterBox();
  const t = groupFilter.text.trim().toLowerCase();
  const named = t ? rows.filter((g) => g.name.toLowerCase().includes(t)) : rows;
  return flowState.igniteOnly ? named.filter((g) => g.z20 >= IGNITE_Z) : named;
}

function ensureGroupFilterBox() {
  if ($('#hGroupFilter')) return;
  const input = document.createElement('input');
  input.id = 'hGroupFilter';
  input.className = 'field';
  input.type = 'search';
  input.placeholder = '篩選族群名稱…';
  input.style.width = '140px';
  input.setAttribute('aria-label', '篩選族群名稱');
  $('#hBDate').after(input);
  input.addEventListener('input', () => {
    groupFilter.text = input.value;
    renderGroups();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      input.value = '';
      groupFilter.text = '';
      renderGroups();
    }
  });
}

// ---- bonus-21: fullscreen button for the history heatmap (A) -------------------------
(function addHistFullscreen() {
  const wrap = document.querySelector('#historyView .hA .chart-wrap');
  if (!wrap || !document.fullscreenEnabled) return;
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'fs-btn';
  b.title = '全螢幕（Esc 離開）';
  b.setAttribute('aria-label', '全螢幕');
  b.textContent = '⛶';
  b.addEventListener('click', () => (document.fullscreenElement ? document.exitFullscreen() : wrap.requestFullscreen()));
  wrap.appendChild(b);
})();

// ---- bonus-22: minimum turnover / market-cap filter (history heatmap A only) ------------
const histMin = { key: '' };
try {
  const saved = JSON.parse(localStorage.getItem('openmarket.history.min') || '""');
  if (saved in MIN_FILTERS) histMin.key = saved;
} catch {
  /* default */
}

function renderHistMinSelect() {
  let sel = $('#hMinSel');
  if (!sel) {
    sel = document.createElement('select');
    sel.id = 'hMinSel';
    sel.className = 'field';
    sel.title = '熱力圖只顯示成交值或市值達門檻的股票（B／D 統計不受影響）';
    sel.innerHTML = Object.entries(MIN_FILTERS).map(([k, f]) => `<option value="${k}">${f.label}</option>`).join('');
    document.querySelector('#historyView .hA .seg[data-hgroup="sizeBy"]').before(sel);
    sel.addEventListener('change', () => {
      histMin.key = sel.value;
      try {
        localStorage.setItem('openmarket.history.min', JSON.stringify(histMin.key));
      } catch {
        /* ignore */
      }
      renderChart();
    });
  }
  sel.value = histMin.key;
}

// ---- ③ H: county money map for the selected day ---------------------------------------------
function mountCountyPanel() {
  if (countyPanel.ctl) return;
  const sec = document.createElement('section');
  sec.id = 'hMapPanel';
  sec.className = 'panel';
  sec.style.gridColumn = '1 / -1';
  sec.innerHTML = `<div class="panel-h"><b>H · 台灣地圖</b>
      <div class="seg" id="hMapView"><button data-value="map">縣市地圖</button><button data-value="words" title="A 目前分組的族群名稱：字越大成交值越大，紅漲綠跌">關鍵字圖</button></div>
      <span class="muted" id="hMapHint">依公司總部所在縣市彙總全市場普通股（地址來自證交所、櫃買中心公司基本資料）；可拖曳縮放</span>
      <span id="hMapCtl"></span><span id="hWordsCtl" hidden></span><span class="muted" id="hMapNote" style="margin-left:auto"></span><span class="muted" id="hWordsNote" style="margin-left:auto" hidden></span></div>
    <div id="hMap" style="height:620px"></div><div id="hWords" style="height:620px" hidden></div>`;
  $('#historyView').appendChild(sec);
  countyPanel.ctl = mountCountyMap($('#hMap'), { controls: $('#hMapCtl'), note: $('#hMapNote'), prefKey: 'openmarket.history.mapMetric', onStock: openHistCard });
  countyPanel.words = mountWordCloud($('#hWords'), {
    controls: $('#hWordsCtl'),
    note: $('#hWordsNote'),
    prefKey: 'openmarket.history.wordMetric',
    onGroup: (name) => {
      zoomTo(name); // A is sticky at the top: the zoom shows right away
    },
    onStock: openHistCard,
  });
  try {
    if (localStorage.getItem('openmarket.history.hView') === 'words') countyPanel.view = 'words';
  } catch {
    /* default */
  }
  $('#hMapView').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-value]');
    if (!b) return;
    countyPanel.view = b.dataset.value;
    try {
      localStorage.setItem('openmarket.history.hView', countyPanel.view);
    } catch {
      /* ignore */
    }
    renderCountyPanel();
  });
}

function syncCountyView() {
  const words = countyPanel.view === 'words';
  for (const b of document.querySelectorAll('#hMapView button')) b.classList.toggle('on', (b.dataset.value === 'words') === words);
  for (const id of ['hMap', 'hMapCtl', 'hMapNote', 'hMapHint']) $('#' + id).hidden = words;
  for (const id of ['hWords', 'hWordsCtl', 'hWordsNote']) $('#' + id).hidden = !words;
}

/** The map always covers the whole market: under other groupings A holds members only. */
async function renderCountyPanel() {
  if (!countyPanel.ctl || !st.date) return;
  const date = st.date;
  syncCountyView();
  if (countyPanel.view === 'words') {
    // the word cloud follows A's grouping (st.stocks: industry := group name)
    countyPanel.words.render(st.stocks, { caption: date, date });
    return;
  }
  if (st.grouping === 'industry') {
    countyPanel.ctl.render(st.stocks, date);
    return;
  }
  const t = ++countyPanel.ticket;
  let stocks = countyPanel.cache.get(date);
  if (!stocks) {
    try {
      stocks = (await getJson(`/api/history/day/${date}`)).stocks;
    } catch {
      return;
    }
    countyPanel.cache.clear(); // keep one day: the map follows the selected date
    countyPanel.cache.set(date, stocks);
  }
  if (t !== countyPanel.ticket || date !== st.date) return;
  countyPanel.ctl.render(stocks, date);
}

// ---- loading state: a panel keeps its old content while a newer request runs, so dim it -------
/** Mark a panel as loading; the returned function clears it only if `t` is still the latest ticket. */
function panelLoading(sel, key, t) {
  const el = document.querySelector(`#historyView ${sel}`);
  el?.classList.add('loading');
  return () => {
    if (t === tickets[key]) el?.classList.remove('loading');
  };
}

// ---- J: picking a news theme switches to the news grouping and zooms into it ----------------------
function pickNewsTheme(name) {
  stopReplay();
  const changed = st.grouping !== 'news';
  st.grouping = 'news';
  st.zoom = name; // renderChart zooms once the regrouped day has loaded
  idx.picked = [];
  members.group = null;
  savePrefs();
  if (changed) reloadGrouped();
  else renderChart();
  document.querySelector('#historyView .hA').scrollIntoView({ block: 'nearest' });
}

// ---- A/B row height: drag the bottom edge of A or B; double-click restores the default ---------
const ROW_KEY = 'openmarket.history.abHeight';
const ROW_MIN = 260;

function applyRowHeight(px) {
  const view = $('#historyView');
  if (px) view.style.setProperty('--hab-h', `${px}px`);
  else view.style.removeProperty('--hab-h');
}

function setupRowResizer() {
  try {
    const saved = Number(localStorage.getItem(ROW_KEY));
    if (saved >= ROW_MIN) applyRowHeight(saved);
  } catch {
    /* default */
  }
  for (const panel of document.querySelectorAll('#historyView .hA, #historyView .hB')) {
    const grip = document.createElement('div');
    grip.className = 'panel-resizer';
    grip.title = '拖動調整 A、B 的高度（雙擊恢復預設）';
    panel.appendChild(grip);
    grip.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      const startY = e.clientY;
      const startH = panel.getBoundingClientRect().height;
      const max = Math.round(window.innerHeight * 0.9);
      grip.classList.add('dragging');
      document.body.classList.add('resizing-rows');
      let h = startH;
      const move = (ev) => {
        h = Math.round(Math.min(max, Math.max(ROW_MIN, startH + ev.clientY - startY)));
        applyRowHeight(h);
      };
      const up = () => {
        grip.removeEventListener('pointermove', move);
        grip.classList.remove('dragging');
        document.body.classList.remove('resizing-rows');
        try {
          localStorage.setItem(ROW_KEY, String(h));
        } catch {
          /* ignore */
        }
      };
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', up, { once: true });
      grip.addEventListener('pointercancel', up, { once: true });
    });
    grip.addEventListener('dblclick', () => {
      applyRowHeight(null);
      try {
        localStorage.removeItem(ROW_KEY);
      } catch {
        /* ignore */
      }
    });
  }
}

// ---- A/B width ratio: drag the edge between A and B; double-click restores 3:2 -----------------
const COL_KEY = 'openmarket.history.abRatio';
const COL_MIN = 0.3;
const COL_MAX = 0.8;

function applyColRatio(r) {
  const view = $('#historyView');
  if (r) {
    view.style.setProperty('--ha-fr', `${r}fr`);
    view.style.setProperty('--hb-fr', `${1 - r}fr`);
  } else {
    view.style.removeProperty('--ha-fr');
    view.style.removeProperty('--hb-fr');
  }
}

function setupColResizer() {
  try {
    const saved = Number(localStorage.getItem(COL_KEY));
    if (saved >= COL_MIN && saved <= COL_MAX) applyColRatio(saved);
  } catch {
    /* default */
  }
  const a = document.querySelector('#historyView .hA');
  const b = document.querySelector('#historyView .hB');
  const grip = document.createElement('div');
  grip.className = 'panel-resizer-v';
  grip.title = '拖動調整 A、B 的寬度（雙擊恢復預設）';
  a.appendChild(grip);
  grip.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    const left = a.getBoundingClientRect().left;
    const total = b.getBoundingClientRect().right - left; // both columns incl. the gap
    grip.classList.add('dragging');
    document.body.classList.add('resizing-cols');
    let r = a.getBoundingClientRect().width / total;
    const move = (ev) => {
      r = Math.min(COL_MAX, Math.max(COL_MIN, (ev.clientX - left) / total));
      applyColRatio(Math.round(r * 1000) / 1000);
    };
    const up = () => {
      grip.removeEventListener('pointermove', move);
      grip.classList.remove('dragging');
      document.body.classList.remove('resizing-cols');
      try {
        localStorage.setItem(COL_KEY, String(Math.round(r * 1000) / 1000));
      } catch {
        /* ignore */
      }
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up, { once: true });
    grip.addEventListener('pointercancel', up, { once: true });
  });
  grip.addEventListener('dblclick', () => {
    applyColRatio(null);
    try {
      localStorage.removeItem(COL_KEY);
    } catch {
      /* ignore */
    }
  });
}

// ---- money flow columns in B (spec 2026-10-23-money-flow-design.md) ----------------------------
const IGNITE_Z = 2;
const flowState = { ticket: 0, igniteOnly: false };

function flowCells(g) {
  if (g.z20 === undefined) return '<td class="muted">…</td>'.repeat(5);
  const z = g.z20 === null ? '<td class="muted">—</td>' : `<td class="c" style="background:${colorFor(g.z20, 3)}" title="5 日 Z ${g.z5 === null ? '—' : g.z5.toFixed(1)}，比重 ${g.share.toFixed(2)}%">${g.z20.toFixed(1)}</td>`;
  const mf = g.mf === null ? '<td class="muted">—</td>' : `<td class="c" style="background:${colorFor(g.mf, 60)}">${g.mf.toFixed(0)}</td>`;
  const tr = g.turnover_rate === null ? '<td class="muted">—</td>' : `<td>${g.turnover_rate.toFixed(2)}%</td>`;
  const inst =
    g.inst_net === null
      ? '<td class="muted" title="這天還沒有法人資料（盤後公布，或背景回補中）">—</td>'
      : `<td class="c" style="background:${colorFor(g.inst_ratio, 0.3)}" title="佔族群市值 ${g.inst_ratio === null ? '—' : g.inst_ratio.toFixed(3) + '%'}">${(g.inst_net / 1e8).toFixed(1)}</td>`;
  const r = g.r20 === null || g.r20 === undefined
    ? '<td class="muted">—</td>'
    : `<td class="c" style="background:${colorFor((g.r20 - 0.3) * 100, 50)}" title="${g.r20 > 0.7 ? '群體圍攻' : g.r20 < 0.3 ? '單兵作戰' : '中度連動'}">${g.r20.toFixed(2)}</td>`;
  return z + mf + tr + inst + r;
}

async function loadFlows() {
  const t = ++flowState.ticket;
  ensureIgniteBox();
  let data;
  try {
    data = await getJson(`/api/history/flows?date=${st.date}&grouping=${st.grouping}`);
  } catch {
    return;
  }
  if (t !== flowState.ticket) return;
  const by = data.groups || {};
  for (const g of st.groups) {
    const f = by[g.name];
    Object.assign(g, f ? { ...f } : { share: null, z5: null, z20: null, mf: null, turnover_rate: null, inst_net: null, inst_ratio: null, r20: null });
  }
  renderGroups();
}

function ensureIgniteBox() {
  if ($('#hIgnite')) return;
  const label = document.createElement('label');
  label.className = 'chk';
  label.title = '只顯示成交比重暴增（比重 Z ≥ 2）的族群：冷門題材突然被資金點火';
  label.innerHTML = '<input type="checkbox" id="hIgnite"> 只看🔥點火';
  ($('#hGroupFilter') || $('#hBDate')).after(label);
  $('#hIgnite').addEventListener('change', (e) => {
    flowState.igniteOnly = e.target.checked;
    renderGroups();
  });
}
