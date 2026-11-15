export const clean = (x, max = 100) => typeof x === 'string' ? x.trim().slice(0,max) : '';
export const list = (x, max = 8) => Array.isArray(x) ? [...new Set(x.map(v=>clean(v,40)).filter(Boolean))].slice(0,max) : [];
export const int = (x,min,max) => Number.isSafeInteger(x) && x>=min && x<=max ? x : undefined;
export function dateString(x) {
  if(typeof x !== 'string' || !/^20\d\d-\d\d-\d\d$/.test(x)) return undefined;
  const dt = new Date(x + 'T00:00:00Z');
  return !isNaN(dt) && dt.toISOString().slice(0,10) === x ? x : undefined;
}
export function tripData(x, {partial = false} = {}) {
  if (!x || typeof x !== 'object') return {};
  const out = {};
  for (const [key,value] of [
    ['city',clean(x.city,40)],['origin',clean(x.origin,40)],
    ['days',int(x.days,1,7)],['people',int(x.people,1,20)],['budget',int(x.budget,1,10000000)],
    ['startDate',dateString(x.startDate)],['interests',list(x.interests)]
  ]) if (value !== undefined && (!(Array.isArray(value)) || value.length) && value !== '') out[key] = value;
  return partial ? out : {city:'',origin:'',...out};
}
export function profileData(x) {
  if (!x || typeof x !== 'object') return {};
  const p = {};
  const city=clean(x.homeCity,40), transport=clean(x.transport,40), daily=int(x.dailyBudget,1,100000), interests=list(x.interests);
  if(city) p.homeCity=city;
  if(transport) p.transport=transport;
  if(daily) p.dailyBudget=daily;
  if(interests.length) p.interests=interests;
  return p;
}
export function planData(x, count) {
  if (!x || !Array.isArray(x.days) || x.days.length !== count || count < 1 || count > 7) return null;
  const days = x.days.map((day,i)=>{
    if (day?.day !== i+1 || !Array.isArray(day.items) || !day.items.length || day.items.length > 8) return null;
    const items = day.items.map(v=>{
      if(typeof v?.time !== 'string' || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(v.time)) return null;
      const title=clean(v.title,90), location=clean(v.location,100), note=clean(v.note,180);
      return title && location ? {time:v.time,title,location,note} : null;
    });
    if(items.includes(null) || items.some((v,i)=>i && v.time<=items[i-1].time)) return null;
    return {day:i+1,items};
  });
  if(days.includes(null)) return null;
  return {title:clean(x.title,100) || '旅行计划',days,notes:list(x.notes,8)};
}
