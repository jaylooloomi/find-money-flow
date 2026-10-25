import { test } from 'node:test';
import assert from 'node:assert/strict';

import { coverageHtml, mdHtml } from '../../static/js/etfstudy.js';

test('mdHtml: headings, lists, tables, bold — escaped', () => {
  const html = mdHtml('## 結論\n不支持 **假說**\n- a <b>\n- b\n\n| 組 | n |\n|---|---|\n| 溢價 | 12 |');
  assert.match(html, /<h3>結論<\/h3><p>不支持 <b>假說<\/b><\/p><ul><li>a &lt;b&gt;<\/li><li>b<\/li><\/ul>/);
  assert.match(html, /<table class="es-table"><thead><tr><th>組<\/th><th>n<\/th><\/tr><\/thead><tbody><tr><td>溢價<\/td><td>12<\/td><\/tr><\/tbody><\/table>/);
  assert.equal(mdHtml(null), '');
});

test('coverageHtml: missing 淨值 in red, gaps in yellow', () => {
  const row = (o) => ({ code: '0050', name: '元大台灣50', active: 'False', nav_first: '20200102', nav_last: '20261022', nav_days: '1653', trade_days_without_nav: '0', dividends: '10', avg_vol_60d_lots: '50000', ...o });
  const html = coverageHtml([row({}), row({ code: '00981A', active: 'True', nav_days: '0' }), row({ code: '0056', trade_days_without_nav: '3' })]);
  assert.match(html, /<tr class=""><td>0050<\/td>/);
  assert.match(html, /<tr class="down"><td>00981A<\/td><td>元大台灣50 <small class="muted">主動<\/small>/);
  assert.match(html, /<tr class="warn"><td>0056/);
  assert.match(html, /50,000/);
  assert.equal(coverageHtml([]), '');
});

test('mdHtml: an image line is left to the panel', () => {
  assert.equal(mdHtml('![圖](a.svg)'), '');
});

test('mdHtml: a quote line is a muted paragraph', () => {
  assert.equal(mdHtml('> 註'), '<p class="muted">註</p>');
});
