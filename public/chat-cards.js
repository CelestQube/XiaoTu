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
const fen = value => Number.isInteger(value) && value >= 0
  ? '¥' + (value / 100).toFixed(2) : '价格未返回';
const foodStatus = value => ({succeeded: '已创建（待支付）', pending: '处理中，请勿重复操作', failed: '未创建',
  awaiting_confirmation: '等待确认', unknown: '结果待核对，请勿重新下单'}[value] || '状态未知');
function foodRows(card, key, render, empty) {
  const list = rows(card[key]);
  return list.length
    ? '<ul class="food-item-list">' + list.slice(0, 12).map(render).join('') + '</ul>'
    : '<p class="chat-card-empty">' + (empty || '没有查到结果。') + '</p>';
}
function foodMenu(card, icon) {
  const groups = rows(card.categories);
  return '<section class="chat-card chat-card-food" aria-label="麦当劳菜单">'
    + head(icon('fork'), '麦当劳 · 麦乐送菜单', card.storeName || '菜单')
    + (groups.length ? groups.map(group => '<div class="food-group"><strong class="food-group-name">'
      + esc(group.name) + '</strong><ul class="food-item-list">'
      + rows(group.items).slice(0, 12).map(item => '<li><span>' + esc(item.name) + '</span><b>' + fen(item.priceFen)
        + (item.originalPriceFen ? ' <s>' + fen(item.originalPriceFen) + '</s>' : '') + '</b></li>').join('')
      + '</ul></div>').join('') : '<p class="chat-card-empty">没有查到可售餐品。</p>')
    + '<footer class="chat-card-foot">' + esc(card.source || '麦当劳 MCP') + ' · 说「来一份巨无霸」即可报价</footer></section>';
}
function foodTotals(card) {
  return '<dl class="food-totals"><div><dt>餐品</dt><dd>' + fen(card.productFen) + '</dd></div>'
    + '<div><dt>配送费</dt><dd>' + fen(card.deliveryFen) + '</dd></div>'
    + (Number.isInteger(card.packingFen) && card.packingFen > 0
      ? '<div><dt>打包费</dt><dd>' + fen(card.packingFen) + '</dd></div>' : '')
    + (Number.isInteger(card.discountFen) ? '<div><dt>优惠</dt><dd>-' + fen(card.discountFen) + '</dd></div>' : '')
    + '<div class="food-pay"><dt>应付</dt><dd>' + fen(Number.isInteger(card.totalFen) ? card.totalFen : card.payFen)
    + '</dd></div></dl>';
}
function foodQuote(card, icon) {
  return '<section class="chat-card chat-card-food" aria-label="麦当劳外卖报价">'
    + head(icon('fork'), '麦当劳 · 报价单', card.storeName || '外卖报价')
    + '<p class="chat-card-sub">配送到 ' + esc(card.addressText || '所选收货地址') + '</p>'
    + foodRows(card, 'items', item => '<li><span>' + esc(item.name) + ' × ' + esc(item.quantity)
      + '</span><b>' + fen(item.priceFen * item.quantity) + '</b></li>', '报价未包含餐品明细。')
    + foodTotals(card)
    + (typeof card.expiresAt === 'string' && Number.isFinite(Date.parse(card.expiresAt))
      ? '<p class="chat-card-sub">报价到期：' + esc(new Date(card.expiresAt).toLocaleString('zh-CN', {hour12: false})) + '</p>' : '')
    + '<div class="food-actions">'
    + (card.orderable === false ? '' : '<button class="primary small" data-action="food-confirm" data-food-quote="'
      + esc(card.quoteId) + '">确认下单</button>')
    + '<button class="outline small" data-action="food-status" data-food-quote="' + esc(card.quoteId)
    + '">查询状态</button></div>'
    + '<footer class="chat-card-foot">确认后才会向麦当劳提交订单；创建的是真实待支付订单，支付由你自己完成。</footer></section>';
}
function foodOrder(card, icon) {
  return '<section class="chat-card chat-card-food" aria-label="麦当劳订单">'
    + head(icon('fork'), '麦当劳 · 订单', card.storeName || '订单详情')
    + '<p class="food-order-status">' + esc(foodStatus(card.status))
    + (card.orderStatus ? ' · ' + esc(card.orderStatus) : '') + '</p>'
    + (card.amountMismatch ? '<p class="food-warning">注意：实际金额与报价不一致（' + fen(card.totalFen)
      + '）。请核对后再支付，也可以在麦当劳 App 取消。</p>' : '')
    + (rows(card.items).length ? foodRows(card, 'items', item => '<li><span>' + esc(item.name) + ' × '
      + esc(item.quantity) + '</span></li>') : '')
    + foodTotals(card)
    + (card.orderId ? '<p class="food-order-id">订单号 <code>' + esc(card.orderId) + '</code></p>' : '')
    + (card.pickupCode ? '<p class="food-order-id">取餐码 <code>' + esc(card.pickupCode) + '</code></p>' : '')
    + '<div class="food-actions">'
    + (typeof card.payUrl === 'string' && /^https:\/\//.test(card.payUrl)
      ? '<a class="primary small" href="' + esc(card.payUrl) + '" target="_blank" rel="noopener noreferrer">去支付 ↗</a>' : '')
    + (typeof card.route === 'string' && card.route.startsWith('#/order/')
      ? '<button class="outline small" data-action="open-order" data-route="' + esc(card.route) + '">查看订单详情</button>' : '')
    + (card.quoteId ? '<button class="text-button" data-action="food-status" data-food-quote="' + esc(card.quoteId)
      + '">刷新状态</button>' : '')
    + '</div><footer class="chat-card-foot">支付在麦当劳自己的页面完成，这里不会代你支付；订单进度以麦当劳 App 为准。</footer></section>';
}
function foodAddresses(card, icon) {
  return '<section class="chat-card chat-card-food" aria-label="麦当劳收货地址">'
    + head(icon('pin'), '麦当劳 · 收货地址', '账号内的地址')
    + foodRows(card, 'addresses', row => '<li><span>' + esc(row.address)
      + (row.addressDetail ? '（' + esc(row.addressDetail) + '）' : '') + '</span></li>', '账号里还没有收货地址。')
    + '<footer class="chat-card-foot">不含联系方式；新增地址请在麦当劳 App 内完成。</footer></section>';
}
function foodStores(card, icon) {
  return '<section class="chat-card chat-card-food" aria-label="麦当劳可配送门店">'
    + head(icon('pin'), '麦当劳 · 可配送门店', card.addressText || '可配送门店')
    + foodRows(card, 'stores', row => '<li><span><strong>' + esc(row.storeName) + '</strong>'
      + (row.address ? '<br>' + esc(row.address) : '')
      + '<small>' + esc(row.businessStatus || '')
      + (row.businessStartTime ? ' ' + esc(row.businessStartTime) + '–' + esc(row.businessEndTime || '') : '')
      + '</small></span></li>', '这个地址暂无可配送门店。')
    + '<footer class="chat-card-foot">' + esc(card.source || '麦当劳 MCP') + '</footer></section>';
}
function foodCoupons(card, icon) {
  return '<section class="chat-card chat-card-food" aria-label="麦当劳优惠券">'
    + head(icon('wallet'), '麦当劳 · 优惠券', card.storeName || '当前门店')
    + foodRows(card, 'coupons', row => '<li><span>' + esc(row.title)
      + (row.description ? '<small>' + esc(row.description) + '</small>' : '') + '</span></li>', '暂无可用优惠券。')
    + '<footer class="chat-card-foot">本版下单暂不自动用券，可在麦当劳 App 核实用券条件。</footer></section>';
}
function foodOrders(card, icon) {
  return '<section class="chat-card chat-card-food" aria-label="麦当劳最近订单">'
    + head(icon('fork'), '麦当劳 · 最近订单', '账号内的订单')
    + foodRows(card, 'orders', row => '<li><span><strong>' + esc(row.orderStatus || '状态未返回') + '</strong> · '
      + esc(row.storeName || '') + '<small>' + esc(row.createTime || '') + '</small></span>'
      + (Number.isInteger(row.totalFen) ? '<b>' + fen(row.totalFen) + '</b>' : '') + '</li>', '最近没有订单。')
    + '<footer class="chat-card-foot">订单进度以麦当劳 App 为准。</footer></section>';
}
export function renderChatCards(cards, icon = () => '') {
  return rows(cards).slice(0, 3).map(card =>
    card.type === 'weather' ? weather(card, icon)
    : card.type === 'rail' ? rail(card, icon)
    : card.type === 'food-menu' ? foodMenu(card, icon)
    : card.type === 'food-quote' ? foodQuote(card, icon)
    : card.type === 'food-order' ? foodOrder(card, icon)
    : card.type === 'food-addresses' ? foodAddresses(card, icon)
    : card.type === 'food-stores' ? foodStores(card, icon)
    : card.type === 'food-coupons' ? foodCoupons(card, icon)
    : card.type === 'food-orders' ? foodOrders(card, icon)
    : '').join('');
}
