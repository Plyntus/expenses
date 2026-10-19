'use strict';
const $ = id => document.getElementById(id);
const fmt = new Intl.NumberFormat('ru-RU');
const colors = ['#176c53', '#2856a3', '#8052a1', '#9b6616'];
const dashes = [[], [6, 3], [2, 3], [8, 3, 2, 3]];
const points = ['circle', 'rect', 'triangle', 'rectRot'];
let state, chartRevenue, chartUnits, refreshing = false;
const money = (value, currency) => new Intl.NumberFormat('ru-RU', {style: 'currency', currency, maximumFractionDigits: 2}).format(value);
const dayLabel = date => new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'short', timeZone: 'UTC'}).format(new Date(date + 'T12:00:00Z'));

async function api(path) {
  const response = await fetch(new URL(path, location.origin), {cache: 'no-store'});
  if (!response.ok) {
    if (response.status === 401) throw new Error('Требуется вход. Обновите страницу и введите логин и пароль.');
    if (response.status === 429) throw new Error('Слишком много попыток входа. Повторите через 5 минут.');
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Ошибка запроса (${response.status}). Повторите попытку.`);
  }
  return response.json();
}
function notice(text) { $('notice').textContent = text || ''; $('notice').hidden = !text; }
function statusView(s) {
  $('setup').hidden = s.configured;
  $('shop-name').textContent = `${s.shop || 'Shopify'} · четыре SKU, каждый отдельно`;
  $('tz').textContent = s.timezone;
  const last = s.last_success && new Date(s.last_success);
  const stale = !last || Date.now() - last.getTime() > s.poll_seconds * 3000;
  $('connection').textContent = !s.configured ? 'Ожидает настройки' : s.last_error ? 'Ошибка синхронизации' : s.syncing ? 'Синхронизация…' : stale ? 'Ожидание данных' : 'Данные обновляются';
  $('connection').className = 'connection' + (s.configured && !stale && !s.last_error ? ' live' : '');
  $('updated').textContent = last ? `Обновлено ${new Intl.DateTimeFormat('ru-RU', {dateStyle: 'short', timeStyle: 'medium', timeZone: s.timezone}).format(last)}` : s.syncing ? `Первая загрузка: проверено ${fmt.format(s.imported_orders)} заказов` : 'Ожидание первой синхронизации';
  if (s.last_error) notice((last ? 'Последние данные сохранены. ' : 'Данные пока не загружены. ') + s.last_error);
  else if (s.configured && !last) notice(s.syncing ? 'Загружаем историю четырёх SKU. Первая синхронизация может занять несколько минут.' : 'Ожидаем первую загрузку из Shopify.');
  else if (s.configured && stale) notice('Данные давно не обновлялись. Проверьте подключение Shopify и логи приложения в Coolify.');
  else notice('');
  for (const option of $('period').options) option.disabled = Number(option.value) > s.history_days;
  if (Number($('period').value) > s.history_days) $('period').value = '1';
}
function chartOptions(currency, isMoney = false) {
  return {responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: 'index', intersect: false},
    plugins: {legend: {display: true, position: 'bottom', labels: {boxWidth: 9, boxHeight: 9, padding: 16, usePointStyle: true, font: {size: 10}}},
      tooltip: {callbacks: {label: c => `${c.dataset.label}: ${isMoney ? money(c.parsed.y, currency) : fmt.format(c.parsed.y)}`}}},
    scales: {x: {grid: {display: false}, border: {display: false}, ticks: {maxTicksLimit: 7, color: '#647176', font: {size: 10}}},
      y: {beginAtZero: true, grid: {color: '#edf0f1'}, border: {display: false}, ticks: {precision: isMoney ? undefined : 0, maxTicksLimit: 5, color: '#647176', font: {size: 10}}}}};
}
function drawCharts(data) {
  const labels = data.daily.map(d => dayLabel(d.date));
  if (chartRevenue) chartRevenue.destroy();
  if (chartUnits) chartUnits.destroy();
  function datasets(metric) {
    return data.skus.map((item, index) => ({label: item.sku, data: item.daily.map(d => d[metric]),
      borderColor: colors[index], backgroundColor: colors[index], borderDash: dashes[index],
      pointStyle: points[index], borderWidth: 2, fill: false, tension: 0.15,
      pointRadius: data.days === 1 ? 5 : 2, pointHoverRadius: 5}));
  }
  chartRevenue = new Chart($('revenue-chart'), {type: 'line', data: {labels, datasets: datasets('revenue')}, options: chartOptions(data.currency, true)});
  chartUnits = new Chart($('units-chart'), {type: 'line', data: {labels, datasets: datasets('quantity')}, options: chartOptions(data.currency)});
}
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function skuCard(item, currency, hasData, index) {
  const card = element('article', `sku-card sku-color-${index}`);
  card.dataset.sku = item.sku;
  const heading = element('h2', 'sku-name', item.sku);
  const label = element('p', 'muted', 'Выручка');
  const revenue = element('strong', 'sku-revenue', hasData ? money(item.revenue, currency) : '—');
  const metrics = element('dl', 'sku-metrics');
  for (const [name, value] of [['Продано единиц', item.quantity], ['Заказы', item.orders]]) {
    const group = element('div'); group.append(element('dt', '', name), element('dd', '', hasData ? fmt.format(value) : '—')); metrics.append(group);
  }
  card.append(heading, label, revenue, metrics);
  return card;
}
function dataView(data) {
  const hasData = Boolean(state.last_success);
  $('sku-cards').replaceChildren(...data.skus.map((item, index) => skuCard(item, data.currency, hasData, index)));
  $('charts-area').hidden = !hasData;
  $('currency').textContent = data.currency;
  $('daily-table').replaceChildren(...data.daily.flatMap((day, index) => data.skus.map(item => {
    const values = item.daily[index];
    const tr = element('tr');
    [dayLabel(day.date), item.sku, money(values.revenue, data.currency), fmt.format(values.quantity), fmt.format(values.orders)]
      .forEach(v => tr.append(element('td', '', v)));
    return tr;
  })));
  if (hasData) drawCharts(data);
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    state = await api('/api/status'); statusView(state);
    dataView(await api('/api/dashboard?days=' + $('period').value));
  } catch (error) {
    notice(error.message);
    $('connection').textContent = 'Нет соединения'; $('connection').className = 'connection';
  } finally { refreshing = false; }
}
$('period').addEventListener('change', refresh);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 60000);
