import {createServer} from 'node:http';
import {stat,readFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
import {getConfig} from './config.js';
import {createAgent} from './agent/index.js';
import {calendarFile} from './calendar.js';
import {ModelError} from './model/qwen.js';
import {clean} from './domain.js';
const root=fileURLToPath(new URL('../public/',import.meta.url));
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8',
 '.svg':'image/svg+xml','.png':'image/png','.webp':'image/webp','.ico':'image/x-icon'};
class RequestError extends Error {
 constructor(status,message){super(message);this.status=status;}
}
function json(res,status,data){res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(data));}
async function bodyOf(req){
 if(!/^application\/json(?:;|$)/i.test(req.headers['content-type']||'')) throw new RequestError(415,'请求需要 application/json。');
 const chunks=[];let size=0;
 for await(const chunk of req.iterator({destroyOnReturn:false})){
  size+=chunk.length;
  if(size>96000){
   // Keep the connection open long enough to send an HTTP error.
   req.resume();
   return null;
  }
  chunks.push(chunk);
 }
 try {
  const x=JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if(!x||typeof x!=='object'||Array.isArray(x)) throw Error();
  return x;
 } catch {throw new RequestError(400,'无效的 JSON 请求。');}
}
export function app(config=getConfig(),deps={}){
 const chat=createAgent(config,deps);
 let active=0;
 return createServer(async(req,res)=>{
  res.setHeader('x-content-type-options','nosniff');
  res.setHeader('referrer-policy','no-referrer');
  res.setHeader('content-security-policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
  try{
   const host=req.headers.host||'';
   if(!/^(localhost|127\.0\.0\.1)(:\d{1,5})?$/.test(host)) throw new RequestError(403,'仅允许本地访问。');
   if(req.headers.origin && req.headers.origin!=='http://'+host) throw new RequestError(403,'拒绝跨站请求。');
   const url=new URL(req.url,'http://'+host);
   const {pathname:p}=url;
   if(p==='/api/status' && req.method==='GET'){
    return json(res,200,{mode:config.key?'qwen':'demo',map:config.amap?'amap':'demo',rail:'manual',calendar:'ics'});
   }
   if(p==='/api/chat' && req.method==='POST'){
    if(active>=2) throw new RequestError(429,'当前请求较多，请稍后重试。');
    const b=await bodyOf(req);
    if(!b) throw new RequestError(413,'请求太大。');
    if(!clean(b.message)||b.message.length>1600) throw new RequestError(400,'请输入 1–1600 字的行程需求。');
    if(active>=2) throw new RequestError(429,'当前请求较多，请稍后重试。');
    active++;
    try {return json(res,200,await chat(b));}
    finally {active--;}
   }
   if(p==='/api/calendar' && req.method==='POST'){
    const b=await bodyOf(req);
    if(!b) throw new RequestError(413,'请求太大。');
    const ics=calendarFile(b);
    if(!ics) throw new RequestError(400,'需确认导出、填写有效出发日期并拥有合法行程。');
    res.writeHead(200,{'content-type':'text/calendar; charset=utf-8','content-disposition':'attachment; filename=xiaotu-trip.ics','cache-control':'no-store'});
    return res.end(ics);
   }
   if(!['GET','HEAD'].includes(req.method)||p.startsWith('/api/')) throw new RequestError(404,'接口不存在。');
   const pathname=p==='/'?'/index.html':p;
   if(!/^\/(?:[a-z0-9_-]+\/)*[a-z0-9_.-]+$/i.test(pathname)||pathname.split('/').some(x=>x.startsWith('.'))) throw new RequestError(404,'页面不存在。');
   const file=path.join(root,pathname), type=MIME[path.extname(file)];
   if(!type || !(await stat(file).catch(()=>null))?.isFile()) throw new RequestError(404,'页面不存在。');
   res.writeHead(200,{'content-type':type,'cache-control':'no-store'});
   res.end(req.method==='HEAD'?undefined:await readFile(file));
  }catch(e){
   if(res.headersSent){res.destroy();return;}
   if(e instanceof RequestError) return json(res,e.status,{error:e.message});
   if(e instanceof ModelError) return json(res,502,{error:e.message});
   return json(res,500,{error:'本地服务处理失败，请重试。'});
  }
 });
}
if(process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url) {
 const config=getConfig(), server=app(config);
 server.on('error',e=>{console.error(e.code==='EADDRINUSE'?'端口已占用，请编辑 .env 中的 PORT。':'本地服务无法启动。');process.exitCode=1;});
 server.listen(config.port,'127.0.0.1',()=>console.log('小途： http://127.0.0.1:'+config.port+' · '+(config.key?'Qwen 已配置，待首次验证':'无密钥演示')));
}
