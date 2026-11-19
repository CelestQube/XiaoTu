export function openRailDialog(trip,open,esc){
 const dialog=open('<h2 id="dialog-title">铁路查询</h2><p class="muted">来源是第三方 12306 MCP。结果会变化，不会代购。</p><form class="field-form" id="rail-form">'
  +'<label>查询类型<select name="action"><option value="tickets">直达车次、余票和票价</option><option value="transfer">一次换乘</option><option value="stops">车次经停</option><option value="stations">车站与三字码</option></select></label>'
  +'<div class="form-row" data-rail-fields="line"><label>出发站<input name="origin" maxlength="30" value="'+esc(trip.origin||'')+'" placeholder="例如：上海虹桥"></label><label>到达站<input name="destination" maxlength="30" value="'+esc(trip.city||'')+'" placeholder="例如：杭州东"></label></div>'
  +'<label data-rail-fields="date">出发日期<input name="date" type="date" value="'+esc(trip.startDate||'')+'"></label>'
  +'<label data-rail-fields="train">车次<input name="trainCode" maxlength="8" placeholder="例如：G39（查直达时可不填）"></label>'
  +'<label data-rail-fields="station">城市、车站名或三字码<input name="keyword" maxlength="30" placeholder="例如：杭州、杭州东站或 HGH"></label>'
  +'<button class="primary">查询铁路信息</button><p class="form-error" data-rail-error role="alert"></p><div class="rail-result" data-rail-result role="status"></div></form>');
 const form=dialog.querySelector('form'),summary=dialog.querySelector('[data-rail-result]'),error=dialog.querySelector('[data-rail-error]'),button=form.querySelector('button');
 const life=new AbortController();dialog.addEventListener('close',()=>life.abort(),{once:true});
 function toggle(){
  const action=form.elements.action.value;
  const fields={line:['tickets','transfer'].includes(action),date:action!=='stations',train:['tickets','stops'].includes(action),station:action==='stations'};
  for(const group of form.querySelectorAll('[data-rail-fields]')){
   const on=fields[group.dataset.railFields];group.hidden=!on;
   for(const input of group.querySelectorAll('input')){input.disabled=!on;input.required=on&&(input.name!=='trainCode'||action==='stops');}
  }
 }
 toggle();form.elements.action.onchange=()=>{toggle();summary.textContent='';error.textContent='';};
 form.onsubmit=async e=>{
  e.preventDefault();
  const body=Object.fromEntries(new FormData(form));
  button.disabled=true;form.elements.action.disabled=true;error.textContent='';summary.textContent='正在查询铁路信息…';
  try{
   const res=await fetch('/api/rail/query',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body),
    signal:AbortSignal.any([life.signal,AbortSignal.timeout(40000)])});
   const data=await res.json();
   if(!res.ok)throw new Error(data.error||'铁路查询未完成。');
   summary.textContent=data.reply;
  }catch(e){if(!life.signal.aborted){summary.textContent='';error.textContent=e.name==='TimeoutError'?'查询超时，请稍后重试或到铁路 12306 核实。':e.message;}}
  finally{button.disabled=false;form.elements.action.disabled=false;}
 };
}
