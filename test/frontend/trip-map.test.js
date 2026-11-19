import test from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as tick} from 'node:timers/promises';
import {clearTripMap,mountTripMap} from '../../public/trip-map.js';

test('itinerary renderer requests local details, identifies absent fields and links to Tencent Maps without keys',async t=>{
 const oldDocument=globalThis.document, oldFetch=globalThis.fetch;
 t.after(()=>{clearTripMap();globalThis.document=oldDocument;globalThis.fetch=oldFetch;});
 globalThis.document={activeElement:null};
 const calls=[];
 globalThis.fetch=async(path,request)=>{
  calls.push([path,JSON.parse(request.body)]);
  switch(path){
   case '/api/maps/day':return Response.json({stops:[{index:0,status:'matched',poi:{id:'POI1',name:'公开景点',
    point:'120.1,30.2',city:'杭州'}}]});
   case '/api/maps/detail':return Response.json({id:'POI1',rating:4.7,
    photos:[{url:'https://p.qpic.cn/image/a',title:'<img onerror=x>'}]});
   case '/api/maps/snapshot':return Response.json({error:'静态地图权限不足。'},{status:502});
   default:assert.fail('unexpected network: '+path);
  }
 };
 const hooks=new Map(),host={isConnected:true,innerHTML:'',contains:()=>false,querySelectorAll:()=>[],
  querySelector(sel){if(!hooks.has(sel))hooks.set(sel,{});return hooks.get(sel);}};
 mountTripMap(host,{trip:{city:'杭州'},plan:{days:[{items:[{location:'公开景点',time:'09:00'}]}]},configured:true});
 await tick();
 assert.match(host.innerHTML,/地点坐标示意/);
 assert.match(host.innerHTML,/静态地图权限不足/);
 assert.match(host.innerHTML,/评分 4\.7\/5/);
 assert.match(host.innerHTML,/本次未返回评论正文/);
 assert.match(host.innerHTML,/loading="lazy"/);
 assert.ok(host.innerHTML.includes('&lt;img onerror=x&gt;'));
 assert.ok(!host.innerHTML.includes('<img onerror=x>'));
 const route=new URL(host.innerHTML.match(/href="(https:\/\/apis\.map\.qq\.com\/uri\/v1\/routeplan\?[^"]+)"/)[1].replaceAll('&amp;','&'));
 assert.equal(route.searchParams.get('tocoord'),'30.2,120.1');
 assert.equal(route.searchParams.get('fromcoord'),'CurrentLocation');
 assert.equal(route.searchParams.has('key'),false);
 assert.deepEqual(calls.map(x=>x[0]),['/api/maps/day','/api/maps/snapshot','/api/maps/detail']);
});

test('renders a local static map and refreshes only when the route geometry changes',async t=>{
 const oldDocument=globalThis.document,oldFetch=globalThis.fetch;
 t.after(()=>{clearTripMap();globalThis.document=oldDocument;globalThis.fetch=oldFetch;});
 globalThis.document={activeElement:null};
 const points=['120.1,30.2','120.2,30.3'],calls=[];
 globalThis.fetch=async(path,request)=>{
  const body=JSON.parse(request.body);calls.push({path,body});
  switch(path){
   case '/api/maps/day':return Response.json({stops:points.map((point,index)=>({index,status:'matched',
    poi:{id:'POI'+index,name:'景点'+index,point}}))});
   case '/api/maps/detail':return Response.json({id:'POI0'});
   case '/api/maps/route':return Response.json({distance:100,duration:120,polyline:[...points].reverse()});
   case '/api/maps/snapshot':return new Response(new Uint8Array([137,80,78,71]),{headers:{'content-type':'image/png'}});
   default:assert.fail('unexpected network call');
  }
 };
 const hooks=new Map(),host={isConnected:true,innerHTML:'',contains:()=>false,querySelectorAll:()=>[],
  querySelector(sel){if(!hooks.has(sel))hooks.set(sel,{});return hooks.get(sel);}};
 mountTripMap(host,{trip:{city:'杭州'},plan:{days:[{items:points.map((_,index)=>({location:'景点'+index,time:'09:00'}))}]},configured:true});
 await tick();
 assert.match(host.innerHTML,/data-map-static-image/);
 assert.match(host.innerHTML,/腾讯静态地图/);
 assert.doesNotMatch(host.innerHTML,/地点坐标示意/);
 const snapshots=()=>calls.filter(x=>x.path==='/api/maps/snapshot').map(x=>x.body);
 assert.deepEqual(snapshots(),[{markers:points,polyline:[]}]);
 hooks.get('[data-map-origin]').value='1';
 hooks.get('[data-map-origin]').onchange();
 assert.equal(snapshots().length,1);
 hooks.get('[data-map-route]').onclick();
 await tick();
 assert.deepEqual(snapshots()[1],{markers:points,polyline:[...points].reverse()});
 assert.equal(snapshots().length,2);
 assert.match(host.innerHTML,/当天已核实地点和当前路线/);
});

test('recognizes an old backend once and lets the user retry after restarting it',async t=>{
 const oldDocument=globalThis.document,oldFetch=globalThis.fetch;
 t.after(()=>{clearTripMap();globalThis.document=oldDocument;globalThis.fetch=oldFetch;});
 globalThis.document={activeElement:null};
 let requests=0,backendUpdated=false;
 globalThis.fetch=async(path,request)=>{
  if(path==='/api/maps/snapshot'){
   requests++;
   return backendUpdated?new Response(new Uint8Array([137,80,78,71]),{headers:{'content-type':'image/png'}})
    :Response.json({error:'地图接口不存在。'},{status:404});
  }
  if(path==='/api/maps/day'){
   const name=JSON.parse(request.body).locations[0];
   return Response.json({stops:[{index:0,status:'matched',poi:{id:name,name,point:name==='甲'?'120.1,30.2':'120.2,30.2'}}]});
  }
  if(path==='/api/maps/detail')return Response.json({});
  assert.fail('unexpected request');
 };
 const hooks=new Map(),days=[{dataset:{mapDay:'0'}},{dataset:{mapDay:'1'}}];
 const host={isConnected:true,innerHTML:'',contains:()=>false,querySelectorAll:sel=>sel==='[data-map-day]'?days:[],
  querySelector(sel){if(!hooks.has(sel))hooks.set(sel,{});return hooks.get(sel);}};
 mountTripMap(host,{trip:{city:'杭州'},plan:{days:['甲','乙'].map(location=>({items:[{location,time:'09:00'}]}))},configured:true});
 await tick();
 assert.match(host.innerHTML,/请重启服务并刷新页面/);
 assert.match(host.innerHTML,/地点坐标示意/);
 days[1].onclick();
 await tick();
 assert.equal(requests,1);
 backendUpdated=true;
 hooks.get('[data-map-static-retry]').onclick();
 await tick();
 assert.equal(requests,2);
 assert.match(host.innerHTML,/data-map-static-image/);
 assert.doesNotMatch(host.innerHTML,/请重启服务并刷新页面/);
});

test('plots verified itinerary POIs without a search and verifies only the selected pending place on demand',async t=>{
 const oldDocument=globalThis.document,oldFetch=globalThis.fetch;
 t.after(()=>{clearTripMap();globalThis.document=oldDocument;globalThis.fetch=oldFetch;});
 globalThis.document={activeElement:null};
 const known={id:'K1',name:'已核实例',point:'120.1,30.2',tripCity:'杭州'},pending={id:'P1',name:'指定景点',point:'120.2,30.2',tripCity:'杭州'};
 const verified=[],lookups=[];
 globalThis.fetch=async(path,r)=>{
  const body=JSON.parse(r.body);
  if(path==='/api/maps/snapshot')return new Response(new Uint8Array([1,2,3]),{headers:{'content-type':'image/png'}});
  if(path==='/api/maps/detail')return Response.json({});
  if(path==='/api/maps/day'){
   lookups.push(body);
   return Response.json({stops:[{index:0,status:'matched',poi:pending}]});
  }
  assert.fail('unexpected request');
 };
 const hooks=new Map(),buttons=[{dataset:{mapStop:'0'}},{dataset:{mapStop:'1'}}];
 const host={isConnected:true,innerHTML:'',contains:()=>false,querySelectorAll:sel=>sel==='[data-map-stop]'?buttons:[],
  querySelector(sel){if(!hooks.has(sel))hooks.set(sel,{});return hooks.get(sel);}};
 mountTripMap(host,{trip:{city:'杭州'},configured:true,onPlaceVerified:(...args)=>verified.push(args),plan:{days:[{items:[
  {location:known.name,time:'09:00',mapStatus:'verified',poi:known},
  {location:pending.name,time:'10:00',mapStatus:'pending'},
  {location:'餐馆（待选）',time:'12:00',mapStatus:'placeholder'}
 ]}]}});
 await tick();
 assert.deepEqual(lookups,[]);
 assert.match(host.innerHTML,/地图已核实/);
 buttons[1].onclick();
 hooks.get('[data-map-verify]').onclick();
 await tick();
 assert.deepEqual(lookups,[{city:'杭州',locations:['指定景点']}]);
 assert.deepEqual(verified,[[0,1,pending]]);
});

test('draws separate paths for one day without looking up across pending stops',async t=>{
 const oldDocument=globalThis.document,oldFetch=globalThis.fetch;
 t.after(()=>{clearTripMap();globalThis.document=oldDocument;globalThis.fetch=oldFetch;});
 globalThis.document={activeElement:null};
 const names=['甲','乙','待核地','丙','丁'],points=['120.1,30.2','120.2,30.2',null,'120.3,30.2','120.4,30.2'];
 const rows=names.map((name,i)=>({location:name,time:'09:0'+i,mapStatus:points[i]?'verified':'pending',
  poi:points[i]?{id:'P'+i,point:points[i],tripCity:'杭州',name}:null}));
 const legs=[],images=[];
 globalThis.fetch=async(path,opt)=>{
  const body=JSON.parse(opt.body);
  if(path==='/api/maps/route'){
   legs.push(body);return Response.json({polyline:[body.origin,body.destination],duration:60});
  }
  if(path==='/api/maps/detail')return Response.json({});
  if(path==='/api/maps/snapshot'){
   images.push(body);return new Response(new Uint8Array([1,2,3]),{headers:{'content-type':'image/png'}});
  }
  assert.fail('unexpected request');
 };
 const hooks=new Map(),host={isConnected:true,innerHTML:'',contains:()=>false,querySelectorAll:()=>[],
  querySelector(sel){if(!hooks.has(sel))hooks.set(sel,{});return hooks.get(sel);}};
 mountTripMap(host,{trip:{city:'杭州'},configured:true,plan:{days:[{items:rows}]}});
 await tick();
 hooks.get('[data-map-daily]').onclick();await tick();
 assert.deepEqual(legs.map(l=>[l.origin,l.destination,l.geometry]),[[points[0],points[1],true],[points[3],points[4],true]]);
 assert.deepEqual(images.at(-1).paths,[[points[0],points[1]],[points[3],points[4]]]);
 assert.match(host.innerHTML,/待核实地点，不跨站连线/);
 hooks.get('[data-map-daily]').onclick();await tick();
 assert.equal(legs.length,2);
});

test('shows retry controls after a positioning error and obtains a point on retry',async t=>{
 const oldDocument=globalThis.document,oldFetch=globalThis.fetch,oldNav=Object.getOwnPropertyDescriptor(globalThis,'navigator');
 t.after(()=>{clearTripMap();globalThis.document=oldDocument;globalThis.fetch=oldFetch;
  if(oldNav)Object.defineProperty(globalThis,'navigator',oldNav);else delete globalThis.navigator;});
 globalThis.document={activeElement:null};let requests=0;
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{geolocation:{getCurrentPosition(ok,fail){
  if(++requests===1)fail({code:1});else ok({coords:{longitude:120.1,latitude:30.2}});
 }}}});
 globalThis.fetch=async(path)=>{
  if(path==='/api/maps/locate')return Response.json({point:'120.101,30.201',address:'已核实地址'});
  if(path==='/api/maps/snapshot')return new Response(new Uint8Array([1,2,3]),{headers:{'content-type':'image/png'}});
  assert.fail('unexpected request');
 };
 const hooks=new Map(),host={isConnected:true,innerHTML:'',contains:()=>false,querySelectorAll:()=>[],
  querySelector(sel){if(!hooks.has(sel))hooks.set(sel,{});return hooks.get(sel);}};
 mountTripMap(host,{trip:{city:'杭州'},configured:true,plan:{days:[{items:[{location:'景点（待选）',time:'09:00',mapStatus:'placeholder'}]}]}});
 await tick();hooks.get('[data-map-locate]').onclick();await tick();
 assert.match(host.innerHTML,/定位未授权/);assert.match(host.innerHTML,/重新获取位置/);
 hooks.get('[data-map-locate-retry]').onclick();await tick();
 assert.equal(requests,2);assert.match(host.innerHTML,/已核实地址/);
 assert.doesNotMatch(host.innerHTML,/定位未授权/);
});
