"""Read-only lookup results for the chat API; cards never come from Qwen."""
from datetime import date, timedelta
import re

from xiaotu.agent import demo
from xiaotu.agent.weather import weather_context
from xiaotu.domain import clean, obj, rows, valid_date
from xiaotu.tools._common import ToolError, utc_now

WEATHER_SOURCE = "腾讯地图 MCP · 天气预报"
RAIL_SOURCE = "铁路 12306 · 第三方 MCP"
CITIES = "|".join(demo.KNOWN)
RAIL_END = rf"(?:{CITIES})(?:虹桥|东|西|南|北)?站?"
RAIL_ROUTE = re.compile(rf"({RAIL_END})\s*(?:到|去|至|→)\s*({RAIL_END})")
RELATIVE = {"今天":0, "明天":1, "后天":2, "大后天":3}
COMMON_DAY = re.compile(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}|20\d{2}年\d{1,2}月\d{1,2}日|大后天|后天|明天|今天|今早|今夜|今晚|明早|明晚")
TRIP_REF = re.compile(r"这趟|这次(?:旅行|行程)|(?:我的|当前|这份)行程|目的地|到那(?:边|天)|去的地方")


def date_in(message, today, proposed):
    matches = list(COMMON_DAY.finditer(message))
    if matches:
        day = matches[-1][0]
        if day in RELATIVE:
            return (date.fromisoformat(today) + timedelta(days=RELATIVE[day])).isoformat()
        if day.startswith(("今", "明")):
            return (date.fromisoformat(today) + timedelta(days=int(day.startswith("明")))).isoformat()
        parts = re.findall(r"\d+", day)
        return "-".join(v.zfill(2) for v in parts)
    if re.search(r"周|星期|日后|天后|月|(?<!\d)\d{1,2}号|元旦|春节|清明|五一|端午|中秋|国庆", message):
        return valid_date(proposed)
    return ""


def rail_request(message, query, previous, today):
    request = {k: v for k in ("origin","destination","keyword","trainCode","middle","filter")
               if (v := clean(query.get(k), 48))}
    if query.get("exact") is True:
        request["exact"] = True
    if match := RAIL_ROUTE.search(message):
        request["origin"], request["destination"] = match.groups()
    if day := date_in(message, today, query.get("date")):
        request["date"] = day
    if TRIP_REF.search(message):
        for key, original in (("origin","origin"), ("destination","city"), ("date","startDate")):
            if not request.get(key) and previous.get(original):
                request[key] = previous[original]
    if "高铁" in message:
        request["filter"] = "G"
    action = query.get("action")
    if re.search(r"经停|途经哪些(?:站|车站)|停靠哪些站", message):
        action = "stops"
        if code := re.search(r"(?<![A-Z0-9])[GDCZTKYS][0-9]{1,5}(?![A-Z0-9])", message, re.I):
            request["trainCode"] = code[0].upper()
    elif re.search(r"车站三字码|站三字码|三字码|有哪些(?:高铁站|火车站|铁路车站|车站)|车站(?:代码|编码)", message):
        action = "stations"
        if not request.get("keyword") and (station := re.search(RAIL_END, message)):
            request["keyword"] = station[0]
    elif re.search(r"(?:只看|查一下|查|接受|怎么|如何)?(?:一次)?(?:换乘|中转)", message) and not re.search(r"不(?:要|接受|想)?(?:换乘|中转)|只看直达", message):
        action = "transfer"
    elif re.search(r"不(?:要|接受|想)?(?:换乘|中转)|只看直达", message):
        action = "tickets"
    request["action"] = action if action in ("transfer", "stops", "stations") else "tickets"
    return request


def _answer(reply, source=None, card=None, *, status="live"):
    return {"reply": reply, **({"sources":[{"name":source,"status":status,"note":reply}]} if source else {}),
            **({"cards":[card]} if card else {})}


async def query_chat(parsed, maps, rail):
    query, message, previous, today = (parsed[k] for k in ("query","lookup_text","previous","today"))
    kind = query.get("kind")
    source = {"weather":WEATHER_SOURCE, "rail":RAIL_SOURCE}.get(kind)
    try:
        if kind == "rail":
            request = rail_request(message, query, previous, today)
            action = request["action"]
            keys = {"stations": (("keyword","城市或车站"),), "stops": (("trainCode","车次"),("date","发车日期"))}.get(
                action, (("origin","出发站"),("destination","到达站"),("date","出发日期")))
            missing = "、".join(label for key, label in keys if not request.get(key))
            if missing:
                return _answer(f"请补充{missing}，我再查铁路信息。")
            result = await rail.query(request)
            items = rows(result.get("items"))
            context = (request["keyword"] if action == "stations" else f"{request['date']} {request['trainCode']}"
                       if action == "stops" else f"{request['date']} {request['origin']} → {request['destination']}")
            reply = (f"{context}：查到 {len(items)} 条铁路结果。" if items
                     else f"{context}：没有查到符合条件的结果，不代表停运或售罄。")
            if re.search(r"帮我(?:买|订)|代买|代订|抢票|下单", message):
                reply += " 小途只做查询，不代购，请到铁路 12306 下单。"
            return _answer(reply, RAIL_SOURCE, {**result, "type":"rail"})
        if kind == "transport":
            return _answer("航班和公路客运票务尚未接入。铁路可按起终点和日期查询。")

        city = clean(query.get("city"), 40)
        mentioned = list(dict.fromkeys(re.findall(CITIES, message)))
        if (len(mentioned) == 1 and mentioned[0] not in city) or (mentioned and not city):
            city = mentioned[0]
        if not city and TRIP_REF.search(message):
            city = previous.get("city", "")
        if kind in ("weather", "place") and not city:
            return _answer("你想查询哪个城市的天气？" if kind == "weather" else "你想查询哪个城市？")
        if kind == "weather":
            target = date_in(message, today, query.get("date"))
            if target and not valid_date(target):
                return _answer("这个日期无效，请确认后再查。")
            report = weather_context(await maps.forecast(city), {"days": 1}, today) if maps.configured else None
            if not report:
                return _answer(f"暂未取得{city}的可核实天气预报。", source, status="unavailable")
            available = report["days"]
            days = [d for d in available if not target or d["date"] == target]
            if not days:
                reply = f"{city}的预报未覆盖 {target}，目前可查范围：{report['horizon']}。"
            elif target:
                reply = f"{city} {target}：白天{days[0]['day']}" + (f"，夜间{days[0]['night']}。" if days[0]["night"] else "。")
            else:
                reply = f"查到了{city}的近期天气预报（{report['horizon']}）。"
            if days and re.search(r"温度|气温|几度|多少度", message) and not any(
                    "dayTemp" in d or "nightTemp" in d for d in days):
                reply += " 天气工具未返回所查日期的气温。"
            card = dict(type="weather", city=city, days=days, requestedDate=target, horizon=report["horizon"],
                        advisory=report["advisory"], source=source, checkedAt=utc_now())
            return _answer(reply, source, card)
        if kind == "place" and maps.configured and (keyword := clean(query.get("keyword"), 80)):
            data = await maps.day({"city":city,"locations":[keyword]})
            poi = next((x["poi"] for x in map(obj, rows(data.get("stops"))) if x.get("poi")), None)
            return _answer(f"{city} · {poi['name']}：{poi.get('address') or '已匹配，未返回详细地址。'}"
                           if poi else "没有找到可靠匹配，请尝试地图上的完整名称。", "腾讯地图 MCP · 地点")
    except ToolError as e:
        return _answer(str(e), source, status="unavailable")
    return _answer("暂时没有可核实的实时数据。", source, status="unavailable")
