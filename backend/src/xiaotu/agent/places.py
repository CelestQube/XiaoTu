"""POI proof format and offline itinerary editing.

The v1 canonical field order and serialization match the Node prototype so a saved
browser catalog can be validated after the backend switch without storing location.
"""
from copy import deepcopy
import hashlib
import hmac
import json
import math
import re
import unicodedata

from xiaotu.domain import clean, number, obj, plan_data, rows, trip_data

LABELS = {"activity": "活动地点", "food": "餐馆", "stay": "住宿"}
MAP_SOURCE = "腾讯地图 MCP"


def normalized(value):
    return re.sub(r"[\s·・-]+", "", unicodedata.normalize("NFKC", clean(value)).lower())


def pending(value):
    return not value or bool(re.search(r"待选|待定|待核实|附近$|周边$|^(酒店|餐厅|餐馆|入住|酒店入住|午餐|晚餐|早餐|市区|当地|景点|住宿|不指定地点)$", value))


def point(value):
    if not isinstance(value, str) or not re.fullmatch(r"[-+]?\d{1,3}(?:\.\d{1,12})?,[-+]?\d{1,3}(?:\.\d{1,12})?", value, re.ASCII):
        return None
    lng, lat = map(float, value.split(","))
    if abs(lng) <= 180 and abs(lat) <= 90:
        return ",".join(f"{x:.6f}".rstrip("0").rstrip(".").removeprefix("-") if abs(x) < .0000005
                        else f"{x:.6f}".rstrip("0").rstrip(".") for x in (lng, lat))
    return None


class Proof:
    def __init__(self, key):
        self.secret = hmac.digest(key.encode(), b"xiaotu/local-poi-proof/v1", "sha256") if key else None

    def issue(self, city, raw):
        p = obj(raw)
        name, coords, ident = clean(p.get("name")), point(p.get("point")), clean(p.get("id"), 64)
        if not (city and name and coords and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident)):
            return None
        data = dict(id=ident, name=name, point=coords, tripCity=city, city=clean(p.get("city"), 50),
                    address=clean(p.get("address"), 150), type=clean(p.get("type")), source=MAP_SOURCE,
                    fetchedAt=clean(p.get("fetchedAt"), 40))
        if self.secret:
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
            data["proof"] = hmac.new(self.secret, raw, hashlib.sha256).hexdigest()
        return data

    def verify(self, city, location, raw):
        proof = clean(obj(raw).get("proof"), 65)
        if not self.secret or obj(raw).get("tripCity") != city or obj(raw).get("name") != location or not re.fullmatch(r"[a-f0-9]{64}", proof):
            return None
        data = self.issue(city, raw)
        return data if data and hmac.compare_digest(data["proof"], proof) else None

    def catalog(self, city, places):
        return [p for x in rows(places)[:48] if (p := self.verify(city, obj(x).get("name"), x))]


def pool(places):
    return [{**p, "ref": f"P{i}"} for i, p in enumerate(list({p["id"]: p for p in map(obj, places)
            if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", clean(p.get("id"), 65)) and clean(p.get("name")) and point(p.get("point"))}.values())[:64], 1)]


def _kind(text):
    if re.search(r"餐馆|餐厅|餐饮|美食|咖啡|用餐|吃饭|小吃|早饭|午饭|晚饭|早餐|午餐|晚餐", text):
        return "food"
    if re.search(r"酒店|住宿|入住|民宿|旅馆|宾馆|旅店|客栈", text):
        return "stay"
    return None


def slot(item):
    known = clean(item.get("placeKind"))
    if known in LABELS or known == "other":
        return known
    if re.search(r"自由活动|自行安排|休息时间|休整|机动时间|预留(?:时间|机动)|返程|回程|抵达|候车|前往目的地|退房", item["title"]):
        return "other"
    return _kind(item["title"]) or _kind(item["location"]) or "activity"


def venue(p):
    if re.search(r"停车场|出入口|入口|出口|售票处|售票厅|公交站|洗手间|游客中心", clean(obj(p).get("name"))):
        return None
    type_ = clean(obj(p).get("type"))
    return _kind(type_) or ("activity" if re.search(r"景点|公园|博物馆|美术馆|展览馆|名胜|遗址|植物园|动物园|纪念馆|文物古迹|风景区|游乐|人文景观|旅游", type_) else None)


def weather_ok(candidate, coverage, time):
    condition = obj(coverage).get("night" if time >= "18:00" else "daytime") or ""
    name, type_ = candidate.get("name", ""), candidate.get("type", "")
    return not re.search(r"雨|雪|冰雹|台风|强对流|沙尘暴", clean(condition)) or not re.search(
        r"公园|动物园|植物园|海滨|海滩|古镇|风景|游乐|山|湖|湿地", name + " " + type_) or bool(
        re.search(r"博物馆|美术馆|展览馆|纪念馆", name))


def distance(a, b):
    x, y = point(obj(a).get("point")), point(obj(b).get("point"))
    if not x or not y:
        return None
    lng, lat = map(lambda s: math.radians(float(s)), x.split(","))
    lon, lad = map(lambda s: math.radians(float(s)), y.split(","))
    h = math.sin((lad-lat)/2)**2 + math.cos(lat)*math.cos(lad)*math.sin((lon-lng)/2)**2
    return 12742 * math.asin(math.sqrt(min(1, max(0, h))))


def nearby(items, index, p):
    left = next((x["poi"] for x in reversed(items[:index]) if x.get("poi")), None)
    right = next((x["poi"] for x in items[index+1:] if x.get("poi")), None)
    direct = distance(left, right)
    legs = [d for d in (distance(left, p), distance(p, right)) if d is not None]
    return legs, sum(legs) - direct if direct is not None else 0


def close(fit, kind):
    return max(fit[0], default=0) <= (3 if kind == "food" else 6) and fit[1] <= (3 if kind == "food" else 5)


def ranked(places, kind, items, index, used, coverage=None):
    candidates = [(p, nearby(items, index, p)) for p in places if venue(p) == kind and p["id"] not in used]
    return sorted(candidates, key=lambda pair: (not weather_ok(pair[0], coverage, items[index]["time"]),
                                                round(sum(pair[1][0])*20), max(pair[1][0], default=0)))


def summary(days):
    statuses = [x.get("mapStatus") for d in days for x in d["items"]]
    return f"地图已核实 {statuses.count('verified')} 项，待核实 {statuses.count('pending')} 项，待选 {statuses.count('placeholder')} 项。"


def prior(city, raw, proof):
    days = plan_data(raw, len(rows(obj(raw).get("days"))))
    if not days:
        return None
    for i, day in enumerate(days["days"]):
        for j, item in enumerate(day["items"]):
            old = obj(raw["days"][i]["items"][j])
            p = proof.verify(city, item["location"], old.get("poi"))
            item["placeKind"] = slot({**item, "placeKind": old.get("placeKind")})
            if p:
                item.update(poi=p, mapStatus="verified")
            elif not pending(item["location"]) or item["placeKind"] != "other":
                item["mapStatus"] = "placeholder" if pending(item["location"]) else "pending"
            if old.get("locked") is True and item.get("mapStatus") in {"verified", "pending"}:
                item["locked"] = True
    return days


def annotate(city, plan, draft, places, pinned, old, proof, coverage, changed_day):
    old_days = obj(old).get("days", [])
    locked = [x for d in old_days[:len(plan["days"])] for x in d["items"] if x.get("locked")]
    names = {normalized(x["location"]) for x in locked}
    pinned = {normalized(x) for x in pinned} | {normalized(x["location"]) for d in old_days for x in d["items"]}
    used = {x["poi"]["id"] for x in locked if "poi" in x}
    by_ref = {p["ref"]: p for p in places}
    fill = []
    for i, day in enumerate(plan["days"]):
        if changed_day is not None and i != changed_day:
            day["items"] = deepcopy(old_days[i]["items"])
            used.update(x["poi"]["id"] for x in day["items"] if x.get("poi"))
            continue
        fixed = [x for x in old_days[i]["items"] if x.get("locked")] if i < len(old_days) else []
        raw_refs = {x.get("time"): clean(x.get("placeRef"), 10) for x in map(obj, draft["days"][i]["items"])}
        items = []
        for item in day["items"]:
            key = normalized(item["location"])
            if key in names or any(x["time"] == item["time"] for x in fixed):
                continue
            kind = item["placeKind"] = slot(item)
            p = by_ref.get(raw_refs.get(item["time"]))
            if not p or normalized(p["name"]) != key:
                hits = [p for p in places if normalized(p["name"]) == key]
                p = hits[0] if len(hits) == 1 else None
            result = proof.issue(city, p)
            if result and result.get("proof"):
                item.update(poi=result, location=result["name"], mapStatus="verified")
                used.add(result["id"])
            elif key in pinned and not pending(item["location"]):
                item["mapStatus"] = "pending"
            elif kind == "other":
                item.update(location="不指定地点", note="")
            else:
                item.update(location=LABELS[kind] + "（待选）", mapStatus="placeholder",
                            note="可从推荐中选择，或搜索想去的地点。")
                fill.append((i, item))
            items.append(item)
        day["items"] = sorted(items[:8-len(fixed)] + fixed, key=lambda item: item["time"]) or [
            dict(time="10:00", title="游览安排", location="活动地点（待选）", note="", mapStatus="placeholder")]
    for i, item in fill:
        items = plan["days"][i]["items"]
        if item not in items:
            continue
        conditions = coverage[i] if i < len(coverage) else {}
        choices = ranked([p for p in places if weather_ok(p, conditions, item["time"])],
                         item["placeKind"], items, items.index(item), used)
        if choices and close(choices[0][1], item["placeKind"]):
            p = proof.issue(city, choices[0][0])
            if p and p.get("proof"):
                item.update(location=p["name"], poi=p, mapStatus="verified", note="地图同类地点；请核对营业或预约信息。")
                used.add(p["id"])
    return summary(plan["days"])


class PlaceError(ValueError):
    pass


def context(body, proof):
    city = trip_data(body.get("trip")).get("city")
    old = prior(city, body.get("plan"), proof) if city else None
    d, ix = number(body.get("day"), 0, 6), number(body.get("index"), 0, 7)
    if not old or d is None or ix is None or d >= len(old["days"]) or ix >= len(old["days"][d]["items"]):
        raise PlaceError("行程或活动已变更，请刷新后重试。")
    return city, old["days"], d, ix


async def options(body, proof, maps):
    city, days, d, ix = context(body, proof)
    raw = obj(body.get("plan"))
    items = days[d]["items"]
    kind = items[ix]["placeKind"]
    if kind not in LABELS:
        return {"places": []}
    used = {x["poi"]["id"] for day in days for x in day["items"] if x.get("poi")}
    places = proof.catalog(city, raw.get("placeCatalog"))
    warnings = {}
    if not places and maps.configured:
        try:
            places = await maps.candidates(city)
        except Exception:
            warnings["warning"] = "暂时无法获取地点推荐。"
    result = []
    for p, fit in ranked(pool(places), kind, items, ix, used)[:3]:
        if signed := proof.issue(city, p):
            result.append({**signed, "nearby": close(fit, kind) if fit[0] else None})
    return {"places": result, **warnings}


def preview(body, proof):
    city, days, d, ix = context(body, proof)
    items, choice = days[d]["items"], obj(body.get("choice"))
    original = items[ix]
    manual = choice.get("manual") is True
    name = clean(choice.get("name") if manual else obj(choice.get("poi")).get("name"))
    p = None if manual else proof.verify(city, name, choice.get("poi"))
    if pending(name) or not (p or manual):
        raise PlaceError("请选取一个地图结果，或明确使用手动填写的具体地点。")
    def same_place(item):
        if p and item.get("poi"):
            return p["id"] == item["poi"]["id"]
        return normalized(item["location"]) == normalized(name)
    dup = [(a, j, x) for a, day in enumerate(days) for j, x in enumerate(day["items"]) if (a, j) != (d, ix)
           and same_place(x) and original["placeKind"] != "stay"]
    same = same_place(original)
    note = original["note"] if same else "出发前请核对营业、预约及交通时间。" if p else "手动填写，尚未在地图核实。"
    label = {"activity": "游览", "food": "用餐", "stay": "住宿"}.get(original["placeKind"], "前往")
    replacement = {**{k: original[k] for k in ("time", "placeKind")}, "title": original["title"] if same else clean(f"{label} · {name}", 90),
                   "location": name, "locked": True, "note": note, "mapStatus": "verified" if p else "pending",
                   **({"poi": p} if p else {})}
    replaced = [*items[:ix], replacement, *items[ix+1:]]
    out = []

    def option(id_, label_, changes, notes, recommended):
        updated = [{"day": day["day"], "items": changes.get(i, day["items"])} for i, day in enumerate(days)]
        out.append(dict(id=id_, label=label_, recommended=recommended, notes=notes,
                        days=[dict(index=i, items=vals) for i, vals in changes.items()], summary=summary(updated)))

    distance_note = "未核实坐标，无法判断绕路。" if not p else "仅比较已核实地点的直线距离，不代表实际车程。"
    option("keep", "仍放在这里，保留重复地点" if dup else "仅替换这一项", {d: replaced},
           [distance_note, *(["该地点在其他时段已有安排。"] if dup else [])], not dup)
    if len(dup) == 1 and p:
        a, j, x = dup[0]
        if obj(x.get("poi")).get("id") == p["id"] and not x.get("locked") and len(days[a]["items"]) > 1:
            changes = {d: replaced}
            changes[a] = [v for k, v in enumerate(replaced if a == d else days[a]["items"]) if k != j]
            option("move", "移到这里，不重复游览", changes, [f"移除第 {a+1} 天 {x['time']} 的重复安排。"], True)
    if not same and not dup and p and original["placeKind"] == venue(p) == "activity":
        coverage = rows(obj(obj(body["plan"]).get("weather")).get("itinerary"))
        covered = coverage[d] if d < len(coverage) else {}
        best = None
        for j, item in enumerate(replaced):
            if j == ix or item.get("locked") or item["placeKind"] != "activity" or venue(item.get("poi")) != "activity":
                continue
            if not weather_ok(p, covered, item["time"]) or not weather_ok(item["poi"], covered, original["time"]):
                continue
            swap = replaced.copy()
            swap[j], swap[ix] = {**replacement, "time": item["time"]}, {**item, "time": original["time"]}
            edges = {i for i in (ix-1, ix, j-1, j) if 0 <= i < len(swap)-1}
            def legs(seq):
                return [distance(seq[i].get("poi"), seq[i+1].get("poi")) for i in edges]
            before, after = legs(replaced), legs(swap)
            if any((a is None) != (b is None) for a, b in zip(before, after)) or sum(a is not None for a in before) < 2:
                continue
            a, b = sum(v or 0 for v in before), sum(v or 0 for v in after)
            if a - b >= 1 and b < a*.86 and (best is None or a-b > best[0]-best[1]):
                best = a, b, swap
        if best:
            out[0]["recommended"] = False
            option("reorder", "调整当天两个游览时段", {d: best[2]},
                   [f"受影响的已知路段约从 {best[0]:.1f} 降到 {best[1]:.1f} 公里；请核对开放时间。"], True)
        here = nearby(items, ix, p)
        if here[0] and not close(here, "activity"):
            alternatives = []
            for a, day in enumerate(days):
                if a == d:
                    continue
                for j, item in enumerate(day["items"]):
                    if (item["placeKind"] != "activity" or item.get("locked") or item.get("mapStatus") != "placeholder"
                            or not weather_ok(p, coverage[a] if a < len(coverage) else {}, item["time"])):
                        continue
                    fit = nearby(day["items"], j, p)
                    if fit[0] and close(fit, "activity") and max(here[0]) - max(fit[0]) >= 3:
                        alternatives.append((max(fit[0]), a, j))
            if alternatives:
                _, a, j = min(alternatives)
                next_items = days[a]["items"].copy()
                next_items[j] = {**replacement, "time": next_items[j]["time"]}
                option("another", f"改填第 {a+1} 天的待选项", {a: next_items}, ["保留当前活动，不挪走已有地点。"], True)
    return {"location": name, "options": out}
