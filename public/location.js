export function browserPosition(){
 return new Promise((resolve,reject)=>{
  if(!globalThis.navigator?.geolocation){reject(new Error('此浏览器不支持定位，可以手动填写出发地。'));return;}
  navigator.geolocation.getCurrentPosition(pos=>resolve(pos.coords.longitude.toFixed(6)+','+pos.coords.latitude.toFixed(6)),
   e=>reject(new Error(e.code===1?'定位未授权，请在浏览器站点设置允许定位后重试，或手动输入。':
    e.code===3?'定位超时，请重试或手动输入。':'无法确定当前位置，请检查设备定位服务后重试。')),
   {enableHighAccuracy:false,timeout:10000,maximumAge:0});
 });
}
export async function departureCity(signal){
 const timeout=AbortSignal.timeout(14000);
 const control=signal?AbortSignal.any([signal,timeout]):timeout;
 async function getPosition(){
  control.throwIfAborted();
  const cancel=()=>reject(control.reason);
  let reject;
  try{return await Promise.race([browserPosition(),new Promise((_,fail)=>{
   reject=fail;control.addEventListener('abort',cancel,{once:true});
  })]);}finally{control.removeEventListener('abort',cancel);}
 }
 const gps=await getPosition();
 control.throwIfAborted();
 const clock=AbortSignal.timeout(12000);
 const r=await fetch('/api/maps/locate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({gps}),
  signal:signal?AbortSignal.any([signal,clock]):clock});
 const data=await r.json();
 if(!r.ok||!data.city)throw new Error(data.error||'地图未取得所在城市。');
 return data.city;
}
