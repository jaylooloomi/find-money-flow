// 盤後報告's chart area: a stock clicked anywhere in the report shows its daily candles (left, with the 5/10/20-day
// averages — the 10-day one is the 回測 10 日線 list's) and that day's intraday chart (right, intradaychart.js).
// Pure functions first (node --test), the panel last.

import { escapeHtml, fmtPct } from './transform.js';
import { movingAverage } from './indexchart.js';
import { mountIntraday } from './intradaychart.js';

export const DAILY_DAYS = 60;
export const DAILY_MA = [5, 10, 20];
const UP = '#e0262c'; // 台股：紅漲
const DOWN = '#1fa24a'; // 綠跌
const MA_COLORS = ['#f2c94c', '#56ccf2', '#bb6bd9'];

/** ECharts option for the daily candles + volume of `series` [{date, open, high, low, close, volume_lots}],
 *  the report day marked. A day without an open (a halt) is a gap, not a made-up candle. */
export function buildDailyOption(series, day) {
  const dates = series.map((r) => r.date.slice(5).replace('-', '/'));
  const closes = series.map((r) => r.close ?? null);
  const at = series.findIndex((r) => r.date === day);
  return {
    animation: false,
    grid: [
      { left: 52, right: 8, top: 10, height: '62%' },
      { left: 52, right: 8, top: '78%', bottom: 22 },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (ps) => {
        const r = series[ps[0]?.dataIndex];
        if (!r) return '';
        const ma = DAILY_MA.map((n, j) => {
          const v = movingAverage(closes, n)[ps[0].dataIndex];
          return v === null ? '' : `<br><span style="color:${MA_COLORS[j]}">MA${n}</span> ${v.toFixed(2)}`;
        }).join('');
        return `${escapeHtml(r.date)}<br>開 ${r.open ?? '—'}　高 ${r.high ?? '—'}<br>低 ${r.low ?? '—'}　收 ${r.close ?? '—'}<br>漲跌 ${fmtPct(r.change_pct)}　量 ${r.volume_lots == null ? '—' : Math.round(r.volume_lots).toLocaleString()} 張${ma}`;
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    xAxis: [0, 1].map((i) => ({
      type: 'category',
      gridIndex: i,
      data: dates,
      boundaryGap: true,
      axisLabel: { show: i === 1, color: '#999', fontSize: 10 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#444' } },
    })),
    yAxis: [0, 1].map((i) => ({
      type: 'value',
      gridIndex: i,
      scale: true,
      splitNumber: i ? 2 : 4,
      axisLabel: { color: '#999', fontSize: 10, formatter: i ? (v) => `${Math.round(v / 1000)}k` : undefined },
      splitLine: { lineStyle: { color: '#262a33' } },
    })),
    series: [
      {
        name: '日K',
        type: 'candlestick',
        data: series.map((r) => (r.open != null && r.close != null && r.high != null && r.low != null ? [r.open, r.close, r.low, r.high] : '-')),
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
        markLine: at >= 0 ? { symbol: 'none', silent: true, label: { show: false }, lineStyle: { color: '#ffd400', width: 1 }, data: [{ xAxis: dates[at] }] } : undefined,
      },
      ...DAILY_MA.map((n, j) => ({
        name: `MA${n}`,
        type: 'line',
        data: movingAverage(closes, n).map((v) => (v === null ? '-' : v)),
        showSymbol: false,
        lineStyle: { width: 1, color: MA_COLORS[j] },
        silent: true,
      })),
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: series.map((r, i) => ({
          value: r.volume_lots ?? '-',
          itemStyle: { color: (r.close ?? 0) >= (r.open ?? 0) ? `${UP}99` : `${DOWN}99`, ...(i === at ? { borderColor: '#ffd400', borderWidth: 1 } : {}) },
        })),
      },
    ],
  };
}

export const VIEW_HINT = '點報告裡任何一檔股票，這裡會顯示左邊的日線（60 天，5／10／20 日線）和右邊當天的分時走勢。';

export function mountStockView(container, { onCard }) {
  const el = document.createElement('div');
  el.className = 'rp-view';
  el.innerHTML = `<div class="rp-view-left"><div class="rp-view-head"><span class="rp-view-title muted">${escapeHtml(VIEW_HINT)}</span></div><div class="rp-view-k"></div></div>
    <div class="rp-view-right"></div>`;
  container.append(el);
  const kEl = el.querySelector('.rp-view-k');
  const head = el.querySelector('.rp-view-title');
  const right = el.querySelector('.rp-view-right');
  const state = { date: null, intraday: null, chart: null, ticket: 0, code: null };
  new ResizeObserver(() => state.chart?.resize()).observe(kEl);

  async function ensureIntraday(date) {
    if (state.intraday && state.date === date) return state.intraday;
    let benchmarks = [];
    try {
      const r = await fetch(`/api/history/index-race?date=${encodeURIComponent(date)}`, { cache: 'no-store' });
      benchmarks = ((r.ok ? await r.json() : null)?.benchmarks || []).filter((b) => b.points?.length);
    } catch {
      /* drawn without the index lines */
    }
    right.innerHTML = '';
    state.intraday = mountIntraday(right, { date, onCard, benchmarks });
    state.date = date;
    return state.intraday;
  }

  async function show(code, name, date) {
    const t = ++state.ticket;
    state.code = code;
    el.classList.add('on');
    head.className = 'rp-view-title';
    head.innerHTML = `<b>${escapeHtml(name || code)}</b> <span class="muted">${escapeHtml(code)} · 日線 ${DAILY_DAYS} 天（黃線＝${escapeHtml(date)}）</span>`;
    const intraday = ensureIntraday(date).then((ic) => t === state.ticket && ic.load(code, name));
    try {
      const r = await fetch(`/api/history/stock/${encodeURIComponent(code)}?to=${encodeURIComponent(date)}&days=${DAILY_DAYS}`, { cache: 'no-store' });
      const d = r.ok ? await r.json() : null;
      if (t !== state.ticket) return;
      if (!d?.series?.length) throw new Error('這一天沒有日線資料');
      if (!state.chart) {
        kEl.innerHTML = '';
        state.chart = echarts.init(kEl);
      }
      state.chart.setOption(buildDailyOption(d.series, date), { notMerge: true });
    } catch (err) {
      if (t !== state.ticket) return;
      state.chart?.dispose();
      state.chart = null;
      kEl.innerHTML = `<p class="hint" style="padding:8px">${escapeHtml(err.message)}</p>`;
    }
    await intraday;
  }

  /** A new report day: the shown stock again on that day, or the hint. */
  function setDate(date) {
    if (state.code && date !== state.date) show(state.code, head.querySelector('b')?.textContent, date);
  }

  return { show, setDate, el };
}
