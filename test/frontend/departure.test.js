import test from 'node:test';
import assert from 'node:assert/strict';
import {departureFields,departureMarkup} from '../../public/departure.js';
import {departureCity} from '../../public/location.js';

test('renders prices, seats, departure controls and a usable location retry',()=>{
 const trip={origin:'上海',city:'杭州'};
 const travel={
  origin:'上海',destination:'杭州',date:'2026-11-18',time:'15:02',dateDefault:true,
  request:{origin:'上海',destination:'杭州',date:'',time:'',mode:'direct'},
  rail:{action:'tickets',status:'available',items:[{
   code:'G42',from:'上海虹桥',to:'杭州东',depart:'16:54',arrive:'17:42',
   seats:[{name:'二等座',price:69,availability:'4'}],
  }]},
 };
 const html=departureMarkup(trip,travel,{error:'无定位权限',locationFailed:true,open:true});
 assert.match(html,/¥69/);assert.match(html,/余 4 张/);assert.match(html,/默认今天，仅用于查票/);
 assert.match(html,/重新获取出发城市/);assert.match(html,/只看一次换乘/);
 assert.equal(departureFields(trip,travel).date,'');
});

test('device location sends only a GPS field to the map endpoint and returns just the city',async t=>{
 const old=Object.getOwnPropertyDescriptor(globalThis,'navigator'),fetcher=globalThis.fetch;
 t.after(()=>{if(old)Object.defineProperty(globalThis,'navigator',old);else delete globalThis.navigator;globalThis.fetch=fetcher;});
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{geolocation:{getCurrentPosition(ok){ok({coords:{longitude:121.4,latitude:31.2}});}}}});
 globalThis.fetch=async(url,req)=>{
  assert.equal(url,'/api/maps/locate');assert.deepEqual(JSON.parse(req.body),{gps:'121.400000,31.200000'});
  return Response.json({city:'上海市',address:'不应向行程或模型返回的完整地址'});
 };
 assert.equal(await departureCity(),'上海市');
});
