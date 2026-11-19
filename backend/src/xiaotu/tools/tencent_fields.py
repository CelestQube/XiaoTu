from __future__ import annotations

import math
import re
import unicodedata
from urllib.parse import urlsplit

from ._common import clean, utc_now
from .tencent_transport import MAP_SOURCE, MapError


def field(value, limit=100) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and number(abs(value)) is not None:
        return clean(str(value), limit)
    return clean(value, limit)


def array(value) -> list:
    return value if isinstance(value, list) else []


def obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def result_of(data):
    if isinstance(data, dict):
        return data.get("result", data.get("data", data))
    return data


def number(value, maximum=9007199254740991):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)) or isinstance(value, str) and len(value) > 60:
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and 0 <= value <= maximum else None


def norm(value) -> str:
    return re.sub(r"[\s·・-]+", "", unicodedata.normalize("NFKC", field(value, 150)).lower())


def city_name(value) -> str:
    city = field(value, 50)
    if not city or not all(c.isalnum() or c in " ·.-" for c in city):
        raise MapError(400, "请填写有效的目的地城市。")
    return city


def poi_id(value) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else None


def point_text(lng: float, lat: float) -> str:
    def single(x: float):
        result = f"{x:.6f}".rstrip("0").rstrip(".")
        return "0" if result == "-0" else result
    return single(lng) + "," + single(lat)


def map_point(value) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[-+]?[0-9]{1,3}(?:\.[0-9]{1,12})?,[-+]?[0-9]{1,3}(?:\.[0-9]{1,12})?", value):
        return None
    lng, lat = map(float, value.split(","))
    return point_text(lng, lat) if abs(lng) <= 180 and abs(lat) <= 90 else None


def to_vendor(point: str) -> str:
    lng, lat = point.split(",")
    return lat + "," + lng


def from_vendor(loc) -> str | None:
    lng, lat = obj(loc).get("lng"), obj(loc).get("lat")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lng, lat)) and abs(lng) <= 180 and abs(lat) <= 90:
        return point_text(lng, lat)
    return None


def wgs_to_gcj(point: str) -> str:
    lon, lat = map(float, point.split(","))
    if lon < 72.004 or lon > 137.8347 or lat < 0.8293 or lat > 55.8271:
        return point
    x, y, pi = lon - 105, lat - 35, math.pi
    def sine(n):
        return math.sin(n * pi)
    ecc, a = 0.006693421622965943, 6378245
    base = (20 * sine(6 * x) + 20 * sine(2 * x)) * 2 / 3
    d_lat = (-100 + 2 * x + 3 * y + .2 * y * y + .1 * x * y + .2 * math.sqrt(abs(x)) + base +
             (20 * sine(y) + 40 * sine(y / 3) + 160 * sine(y / 12) + 320 * sine(y / 30)) * 2 / 3)
    d_lon = (300 + x + 2 * y + .1 * x * x + .1 * x * y + .1 * math.sqrt(abs(x)) + base +
             (20 * sine(x) + 40 * sine(x / 3) + 150 * sine(x / 12) + 300 * sine(x / 30)) * 2 / 3)
    r = lat * pi / 180
    h = 1 - ecc * math.sin(r) ** 2
    root = math.sqrt(h)
    return point_text(lon + d_lon * 180 * root / (a * math.cos(r) * pi),
                      lat + d_lat * 180 * h * root / (a * (1 - ecc) * pi))


def photo_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 1600 or re.search(r"[\s\\]", value):
        return None
    try:
        u = urlsplit(value)
        if (u.scheme == "https" and not u.username and u.password is None and u.port is None
                and re.fullmatch(r"(?:(?:[a-z0-9-]+\.)*map\.qq\.com|(?:[a-z0-9-]+\.)*qpic\.cn)", u.hostname or "", re.I)):
            return value
    except ValueError:
        pass
    return None


def media(raw: dict) -> dict:
    out = {}
    rating, cost = number(raw.get("rating"), 5), number(raw.get("cost"))
    if rating:
        out["rating"] = rating
    if cost:
        out["cost"] = cost
    if hours := field(raw.get("hours", raw.get("opentime")), 100):
        out["hours"] = hours
    photos = [{"url": url, "title": field(obj(p).get("title"), 100)}
              for p in array(raw.get("photos"))[:12] if (url := photo_url(obj(p).get("url", p)))]
    if photos:
        out["photos"] = photos[:4]
    reviews = [text for p in array(raw.get("reviews"))[:8]
               if (text := field(obj(p).get("content", obj(p).get("text", p)), 220))]
    if reviews:
        out["reviews"] = reviews[:3]
    return out


def poi_data(raw, city: str) -> dict | None:
    raw = obj(raw)
    ident, name, point = poi_id(raw.get("id")), field(raw.get("title", raw.get("name")), 100), from_vendor(raw.get("location"))
    if not ident or not name or not point or raw.get("type") not in (0, "0", None):
        return None
    area = obj(raw.get("ad_info", raw))
    regions = {norm(area.get(key)).removesuffix("市") for key in ("city", "district", "province")} - {""}
    code = field(area.get("adcode"), 6)
    by_code = bool(re.fullmatch(r"[0-9]{6}", city) and code and
                   (code == city or city.endswith("00") and code.startswith(city[:4])))
    if norm(city).removesuffix("市") not in regions and not by_code:
        return None
    return {"id": ident, "name": name, "point": point, "city": field(area.get("city"), 50),
            "district": field(area.get("district"), 50), "address": field(raw.get("address"), 150),
            "type": field(raw.get("category"), 100), "source": MAP_SOURCE, "fetchedAt": utc_now(), **media(raw)}


def unique(places: list[dict]) -> list[dict]:
    return list({p["id"]: p for p in places}.values())


def placeholder(name: str) -> bool:
    return not name or bool(re.search(r"待选|待定|待核实|附近$|周边$|^(?:酒店|餐厅|酒店入住|午餐|晚餐|早餐|市区|当地|景点)$", name))


def decode_polyline(raw) -> list[str]:
    if not isinstance(raw, list) or not 4 <= len(raw) <= 80000 or len(raw) % 2:
        return []
    if any(not isinstance(x, (int, float)) or isinstance(x, bool) or abs(x) > 1e12 or not math.isfinite(x) for x in raw):
        return []
    lat, lng = raw[:2]
    out = []
    for i in range(0, len(raw), 2):
        if i:
            lat += raw[i] / 1e6
            lng += raw[i + 1] / 1e6
        if abs(lat) > 90 or abs(lng) > 180:
            return []
        point = point_text(lng, lat)
        if not out or point != out[-1]:
            out.append(point)
    if len(out) <= 100:
        return out
    return [out[math.floor(i * (len(out) - 1) / 99 + .5)] for i in range(100)]


def transit_paths(route: dict) -> list[list[str]]:
    paths = []
    for s in array(route.get("steps"))[:120]:
        s = obj(s)
        lines = array(s.get("lines"))
        for p in (s.get("polyline"), obj(lines[0]).get("polyline") if lines else None):
            if len(decoded := decode_polyline(p)) > 1:
                paths.append(decoded)
                if len(paths) >= 20:
                    return paths
    return paths


def steps_of(route: dict, transit: bool) -> list[str]:
    def instructions(rows):
        return [text for step in array(rows)[:80] if (text := field(obj(step).get("instruction"), 170))]
    if not transit:
        return instructions(route.get("steps"))
    all_steps = []
    for step in array(route.get("steps"))[:80]:
        step = obj(step)
        more = instructions(step.get("steps"))
        for line in array(step.get("lines"))[:1]:
            line = obj(line)
            name = field(line.get("title", line.get("name")), 75)
            on, off = obj(line.get("geton")), obj(line.get("getoff"))
            departure, arrival = field(on.get("title", on.get("name")), 40), field(off.get("title", off.get("name")), 40)
            if name:
                more.append("乘坐 " + name + ("：" + departure + " → " + arrival if departure and arrival else ""))
        all_steps.extend(more or instructions([step]))
    return all_steps[:80]


def suggestion_text(text: str):
    count = re.match(r"^找到(?:约)?([0-9]+)条结果", text.strip())
    if not count:
        return None
    if count[1].strip("0") == "":
        return []
    out = []
    for block in re.split(r"^[ \t]*\([0-9]{1,3}\)[ \t]*", text.strip(), maxsplit=41, flags=re.M)[1:41]:
        rows = block.splitlines()
        if not rows:
            continue
        title, *lines = rows
        fields = {}
        for line in lines:
            if m := re.match(r"^[ \t]*([^：:]+)[：:][ \t]*(.*)$", line):
                fields[m[1].strip()] = m[2].strip()
        ident = fields.get("地点ID(POI ID)")
        point = map_point(fields.get("经度", "") + "," + fields.get("纬度", ""))
        if poi_id(ident) and point and fields.get("市"):
            lng, lat = map(float, point.split(","))
            out.append({"id": ident, "title": title, "city": fields.get("市"), "district": fields.get("区"),
                        "province": fields.get("省"), "address": fields.get("地址"), "category": fields.get("类型"),
                        "location": {"lng": lng, "lat": lat}})
    return out or None


def text_address(text: str):
    parts = {}
    for line in text.strip().splitlines():
        if (m := re.match(r"^([^（(:：]+)(?:[（(][^）)]*[）)])?[:：]\s*(.*)$", line)) and m[2]:
            parts[m[1].strip()] = m[2].strip()
    address, city = parts.get("坐标所在地址"), parts.get("市")
    position = map_point(parts.get("经度", "") + "," + parts.get("纬度", ""))
    if not address and not city and not position:
        return None
    result = {"address": address, "ad_info": {"city": city, "province": parts.get("省"),
              "district": parts.get("区"), "adcode": parts.get("行政区划代码")}}
    if position:
        lng, lat = map(float, position.split(","))
        result["location"] = {"lng": lng, "lat": lat}
    return result


def text_route(text: str):
    def extract(pattern, value=text):
        m = re.search(pattern, value)
        return number(m[1]) if m else None
    if re.match(r"^为您找到[0-9]+条乘坐方案", text.strip()):
        choices = re.split(r"^方案[0-9]+[:：]", text, maxsplit=2, flags=re.M)
        if len(choices) < 2 or not choices[1].strip():
            return None
        head, *rest = choices[1].strip().splitlines()
        minutes = extract(r"用时\s*([0-9]+(?:\.[0-9]+)?)\s*分钟", head)
        if minutes is None:
            return None
        walk = re.search(r"总步行\s*([0-9]+(?:\.[0-9]+)?)\s*(公里|米)", head)
        return {"duration": minutes, "fare": extract(r"预估费用\s*([0-9]+(?:\.[0-9]+)?)\s*元", head),
                "walking_distance": float(walk[1]) * (1000 if walk[2] == "公里" else 1) if walk else None,
                "steps": [{"instruction": s} for row in rest if (s := row.strip()) and s not in {"详细换乘方案:", "详细换乘方案："}]}
    distance = extract(r"(?:路线总距离|总距离)[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*米")
    minutes = extract(r"(?:路线预估用时|预估用时|总耗时|总用时)[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*分钟")
    if distance is None and minutes is None:
        return None
    road = re.search(r"途经道路[:：]\s*([^\r\n]+)", text)
    return {"distance": distance, "duration": minutes,
            "taxi_fare": {"fare": extract(r"预估打车费[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*元")},
            "toll": extract(r"过路费(?:/通用费)?[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*元"),
            "fare": extract(r"(?:公交票价|总票价|路线总票价)[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*元"),
            "steps": [{"instruction": "途经道路：" + road[1][:155]}] if road else []}


def text_forecast(text: str):
    if not re.search(r"未来\s*[0-9]+\s*天天气预报", text):
        return None
    infos = []
    for line in text.strip().splitlines():
        if m := re.match(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})(?:[（(].*)?$", line):
            infos.append({"date": m[1]})
        elif infos and (m := re.match(r"^(白天|夜晚)[:：]\s*([^,，\s]+)", line)):
            period = {"weather": m[2]}
            if temp := re.match(r"^\s*[,，]\s*(?:气温\s*[:：]?\s*)?([+-]?\d{1,2}(?:\.\d)?)\s*(?:°\s*C|℃)", line[m.end():], re.I):
                period["temperature"] = temp[1]
            infos[-1]["day" if m[1] == "白天" else "night"] = period
    return {"forecast": [{"infos": infos}]}
