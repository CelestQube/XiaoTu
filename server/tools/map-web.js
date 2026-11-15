import {clean} from '../domain.js';
const root='https://restapi.amap.com';
const fields='business,photos,navi';
const modePath={walk:'walking',car:'driving'};
const explanations={
 10001:'高德 Key 无效或已过期。',10003:'高德调用额度已用完。',
 10005:'当前请求出口 IP 不在高德 Key 的白名单内。',10007:'高德要求校验数字签名，请核对 Key 设置。',
 10009:'高德 Key 平台不匹配（10009）。请使用「Web 服务」类型的 Key，填写 AMAP_WEB_SERVICE_KEY 后重启。',
 10012:'当前高德 Key 未开通该接口权限（10012）。',10021:'高德限流，请稍后重试。'
};
export class MapError extends Error {
 constructor(status,message,code){super(message);this.status=status;this.code=code;}
}
const bad=msg=>new MapError(400,msg);
const noData=msg=>new MapError(404,msg);
const fail=()=>new MapError(502,'暂时无法连接高德，请检查网络后重试。');
function vendorError(code) {
 const id=/^\d{5}$/.test(String(code))?String(code):undefined;
 return new MapError(502,explanations[id] || (id?'高德暂未返回可用数据（'+id+'）。':'高德返回的数据暂不可用。'),id);
}
export function mapPoint(value) {
 if(typeof value!=='string'||!/^[-+]?\d{1,3}(?:\.\d{1,12})?,[-+]?\d{1,3}(?:\.\d{1,12})?$/.test(value)) return null;
 const [lon,lat]=value.split(',').map(Number);
 return Math.abs(lon)<=180&&Math.abs(lat)<=90?[lon,lat].map(x=>String(+x.toFixed(6))).join(','):null;
}
const field=(v,n)=>clean(typeof v==='number'&&Number.isFinite(v)?String(v):v,n);
function photo(p) {
 try {
  const u=new URL(clean(p?.url,1600));
  if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.port
     ||!/(?:^|\.)(?:amap\.com|autonavi\.com)$/.test(u.hostname)) return null;
  u.protocol='https:';u.hash='';
  return {url:u.href,title:field(p.title,90)};
 } catch {return null;}
}
const norm = s=>field(s,150).normalize('NFKC').toLowerCase().replace(/[\s·・-]+/g,'');
const validId = s => /^[a-z0-9]{5,30}$/i.test(s||'');
const safeName = s=>field(s,80).replaceAll('|',' ');
function poiData(raw) {
 const id=field(raw?.id,30), name=field(raw?.name,100), point=mapPoint(raw?.location);
 if(!validId(id)||!name||!point) return null;
 const b=raw.business||{};
 const number=v=>field(v,12);
 const rating=number(b.rating);
 return {id,name,point,entrance:mapPoint(raw.navi?.entr_location),address:field(raw.address,150),city:field(raw.cityname,50),
  type:field(raw.type,100),rating:rating && Number(rating)>0 && Number(rating)<=5 ? rating : '',
  cost:number(b.cost),hours:field(b.opentime_today,90),
  photos:Array.isArray(raw.photos)?raw.photos.slice(0,8).map(photo).filter(Boolean).slice(0,4):[],
  source:'高德地图',fetchedAt:new Date().toISOString()};
}
function unique(items) {return [...new Map(items.map(p=>[p.id,p])).values()];}
function placeholder(name){return !name||/待选|待定|待核实|附近$|周边$|^(酒店|餐厅|酒店入住|午餐|晚餐|早餐|市区|当地|景点)$/.test(name);}
function isMatch(row,name){
 const aliases=[row?.business?.alias,row?.alias].filter(x=>typeof x==='string').flatMap(x=>x.split(/[|;；]/));
 return [row?.name,...aliases].some(x=>norm(x)===norm(name));
}
async function readBytes(res,limit){
 if(!res.body) throw vendorError();
 const reader=res.body.getReader();let size=0;const chunks=[];
 try{
  while(true){
   const {value,done}=await reader.read();
   if(done) break;
   size+=value.byteLength;
   if(size>limit) throw vendorError();
   chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks,size);
 } finally {void reader.cancel().catch(()=>{});}
}
export function createMapWeb(key,fetcher=fetch) {
 const memo=new Map();
 async function request(path,params,image=false){
  if(!key) throw new MapError(503,'尚未配置高德「Web 服务」Key。');
  const url=new URL(path,root);
  url.search=new URLSearchParams({...params,key});
  let res,bytes;
  try {
   res=await fetcher(url,{redirect:'error',signal:AbortSignal.timeout(8000)});
   if(!res.ok) {void res.body?.cancel().catch(()=>{});throw vendorError();}
   bytes=await readBytes(res,image?3000000:1000000);
  } catch(e) {throw e instanceof MapError?e:fail();}
  const type=res.headers.get('content-type')?.split(';')[0].trim();
  if(image && ['image/png','image/jpeg','image/gif','image/webp'].includes(type)) return {bytes,type};
  let data;
  try{data=JSON.parse(bytes.toString('utf8'));}catch{throw vendorError();}
  if(String(data?.status)!=='1'||image) throw vendorError(data?.infocode);
  return data;
 }
 async function search(city,name,types){
  const raw=await request('/v5/place/text',{keywords:name,region:city,city_limit:'true',page_size:types?'5':'10',
   show_fields:fields,...(types?{types}:{})});
  return Array.isArray(raw.pois)?raw.pois:[];
 }
 async function find(city,name) {
  if(placeholder(name)) return {status:'unresolved'};
  const id=city+'|'+norm(name), prior=memo.get(id);
  if(prior && Date.now()-prior.time<5*60000) return prior.value;
  const running=(async()=>{
   const candidates=unique((await search(city,name)).filter(row=>isMatch(row,name)).map(poiData).filter(Boolean));
   return candidates.length===1?{status:'matched',poi:candidates[0]}
      :{status:candidates.length?'ambiguous':'unresolved'};
  })();
  if(memo.size>=100) memo.delete(memo.keys().next().value);
  memo.set(id,{time:Date.now(),value:running});
  try{return await running;}catch(e){memo.delete(id);throw e;}
 }
 return {
  async day(body) {
   const city=field(body.city,50);
   if(!city||!Array.isArray(body.locations)||!body.locations.length||body.locations.length>8||body.locations.some(x=>typeof x!=='string')) throw bad('需要目的地城市和当天 1–8 个地点。');
   const stops=[];
   // Small batches keep a single itinerary from overloading the live quota.
   for(let i=0;i<body.locations.length;i+=2) {
    const batch=await Promise.all(body.locations.slice(i,i+2).map((v,j)=>find(city,safeName(v)).then(r=>({index:i+j,...r}))));
    stops.push(...batch);
   }
   return {stops,source:'高德地图'};
  },
  async candidates(city) {
   const groups=[['景点','旅游景点','110000'],['酒店','酒店','100000'],['餐馆','餐厅','050000']];
   const results=await Promise.allSettled(groups.map(([,name,types])=>search(city,name,types)));
   if(!results.some(r=>r.status==='fulfilled')) throw results[0].reason;
   return results.flatMap((r,i)=>r.status==='fulfilled'?r.value.map(poiData).filter(Boolean).map(p=>({...p,type:groups[i][0]})):[]);
  },
  async locate(body) {
   const point=mapPoint(body.gps);
   if(!point) throw bad('缺少有效的 GPS 坐标。');
   const data=await request('/v3/assistant/coordinate/convert',{locations:point,coordsys:'gps'});
   const converted=mapPoint(data.locations);
   if(!converted) throw noData('无法将当前位置转换为高德坐标。');
   let address='';
   try {
    const geo=await request('/v3/geocode/regeo',{location:converted,extensions:'base'});
    address=field(geo.regeocode?.formatted_address,150);
   }catch{/* Route can still use converted coordinates. */}
   return {point:converted,address,source:'浏览器定位 · 高德坐标转换'};
  },
  async route(body) {
   const origin=mapPoint(body.origin),destination=mapPoint(body.destination),mode=body.mode;
   if(!origin||!destination||origin===destination||!Object.hasOwn(modePath,mode)) throw bad('需选择不同的起终点及步行或驾车。');
   const data=await request('/v5/direction/'+modePath[mode],{origin,destination,show_fields:'cost,polyline,navi',
    ...(validId(body.originId)?{origin_id:body.originId}:{}),
    ...(validId(body.destinationId)?{destination_id:body.destinationId}:{})});
   const path=data.route?.paths?.[0];
   if(!path||!Number.isFinite(Number(path.distance))) throw noData('高德没有返回此段的可用路线。');
   const steps=Array.isArray(path.steps)?path.steps:[];
   const points=steps.flatMap(s=>typeof s.polyline==='string'?s.polyline.split(';').map(mapPoint).filter(Boolean):[]);
   const polyline=thinPolyline(points.filter((p,i)=>!i||p!==points[i-1]));
   const seconds=Number(path.cost?.duration??path.duration);
   return {source:'高德地图',distance:Number(path.distance),duration:Number.isFinite(seconds)&&seconds>0?seconds:null,
    polyline,steps:steps.map(s=>field(s.instruction,170)).filter(Boolean).slice(0,40)};
  },
  async snapshot(body) {
   if(!Array.isArray(body.markers)||!body.markers.length||body.markers.length>9||body.markers.some(v=>!mapPoint(v))) throw bad('地图标记坐标无效。');
   const p=body.polyline??[];
   if(!Array.isArray(p)||p.length>70||p.some(v=>!mapPoint(v))) throw bad('地图路线坐标无效。');
   const markers=body.markers.map((v,i)=>'mid,0x21655D,'+(i+1)+':'+mapPoint(v)).join('|');
   return request('/v3/staticmap',{markers,size:'900*410',...(p.length>1?{paths:'5,0x197968,1,,:'+p.map(mapPoint).join(';')}: {})},true);
  }
 };
}
function thinPolyline(points) {
 // Preserve endpoints and select the highest-error turns, rather than taking uniform samples.
 if(points.length<=70) return points;
 const xyz=points.map(s=>s.split(',').map(Number)), kept=[0,points.length-1];
 while(kept.length<70) {
  let best=-1,gain=0;
  for(let j=1;j<kept.length;j++){
   const lo=kept[j-1],hi=kept[j],a=xyz[lo],b=xyz[hi], dx=b[0]-a[0],dy=b[1]-a[1],n=dx*dx+dy*dy;
   for(let i=lo+1;i<hi;i++){
    const [x,y]=xyz[i], ax=x-a[0],ay=y-a[1], t=n?Math.min(1,Math.max(0,(ax*dx+ay*dy)/n)):0;
    const err=(ax-t*dx)**2+(ay-t*dy)**2;
    if(err>gain){gain=err;best=i;}
   }
  }
  if(best<0) break;
  kept.push(best);kept.sort((a,b)=>a-b);
 }
 return kept.map(i=>points[i]);
}
