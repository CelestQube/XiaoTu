from datetime import date, datetime, timedelta, timezone
from math import ceil, floor
import re
import uuid

CHINA = timezone(timedelta(hours=8), "Asia/Shanghai")
CLOCK = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]"


def obj(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return value if isinstance(value, list) else []


def clean(value, maximum=100):
    return value.strip()[:maximum] if isinstance(value, str) else ""


def valid_date(value):
    if isinstance(value, str) and re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return ""


def number(value, low, high):
    return value if type(value) is int and low <= value <= high else None


def strings(value, limit=8, length=40):
    return list(dict.fromkeys(v for x in rows(value) if (v := clean(x, length))))[:limit]


def trip_data(raw):
    x = obj(raw)
    fields = {"city": clean(x.get("city"), 40), "origin": clean(x.get("origin"), 40),
              "days": number(x.get("days"), 1, 7), "people": number(x.get("people"), 1, 20),
              "budget": number(x.get("budget"), 1, 10_000_000), "startDate": valid_date(x.get("startDate")),
              "interests": strings(x.get("interests"))}
    return {k: v for k, v in fields.items() if v}


def profile_data(raw):
    x = obj(raw)
    fields = {"homeCity": clean(x.get("homeCity"), 40), "transport": clean(x.get("transport"), 40),
              "dailyBudget": number(x.get("dailyBudget"), 1, 100_000), "interests": strings(x.get("interests"))}
    return {k: v for k, v in fields.items() if v}


def plan_data(raw, count):
    days = rows(obj(raw).get("days"))
    if len(days) != count or not 1 <= len(days) <= 7:
        return None
    result = []
    for i, day in enumerate(map(obj, days), 1):
        if number(day.get("day"), 1, 7) != i or not 1 <= len(rows(day.get("items"))) <= 8:
            return None
        items, last = [], ""
        for item in map(obj, day["items"]):
            time, title, location = clean(item.get("time"), 20), clean(item.get("title"), 90), clean(item.get("location"), 100)
            if not re.fullmatch(CLOCK, time) or time <= last or not title or not location:
                return None
            last = time
            items.append({"time": time, "title": title, "location": location, "note": clean(item.get("note"), 180)})
        result.append({"day": i, "items": items})
    return {"title": clean(raw.get("title")) or "旅行计划", "days": result, "notes": strings(raw.get("notes"))}


def budget(trip, profile):
    days, people = trip["days"], trip["people"]
    nights, rooms = days - 1, ceil(people / 2)
    meals = min(240, max(35, floor(profile["dailyBudget"] * .2 + .5))) if "dailyBudget" in profile else 65
    return [dict(category=k, low=a, high=b, note=n) for k, a, b, n in (
        ("市内交通", 15*days*people, 40*days*people, "公共交通为基准；不含城际票价"),
        ("住宿", 160*nights*rooms, 320*nights*rooms, f"{nights} 晚 × {rooms} 间，非实时房价"),
        ("餐饮", meals*days*people, (meals+55)*days*people, f"{people} 人，按每天用餐估算"),
        ("门票与活动", 0, 100*days*people, "免费项目至常规付费项目的估算"))]


def calendar_file(body):
    start = valid_date(body.get("startDate"))
    plan = plan_data(body.get("plan"), len(rows(obj(body.get("plan")).get("days"))))
    if body.get("confirmed") is not True or not start or not plan:
        return None

    def escape(s):
        return s.replace("\\", "\\\\").replace("\r\n", "\\n").replace("\n", "\\n").replace(
            "\r", "\\n").replace(",", "\\,").replace(";", "\\;")

    def folded(line):
        chunks, chunk = [], ""
        for char in line:
            if len((chunk + char).encode()) > 75:
                chunks.append(chunk)
                chunk = " "
            chunk += char
        return "\r\n".join(chunks + [chunk])

    def stamp(instant):
        return instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    now = stamp(datetime.now(timezone.utc))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Xiaotu//Travel Planner//ZH-CN", "CALSCALE:GREGORIAN"]
    base = datetime.fromisoformat(start).replace(tzinfo=CHINA)
    for day in plan["days"]:
        for item in day["items"]:
            h, m = map(int, item["time"].split(":"))
            starts = base + timedelta(days=day["day"] - 1, hours=h, minutes=m)
            lines.extend(["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@xiaotu.local", "DTSTAMP:" + now,
                          "DTSTART:" + stamp(starts), "SUMMARY:" + escape(item["title"]),
                          "LOCATION:" + escape(item["location"]), "DESCRIPTION:" + escape(item["note"]), "END:VEVENT"])
    return "\r\n".join(map(folded, lines + ["END:VCALENDAR"])) + "\r\n"
