import {dateString,planData} from './domain.js';
import {randomUUID} from 'node:crypto';
const escape = s => s.replaceAll('\\','\\\\').replace(/\r\n|\r|\n/g,'\\n').replaceAll(',','\\,').replaceAll(';','\\;');
const stamp = d => d.toISOString().replace(/[-:]/g,'').replace(/\.\d{3}Z$/,'Z');
function fold(line) {
 let size=0,out='';
 for(const c of line){
  const bytes=Buffer.byteLength(c);
  if(size+bytes>75){out+='\r\n ';size=1;}
  out+=c;size+=bytes;
 }
 return out;
}
export function calendarFile(body) {
 const date=dateString(body.startDate);
 if(body.confirmed!==true||!date) return null;
 const plan=planData(body.plan,body.plan?.days?.length);
 if(!plan) return null;
 const now=stamp(new Date());
 const rows=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Xiaotu//Travel Planner//ZH-CN','CALSCALE:GREGORIAN'];
 for(const {day,items} of plan.days){
  // Mainland China MVP: timestamps encoded in UTC from Asia/Shanghai (+08:00).
  const base=Date.parse(date+'T00:00:00+08:00')+(day-1)*86400000;
  for(const item of items) {
   const [h,m]=item.time.split(':').map(Number);
   const time=base+(h*60+m)*60000;
   rows.push('BEGIN:VEVENT','UID:'+randomUUID()+'@xiaotu.local','DTSTAMP:'+now,
    'DTSTART:'+stamp(new Date(time)),
    'SUMMARY:'+escape(item.title),'LOCATION:'+escape(item.location),'DESCRIPTION:'+escape(item.note),'END:VEVENT');
  }
 }
 rows.push('END:VCALENDAR');
 return rows.map(fold).join('\r\n')+'\r\n';
}
