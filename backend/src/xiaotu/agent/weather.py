from datetime import date, timedelta
import re

from xiaotu.domain import clean, obj, rows, valid_date

SEVERE = re.compile(r"暴雨|暴雪|台风|强对流|雷暴|冰雹|沙尘暴|冻雨")
ADVERSE = re.compile(r"雨|雪|冰雹|大风|雾|沙尘|霾")


def _offset(day, amount):
    return (date.fromisoformat(day) + timedelta(days=amount)).isoformat()


def _score(value):
    return -10 if SEVERE.search(value) else -3 if ADVERSE.search(value) else 3 if re.search(r"晴|多云", value) else 0


def weather_context(report, trip, today):
    report = obj(report)
    by_date = {}
    for item in map(obj, rows(report.get("days"))):
        day = valid_date(item.get("date"))
        if day and day >= today and clean(item.get("day"), 25):
            temperatures = {k: v for k in ("dayTemp", "nightTemp")
                            if type(v := item.get(k)) in (int, float) and -100 <= v <= 80}
            by_date[day] = dict(date=day, day=clean(item["day"], 25), night=clean(item.get("night"), 25), **temperatures)
    if not by_date:
        return None
    days = [by_date[d] for d in sorted(by_date)]
    horizon = days[0]["date"] + (" 至 " + days[-1]["date"] if len(days) > 1 else "")
    advisory, suggestion, itinerary = [], None, []
    severe = [x["date"] for x in days if SEVERE.search(x["day"] + x["night"])]
    if severe:
        advisory.append("预报显示 " + "、".join(severe) + " 可能有较强不利天气；请核对当地气象部门预警。")
    streak, high, prev = 0, 0, ""
    for item in days:
        streak = (streak + 1 if prev and _offset(prev, 1) == item["date"] else 1) if ADVERSE.search(item["day"]) else 0
        prev, high = item["date"], max(streak, high)
    if high >= 3:
        advisory.append(f"近期预报连续 {high} 天白天可能有不利天气；建议预留室内方案或考虑改期。")
    alerts = [clean(a, 100) for a in rows(report.get("alerts"))[:4] if clean(a, 100)]
    if alerts:
        advisory.append("天气工具返回预警：" + "；".join(alerts) + "。请核对适用范围。")
    length = trip.get("days", 1)
    if not trip.get("startDate"):
        choices = [[by_date.get(_offset(day, n)) for n in range(length)] for day in sorted(by_date)]
        best = max((x for x in choices if all(x)), key=lambda r: sum(2*_score(x["day"]) + _score(x["night"]) for x in r), default=None)
        if best and any(_score(x["day"]) > 0 for x in best) and all(x["date"] not in severe for x in best):
            suggestion = {"start": best[0]["date"], "end": best[-1]["date"]}
    target = trip.get("startDate") or (suggestion or {}).get("start")
    if target:
        for n in range(length):
            day = _offset(target, n)
            item = by_date.get(day, {})
            itinerary.append(dict(day=n+1, date=day, daytime=item.get("day", ""), night=item.get("night", ""), covered=bool(item)))
        if any(not x["covered"] for x in itinerary):
            advisory.append("部分行程日期超出预报范围，不按近期天气推断。")
    detail = "；".join(f"{x['date']} 白天{x['day']}" + (f"、夜间{x['night']}" if x["night"] else "") for x in days)
    return dict(source=report.get("source", "腾讯地图 MCP"), days=days, itinerary=itinerary, advisory=advisory,
                horizon=horizon, summary=f"目的地天气预报（{horizon}）：{detail}", **({"suggestion": suggestion} if suggestion else {}))
