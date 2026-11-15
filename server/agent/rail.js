export const RAIL_LINK = 'https://www.12306.cn/';
export function railNotice(trip) {
 return {
   name:'铁路交通',
   status:'unavailable',
   note:'未接入授权票务数据，请在铁路 12306 核实'+(trip.origin?' '+trip.origin+' 至 '+trip.city:'')+' 的车次、票价和余票。'
 };
}
