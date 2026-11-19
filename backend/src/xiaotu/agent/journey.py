import asyncio
from datetime import datetime, timezone
import re

from xiaotu.domain import CHINA, CLOCK, clean, obj, rows, valid_date


class JourneyError(ValueError):
    pass


def travel_request(x):
    x = obj(x)
    mode, time = clean(x.get("mode")), clean(x.get("time"), 12)
    return {"origin": clean(x.get("origin"), 30), "destination": clean(x.get("destination"), 30),
            "date": valid_date(x.get("date")), "time": time if re.fullmatch(CLOCK, time) else "",
            "mode": mode if mode in {"direct", "transfer", "auto"} else "direct",
            "middle": clean(x.get("middle"), 30), "originSource": "device" if x.get("originSource") == "device" else "manual",
            "filter": "G" if x.get("filter") == "G" else "", "skipRail": x.get("skipRail") is True}


def departure(trip, previous, old, explicit, transport, message):
    saved = travel_request(old) if isinstance(old, dict) and previous.get("city") == trip["city"] else {}
    transport = obj(transport)
    time = transport.get("time") if re.search(r"\d{1,2}[:：]\d{2}|[\d零一二两三四五六七八九十]+\s*点", message) else ""
    match = re.search(r"(?:出发时间|发车时间)\s*[：:]?\s*(\d{2}:\d{2})|(\d{2}:\d{2})\s*(?:出发|发车)", message)
    mode = transport.get("mode")
    if re.search(r"不(?:要|想|接受)?(?:换乘|中转)|只(?:要|看|坐)直达", message):
        mode = "direct"
    elif re.search(r"只(?:查|看|要)(?:一次)?(?:换乘|中转)", message):
        mode = "transfer"
    elif re.search(r"可(?:以)?(?:换乘|中转)|接受(?:换乘|中转)", message):
        mode = "auto"
    return travel_request({
        "origin": explicit.get("origin") or saved.get("origin") or trip.get("origin"),
        "destination": saved.get("destination") or trip["city"],
        "date": explicit.get("startDate") or (saved.get("date") if saved else trip.get("startDate")),
        "time": time or (next((g for g in match.groups() if g), "") if match else "") or saved.get("time"),
        "mode": mode or saved.get("mode"), "middle": transport.get("middle") or saved.get("middle"),
        "originSource": "manual" if explicit.get("origin") else saved.get("originSource"),
        "filter": "G" if "高铁" in message else saved.get("filter"),
        "skipRail": bool(re.search(r"只(?:能|想|考虑)?自驾|不坐(?:火车|高铁|动车)|只开车|只坐飞机", message)
                         or saved.get("skipRail") and not re.search(r"铁路|火车|高铁|动车|换乘|中转", message)),
    })


class Journey:
    def __init__(self, maps, rail, now=None):
        self.map, self.rail = maps, rail
        self.now = now or (lambda: datetime.now(CHINA))

    async def __call__(self, raw):
        request = travel_request(raw)
        origin, dest, mode = request["origin"], request["destination"], request["mode"]
        if not dest:
            raise JourneyError("请填写到达城市或车站。")
        if (raw.get("date") and not request["date"]) or (raw.get("time") and not request["time"]):
            raise JourneyError("请填写有效的出发日期和时间。")
        now = self.now().astimezone(CHINA)
        today, clock = now.date().isoformat(), now.strftime("%H:%M")
        day = request["date"] or today
        time = request["time"] or (clock if day == today else "00:00")
        after = max(clock, time) if day == today else time
        same = origin and origin.removesuffix("市") == dest.removesuffix("市")

        async def rail_result():
            if not origin:
                return dict(status="origin-needed", note="请使用定位城市或手动填写出发地。")
            if same:
                return dict(status="local", note="起终点相同。若要查市内铁路，请填写不同的车站。")
            if request["skipRail"]:
                return dict(status="skipped", note="按本次对话偏好未查询铁路。")
            if not self.rail.configured:
                return dict(status="unavailable", note="本地 12306 MCP 未启用。")
            args = dict(origin=origin, destination=dest, date=day, departureTime=after, limit=5)
            if request["filter"]:
                args["filter"] = request["filter"]
            try:
                action = "transfer" if mode == "transfer" else "tickets"
                if action == "transfer" and request["middle"]:
                    args["middle"] = request["middle"]
                result = await self.rail.query({**args, "action": action})
                if mode == "auto" and not result["items"]:
                    result = await self.rail.query({**args, "action": "transfer", "middle": request["middle"]})
                return {**result, "status": "available" if result["items"] else "empty",
                        "note": "未选定或购票。票价、余票以铁路 12306 为准。" if result["items"] else
                                "没有查到符合当前条件的车次；不等于停运或售罄。"}
            except Exception:
                return dict(status="unavailable", note="本次没有取得可核实的铁路数据，请稍后重试或到铁路 12306 查询。")

        async def driving():
            if not origin or same or not self.map.configured:
                return []
            try:
                a, b = await asyncio.gather(self.map.geocode(origin), self.map.geocode(dest))
                if not a.get("point") or not b.get("point") or a["point"] == b["point"]:
                    return []
                if any(x.get("city") and x["city"].removesuffix("市") != city.removesuffix("市")
                       for x, city in ((a, origin), (b, dest))):
                    return []
                route = await self.map.route({"origin": a["point"], "destination": b["point"], "mode": "car"})
                if any(route.get(k) is not None for k in ("distance", "duration")):
                    return [dict(mode="自驾", status="estimate", note="按两城市参考点估算，并非门到门。",
                                 **{k: route[k] for k in ("duration", "distance", "toll", "source") if k in route})]
            except Exception:
                pass
            return []

        rail, options = await asyncio.gather(rail_result(), driving())
        status = "origin-needed" if not origin else "local" if same else "partial"
        return dict(origin=origin, destination=dest, date=day, time=after, dateDefault=not request["date"],
                    timeDefault=not request["time"], timeAdjusted=bool(request["time"] and time != after),
                    request=request, generatedAt=now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    rail=rail, options=options, status=status, note={
                        "origin-needed": "请填写出发城市，或使用当前定位城市。",
                        "local": "出发城市与目的地相同，可在行程地图查看具体路线。",
                        "partial": "交通仅供参考；需另留进出站、候车与换乘时间。"}[status])


def rail_reply(data):
    action, result = data["action"], rows(data.get("items"))

    def line(x):
        seats = "；".join(f"{s.get('name', '')} " + (f"¥{s['price']} " if s.get("price") is not None else "")
                         + str(s.get("availability") or "") for s in rows(x.get("seats"))[:4])
        return f"{x.get('code', '')}  {x.get('from', '')} {x.get('depart', '')} → {x.get('to', '')} {x.get('arrive', '')}" + (" · " + seats if seats else "")

    if action == "stations":
        heading, records = "铁路车站", [f"{x['name']}（{x['code']}）" for x in result]
    elif action == "stops":
        heading, records = "列车经停", [f"{x['name']}：到 {x.get('arrive', '—')} 发 {x.get('depart', '—')}" for x in result]
    elif action == "transfer":
        heading, records = "一次换乘", ["经 " + x["middle"] + " 换乘\n" + "\n".join(map(line, x["segments"])) for x in result[:4]]
    else:
        heading, records = "直达车次", list(map(line, result[:8]))
    return "\n".join([heading, *(records or ["未查到符合条件的结果，不代表停运或售罄。"]),
                      "来源：铁路 12306 查询，余票与票价请以铁路 12306 为准。"])
