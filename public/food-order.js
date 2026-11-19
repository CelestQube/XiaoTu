// Isolated mock checkout. This module never requests a real address or payment.
export const FOOD_STORAGE_KEY = 'xiaotu.food-demo.v1';
const escape = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currency = value => Number(value).toFixed(2);
const id = value => typeof value === 'string' && /^[\w-]{1,128}$/.test(value);
const label = value => typeof value === 'string' && value.length > 0 && value.length <= 160;
const receiptStates = new Set(['awaiting_confirmation','pending','succeeded','failed']);
function safeStorage() { try { return globalThis.localStorage; } catch { return null; } }

function quoteFrom(value) {
  if (!value || !id(value.quoteId) || !id(value.itemId) || !label(value.name) || !label(value.merchant)
    || !Number.isInteger(value.quantity) || value.quantity < 1 || value.quantity > 10
    || !Number.isFinite(value.total) || value.total < 0 || value.total > 1000000
    || typeof value.expiresAt !== 'string' || !Number.isFinite(Date.parse(value.expiresAt))) return null;
  const {quoteId,itemId,name,merchant,quantity,total,expiresAt} = value;
  return {quoteId,itemId,name,merchant,quantity,total,expiresAt};
}

// Persist only operation identifiers and the mock quote, never arbitrary API data.
export function restoreFoodOrder(raw) {
  try {
    const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (data?.version !== 1) return null;
    if (data.phase === 'quote_unknown' && !data.quote) return {quote:null,attempted:false,phase:'quote_unknown'};
    const quote = quoteFrom(data.quote);
    if (!quote) return null;
    const attempted = data.attempted === true;
    return {quote,attempted,phase:attempted ? 'unknown' : 'awaiting_confirmation'};
  } catch { return null; }
}

export function foodOrderEntry(status) {
  return status?.foodDemo === true
    ? '<button class="food-entry" data-action="food-demo" aria-label="打开模拟外卖，仅测试不付款">模拟外卖<span>不付款</span></button>' : '';
}

export function createFoodOrderController({fetchImpl=globalThis.fetch,storage=safeStorage(),now=Date.now,timeoutMs=15000}={}) {
  let saved;
  try { saved = restoreFoodOrder(storage?.getItem(FOOD_STORAGE_KEY)); } catch { /* In-memory menu remains available. */ }
  const state = {items:[],keyword:'',quote:null,attempted:false,phase:'menu',receipt:null,busy:'',error:'',storageError:false,...saved};
  const listeners = new Set();
  let flight = null;
  function snapshot() {
    const expired = !!state.quote && Date.parse(state.quote.expiresAt) <= now();
    return structuredClone({...state,expired,canConfirm:state.phase==='awaiting_confirmation' && !expired && !state.busy,
      canReset:!state.busy && (!state.attempted || ['succeeded','failed'].includes(state.phase)
        || (state.phase==='awaiting_confirmation' && state.receipt?.status==='awaiting_confirmation'))});
  }
  function emit() { for (const fn of listeners) fn(snapshot()); }
  function persist() {
    try {
      if (!storage) throw new Error('storage unavailable');
      storage.setItem(FOOD_STORAGE_KEY, JSON.stringify({version:1,quote:state.quote,attempted:state.attempted,phase:state.phase}));
      state.storageError=false; return true;
    } catch { state.storageError=true; return false; }
  }
  async function request(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl('/api/food/'+path,{method:'POST',credentials:'same-origin',headers:{'content-type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.error==='string' ? result.error : '模拟服务未完成请求。');
      return result;
    } finally { clearTimeout(timer); }
  }
  function run(kind, task) {
    if (flight) return flight;
    state.busy=kind;state.error='';emit();
    flight=Promise.resolve().then(task).finally(()=>{state.busy='';flight=null;emit();});
    return flight;
  }
  function acceptReceipt(result) {
    if (result?.quoteId !== state.quote?.quoteId || !receiptStates.has(result.status)
      || (result.status!=='awaiting_confirmation' && !id(result.operationId))
      || (result.orderId !== undefined && !id(result.orderId))) throw new Error('回执格式不匹配，请查询原报价状态。');
    state.phase=result.status;
    state.receipt={quoteId:result.quoteId,status:result.status};
    if (result.operationId) state.receipt.operationId=result.operationId;
    if (result.orderId) state.receipt.orderId=result.orderId;
    persist();
  }
  const api = {
    getState:snapshot,
    subscribe(fn) { listeners.add(fn); return ()=>listeners.delete(fn); },
    refresh:emit,
    search(keyword='') {
      if (flight || state.quote || state.phase==='quote_unknown') return flight || Promise.resolve();
      state.keyword=String(keyword).trim().slice(0,80);
      return run('menu',async()=>{
        try {
          const result=await request('menu',{keyword:state.keyword});
          if (!Array.isArray(result.items)) throw new Error('模拟菜单格式不完整。');
          state.items=result.items.filter(x=>id(x.id)&&label(x.name)&&label(x.merchant)&&Number.isFinite(x.price)&&x.price>=0&&x.price<=1000000)
            .map(({id,name,merchant,price})=>({id,name,merchant,price}));
          state.phase='menu';
        } catch (error) { state.error=error.name==='AbortError'?'菜单查询超时，请手动重试。':error.message; }
      });
    },
    quote(itemId, quantity) {
      if (flight) return flight;
      if (state.quote || state.phase==='quote_unknown') return Promise.resolve();
      if (!state.items.some(x=>x.id===itemId)||!Number.isInteger(quantity)||quantity<1||quantity>10) {
        state.error='请选择菜单中的餐品，数量为 1–10 份。';emit();return Promise.resolve();
      }
      // Record the uncertainty before dispatch; refresh must not auto-create another quote.
      state.phase='quote_unknown';persist();
      return run('quote',async()=>{
        try {
          const result=await request('quote',{itemId,quantity});
          const quote=quoteFrom(result);
          if (!quote || result.status!=='awaiting_confirmation' || quote.itemId!==itemId || quote.quantity!==quantity) throw new Error('报价内容未通过校验，未发起确认。');
          state.quote=quote;state.attempted=false;state.phase='awaiting_confirmation';state.receipt=null;persist();
        } catch (error) { state.error='未收到可确认的报价，未发起模拟下单。不会自动重试报价；可关闭后稍后再试。'+(error.name==='AbortError'?'（请求超时）':' '+error.message); }
      });
    },
    confirm(confirmed) {
      if (flight) return flight;
      if (confirmed!==true) {state.error='请先勾选确认：仅测试，不付款、不配送。';emit();return Promise.resolve();}
      if (!snapshot().canConfirm) {state.error='此报价当前不能确认。请查看到期时间或查询原报价状态。';emit();return Promise.resolve();}
      const previouslyAttempted=state.attempted;
      state.attempted=true;state.phase='unknown';
      if (!persist()) {
        state.attempted=previouslyAttempted;state.phase='awaiting_confirmation';
        state.error='浏览器无法保存报价标识，已阻止确认。请允许本地存储后再试，以便刷新后查询结果。';emit();return Promise.resolve();
      }
      return run('confirm',async()=>{
        try {acceptReceipt(await request('confirm',{quoteId:state.quote.quoteId,confirmed:true}));}
        catch (error) {state.phase='unknown';state.error='确认结果暂不明确。请查询原报价状态，不要重新生成报价。'+(error.name==='AbortError'?'（请求超时）':' '+error.message);persist();}
      });
    },
    checkStatus() {
      if (flight) return flight;
      if (!state.quote) return Promise.resolve();
      return run('status',async()=>{
        try {acceptReceipt(await request('status',{quoteId:state.quote.quoteId}));}
        catch (error) {
          if(state.attempted)state.phase='unknown';
          state.error='暂未查到可靠结果，已保留原报价标识。可稍后再次查询；不会自动确认或新建报价。'+(error.name==='AbortError'?'（请求超时）':' '+error.message);persist();
        }
      });
    },
    reset() {
      if (!snapshot().canReset) return false;
      try {storage?.removeItem(FOOD_STORAGE_KEY);} catch {state.error='无法清除旧报价，请检查浏览器存储设置。';emit();return false;}
      Object.assign(state,{quote:null,attempted:false,phase:'menu',receipt:null,error:'',storageError:false});emit();return true;
    },
  };
  return api;
}

export function foodOrderMarkup(state) {
  const q=state.quote;
  const title={awaiting_confirmation:'确认前，再看一眼',unknown:'结果待核实',pending:'模拟操作处理中',succeeded:'已收到模拟成功回执',failed:'模拟操作未成功'}[state.phase];
  const message={unknown:'请求可能已被服务接收。保留原报价，只查询它的状态。',pending:'请稍后查询本次操作，不要重复确认或另建报价。',succeeded:'这只是模拟成功，没有真实扣款，也不会配送。',failed:'服务返回失败回执，没有创建真实外卖订单。'}[state.phase];
  const common='<div class="food-demo-label">开发验证 · 模拟外卖</div><h2 id="food-dialog-title">给旅途，加一餐。</h2><p class="food-safety">仅测试，不付款、不配送。这里的餐品、商家和金额都是模拟数据。</p>';
  const error=state.error?'<p class="food-error" role="alert">'+escape(state.error)+'</p>':'';
  if(!q) return common+(state.phase==='quote_unknown'
    ? '<section class="food-result"><h3>报价响应未收到</h3><p>没有取得可查询的报价标识；本次没有发起确认。不会自动重试或模拟下单。</p>'+error+'<button class="outline" data-food="reset" '+(state.busy?'disabled':'')+'>放弃此次报价尝试，重新选餐</button></section>'
    : '<form class="food-search"><label for="food-keyword">寻找模拟餐品</label><div><input id="food-keyword" name="keyword" maxlength="80" placeholder="例如：米饭" value="'+escape(state.keyword)+'" '+(state.busy?'disabled':'')+'><button class="outline" '+(state.busy?'disabled':'')+'>查询菜单</button></div></form>'+error+'<div class="food-menu">'+(state.items.length?state.items.map(item=>'<form class="food-item" data-food-item="'+escape(item.id)+'"><div><span class="food-merchant">'+escape(item.merchant)+'</span><h3>'+escape(item.name)+'</h3><strong>¥'+currency(item.price)+'<small> / 份 · 模拟价</small></strong></div><div class="food-item-actions"><label>数量<input name="quantity" type="number" min="1" max="10" value="1" required aria-label="'+escape(item.name)+'数量" '+(state.busy?'disabled':'')+'></label><button class="primary small" '+(state.busy?'disabled':'')+'>查看模拟报价</button></div></form>').join(''):'<p class="food-empty">'+(state.busy?'正在查询模拟菜单…':'暂无匹配餐品，可换个关键词查询。')+'</p>')+'</div>')+'<p class="food-footnote">不需要姓名、手机号、地址或支付信息。</p>';
  return common+'<section class="food-ticket"><div class="food-ticket-heading"><span>模拟确认单</span><strong>'+escape(title||'报价待核实')+'</strong></div><h3>'+escape(q.name)+'</h3><p>'+escape(q.merchant)+'</p><dl><div><dt>数量</dt><dd>'+q.quantity+' 份</dd></div><div class="food-total"><dt>模拟合计</dt><dd>¥'+currency(q.total)+'</dd></div><div><dt>报价到期</dt><dd>'+escape(new Date(q.expiresAt).toLocaleString('zh-CN',{hour12:false}))+'</dd></div></dl><p class="food-id">报价标识 <code>'+escape(q.quoteId)+'</code></p></section>'
    +(message?'<p class="food-result-message" role="status">'+message+'</p>':'')
    +(state.receipt?.orderId?'<p class="food-id">模拟订单号 <code>'+escape(state.receipt.orderId)+'</code></p>':'')
    +(state.receipt?.operationId?'<p class="food-id">操作标识 <code>'+escape(state.receipt.operationId)+'</code></p>':'')
    +error+(state.storageError?'<p class="food-error">无法保存状态，确认操作将被阻止。</p>':'')
    +(state.phase==='awaiting_confirmation'?(state.expired?'<p class="food-error">报价已到期，不能确认。已尝试过确认的报价请先查询状态。</p>':'<form class="food-confirm"><label><input type="checkbox" name="confirmed" required '+(state.busy?'disabled':'')+'>我已核对餐品、数量与模拟金额，确认仅测试，不付款、不配送。</label><button class="primary" '+(!state.canConfirm?'disabled':'')+'>'+(state.attempted?'再次确认同一报价（仅测试，不付款）':'确认模拟下单（仅测试，不付款）')+'</button></form>'):'')
    +'<div class="food-actions"><button class="outline" data-food="status" '+(state.busy?'disabled':'')+'>'+(state.busy==='status'?'正在查询…':'查询原报价状态')+'</button>'+(state.canReset?'<button class="text-button" data-food="reset">'+(state.attempted?'开始另一笔模拟':'放弃此报价，重新选餐')+'</button>':'')+'</div>'
    +(state.busy?'<p class="food-working" role="status">'+({confirm:'正在确认模拟操作，请勿重复点击…',quote:'正在取得模拟报价…',status:'正在核实同一个报价的状态…'}[state.busy]||'正在查询…')+'</p>':'')
    +'<p class="food-footnote">关闭窗口不会撤销已发起的模拟操作；再次打开或刷新后可继续查询同一报价。</p>';
}

let controller;
export function clearFoodOrderStorage() {
  const storage=safeStorage();
  try {storage?.removeItem(FOOD_STORAGE_KEY);} catch { /* Existing global clear action handles its own feedback. */ }
  controller=undefined;
}

export function openFoodOrder(status) {
  if(status?.foodDemo!==true) return;
  const active=document.querySelector('.food-dialog');
  if(active?.open){active.focus();return;}
  controller ||= createFoodOrderController();
  const flow=controller,dialog=document.createElement('dialog');
  dialog.className='food-dialog';dialog.setAttribute('aria-labelledby','food-dialog-title');
  document.body.append(dialog);
  let expiryTimer;
  function render(state) {
    if(!dialog.isConnected)return;
    clearTimeout(expiryTimer);
    dialog.innerHTML='<button type="button" class="dialog-close icon-button" data-food="close" aria-label="关闭模拟外卖">×</button>'+foodOrderMarkup(state);
    if(state.quote&&!state.expired)expiryTimer=setTimeout(()=>flow.refresh(),Math.min(2147483647,Math.max(1,Date.parse(state.quote.expiresAt)-Date.now()+20)));
  }
  const unsubscribe=flow.subscribe(render);
  dialog.addEventListener('close',()=>{clearTimeout(expiryTimer);unsubscribe();dialog.remove();},{once:true});
  dialog.addEventListener('click',event=>{
    const button=event.target.closest('[data-food]');if(!button||button.disabled)return;
    if(button.dataset.food==='close')dialog.close();
    if(button.dataset.food==='status')flow.checkStatus();
    if(button.dataset.food==='reset'&&flow.reset())flow.search();
  });
  dialog.addEventListener('submit',event=>{
    event.preventDefault();const form=event.target,data=new FormData(form);
    if(form.matches('.food-search'))flow.search(String(data.get('keyword')||''));
    if(form.matches('.food-item'))flow.quote(form.dataset.foodItem,Number(data.get('quantity')));
    if(form.matches('.food-confirm'))flow.confirm(data.get('confirmed')==='on');
  });
  render(flow.getState());dialog.showModal();
  if(flow.getState().attempted)flow.checkStatus();
  else if(!flow.getState().quote&&flow.getState().phase==='menu'&&!flow.getState().items.length)flow.search();
}
