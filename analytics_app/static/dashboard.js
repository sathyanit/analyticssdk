'use strict';
const $ = selector => document.querySelector(selector);
const fmt = n => new Intl.NumberFormat().format(n);
let requestSequence = 0;
let allEventNames = new Set();
let latestDaily = [];
function range(days) {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - days + 1);
  $('#start').value = start.toISOString().slice(0, 10);
  $('#end').value = end.toISOString().slice(0, 10);
}
function cell(text, cls) {
  const td = document.createElement('td');
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}
function emptyTable(target, columns, message) {
  const tr = document.createElement('tr');
  const td = cell(message, 'empty-cell');
  td.colSpan = columns;
  tr.append(td);
  $(target).replaceChildren(tr);
}
function svgElement(tag, attrs) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}
function drawChart(daily) {
  const eventMode = $('#trend-metric').value === 'events';
  const primary = eventMode ? 'events' : 'page_views';
  const secondary = eventMode ? 'orders' : 'visitors';
  $('#trend-title').textContent = eventMode ? 'Event trends' : 'Traffic over time';
  $('#legend-primary').textContent = eventMode ? 'Events' : 'Page views';
  $('#legend-secondary').textContent = eventMode ? 'Orders' : 'Visitors';
  const width = Math.max(260, Math.min(1000, $('#chart').clientWidth - 46));
  const height = 230, left = 36, bottom = 200, top = 16;
  const max = Math.max(4, ...daily.map(d => Math.max(d[primary], d[secondary])));
  const ceiling = Math.ceil(max / 4) * 4;
  const x = i => left + (i + .5) / daily.length * (width - left - 10);
  const y = n => bottom - n / ceiling * (bottom - top);
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, 'aria-hidden': 'true' });
  for (let i = 0; i <= 4; i++) {
    const py = y(ceiling * i / 4);
    svg.append(svgElement('line', { x1: left, y1: py, x2: width, y2: py, stroke: '#edf0eb', 'stroke-dasharray': '4 5' }));
    const label = svgElement('text', { x: left - 12, y: py + 4, 'text-anchor': 'end', fill: '#889184', 'font-size': 11 });
    label.textContent = fmt(ceiling * i / 4);
    svg.append(label);
  }
  const barWidth = Math.max(2, Math.min(22, 650 / daily.length));
  daily.forEach((d, i) => {
    for (const [key, color, offset] of [[primary, '#467950', -barWidth / 2], [secondary, '#c2d7b9', barWidth / 2]]) {
      const bar = svgElement('rect', { x: x(i) + offset - barWidth / 2, y: y(d[key]), width: barWidth - 1,
        height: bottom - y(d[key]), rx: 2, fill: color });
      const title = svgElement('title', {}); title.textContent = `${d.day}: ${d[key]} ${key}`; bar.append(title); svg.append(bar);
    }
    if (i % Math.ceil(daily.length / (width < 500 ? 4 : 10)) === 0 || i === daily.length - 1) {
      const label = svgElement('text', { x: x(i), y: 222, 'text-anchor': 'middle', fill: '#889184', 'font-size': 11 });
      label.textContent = d.day.slice(5); svg.append(label);
    }
  });
  $('#chart').replaceChildren(svg);
  $('#chart').setAttribute('aria-label', daily.map(d => `${d.day}: ${d[primary]} ${primary}, ${d[secondary]} ${secondary}`).join('; '));
  $('#chart-caption').textContent = daily.some(d => d.events) ? 'Hover over a bar to see daily counts.' : 'Your story starts here. Open the restaurant demo to send your first events.';
}
async function load() {
  const seq = ++requestSequence;
  const query = new URLSearchParams({ start: $('#start').value, end: $('#end').value });
  if ($('#event-filter').value) query.set('event_name', $('#event-filter').value);
  try {
    const token = sessionStorage.getItem('gather:dashboard-token');
    const response = await fetch(`/api/v1/analytics?${query}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (seq !== requestSequence) return;
    if (response.status === 401) { $('#auth').hidden = false; throw new Error('Enter your configured dashboard token to view analytics.'); }
    if (!response.ok) { const body = await response.json(); throw new Error(typeof body.detail === 'string' ? body.detail : 'Check the selected dates.'); }
    const data = await response.json();
    if (seq !== requestSequence) return;
    $('#error').hidden = true; $('#auth').hidden = true;
    for (const [key, value] of Object.entries(data.summary)) $(`#${key}`).textContent = fmt(value);
    latestDaily = data.daily;
    drawChart(latestDaily);
    $('#pages').replaceChildren();
    for (const page of data.pages) {
      const tr = document.createElement('tr'); tr.append(cell(page.path || '(unspecified)', 'path'), cell(fmt(page.views)), cell(fmt(page.visitors))); $('#pages').append(tr);
    }
    if (!data.pages.length) emptyTable('#pages', 3, 'No page views in this date range.');
    const selected = $('#event-filter').value;
    data.event_types.forEach(e => allEventNames.add(e.name));
    $('#event-filter').replaceChildren(new Option('All events', ''), ...[...allEventNames].sort().map(n => new Option(n, n)));
    $('#event-filter').value = selected;
    $('#event-types').replaceChildren();
    const maximum = Math.max(1, ...data.event_types.map(e => e.count));
    for (const event of data.event_types) {
      const row = document.createElement('div'); row.className = 'event-row';
      const name = document.createElement('span'); name.textContent = event.name;
      const source = document.createElement('small'); source.textContent = ` ${event.source}`; name.append(source);
      const count = document.createElement('strong'); count.textContent = fmt(event.count);
      const bar = document.createElement('progress'); bar.max = maximum; bar.value = event.count; bar.setAttribute('aria-label', `${event.name}: ${event.count}`);
      row.append(name, count, bar); $('#event-types').append(row);
    }
    if (!data.event_types.length) $('#event-types').textContent = 'No events yet. Try adding a dish to your cart.';
    $('#recent').replaceChildren();
    for (const event of data.recent) {
      const tr = document.createElement('tr');
      const source = cell(''); const badge = document.createElement('span'); badge.className = `source ${event.source}`; badge.textContent = event.source; source.append(badge);
      tr.append(cell(event.name, 'event-name'), source, cell(event.path || (event.properties.order_id ? `Order ${event.properties.order_id.slice(0, 8)}` : '—')), cell(new Date(event.occurred_at).toISOString().replace('T', ' ').slice(0, 19), 'time'));
      $('#recent').append(tr);
    }
    if (!data.recent.length) emptyTable('#recent', 4, 'Waiting for your first event. Explore the restaurant demo to get started.');
    $('#live-status').textContent = '● Updated just now';
  } catch (error) {
    if (seq !== requestSequence) return;
    $('#error').textContent = error.message; $('#error').hidden = false; $('#live-status').textContent = 'Updates paused';
  }
}
$('#filters').addEventListener('submit', e => { e.preventDefault(); document.querySelectorAll('.preset').forEach(b => b.classList.remove('selected')); load(); });
document.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.preset').forEach(b => b.classList.remove('selected')); button.classList.add('selected'); range(Number(button.dataset.days)); load();
}));
$('#event-filter').addEventListener('change', () => { if ($('#event-filter').value) $('#trend-metric').value = 'events'; load(); });
$('#trend-metric').addEventListener('change', () => { if (latestDaily.length) drawChart(latestDaily); });
window.addEventListener('resize', () => { if (latestDaily.length) drawChart(latestDaily); });
$('#refresh').addEventListener('click', load);
$('#auth').addEventListener('submit', e => { e.preventDefault(); sessionStorage.setItem('gather:dashboard-token', $('#token').value); $('#token').value = ''; load(); });
range(7); load(); setInterval(() => { if (!document.hidden && $('#auth').hidden) load(); }, 15000);
