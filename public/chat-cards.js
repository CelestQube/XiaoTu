// Only server-produced result types are supported. Data is always escaped.
const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rows = value => Array.isArray(value) ? value.filter(x => x && typeof x === 'object' && !Array.isArray(x)) : [];
const dateTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
function observedAt(value) {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    ? ' · ' + dateTime.format(new Date(value)) + ' 查询（北京时间）' : '';
}
function head(iconMarkup, hint, title) {
  return '<header class="chat-card-head"><span class="chat-card-icon">' + iconMarkup
    + '</span><div><span class="chat-card-kicker">' + hint + '</span><h3>' + esc(title) + '</h3></div></header>';
}
function weather(card, icon) {
  const days = rows(card.days).slice(0, 7);
  const temp = value => typeof value === 'number' && Number.isFinite(value)
    ? ' <strong class="chat-weather-temp">' + esc(value) + ' °C</strong>' : '';
  const forecasts = days.map(d => '<li><time datetime="' + esc(d.date) + '">' + esc(d.date) + '</time><dl>'
    + '<div><dt>白天</dt><dd>' + esc(d.day || '未返回') + temp(d.dayTemp) + '</dd></div>'
    + '<div><dt>夜间</dt><dd>' + esc(d.night || '未返回') + temp(d.nightTemp) + '</dd></div></dl></li>').join('');
  const notes = Array.isArray(card.advisory) ? card.advisory.filter(x => typeof x === 'string' && x).slice(0, 6) : [];
  return '<section class="chat-card chat-card-weather" aria-label="天气预报">'
    + head(icon('cloud'), '城市天气 · 预报', card.city || '天气预报')
    + (card.horizon ? '<p class="chat-card-sub">预报覆盖：' + esc(card.horizon) + '</p>' : '')
    + (days.length ? '<ul class="chat-weather-grid">' + forecasts + '</ul>'
      : '<p class="chat-card-empty">' + esc(card.requestedDate || '所问日期') + ' 未覆盖，不按其他日期推断。</p>')
    + (notes.length ? '<ul class="chat-card-advisory">' + notes.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>' : '')
    + '<footer class="chat-card-foot">' + esc(card.source || '腾讯地图 MCP') + esc(observedAt(card.checkedAt))
    + ' · 仅为预报</footer></section>';
}
const availability = value => {
  const v = String(value ?? '');
  if (/^\d{1,4}$/.test(v)) return Number(v) ? '余 ' + v + ' 张' : '无余票';
  return ['有','充足'].includes(v) ? '有票' : v === '无' ? '无余票'
    : ['候补','暂售','停售'].includes(v) ? v : '余票未返回';
};
const price = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2});
function train(row) {
  const seats = rows(row.seats).slice(0, 12).map(s => {
    const fare = typeof s.price === 'number' && Number.isFinite(s.price) && s.price > 0 ? '¥' + price.format(s.price) : '';
    return '<li>' + esc(s.name) + (fare ? ' <strong>' + esc(fare) + '</strong>' : '')
      + ' <span>· ' + esc(availability(s.availability)) + '</span></li>';
  }).join('');
  return '<div class="chat-train"><div class="chat-train-head"><strong class="chat-train-code">' + esc(row.code) + '</strong>'
    + '<strong>' + esc(row.from) + ' ' + esc(row.depart) + '</strong><span aria-hidden="true">→</span>'
    + '<strong>' + esc(row.to) + ' ' + esc(row.arrive) + '</strong>'
    + (row.duration ? '<small>历时 ' + esc(row.duration) + '</small>' : '') + '</div>'
    + (seats ? '<ul class="chat-train-seats">' + seats + '</ul>' : '<p class="chat-card-sub">上游未返回票价或余票。</p>') + '</div>';
}
function rail(card, icon) {
  const action = card.action;
  const items = rows(card.items);
  let title, label, render, limit = 4;
  switch (action) {
    case 'tickets':
      title = card.origin && card.destination ? card.origin + ' → ' + card.destination : '直达车次';
      label = '铁路 · 直达'; render = train; break;
    case 'transfer':
      title = card.origin && card.destination ? card.origin + ' → ' + card.destination : '一次换乘';
      label = '铁路 · 一次换乘'; limit = 2;
      render = row => '<p class="chat-transfer"><strong>经 ' + esc(row.middle) + ' 换乘</strong>'
        + (row.nextStation ? ' · 换站至 ' + esc(row.nextStation) : '') + (row.wait ? ' · 等候 ' + esc(row.wait) : '')
        + (row.duration ? ' · 总历时 ' + esc(row.duration) : '') + '</p>' + rows(row.segments).slice(0, 2).map(train).join('');
      break;
    case 'stops':
      title = card.code ? card.code + ' 经停站' : '列车经停站'; label = '铁路 · 经停'; limit = 6;
      render = row => '<div class="chat-rail-stop"><strong>' + esc(row.name) + '</strong><span>到 ' + esc(row.arrive || '—')
        + ' · 发 ' + esc(row.depart || '—') + (row.day ? ' · ' + esc(row.day) : '') + '</span></div>';
      break;
    case 'stations':
      title = card.keyword ? card.keyword + ' · 车站' : '铁路车站'; label = '铁路 · 车站'; limit = 6;
      render = row => '<div class="chat-rail-stop"><strong>' + esc(row.name) + '</strong><span>'
        + esc(row.code || '未返回代码') + '</span></div>';
      break;
    default: return '';
  }
  const list = listItems => '<ol class="chat-rail-list">' + listItems.map(row => '<li>' + render(row) + '</li>').join('') + '</ol>';
  return '<section class="chat-card chat-card-rail" aria-label="铁路查询结果">' + head(icon('train'), label, title)
    + (card.date ? '<p class="chat-card-sub">出发日期：<time datetime="' + esc(card.date) + '">' + esc(card.date) + '</time></p>' : '')
    + (items.length ? list(items.slice(0, limit)) : '<p class="chat-card-empty">没有查到符合条件的结果，不代表停运或售罄。</p>')
    + (items.length > limit ? '<details class="chat-card-more"><summary>展开其余 ' + (items.length - limit) + ' 条结果</summary>'
      + list(items.slice(limit)) + '</details>' : '')
    + '<footer class="chat-card-foot"><span>' + esc(card.source || '铁路 12306 · 第三方 MCP') + esc(observedAt(card.checkedAt)) + '</span>'
    + '<a href="https://www.12306.cn/" target="_blank" rel="noopener noreferrer">去铁路 12306 核实 ↗</a></footer></section>';
}
export function renderChatCards(cards, icon = () => '') {
  return rows(cards).slice(0, 3).map(card =>
    card.type === 'weather' ? weather(card, icon) : card.type === 'rail' ? rail(card, icon) : '').join('');
}
