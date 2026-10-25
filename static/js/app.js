// DOM, events, ECharts binding and polling. Data shaping lives in transform.js.
import {
  ROOT_ID,
  aggregateIndustries,
  breadthColor,
  breadthShare,
  buildTreemapData,
  buildTreemapOption,
  colorFor,
  colorLimit,
  escapeHtml,
  filterStocks,
  fmtCap,
  fmtChange,
  fmtNum,
  fmtPct,
  fmtPrice,
  fmtTurnover,
  sortRows,
  watchRows,
} from './transform.js';
import { showHistory } from './history.js';
import { mountCountyMap } from './countymap.js';
import { mountWordCloud } from './wordcloud.js';
import { mountLiveAiButton } from './ainews.js';
import { GROUP_LABELS, MIN_FILTERS, TODAY_SCALES, breadthBarHtml, findStock, marketBreadth, regroupStocks, withRelVolume } from './transform.js';
import { addToGroup, getGroups, loadGroups, onGroupsChange, openGroupPanel, setStockNames } from './groups.js';

const SNAPSHOT_POLL_MS = 10_000;
// bonus-11: 20-day industry returns for the live matrix (declared before main() runs)
const livePeriods = { date: null, byGroup: {}, loading: false };
// bonus-17: live grouping switch state (declared before main() runs)
const liveGrouping = { by: 'industry', maps: {}, loading: {} };
// bonus-24: relative-volume baseline (declared before main() runs)
const liveRvol = { avg: null, day: null, loading: false, failedAt: 0 };
const RVOL_RETRY_MS = 5 * 60_000;
// bonus-23: declared up here because main() renders before the module finishes evaluating
const liveSearch = { shown: [], listFor: null };
// money flow: share baselines per grouping from the history store (declared before main() runs)
const liveFlow = { baselines: {}, loading: {} };
// ③: main area shows the heatmap, the county money map or the group word cloud (declared before main() runs)
const liveMap = { view: 'heat', ctl: null, words: null };
const WORD_GROUPING_LABELS = { industry: '官方產業', custom: '自訂族群', concept: '概念股', chain: '細產業', news: '新聞族群' };
let liveAi = null; // ⑤ AI 解讀 button
try {
  const view = localStorage.getItem('openmarket.live.view');
  if (view === 'map' || view === 'words') liveMap.view = view;
} catch {
  /* default */
}
try {
  const saved = JSON.parse(localStorage.getItem('openmarket.live.grouping') || '""');
  if (['industry', 'custom', 'concept', 'chain', 'news'].includes(saved)) liveGrouping.by = saved;
} catch {
  /* default */
}
const PREFS_KEY = 'openmarket.prefs.v1';

const $ = (sel) => document.querySelector(sel);

const state = {
  snapshot: null,
  etag: null,
  watchCodes: [],
  settings: { poll_seconds: 60 },
  // persisted preferences
  filters: { market: 'all', includeEtf: false, watchOnly: false },
  view: { sizeBy: 'cap', colorBy: 'today' },
  tab: 'industry',
  mode: 'live',
  sort: { industry: { key: 'change_pct', dir: 'desc' }, watch: { key: null, dir: 'desc' } },
  // transient
  zoomIndustry: null,
  hoverIndustry: null,
  cardCode: null,
  cardLast: null,
  tree: null,
};

// ---- preferences ----------------------------------------------------------

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
    if (p.filters) Object.assign(state.filters, p.filters);
    if (p.view) Object.assign(state.view, p.view);
    if (p.tab === 'industry' || p.tab === 'watch') state.tab = p.tab;
    if (p.mode === 'live' || p.mode === 'history') state.mode = p.mode;
    if (p.sort) Object.assign(state.sort, p.sort);
  } catch {
    /* storage unavailable or corrupt: defaults */
  }
}

function savePrefs() {
  try {
    const { filters, view, tab, sort, mode } = state;
    localStorage.setItem(PREFS_KEY, JSON.stringify({ filters, view, tab, sort, mode }));
  } catch {
    /* ignore */
  }
}

// ---- API ------------------------------------------------------------------

async function api(path, options = {}) {
  const resp = await fetch(path, { cache: 'no-store', ...options });
  let body = null;
  if (resp.status !== 304 && resp.status !== 204) {
    try {
      body = await resp.json();
    } catch {
      body = null;
    }
  }
  return { resp, body };
}

async function pollSnapshot() {
  try {
    const headers = state.etag ? { 'If-None-Match': state.etag } : {};
    const { resp, body } = await api('/api/snapshot', { headers });
    if (resp.status === 304) {
      renderStatus(); // clears a previous connection error
      return;
    }
    if (!resp.ok || !body) throw new Error(`HTTP ${resp.status}`);
    state.etag = resp.headers.get('ETag');
    state.snapshot = body;
    setStockNames(body.stocks);
    render();
  } catch (err) {
    renderStatus(`無法連線到本機伺服器（${err.message}）`);
  }
}

let pollTimer = null;
function schedulePoll(ms = SNAPSHOT_POLL_MS) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    await pollSnapshot();
    schedulePoll();
  }, ms);
}

async function loadWatchlist() {
  const { resp, body } = await api('/api/watchlist');
  if (resp.ok && body) state.watchCodes = body.codes;
}

async function setWatched(code, watched) {
  let resp, body;
  try {
    ({ resp, body } = await api(`/api/watchlist/${encodeURIComponent(code)}`, { method: watched ? 'PUT' : 'DELETE' }));
  } catch {
    toast('自選清單更新失敗（無法連線）');
    return;
  }
  if (!resp.ok || !body) {
    toast('自選清單更新失敗');
    return;
  }
  state.watchCodes = body.codes;
  render();
}

// ---- chart ----------------------------------------------------------------

let chart = null;

function initChart() {
  chart = echarts.init($('#chart'), null, { renderer: 'canvas' });
  chart.on('click', (p) => {
    const d = p.data || {};
    if (d.stock) openCard(d.code);
    else if (d.industry) zoomTo(d.name);
  });
  new ResizeObserver(() => chart.resize()).observe($('#chart'));
}

function hasIndustry(name) {
  return !!state.tree && state.tree.data.some((d) => d.name === name);
}

function zoomTo(name) {
  if (!hasIndustry(name)) return;
  state.zoomIndustry = name;
  chart.dispatchAction({ type: 'treemapRootToNode', seriesId: 'map', targetNodeId: 'ind:' + name });
  reapplyHighlight();
  renderCrumb();
  syncMatrixZoom();
}

function zoomOut() {
  state.zoomIndustry = null;
  chart.dispatchAction({ type: 'treemapRootToNode', seriesId: 'map', targetNodeId: ROOT_ID });
  reapplyHighlight();
  renderCrumb();
  syncMatrixZoom();
}

/** Re-rooting or re-rendering rebuilds the treemap elements and drops their emphasis state. */
function reapplyHighlight() {
  if (state.hoverIndustry) chart.dispatchAction({ type: 'highlight', seriesId: 'map', name: state.hoverIndustry });
}

function syncMatrixZoom() {
  for (const row of $('#matrix').tBodies[0].rows) row.classList.toggle('hl', row.dataset.industry === state.zoomIndustry);
}

function highlightIndustry(name) {
  if (state.hoverIndustry && state.hoverIndustry !== name) {
    chart.dispatchAction({ type: 'downplay', seriesId: 'map', name: state.hoverIndustry });
  }
  state.hoverIndustry = name;
  if (name) chart.dispatchAction({ type: 'highlight', seriesId: 'map', name });
}

function renderChart(stocks) {
  renderGroupLabelSelect(); // ①
  state.tree = buildTreemapData(stocks, { ...state.view, stocksOnly: liveGrouping.by === 'industry' });
  // setOption always resets the treemap view root: zoom state is owned here and re-applied.
  chart.setOption(buildTreemapOption(state.tree), { notMerge: true });
  if (state.zoomIndustry && hasIndustry(state.zoomIndustry)) {
    chart.dispatchAction({ type: 'treemapRootToNode', seriesId: 'map', targetNodeId: 'ind:' + state.zoomIndustry });
  } else {
    state.zoomIndustry = null;
  }
  reapplyHighlight();
}

// ---- rendering --------------------------------------------------------------

function render() {
  const snap = state.snapshot;
  renderRvolButton(); // bonus-24: before renderControls() so a saved 量比 is highlighted
  renderControls();
  renderStatus();
  $('#watchCount').textContent = `(${state.watchCodes.length})`;
  renderLiveMinSelect(); // bonus-22
  const filtered = snap ? filterStocks(snap.stocks, state.filters, state.watchCodes) : [];
  const stocks = applyLiveGrouping(applyRelVolume(filtered)); // bonus-17: custom / concept regrouping; bonus-24

  renderChart(stocks);
  const empty = emptyMessage(snap, stocks);
  $('#empty').hidden = !empty;
  $('#empty').textContent = empty || '';
  renderCrumb();
  renderNote();
  renderLegend();
  renderMatrix(withFlows(withPeriods(aggregateIndustries(stocks, { stocksOnly: liveGrouping.by === 'industry' })), stocks, snap)); // bonus-11: 20-day column; ①; money flow
  renderLiveBreadth(filtered); // bonus-05 (each stock once, whatever the grouping)
  renderLiveSearch(snap, stocks); // bonus-23
  renderLiveMap(filtered, stocks); // ③ map: each stock once; word cloud: the current grouping
  liveAi ??= mountLiveAiButton($('#liveControls'), () => !!state.snapshot?.market_open); // ⑤
  liveAi.sync();
  renderWatch(snap ? snap.stocks : []);
  renderCard();
}

function emptyMessage(snap, stocks) {
  if (!snap || snap.status === 'loading') return '載入中…首次啟動需要抓取報價與產業分類資料。';
  if (!snap.stocks.length) return `目前沒有資料：${snap.message || '報價抓取失敗'}`;
  if (!stocks.length) {
    if (liveGrouping.by === 'custom') return '還沒有自訂族群（或族群內沒有符合篩選的股票）。到「歷史」頁按「管理族群」建立，或在個股資訊卡加入。'; // bonus-17
    if (state.filters.min && MIN_FILTERS[state.filters.min]) return `目前篩選條件（${MIN_FILTERS[state.filters.min].label}）下沒有股票。`; // bonus-22
    if (state.filters.watchOnly) return '自選清單是空的（或不在目前的市場篩選中）。點個股資訊卡的「加入自選」來新增。';
    return '目前篩選條件下沒有股票。';
  }
  if (!state.tree?.data.length) return '目前篩選條件下的股票都沒有可用的格子大小資料，請切換「成交值」或「市值」。';
  return '';
}

function renderControls() {
  for (const seg of document.querySelectorAll('.seg[data-group]')) {
    const group = seg.dataset.group;
    const current = String(group in state.filters ? state.filters[group] : state.view[group]);
    for (const b of seg.querySelectorAll('button')) {
      const on = b.dataset.value === current;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    }
  }
  const etf = $('#includeEtf');
  etf.checked = state.filters.includeEtf;
  etf.disabled = state.filters.watchOnly;
  etf.closest('.chk').classList.toggle('disabled', state.filters.watchOnly);
  etf.closest('.chk').title = state.filters.watchOnly ? '只看自選時會顯示所有自選股（含 ETF）' : '';
  for (const b of document.querySelectorAll('.tabs button')) b.classList.toggle('on', b.dataset.tab === state.tab);
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === state.tab));
  $('#matrix').hidden = state.tab !== 'industry';
  $('#watch').hidden = state.tab !== 'watch';
  $('#watchEmpty').hidden = !(state.tab === 'watch' && state.watchCodes.length === 0);
}

function renderStatus(connectionError) {
  const el = $('#status');
  const snap = state.snapshot;
  let cls = 'loading';
  let text = '載入中…';
  let title = '';
  if (connectionError) {
    cls = 'error';
    text = '伺服器未連線';
    title = connectionError;
  } else if (snap) {
    cls = snap.status;
    const time = snap.as_of ? new Date(snap.as_of).toLocaleTimeString('zh-TW', { hour12: false }) : '—';
    const session = snap.market_open ? `盤中 · 每 ${state.settings.poll_seconds} 秒` : '已收盤';
    text = { ok: `${time} 更新 · ${session}`, stale: `${time} 的資料（更新失敗）· ${session}`, error: '資料抓取失敗', loading: '載入中…' }[snap.status];
    title = snap.message || '';
  }
  el.className = `status ${cls}`;
  $('#statusText').textContent = text;
  el.title = title;
}

function renderCrumb() {
  const el = $('#crumb');
  if (state.zoomIndustry) {
    el.innerHTML = `<button type="button" id="crumbRoot">全市場</button> › <b>${escapeHtml(state.zoomIndustry)}</b>`;
    $('#crumbRoot').addEventListener('click', zoomOut);
  } else {
    el.innerHTML = '<b>全市場</b> <span class="muted">（點產業標題放大）</span>';
  }
}

function renderNote() {
  const t = state.tree;
  const notes = [];
  if (t) {
    if (t.hiddenNoSize) notes.push(`${t.hiddenNoSize} 檔${state.view.sizeBy === 'cap' || state.view.sizeBy === 'capped' ? '缺市值' : '無成交值'}未顯示`);
    if (t.hiddenEtf) notes.push(`市值模式不顯示 ETF（${t.hiddenEtf} 檔，無股本資料），可切換「成交值」`);
  }
  const snap = state.snapshot;
  if (snap && snap.message && snap.status === 'ok') notes.push(snap.message);
  $('#note').textContent = notes.join('　·　');
}

function renderLegend() {
  renderScaleSelect(); // bonus-25
  const limit = colorLimit(state.view.colorBy, state.view.scale);
  const rvol = state.view.colorBy === 'rvol'; // bonus-24: log2 scale, ±100 = ½x … 2x
  $('#legendMin').textContent = rvol ? '½x' : `-${limit}%`;
  $('#legendMax').textContent = rvol ? '2x' : `+${limit}%`;
}

function setSortHeaders(table, sort) {
  for (const th of table.querySelectorAll('th[data-sort]')) {
    if (th.dataset.sort === sort.key) th.setAttribute('aria-sort', sort.dir === 'asc' ? 'ascending' : 'descending');
    else th.removeAttribute('aria-sort');
  }
}

function pctCell(v, limit) {
  return `<td class="c" style="background:${colorFor(v, limit)}">${fmtPct(v)}</td>`;
}

function breadthCell(up, down) {
  const share = breadthShare(up, down);
  const title = share === null ? '' : `上漲家數佔 ${share.toFixed(0)}%`;
  return `<td class="c" style="background:${breadthColor(up, down)}" title="${title}">${up}/${down}</td>`;
}

function renderMatrix(rows) {
  const table = $('#matrix');
  const sort = state.sort.industry;
  setSortHeaders(table, sort);
  const sorted = sortRows(rows, sort.key, sort.dir);
  table.tBodies[0].innerHTML = sorted
    .map(
      (r) => `<tr data-industry="${escapeHtml(r.name)}"${r.name === state.zoomIndustry ? ' class="hl"' : ''}>
        <td class="l"><span class="clip" title="${escapeHtml(r.name)}">${r.z20 >= 2 ? '<span title="成交比重暴增（Z ≥ 2）">🔥</span>' : ''}${escapeHtml(r.name)}</span></td>
        ${pctCell(r.change_pct, colorLimit('today', state.view.scale))}
        ${pctCell(r.week_pct, colorLimit('week'))}
        ${pctCell(livePeriods.byGroup[r.name]?.perf_20 ?? null, 15)}
        <td>${r.amplitude_pct === null ? '—' : r.amplitude_pct.toFixed(2) + '%'}</td>
        <td>${r.turnover_share === null ? '—' : r.turnover_share.toFixed(1) + '%'}</td>
        ${volRatioCell(r.vol_ratio)}
        ${ratioCell(r.strong_ratio, r.strong_count, 30)}
        ${ratioCell(r.limit_ratio, r.limit_up, 20)}
        ${liveFlowCells(r)}
        ${breadthCell(r.up_count, r.down_count)}
      </tr>`,
    )
    .join('');
}

function renderWatch(allStocks) {
  const table = $('#watch');
  const sort = state.sort.watch;
  setSortHeaders(table, sort);
  let rows = watchRows(allStocks, state.watchCodes);
  if (sort.key) rows = sortRows(rows, sort.key, sort.dir);
  table.tBodies[0].innerHTML = rows
    .map((s) =>
      s.missing
        ? `<tr data-code="${escapeHtml(s.code)}"><td class="l">${escapeHtml(s.code)}</td><td class="l muted" colspan="5">目前無報價</td>
           <td><button class="rm" data-remove="${escapeHtml(s.code)}" title="移除">✕</button></td></tr>`
        : `<tr data-code="${escapeHtml(s.code)}">
        <td class="l">${escapeHtml(s.code)}</td>
        <td class="l">${escapeHtml(s.name)}</td>
        <td>${fmtPrice(s.price)}</td>
        ${pctCell(s.change_pct, colorLimit('today', state.view.scale))}
        ${pctCell(s.week_pct, colorLimit('week'))}
        <td>${fmtTurnover(s.turnover_100m)}</td>
        <td><button class="rm" data-remove="${escapeHtml(s.code)}" title="移除">✕</button></td>
      </tr>`,
    )
    .join('');
}

// ---- info card ----------------------------------------------------------------

function openCard(code) {
  state.cardCode = code;
  state.cardLast = null;
  renderCard();
}

function closeCard() {
  delete $('#card').dataset.actionsSig;
  state.cardCode = null;
  state.cardLast = null;
  renderCard();
}

function groupSelect(code) {
  const groups = getGroups();
  const member = groups.filter((g) => g.codes.includes(code)).map((g) => g.name);
  const options = groups
    .filter((g) => !g.codes.includes(code))
    .map((g) => `<option value="${escapeHtml(g.name)}">${escapeHtml(g.name)}</option>`)
    .join('');
  const label = member.length ? `已在：${member.map(escapeHtml).join('、')}` : '加入自訂族群…';
  return `<select class="field" id="cardGroup" aria-label="加入自訂族群"><option value="">${label}</option>${options}<option value="__manage">管理族群…</option></select>`;
}

function renderCard() {
  const el = $('#card');
  if (!state.cardCode) {
    el.hidden = true;
    return;
  }
  const fresh = state.snapshot?.stocks.find((s) => s.code === state.cardCode);
  if (fresh) state.cardLast = fresh;
  const s = state.cardLast;
  if (!s) {
    el.hidden = true;
    state.cardCode = null;
    return;
  }
  const watched = state.watchCodes.includes(s.code);
  const dir = s.change > 0 ? 'up' : s.change < 0 ? 'down' : '';
  const dataHtml = `
    <h3>${escapeHtml(s.code)} ${escapeHtml(s.name)} <span class="sub">${escapeHtml(s.industry)} · ${escapeHtml(s.market)}</span></h3>
    <div class="px"><b class="${dir}">${fmtPrice(s.price)}</b><span class="${dir}">${fmtChange(s.change)}（${fmtPct(s.change_pct)}）</span></div>
    <table>
      <tr><td>開盤</td><td>${fmtPrice(s.open)}</td><td>昨收</td><td>${fmtPrice(s.prev_close)}</td></tr>
      <tr><td>最高</td><td>${fmtPrice(s.high)}</td><td>最低</td><td>${fmtPrice(s.low)}</td></tr>
      <tr><td>振幅</td><td>${s.amplitude_pct === null ? '—' : s.amplitude_pct.toFixed(2) + '%'}</td><td>週漲跌</td><td>${fmtPct(s.week_pct)}</td></tr>
      <tr><td>成交量</td><td>${fmtNum(s.volume_lots, 0)} 張</td><td>成交值</td><td>${fmtTurnover(s.turnover_100m)}</td></tr>
      <tr><td>市值</td><td colspan="3" style="text-align:right">${fmtCap(s.market_cap)}</td></tr>
    </table>
    ${fresh ? '' : '<div class="stale-note">資料未更新（此代號不在最新報價中）</div>'}`;
  // The action part (buttons, group dropdown, membership line) is rebuilt only when it changes,
  // so a snapshot poll does not close an open dropdown or re-fetch the membership line.
  const actionsSig = JSON.stringify([s.code, watched, getGroups()]);
  if (el.dataset.actionsSig !== actionsSig || !$('#cardData')) {
    el.dataset.actionsSig = actionsSig;
    el.innerHTML = `<div id="cardData"></div>
    <div class="btns">
      <button type="button" class="${watched ? '' : 'primary'}" id="cardWatch">${watched ? '★ 移除自選' : '☆ 加入自選'}</button>
      <a href="https://histock.tw/stock/${encodeURIComponent(s.code)}" target="_blank" rel="noopener noreferrer">HiStock ↗</a>
      <button type="button" id="cardClose" aria-label="關閉">✕</button>
    </div>
    ${groupSelect(s.code)}
    <div class="memb" data-memb="${escapeHtml(s.code)}"></div>`;
    $('#cardWatch').addEventListener('click', () => setWatched(s.code, !watched));
    $('#cardClose').addEventListener('click', closeCard);
    fillMembership(el);
    $('#cardGroup').addEventListener('change', async (e) => {
      const value = e.target.value;
      e.target.value = '';
      if (value === '__manage') return openGroupPanel();
      if (!value) return;
      try {
        await addToGroup(value, s.code);
        toast(`已加入族群「${value}」`);
      } catch (err) {
        toast(`加入失敗：${err.message}`);
      }
    });
  }
  $('#cardData').innerHTML = dataHtml;
  el.hidden = false;
}

// ---- toast ------------------------------------------------------------------

let toastTimer = null;
function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3000);
}

// ---- events -----------------------------------------------------------------

function bindEvents() {
  for (const seg of document.querySelectorAll('.seg[data-group]')) {
    seg.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      const group = seg.dataset.group;
      if (group in state.filters) {
        state.filters[group] = group === 'watchOnly' ? b.dataset.value === 'true' : b.dataset.value;
      } else {
        state.view[group] = b.dataset.value;
      }
      savePrefs();
      render();
    });
  }
  $('#includeEtf').addEventListener('change', (e) => {
    state.filters.includeEtf = e.target.checked;
    savePrefs();
    render();
  });
  for (const b of document.querySelectorAll('.tabs button')) {
    b.addEventListener('click', () => {
      state.tab = b.dataset.tab;
      savePrefs();
      renderControls();
    });
  }
  for (const [tableId, sortKey] of [['#matrix', 'industry'], ['#watch', 'watch']]) {
    const table = $(tableId);
    table.tHead.addEventListener('click', (e) => {
      const th = e.target.closest('th[data-sort]');
      if (!th) return;
      const sort = state.sort[sortKey];
      const key = th.dataset.sort;
      const textual = key === 'name' || key === 'code';
      if (sort.key === key) sort.dir = sort.dir === 'asc' ? 'desc' : 'asc';
      else Object.assign(sort, { key, dir: textual ? 'asc' : 'desc' });
      savePrefs();
      render();
    });
  }
  const matrixBody = $('#matrix').tBodies[0];
  matrixBody.addEventListener('mouseover', (e) => {
    const tr = e.target.closest('tr[data-industry]');
    if (tr && tr.dataset.industry !== state.hoverIndustry) highlightIndustry(tr.dataset.industry);
  });
  matrixBody.addEventListener('mouseleave', () => highlightIndustry(null));
  matrixBody.addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-industry]');
    if (!tr) return;
    zoomTo(tr.dataset.industry);
  });
  $('#watch').tBodies[0].addEventListener('click', (e) => {
    const rm = e.target.closest('[data-remove]');
    if (rm) {
      e.stopPropagation();
      setWatched(rm.dataset.remove, false);
      return;
    }
    const tr = e.target.closest('tr[data-code]');
    if (tr && state.snapshot?.stocks.some((s) => s.code === tr.dataset.code)) openCard(tr.dataset.code);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || state.mode !== 'live') return;
    if (!$('#settings').hidden) $('#settings').hidden = true;
    else if (state.cardCode) closeCard();
    else if (state.zoomIndustry) zoomOut();
  });

  // refresh
  $('#refreshBtn').addEventListener('click', async () => {
    const btn = $('#refreshBtn');
    btn.disabled = true;
    try {
      const { resp, body } = await api('/api/refresh', { method: 'POST' });
      if (resp.status === 429) toast(`${body?.retry_after ?? 30} 秒後可再更新`);
      else if (resp.ok) {
        toast('更新中…');
        schedulePoll(2500);
      } else toast('更新失敗');
    } catch {
      toast('更新失敗（無法連線）');
    } finally {
      setTimeout(() => (btn.disabled = false), 1500);
    }
  });

  // settings
  $('#settingsBtn').addEventListener('click', () => {
    const pop = $('#settings');
    pop.hidden = !pop.hidden;
    if (!pop.hidden) {
      $('#pollSeconds').value = state.settings.poll_seconds;
      $('#settingsErr').textContent = '';
      $('#pollSeconds').focus();
    }
  });
  $('#settingsCancel').addEventListener('click', () => ($('#settings').hidden = true));
  const save = async () => {
    const value = Number($('#pollSeconds').value);
    if (!Number.isInteger(value) || value < 30 || value > 600) {
      $('#settingsErr').textContent = '請輸入 30 到 600 之間的整數';
      return;
    }
    let resp, body;
    try {
      ({ resp, body } = await api('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ poll_seconds: value }),
      }));
    } catch {
      $('#settingsErr').textContent = '儲存失敗（無法連線）';
      return;
    }
    if (!resp.ok || !body) {
      $('#settingsErr').textContent = '儲存失敗';
      return;
    }
    state.settings = body;
    $('#settings').hidden = true;
    renderStatus();
    toast(`盤中每 ${body.poll_seconds} 秒更新`);
  };
  $('#settingsSave').addEventListener('click', save);
  $('#pollSeconds').addEventListener('keydown', (e) => e.key === 'Enter' && save());
}

function setMode(mode) {
  state.mode = mode;
  savePrefs();
  const live = mode === 'live';
  $('#liveView').hidden = !live;
  $('#liveControls').hidden = !live;
  $('#historyView').hidden = live;
  $('#histControls').hidden = live;
  $('#historyTab2').hidden = live || $('#historyView').dataset.tab !== '2'; // 歷史 分頁2／3 (history.js)
  $('#historyTab3').hidden = live || $('#historyView').dataset.tab !== '3';
  $('#historyTab4').hidden = live || $('#historyView').dataset.tab !== '4';
  for (const b of $('#modeSwitch').querySelectorAll('button')) {
    b.classList.toggle('on', b.dataset.mode === mode);
    b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
  }
  if (live) {
    chart.resize();
    if (location.hash !== '#live') history.replaceState(null, '', '#live'); // bonus-12
  }
  else showHistory();
}

// ---- boot -------------------------------------------------------------------

async function main() {
  loadPrefs();
  if (typeof echarts === 'undefined') {
    $('#empty').hidden = false;
    $('#empty').textContent = '無法載入 ECharts（需要網路連線到 cdnjs.cloudflare.com）。';
    return;
  }
  initChart();
  bindEvents();
  onGroupsChange(() => {
    renderCard();
    if (liveGrouping.by === 'custom') loadLiveMapping('custom', true); // bonus-17
  });
  loadGroups().catch(() => {});
  $('#modeSwitch').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-mode]');
    if (b) setMode(b.dataset.mode);
  });
  render();
  if (location.hash.startsWith('#history')) state.mode = 'history'; // bonus-12: bookmarked view
  else if (location.hash === '#live') state.mode = 'live';
  setMode(state.mode);
  window.addEventListener('hashchange', () => {
    // bonus-12: a pasted bookmark switches mode; history.js then applies its date/grouping
    const want = location.hash.startsWith('#history') ? 'history' : location.hash === '#live' ? 'live' : null;
    if (want && want !== state.mode) setMode(want);
  });
  try {
    const [{ body: settings }] = await Promise.all([api('/api/settings'), loadWatchlist()]);
    if (settings) state.settings = settings;
  } catch {
    /* rendered as connection error by pollSnapshot */
  }
  await pollSnapshot();
  schedulePoll();
}

main();

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

// ---- bonus-05: market breadth bar in the live top bar ----
function renderLiveBreadth(stocks) {
  let el = $('#mbarLive');
  if (!el) {
    el = document.createElement('span');
    el.id = 'mbarLive';
    $('#status').before(el);
  }
  el.innerHTML = breadthBarHtml(marketBreadth(stocks));
}

// ---- bonus-11: 20-day industry return in the live matrix (from stored history) ----

function withPeriods(rows) {
  ensureLivePeriods();
  if (liveGrouping.by !== 'industry') return rows.map((r) => ({ ...r, perf_20: null, vol_ratio: null })); // industry-only figures
  return rows.map((r) => ({ ...r, perf_20: livePeriods.byGroup[r.name]?.perf_20 ?? null, vol_ratio: volRatio(r) }));
}

async function ensureLivePeriods() {
  if (livePeriods.loading || Date.now() - (livePeriods.checkedAt || 0) < 10 * 60 * 1000) return;
  livePeriods.loading = true;
  livePeriods.checkedAt = Date.now();
  try {
    const days = await fetch('/api/history/days', { cache: 'no-store' }).then((r) => r.json());
    const latest = days.days.find((d) => d.status === 'ok')?.date;
    if (latest && latest !== livePeriods.date) {
      const data = await fetch(`/api/history/periods?date=${latest}&grouping=industry`, { cache: 'no-store' }).then((r) => r.json());
      const turnover = await fetch('/api/history/turnover-avg?days=20', { cache: 'no-store' }).then((r) => r.json()); // bonus-16
      livePeriods.byGroup = data.groups || {};
      livePeriods.turnoverAvg = turnover.avg || {};
      livePeriods.date = latest; // only after both succeeded, so a failure is retried
      render();
    }
  } catch {
    /* history not available yet: column stays empty */
  } finally {
    livePeriods.loading = false;
  }
}

// ---- bonus-16: 量能 — today's industry turnover vs its 20-day daily average ---------------

const SESSION_MINUTES = 270; // 09:00–13:30

/** Share of the session elapsed at the snapshot time (1 after the close); intraday turnover is
 * compared with the same share of a full day's average, the usual intraday relative-volume rule. */
function sessionFraction() {
  const snap = state.snapshot;
  if (!snap?.market_open || !snap.as_of) return 1;
  const t = new Date(snap.as_of);
  const taipei = new Date(t.getTime() + (t.getTimezoneOffset() + 480) * 60000);
  const minutes = (taipei.getHours() - 9) * 60 + taipei.getMinutes();
  return Math.min(Math.max(minutes / SESSION_MINUTES, 0.05), 1);
}

function volRatio(r) {
  const avg = livePeriods.turnoverAvg?.[r.name];
  if (!avg || state.filters.watchOnly) return null;
  const markets = state.filters.market === 'all' ? Object.keys(avg) : [state.filters.market];
  const base = markets.reduce((a, m) => a + (avg[m] || 0), 0) * sessionFraction();
  return base > 0 ? (r.turnover_100m * 1e8) / base : null;
}

function volRatioCell(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '<td class="muted">—</td>';
  return `<td class="c" style="background:${colorFor((v - 1) * 50, 50)}">${v.toFixed(1)}x</td>`;
}

// ---- bonus-17: live heatmap grouping switch (官方產業／自訂族群／概念股) ----------------------


function applyLiveGrouping(stocks) {
  renderLiveGroupingSeg();
  if (liveGrouping.by === 'industry') return stocks;
  const map = liveGrouping.maps[liveGrouping.by];
  if (!map) {
    loadLiveMapping(liveGrouping.by);
    return stocks; // official industries until the mapping arrives
  }
  return regroupStocks(stocks, map);
}

async function loadLiveMapping(name, force = false) {
  if (liveGrouping.loading[name]) {
    if (force) liveGrouping.pending = { ...(liveGrouping.pending || {}), [name]: true };
    return;
  }
  if (liveGrouping.maps[name] && !force) return;
  liveGrouping.loading[name] = true;
  try {
    const resp = await fetch(`/api/groupings/${name}`, { cache: 'no-store' });
    if (!resp.ok) throw new Error(resp.status === 404 ? `${{ chain: '細產業', news: '新聞族群', concept: '概念股' }[name] || name}分類尚未載入完成` : `HTTP ${resp.status}`);
    liveGrouping.maps[name] = (await resp.json()).mapping;
    render();
  } catch (err) {
    if (liveGrouping.by === name) {
      toast(`無法使用此分組：${err.message}`);
      setLiveGrouping('industry', { persist: false }); // keep the saved choice for next time
    }
  } finally {
    liveGrouping.loading[name] = false;
    if (liveGrouping.pending?.[name]) {
      liveGrouping.pending[name] = false;
      loadLiveMapping(name, true);
    }
  }
}

function setLiveGrouping(by, { persist = true } = {}) {
  liveGrouping.by = by;
  state.zoomIndustry = null; // names differ between groupings
  if (persist) {
    try {
      localStorage.setItem('openmarket.live.grouping', JSON.stringify(by));
    } catch {
      /* ignore */
    }
  }
  render();
}

function renderLiveGroupingSeg() {
  let seg = $('#liveGrouping');
  if (!seg) {
    seg = document.createElement('div');
    seg.className = 'seg';
    seg.id = 'liveGrouping';
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', '族群分類');
    seg.innerHTML = '<button data-value="industry">官方產業</button><button data-value="custom">自訂族群</button><button data-value="concept">概念股</button><button data-value="chain" title="櫃買中心產業價值鏈平台的細分類">細產業</button><button data-value="news" title="近 3 個交易日新聞最常提到的族群（每日更新）">新聞族群</button>';
    $('#liveControls .seg[data-group="market"]').before(seg);
    seg.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-value]');
      if (b && b.dataset.value !== liveGrouping.by) setLiveGrouping(b.dataset.value);
    });
  }
  for (const b of seg.querySelectorAll('button')) {
    b.classList.toggle('on', b.dataset.value === liveGrouping.by);
    b.setAttribute('aria-pressed', String(b.dataset.value === liveGrouping.by));
  }
}

// ---- bonus-21: fullscreen button for the live heatmap --------------------------------
(function addLiveFullscreen() {
  const wrap = document.querySelector('#liveView .chart-wrap');
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

// ---- bonus-22: minimum turnover / market-cap filter (live) ----------------------------
function renderLiveMinSelect() {
  let sel = $('#liveMin');
  if (!sel) {
    sel = document.createElement('select');
    sel.id = 'liveMin';
    sel.className = 'field';
    sel.title = '只顯示成交值或市值達門檻的股票（熱力圖、產業矩陣與漲跌家數列）';
    sel.innerHTML = Object.entries(MIN_FILTERS).map(([k, f]) => `<option value="${k}">${f.label}</option>`).join('');
    $('#includeEtf').closest('.chk').after(sel);
    sel.addEventListener('change', () => {
      state.filters.min = sel.value;
      savePrefs();
      render();
    });
  }
  sel.value = state.filters.min || '';
}

// ---- bonus-23: "find a stock" box (code / name -> zoom its group and open the card) -----

function renderLiveSearch(snap, shown) {
  liveSearch.shown = shown;
  let input = $('#liveFind');
  if (!input) {
    input = document.createElement('input');
    input.id = 'liveFind';
    input.type = 'search';
    input.className = 'field';
    input.placeholder = '找股票：代號／名稱';
    input.setAttribute('list', 'liveFindList');
    input.setAttribute('aria-label', '找股票');
    const list = document.createElement('datalist');
    list.id = 'liveFindList';
    $('#liveControls').append(input, list);
    input.addEventListener('keydown', (e) => {
      if (e.isComposing || e.keyCode === 229) return; // Zhuyin / Cangjie candidate selection
      if (e.key === 'Enter') goToStock(input.value);
      else if (e.key === 'Escape') input.blur();
      e.stopPropagation(); // keep the page's Esc / shortcut handlers out of typing
    });
    // picking a datalist entry fires "input" with the full "code name" value
    input.addEventListener('input', (e) => {
      if (!e.inputType || e.inputType === 'insertReplacementText') goToStock(input.value);
    });
  }
  if (snap && liveSearch.listFor !== snap) {
    liveSearch.listFor = snap;
    $('#liveFindList').innerHTML = snap.stocks
      .map((s) => `<option value="${escapeHtml(s.code)} ${escapeHtml(s.name)}"></option>`)
      .join('');
  }
}

function goToStock(query) {
  if (!String(query || '').trim()) return;
  const all = state.snapshot?.stocks || [];
  const hit = findStock(all, query);
  if (!hit) {
    toast(`找不到「${query.trim()}」`);
    return;
  }
  const shown = liveSearch.shown.find((s) => s.code === hit.code);
  if (shown) zoomTo(shown.industry);
  else toast(`${hit.code} ${hit.name} 不在目前的篩選／分組中，只開啟資訊卡`);
  openCard(hit.code);
  $('#liveFind').value = '';
  $('#liveFind').blur();
}

// ---- bonus-24: 量比 colour mode (today's turnover ÷ 20-day average turnover) ----------------
function applyRelVolume(stocks) {
  renderRvolButton();
  if (state.view.colorBy !== 'rvol') return stocks;
  // the server's cut-off moves at 09:00 (pre-open quotes are the last session's), so key on both
  const now = new Date();
  const hour = Number(now.toLocaleTimeString('en-GB', { timeZone: 'Asia/Taipei', hour: '2-digit', hour12: false }));
  const key = `${now.toLocaleDateString('sv', { timeZone: 'Asia/Taipei' })}|${hour >= 9}`;
  if (liveRvol.day !== key && Date.now() - liveRvol.failedAt > RVOL_RETRY_MS) loadRelVolume(key);
  return withRelVolume(stocks, liveRvol.avg || {}, sessionFraction()); // same intraday rule as 量能 (bonus-16)
}

async function loadRelVolume(today) {
  if (liveRvol.loading) return;
  liveRvol.loading = true;
  try {
    const resp = await fetch('/api/history/stock-turnover-avg?days=20', { cache: 'no-store' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json();
    if (!body.days) throw new Error('歷史資料尚未回補');
    liveRvol.avg = body.avg;
    liveRvol.day = today; // only after success
    liveRvol.failedAt = 0;
    render();
  } catch (err) {
    toast(`量比需要歷史成交值：${err.message}`);
    liveRvol.failedAt = Date.now(); // retried after RVOL_RETRY_MS (e.g. once the backfill has run)
  } finally {
    liveRvol.loading = false;
  }
}

function renderRvolButton() {
  const seg = $('#liveControls .seg[data-group="colorBy"]');
  if (seg && !seg.querySelector('[data-value="rvol"]')) {
    const b = document.createElement('button');
    b.dataset.value = 'rvol';
    b.textContent = '量比';
    b.title = '今日成交值 ÷ 前 20 個交易日平均成交值（盤中依已交易時間比例換算；紅＝放量、綠＝縮量；2 倍以上全紅）';
    seg.append(b);
  }
}

// ---- bonus-25: daily colour range (±3 / ±5 / ±7 / ±10%) next to the legend ---------------
function renderScaleSelect() {
  let sel = $('#liveScale');
  if (!sel) {
    sel = document.createElement('select');
    sel.id = 'liveScale';
    sel.className = 'field';
    sel.title = '今日漲跌的色階範圍：數字越小，小漲小跌越明顯（本週／量比不受影響）';
    sel.innerHTML = TODAY_SCALES.map((n) => `<option value="${n}">色階 ±${n}%</option>`).join('');
    $('#legendMin').closest('.legend').after(sel);
    sel.addEventListener('change', () => {
      state.view.scale = Number(sel.value);
      savePrefs();
      render();
    });
  }
  sel.value = String(colorLimit('today', state.view.scale));
  sel.disabled = state.view.colorBy !== 'today';
}

// ---- ①: ≥5% / limit-up shares (matrix columns + heatmap title mode) ----------------------
function ratioCell(v, count, full) {
  if (v === null || v === undefined) return '<td class="muted">—</td>';
  return `<td class="c" style="background:${colorFor(v, full)}">${v.toFixed(0)}%<small class="muted"> ${count}</small></td>`;
}

function renderGroupLabelSelect() {
  let sel = $('#liveGroupLabel');
  if (!sel) {
    sel = document.createElement('select');
    sel.id = 'liveGroupLabel';
    sel.className = 'field';
    sel.title = '熱力圖產業標題旁顯示的數字（量比模式時顯示量比）';
    sel.innerHTML = Object.entries(GROUP_LABELS).map(([k, label]) => `<option value="${k}">${label}</option>`).join('');
    $('#liveControls .seg[data-group="colorBy"]').after(sel);
    sel.addEventListener('change', () => {
      state.view.groupLabel = sel.value;
      savePrefs();
      render();
    });
  }
  sel.value = state.view.groupLabel in GROUP_LABELS ? state.view.groupLabel : 'cap';
}

// ---- ③: county money map / group word cloud in the live main area ----------------------------
function liveOverlay(id, inner) {
  const box = document.createElement('div');
  box.id = id;
  box.style.cssText = 'position:absolute;inset:0;display:flex;flex-direction:column;background:var(--bg)';
  box.innerHTML = inner;
  box.hidden = true;
  document.querySelector('#liveView .chart-wrap').appendChild(box);
  return box;
}

function renderLiveMap(stocks, grouped) {
  let seg = $('#liveViewMode');
  if (!seg) {
    seg = document.createElement('div');
    seg.className = 'seg';
    seg.id = 'liveViewMode';
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', '主畫面');
    seg.innerHTML =
      '<button data-value="heat">熱力圖</button><button data-value="map" title="依公司總部所在縣市彙總">台灣地圖</button>' +
      '<button data-value="words" title="目前分組的族群名稱：字越大成交值越大，紅漲綠跌">關鍵字圖</button>';
    $('#liveControls').prepend(seg);
    liveOverlay('liveMapBox', '<div class="panel-h" style="gap:8px"><span id="liveMapCtl"></span><span class="muted" id="liveMapNote"></span></div><div id="liveMap" style="flex:1;min-height:0"></div>');
    liveMap.ctl = mountCountyMap($('#liveMap'), { controls: $('#liveMapCtl'), note: $('#liveMapNote'), prefKey: 'openmarket.live.mapMetric', onStock: openCard });
    liveOverlay('liveWordsBox', '<div class="panel-h" style="gap:8px"><span id="liveWordsCtl"></span><span class="muted" id="liveWordsNote"></span></div><div id="liveWords" style="flex:1;min-height:0"></div>');
    liveMap.words = mountWordCloud($('#liveWords'), {
      controls: $('#liveWordsCtl'),
      note: $('#liveWordsNote'),
      prefKey: 'openmarket.live.wordMetric',
      onGroup: (name) => {
        // the side panel's 在熱力圖放大 button
        setLiveView('heat');
        zoomTo(name);
      },
      onStock: openCard,
    });
    seg.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-value]');
      if (b) setLiveView(b.dataset.value);
    });
  }
  for (const b of seg.querySelectorAll('button')) {
    const on = b.dataset.value === liveMap.view;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  }
  const mapBox = $('#liveMapBox');
  const wordsBox = $('#liveWordsBox');
  const wasHeat = mapBox.hidden && wordsBox.hidden;
  const mapWasHidden = mapBox.hidden;
  const wordsWereHidden = wordsBox.hidden;
  mapBox.hidden = liveMap.view !== 'map';
  wordsBox.hidden = liveMap.view !== 'words';
  if (liveMap.view === 'heat') {
    if (!wasHeat) chart.resize();
    return;
  }
  if (liveMap.view === 'map') {
    liveMap.ctl.render(stocks, '今日即時');
    if (mapWasHidden) liveMap.ctl.show();
  } else {
    liveMap.words.render(grouped, { caption: `今日即時 · ${WORD_GROUPING_LABELS[liveGrouping.by]}`, force: wordsWereHidden });
  }
}

function setLiveView(view) {
  liveMap.view = view;
  try {
    localStorage.setItem('openmarket.live.view', view);
  } catch {
    /* ignore */
  }
  render();
}

// ---- money flow in the live matrix: share z (vs the stored 20-day baseline), CLV flow, turnover rate --
function liveClv(s) {
  const { high, low, price } = s;
  if (high === null || low === null || price === null || high === undefined || !(high > low)) return 0;
  return (price - low - (high - price)) / (high - low);
}

function withFlows(rows, stocks, snap) {
  const by = liveGrouping.by;
  const base = liveFlow.baselines[by];
  if (!base && !liveFlow.loading[by]) loadFlowBaseline(by);
  // the market is every common stock in the snapshot, whatever the filters
  const market = (snap?.stocks || []).filter((s) => s.kind === 'stock').reduce((a, s) => a + (s.turnover_100m || 0), 0);
  const acc = new Map();
  const seen = new Set();
  for (const s of stocks) {
    if (s.kind !== 'stock' || seen.has(`${s.industry}|${s.code}`)) continue;
    seen.add(`${s.industry}|${s.code}`);
    const g = acc.get(s.industry) || { t: 0, mf: 0, vol: 0, shares: 0 };
    const t = s.turnover_100m || 0;
    g.t += t;
    g.mf += liveClv(s) * t;
    g.vol += (s.volume_lots || 0) * 1000;
    if (s.market_cap && s.price) g.shares += s.market_cap / s.price;
    acc.set(s.industry, g);
  }
  return rows.map((r) => {
    const g = acc.get(r.name);
    if (!g) return { ...r, z20: null, mf: null, turnover_rate: null };
    const share = market > 0 ? (g.t / market) * 100 : null;
    const b = base?.groups?.[r.name];
    return {
      ...r,
      share,
      z20: share !== null && b ? (share - b.m20) / b.s20 : base ? null : undefined,
      mf: g.t > 0 ? (g.mf / g.t) * 100 : null,
      turnover_rate: g.shares > 0 ? (g.vol / g.shares) * 100 : null,
    };
  });
}

async function loadFlowBaseline(by) {
  liveFlow.loading[by] = true;
  try {
    const resp = await fetch(`/api/history/flow-baseline?grouping=${by}`, { cache: 'no-store' });
    if (resp.ok) liveFlow.baselines[by] = await resp.json();
    render();
  } catch {
    /* retried on a later render */
  } finally {
    liveFlow.loading[by] = false;
  }
}

function liveFlowCells(r) {
  const z =
    r.z20 === undefined
      ? '<td class="muted">…</td>'
      : r.z20 === null
        ? '<td class="muted">—</td>'
        : `<td class="c" style="background:${colorFor(r.z20, 3)}" title="比重 ${r.share?.toFixed(2)}%">${r.z20.toFixed(1)}</td>`;
  const mf = r.mf === null ? '<td class="muted">—</td>' : `<td class="c" style="background:${colorFor(r.mf, 60)}">${r.mf.toFixed(0)}</td>`;
  const tr = r.turnover_rate === null ? '<td class="muted">—</td>' : `<td>${r.turnover_rate.toFixed(2)}%</td>`;
  return z + mf + tr;
}
