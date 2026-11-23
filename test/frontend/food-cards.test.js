import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {renderChatCards} from '../../public/chat-cards.js';

const quote = {type:'food-quote', quoteId:'quote_' + 'a'.repeat(32), storeName:'麦当劳南京西路店',
  addressText:'上海市黄浦区（某酒店前台）',
  items:[{productCode:'M1', name:'巨无霸', quantity:2, priceFen:2200}],
  productFen:4400, deliveryFen:900, discountFen:200, payFen:5100,
  expiresAt:'2026-11-20T12:05:00Z', orderable:true, source:'麦当劳 MCP'};
const menu = {type:'food-menu', storeName:'麦当劳南京西路店',
  categories:[{name:'汉堡类', items:[{code:'M1', name:'巨无霸', priceFen:2200, originalPriceFen:2500}]}],
  source:'麦当劳 MCP'};
const order = {type:'food-order', status:'succeeded', orderStatus:'待支付', quoteId:quote.quoteId,
  storeName:'麦当劳南京西路店', items:quote.items, productFen:4400, deliveryFen:900, payFen:5100,
  totalFen:5100, orderId:'MCD0001', payUrl:'https://pay.example/MCD0001', route:'#/order/' + quote.quoteId};
const noHtml = html => assert.doesNotMatch(html, /<img|<script/);

test('food menu card lists priced dishes without images', () => {
  const html = renderChatCards([menu], () => '<i></i>');
  assert.match(html, /麦当劳 · 麦乐送菜单/);
  assert.match(html, /巨无霸/);
  assert.match(html, /¥22\.00/);
  assert.match(html, /<s>¥25\.00<\/s>/);
  noHtml(html);
});

test('food quote card shows amounts, expiry and explicit confirm action', () => {
  const html = renderChatCards([quote], () => '<i></i>');
  assert.match(html, /麦当劳 · 报价单/);
  assert.match(html, /巨无霸 × 2/);
  assert.match(html, /配送费/);
  assert.match(html, /¥51\.00/);
  assert.match(html, /确认后才会向麦当劳提交订单/);
  assert.match(html, new RegExp('data-action="food-confirm" data-food-quote="' + quote.quoteId + '"'));
  assert.match(html, /data-action="food-status"/);
  noHtml(html);
});

test('food order card maps statuses, pay link and the order route', () => {
  const placed = renderChatCards([order], () => '<i></i>');
  assert.match(placed, /已创建（待支付）/);
  assert.match(placed, /订单号 <code>MCD0001<\/code>/);
  assert.match(placed, /href="https:\/\/pay\.example\/MCD0001"/);
  assert.match(placed, new RegExp('data-route="#/order/' + quote.quoteId + '"'));
  noHtml(placed);
  const unknown = renderChatCards([{...order, status:'unknown', payUrl:undefined, orderId:undefined}],
    () => '<i></i>');
  assert.match(unknown, /结果待核对，请勿重新下单/);
  const mismatch = renderChatCards([{...order, amountMismatch:true, totalFen:5600}], () => '<i></i>');
  assert.match(mismatch, /实际金额与报价不一致/);
});

test('hostile card text stays escaped', () => {
  const hostile = renderChatCards([{...menu, storeName:'<script>alert(1)</script>',
    categories:[{name:'<img src=x onerror=1>', items:[{code:'M1', name:'<b>汉堡</b>', priceFen:1}]}]}],
    () => '<i></i>');
  assert.doesNotMatch(hostile, /<script|<img|<b>汉堡/);
  assert.match(hostile, /&lt;script&gt;/);
  assert.match(hostile, /&lt;img src=x onerror=1&gt;/);
});

test('app wires the delivery suggestion, confirm flow and the order route', async () => {
  const app = await readFile(new URL('../../public/app.js', import.meta.url), 'utf8');
  const html = await readFile(new URL('../../public/index.html', import.meta.url), 'utf8');
  assert.match(app, /label: '帮我点份外卖'/);
  assert.match(app, /case 'food-confirm': void confirmFoodQuote\(target.dataset.foodQuote\); break;/);
  assert.match(app, /\/\^#\\\/order\\\/\(quote_\[a-f0-9\]\{32\}\)\$\/\.exec\(location\.hash/);
  assert.match(app, /if \(receipt\?\.route\) location\.hash = receipt\.route;/);
  assert.match(app, /state\.view === 'food-order' \? orderPage\(\)/);
  assert.match(html, /food-cards\.css/);
});
