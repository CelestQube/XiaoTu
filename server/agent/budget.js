export function estimate(trip, profile = {}) {
 const {days,people} = trip, nights=Math.max(0,days-1), rooms=Math.ceil(people/2);
 const b=profile.dailyBudget; // CNY per person per day; only used as a preference, not a reservation.
 const meals=b ? Math.min(240,Math.max(35,Math.round(b*0.2))) : 65;
 return [
   {category:'市内交通',low:15*days*people,high:40*days*people,note:'公共交通为基准；不含城际票价'},
   {category:'住宿',low:160*nights*rooms,high:320*nights*rooms,note:nights+' 晚 × '+rooms+' 间，非实时房价'},
   {category:'餐饮',low:meals*days*people,high:(meals+55)*days*people,note:people+' 人，按每天用餐估算'},
   {category:'门票与活动',low:0,high:100*days*people,note:'免费项目至常规付费项目的估算'}
 ];
}
