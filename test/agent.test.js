import test from 'node:test';
import assert from 'node:assert/strict';
import {createAgent} from '../server/agent/index.js';
import {mapContext} from '../server/tools/amap.js';
import {calendarFile} from '../server/calendar.js';
import {app} from '../server/index.js';

const demo={key:'',amap:''};
const request={message:'从上海出发去杭州玩3天，两人，总预算1800元，2026-12-11出发'};
test('clarify, accumulate constraints, and compute reproducible CNY ranges',async()=>{
 const run=createAgent(demo);
 const a=await run({message:'去杭州'});
 assert.equal(a.plan,undefined);
 assert.equal(a.trip.city,'杭州');
 const b=await run({message:'玩3天，两人，总预算1800元',trip:a.trip});
 assert.equal(b.mode,'demo');
 assert.equal(b.plan.days.length,3);
 assert.equal(b.trip.budget,1800);
 assert.deepEqual(b.plan.budget.map(p=>p.low),[90,320,390,0]);
 assert.equal(b.sources[0].status,'demo');
});
test('a local edit preserves other days and does not overwrite trip duration',async()=>{
 const run=createAgent(demo), before=await run(request);
 const after=await run({message:'把第二天下午改成室内活动',trip:before.trip,priorPlan:before.plan});
 assert.equal(after.trip.days,3);
 assert.deepEqual(after.plan.days[0],before.plan.days[0]);
 assert.match(after.plan.days[1].items[2].title,/室内/);
 assert.deepEqual(after.plan.days[2],before.plan.days[2]);
});
test('explicit habitual preferences are suggestions only',async()=>{
 const r=await createAgent(demo)({message:'我常住上海，平时坐高铁，旅行喜欢美食，平时每人每天400元'});
 assert.deepEqual(r.profileSuggestion,{homeCity:'上海',dailyBudget:400,transport:'高铁',interests:['美食']});
 assert.equal(r.plan,undefined);
});
test('ICS requires authorization and a real date; escapes/folds import text',async()=>{
 const r=await createAgent(demo)(request);
 const b={plan:r.plan,startDate:r.trip.startDate};
 assert.equal(calendarFile(b),null);
 assert.equal(calendarFile({...b,confirmed:true,startDate:'2026-02-30'}),null);
 b.plan.days[0].items[0].title='路线,'+'漫'.repeat(80)+'\nEND:VEVENT';
 const s=calendarFile({...b,confirmed:true});
 assert.match(s,/DTSTART:20261211T010000Z/);
 assert.equal(s.match(/^BEGIN:VEVENT$/gm).length,9);
 assert.ok(s.split('\r\n').every(line=>Buffer.byteLength(line)<=75));
 assert.match(s,/SUMMARY:路线\\,/);
});
test('Qwen adapter sends only to configured provider and honors JSON plan validation',async()=>{
 const config={key:'FAKE_TEST_ONLY',amap:'',base:'https://example.invalid/compatible-mode/v1',model:'qwen-plus'};
 const calls=[];
 const fetcher=async (url,opts)=>{
  assert.equal(url,config.base+'/chat/completions');
  assert.equal(opts.headers.authorization,'Bearer FAKE_TEST_ONLY');
  const body=JSON.parse(opts.body); calls.push(body);
  assert.deepEqual(body.response_format,{type:'json_object'});
  const value=calls.length===1?{trip:{city:'杭州',days:1,people:2}}:
    {title:'测试行程',days:[{day:1,items:[{time:'09:30',title:'游览',location:'西湖'}]}]};
  return Response.json({choices:[{message:{content:JSON.stringify(value)}}]});
 };
 const r=await createAgent(config,{fetcher})({message:'给我一份行程'});
 assert.equal(r.mode,'qwen');
 assert.equal(r.plan.days[0].items[0].location,'西湖');
 assert.equal(calls.length,2);
 assert.ok(!JSON.stringify(r).includes(config.key));
 await assert.rejects(createAgent(config,{fetcher:async()=>new Response('SECRET',{status:401})})(request),
   e=>e.message.includes('401')&&!e.message.includes('SECRET')&&!e.message.includes(config.key));
});
test('AMap finalizes MCP session and accepts JSON and streamable frames',async()=>{
 const calls=[];
 const fetcher=async (url,opts)=>{
  assert.equal(url.hostname,'mcp.amap.com');
  if(opts.method==='DELETE'){ assert.equal(opts.headers['mcp-session-id'],'abc'); return new Response(null,{status:204}); }
  const b=JSON.parse(opts.body); calls.push(b.method);
  if(b.method==='notifications/initialized') return new Response(null,{status:202});
  const result=b.method==='initialize'?{protocolVersion:'2025-03-26'}
    : b.params.name==='maps_weather'?{structuredContent:{casts:[{date:'2026-12-11',dayweather:'晴'}]}}
    : b.params.name==='maps_direction_walking'?{structuredContent:{route:{paths:[{distance:1200}]}}}
    : {content:[{type:'text',text:JSON.stringify({pois:[{name:'候选点A',location:'120.1,30.1'},{name:'候选点B',location:'120.2,30.2',biz_ext:{rating:'4.5'}}]})}]};
  const frame={jsonrpc:'2.0',id:b.id,result};
  return b.method==='initialize'? Response.json(frame,{headers:{'mcp-session-id':'abc'}})
    : new Response('data: '+JSON.stringify(frame)+'\n\n',{headers:{'content-type':'text/event-stream'}});
 };
 const r=await mapContext('FAKE_AMAP_ONLY',{city:'杭州'},fetcher);
 assert.equal(r.source.status,'live');
 assert.equal(r.places.length,6);
 assert.equal(r.places[0].rating,undefined);
 assert.equal(r.places[1].rating,'4.5');
 assert.match(r.weather,/晴/);
 assert.match(r.route,/1.2/);
 assert.equal(calls.filter(x=>x==='tools/call').length,5);
});
test('local HTTP rejects cross-origin callers and keeps env outside web root',async t=>{
 const server=app({...demo,port:0});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>server.close(resolve)));
 const url='http://127.0.0.1:'+server.address().port;
 assert.equal((await (await fetch(url+'/api/status')).json()).mode,'demo');
 assert.equal((await fetch(url+'/.env')).status,404);
 assert.equal((await fetch(url+'/api/status',{headers:{origin:'https://example.invalid'}})).status,403);
 const opts={method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request)};
 const result=await fetch(url+'/api/chat',opts);
 assert.equal(result.status,200);
 assert.equal((await result.json()).trip.people,2);
 assert.equal((await fetch(url+'/api/chat',{...opts,body:JSON.stringify({message:'x'.repeat(97000)})})).status,413);
});
