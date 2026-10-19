'use strict';
const $ = id => document.getElementById(id);
const fmt = new Intl.NumberFormat('en-US');
const colors = ['#c45100', '#2856a3', '#8052a1', '#68732c'];
const dashes = [[], [6, 3], [2, 3], [8, 3, 2, 3]];
const points = ['circle', 'rect', 'triangle', 'rectRot'];
let state, chartUnits, chartHourly, refreshing = false, refreshAgain = false;
const dayLabel = date => new Intl.DateTimeFormat('en-US', {day: 'numeric', month: 'short', timeZone: 'UTC'}).format(new Date(date + 'T12:00:00Z'));

async function api(path) {
  const response = await fetch(new URL(path, location.origin), {cache: 'no-store'});
  if (!response.ok) {
    if (response.status === 401) throw new Error('Sign-in required. Reload the page and enter your username and password.');
    if (response.status === 429) throw new Error('Too many sign-in attempts. Please try again in 5 minutes.');
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}). Please try again.`);
  }
  return response.json();
}
function notice(text) { $('notice').textContent = text || ''; $('notice').hidden = !text; }
function statusView(s) {
  $('setup').hidden = s.configured;
  $('shop-name').textContent = `${s.shop || 'Shopify'} · four SKUs, tracked separately`;
  $('tz').textContent = s.timezone;
  const last = s.last_success && new Date(s.last_success);
  const stale = !last || Date.now() - last.getTime() > s.poll_seconds * 3000;
  $('connection').textContent = !s.configured ? 'Setup required' : s.last_error ? 'Sync error' : s.syncing ? 'Syncing…' : stale ? 'Waiting for data' : 'Live updates';
  $('connection').className = 'connection' + (s.configured && !stale && !s.last_error ? ' live' : '');
  $('updated').textContent = last ? `Last updated ${new Intl.DateTimeFormat('en-US', {dateStyle: 'medium', timeStyle: 'medium', timeZone: s.timezone}).format(last)}` : s.syncing ? `Initial import: ${fmt.format(s.imported_orders)} orders checked` : 'Waiting for the first sync';
  if (s.last_error) notice((last ? 'Your last successful data is preserved. ' : 'Data has not loaded yet. ') + s.last_error);
  else if (s.configured && !last) notice(s.syncing ? 'Loading history for your four SKUs. The first sync may take a few minutes.' : 'Waiting for the first Shopify import.');
  else if (s.configured && stale) notice('Data has not updated recently. Check the Shopify connection and application logs in Coolify.');
  else if (s.configured && !s.refunds_ready) notice('Importing refund history. Refunded counts will appear when the import is complete.');
  else notice('');
  for (const option of $('period').options) option.disabled = Number(option.value) > s.history_days;
  if (Number($('period').value) > s.history_days) $('period').value = '1';
}
function chartOptions() {
  return {responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: 'index', intersect: false},
    plugins: {legend: {display: true, position: 'bottom', labels: {boxWidth: 9, boxHeight: 9, padding: 16, usePointStyle: true, font: {size: 10}}},
      tooltip: {callbacks: {label: c => `${c.dataset.label}: ${fmt.format(c.parsed.y)} units`}}},
    scales: {x: {grid: {display: false}, border: {display: false}, ticks: {maxTicksLimit: 7, color: '#647176', font: {size: 10}}},
      y: {beginAtZero: true, grid: {color: '#edf0f1'}, border: {display: false}, ticks: {precision: 0, maxTicksLimit: 5, color: '#647176', font: {size: 10}}}}};
}
function drawChart(data) {
  const labels = data.daily.map(d => dayLabel(d.date));
  if (chartUnits) chartUnits.destroy();
  if (chartHourly) chartHourly.destroy();
  function datasets(skus, key) {
    return skus.map((item, index) => ({label: item.sku, data: item[key].map(d => d.quantity),
      borderColor: colors[index], backgroundColor: colors[index], borderDash: dashes[index],
      pointStyle: points[index], borderWidth: 2, fill: false, tension: 0.15,
      pointRadius: key === 'daily' && data.days === 1 ? 5 : 2, pointHoverRadius: 5}));
  }
  chartUnits = new Chart($('units-chart'), {type: 'line', data: {labels, datasets: datasets(data.skus, 'daily')}, options: chartOptions()});
  const hourly = data.last_24_hours;
  const timeFormat = new Intl.DateTimeFormat('en-US', {hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: hourly.timezone});
  const fullTimeFormat = new Intl.DateTimeFormat('en-US', {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: hourly.timezone, timeZoneName: 'shortOffset'});
  const options = chartOptions();
  options.scales.x.ticks.maxTicksLimit = 12;
  options.plugins.tooltip.callbacks.title = items => {
    const hour = hourly.hourly[items[0].dataIndex];
    return `${fullTimeFormat.format(new Date(hour.start_at))} – ${fullTimeFormat.format(new Date(hour.end_at))}`;
  };
  $('hourly-window').textContent = `Rolling 60-minute buckets · ${hourly.timezone}`;
  chartHourly = new Chart($('hourly-chart'), {type: 'line', data: {
    labels: hourly.hourly.map(hour => timeFormat.format(new Date(hour.start_at))),
    datasets: datasets(hourly.skus, 'hourly')}, options});
}
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function skuCard(item, hasData, refundsReady, index) {
  const card = element('article', `sku-card sku-color-${index}`);
  card.dataset.sku = item.sku;
  const heading = element('h2', 'sku-name', item.sku);
  const label = element('p', 'muted', 'Units');
  const quantity = element('strong', 'sku-quantity', hasData ? fmt.format(item.quantity) : '—');
  const metrics = element('dl', 'sku-metrics');
  for (const [name, value] of [['Orders', item.orders], ['Refunded', item.refunded_quantity], ['Authorized', item.authorized_orders]]) {
    const group = element('div');
    const label = element('dt', '', name);
    if (name === 'Refunded') label.title = 'Refunded units allocated to this SKU in Shopify';
    group.append(label, element('dd', '', hasData && (name !== 'Refunded' || refundsReady) ? fmt.format(value) : '—')); metrics.append(group);
  }
  card.append(heading, label, quantity, metrics);
  return card;
}
function dataView(data) {
  const hasData = Boolean(state.last_success);
  $('sku-cards').replaceChildren(...data.skus.map((item, index) => skuCard(item, hasData, data.refunds_ready, index)));
  $('charts-area').hidden = !hasData;
  $('daily-table').replaceChildren(...data.daily.map((day, index) => ({day, index})).reverse().flatMap(({day, index}) => data.skus.map(item => {
    const values = item.daily[index];
    const tr = element('tr');
    [dayLabel(day.date), item.sku, fmt.format(values.quantity), fmt.format(values.orders), data.refunds_ready ? fmt.format(values.refunded_quantity) : '—', fmt.format(values.authorized_orders)]
      .forEach(v => tr.append(element('td', '', v)));
    return tr;
  })));
  if (hasData) drawChart(data);
}
function trafficView(data) {
  const days = data ? data.days : Number($('period').value);
  $('traffic-period').textContent = days === 1 ? 'Today' : `Last ${days} days`;
  $('traffic-count').textContent = data && data.available ? fmt.format(data.sessions) : '—';
  $('traffic-email').textContent = data && data.breakdown_available ? fmt.format(data.email_sessions) : '—';
  $('traffic-other').textContent = data && data.breakdown_available ? fmt.format(data.other_sessions) : '—';
  let message = 'Waiting for Shopify analytics';
  if (data && data.last_success) {
    const time = new Intl.DateTimeFormat('en-US', {dateStyle: 'medium', timeStyle: 'short', timeZone: data.timezone}).format(new Date(data.last_success));
    message = `Last updated ${time} · ${data.timezone}`;
    if (data.stale) message = `Data may be delayed. ${message}`;
    if (!data.available) message = 'Waiting for the selected date range to refresh';
    else if (!data.breakdown_available) message = 'Loading the Email / Other breakdown. ' + message;
  }
  if (data && data.last_error) message = (data.available ? 'Showing the last successful report. ' : '') + data.last_error;
  if (!data) message = 'Sessions could not be loaded. Retrying next minute.';
  $('traffic-status').textContent = message;
}
async function refresh() {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    state = await api('/api/status'); statusView(state);
    const days = $('period').value;
    const [sales, traffic] = await Promise.all([api('/api/dashboard?days=' + days), api('/api/traffic?days=' + days).catch(() => null)]);
    dataView(sales); trafficView(traffic);
  } catch (error) {
    notice(error.message);
    $('connection').textContent = 'Connection lost'; $('connection').className = 'connection';
  } finally {
    refreshing = false;
    if (refreshAgain) { refreshAgain = false; refresh(); }
  }
}
$('period').addEventListener('change', refresh);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 60000);
