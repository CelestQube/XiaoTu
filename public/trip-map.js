const esc=(v='')=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>';
const link=(kind,props)=>'https://uri.amap.com/'+kind+'?'+new URLSearchParams({...props,src:'xiaotu',callnative:'1'});
const external=' target="_blank" rel="noopener noreferrer"';
const amapName=name=>String(name||'').replace(/[|,，]/g,' ');
const pointOf=p=>p?.entrance||p?.point;
const searchLink=(city,name)=>link('search',{city,keyword:name,view:'list'});
const safePhoto=url=>{
 try{const u=new URL(url);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&/(?:^|\.)(?:amap\.com|autonavi\.com)$/.test(u.hostname)?u.href:'';}
 catch{return '';}
};
const timeCN=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'short'});
let panel;
function isCurrent(m){return panel===m&&m.host?.isConnected;}
function release(m){if(m.mapURL){URL.revokeObjectURL(m.mapURL);m.mapURL='';}}
export function clearTripMap(){
 if(panel){panel.controller?.abort();release(panel);panel=null;}
}
export function namedStop(name){
 return typeof name==='string'&&!!name.trim()&&!/待选|待定|待核实|附近$|周边$|^(酒店|餐厅|酒店入住|午餐|晚餐|早餐|市区|当地|景点)$/.test(name.trim());
}
export function mountTripMap(host,{trip,plan,configured}){
 if(!host||!plan||!trip.city){clearTripMap();return;}
 const days=plan.days.map(d=>d.items.map(x=>({location:x.location,time:x.time})));
 const signature=JSON.stringify([trip.city,days]);
 if(panel?.signature!==signature||panel?.configured!==configured){
  clearTripMap();
  panel={signature,host,city:trip.city,days,configured,selectedDay:0,selected:0,mode:'walk',
   stops:[],route:null,routeEpoch:0,dayEpoch:0,mapEpoch:0,localized:null,origin:'',error:'',mapError:'',status:'idle',snap:'idle'};
 } else panel.host=host;
 const m=panel;
 draw(m);
 if(configured&&m.status==='idle') void loadDay(m);
}
export function focusTripMap(day,index){
 const m=panel;if(!m)return;
 m.scrollPoi=matchMedia("(max-width: 900px)").matches;
 if(m.selectedDay!==day) {m.selectedDay=day;m.selected=index;m.lockSelection=true;void loadDay(m);}
 else {select(m,index);draw(m);}
 m.host.scrollIntoView({behavior:'instant',block:'start'});
 m.host.focus({preventScroll:true});
}
const stopLabel={matched:'已匹配',unresolved:'未匹配到确切地点',ambiguous:'多个同名地点'};
function stopData(m,i){return m.stops.find(s=>s.index===i);}
function selectedPoi(m){return stopData(m,m.selected)?.poi;}
function markList(m){
 const a=m.stops.filter(s=>s.poi).map(s=>({key:String(s.index),point:s.poi.point,name:s.poi.name}));
 if(m.localized)a.push({key:'me',point:m.localized.point,name:'我的位置'});
 return a;
}
function startOf(m){
 if(m.origin==='me'&&m.localized)return {name:'我的位置',point:m.localized.point};
 if(m.origin==='')return null;
 const p=stopData(m,Number(m.origin))?.poi;
 return p?{id:p.id,name:p.name,point:pointOf(p)}:null;
}
function setStart(m){
 const p=selectedPoi(m);
 const prev=[...m.stops].reverse().find(s=>s.index<m.selected&&s.poi&&pointOf(s.poi)!==pointOf(p));
 m.origin=m.localized?'me':prev?String(prev.index):'';
}
function select(m,index){m.selected=index;clearRoute(m);setStart(m);if(m.status==='ready')void snapshot(m);}
function clearRoute(m){m.route=null;m.routeError='';m.routing=false;m.routeEpoch++;}
async function post(action,body,{signal,blob=false}={}){
 const r=await fetch('/api/maps/'+action,{method:'POST',headers:{'content-type':'application/json'},
  body:JSON.stringify(body),signal:signal||AbortSignal.timeout(36000)});
 if(!r.ok){
  let msg;
  try{msg=(await r.json()).error;}catch{/* Ignore invalid HTML responses. */}
  throw new Error(msg||'地图查询暂时失败，请稍后重试。');
 }
 return blob?r.blob():r.json();
}
async function loadDay(m){
 m.controller?.abort();m.controller=new AbortController();
 const id=++m.dayEpoch;
 m.mapEpoch++;release(m);clearRoute(m);
 m.stops=[];m.status='loading';m.error='';m.mapError='';m.snap='idle';draw(m);
 try{
  const data=await post('day',{city:m.city,locations:m.days[m.selectedDay].map(x=>x.location)},
   {signal:AbortSignal.any([m.controller.signal,AbortSignal.timeout(36000)])});
  if(!isCurrent(m)||id!==m.dayEpoch)return;
  m.stops=data.stops||[];m.status='ready';
  if(!stopData(m,m.selected)?.poi&&m.selected===0&&!m.lockSelection)
    m.selected=m.stops.find(s=>s.poi)?.index??m.selected;
  m.lockSelection=false;setStart(m);draw(m);void snapshot(m);
 }catch(e){
  if(!isCurrent(m)||id!==m.dayEpoch)return;
  m.status='error';m.error=e.name==='TimeoutError'?'地点查询超时，请重试。':e.message;draw(m);
 }
}
async function snapshot(m){
 const id=++m.mapEpoch, markers=markList(m).map(s=>s.point);
 release(m);m.mapError='';
 if(!markers.length){m.snap='empty';draw(m);return;}
 m.snap='loading';draw(m);
 try{
  const blob=await post('snapshot',{markers,polyline:m.route?.polyline||[]},{blob:true,
   signal:AbortSignal.any([m.controller?.signal||new AbortController().signal,AbortSignal.timeout(12000)])});
  if(!isCurrent(m)||id!==m.mapEpoch)return;
  m.mapURL=URL.createObjectURL(blob);m.snap='ready';draw(m);
 }catch(e){
  if(!isCurrent(m)||id!==m.mapEpoch)return;
  m.mapError=e.name==='TimeoutError'?'底图请求超时。':e.message;m.snap='error';draw(m);
 }
}
async function route(m){
 const start=startOf(m),p=selectedPoi(m);
 if(!start||!p||start.point===pointOf(p))return;
 const id=++m.routeEpoch;
 m.routing=true;m.routeError='';m.route=null;draw(m);
 try{
  const data=await post('route',{origin:start.point,originId:start.id,destination:pointOf(p),destinationId:p.id,mode:m.mode});
  if(!isCurrent(m)||id!==m.routeEpoch)return;
  m.route=data;m.routing=false;draw(m);void snapshot(m);
 }catch(e){
  if(!isCurrent(m)||id!==m.routeEpoch)return;
  m.routing=false;m.routeError=e.name==='TimeoutError'?'算路超时，请重试。':e.message;draw(m);
 }
}
function locate(m){
 if(!navigator.geolocation){m.locError='此浏览器不支持定位，请改用行程地点作起点。';draw(m);return;}
 m.locating=true;m.locError='';draw(m);
 navigator.geolocation.getCurrentPosition(async pos=>{
  if(!isCurrent(m))return;
  try{
   const gps=pos.coords.longitude.toFixed(6)+','+pos.coords.latitude.toFixed(6);
   const result=await post('locate',{gps});
   if(!isCurrent(m))return;
   m.localized=result;m.origin='me';clearRoute(m);m.locating=false;draw(m);void snapshot(m);
  }catch(e){if(isCurrent(m)){m.locError=e.message;m.locating=false;draw(m);}}
 },err=>{
  if(!isCurrent(m))return;
  m.locating=false;m.locError=err.code===1?'定位未授权。可在浏览器站点设置允许，或选行程地点起步。':err.code===3?'定位超时。可重试或手动选起点。':'无法确定当前位置，请检查设备定位服务。';draw(m);
 },{enableHighAccuracy:false,timeout:10000,maximumAge:0});
}
function poiCard(m){
 const p=selectedPoi(m), stop=stopData(m,m.selected), item=m.days[m.selectedDay][m.selected];
 if(!p) return '<div class="map-poi-empty"><strong>'+esc(item?.location||'选择一个地点')+'</strong><p>'+esc(stop?.status==='ambiguous'?'高德返回多个同名地点，暂不绑定照片和评分。':m.status==='ready'?'没有可靠匹配。可在行程中填写高德显示的完整地名后重查。':'地点详情将在核实后展示。')+'</p>'+(namedStop(item?.location)?'<a class="outline" href="'+esc(searchLink(m.city,item.location))+'"'+external+'>在高德搜索此地点 ↗</a>':'')+'</div>';
 const photos=(p.photos||[]).map(v=>({...v,safe:safePhoto(v.url)})).filter(v=>v.safe);
 const imgs=photos.length?'<div class="poi-gallery">'+photos.map((ph,i)=>'<a href="'+esc(ph.safe)+'"'+external+' class="poi-photo"><img loading="lazy" referrerpolicy="no-referrer" src="'+esc(ph.safe)+'" alt="'+esc(ph.title||p.name+' 照片 '+(i+1))+'"></a>').join('')+'</div>':'<div class="poi-no-photo">'+icon+' 高德暂未提供图片</div>';
 const stamp=Date.parse(p.fetchedAt);
 const detail=link('marker',{markers:p.id});
 const start=startOf(m);
 const nav=link('navigation',{from:start?start.point+','+amapName(start.name):'',
   to:pointOf(p)+','+amapName(p.name),mode:m.mode,coordinate:'gaode'});
 return imgs+'<div class="poi-copy"><div class="poi-title"><div><span class="eyebrow">高德地图 · 地点档案</span><h3>'+esc(p.name)+'</h3></div>'
 +(p.rating?'<span class="poi-rating" aria-label="高德评分 '+esc(p.rating)+' 分">★ '+esc(p.rating)+' <small>/ 5</small></span>':'<span class="poi-rating missing">暂无评分</span>')
 +'</div><p class="poi-address">'+esc([p.city,p.address].filter(Boolean).join(' · ')||'地址待核实')+'</p><p class="poi-meta">'+esc([p.hours?'今日营业 '+p.hours:'',p.cost?'人均 ¥'+p.cost:''].filter(Boolean).join(' · '))+'</p><div class="poi-links"><a class="outline" href="'+esc(detail)+'"'+external+'>在高德看详情与点评 ↗</a><a class="outline" href="'+esc(nav)+'"'+external+'>在高德导航 ↗</a></div><p class="map-fine">逐条点评未通过公开接口提供；详情以高德页面为准。'+(Number.isFinite(stamp)?' 查询于 '+esc(timeCN.format(stamp))+'（中国时间）。':'')+'</p></div>';
}
function routeCard(m){
 const p=selectedPoi(m);if(!p)return '';
 const start=startOf(m),opts=m.stops.filter(s=>s.poi&&pointOf(s.poi)!==pointOf(p));
 const r=m.route;
 return '<div class="map-route"><div class="map-route-controls"><label>起点<select data-map-origin><option value="">选择起点</option>'
  +(m.localized?'<option value="me" '+(m.origin==='me'?'selected':'')+'>我的位置</option>':'')
  +opts.map(s=>'<option value="'+s.index+'" '+(m.origin===String(s.index)?'selected':'')+'>'+esc(s.poi.name)+'</option>').join('')
  +'</select></label><label>方式<select data-map-mode><option value="walk" '+(m.mode==='walk'?'selected':'')+'>步行</option><option value="car" '+(m.mode==='car'?'selected':'')+'>驾车</option></select></label>'
  +'<button class="primary" data-map-route '+(!start||start.point===pointOf(p)||m.routing?'disabled':'')+'>'+(m.routing?'查询中…':'查看此段路线')+'</button></div>'
  +(m.routeError?'<p class="map-error" role="alert">'+esc(m.routeError)+'</p>':'')
  +(r?'<div class="map-route-result"><strong>'+esc(start?.name)+' → '+esc(p.name)+'</strong><div>'+esc(r.distance>=1000?(r.distance/1000).toFixed(1)+' 公里':r.distance+' 米')+' · '+(r.duration?esc(Math.ceil(r.duration/60))+' 分钟':'耗时未返回')+' · 高德'+(m.mode==='walk'?'步行':'驾车')+'</div>'
  +(!r.polyline?.length?'<small>高德未返回本段折线。</small>':'')
  +(r.steps?.length?'<details><summary>查看路线步骤</summary><ol>'+r.steps.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol></details>':'')+'</div>':'')+'</div>';
}
function draw(m){
 if(!isCurrent(m))return;
 const old=m.host.contains(document.activeElement)?document.activeElement:null;
 const focusKey=old?.getAttribute('data-map-focus');
 const controls='<div class="trip-map-head"><div><span class="eyebrow">ON THE MAP · 每日地图</span><h2>从这站到下一站</h2><p>核实每天的实际地点、照片与交通路线。</p></div><button class="outline" data-map-locate data-map-focus="locate" '+(!m.configured||m.locating?'disabled':'')+'>'+icon+' '+(m.locating?'正在定位…':'使用我的位置')+'</button></div>';
 const disabled=m.configured===false?'尚未配置高德 Web 服务 Key，行程仍可使用，但真实地图与景点评价暂不可用。':'正在检查本地地图服务。';
 if(!m.configured){m.host.innerHTML=controls+'<p class="map-state">'+disabled+'</p>';return;}
 const dayNav='<div class="map-days" role="group" aria-label="选择行程天数">'+m.days.map((_,i)=>'<button class="map-day" data-map-day="'+i+'" data-map-focus="day'+i+'" aria-pressed="'+(m.selectedDay===i)+'">第 '+(i+1)+' 天</button>').join('')+'</div>';
 const markerIndex=new Map(markList(m).map((s,i)=>[s.key,i+1]));
 const items='<div class="map-stops" role="group" aria-label="选择当天地点">'+m.days[m.selectedDay].map((x,i)=>{
  const stop=stopData(m,i);
  return '<button class="map-stop" data-map-stop="'+i+'" data-map-focus="stop'+i+'" aria-pressed="'+(m.selected===i)+'"><span class="map-pin">'+(markerIndex.get(String(i))||'·')+'</span><span><small>'+esc(x.time)+'</small><strong>'+esc(stop?.poi?.name||x.location)+'</strong><small>'+esc(stopLabel[stop?.status]||'待核实')+'</small></span></button>';
 }).join('')+'</div>';
 let canvas;
 if(m.status==='error')canvas='<div class="map-state" role="alert"><strong>地图查询未完成</strong><p>'+esc(m.error)+'</p><button class="outline" data-map-retry>重新查询</button></div>';
 else if(m.status==='loading'||m.snap==='loading')canvas='<div class="map-state" role="status">'+icon+' 正在核实地点并绘制高德地图…</div>';
 else if(m.snap==='error')canvas='<div class="map-state" role="alert"><p>'+esc(m.mapError)+'</p><button class="outline" data-map-refresh>重试底图</button></div>';
 else if(m.mapURL)canvas='<figure class="map-image"><img src="'+esc(m.mapURL)+'" alt="高德地图，标出第 '+(m.selectedDay+1)+' 天已匹配地点'+(m.route?.polyline?.length?'与当前分段路线':'')+'"><figcaption>底图与路线：高德地图</figcaption></figure>';
 else canvas='<div class="map-state">当天暂无能够准确标注的地点。</div>';
 const privacy= m.localized ? '<div class="map-location"><strong>地图编号 '+(markerIndex.get('me')||'—')+' · 我的位置</strong> '+esc(m.localized.address||'已取得高德坐标')+' <button class="text-button" data-map-forget>移除位置</button></div>'
   : '<p class="map-fine">仅在点击定位并授权后发送坐标给高德；离开行程页即清除。</p>';
 m.host.innerHTML=controls+privacy+(m.locError?'<p class="map-error" role="alert">'+esc(m.locError)+'</p>':'')+dayNav
  +'<div class="map-layout"><div class="map-main">'+canvas+items+routeCard(m)+'</div><aside class="map-poi" tabindex="-1" aria-label="地点图片和评价">'+poiCard(m)+'</aside></div>';
 m.host.querySelectorAll('.poi-photo img').forEach(img=>img.addEventListener('error',()=>{img.closest('a')?.replaceWith(Object.assign(document.createElement('span'),{className:'poi-no-photo',textContent:'图片暂不可加载'}));},{once:true}));
 m.host.querySelectorAll('[data-map-day]').forEach(b=>b.onclick=()=>{m.selectedDay=+b.dataset.mapDay;m.selected=0;void loadDay(m);});
 m.host.querySelectorAll('[data-map-stop]').forEach(b=>b.onclick=()=>select(m,+b.dataset.mapStop));
 m.host.querySelector('[data-map-locate]').onclick=()=>locate(m);
 const forget=m.host.querySelector('[data-map-forget]');if(forget)forget.onclick=()=>{m.localized=null;clearRoute(m);setStart(m);void snapshot(m);};
 const origin=m.host.querySelector('[data-map-origin]'); if(origin) origin.onchange=()=>{m.origin=origin.value;clearRoute(m);void snapshot(m);};
 const mode=m.host.querySelector('[data-map-mode]'); if(mode) mode.onchange=()=>{m.mode=mode.value;clearRoute(m);void snapshot(m);};
 const go=m.host.querySelector('[data-map-route]'); if(go) go.onclick=()=>void route(m);
 const again=m.host.querySelector('[data-map-retry]'); if(again) again.onclick=()=>void loadDay(m);
 const refresh=m.host.querySelector('[data-map-refresh]'); if(refresh) refresh.onclick=()=>void snapshot(m);
 if(m.scrollPoi&&(m.status==='ready'||m.status==='error')) {
  m.scrollPoi=false;
  queueMicrotask(()=>{
   if(!isCurrent(m))return;
   const card=m.host.querySelector('.map-poi');
   card?.focus({preventScroll:true});card?.scrollIntoView({behavior:'instant',block:'start'});
  });
 } else if(focusKey) m.host.querySelector('[data-map-focus="'+CSS.escape(focusKey)+'"]')?.focus({preventScroll:true});
}
