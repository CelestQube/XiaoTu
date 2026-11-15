import {clean} from '../domain.js';
const version='2025-03-26';
const names=new Set(['maps_text_search','maps_weather','maps_direction_walking']);
async function readFrame(r,id) {
 if(!(r.headers.get('content-type')||'').includes('text/event-stream')) return r.json();
 const reader=r.body.getReader(), decoder=new TextDecoder();
 let buf='', size=0;
 try {
  while(true) {
   const {done,value}=await reader.read(); size+=value?.length||0;
   if(size>500000) throw Error('MCP response too large');
   buf+=decoder.decode(value,{stream:!done});
   const frames=buf.split(/\r?\n\r?\n/); buf=frames.pop();
   if(done&&buf) frames.push(buf);
   for(const f of frames) {
    const s=f.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');
    let msg; try { msg=JSON.parse(s); } catch { continue; }
    if(msg?.id===id) return msg;
   }
   if(done) throw Error('No MCP response');
  }
 } finally { void reader.cancel().catch(()=>{}); }
}
export function amapClient(key,fetcher=fetch) {
 const url=new URL('https://mcp.amap.com/mcp');
 url.searchParams.set('key',key);
 let id=0,session,proto=version;
 const signal=AbortSignal.timeout(12500);
 async function send(method,params,notify=false) {
  const body={jsonrpc:'2.0',method,...(params?{params}:{}),...(notify?{}:{id:++id})};
  const headers={accept:'application/json, text/event-stream','content-type':'application/json'};
  if(method!=='initialize') headers['mcp-protocol-version']=proto;
  if(session) headers['mcp-session-id']=session;
  const r=await fetcher(url,{method:'POST',headers,body:JSON.stringify(body),redirect:'error',signal});
  if(!r.ok) throw Error('AMap unavailable');
  session=r.headers.get('mcp-session-id')||session;
  if(notify) { void r.body?.cancel().catch(()=>{}); return; }
  const msg=await readFrame(r,body.id);
  if(msg?.id!==body.id||msg.error||!('result' in msg)) throw Error('MCP returned an error');
  return msg.result;
 }
 return {
  async init(){
   const r=await send('initialize',{protocolVersion:version,capabilities:{},clientInfo:{name:'xiaotu-travel',version:'0.1.0'}});
   if(![version,'2025-06-18','2025-11-25'].includes(r.protocolVersion)) throw Error('Unsupported MCP version');
   proto=r.protocolVersion; await send('notifications/initialized',null,true);
  },
  async call(name,args){
   if(!names.has(name)) throw Error('Unknown AMap tool');
   const r=await send('tools/call',{name,arguments:args});
   if(r.isError) throw Error('AMap tool failed');
   if(r.structuredContent&&typeof r.structuredContent==='object') return r.structuredContent;
   for(const item of r.content||[]) if(item.type==='text') {
    try { return JSON.parse(item.text); } catch { /* Ignore unstructured payloads. */ }
   }
   return null;
  },
  async close() {
   if(!session) return;
   try {
    const r=await fetcher(url,{method:'DELETE',redirect:'error',signal:AbortSignal.timeout(1200),
      headers:{'mcp-session-id':session,'mcp-protocol-version':proto}});
    void r.body?.cancel().catch(()=>{});
   } catch { /* Best effort. */ }
  }
 };
}
const field=v=>clean(typeof v==='number'?String(v):v,100);
export async function mapContext(key,trip,fetcher=fetch) {
 const client=amapClient(key,fetcher);
 try {
  await client.init();
  const queries=[['景点',trip.interests?.includes('博物馆')?'博物馆':'旅游景点'],['酒店','酒店'],['餐馆','餐厅']];
  const settled=await Promise.allSettled([
   ...queries.map(([,keywords])=>client.call('maps_text_search',{keywords,city:trip.city,citylimit:true})),
   client.call('maps_weather',{city:trip.city})
  ]);
  const result=i=>settled[i].status==='fulfilled'?settled[i].value:null;
  const places=queries.flatMap(([type],i)=>{
   const raw=result(i), rows=raw?.pois||raw?.data?.pois;
   if(!Array.isArray(rows)) return [];
   return rows.slice(0,5).filter(p=>field(p?.name)).map(p=>{
    const rating=field(p.rating||p.biz_ext?.rating||p.business?.rating);
    const cost=field(p.cost||p.biz_ext?.cost||p.business?.cost);
    const hours=field(p.open_time||p.opening_hours||p.business?.opentime_today);
    return {type,name:field(p.name),address:field(p.address)||'地址待核实',source:'高德地图',coordinate:field(p.location),
     ...(rating?{rating}:{}),...(cost?{cost}:{}),...(hours?{hours}:{})};
   });
  });
  const w=result(3), casts=w?.forecasts?.[0]?.casts||w?.casts||w?.forecasts;
  const weather=Array.isArray(casts)?casts.slice(0,4).filter(x=>field(x?.date)&&field(x?.dayweather)).map(x=>field(x.date)+' '+field(x.dayweather)).join('；')||undefined:undefined;
  const pair=places.filter(p=>p.type==='景点'&&/^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$/.test(p.coordinate)).slice(0,2);
  let route;
  if(pair.length===2 && pair[0].coordinate!==pair[1].coordinate) try {
   const r=await client.call('maps_direction_walking',{origin:pair[0].coordinate,destination:pair[1].coordinate});
   const meters=Number((r?.route?.paths||r?.paths)?.[0]?.distance);
   if(meters>0&&Number.isFinite(meters)) route=pair[0].name+' → '+pair[1].name+'：高德步行约 '+(meters/1000).toFixed(1)+' 公里（候选点比较）';
  } catch { /* Optional. */ }
  const ok=!!(places.length||weather||route);
  return {places:places.map(({coordinate,...rest})=>rest),weather,route,source:{name:'高德地图 MCP',status:ok?'live':'unavailable',note:ok?'查询时间 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'short'}).format(new Date())+'（中国时间）。只展示已返回字段；实时营业、点评、缺失评分及价格待核实。':'没有可核实的地图返回数据。'}};
 } catch { return {places:[],source:{name:'高德地图 MCP',status:'unavailable',note:'地图查询失败；请核对 Web 服务 Key、城市与网络。'}}; }
 finally { await client.close(); }
}
