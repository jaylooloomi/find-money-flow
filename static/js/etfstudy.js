// 分頁4 · ETF 溢價研究: shows research/etf_premium's outputs as they stand (GET /api/research/etf-premium).
// The study runs offline (research/etf_premium/*.py); this tab only reads its results. Pure functions first
// (node --test), the panel last.

import { escapeHtml } from './transform.js';

/** A small Markdown subset → HTML: headings, lists, tables, bold, code, paragraphs (our own result files only). */
export function mdHtml(md) {
  if (!md) return '';
  const inline = (s) => escapeHtml(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const out = [];
  const lines = md.replace(/\r/g, '').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\|/.test(l)) {
      const rows = [];
      for (; i < lines.length && /^\|/.test(lines[i]); i++) rows.push(lines[i]);
      i--;
      const cells = (r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const body = rows.filter((r) => !/^\|[\s:|-]+\|$/.test(r));
      const [head, ...rest] = body;
      out.push(`<table class="es-table"><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rest
        .map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
        .join('')}</tbody></table>`);
    } else if (/^\s*[-*] /.test(l)) {
      const items = [];
      for (; i < lines.length && /^\s*[-*] /.test(lines[i]); i++) items.push(`<li>${inline(lines[i].replace(/^\s*[-*] /, ''))}</li>`);
      i--;
      out.push(`<ul>${items.join('')}</ul>`);
    } else if (/^!\[/.test(l)) {
      // an image line: the chart is placed by the panel
    } else if (/^#{1,4} /.test(l)) {
      const n = Math.min(4, l.match(/^#+/)[0].length + 1);
      out.push(`<h${n}>${inline(l.replace(/^#+ /, ''))}</h${n}>`);
    } else if (l.trim()) {
      out.push(`<p>${inline(l)}</p>`);
    }
  }
  return out.join('');
}

/** The coverage rows as a table: one line per ETF, the missing ones flagged. */
export function coverageHtml(rows) {
  if (!rows?.length) return '';
  const body = rows
    .map((r) => {
      const bad = r.nav_days === '0' ? 'down' : Number(r.trade_days_without_nav) > 0 ? 'warn' : '';
      return `<tr class="${bad}"><td>${escapeHtml(r.code)}</td><td>${escapeHtml(r.name)}${r.active === 'True' ? ' <small class="muted">主動</small>' : ''}</td>
        <td>${escapeHtml(r.nav_first)}～${escapeHtml(r.nav_last)}</td><td>${r.nav_days}</td><td>${r.trade_days_without_nav}</td>
        <td>${r.dividends}</td><td>${Number(r.avg_vol_60d_lots).toLocaleString()}</td></tr>`;
    })
    .join('');
  return `<table class="es-table"><thead><tr><th>代號</th><th>名稱</th><th>淨值期間</th><th>淨值天數</th>
    <th title="有成交但抓不到淨值的交易日（不內插，事件計算時剔除）">缺淨值交易日</th><th>配息次數</th><th title="今天往回 60 個交易日的日均成交量">近 60 日均量（張）</th></tr></thead><tbody>${body}</tbody></table>`;
}

export function mountEtfStudy(container) {
  const sec = document.createElement('section');
  sec.className = 'panel hEtfStudy';
  sec.innerHTML = `<div class="panel-h"><b>ETF 溢價研究</b><span class="muted">台股 ETF 下跌中出現溢價後，報酬是否較差？（程式在 research/etf_premium）</span></div>
    <div class="es-body"><p class="hint">讀取中…</p></div>`;
  container.append(sec);
  const body = sec.querySelector('.es-body');
  let loaded = false;

  async function refresh() {
    if (loaded) return;
    try {
      const r = await fetch('/api/research/etf-premium');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const x = await r.json();
      loaded = true;
      const parts = [];
      const svg = (s) => (s ? `<img class="es-chart" alt="三組在六個視窗的中位數報酬" src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(s)}">` : '');
      if (x.summary) parts.push(`<div class="es-md">${mdHtml(x.summary)}${svg(x.chart)}</div>`);
      if (x.robustness) parts.push(`<details><summary>穩健性檢查</summary><div class="es-md">${mdHtml(x.robustness)}</div></details>`);
      if (x.ext_summary) parts.push(`<details><summary>附錄：延伸到 2003 年（MoneyDJ 淨值）</summary><div class="es-md">${mdHtml(x.ext_summary)}${svg(x.ext_chart)}</div></details>`);
      if (x.feasibility) parts.push(`<div class="es-md">${mdHtml(x.feasibility)}</div>`);
      if (x.coverage?.length) parts.push(`<details><summary>各檔資料涵蓋（${x.coverage.length} 檔）</summary>${coverageHtml(x.coverage)}</details>`);
      body.innerHTML = parts.join('') || '<p class="hint">還沒有結果：先執行 research/etf_premium/fetch.py 與 coverage.py。</p>';
    } catch (e) {
      body.innerHTML = `<p class="hint">讀取失敗：${escapeHtml(e.message)}</p>`;
    }
  }
  return { refresh };
}
