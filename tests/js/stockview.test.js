import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildDailyOption, DAILY_MA } from '../../static/js/stockview.js';

const day = (d, o, c, v = 1000) => ({ date: `2026-10-${String(d).padStart(2, '0')}`, open: o, high: Math.max(o, c) + 1, low: Math.min(o, c) - 1, close: c, change_pct: 0, volume_lots: v });

test('buildDailyOption: candles, the 5/10/20-day averages, volume, the report day marked', () => {
  const series = Array.from({ length: 25 }, (_, i) => day(i + 1, 100 + i, 101 + i));
  series[3] = { ...series[3], open: null }; // a halt: a gap, not a candle
  const o = buildDailyOption(series, '2026-10-25');
  const k = o.series.find((s) => s.type === 'candlestick');
  assert.equal(k.data.length, 25);
  assert.equal(k.data[3], '-');
  assert.deepEqual(k.data[0], [100, 101, 99, 102]); // [open, close, low, high]
  assert.equal(k.markLine.data[0].xAxis, '10/25');
  assert.deepEqual(o.series.filter((s) => s.type === 'line').map((s) => s.name), DAILY_MA.map((n) => `MA${n}`));
  const ma10 = o.series.find((s) => s.name === 'MA10').data;
  assert.equal(ma10[8], '-'); // not 10 days yet
  assert.ok(Math.abs(ma10[9] - (101 + 110) / 2) < 1e-9);
  assert.equal(o.series.find((s) => s.type === 'bar').data[24].itemStyle.borderColor, '#ffd400');
  assert.equal(buildDailyOption(series, '2026-11-30').series[0].markLine, undefined); // the day not in range
});
