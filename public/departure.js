const esc=(x='')=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modes={direct:'只看直达',auto:'直达优先，无直达再换乘',transfer:'只看一次换乘'};
export function departureFields(trip,travel){
 return {origin:trip.origin||travel?.origin||'',destination:trip.city||travel?.destination||'',date:trip.startDate||'',time:'',
  mode:'direct',middle:'',filter:'',originSource:'manual',...travel?.request};
}
const seatState=x=>/^\d+$/.test(x??'')?(Number(x)?'余 '+x+' 张':'无余票'):
 ({有:'有票',充足:'有票',无:'无余票',候补:'候补',暂售:'暂售',停售:'停售'})[x]||'';
function train(row){
 const seats=(row.seats||[]).filter(x=>x.price!=null||seatState(x.availability));
 const priority=['二等座','一等座','硬座','硬卧','软卧','商务座','无座'];
 seats.sort((a,b)=>(priority.indexOf(a.name)+1||99)-(priority.indexOf(b.name)+1||99));
 return '<div class="rail-train"><b>'+esc(row.code)+'</b><span>'+esc(row.from)+' <strong>'+esc(row.depart)+'</strong> → '+esc(row.to)+' <strong>'+esc(row.arrive)+'</strong></span>'
  +(row.duration?'<small>历时 '+esc(row.duration)+'</small>':'')+'<div class="rail-seats">'+(seats.length?seats.map(s=>
   '<span>'+esc(s.name)+(s.price==null?'':' <strong>¥'+esc(s.price)+'</strong>')+' · '+esc(seatState(s.availability)||'余票未返回')+'</span>').join(''):
   '<small>上游未返回票价或余票</small>')+'</div></div>';
}
function railway(travel){
 const old=(travel?.options||[]).find(x=>x.mode==='铁路');
 const r=travel?.rail||old;
 const items=Array.isArray(r?.items)?r.items:[];
 const checked=r?.checkedAt&&Number.isFinite(Date.parse(r.checkedAt))?new Intl.DateTimeFormat('zh-CN',{
  timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(r.checkedAt)):'';
 return '<div class="departure-rail"><div class="departure-rail-head"><h3>铁路候选</h3>'+(checked?'<span>12306 MCP · '+esc(checked)+' 查询</span>':'')+'</div>'
  +'<p class="muted">'+esc(r?.note||'请填写出发地，或用设备定位城市。')+'</p>'
  +items.map(row=>r.action==='transfer'?'<div class="rail-interline"><p><strong>在 '+esc(row.middle)+' 换乘</strong>'
    +(row.nextStation?' · 需换站至 '+esc(row.nextStation):'')+(row.wait?' · 等候 '+esc(row.wait):'')+(row.duration?' · 总历时 '+esc(row.duration):'')+'</p>'
    +row.segments.map(train).join('')+'</div>':train(row)).join('')+'</div>';
}
const duration=seconds=>seconds>=3600?Math.floor(seconds/3600)+' 小时 '+Math.ceil(seconds%3600/60)+' 分钟':Math.ceil(seconds/60)+' 分钟';
export function departureMarkup(trip,travel,ui){
 const input={...departureFields(trip,travel),...ui?.draft};
 const date=travel?.request?travel.date:input.date,time=travel?.request?travel.time:input.time;
 const drive=(travel?.options||[]).find(x=>x.mode==='自驾');
 const val=k=>esc(input[k]||'');
 const option=(k,label)=>'<option value="'+k+'"'+(input.mode===k?' selected':'')+'>'+label+'</option>';
 return '<span class="eyebrow">BEFORE YOU ARRIVE</span><h2>出发与抵达</h2>'
  +'<p class="departure-line"><strong>'+esc(travel?.origin||trip.origin||'出发地待确认')+'</strong><span aria-hidden="true"> → </span><strong>'+esc(travel?.destination||input.destination)+'</strong>'
  +(travel?.request?.originSource==='device'?'<small>定位城市</small>':'')+'</p>'
  +'<p class="muted departure-meta">'+(date?esc(date+(time?' '+time+' 起':'')+'（北京时间）'):'未填日期时，默认查询今天')
  +(travel?.dateDefault?' · 默认今天，仅用于查票':'')+(travel?.timeAdjusted?' · 已排除今天过去的发车时间':'')
  +' · '+esc(modes[travel?.request?.mode||input.mode]||modes.direct)+'</p>'
  +'<details class="departure-edit"'+(ui?.open||!input.origin?' open':'')+'><summary>调整出发条件</summary>'
  +'<form data-departure-form class="field-form"><fieldset class="departure-fieldset"'+(ui?.pending==='query'?' disabled':'')+'><div class="departure-fields"><label>出发城市或车站<input name="origin" maxlength="30" value="'+val('origin')+'" placeholder="例如上海虹桥"></label>'
  +'<label>到达城市或车站<input name="destination" maxlength="30" value="'+val('destination')+'" required></label>'
  +'<label>出发日期<input type="date" name="date" value="'+val('date')+'" aria-describedby="departure-date-help"><small id="departure-date-help">留空用今天</small></label>'
  +'<label>最早出发<input type="time" name="time" value="'+val('time')+'" aria-describedby="departure-time-help"><small id="departure-time-help">当天留空用现在</small></label>'
  +'<label>铁路方式<select name="mode">'+Object.entries(modes).map(([k,v])=>option(k,v)).join('')+'</select></label>'
  +'<label data-departure-middle'+(input.mode==='direct'?' hidden':'')+'>指定换乘城市或车站<input name="middle" maxlength="30" value="'+val('middle')+'" placeholder="可不填"></label></div>'
  +'<div class="departure-actions"><button type="submit" class="primary"'+(ui?.pending==='query'?' disabled':'')+'>查这个行程的车票</button>'
  +'<button type="button" class="outline" data-departure-locate'+(ui?.pending?' disabled':'')+'>'+(ui?.locationFailed?'重新获取出发城市':'使用定位城市')+'</button>'
  +'<label class="checkbox-label"><input type="checkbox" name="highspeed"'+(input.filter==='G'?' checked':'')+'>只看高铁／城际</label></div></fieldset>'
  +'<small class="muted">只改这张卡的交通条件，不会自动修改景点、天气或日历。定位只取城市，原始坐标不保存。</small></form></details>'
  +(ui?.error?'<p class="map-error" role="status">'+esc(ui.error)+'</p>':'')
  +(ui?.pending?'<p class="muted" role="status">'+(ui.pending==='locating'?'正在获取设备所在城市…':'正在查询 12306 与交通路线…')+'</p>':railway(travel))
  +(drive?'<p class="departure-drive">自驾参考 · '+esc([drive.duration==null?'':duration(drive.duration),drive.distance==null?'':(drive.distance/1000).toFixed(1)+' 公里',drive.toll==null?'':'过路费估算 ¥'+drive.toll].filter(Boolean).join(' · '))+'</p>':'')
  +'<p class="departure-links"><a href="https://www.12306.cn/" target="_blank" rel="noopener noreferrer">到铁路 12306 核实 ↗</a></p>';
}

export function bindDeparture(host,previous,callbacks){
 const form=host?.querySelector('[data-departure-form]');
 if(!form)return;
 const read=()=>{
  const raw=Object.fromEntries(new FormData(form));
  return {...raw,originSource:raw.origin.trim()===previous.origin?previous.originSource:'manual',
   filter:raw.highspeed==='on'?'G':'',highspeed:undefined,skipRail:false};
 };
 form.elements.mode.onchange=()=>{form.querySelector('[data-departure-middle]').hidden=form.elements.mode.value==='direct';};
 form.oninput=()=>callbacks.edit(read());
 form.onsubmit=e=>{e.preventDefault();callbacks.submit(read());};
 form.querySelector('[data-departure-locate]').onclick=()=>callbacks.locate(read());
}
