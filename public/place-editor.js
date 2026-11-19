const esc=(v='')=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const endTime=ms=>AbortSignal.timeout(ms);
async function post(path,body,signal,ms=17000){
 const r=await fetch('/api/'+path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body),
  signal:AbortSignal.any([signal,endTime(ms)])});
 const data=await r.json();
 if(!r.ok)throw new Error(data.error||'查询未完成，请重试。');
 return data;
}
const errorText=e=>e.name==='TimeoutError'?'本次查询超时，可以重试。':e.message;

export function editPlace({trip,plan,day,index,openDialog,stillCurrent,onSave,onUnlock}){
 const item=plan.days[day].items[index],body={trip,plan,day,index};
 const dialog=openDialog('<div class="place-editor"><span class="eyebrow">DAY '+String(day+1).padStart(2,'0')+' · '+esc(item.time)+'</span>'
  +'<h2 id="dialog-title">'+(item.mapStatus==='placeholder'?'为这项活动选地点':'更换活动地点')+'</h2>'
  +'<p class="muted">'+esc(trip.city)+' · 当前：'+esc(item.location)+(item.locked?' · 已保留':'')+'</p>'
  +'<div class="place-chooser"><section class="suggested-places" aria-label="推荐地点"><h3>可替换的同类地点</h3><p class="muted" data-pick-info role="status">正在查看可推荐的地点…</p><div data-pick-places></div></section>'
  +'<form class="field-form place-search"><label>搜索'+esc(trip.city)+'的地点<input name="keyword" maxlength="70" required placeholder="输入景点名、餐厅名或地标"></label>'
  +'<button class="outline" type="submit">搜索地图</button></form><p class="muted" data-search-info role="status"></p><div data-search-places></div>'
  +'<button class="text-button place-manual" type="button" data-place-manual hidden>手动使用这个名称（待核实）</button>'
  +(item.locked?'<p class="muted">此地点会在后续重新规划时保留。 <button type="button" class="text-button" data-place-unlock>取消保留</button></p>':'')+'</div>'
  +'<div data-place-preview hidden></div><p class="form-error" role="alert" data-place-error></p></div>');
 const root=dialog.querySelector('.place-editor'),get=s=>root.querySelector(s),form=get('form'),keyword=form.elements.keyword;
 const life=new AbortController();let search,review;
 dialog.addEventListener('close',()=>life.abort(),{once:true});
 const active=()=>root.isConnected&&dialog.open&&!life.signal.aborted;
 const showError=s=>{get('[data-place-error]').textContent=s;};
 const current=()=>stillCurrent()||(showError('行程已变化。请关闭后从最新行程重新打开。'),false);
 const renderList=(node,places,rec=false)=>{
  node.replaceChildren();
  for(const p of places){
   const button=document.createElement('button');button.type='button';button.className='place-option';
   button.innerHTML='<span><strong>'+esc(p.name)+'</strong><small>'+esc([p.type,p.address].filter(Boolean).join(' · ')||'腾讯地图地点')+'</small></span>'
    +'<span class="place-option-hint">'+(rec&&p.nearby===true?'较顺路':rec&&p.nearby===false?'可能绕路':'预览')+'</span>';
   button.onclick=()=>void preview({poi:p});node.append(button);
  }
 };
 async function recommendations(){
  const info=get('[data-pick-info]');
  try{
   const {places,warning}=await post('plan/place-options',body,life.signal,24000);
   if(!active())return;
   info.textContent=places.length?'优先显示未安排的同类地点，按前后站点估算是否顺路。'
    :warning||'暂时没有合适的同类推荐。可以搜索具体地点。';
   renderList(get('[data-pick-places]'),places,true);
  }catch(e){if(active())info.textContent=errorText(e)+' 你仍可搜索或手填地点。';}
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();const word=keyword.value.trim();if(!word||!current())return;
  search?.abort();search=new AbortController();const controller=search;
  const info=get('[data-search-info]'),list=get('[data-search-places]');
  showError('');list.replaceChildren();info.textContent='正在搜索地图…';
  try{
   const data=await post('maps/search',{city:trip.city,keyword:word},AbortSignal.any([life.signal,controller.signal]));
   if(!active()||controller.signal.aborted)return;
   info.textContent=data.places.length?'请核对名称与地址，再选择一条结果。':'这座城市没有匹配结果。试试完整地名，或手动保留名称。';
   renderList(list,data.places);
  }catch(e){if(active()&&!controller.signal.aborted)info.textContent=errorText(e)+' 你也可以手填名称。';}
 });
 keyword.addEventListener('input',()=>{
  search?.abort();get('[data-search-info]').textContent='';get('[data-search-places]').replaceChildren();
  get('[data-place-manual]').hidden=!keyword.value.trim();showError('');
 });
 get('[data-place-manual]').onclick=()=>{if(keyword.value.trim())void preview({manual:true,name:keyword.value.trim()});};
 const unlock=get('[data-place-unlock]');
 if(unlock)unlock.onclick=()=>{if(current()){dialog.close();onUnlock();}};
 function describe(changed){
  const lines=[];
  for(const {index:i,items} of changed){
   const before=new Map(plan.days[i].items.map(x=>[x.time,x.location])),after=new Map(items.map(x=>[x.time,x.location]));
   for(const time of [...new Set([...before.keys(),...after.keys()])].sort()){
    const old=before.get(time),next=after.get(time);
    if(old!==next)lines.push('第 '+(i+1)+' 天 '+time+'：'+([old,next].every(Boolean)?old+' → '+next:next?'新增 '+next:'移除 '+old));
   }
  }
  return lines.length?lines:['地点和时间不变，只更新核实或保留状态。'];
 }
 async function preview(choice){
  if(!current())return;
  review?.abort();review=new AbortController();const controller=review;
  const chooser=get('.place-chooser'),stage=get('[data-place-preview]');
  chooser.hidden=true;stage.hidden=false;showError('');stage.innerHTML='<p class="muted" role="status">正在比较地点改动…</p><button type="button" class="outline" data-review-back>返回选地点</button>';
  const back=()=>{controller.abort();stage.hidden=true;chooser.hidden=false;showError('');keyword.focus();};
  get('[data-review-back]').onclick=back;
  try{
   const {location,options}=await post('plan/place-preview',{...body,choice},AbortSignal.any([life.signal,controller.signal]));
   if(!active()||controller.signal.aborted)return;
   const selected=options.find(o=>o.recommended)?.id||options[0].id;
   stage.innerHTML='<h3 tabindex="-1">选择怎样安排「'+esc(location)+'」</h3><p class="muted">确认前不会修改行程。新选地点默认保留，后续规划不会自动换掉。</p>'
    +'<form data-apply><fieldset class="place-plans"><legend>调整方案</legend>'+options.map(o=>'<label class="place-plan-option">'
     +'<input type="radio" name="option" value="'+esc(o.id)+'"'+(o.id===selected?' checked':'')+'><span><strong>'+esc(o.label)+'</strong>'
     +(o.recommended?'<small class="place-plan-rec">推荐</small>':'')+'<span class="place-diff">'+describe(o.days).map(x=>'<span>'+esc(x)+'</span>').join('')+'</span>'
     +'<span class="place-plan-notes">'+o.notes.map(x=>'<span>'+esc(x)+'</span>').join('')+'</span></span></label>').join('')+'</fieldset>'
    +'<div class="page-actions"><button type="button" class="outline" data-review-back>返回选地点</button><button type="submit" class="primary">应用所选方案</button></div></form>';
   get('[data-review-back]').onclick=back;stage.querySelector('h3').focus();
   get('[data-apply]').onsubmit=event=>{
    event.preventDefault();if(!current())return;
    const option=options.find(o=>o.id===new FormData(event.currentTarget).get('option'));
    if(option){dialog.close();onSave({...option,location});}
   };
  }catch(e){if(active()&&!controller.signal.aborted){back();showError(errorText(e));}}
 }
 void recommendations();
}
