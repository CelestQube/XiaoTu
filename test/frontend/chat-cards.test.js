import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {renderChatCards} from '../../public/chat-cards.js';

const train = {
  code:'G42',from:'上海虹桥',to:'杭州东',depart:'09:02',arrive:'09:55',duration:'00:53',
  seats:[{name:'二等座',price:69,availability:'4'},{name:'一等座',availability:'候补'}],
};

test('weather displays day, night, source and advice without rendering untrusted HTML', () => {
  const card = {type:'weather',city:'杭州 <img>',horizon:'2026-11-18 至 2026-11-20',
    days:[{date:'2026-11-19',day:'小雨',night:'阴',dayTemp:0,nightTemp:-2}],advisory:['请核对气象部门预警<script>'],
    source:'腾讯地图 MCP · 天气预报',checkedAt:'2026-11-18T07:00:00Z'};
  const html = renderChatCards([card, {type:'other'}]);
  for (const field of ['天气预报','杭州 &lt;img&gt;','白天','小雨','夜间','阴','0 °C','-2 °C','腾讯地图 MCP','北京时间','请核对气象部门预警']) {
    assert.ok(html.includes(field), field);
  }
  assert.doesNotMatch(html, /<img|<script>/);
  const noData = renderChatCards([{...card,requestedDate:'2026-12-25',days:[]}]);
  assert.match(noData, /2026-12-25 未覆盖/);
  assert.doesNotMatch(noData, /小雨/);
});

test('rail cards show verified seats, extra results, and an official link', () => {
  const html = renderChatCards([{type:'rail',action:'tickets',origin:'上海',destination:'杭州',
    date:'2026-11-19',items:Array(5).fill(train),checkedAt:'2026-11-18T07:00:00Z'}]);
  for (const field of ['上海 → 杭州','09:02','G42','¥69','余 4 张','候补','展开其余 1 条','https://www.12306.cn/']) {
    assert.ok(html.includes(field), field);
  }
  assert.match(renderChatCards([{type:'rail',action:'tickets',items:[]}]), /不代表停运或售罄/);
  const transfer = renderChatCards([{type:'rail',action:'transfer',
    items:[{middle:'南京南',nextStation:'南京站',wait:'30分钟',segments:[train,{...train,code:'D89',seats:[]}]}]}]);
  assert.match(transfer, /经 南京南 换乘/);
  assert.match(transfer, /换站至 南京站/);
  assert.match(transfer, /D89/);
  assert.match(transfer, /上游未返回票价或余票/);
  assert.match(renderChatCards([{type:'rail',action:'stations',items:[{name:'杭州东',code:'HGH'}]}]), /HGH/);
  assert.match(renderChatCards([{type:'rail',action:'stops',items:[{name:'杭州东',depart:'12:00'}]}]), /到 — · 发 12:00/);
});

test('chat entry wires cards to lookup messages and gates itinerary writes on plan intent', async () => {
  const app = await readFile(new URL('../../public/app.js',import.meta.url),'utf8');
  const html = await readFile(new URL('../../public/index.html',import.meta.url),'utf8');
  assert.match(app, /renderChatCards\(m.cards, icon\)/);
  assert.match(app, /cards:data.intent === 'lookup' \? data.cards : undefined/);
  assert.match(app, /const updatingTrip = data.intent === 'plan'/);
  assert.match(app, /if \(updatingTrip && data.trip\) state.trip/);
  assert.match(html, /chat-cards\.css/);
});
