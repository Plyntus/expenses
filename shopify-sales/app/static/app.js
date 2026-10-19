'use strict';
const $ = id => document.getElementById(id);
const fmt = new Intl.NumberFormat('ru-RU');
let state, chartRevenue, chartUnits, selected = new Map(), nextCursor = null, searchTerm = '', refreshing = false, catalogLoading = false;
const money = (value, currency) => new Intl.NumberFormat('ru-RU', {style: 'currency', currency, maximumFractionDigits: 2}).format(value);
const dayLabel = date => new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'short', timeZone: 'UTC'}).format(new Date(date + 'T12:00:00Z'));

async function api(path, options = {}) {
  const response = await fetch(new URL(path, location.origin), {cache: 'no-store', ...options, headers: {'X-Sales-Request': '1', ...options.headers}});
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
  $('manage').disabled = !s.configured;
  $('shop-name').textContent = s.shop_name || s.shop || 'Свежие цифры по выбранным товарам';
  $('tz').textContent = s.timezone;
  const last = s.last_success && new Date(s.last_success);
  const stale = !last || Date.now() - last.getTime() > s.poll_seconds * 3000;
  $('connection').textContent = !s.configured ? 'Ожидает настройки' : s.last_error ? 'Ошибка синхронизации' : s.syncing ? 'Синхронизация…' : stale ? 'Ожидание данных' : 'Данные обновляются';
  $('connection').className = 'connection' + (!stale && !s.last_error ? ' live' : '');
  $('updated').textContent = last ? `Обновлено ${new Intl.DateTimeFormat('ru-RU', {dateStyle: 'short', timeStyle: 'medium', timeZone: s.timezone}).format(last)}` : s.syncing ? `Первая загрузка: получено ${fmt.format(s.imported_orders)} заказов` : 'Ожидание первой синхронизации';
  if (s.last_error) notice('Последние данные сохранены. ' + s.last_error);
  else if (s.configured && !last) notice(s.syncing ? 'Загружаем историю заказов. Первая синхронизация может занять несколько минут.' : 'Ожидаем первую загрузку из Shopify.');
  else if (s.configured && stale) notice('Данные давно не обновлялись. Проверьте подключение Shopify и логи приложения в Coolify.');
  else notice('');
  for (const option of $('period').options) option.disabled = Number(option.value) > s.history_days;
  if (Number($('period').value) > s.history_days) $('period').value = '1';
}
function chartOptions(currency, isMoney = false) {
  return {responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: 'index', intersect: false},
    plugins: {legend: {display: !isMoney, position: 'bottom', labels: {boxWidth: 10, boxHeight: 10, padding: 20, usePointStyle: true}},
      tooltip: {callbacks: {label: c => `${c.dataset.label}: ${isMoney ? money(c.parsed.y, currency) : fmt.format(c.parsed.y)}`}}},
    scales: {x: {grid: {display: false}, border: {display: false}, ticks: {maxTicksLimit: 7, color: '#647176', font: {size: 10}}},
      y: {beginAtZero: true, grid: {color: '#edf0f1'}, border: {display: false}, ticks: {precision: isMoney ? undefined : 0, maxTicksLimit: 5, color: '#647176', font: {size: 10}}}}};
}
function drawCharts(data) {
  const labels = data.daily.map(d => dayLabel(d.date));
  if (chartRevenue) chartRevenue.destroy();
  if (chartUnits) chartUnits.destroy();
  chartRevenue = new Chart($('revenue-chart'), {type: 'line', data: {labels, datasets: [{label: 'Выручка', data: data.daily.map(d => d.revenue),
    borderColor: '#176c53', backgroundColor: '#176c5312', borderWidth: 2.5, fill: true, tension: 0.25, pointRadius: data.days === 1 ? 5 : 2, pointHoverRadius: 5}]}, options: chartOptions(data.currency, true)});
  chartUnits = new Chart($('units-chart'), {type: 'bar', data: {labels, datasets: [
    {label: 'Единицы', data: data.daily.map(d => d.quantity), backgroundColor: '#176c53', borderRadius: 3, maxBarThickness: 20},
    {label: 'Заказы', data: data.daily.map(d => d.orders), backgroundColor: '#b1c9d0', borderRadius: 3, maxBarThickness: 20}
  ]}, options: chartOptions(data.currency)});
}
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function productRow(product, currency) {
  const row = element('div', 'product-row');
  row.append(element('span', 'product-title', product.title));
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 160 40'); svg.setAttribute('class', 'sparkline'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  const values = product.daily.map(d => d.revenue), max = Math.max(...values, 1);
  path.setAttribute('d', values.map((v, i) => `${i ? 'L' : 'M'}${values.length > 1 ? i * 156 / (values.length - 1) + 2 : 80},${36 - v / max * 30}`).join(' ')); svg.append(path); row.append(svg);
  const rev = element('span', 'product-number', money(product.revenue, currency)); rev.append(element('small', '', `${fmt.format(product.quantity)} ед.`)); row.append(rev);
  const orders = element('span', 'product-number order-number', fmt.format(product.orders)); orders.append(element('small', '', 'заказов')); row.append(orders);
  return row;
}
function dataView(data) {
  const hasData = state.last_success && data.products.length;
  $('revenue').textContent = hasData ? money(data.totals.revenue, data.currency) : '—';
  $('quantity').textContent = hasData ? fmt.format(data.totals.quantity) : '—';
  $('orders').textContent = hasData ? fmt.format(data.totals.orders) : '—';
  $('empty').hidden = !state.configured || data.products.length > 0;
  $('charts-area').hidden = !data.products.length;
  $('currency').textContent = data.currency;
  $('product-count').textContent = `ТОВАРОВ: ${data.products.length}`;
  $('product-list').replaceChildren(...data.products.map(p => productRow(p, data.currency)));
  $('daily-table').replaceChildren(...data.daily.map(day => {
    const tr = element('tr'); [dayLabel(day.date), money(day.revenue, data.currency), fmt.format(day.quantity), fmt.format(day.orders)].forEach(v => tr.append(element('td', '', v))); return tr;
  }));
  if (data.products.length) drawCharts(data);
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
function selectionCount() { $('selection-count').textContent = `Выбрано ${selected.size} из 100`; }
function catalogItem(product) {
  const label = element('label', 'catalog-item');
  const input = document.createElement('input'); input.type = 'checkbox'; input.checked = selected.has(product.id);
  const text = element('span', '', product.title); text.append(element('small', '', product.id.split('/').pop()));
  input.addEventListener('change', () => {
    if (input.checked && selected.size >= 100) { input.checked = false; $('dialog-error').textContent = 'Можно выбрать не больше 100 товаров.'; $('dialog-error').hidden = false; return; }
    if (input.checked) selected.set(product.id, {id: product.id, title: product.title}); else selected.delete(product.id);
    selectionCount();
  });
  label.append(input, text); return label;
}
async function catalog(append = false) {
  if (catalogLoading) return;
  catalogLoading = true; $('more-products').disabled = true;
  $('catalog-message').textContent = 'Загружаем товары…'; $('dialog-error').hidden = true;
  const params = new URLSearchParams({q: searchTerm}); if (append && nextCursor) params.set('after', nextCursor);
  try {
    const result = await api('/api/products?' + params);
    if (!append) $('catalog').replaceChildren();
    const rendered = new Set([...$('catalog').children].map(n => n.dataset.productId));
    // Keep selected items visible even when they are archived or outside the current catalog page.
    const products = !append && !searchTerm ? [...selected.values(), ...result.nodes] : result.nodes;
    for (const p of products) if (!rendered.has(p.id)) { const item = catalogItem(p); item.dataset.productId = p.id; $('catalog').append(item); rendered.add(p.id); }
    nextCursor = result.pageInfo.hasNextPage ? result.pageInfo.endCursor : null;
    $('more-products').hidden = !nextCursor;
    $('catalog-message').textContent = result.nodes.length ? 'Отметьте нужные товары в списке.' : 'Ничего не найдено. Попробуйте другое название.';
  } catch (error) { $('catalog-message').textContent = ''; $('dialog-error').textContent = error.message; $('dialog-error').hidden = false; }
  finally { catalogLoading = false; $('more-products').disabled = false; }
}
async function openProducts() {
  if (!state?.configured) return;
  selected = new Map(state.products.map(p => [p.id, p])); searchTerm = ''; nextCursor = null;
  $('search').value = ''; $('catalog').replaceChildren(); $('more-products').hidden = true;
  selectionCount(); $('product-dialog').showModal(); await catalog();
}
$('manage').addEventListener('click', openProducts);
$('empty-manage').addEventListener('click', openProducts);
for (const id of ['close-dialog', 'cancel-dialog']) $(id).addEventListener('click', () => $('product-dialog').close());
$('search-form').addEventListener('submit', event => { event.preventDefault(); if (catalogLoading) return; searchTerm = $('search').value.trim(); nextCursor = null; catalog(); });
$('more-products').addEventListener('click', () => catalog(true));
$('save-products').addEventListener('click', async () => {
  $('save-products').disabled = true; $('dialog-error').hidden = true;
  try {
    await api('/api/products', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({products: [...selected.values()]})});
    $('product-dialog').close(); await refresh();
  } catch (error) { $('dialog-error').textContent = error.message; $('dialog-error').hidden = false; }
  finally { $('save-products').disabled = false; }
});
$('period').addEventListener('change', refresh);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 60000);
