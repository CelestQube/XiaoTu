import {tripData, profileData} from '../domain.js';
const fixtures = {
 杭州:{scenic:['西湖','灵隐飞来峰','西溪湿地','京杭大运河杭州景区','良渚博物院','中国茶叶博物馆','浙江省博物馆之江馆'],hotel:['湖滨','武林广场'],food:['河坊街','大兜路']},
 成都:{scenic:['武侯祠','成都博物馆','杜甫草堂','成都大熊猫繁育研究基地','金沙遗址博物馆','望江楼公园','宽窄巷子'],hotel:['春熙路','天府广场'],food:['玉林路','奎星楼街']},
 厦门:{scenic:['鼓浪屿','厦门园林植物园','环岛路','厦门博物馆','厦门海湾公园','集美学村','沙坡尾'],hotel:['中山路','厦门站'],food:['八市','中山路']},
 北京:{scenic:['故宫博物院','天坛公园','颐和园','中国国家博物馆','景山公园','北京动物园','首都博物馆'],hotel:['东直门','前门'],food:['护国寺街','前门大街']},
};
const known = ['哈尔滨','张家界','重庆','上海','广州','深圳','南京','苏州','三亚','昆明','丽江','大理','青岛','西安','长沙','武汉','洛阳','济南','天津','宁波','福州','珠海','黄山','桂林',...Object.keys(fixtures)];
const num = v=>Number(v) || ({一:1,两:2,二:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9,十:10,半:0.5})[v];
export function preferences(text) {
 const p={};
 const home=text.match(/(?:常住|长期住在|长期居住在|常居)([\u4e00-\u9fa5]{2,8}?)(?:市|[，。,\s]|$)/);
 if(home) p.homeCity=home[1];
 const tr=text.match(/(?:平时|通常|出游一般|习惯)[^，。,\n]{0,8}?(高铁|火车|飞机|自驾|公共交通|地铁|骑行)/);
 if(tr) p.transport=tr[1];
 const daily=text.match(/(?:平时|通常|一般)[^，。,\n]{0,18}?(?:每人每天|每天|日均|日均预算)\s*(\d{2,6})\s*(?:元|块)/);
 if(daily) p.dailyBudget=Number(daily[1]);
 if(/(?:旅游|旅行|出游|平时).{0,8}(?:喜欢|偏好)|我(?:很)?喜欢/.test(text)) p.interests = interests(text);
 return profileData(p);
}
function interests(text) { return ['美食','风景','观光','摄影','亲子','骑行','登山','博物馆','海边','徒步','人文','自然'].filter(k=>text.includes(k)); }
export function readRequest(text, previous, profile) {
 const next = {...previous};
 const origin=known.find(x=>new RegExp('从'+x+'(?:市|出发|[到去，,])').test(text))
   || text.match(/从([\u4e00-\u9fa5]{2,8}?)出发/)?.[1];
 if(origin) next.origin=origin;
 else if(!next.origin && profile.homeCity) next.origin=profile.homeCity;
 const city=known.find(x=>new RegExp('(?:去|到|玩|换成|改成|改为|在|安排|规划|推荐|查一下)\\s*'+x).test(text))
   || known.find(x=>x!==origin && (text.trim()===x || (!/(?:常住|常居|长期住)/.test(text)&&text.includes(x))))
   || text.match(/(?:想去|改去|准备去|出发去|前往|去)([\u4e00-\u9fa5]{2,8}?)(?:市|玩|旅游|旅行|出游|[，。,\s]|$)/)?.[1]
   || (/^[\u4e00-\u9fa5]{2,8}市$/.test(text)?text.slice(0,-1):'');
 if(city && !city.endsWith('出发')) next.city=city;
 const days=text.match(/(?<![第\d])([1-9]\d?|[一两二三四五六七八九十])\s*(?:天|日游)/);
 const people=text.match(/([1-9]\d?|[一两二三四五六七八九十])\s*(?:个人|人|位)/);
 if(days) next.days=num(days[1]);
 if(people) next.people=num(people[1]);
 const money=text.match(/(?:总预算|预算|控制在|人均)\s*[为是约：:¥￥ ]*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(万|千|元|块)?/);
 if(money) {
   const amount=+money[1].replaceAll(',','') * ({万:10000,千:1000}[money[2]]||1);
   next.budget=Math.round(money[0].startsWith('人均') ? amount*(next.people||1) : amount);
 }
 const date=text.match(/20\d\d[-\/]\d{1,2}[-\/]\d{1,2}|20\d\d年\d{1,2}月\d{1,2}日/);
 if(date) next.startDate=date[0].match(/\d+/g).map(x=>x.padStart(2,'0')).join('-');
 const i=interests(text); if(i.length) next.interests=i;
 return tripData(next,{partial:true});
}
export function demoPlaces(city) {
 const f=fixtures[city];
 if(!f) return [];
 return Object.entries(f).flatMap(([key,values])=>values.map(name=>({
  name,type:{scenic:'景点',hotel:'住宿商圈',food:'餐饮商圈'}[key],address:city+' · 示意位置',source:'本地演示'
 })));
}
export function demoPlan(trip, places, message, profile) {
 const p=type=>places.filter(x=>x.type===type);
 let scenic=p('景点');
 const hotels=[...p('酒店'),...p('住宿商圈')], food=[...p('餐馆'),...p('餐饮商圈')];
 const indoor=/室内|下雨|雨天/.test(message);
 const dayIndex=num(message.match(/第([1-7一两二三四五六七])天/)?.[1]);
 const ix = Array.from({length:trip.days},(_,n)=>n);
 if(!scenic.length) scenic=[{name:trip.city+'当地景点（待选）'}];
 return {title:trip.city+' '+trip.days+'日旅行草案',days:ix.map(i=>{
   const venue=scenic[i%scenic.length].name;
   const rain=indoor && (!dayIndex || dayIndex===i+1);
   const hobby=(trip.interests || profile.interests || []).join('、');
   return {day:i+1,items:[
    {time:'09:00',title:i===0?'抵达与熟悉周边':venue,location:i===0?(hotels[0]?.name || trip.city+'市区住宿区（待选）'):venue,note:i===0?'抵达时间根据实际交通票据调整。':'查验预约、开放和交通信息。'},
    {time:'12:00',title:'午餐',location:food[i%Math.max(food.length,1)]?.name || '当天活动附近（待选）',note:'按实际菜单与营业时间选店。'},
    {time:'14:00',title:rain?'室内备选：城市展馆':(i===0?venue:'周边慢游'),location:rain?(scenic.find(s=>s.name.includes('博物馆'))?.name || trip.city+'室内场馆（待选）'):(i===0?venue:venue+'周边'),note:rain?'出发前复核开放、预约和天气。':hobby?'优先安排 '+hobby+' 相关活动。':'保留机动时间，不排紧。'}
   ]};
 }),notes:['日期和费用均需出发前复核。','当前为示范规划，具体路线和营业情况以服务结果为准。']};
}
