import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createFoodOrderController,restoreFoodOrder,foodOrderEntry,foodOrderMarkup,FOOD_STORAGE_KEY} from '../../public/food-order.js';

const now=()=>Date.parse('2026-11-18T08:00:00Z');
const item={id:'rice-1',name:'模拟香菇饭',merchant:'小途模拟厨房',price:26.5};
const quote={quoteId:'quote-1',itemId:item.id,name:item.name,merchant:item.merchant,quantity:2,total:53,expiresAt:'2026-11-18T08:10:00Z',status:'awaiting_confirmation'};
const receipt=(status='succeeded')=>({quoteId:'quote-1',operationId:'operation-1',status,...(status==='succeeded'?{orderId:'mock-order-1'}:{})});
function memory(initial) {
  const values=new Map(initial?[[FOOD_STORAGE_KEY,JSON.stringify(initial)]]:[]);
  return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key),values};
}
function harness({handler,storage=memory(),timeoutMs=5000}={}) {
  const calls=[];
  const flow=createFoodOrderController({storage,now,timeoutMs,fetchImpl:async(path,options)=>{
    const body=JSON.parse(options.body);calls.push({path,body,credentials:options.credentials});
    assert.equal(options.credentials,'same-origin');
    if(handler){const result=await handler(path,body,options,calls);if(result!==undefined)return result;}
    if(path.endsWith('/menu'))return Response.json({items:[item]});
    if(path.endsWith('/quote'))return Response.json(quote);
    return Response.json(receipt());
  }});
  return {flow,calls,storage};
}
async function prepared(options) {const h=harness(options);await h.flow.search();await h.flow.quote(item.id,2);return h;}

test('food demo entry requires the exact enabled capability; no implicit UI side effects',()=>{
  for(const status of [null,{}, {foodDemo:false},{foodDemo:'true'},{foodDemo:1}])assert.equal(foodOrderEntry(status),'');
  assert.match(foodOrderEntry({foodDemo:true}),/data-action="food-demo"/);
  assert.match(foodOrderEntry({foodDemo:true}),/不付款/);
});

test('menu and quote use only expected request fields; quotation is not confirmation',async()=>{
  const {flow,calls}=harness();
  await flow.search('米饭');await flow.quote(item.id,2);
  assert.deepEqual(calls.map(x=>[x.path,x.body]),[['/api/food/menu',{keyword:'米饭'}],['/api/food/quote',{itemId:item.id,quantity:2}]]);
  assert.equal(flow.getState().phase,'awaiting_confirmation');assert.equal(flow.getState().canConfirm,true);
  assert.equal(flow.getState().attempted,false);
});

test('double clicking quote creates exactly one quote request',async()=>{
  let finish;
  const h=harness({handler:async path=>{if(path.endsWith('/quote'))return new Promise(resolve=>{finish=()=>resolve(Response.json(quote));});}});
  await h.flow.search();
  const first=h.flow.quote(item.id,2),second=h.flow.quote(item.id,2);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(first,second);assert.equal(h.calls.filter(x=>x.path.endsWith('/quote')).length,1);
  finish();await first;
  await h.flow.quote(item.id,2);
  assert.equal(h.calls.filter(x=>x.path.endsWith('/quote')).length,1);
});

test('explicit consent is required, double confirmation is single-flight, no subsequent automatic submit',async()=>{
  let finish;
  const h=await prepared({handler:async(path,body,_options,_calls)=>{
    if(path.endsWith('/confirm')){
      assert.deepEqual(body,{quoteId:quote.quoteId,confirmed:true});
      return new Promise(resolve=>{finish=()=>resolve(Response.json(receipt()));});
    }
  }});
  await h.flow.confirm(false);await h.flow.confirm('true');
  assert.equal(h.calls.length,2);
  const a=h.flow.confirm(true),b=h.flow.confirm(true);
  assert.equal(a,b);
  const saved=JSON.parse(h.storage.getItem(FOOD_STORAGE_KEY));
  assert.equal(saved.attempted,true);assert.equal(saved.quote.quoteId,quote.quoteId);
  await new Promise(resolve=>setImmediate(resolve));finish();await a;
  assert.equal(h.flow.getState().phase,'succeeded');assert.equal(h.flow.getState().canReset,true);
  await h.flow.confirm(true);
  assert.equal(h.calls.filter(x=>x.path.endsWith('/confirm')).length,1);
});

test('confirmation timeout retains quoteId and only status checks can resolve uncertainty',async()=>{
  const h=await prepared({timeoutMs:10,handler:async(path,_body,options)=>{
    if(path.endsWith('/confirm'))return new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError')),{once:true}));
  }});
  await h.flow.confirm(true);
  assert.equal(h.flow.getState().phase,'unknown');assert.equal(h.flow.getState().canReset,false);
  assert.equal(h.flow.reset(),false);
  await h.flow.quote(item.id,2);await h.flow.confirm(true);
  await h.flow.checkStatus();
  assert.equal(h.flow.getState().phase,'succeeded');
  assert.deepEqual(h.calls.map(x=>x.path),['/api/food/menu','/api/food/quote','/api/food/confirm','/api/food/status']);
  assert.deepEqual(h.calls.at(-1).body,{quoteId:'quote-1'});
});

test('refresh after a possibly dispatched confirmation restores uncertainty and never auto-confirms',async()=>{
  const h=await prepared({handler:async path=>{if(path.endsWith('/confirm'))throw new TypeError('Network disconnected');}});
  await h.flow.confirm(true);
  const restored=harness({storage:h.storage});
  assert.equal(restored.calls.length,0);assert.equal(restored.flow.getState().phase,'unknown');
  assert.equal(restored.flow.getState().quote.quoteId,'quote-1');
  await restored.flow.checkStatus();
  assert.deepEqual(restored.calls.map(x=>x.path),['/api/food/status']);
  assert.equal(restored.flow.getState().phase,'succeeded');
});

test('pending status remains attached to its quote and a later status can succeed',async()=>{
  let checks=0;
  const h=await prepared({handler:async path=>{
    if(path.endsWith('/confirm'))return Response.json(receipt('pending'));
    if(path.endsWith('/status'))return Response.json(receipt(++checks>1?'succeeded':'pending'));
  }});
  await h.flow.confirm(true);assert.equal(h.flow.getState().phase,'pending');
  assert.equal(h.flow.reset(),false);await h.flow.checkStatus();assert.equal(h.flow.getState().phase,'pending');
  await h.flow.checkStatus();assert.equal(h.flow.getState().receipt.orderId,'mock-order-1');
  assert.equal(h.calls.filter(x=>x.path.endsWith('/quote')).length,1);
  assert.equal(h.calls.filter(x=>x.path.endsWith('/confirm')).length,1);
});

test('server can prove confirmation was not received; retry requires another explicit action with the same quote',async()=>{
  let confirms=0;
  const h=await prepared({handler:async path=>{
    if(path.endsWith('/confirm')&&++confirms===1)throw new Error('connection lost');
    if(path.endsWith('/status'))return Response.json({quoteId:'quote-1',status:'awaiting_confirmation'});
  }});
  await h.flow.confirm(true);await h.flow.checkStatus();
  assert.equal(h.flow.getState().canConfirm,true);assert.equal(confirms,1);
  await h.flow.confirm(true);assert.equal(confirms,2);
  assert.deepEqual(h.calls.filter(x=>x.path.endsWith('/confirm')).map(x=>x.body),[{quoteId:'quote-1',confirmed:true},{quoteId:'quote-1',confirmed:true}]);
  assert.equal(h.calls.filter(x=>x.path.endsWith('/quote')).length,1);
});

test('expired quote cannot be confirmed and failed result permits explicitly starting another mock',async()=>{
  const storage=memory({version:1,quote:{...quote,expiresAt:'2026-11-18T07:59:00Z'},attempted:false});
  const h=harness({storage});await h.flow.confirm(true);
  assert.equal(h.flow.getState().expired,true);assert.equal(h.calls.length,0);
  assert.match(foodOrderMarkup(h.flow.getState()),/报价已到期/);
  const failed=await prepared({handler:async path=>path.endsWith('/confirm')?Response.json(receipt('failed')):undefined});
  await failed.flow.confirm(true);assert.equal(failed.flow.getState().phase,'failed');
  assert.equal(failed.flow.reset(),true);assert.equal(failed.flow.getState().quote,null);
});

test('a mismatched result cannot mark another quote as succeeded',async()=>{
  const h=await prepared({handler:async path=>path.endsWith('/confirm')?Response.json({...receipt(),quoteId:'someone-else'}):undefined});
  await h.flow.confirm(true);assert.equal(h.flow.getState().phase,'unknown');assert.equal(h.flow.getState().receipt,null);
  assert.match(h.flow.getState().error,/回执格式不匹配/);
});

test('storage failure blocks confirmation before the request is dispatched',async()=>{
  const storage=memory();const h=await prepared({storage});
  storage.setItem=()=>{throw new Error('quota exceeded');};
  await h.flow.confirm(true);
  assert.equal(h.calls.length,2);assert.equal(h.flow.getState().attempted,false);
  assert.match(h.flow.getState().error,/已阻止确认/);
});

test('quotation timeout does not automatically request another quote, even after refresh',async()=>{
  const h=harness({handler:async path=>{if(path.endsWith('/quote'))throw new Error('network timeout');}});
  await h.flow.search();await h.flow.quote(item.id,2);await h.flow.quote(item.id,2);
  assert.equal(h.flow.getState().phase,'quote_unknown');assert.equal(h.calls.length,2);
  const restored=harness({storage:h.storage});
  await restored.flow.search();await restored.flow.quote(item.id,2);await restored.flow.confirm(true);
  assert.equal(restored.calls.length,0);assert.equal(restored.flow.getState().phase,'quote_unknown');
});

test('whitelist persistence excludes personal and arbitrary response fields',async()=>{
  const h=await prepared({handler:async path=>path.endsWith('/quote')?Response.json({...quote,address:'not stored',phone:'not stored',token:'not stored'}):undefined});
  const value=h.storage.getItem(FOOD_STORAGE_KEY);
  assert.doesNotMatch(value,/address|phone|token|keyword/);
  assert.deepEqual(Object.keys(JSON.parse(value).quote).sort(),['quoteId','itemId','name','merchant','quantity','total','expiresAt'].sort());
  assert.equal(restoreFoodOrder('not json'),null);
  assert.equal(restoreFoodOrder({version:1,quote:{...quote,total:-100}}),null);
  assert.equal(restoreFoodOrder({version:1,quote:{...quote,quoteId:'<script>'}}),null);
});

test('model/API labels and errors are escaped and confirmation wording is explicit',async()=>{
  const h=await prepared();const state=h.flow.getState();
  state.quote.name='<img src=x onerror=alert(1)>';state.quote.merchant='<script>bad</script>';state.error='<svg onload=bad>';
  const html=foodOrderMarkup(state);
  assert.doesNotMatch(html,/<img|<script|<svg/);assert.match(html,/&lt;img/);
  assert.match(html,/确认模拟下单（仅测试，不付款）/);assert.match(html,/type="checkbox" name="confirmed" required/);
  assert.match(html,/查询原报价状态/);
});

test('invalid quantity, unknown item and malformed quote never dispatch confirmation',async()=>{
  const h=harness({handler:async path=>path.endsWith('/quote')?Response.json({...quote,itemId:'wrong'}):undefined});
  await h.flow.search();await h.flow.quote(item.id,0);await h.flow.quote(item.id,11);await h.flow.quote('missing',1);
  assert.equal(h.calls.length,1);await h.flow.quote(item.id,2);await h.flow.confirm(true);
  assert.equal(h.flow.getState().quote,null);assert.equal(h.calls.length,2);
});

test('small integration keeps existing page/module entry points and removes mock data on explicit clear',async()=>{
  const app=await readFile(new URL('../../public/app.js',import.meta.url),'utf8');
  const html=await readFile(new URL('../../public/index.html',import.meta.url),'utf8');
  assert.match(app,/foodOrderEntry\(state.statusError \? null : state.status\)/);
  assert.match(app,/case 'food-demo': if\(!state.statusError\)openFoodOrder\(state.status\)/);
  assert.match(app,/clearFoodOrderStorage\(\);location.reload/);
  for(const old of ['mountTripMap','openRailDialog','bindDeparture'])assert.ok(app.includes(old));
  assert.match(html,/href="\/food-order.css"/);assert.match(html,/href="\/trip-map.css"/);
});
