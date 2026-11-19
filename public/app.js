// Small, buildless view layer. All external text is escaped before rendering.
import {mountTripMap,clearTripMap,focusTripMap,namedStop} from "./trip-map.js";
import {browserPosition,departureCity} from './location.js';
import {editPlace} from './place-editor.js';
import {openRailDialog} from './rail-query.js';
import {departureFields,departureMarkup,bindDeparture} from './departure.js';
import {foodOrderEntry,openFoodOrder,clearFoodOrderStorage} from './food-order.js';
import {renderChatCards} from './chat-cards.js';
const $ = (selector) => document.querySelector(selector);
const esc = (value = '') => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const money = (value = 0) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(Number(value) || 0);
const key = 'xiaotu.workspace.v1';
let stored = {};
try { stored = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch { /* Corrupt storage must not prevent startup. */ }
const state = {
  view: 'chat', input: '', messages: [], busy: false, status: null, statusError: false,
  profile: stored.profile && typeof stored.profile === 'object' ? stored.profile : {},
  trip: stored.trip && typeof stored.trip === 'object' ? stored.trip : {},
  plan: Array.isArray(stored.plan?.days) && Array.isArray(stored.plan?.budget) ? stored.plan : null,
  expenses: Array.isArray(stored.expenses) ? stored.expenses : [],
  planMode: stored.planMode || 'demo',
  sources: [], places: [], suggestion: null, weather: '', route: '',
  useProfile: true, marqueePaused: false,
};
let requestController;
let travelUi;
const triedTravel=new WeakSet();
const paths = {
  chat: '<path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l-3 3V11.5A7.5 7.5 0 0 1 9.5 4h3a7.5 7.5 0 0 1 7.5 7.5Z"/><path d="M7 10h8M7 14h5"/>',
  map: '<path d="m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5Z"/><path d="M9 3v16M15 5v16"/>',
  wallet: '<rect x="3" y="5" width="18" height="15" rx="3"/><path d="M16 10h5v5h-5a2.5 2.5 0 0 1 0-5ZM3 8V5l14-3v3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  leaf: '<path d="M20 3C6 2 2 8 5 15s15 6 15-12ZM4 21l11-12"/>',
  arrow: '<path d="m8 4 8 8-8 8"/>',
  send: '<path d="m12 19 0-14M5 12l7-7 7 7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1"/>',
  plug: '<path d="M8 3v5M16 3v5M6 8h12v4a6 6 0 0 1-12 0V8ZM12 18v4"/>',
  pin: '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
  train: '<rect x="5" y="3" width="14" height="15" rx="4"/><path d="M5 11h14M9 3v8M15 3v8M8 18l-2 3M16 18l2 3M8 15h1M15 15h1"/>',
  fork: '<path d="M5 3v6a3 3 0 0 0 6 0V3M8 3v18M18 3c-4 4-4 9 0 9h1M19 3v18"/>',
  bed: '<path d="M3 18V7M21 18v-7H3M3 14h18M7 7h5v4"/>',
  cloud: '<path d="M6 18a5 5 0 1 1 1-10 6 6 0 0 1 11 2 4 4 0 1 1 0 8H6Z"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  edit: '<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14v6Z"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
};
const icon = (name, cls = '') => '<svg class="icon ' + cls + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || paths.leaf) + '</svg>';
const suggestions = [
  { icon: 'map', label: '安排一趟松弛的旅行', prompt: '我想从上海去杭州玩3天，2个人，总预算3000元，喜欢自然风景，帮我安排轻松一些的行程。' },
  { icon: 'fork', label: '跟着味蕾去旅行', prompt: '想去成都玩3天，喜欢本地美食，不想赶路，帮我推荐美食和周边景点。' },
  { icon: 'bed', label: '找一间刚好的酒店', prompt: '计划去厦门玩3天，两个人，希望住在方便出行的地方，每晚住宿预算400元，请帮我规划。' },
  { icon: 'wallet', label: '让预算心里有数', prompt: '从上海出发去北京玩4天，两人总预算5000元，请帮我分配住宿、餐饮、景点和交通预算。' },
  { icon: 'train', label: '想个周末出逃计划', prompt: '我住在上海，周末想坐高铁去杭州玩2天，喜欢散步和湖边风景，预算1500元。' },
  { icon: 'cloud', label: '看看目的地天气', prompt: '杭州明天天气怎么样？' },
  { icon: 'train', label: '查车次、票价和余票', prompt: '查一下明天上海到杭州的高铁票。' },
];
function persist() {
  try { localStorage.setItem(key, JSON.stringify({ profile: state.profile, trip: state.trip, plan: state.plan, expenses: state.expenses, planMode: state.planMode })); }
  catch { toast('浏览器未能保存数据；当前页面仍可继续使用。'); }
}
function toast(message) {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove('show'), 4500);
}
function navigate(view) { state.view = view; render(); $('#main')?.focus({ preventScroll: true }); if(view==='itinerary')autoDeparture(); }
function badge(mode) { return mode === 'qwen' ? 'Qwen 对话' : '演示模式'; }
function placeBadge(item) {
  const status=item.mapStatus==='verified'&&(item.poi?.tripCity!==state.trip.city||item.poi?.name!==item.location)?'pending':item.mapStatus;
  const label = {verified:'地图已核实',pending:'待核实',placeholder:'待选'}[status];
  return (label ? '<span class="place-proof">'+label+'</span>' : '')+(item.locked?'<span class="place-proof place-retained" title="重新规划时保留此地点">已保留</span>':'');
}
function header() {
  const names = { chat: ['与小途聊聊', '从一个念头，到一段旅程'], itinerary: ['我的行程', '把每一天，安排得刚刚好'], budget: ['旅行账本', '花得明白，玩得自在'], profile: ['旅行偏好', '你的旅途，由你定义'], tools: ['数据与连接', '知道每条信息来自哪里'] };
  return '<header class="topbar"><div class="page-heading"><strong>' + names[state.view][0] + '</strong><span>' + names[state.view][1] + '</span></div><div class="top-actions">' + foodOrderEntry(state.statusError ? null : state.status) + '<span class="mode-badge ' + (state.statusError ? 'error' : '') + '"><i></i>' + (state.statusError ? '服务未连接' : state.status ? badge(state.status.mode) : '检查连接中') + '</span><button class="avatar" data-view="profile" aria-label="打开旅行偏好">' + icon('user') + '</button></div></header>';
}
function sidebar() {
  return '<aside class="sidebar"><a class="brand" href="#chat" aria-label="小途首页"><img src="/assets/logo-a.svg" alt="" width="42" height="42"><span>小途<small>随心出发 · XIAOTU</small></span></a><button class="new-chat" data-action="new" ' + (state.busy ? 'disabled' : '') + '>' + icon('plus') + ' 开始新旅程</button><nav aria-label="主要导航">' + [['chat','chat','与小途聊聊'],['itinerary','map','我的行程'],['budget','wallet','旅行账本'],['profile','user','旅行偏好']].map(([id,ico,name]) => '<button data-view="' + id + '" class="nav-item ' + (state.view === id ? 'active' : '') + '" ' + (state.view === id ? 'aria-current="page"' : '') + '>' + icon(ico) + '<span>' + name + '</span>' + (id === 'itinerary' && state.plan ? '<i class="nav-dot"></i>' : '') + '</button>').join('') + '</nav><div class="sidebar-note"><span class="eyebrow">下一站 · 不必匆忙</span><p>把时间留给风景，<br>把计划交给小途。</p><div class="mini-landscape" aria-hidden="true"></div></div><div class="sidebar-bottom"><button class="nav-item ' + (state.view === 'tools' ? 'active' : '') + '" data-view="tools">' + icon('plug') + '<span>数据与连接</span></button><a href="/logo-concepts.html" class="prototype-link">原型 v0.1 · Logo 待确认 ↗</a></div></aside>';
}
function emptyChat() {
  return '<div class="welcome"><div class="welcome-topline"><span></span> 给生活，留一点出走的时间 <span></span></div><div class="hero-mark"><img src="/assets/logo-a.svg" width="83" height="83" alt="小途暂定 Logo：一叶有途"><span class="leaf-orbit"></span></div><h1>把向往，<br>变成<span>下一站。</span></h1><p class="welcome-description">我是小途，你的旅行搭子。<br>告诉我你想去哪里，剩下的，我们慢慢安排。</p><div class="suggestion-heading"><span>不知道从哪聊起？试试这些</span><button class="text-button" data-action="marquee" aria-label="' + (state.marqueePaused ? '继续滚动建议' : '暂停滚动建议') + '">' + (state.marqueePaused ? '继续滚动' : '暂停滚动') + '</button></div><div class="suggestion-window"><div class="suggestion-track ' + (state.marqueePaused ? 'paused' : '') + '">' + [0,1].map(copy => '<div class="suggestion-set" ' + (copy ? 'aria-hidden="true"' : '') + '>' + suggestions.map((s,i) => '<button class="suggestion" data-prompt="' + i + '"' + (copy ? ' tabindex="-1"' : '') + '>' + icon(s.icon) + s.label + '</button>').join('') + '</div>').join('') + '</div></div><div class="hero-footnote">' + icon('leaf') + ' 不用一次说完，灵感可以慢慢来。</div></div>';
}
function messageList() {
  const avatar = '<img src="/assets/logo-a.svg" alt="" width="24" height="24">';
  const messages = state.messages.map(m => {
    const assistant = m.role === 'assistant';
    const planLink = m.hasPlan ? '<button class="plan-inline" data-view="itinerary">' + icon('map')
      + '<span><strong>行程草案已整理</strong><small>查看每日安排与预算估算</small></span>' + icon('arrow') + '</button>' : '';
    return '<article class="message ' + (assistant ? 'assistant' : 'user') + '"><div class="message-avatar">'
      + (assistant ? avatar : '我') + '</div><div class="message-body"><div class="message-author">' + (assistant ? '小途' : '你')
      + (m.mode ? '<span>' + badge(m.mode) + '</span>' : '') + '</div><div class="message-text">' + esc(m.content) + '</div>'
      + (assistant ? renderChatCards(m.cards, icon) : '') + planLink + '</div></article>';
  }).join('');
  const loading = state.busy ? '<article class="message assistant"><div class="message-avatar">' + avatar
    + '</div><div class="thinking"><span></span><span></span><span></span><small>小途正在准备回复…</small></div></article>' : '';
  const suggestion = state.suggestion ? '<div class="profile-suggestion"><strong>' + icon('leaf')
    + ' 这些偏好，要帮你记住吗？</strong><p>' + esc(profileSummary(state.suggestion))
    + '</p><small>仅保存到本浏览器，随时可修改或清除。</small><div><button class="primary small" data-action="accept-profile">确认保存</button>'
    + '<button class="text-button" data-action="dismiss-profile">这次就好</button></div></div>' : '';
  return '<div class="message-list" role="log" aria-label="与小途的对话" aria-live="polite">' + messages + loading + suggestion + '</div>';
}
function composer() {
  return '<div class="composer-wrap"><form id="chat-form" class="composer"><label for="message" class="sr-only">告诉小途你的旅行想法</label><textarea id="message" maxlength="1600" rows="2" placeholder="想去哪里？什么时候出发？或只是，想出去走走…" ' + (state.busy ? 'disabled' : '') + '>' + esc(state.input) + '</textarea><div class="composer-bottom"><span>' + icon('leaf') + ' 文字输入 <span class="composer-hint">· Enter 发送 / Shift + Enter 换行</span></span>' + (state.busy ? '<button type="button" class="stop-button" data-action="cancel">停止生成</button>' : '<button class="send-button" type="submit" aria-label="发送消息" ' + (!state.input.trim() ? 'disabled' : '') + '>' + icon('send') + '</button>') + '</div></form><div class="composer-disclaimer"><span>小途可能出错，价格、营业时间及余票请在出发前核实。</span><span class="input-count">' + state.input.length + '/1600</span></div></div>';
}
function overview() {
  const t = state.trip; const sum = state.plan?.budget.reduce((n,b) => n + (Number(b.high)||0), 0);
  return '<aside class="overview"><div class="overview-top"><span class="eyebrow">YOUR NEXT JOURNEY</span><h2>旅程，慢慢成形</h2></div><div class="postcard"><img src="/assets/coast.svg" alt="山海与绿叶的旅行插画"><div><span>给自己一段好时光</span><strong>' + esc(t.city || '下一站，由你决定') + '</strong></div></div><div class="overview-details"><div><span>' + icon('pin') + '目的地</span><strong>' + esc(t.city || '等你告诉小途') + '</strong></div><div><span>' + icon('sun') + '旅行时长</span><strong>' + (t.days ? esc(t.days) + ' 天' : '慢慢计划') + '</strong></div><div><span>' + icon('user') + '同行人数</span><strong>' + (t.people ? esc(t.people) + ' 人' : '待确认') + '</strong></div><div><span>' + icon('wallet') + '总预算</span><strong>' + (t.budget ? '¥ ' + money(t.budget) : '量力而行') + '</strong></div></div><button class="outline full" data-action="trip-details">' + icon('edit') + ' 补充旅行信息</button>' + (state.plan ? '<button class="primary full" data-view="itinerary">查看行程草案 ' + icon('arrow') + '</button><p class="fine-print">估算上限 ¥' + money(sum) + ' · 城际票价另行核实</p>' : '<div class="little-note">' + icon('cloud') + '<p>不必现在就有答案。<br>聊聊最近想看的风景吧。</p></div>') + '<div class="local-note">' + icon('check') + '<span>偏好与行程仅保存在本浏览器</span></div></aside>';
}
function emptyPage(kind) {
  return '<div class="empty-page">' + icon(kind === 'budget' ? 'wallet' : 'map') + '<h2>' + (kind === 'budget' ? '先有一段旅程，再算一笔小账' : '下一段旅程，还在酝酿') + '</h2><p>告诉小途目的地、天数与预算，<br>我们会把旅行想法整理成可调整的草案。</p><button class="primary" data-view="chat">去和小途聊聊 ' + icon('arrow') + '</button></div>';
}
function travelCard(plan){
  return '<section id="departure-card" class="panel travel-context departure-card">'+
    departureMarkup(state.trip,plan.travel,travelUi?.plan===plan?travelUi:undefined)+'</section>';
}
function departureControl(){
  const plan=state.plan,host=$('#departure-card');
  if(!host||!plan)return;
  const prev=departureFields(state.trip,plan.travel);
  bindDeparture(host,prev,{
    edit(draft){
      if(travelUi?.plan!==plan)travelUi={plan};
      travelUi.draft=draft;travelUi.open=true;
    },
    submit(fields){void refreshDeparture(plan,fields,{locate:!fields.origin.trim(),open:true});},
    locate(fields){void refreshDeparture(plan,fields,{locate:true,open:true});}
  });
}
function paintDeparture(){
  const host=$('#departure-card'),plan=state.plan;
  if(!host||!plan)return;
  host.innerHTML=departureMarkup(state.trip,plan.travel,travelUi?.plan===plan?travelUi:undefined);
  departureControl();
}
async function refreshDeparture(plan,fields,{locate=false,open=false,automatic=false}={}){
  if(state.plan!==plan||!fields.destination.trim())return;
  triedTravel.add(plan);
  travelUi?.controller?.abort();
  const controller=new AbortController();
  const ui=travelUi={plan,controller,draft:fields,open,pending:locate?'locating':'query'};
  const active=()=>state.plan===plan&&travelUi===ui&&!controller.signal.aborted;
  if(locate&&automatic&&state.view==='chat')toast('未填写出发地，正在尝试使用设备定位城市。');
  paintDeparture();
  let phase=locate?'locate':'query';
  try{
    let body=fields;
    if(locate){
      const city=await departureCity(controller.signal);
      if(!active())return;
      const edited=ui.draft.origin!==fields.origin&&ui.draft.origin.trim();
      body={...ui.draft,origin:edited?ui.draft.origin:city,originSource:edited?'manual':'device'};
      ui.draft=body;ui.pending='query';phase='query';paintDeparture();
    }
    const response=await fetch('/api/travel',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body),
      signal:AbortSignal.any([controller.signal,AbortSignal.timeout(63000)])});
    const data=await response.json();
    if(!response.ok||!data.request)throw new Error(data.error||'未取得交通查询结果。');
    if(!active())return;
    plan.travel=data;ui.draft=undefined;
    const live=['available','empty'].includes(data.rail?.status);
    state.sources=[...state.sources.filter(s=>!['铁路交通','铁路 12306 · 第三方 MCP'].includes(s.name)),
      {name:'铁路 12306 · 第三方 MCP',status:live?'live':'unavailable',note:data.rail?.note||'未取得铁路结果。'}];
    persist();
  }catch(e){
    if(!active())return;
    ui.error=(phase==='locate'?'未能获取定位城市：':'')+(e.name==='TimeoutError'?'查询超时，请重试。':e.message);
    ui.locationFailed=phase==='locate';if(ui.locationFailed)ui.open=true;
    if(ui.locationFailed&&automatic&&state.view==='chat')toast('未能获取定位。可在「出发与抵达」重试或手动填写。');
  }finally{if(active()){ui.pending='';paintDeparture();}}
}
function autoDeparture(){
  const plan=state.plan;
  if(!plan||triedTravel.has(plan))return;
  const fields=departureFields(state.trip,plan.travel);
  if(!fields.destination.trim())return;
  const locate=!fields.origin.trim();
  if(!locate&&plan.travel?.request){
    const stamp=Date.parse(plan.travel.generatedAt);
    if(fields.skipRail||fields.date&&fields.time||Number.isFinite(stamp)&&Date.now()-stamp<5*60000)return;
  }
  void refreshDeparture(plan,fields,{locate,automatic:true});
}
function forecastCard(plan){
  const w=plan.weather;
  if(!w)return '';
  const dates=w.suggestion;
  return '<section class="panel travel-context"><span class="eyebrow">WEATHER WINDOW</span><h2>天气与出发时机</h2>'
    +'<p>'+esc(w.summary)+'</p>'
    +(dates?'<p><strong>天气较合适的备选：</strong>'+esc(dates.start+(dates.end!==dates.start?' 至 '+dates.end:''))+'（未确认出发日期）</p>':'')
    +(w.advisory?.length?'<ul>'+w.advisory.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'')
    +'<small>仅按收到的白天／夜间预报安排，不等同于实时气象预警。</small></section>';
}
function locationActions(item,day,index) {
  if(item.location==='不指定地点'||item.placeKind==='other'&&!namedStop(item.location))return '';
  const key=day+','+index,label=item.mapStatus==='placeholder'?'选地点 · 推荐与搜索':'更换地点';
  return '<div class="timeline-place-actions"><button class="'+(item.mapStatus==='placeholder'?'outline small':'timeline-map-link')+'" data-place-edit="'+key+'">'+label+'</button>'
    +(namedStop(item.location)?'<button class="timeline-map-link" data-map-jump="'+key+'">地图与导航 ↗</button>':'')+'</div>';
}
function itinerary() {
  if (!state.plan) return emptyPage('itinerary');
  const p = state.plan;
  return '<div class="content-page"><div class="section-intro"><div><span class="eyebrow">A LITTLE PLAN, A BIG WORLD</span><h1>' + esc(p.title || '我的旅行草案') + '</h1><p>' + esc(state.trip.origin || '出发地待定') + ' → ' + esc(state.trip.city || '目的地待定') + ' · ' + p.days.length + ' 天 · ' + badge(state.planMode) + '</p></div><button class="outline" data-action="calendar">' + icon('download') + ' 导出日历</button></div><div class="notice">' + icon('leaf') + '<span>这是一份可调整的草案，不代表已预订。景点开放情况与所有价格请出发前复核。</span></div>' + travelCard(p) + forecastCard(p) + (!p.weather && (state.weather || state.route) ? '<div class="weather-note">' + icon('cloud') + '<p>' + esc([state.weather,state.route].filter(Boolean).join('\n')) + '</p></div>' : '') + '<section id="itinerary-map" class="trip-map" tabindex="-1" aria-label="行程地图"></section><div class="day-grid">' + p.days.map((day,di) => '<section class="day-card"><div class="day-heading"><span>DAY ' + String(day.day || di+1).padStart(2,'0') + '</span><h2>第 ' + (di+1) + ' 天</h2><small>' + dateForDay(di) + '</small></div><div class="timeline">' + (day.items || []).map((item,ii) => '<article class="timeline-item"><time>' + esc(item.time) + '</time><div><h3>' + esc(item.title) + '</h3><p class="location">' + icon('pin') + esc(item.location) + '<span data-place-proof="' + di + ',' + ii + '">' + placeBadge(item) + '</span></p><p>' + esc(item.note) + '</p>' + locationActions(item,di,ii) + '<details><summary>调整这项安排</summary><form class="item-form" data-day="' + di + '" data-item="' + ii + '"><label>时间<input type="time" name="time" value="' + esc(item.time) + '" required></label><label>安排<input name="title" value="' + esc(item.title) + '" required maxlength="90"></label><label>备注<textarea name="note" maxlength="180">' + esc(item.note) + '</textarea></label><button class="primary small">保存调整</button></form></details></div></article>').join('') + '</div></section>').join('') + '</div>' + (state.places.length ? '<h2 class="section-title">地点参考</h2><div class="place-grid">' + state.places.map(place => '<article class="place-card"><span class="eyebrow">' + esc(place.type || '地点') + '</span><h3>' + esc(place.name) + '</h3><p>' + esc(place.address) + '</p><small>' + esc(place.source || '来源待核实') + (place.rating ? ' · 评分 ' + esc(place.rating) : '') + (place.cost ? ' · 人均 ' + esc(place.cost) : '') + '</small>' + (place.hours ? '<p>营业信息：' + esc(place.hours) + '（到店前核实）</p>' : '') + '</article>').join('') + '</div>' : '') + '<div class="notes"><h3>出发前，再确认一下</h3><ul>' + (p.notes || []).map(note => '<li>' + esc(note) + '</li>').join('') + '</ul><p>出行日期：' + esc(state.trip.startDate || '未确定；导出日历前需确认') + '</p></div><div class="page-actions"><button class="outline" data-action="refine">继续和小途调整</button><button class="text-button danger" data-action="clear-plan">' + icon('trash') + ' 清除本次行程与账本</button></div></div>';
}
function dateForDay(offset) {
  if (!state.trip.startDate || !/^\d{4}-\d{2}-\d{2}$/.test(state.trip.startDate)) return '日期待定';
  const date = new Date(state.trip.startDate + 'T12:00:00');
  date.setDate(date.getDate() + offset);
  return date.toLocaleDateString('zh-CN', { month:'long',day:'numeric' });
}
function budget() {
  if (!state.plan) return emptyPage('budget');
  const rows = state.plan.budget;
  const low = rows.reduce((n,b) => n + (Number(b.low)||0),0), high = rows.reduce((n,b) => n + (Number(b.high)||0),0);
  const spent = state.expenses.reduce((n,e) => n + (Number(e.amount)||0),0), cap = Number(state.trip.budget)||0;
  return '<div class="content-page"><div class="section-intro"><div><span class="eyebrow">SPEND ON GOOD MEMORIES</span><h1>快乐有计划，花费有分寸。</h1><p>以下是整段旅程的预算区间，并非实时商家报价。</p></div></div><div class="budget-stats"><article><span>旅行总预算</span><strong>' + (cap ? '¥ ' + money(cap) : '待设置') + '</strong><button class="text-button" data-action="set-budget">调整预算</button></article><article><span>已估算部分</span><strong>¥ ' + money(low) + '–' + money(high) + '</strong><small>不含待核实城际票价</small></article><article><span>实际已记账</span><strong>¥ ' + money(spent) + '</strong><small>' + (cap ? '预算' + (spent > cap ? '超出 ¥' + money(spent-cap) : '剩余 ¥' + money(cap-spent)) : '设置总预算后显示余额') + '</small></article></div>' + (cap && high > cap ? '<div class="notice warning">估算上限已超过预算 ¥' + money(high-cap) + '，且尚未计入城际交通。建议调整住宿或旅行天数。</div>' : '') + '<div class="budget-layout"><section class="panel"><h2>费用怎么分配？</h2><div class="stacked-bar" aria-hidden="true">' + rows.map((row,i) => '<span style="width:' + (high ? Number(row.high)/high*100 : 0) + '%;background:var(--chart-' + i%5 + ')"></span>').join('') + '</div><div class="budget-rows">' + rows.map((row,i) => '<div class="budget-row"><span class="category"><i style="background:var(--chart-' + i%5 + ')"></i>' + esc(row.category) + '</span><strong>¥' + money(row.low) + '–' + money(row.high) + '</strong><p>' + esc(row.note) + '</p></div>').join('') + '</div></section><section class="panel"><h2>记下一笔花费</h2><p class="muted">仅记录实际支付，计划与账本分开保存。</p><form id="expense-form" class="field-form"><label>消费内容<input name="title" placeholder="例如：今日午餐" maxlength="60" required></label><div class="form-row"><label>金额 / 元<input name="amount" type="number" min="0.01" max="1000000" step="0.01" placeholder="0.00" required></label><label>分类<select name="category"><option>餐饮</option><option>住宿</option><option>交通</option><option>景点</option><option>其他</option></select></label></div><button class="primary">保存这笔花费 ' + icon('plus') + '</button></form><div class="expense-list">' + (state.expenses.length ? state.expenses.map((e,i) => '<div class="expense"><span><strong>' + esc(e.title) + '</strong><small>' + esc(e.category) + '</small></span><b>¥' + money(e.amount) + '</b><button class="icon-button" data-remove-expense="' + i + '" aria-label="删除' + esc(e.title) + '">' + icon('close') + '</button></div>').join('') : '<p class="fine-print">还没有记录，旅途中随手记一笔吧。</p>') + '</div></section></div></div>';
}
function profileSummary(p) { return [p.homeCity ? '常居 ' + p.homeCity : '',p.dailyBudget ? '日均预算 ¥' + p.dailyBudget : '',p.transport ? '偏好 ' + p.transport : '',Array.isArray(p.interests) ? p.interests.join('、') : ''].filter(Boolean).join(' · ') || '暂未保存旅行偏好'; }
function profile() {
  const p = state.profile;
  return '<div class="content-page narrow"><div class="section-intro"><div><span class="eyebrow">TRAVEL YOUR OWN WAY</span><h1>小途，想更懂你的旅途。</h1><p>只记住你愿意分享的偏好，不猜测收入或身份。</p></div></div><div class="privacy-note">' + icon('leaf') + '<div><strong>你的偏好，你说了算。</strong><p>仅保存在本浏览器；启用后会随对话发送给服务端及所配置的模型。对话中的新偏好，须经你确认后才保存。</p></div></div><form id="profile-form" class="field-form panel"><div class="form-row"><label>常居城市<input name="homeCity" maxlength="30" value="' + esc(p.homeCity || '') + '" placeholder="例如：上海"></label><label>旅行日均预算 / 元<input name="dailyBudget" type="number" min="1" max="100000" value="' + esc(p.dailyBudget || '') + '" placeholder="选填"></label></div><label>偏好的交通方式<select name="transport">' + ['', '高铁','飞机','自驾','公共交通','骑行','步行'].map(v => '<option value="' + v + '" ' + (p.transport === v ? 'selected' : '') + '>' + (v||'暂不设置') + '</option>').join('') + '</select></label><label>旅行兴趣<input name="interests" maxlength="150" value="' + esc(Array.isArray(p.interests) ? p.interests.join('、') : '') + '" placeholder="美食、自然风景、骑行、登山…"><small>用顿号或逗号分隔，可以随时修改。</small></label><label class="checkbox-label"><input type="checkbox" id="use-profile" ' + (state.useProfile ? 'checked' : '') + '>本次会话使用已保存偏好辅助规划</label><div class="page-actions"><button class="primary">保存旅行偏好 ' + icon('check') + '</button><button type="button" class="text-button danger" data-action="clear-profile">清除已保存偏好</button></div></form><p class="fine-print">消费预算不等于收入水平。敏感信息、身份证和支付资料不需要告诉小途。</p></div>';
}
function toolsPage() {
  const s = state.status;
  const cards = [
    ['chat','对话模型',s ? (s.mode === 'qwen' ? 'Qwen 已配置' : '本地演示') : '连接待确认', '真实调用与演示模式会分别标注。模型凭据仅在服务端配置，浏览器不会接收密钥。'],
    ['pin','腾讯地图 MCP',s ? (s.mapMcp ? '已配置 · 以实际查询为准' : '未配置') : '连接待确认', '地点、天气、逆地理编码和分段路线通过 MCP 获取。行程底图使用腾讯静态地图；照片、评分与评论只展示实际返回的内容。'],
    ['train','铁路与城际交通',s?.rail==='12306-mcp'?({ready:'已收到查询结果',unavailable:'上次查询失败',unchecked:'已配置 · 待查询'}[s.railState]||'已配置'):'未启用', '通过第三方 12306 MCP 查询直达车票、一次换乘、经停和车站。只查询，不代购。<br><button class="outline" data-action="rail-query">打开铁路查询</button>'],
    ['map','日历','ICS 文件导出', '确认出行日期后下载日历文件，自行导入日历应用。当前不会直接写入或修改你的线上日历。'],
  ];
  return '<div class="content-page"><div class="section-intro"><div><span class="eyebrow">CLEAR SOURCES, BETTER CHOICES</span><h1>有来源，才放心出发。</h1><p>已配置不等于每次查询成功。每次对话的来源结果会单独记录。</p></div><button class="outline" data-action="refresh-status">重新检查连接</button></div><div class="integration-grid">' + cards.map(([ico,title,status,desc]) => '<article class="panel integration"><div class="integration-icon">' + icon(ico) + '</div><h2>' + title + '</h2><span class="subtle-badge">' + status + '</span><p>' + desc + '</p></article>').join('') + '</div><section class="panel source-panel"><h2>最近一次对话的数据来源</h2>' + (state.sources.length ? state.sources.map(source => '<div class="source-row"><strong>' + esc(source.name) + '</strong><span class="source-status">' + ({live:'实时调用',demo:'演示数据',unavailable:'暂不可用'}[source.status] || '待核实') + '</span><p>' + esc(source.note) + '</p></div>').join('') : '<p class="muted">还没有查询记录。发起一次对话后，这里会显示实际使用的来源。</p>') + '</section><div class="page-actions"><a class="outline" href="https://www.12306.cn/" target="_blank" rel="noopener noreferrer">前往 12306 官方网站 ↗</a><button class="text-button danger" data-action="clear-all">清除本浏览器全部小途数据</button></div></div>';
}
function render() {
  $('#app').innerHTML = sidebar() + '<div class="workspace">' + header() + (state.statusError ? '<div class="connection-error" role="alert">暂时无法连接本地服务，请确认服务已启动。<button data-action="refresh-status">重试</button></div>' : '') + '<main id="main" tabindex="-1" class="' + (state.view === 'chat' ? 'chat-layout' : 'page-main') + '">' + (state.view === 'chat' ? '<section class="chat-panel" aria-label="旅行规划对话"><div class="chat-scroll">' + (state.messages.length ? messageList() : emptyChat()) + '</div>' + composer() + '</section>' + overview() : state.view === 'itinerary' ? itinerary() : state.view === 'budget' ? budget() : state.view === 'profile' ? profile() : toolsPage()) + '</main></div><dialog id="dialog" aria-labelledby="dialog-title"></dialog>';
  bindForms();
  departureControl();
  const map = $('#itinerary-map');
  if (map) {
    mountTripMap(map,{trip:state.trip,plan:state.plan,configured:state.status?Boolean(state.status.mapMcp):null,
      onPlaceVerified(day,index,poi){
        const item=state.plan?.days?.[day]?.items?.[index];
        if(!item||item.location!==poi.name||state.trip.city!==poi.tripCity)return;
        item.mapStatus='verified';item.poi=poi;persist();
        const badge=document.querySelector('[data-place-proof="'+day+','+index+'"]');
        if(badge)badge.innerHTML=placeBadge(item);
      }});
    document.querySelectorAll('[data-map-jump]').forEach(el=>el.onclick=()=>focusTripMap(...el.dataset.mapJump.split(',').map(Number)));
  } else clearTripMap();
  if (state.messages.length) { const scroll = $('.chat-scroll'); if (scroll) scroll.scrollTop = scroll.scrollHeight; }
}
function openDialog(content) {
  const dialog = $('#dialog');
  dialog.innerHTML = '<button class="dialog-close icon-button" data-action="close-dialog" aria-label="关闭弹窗">' + icon('close') + '</button>' + content;
  dialog.showModal();
  return dialog;
}
function placeDialog(day,index) {
  const {plan,trip}=state;
  if(!plan?.days?.[day]?.items?.[index]||!trip.city)return;
  if(state.busy){toast('小途正在回复，请等这次更新完成后再调整。');return;}
  const snapshot=JSON.stringify(plan.days);
  editPlace({trip,plan,day,index,openDialog,
    stillCurrent:()=>!state.busy&&state.plan===plan&&state.trip===trip&&snapshot===JSON.stringify(plan.days),
    onSave(option){
      for(const {index:i,items} of option.days)plan.days[i]={...plan.days[i],items};
      plan.notes=[...(plan.notes||[]).filter(n=>!/^地图已核实 \d+ 项/.test(n)),option.summary];
      persist();render();
      const changed=option.days.find(d=>d.index===day)||option.days[0];
      const i=changed.items.findIndex(x=>x.locked&&x.location===option.location);
      document.querySelector('[data-place-edit="'+changed.index+','+(i>=0?i:Math.min(index,changed.items.length-1))+'"]')?.focus();
      toast('地点已更新，地图按新行程显示。');
    },
    onUnlock(){delete plan.days[day].items[index].locked;persist();render();toast('已取消保留，地点仍在当前行程。');}
  });
}
function tripDialog() {
  const t = state.trip;
  openDialog('<span class="eyebrow">A FEW LITTLE DETAILS</span><h2 id="dialog-title">这次，想怎样出发？</h2><p class="muted">无需一次填完。保存后会把信息填入输入栏，发送给小途即可更新行程。</p><form id="trip-form" class="field-form"><div class="form-row"><label>出发地<input name="origin" value="' + esc(t.origin || '') + '" maxlength="30" placeholder="例如：上海"></label><label>目的地<input name="city" value="' + esc(t.city || '') + '" maxlength="30" placeholder="例如：杭州"></label></div><label>出发日期<input name="startDate" type="date" value="' + esc(t.startDate || '') + '"></label><div class="form-row"><label>天数<input type="number" name="days" min="1" max="7" value="' + esc(t.days || '') + '"></label><label>人数<input type="number" name="people" min="1" max="20" value="' + esc(t.people || '') + '"></label></div><label>全程总预算 / 元<input type="number" name="budget" min="1" max="10000000" value="' + esc(t.budget || '') + '"></label><button class="primary">填入对话 ' + icon('arrow') + '</button></form>');
  $('#trip-form').addEventListener('submit', e => {
    e.preventDefault(); const f = new FormData(e.currentTarget);
    const bits = [f.get('origin') ? '从' + f.get('origin') + '出发' : '', f.get('city') ? '去' + f.get('city') : '',f.get('startDate') ? f.get('startDate') + '出发' : '',f.get('days') ? '玩' + f.get('days') + '天' : '',f.get('people') ? f.get('people') + '个人' : '',f.get('budget') ? '总预算' + f.get('budget') + '元' : ''].filter(Boolean);
    if (!bits.length) { toast('先补充至少一项旅行信息。'); return; }
    state.input = bits.join('，') + '，请帮我' + (state.plan ? '重新调整' : '规划') + '行程。';
    navigate('chat'); $('#message')?.focus();
  });
  const form=$('#trip-form');
  const locationButton=document.createElement('button');
  locationButton.type='button';locationButton.className='outline';locationButton.textContent='使用定位城市';
  const locationStatus=document.createElement('p');
  locationStatus.className='map-fine';locationStatus.setAttribute('role','status');
  locationStatus.textContent='只用城市填入表单。精确坐标仅用于腾讯地图反查，不写入行程或发给千问。';
  form.querySelector('.form-row').after(locationButton,locationStatus);
  locationButton.onclick=async()=>{
    locationButton.disabled=true;locationStatus.textContent='正在获取出发城市…';
    try{
      const gps=await browserPosition();
      if(!form.isConnected||!$('#dialog')?.open)return;
      const r=await fetch('/api/maps/locate',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({gps}),signal:AbortSignal.timeout(12000)});
      const data=await r.json();
      if(!r.ok||!data.city)throw new Error(data.error||'未取得所在城市，可以手动填写。');
      form.elements.origin.value=data.city;
      locationStatus.textContent='已填入 '+data.city+'，请核对后发送。';
    }catch(e){
      locationStatus.textContent=(e.name==='TimeoutError'?'查询超时。':e.message)+' 可重试或手动填写。';
      locationButton.textContent='重新获取出发城市';
    }finally{locationButton.disabled=false;}
  };
}
function confirmDialog(title, detail, onConfirm) {
  openDialog('<h2 id="dialog-title">' + esc(title) + '</h2><p class="muted">' + esc(detail) + '</p><div class="page-actions"><button class="outline" data-action="close-dialog">取消</button><button class="primary" id="confirm-action">确认</button></div>');
  $('#confirm-action').addEventListener('click', () => { $('#dialog').close(); onConfirm(); });
}
async function loadStatus() {
  try { const response = await fetch('/api/status', { signal: AbortSignal.timeout(10000) }); if (!response.ok) throw new Error(); state.status = await response.json(); state.statusError = false; }
  catch { state.statusError = true; }
  // Do not close an active input dialog for a background status result.
  if (!$('#dialog')?.open) render();
}
async function sendMessage() {
  const message = state.input.trim(); if (!message || state.busy) return;
  const history = state.messages.filter(m => !m.failed).slice(-12).map(({role,content}) => ({role,content}));
  state.messages.push({role:'user',content:message}); state.input = ''; state.busy = true; render();
  requestController = new AbortController();
  let planned=false;
  const timeout = setTimeout(() => requestController.abort('timeout'), 90000);
  try {
    const response = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, signal:requestController.signal, body:JSON.stringify({message,history,trip:state.trip,priorPlan:state.plan,profile:state.useProfile ? state.profile : {}}) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败，请稍后重试。');
    if (typeof data.reply !== 'string') throw new Error('服务返回的对话格式不完整，请重试。');
    const updatingTrip = data.intent === 'plan';
    planned = updatingTrip && Array.isArray(data.plan?.days) && Array.isArray(data.plan?.budget);
    state.messages.push({role:'assistant',content:data.reply,mode:data.mode,hasPlan:planned,
      cards:data.intent === 'lookup' ? data.cards : undefined});
    if (updatingTrip && data.trip) state.trip = {...state.trip,...data.trip};
    if (planned) {
      travelUi?.controller?.abort();travelUi=undefined;state.plan = data.plan;state.planMode = data.mode;
      state.places = Array.isArray(data.places) ? data.places : [];
      state.weather = data.weather || ''; state.route = data.route || '';
    }
    if (Array.isArray(data.sources)) state.sources = data.sources;
    state.suggestion = data.profileSuggestion && Object.keys(data.profileSuggestion).length ? data.profileSuggestion : null;
    if (updatingTrip) persist();
  } catch (error) {
    const content = requestController.signal.aborted ? (requestController.signal.reason === 'timeout' ? '这次等待超过了 90 秒。输入已保留，请稍后重试。' : '已停止本次生成。你可以修改输入后重新发送。') : '这次未能完成：' + (error.message || '请检查本地服务连接。');
    state.messages.push({role:'assistant',content,failed:true}); state.input = message;
  } finally { clearTimeout(timeout); state.busy = false; render(); $('#message')?.focus(); if(planned)autoDeparture(); }
}
function calendarDialog() {
  openDialog('<span class="eyebrow">TAKE THE PLAN WITH YOU</span><h2 id="dialog-title">把旅程装进日历</h2><p class="muted">将导出 ' + state.plan.days.length + ' 天的安排为 ICS 文件。确认后只会下载文件，不会直接修改线上日历。所有时间按中国标准时间安排。</p><form id="calendar-form" class="field-form"><label>确认出发日期<input name="startDate" type="date" value="' + esc(state.trip.startDate || '') + '" required></label><label class="checkbox-label"><input type="checkbox" required>我已核对日期和行程，确认导出。</label><button class="primary">确认并下载日历 ' + icon('download') + '</button><p id="calendar-error" class="form-error" role="alert"></p></form>');
  $('#calendar-form').addEventListener('submit', async e => {
    e.preventDefault(); const button = e.currentTarget.querySelector('button'); const date = new FormData(e.currentTarget).get('startDate'); button.disabled = true;
    try {
      const response = await fetch('/api/calendar', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan:state.plan,startDate:date,confirmed:true}),signal:AbortSignal.timeout(15000)});
      if (!response.ok) { const error = await response.json(); throw new Error(error.error || '导出失败'); }
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = 'xiaotu-trip-' + date + '.ics'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
      state.trip.startDate = date; persist(); render(); toast('日历文件已下载，可导入你的日历应用。');
    } catch(error) { const el = $('#calendar-error'); if (el) el.textContent = error.message || '导出失败，请重试。'; button.disabled = false; }
  });
}
function bindForms() {
  $('#chat-form')?.addEventListener('submit', e => {e.preventDefault(); sendMessage();});
  $('#message')?.addEventListener('input', e => {state.input = e.target.value; const send = $('.send-button'); if (send) send.disabled = !state.input.trim(); const count = $('.input-count'); if (count) count.textContent = state.input.length + '/1600';});
  $('#message')?.addEventListener('keydown', e => {if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){e.preventDefault(); sendMessage();}});
  $('#profile-form')?.addEventListener('submit', e => {
    e.preventDefault(); const f = new FormData(e.currentTarget);
    state.profile = {homeCity:String(f.get('homeCity')).trim(),transport:String(f.get('transport')),interests:String(f.get('interests')).split(/[、,，]/).map(s=>s.trim()).filter(Boolean)};
    if (f.get('dailyBudget')) state.profile.dailyBudget = Number(f.get('dailyBudget'));
    state.useProfile = $('#use-profile').checked; persist(); toast('旅行偏好已保存到本浏览器。');
  });
  $('#use-profile')?.addEventListener('change', e => {state.useProfile=e.target.checked;});
  $('#expense-form')?.addEventListener('submit', e => {
    e.preventDefault(); const f = new FormData(e.currentTarget);
    state.expenses.push({title:String(f.get('title')).trim(),amount:Number(f.get('amount')),category:String(f.get('category'))});
    persist(); render(); toast('这笔花费已保存。');
  });
  document.querySelectorAll('.item-form').forEach(form => form.addEventListener('submit', e => {
    e.preventDefault(); const f = new FormData(form);
    const items = state.plan.days[Number(form.dataset.day)].items, index = Number(form.dataset.item), time = String(f.get('time')); if ((index > 0 && time <= items[index - 1].time) || (index < items.length - 1 && time >= items[index + 1].time)) { toast('时间需晚于上一项、早于下一项，不能重复。'); return; }
    items[index]={...items[index],...Object.fromEntries(f.entries())};
    persist(); render(); toast('行程安排已调整。');
  }));
}
document.addEventListener('click', event => {
  const target = event.target.closest('button,a'); if (!target) return;
  if (target.matches('.brand')) {event.preventDefault(); navigate('chat'); return;}
  if (target.dataset.view) {navigate(target.dataset.view); return;}
  if (target.dataset.prompt !== undefined) {state.input=suggestions[Number(target.dataset.prompt)].prompt; render(); $('#message')?.focus(); return;}
  if (target.dataset.removeExpense !== undefined) {state.expenses.splice(Number(target.dataset.removeExpense),1); persist(); render(); return;}
  if (target.dataset.placeEdit !== undefined) {placeDialog(...target.dataset.placeEdit.split(',').map(Number)); return;}
  switch(target.dataset.action) {
    case 'marquee': state.marqueePaused=!state.marqueePaused; const track=$('.suggestion-track'); track?.classList.toggle('paused',state.marqueePaused); target.textContent=state.marqueePaused?'继续滚动':'暂停滚动'; target.setAttribute('aria-label',target.textContent+'建议'); break;
    case 'trip-details': tripDialog(); break;
    case 'food-demo': if(!state.statusError)openFoodOrder(state.status); break;
    case 'rail-query': openRailDialog(state.trip,openDialog,esc); break;
    case 'close-dialog': $('#dialog').close(); break;
    case 'cancel': requestController?.abort('user'); break;
    case 'refresh-status': loadStatus(); break;
    case 'refine': state.input='请帮我调整刚才的行程：'; navigate('chat'); $('#message')?.focus(); break;
    case 'accept-profile': state.profile={...state.profile,...state.suggestion}; state.suggestion=null; persist(); render(); toast('已确认保存这次提到的旅行偏好。'); break;
    case 'dismiss-profile': state.suggestion=null; render(); break;
    case 'calendar': if(state.plan) calendarDialog(); break;
    case 'new': confirmDialog('开始一段新旅程？','当前对话、行程和账本会清空。已保存的旅行偏好会保留。',()=>{state.messages=[];state.plan=null;state.trip={};state.expenses=[];state.sources=[];state.places=[];state.suggestion=null;state.weather='';state.route='';state.input='';persist();navigate('chat');}); break;
    case 'clear-plan': confirmDialog('清除本次行程与账本？','这会删除本浏览器保存的行程、旅行信息与花费记录，不影响旅行偏好。',()=>{state.plan=null;state.trip={};state.expenses=[];persist();render();}); break;
    case 'clear-profile': confirmDialog('清除已保存偏好？','常居城市、预算与兴趣将从本浏览器删除。当前对话中的原始文字仍保留，开始新旅程可一并清除。',()=>{state.profile={};state.suggestion=null;persist();render();toast('已清除已保存偏好。');}); break;
    case 'clear-all': confirmDialog('清除本浏览器全部小途数据？','删除偏好、行程、花费、本次对话与模拟报价记录。清除后不能用原记录查询模拟操作，也不会撤销已发起的操作。这个操作无法撤销。',()=>{requestController?.abort('user');localStorage.removeItem(key);clearFoodOrderStorage();location.reload();}); break;
    case 'set-budget':
      openDialog('<h2 id="dialog-title">调整旅行总预算</h2><form id="budget-form" class="field-form"><label>本次旅行全程预算 / 元<input name="budget" type="number" min="1" max="10000000" value="'+esc(state.trip.budget||'')+'" required></label><p class="muted">只更新预算上限，不自动重排旅行。需要更省钱的安排，可以继续和小途聊聊。</p><button class="primary">保存预算</button></form>');
      $('#budget-form').addEventListener('submit',e=>{e.preventDefault();state.trip.budget=Number(new FormData(e.currentTarget).get('budget'));persist();render();toast('旅行总预算已更新。');}); break;
  }
});
render();
loadStatus();
