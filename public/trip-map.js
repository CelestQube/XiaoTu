import {browserPosition} from './location.js';
const esc=(v='')=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>';
const external=' target="_blank" rel="noopener noreferrer"';
const timeCN=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'short'});
const modes={walk:'步行',car:'驾车',bike:'骑行',transit:'公交／地铁'};
const stopLabel={matched:'地图已核实',pending:'待核实',placeholder:'待选',unresolved:'未匹配到确切地点',ambiguous:'多个同名地点',unavailable:'搜索暂不可用'};
const tencentUri=(method,params)=>'https://apis.map.qq.com/uri/v1/'+method+'?'+new URLSearchParams({...params,referer:'xiaotu'});
const toTencent=point=>point.split(',').reverse().join(',');
const routeType={walk:'walk',car:'drive',bike:'bike',transit:'bus'};
let panel;
const current=m=>panel===m&&m.host?.isConnected;
const stopData=(m,i)=>m.stops.find(s=>s.index===i);
const selectedPoi=m=>stopData(m,m.selected)?.poi;

function dropSnapshot(m){
 if(!m.snapshot)return;
 m.snapshot.controller.abort();
 if(m.snapshot.url)URL.revokeObjectURL(m.snapshot.url);
 m.snapshot=null;
}
export function clearTripMap(){
 if(panel){dropSnapshot(panel);panel.dayRequest?.abort();panel.dailyAbort?.abort();panel.life.abort();panel=null;}
}
export function namedStop(name){
 return typeof name==='string'&&!!name.trim()&&!/待选|待定|待核实|附近$|周边$|^(酒店|餐厅|酒店入住|午餐|晚餐|早餐|市区|当地|景点|不指定地点)$/.test(name.trim());
}
function knownStop(item,city){
 const p=item.poi;
 if(item.mapStatus!=='verified'||p?.tripCity!==city||p.name!==item.location||typeof p.id!=='string'||
    typeof p.point!=='string'||!/^[-+]?\d{1,3}(?:\.\d{1,6})?,[-+]?\d{1,3}(?:\.\d{1,6})?$/.test(p.point))return null;
 return p;
}
function rememberStop(m,day,index,item,stop){
 const p=stop.poi;
 if(stop.status!=='matched'||!p||p.name!==item.location||p.tripCity!==m.city)return;
 item.poi=p;item.mapStatus='verified';m.onPlaceVerified?.(day,index,p);
}
export function mountTripMap(host,{trip,plan,configured,onPlaceVerified}){
 if(!host||!plan||!trip.city){clearTripMap();return;}
 const days=plan.days.map(d=>d.items.map(x=>({location:x.location,time:x.time,mapStatus:x.mapStatus,poi:knownStop(x,trip.city)})));
 const signature=JSON.stringify([trip.city,days]);
 if(panel?.signature!==signature||panel?.configured!==configured){
  clearTripMap();
  panel={signature,host,city:trip.city,days,configured,selectedDay:0,selected:0,mode:'walk',
   stops:[],routeEpoch:0,dayEpoch:0,origin:'',status:'idle',life:new AbortController(),details:new Map(),daily:new Map()};
 }else panel.host=host;
 const m=panel;
 m.onPlaceVerified=onPlaceVerified;
 draw(m);
 if(configured&&m.status==='idle')void loadDay(m);
}
export function focusTripMap(day,index){
 const m=panel;
 if(!m||!m.days[day]?.[index])return;
 m.scrollPoi=m.configured&&matchMedia('(max-width: 900px)').matches;
 if(m.selectedDay!==day&&m.configured){
  m.selectedDay=day;m.selected=index;m.lockSelection=true;void loadDay(m);
 }else{m.selectedDay=day;select(m,index);}
 m.host.scrollIntoView({block:'start'});m.host.focus({preventScroll:true});
}
function originOf(m){
 if(m.origin==='me'&&m.localized)return {name:'我的位置',point:m.localized.point,city:m.localized.city};
 return m.origin===''?null:stopData(m,Number(m.origin))?.poi||null;
}
function setOrigin(m){
 const p=selectedPoi(m);
 const prev=[...m.stops].reverse().find(s=>s.index<m.selected&&s.poi&&s.poi.point!==p?.point);
 m.origin=m.localized?'me':prev?String(prev.index):'';
}
function clearRoute(m){m.route=null;m.routeError='';m.routing=false;m.routeEpoch++;}
function select(m,index){m.selected=index;clearRoute(m);setOrigin(m);draw(m);void loadDetail(m);}
async function post(action,body,{signal,timeout=18000,image=false}={}){
 const clock=AbortSignal.timeout(timeout);
 const r=await fetch('/api/maps/'+action,{method:'POST',headers:{'content-type':'application/json'},
  body:JSON.stringify(body),signal:signal?AbortSignal.any([signal,clock]):clock});
 if(!r.ok){
  if(image&&r.status===404){
   const error=new Error('当前小途后端没有加载静态地图接口，请重启服务并刷新页面。');
   error.code='STATIC_ROUTE_MISSING';throw error;
  }
  let msg;
  try{msg=(await r.json()).error;}catch{/* Ignore unexpected payloads. */}
  const e=new Error(msg||'地图查询暂时失败，请稍后重试。');e.status=r.status;throw e;
 }
 return image?r.blob():r.json();
}
function syncSnapshot(m,marks,force){
 if(m.status!=='ready'||!marks.length){dropSnapshot(m);return;}
 if(m.staticRouteMissing&&!force){dropSnapshot(m);return;}
 if(force)m.staticRouteMissing='';
 const paths=visiblePaths(m),body={markers:marks.map(p=>p.point),polyline:paths.length===1?paths[0]:[]};
 if(paths.length>1)body.paths=paths;
 const signature=JSON.stringify(body);
 if(!force&&m.snapshot?.signature===signature)return;
 dropSnapshot(m);
 const state={signature,controller:new AbortController(),loading:true};
 m.snapshot=state;
 void (async()=>{
  try{
   const blob=await post('snapshot',body,{image:true,signal:state.controller.signal,timeout:12000});
   if(!current(m)||m.snapshot!==state)return;
   if(blob.type!=='image/png'||!blob.size)throw new Error('腾讯静态地图未返回可用图片。');
   state.url=URL.createObjectURL(blob);
  }catch(e){
   state.error=e.name==='TimeoutError'?'静态底图加载超时。':e.message;
   if(e.code==='STATIC_ROUTE_MISSING'&&current(m)&&m.snapshot===state)m.staticRouteMissing=state.error;
  }finally{
   state.loading=false;
   if(current(m)&&m.snapshot===state){
    if(!state.url&&!state.error)state.error='静态底图暂时不可用。';
    draw(m);
   }
  }
 })();
}
async function loadDay(m){
 m.dayRequest?.abort();m.dayRequest=new AbortController();
 m.dailyAbort?.abort();m.dailyAbort=null;
 const id=++m.dayEpoch;
 clearRoute(m);m.verifying=null;m.verifyError='';m.status='loading';m.error='';m.warning='';
 const day=m.days[m.selectedDay];
 const legacy=[];
 const stops=day.map((item,index)=>{
  if(item.poi)return {index,status:'matched',poi:item.poi};
  if(item.mapStatus==='placeholder'||!namedStop(item.location))return {index,status:'placeholder'};
  if(!item.mapStatus&&legacy.length<2)legacy.push(index);
  return {index,status:'pending'};
 });
 m.stops=stops;
 if(legacy.length)draw(m);
 try{
  if(legacy.length){
   try{
    const data=await post('day',{city:m.city,locations:legacy.map(i=>day[i].location)},
     {signal:m.dayRequest.signal,timeout:60000});
    if(!current(m)||id!==m.dayEpoch)return;
    for(const found of data.stops||[]){
     const index=legacy[found.index];
     if(index!==undefined){
      stops[index]={...found,index};rememberStop(m,m.selectedDay,index,day[index],found);
     }
    }
    m.warning=data.warning;
   }catch(e){
    if(!current(m)||id!==m.dayEpoch)return;
    if(!stops.some(s=>s.poi))throw e;
    m.warning=e.name==='TimeoutError'?'待核实地点查询超时。':e.message;
   }
  }
  m.status='ready';
  if(!selectedPoi(m)&&m.selected===0&&!m.lockSelection)m.selected=m.stops.find(s=>s.poi)?.index??0;
  m.lockSelection=false;setOrigin(m);draw(m);void loadDetail(m);
 }catch(e){
  if(!current(m)||id!==m.dayEpoch)return;
  m.status='error';m.error=e.name==='TimeoutError'?'地点查询超时，请重试。':e.message;draw(m);
 }
}
async function verifySelected(m){
 const index=m.selected,day=m.selectedDay,item=m.days[day]?.[index],epoch=m.dayEpoch;
 if(!item||!namedStop(item.location)||m.verifying)return;
 const state={index};m.verifying=state;m.verifyError='';draw(m);
 try{
  const data=await post('day',{city:m.city,locations:[item.location]},{signal:m.life.signal});
  if(!current(m)||m.dayEpoch!==epoch)return;
  const stop=data.stops?.[0]||{status:'unresolved'};
  m.stops[index]={...stop,index};
  rememberStop(m,day,index,item,stop);
  if(m.selected===index){setOrigin(m);void loadDetail(m);}
 }catch(e){
  if(current(m)&&m.dayEpoch===epoch&&m.selected===index)m.verifyError=e.name==='TimeoutError'?'地点核实超时。':e.message;
 }finally{
  if(current(m)&&m.verifying===state){m.verifying=null;draw(m);}
 }
}
async function loadDetail(m,{force=false}={}){
 const p=selectedPoi(m);
 if(!p)return;
 const last=m.details.get(p.id);
 if(last?.loading||last&&!force)return;
 const state={loading:true};m.details.set(p.id,state);draw(m);
 try{state.data=await post('detail',{id:p.id},{signal:m.life.signal});}
 catch(e){state.error=e.name==='TimeoutError'?'地点详情查询超时。':e.message;}
 finally{state.loading=false;if(current(m)&&selectedPoi(m)?.id===p.id)draw(m);}
}
async function route(m){
 const start=originOf(m),p=selectedPoi(m);
 if(!start||!p||start.point===p.point)return;
 const id=++m.routeEpoch;
 m.routing=true;m.routeError='';m.route=null;draw(m);
 try{
  const data=await post('route',{origin:start.point,destination:p.point,mode:m.mode,...(m.geometry?{geometry:true}:{})},
   {signal:m.life.signal,timeout:m.geometry?22000:18000});
  if(!current(m)||id!==m.routeEpoch)return;
  m.route=data;m.routing=false;draw(m);
 }catch(e){
  if(!current(m)||id!==m.routeEpoch)return;
  m.routing=false;m.routeError=e.name==='TimeoutError'?'算路超时，请重试。':e.message;draw(m);
 }
}
async function locate(m){
 if(m.locating)return;
 m.locating=true;m.locError='';draw(m);
 try{
  const gps=await browserPosition();
  if(!current(m))return;
  const result=await post('locate',{gps},{signal:m.life.signal});
  if(!current(m))return;
  if(!result.point)throw new Error('未取得可用定位，可以重试。');
  m.localized=result;m.origin='me';clearRoute(m);m.locating=false;draw(m);
 }catch(e){if(current(m)){m.locError=e.name==='TimeoutError'?'位置查询超时。':e.message;m.locating=false;draw(m);}}
}
const dailyKey=m=>JSON.stringify([m.selectedDay,m.mode,m.stops.map(s=>s.poi?.point??'')]);
const routePaths=r=>r?.polyline?.length>1?[r.polyline]:(r?.paths||[]).filter(p=>Array.isArray(p)&&p.length>1);
function visiblePaths(m){
 const paths=(m.route?routePaths(m.route):(m.daily.get(dailyKey(m))?.legs||[]).flatMap(l=>routePaths(l.route))).slice(0,20);
 if(paths.length<=1)return paths;
 const limit=Math.floor(185/paths.length);
 return paths.map(p=>p.length<=limit?p:Array.from({length:limit},(_,i)=>p[Math.round(i*(p.length-1)/(limit-1))]));
}
function legsFor(m){
 return m.stops.slice(1).map((to,i)=>{
  const from=m.stops[i],a=from.poi,b=to.poi;
  return {from:a,to:b,label:(a?.name||m.days[m.selectedDay][i].location)+' → '+(b?.name||m.days[m.selectedDay][i+1].location),
   state:!a||!b?'待核实地点，不跨站连线':a.point===b.point?'同一地点，不需要算路':''};
 });
}
async function plotDaily(m){
 if(m.dailyAbort||m.routing||m.status!=='ready')return;
 const legs=legsFor(m);
 if(!legs.some(x=>!x.state))return;
 const key=dailyKey(m),mode=m.mode,control=new AbortController();
 const previous=m.daily.get(key)?.legs;
 for(let i=0;i<legs.length;i++)if(routePaths(previous?.[i]?.route).length)legs[i].route=previous[i].route;
 if(previous&&legs.every(l=>l.state||routePaths(l.route).length)){
  clearRoute(m);draw(m);return;
 }
 m.dailyAbort=control;clearRoute(m);draw(m);
 let quota=false;
 for(const leg of legs){
  if(leg.state||routePaths(leg.route).length)continue;
  if(quota){leg.state='限流后未继续查询';continue;}
  try{
   leg.route=await post('route',{origin:leg.from.point,destination:leg.to.point,mode,geometry:true},
    {signal:control.signal,timeout:22000});
  }catch(e){
   if(control.signal.aborted)return;
   leg.state=e.name==='TimeoutError'?'本段查询超时':e.message;quota=e.status===429;
  }
  if(!current(m)||m.dailyAbort!==control)return;
 }
 if(!current(m)||m.dailyAbort!==control)return;
 m.daily.set(key,{legs});m.dailyAbort=null;draw(m);
}
function poiCard(m){
 const p=selectedPoi(m),stop=stopData(m,m.selected),item=m.days[m.selectedDay][m.selected];
 if(!p)return '<div class="map-poi-empty"><strong>'+esc(item?.location||'选择一个地点')+'</strong><p>'+esc(
  stop?.status==='ambiguous'?'腾讯地图返回多个同名地点，暂未绑定。':stop?.status==='unavailable'?'地点搜索暂不可用。':
  stop?.status==='pending'?'此地点保留在行程中，尚未定位。':stop?.status==='placeholder'?'这是待选安排，选定地点后再核实。':
  m.status==='ready'?'未找到可靠匹配。可改为腾讯地图显示的完整地名。':'地点详情将在核实后展示。'
 )+'</p>'+(m.configured&&['ready','error'].includes(m.status)&&namedStop(item?.location)?'<button class="outline" data-map-verify '
  +(m.verifying?'disabled':'')+'>'+(m.verifying?.index===m.selected?'核实中…':'核实此地点')+'</button>':'')
 +(m.verifyError?'<p class="map-error" role="status">'+esc(m.verifyError)+'</p>':'')+'</div>';
 const detail=m.details.get(p.id),start=originOf(m);
 const mapLink=tencentUri('search',{keyword:p.name,region:p.city||m.city});
 const navigation=tencentUri('routeplan',{type:routeType[m.mode],from:start?.name||'当前位置',
  fromcoord:start?toTencent(start.point):'CurrentLocation',to:p.name,tocoord:toTencent(p.point)});
 const d=detail?.data||{},rating=d.rating??p.rating,cost=d.cost??p.cost,hours=d.hours??p.hours;
 const photos=d.photos??p.photos??[],reviews=d.reviews??p.reviews??[];
 const missing=[!photos.length&&'照片',!rating&&'评分',!reviews.length&&'评论正文'].filter(Boolean).join('、');
 const stamp=Date.parse(d.fetchedAt||p.fetchedAt);
 return '<div class="poi-copy"><span class="eyebrow">腾讯地图 MCP · 地点详情</span><h3>'+esc(p.name)+'</h3>'
 +'<p class="poi-address">'+esc([p.city,d.address||p.address].filter(Boolean).join(' · ')||'地址待核实')+'</p>'
 +'<p class="poi-meta">'+esc([p.type,rating&&'评分 '+rating+'/5',cost&&'人均 ¥'+cost].filter(Boolean).join(' · '))+'</p>'
 +(hours?'<p class="poi-meta">营业时间：'+esc(hours)+'（出发前核实）</p>':'')
 +(photos.length?'<div class="poi-photos">'+photos.map(x=>'<figure><img src="'+esc(x.url)+'" loading="lazy" referrerpolicy="no-referrer" alt="'+esc(x.title||p.name+'的腾讯地图地点照片')+'">'
   +(x.title?'<figcaption>'+esc(x.title)+'</figcaption>':'')+'</figure>').join('')+'</div>':'')
 +(reviews.length?'<div class="poi-reviews"><strong>腾讯地图返回的评论</strong><ul>'+reviews.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul></div>':'')
 +(detail?.error?'<p class="map-fine" role="status">详情查询未完成：'+esc(detail.error)+'</p><button class="outline" data-map-detail>重查详情</button>'
  :detail?.loading?'<p class="map-fine" role="status">正在查询地点详情…</p>'
  :missing?'<p class="map-fine">本次未返回'+esc(missing)+'，可到腾讯地图查看是否有更多信息。</p>':'')
 +'<div class="poi-links"><a class="outline" href="'+esc(mapLink)+'"'+external+'>在腾讯地图查看地点 ↗</a>'
 +'<a class="outline" href="'+esc(navigation)+'"'+external+'>在腾讯地图打开路线 ↗</a></div>'
 +(Number.isFinite(stamp)?'<p class="map-fine">查询于 '+esc(timeCN.format(stamp))+'（中国时间）。</p>':'')+'</div>';
}
function routeCard(m){
 const p=selectedPoi(m);if(!p)return '';
 const start=originOf(m),opts=m.stops.filter(s=>s.poi&&s.poi.point!==p.point),r=m.route;
 const distance=routeDistance(r);
 return '<div class="map-route"><p class="map-fine">单段查询：从下方选定起点，去当前选中的终点 <strong>'+esc(p.name)+'</strong>。</p><div class="map-route-controls"><label>起点<select data-map-origin><option value="">选择起点</option>'
 +(m.localized?'<option value="me" '+(m.origin==='me'?'selected':'')+'>我的位置</option>':'')
 +opts.map(s=>'<option value="'+s.index+'" '+(m.origin===String(s.index)?'selected':'')+'>'+esc(s.poi.name)+'</option>').join('')
 +'</select></label><label>方式<select data-map-mode>'+Object.entries(modes).map(([k,v])=>
  '<option value="'+k+'" '+(k===m.mode?'selected':'')+'>'+v+'</option>').join('')+'</select></label>'
 +'<button class="primary" data-map-route '+(!start||start.point===p.point||m.routing||m.dailyAbort?'disabled':'')+'>'+(m.routing?'查询中…':'查看此段路线')+'</button></div>'
 +'<label class="map-fine"><input type="checkbox" data-map-geometry '+(m.geometry?'checked':'')+'> 补查道路轨迹（可能额外使用腾讯配额）</label>'
 +(m.routeError?'<p class="map-error" role="alert">'+esc(m.routeError)+'</p>':'')
 +(r?'<div class="map-route-result"><strong>'+esc(start?.name)+' → '+esc(p.name)+'</strong><div>'+esc(distance)+' · '
 +(r.duration==null?'耗时未返回':esc(Math.ceil(r.duration/60))+' 分钟')+' · 腾讯地图'+modes[m.mode]+'</div>'
 +priceLabel(r)
 +(routePaths(r).length?'<small>轨迹来源：'+esc(r.geometrySource||r.source)+'</small>':
  '<small>'+esc(r.geometryWarning||'工具未返回本段折线，可勾选补查。')+'</small>')
 +(r.steps?.length?'<details><summary>查看路线步骤</summary><ol>'+r.steps.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol></details>':'')+'</div>':'')+'</div>';
}
const metric=d=>d>=1000?(d/1000).toFixed(1)+' 公里':d+' 米';
const routeDistance=r=>r?.distance!=null?metric(r.distance):r?.walkingDistance!=null?'其中步行 '+metric(r.walkingDistance):'总距离未返回';
function priceLabel(r){
 const parts=[r.fare!=null?'票价预估 ¥'+r.fare:null,r.toll!=null?'预计过路费 ¥'+r.toll:null,
  r.taxiEstimate!=null?'打车参考 ¥'+r.taxiEstimate+'（非实时报价）':null].filter(Boolean);
 return parts.length?'<div class="map-fine">'+esc(parts.join(' · '))+'</div>':'';
}
function dailyCard(m){
 if(m.status!=='ready'||m.stops.length<2)return '';
 const saved=m.daily.get(dailyKey(m)), legs=saved?.legs||legsFor(m);
 const modesOptions=Object.entries(modes).map(([k,v])=>'<option value="'+k+'" '+(k===m.mode?'selected':'')+'>'+v+'</option>').join('');
 const disabled=m.dailyAbort||m.routing||!legsFor(m).some(l=>!l.state);
 return '<div class="map-route map-day-route"><strong>当天路线</strong><p class="map-fine">按行程顺序连接相邻已核实站点。点击绘制后才查询；缺道路坐标时会补查轨迹，额外使用腾讯配额。</p>'
 +'<div class="map-route-controls"><label>各段出行方式<select data-map-daily-mode>'+modesOptions+'</select></label>'
 +'<button class="outline" data-map-daily '+(disabled?'disabled':'')+'>'+(m.dailyAbort?'正在绘制…':saved?
   (legs.some(l=>!l.state&&!routePaths(l.route).length)||legs.some(l=>l.state&&l.from&&l.to&&l.from.point!==l.to.point)?'补查未绘制路段':'显示当天路线')
   :'绘制当天路线')+'</button></div>'
 +(saved?'<ol class="map-leg-list">'+legs.map(l=>{
  const r=l.route;
  return '<li><strong>'+esc(l.label)+'</strong><div>'+esc(l.state||(r?.duration!=null?Math.ceil(r.duration/60)+' 分钟':'耗时未返回')+
   (r?' · '+routeDistance(r):'')+(!routePaths(r).length?' · 未绘制本段':''))
   +'</div>'+(r?priceLabel(r):'')+'</li>';
 }).join('')+'</ol>':'')+'</div>';
}
function diagram(m,marks){
 if(m.status==='error')return '<div class="map-state" role="alert"><strong>地点查询未完成</strong><p>'+esc(m.error)+'</p><button class="outline" data-map-retry>重新查询</button></div>';
 if(m.status==='loading')return '<div class="map-state" role="status">'+icon+' 正在用腾讯地图 MCP 核实行程地点…</div>';
 if(!marks.length)return '<div class="map-state">当天暂无能够准确标注的地点。</div>';
 const convert=s=>String(s).split(',').map(Number);
 const fixes=marks.map(p=>convert(p.point)),paths=visiblePaths(m).map(path=>path.map(convert));
 const all=[...fixes,...paths.flat()],xs=all.map(x=>x[0]),ys=all.map(x=>x[1]),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
 const cos=Math.max(.1,Math.cos((minY+maxY)/2*Math.PI/180)),scale=Math.min(520/Math.max(.002,(maxX-minX)*cos),196/Math.max(.002,maxY-minY));
 const screen=([x,y])=>[(300+(x-(minX+maxX)/2)*cos*scale).toFixed(2),(128-(y-(minY+maxY)/2)*scale).toFixed(2)];
 return '<figure class="map-image"><svg viewBox="0 0 600 256" role="img" aria-label="当天地点相对位置与已返回的路线示意" class="map-diagram">'
 +paths.map(line=>'<polyline points="'+line.map(x=>screen(x).join(',')).join(' ')+'" />').join('')
 +fixes.map((point,i)=>{
  const [x,y]=screen(point),on=marks[i].key===String(m.selected);
  return '<g transform="translate('+x+' '+y+')" class="'+(on?'map-mark-selected':'map-mark')+'"><title>'+esc(marks[i].name)
    +'</title><circle r="14"/><text y="1">'+(i+1)+'</text></g>';
 }).join('')+'</svg><figcaption>地点坐标示意 · 腾讯地图 MCP（非道路底图）</figcaption></figure>';
}
function mapPreview(m,marks){
 const snap=m.snapshot;
 if(snap?.url)return '<figure class="map-image"><img data-map-static-image class="map-static" src="'+esc(snap.url)
  +'" width="900" height="410" alt="腾讯地图静态底图：当天已核实地点'+(visiblePaths(m).length?'和当前路线':'')
  +'"><figcaption>腾讯静态地图 · 编号对应下方地点</figcaption></figure>';
 const error=m.status==='ready'&&marks.length?(snap?.error||m.staticRouteMissing):'';
 return diagram(m,marks)+(snap?.loading?'<p class="map-fine" role="status">正在加载腾讯静态底图…</p>'
  :error?'<div class="map-static-fallback"><span role="status">'+esc(error)
   +' 已显示坐标示意图。</span><button class="outline" data-map-static-retry>重试底图</button></div>':'');
}
function draw(m,{refreshSnapshot=false}={}){
 if(!current(m))return;
 const old=m.host.contains(document.activeElement)?document.activeElement:null;
 const focusKey=old?.getAttribute('data-map-focus');
 const controls='<div class="trip-map-head"><div><span class="eyebrow">ON THE MAP · 每日地图</span><h2>从这站到下一站</h2><p>核实每天的地点与交通路线。</p></div><button class="outline" data-map-locate data-map-focus="locate" '+(!m.configured||m.locating?'disabled':'')+'>'+icon+' '+(m.locating?'正在定位…':'使用我的位置')+'</button></div>';
 const disabled=m.configured===false?'请在服务端填入腾讯地图 WebService Key；你仍可以查看行程草案。':'正在检查本地地图服务。';
 if(!m.configured){m.host.innerHTML=controls+'<p class="map-state">'+disabled+'</p>'+(m.configured===false?poiCard(m):'');return;}
 const days='<div class="map-days" role="group" aria-label="选择行程天数">'+m.days.map((_,i)=>'<button class="map-day" data-map-day="'+i+'" data-map-focus="day'+i+'" aria-pressed="'+(m.selectedDay===i)+'">第 '+(i+1)+' 天</button>').join('')+'</div>';
 const marks=m.stops.filter(s=>s.poi).map(s=>({key:String(s.index),point:s.poi.point,name:s.poi.name}));
 if(m.localized)marks.push({key:'me',point:m.localized.point,name:'我的位置'});
 syncSnapshot(m,marks,refreshSnapshot);
 const index=new Map(marks.map((s,i)=>[s.key,i+1]));
 const items='<div class="map-stops" role="group" aria-label="选择当天地点">'+m.days[m.selectedDay].map((x,i)=>{
  const stop=stopData(m,i);
  return '<button class="map-stop" data-map-stop="'+i+'" data-map-focus="stop'+i+'" aria-pressed="'+(m.selected===i)+'"><span class="map-pin">'+(index.get(String(i))||'·')+'</span><span><small>'+esc(x.time)+'</small><strong>'+esc(stop?.poi?.name||x.location)+'</strong><small>'+esc(stopLabel[stop?.status]||'待核实')+'</small></span></button>';
 }).join('')+'</div>';
 const privacy=m.localized?'<div class="map-location"><strong>地图编号 '+(index.get('me')||'—')+' · 我的位置</strong> '+esc(m.localized.address||'已取得坐标')+' <button class="text-button" data-map-forget>移除位置</button></div>'
  :'<p class="map-fine">仅在点击并授权后，才会用你的位置查询腾讯地图和绘制底图；离开行程页即清除。</p>';
 m.host.innerHTML=controls+privacy+(m.locError?'<div class="map-static-fallback"><span class="map-error" role="alert">'+esc(m.locError)
  +'</span><button class="outline" data-map-locate-retry>重新获取位置</button></div>':'')+days
 +'<div class="map-layout"><div class="map-main">'+mapPreview(m,marks)+(m.warning?'<p class="map-error" role="status">'+esc(m.warning)+'</p>':'')+items+dailyCard(m)+routeCard(m)
 +'</div><aside class="map-poi" tabindex="-1" aria-label="地点详情与导航">'+poiCard(m)+'</aside></div>';
 m.host.querySelectorAll('[data-map-day]').forEach(b=>b.onclick=()=>{m.selectedDay=+b.dataset.mapDay;m.selected=0;void loadDay(m);});
 m.host.querySelectorAll('[data-map-stop]').forEach(b=>b.onclick=()=>select(m,+b.dataset.mapStop));
 const bind=(name,fn,event='onclick')=>{const el=m.host.querySelector('[data-map-'+name+']');if(el)el[event]=()=>fn(el);};
 bind('locate',()=>locate(m));
 bind('locate-retry',()=>locate(m));
 bind('forget',()=>{m.localized=null;clearRoute(m);setOrigin(m);draw(m);});
 bind('origin',el=>{m.origin=el.value;clearRoute(m);draw(m);},'onchange');
 const chooseMode=el=>{m.dailyAbort?.abort();m.dailyAbort=null;m.mode=el.value;clearRoute(m);draw(m);};
 bind('mode',chooseMode,'onchange');
 bind('daily-mode',chooseMode,'onchange');
 bind('geometry',el=>{m.geometry=el.checked;},'onchange');
 bind('daily',()=>void plotDaily(m));
 bind('route',()=>void route(m));
 bind('retry',()=>void loadDay(m));
 bind('static-retry',()=>draw(m,{refreshSnapshot:true}));
 const staticImage=m.host.querySelector('[data-map-static-image]'),imageState=m.snapshot;
 if(staticImage&&imageState?.url)staticImage.onerror=()=>{
  if(!current(m)||m.snapshot!==imageState||!imageState.url)return;
  URL.revokeObjectURL(imageState.url);imageState.url=null;imageState.error='静态底图无法显示。';draw(m);
 };
 bind('detail',()=>void loadDetail(m,{force:true}));
 bind('verify',()=>void verifySelected(m));
 if(m.scrollPoi&&(m.status==='ready'||m.status==='error')){
  m.scrollPoi=false;
  queueMicrotask(()=>{
   if(!current(m))return;
   const card=m.host.querySelector('.map-poi');
   card?.focus({preventScroll:true});card?.scrollIntoView({block:'start'});
  });
 }else if(focusKey)m.host.querySelector('[data-map-focus="'+CSS.escape(focusKey)+'"]')?.focus({preventScroll:true});
}
