import test from 'node:test';
import assert from 'node:assert/strict';
import {createMapWeb,mapPoint} from '../server/tools/map-web.js';
import {app} from '../server/index.js';
import {getConfig} from '../server/config.js';

const example=(name,id,patch={})=>({id,name,location:'120.15,30.25',address:'样例地址',cityname:'杭州市',
 business:{rating:'4.8',alias:'别名'},photos:[
  {title:'实景',url:'http://store.is.autonavi.com/example.png'},
  {url:'https://fake.example/no.png'},{url:'https://autonavi.com@fake.example/h.png'}
 ],navi:{entr_location:'120.151,30.251'},...patch});
const response=data=>Response.json({status:'1',...data});
test('Web Service key is separate and has an MCP key fallback',()=>{
 assert.equal(getConfig({AMAP_MAPS_API_KEY:'mcp',AMAP_WEB_SERVICE_KEY:'web'}).amapWeb,'web');
 assert.equal(getConfig({AMAP_MAPS_API_KEY:'mcp'}).amapWeb,'mcp');
 assert.equal(getConfig({}).amapWeb,'');
});
test('resolve day POIs by name or provider alias; never copy suggestions from nearby mismatches',async()=>{
 let requests=0;
 const api=createMapWeb('test-only',async url=>{
  requests++;
  assert.equal(url.origin,'https://restapi.amap.com');
  assert.equal(url.searchParams.get('city_limit'),'true');
  assert.equal(url.searchParams.get('show_fields'),'business,photos,navi');
  assert.equal(url.searchParams.get('region'),'杭州');
  const k=url.searchParams.get('keywords');
  return response({pois:k==='同名'?[
   example(k,'TEST1111'),example(k,'TEST2222')
  ]:[example('雷峰塔停车场','TEST3333',{business:{}}),example('雷峰塔','TEST4444')]});
 });
 const data=await api.day({city:'杭州',locations:['雷峰塔','雷峰塔','待选','别名','无此景点','同名']});
 assert.deepEqual(data.stops.map(x=>x.status),['matched','matched','unresolved','matched','unresolved','ambiguous']);
 const p=data.stops[0].poi;
 assert.equal(p.rating,'4.8');
 assert.equal(p.photos.length,1);
 assert.equal(p.photos[0].url,'https://store.is.autonavi.com/example.png');
 assert.equal(p.entrance,'120.151,30.251');
 assert.equal(requests,4);
 assert.ok(!JSON.stringify(data).includes('test-only'));
});
test('route, GPS conversion and static overlay use official HTTPS endpoints',async()=>{
 const seen=[];
 const api=createMapWeb('test-only',async (url,options)=>{
  assert.equal(options.redirect,'error');
  seen.push(url.pathname);
  if(url.pathname.includes('direction')){
   assert.equal(url.searchParams.get('origin'),'120.15,30.25');
   assert.equal(url.searchParams.get('destination_id'),'TEST4444');
   assert.equal(url.searchParams.get('show_fields'),'cost,polyline,navi');
   const points=Array.from({length:120},(_,i)=>(120+i/10000)+','+(30+(i%11)/10000));
   return response({route:{paths:[{distance:'823',cost:{duration:'790'},steps:[{instruction:'向东步行',polyline:points.join(';')}]}]}});
  }
  if(url.pathname.includes('convert')){
   assert.equal(url.searchParams.get('coordsys'),'gps');
   return response({locations:'120.15,30.25'});
  }
  if(url.pathname.includes('regeo'))return response({regeocode:{formatted_address:'测试市测试街'}});
  assert.equal(url.pathname,'/v3/staticmap');
  assert.match(url.searchParams.get('markers'),/mid,0x21655D,1:120\.15,30\.25/);
  assert.match(url.searchParams.get('paths'),/^5,0x197968,1,,:120,30;/);
  return new Response(Buffer.from([137,80,78,71]),{headers:{'content-type':'image/png'}});
 });
 const loc=await api.locate({gps:'120.000000,30.000000'});
 assert.equal(loc.address,'测试市测试街');
 const route=await api.route({origin:loc.point,destination:'120.151,30.251',destinationId:'TEST4444',mode:'walk'});
 assert.equal(route.distance,823);assert.equal(route.duration,790);
 assert.equal(route.polyline.at(-1),'120.0119,30.0009');
 assert.ok(route.polyline.length<=70);
 const png=await api.snapshot({markers:[loc.point],polyline:route.polyline});
 assert.equal(png.type,'image/png');
 assert.deepEqual(seen,['/v3/assistant/coordinate/convert','/v3/geocode/regeo','/v5/direction/walking','/v3/staticmap']);
 await assert.rejects(api.route({origin:'120,30',destination:'120.15,30',mode:'boat'}),{status:400});
 assert.equal(mapPoint('181,0'),null);
});
test('HTTP maps fail with a safe provider code and reject cross-site geolocation',async t=>{
 const server=app({key:'',amap:'',amapWeb:'FAKE_SECRET'}, {fetcher:async()=>Response.json({status:'0',infocode:'10009',info:'FAKE_SECRET'})});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 t.after(()=>new Promise(r=>server.close(r)));
 const base='http://127.0.0.1:'+server.address().port;
 const post=(name,body,headers={})=>fetch(base+'/api/maps/'+name,{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
 assert.equal((await (await fetch(base+'/api/status')).json()).mapWeb,true);
 const r=await post('day',{city:'杭州',locations:['雷峰塔']});
 assert.equal(r.status,502);
 const data=await r.json();assert.equal(data.code,'10009');assert.match(data.error,/Web 服务/);
 assert.doesNotMatch(JSON.stringify(data),/FAKE_SECRET/);
 assert.equal((await post('locate',{gps:'120,30'},{origin:'https://example.org'})).status,403);
});
