from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import date

import httpx

from ._common import AsyncCache, utc_now
from .tencent_fields import (
    array, city_name, decode_polyline, field, from_vendor, map_point, media, norm, number, obj, placeholder,
    poi_data, poi_id, result_of, steps_of, suggestion_text, text_address, text_forecast, text_route, to_vendor,
    transit_paths, unique, wgs_to_gcj,
)
from .tencent_transport import MAP_SOURCE, MAP_TOOLS, MapError, TencentMcp, json_load

__all__ = ["MAP_SOURCE", "MapError", "TencentMap", "map_point"]

MODES = {"walk": ("directionWalking", "walking"), "car": ("directionDriving", "driving"),
         "bike": ("directionBicycling", "bicycling"), "transit": ("directionTransit", "transit")}
REST_BASE = "https://apis.map.qq.com/ws/"
PNG_START = b"\x89PNG\r\n\x1a\n"


def _iso_date(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


async def _bounded(res: httpx.Response, limit: int, failure: MapError):
    out = bytearray()
    async for chunk in res.aiter_bytes():
        if len(out) + len(chunk) > limit:
            raise failure
        out.extend(chunk)
    return bytes(out)


class TencentMap:
    def __init__(self, key: str = "", settings: dict | None = None, *, transport=None, http_client=None):
        settings = settings or {}
        self._key = key.strip()
        self._mcp = transport if transport is not None else TencentMcp(self._key, settings, http_client=http_client)
        self._disabled = bool(settings.get("disabled"))
        self._tools = MAP_TOOLS.intersection(settings.get("allowedTools", MAP_TOOLS))
        self._http = http_client
        self._own_http = http_client is None
        self._cache: AsyncCache = AsyncCache(200)
        self._limits: dict[str, tuple[float, MapError]] = {}

    @property
    def configured(self) -> bool:
        return not self._disabled and bool(self._mcp.configured)

    @property
    def static_configured(self) -> bool:
        return bool(self._key)

    async def close(self) -> None:
        await self._cache.close()
        if close := getattr(self._mcp, "close", None):
            await close()
        if self._own_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=9.5)
        return self._http

    async def _call(self, name: str, args: dict):
        if self._disabled:
            raise MapError(503, "腾讯地图 MCP 已在项目配置中关闭。", "MCP_CONFIG")
        if name not in self._tools:
            raise MapError(503, "此腾讯地图工具已在项目配置中关闭。", "MCP_TOOL_DISABLED")
        limit = self._limits.get(name)
        if limit and time.monotonic() < limit[0]:
            old = limit[1]
            raise MapError(old.status, str(old), old.code)
        try:
            return await self._mcp.call(name, args)
        except MapError as e:
            ttl = {"TENCENT_DAILY_LIMIT": 300, "TENCENT_RATE_LIMIT": 10}.get(e.code, 0)
            if ttl:
                self._limits[name] = (time.monotonic() + ttl, MapError(e.status, str(e), e.code))
            raise

    async def _search(self, city: str, keyword: str) -> list[dict]:
        async def run():
            try:
                raw = await self._call("placeSuggestion", {"keyword": keyword, "region": city})
            except MapError as e:
                if e.code == "TENCENT_NO_RESULT":
                    return []
                raise
            data = suggestion_text(raw) if isinstance(raw, str) else result_of(raw)
            if not isinstance(data, list):
                raise MapError(502, "腾讯地图 MCP 未返回可识别的地点列表。", "TENCENT_POIS")
            return unique([p for row in data[:40] if (p := poi_data(row, city))])
        return await self._cache.get(("keyword", city, keyword.lower()), 300, run)

    async def search(self, body: dict) -> dict:
        city, keyword = city_name(body.get("city")), field(body.get("keyword"), 100)
        if len(keyword.encode("utf-8")) > 160 or placeholder(keyword):
            raise MapError(400, "请填写一个具体的地点名或较短的关键词。")
        return {"places": (await self._search(city, keyword))[:10], "source": MAP_SOURCE}

    async def day(self, body: dict) -> dict:
        city, places = city_name(body.get("city")), body.get("locations")
        if not isinstance(places, list) or not 1 <= len(places) <= 8 or not all(isinstance(v, str) for v in places):
            raise MapError(400, "需要当天 1–8 个地点。")
        stops, warning = [], None
        for i, v in enumerate(places):
            name = field(v, 100)
            if warning:
                stops.append({"index": i, "status": "unavailable"})
                continue
            if placeholder(name) or len(name.encode("utf-8")) > 160:
                stops.append({"index": i, "status": "unresolved"})
                continue
            try:
                matches = [p for p in await self._search(city, name) if norm(p["name"]) == norm(name)]
            except MapError as e:
                if e.status != 429 or not any(s.get("poi") for s in stops):
                    raise
                warning = str(e)
                stops.append({"index": i, "status": "unavailable"})
                continue
            status = "matched" if len(matches) == 1 else "ambiguous" if matches else "unresolved"
            stops.append({"index": i, "status": status, **({"poi": matches[0]} if status == "matched" else {})})
        return {"stops": stops, "source": MAP_SOURCE, **({"warning": warning} if warning else {})}

    async def candidates(self, city: str) -> list[dict]:
        city = city_name(city)
        async def run():
            places, error = [], None
            for kind, keyword, limit in (("景点", "旅游景点", 12), ("餐馆", "餐厅", 10), ("酒店", "酒店", 8)):
                try:
                    places.extend({**p, "type": p["type"] or kind} for p in (await self._search(city, keyword))[:limit])
                except MapError as e:
                    error = error or e
                    if e.status == 429 or e.code in {"TENCENT_AUTH", "MCP_CONFIG", "MCP_TOOL_DISABLED"}:
                        break
            if not places and error:
                raise error
            return unique(places)
        return await self._cache.get(("candidates", city), 300, run)

    async def detail(self, body: dict) -> dict:
        ident = poi_id(body.get("id"))
        if not ident:
            raise MapError(400, "缺少有效的腾讯地图地点编号。")
        async def run():
            data = result_of(await self._call("placeDetail", {"id": ident}))
            rows = [*array(data), obj(data).get("poi"), data]
            raw = next((r for r in rows if obj(r).get("id") == ident), None)
            if raw is None:
                raise MapError(404, "腾讯地图未返回该地点的可核实详情。")
            return {"id": ident, "address": field(raw.get("address"), 150), "source": MAP_SOURCE,
                    "fetchedAt": utc_now(), **media(raw)}
        return await self._cache.get(("detail", ident), 900, run)

    async def geocode(self, city: str) -> dict:
        city = city_name(city)
        async def run():
            raw = await self._call("geocoder", {"address": city})
            area = obj(text_address(raw) if isinstance(raw, str) else result_of(raw))
            info = obj(area.get("ad_info"))
            code = field(info.get("adcode", area.get("adcode")), 12)
            if not re.fullmatch(r"[0-9]{6}", code):
                code = ""
            point = from_vendor(area.get("location"))
            if not point and not code:
                raise MapError(404, "腾讯地图未找到该城市的参考坐标。")
            return {"point": point, "city": field(info.get("city"), 50), **({"adcode": code} if code else {})}
        return await self._cache.get(("geocode", city), 1800, run)

    async def forecast(self, city: str) -> dict | None:
        city = city_name(city)
        async def run():
            anchor = {"adcode": city} if re.fullmatch(r"[0-9]{6}", city) else await self.geocode(city)
            loc = {"adcode": anchor["adcode"]} if anchor.get("adcode") else {"location": to_vendor(anchor["point"])}
            raw = await self._call("weather", {**loc, "type": "future"})
            data = obj(text_forecast(raw) if isinstance(raw, str) else result_of(raw))
            rows = array(data.get("forecast"))
            row = obj(rows[0]) if rows else {}
            days = []
            for info in array(row.get("infos"))[:7]:
                info = obj(info)
                daytime, nighttime = obj(info.get("day")), obj(info.get("night"))
                day = field(daytime.get("weather"), 25)
                if _iso_date(info.get("date")) and day:
                    record = {"date": info["date"], "day": day, "night": field(nighttime.get("weather"), 25)}
                    for key, period in (("dayTemp", daytime), ("nightTemp", nighttime)):
                        value = field(period.get("temperature"), 12)
                        if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{1,2})?", value) and -100 <= float(value) <= 80:
                            record[key] = float(value)
                    days.append(record)
            warnings = [title for a in array(row.get("alarms", data.get("alarms")))[:20]
                        if (title := field(obj(a).get("title"), 100))]
            return {"source": MAP_SOURCE, "days": days, "alerts": warnings[:4]} if days else None
        return await self._cache.get(("forecast", city), 900, run)

    async def weather(self, city: str) -> str | None:
        report = await self.forecast(city)
        if not report:
            return None
        return MAP_SOURCE + " 天气预报：" + "；".join(row["date"] + " " + row["day"] +
            (" · 夜间" + row["night"] if row["night"] else "") for row in report["days"])

    async def locate(self, body: dict) -> dict:
        gps = map_point(body.get("gps"))
        if not gps:
            raise MapError(400, "缺少有效的 GPS 坐标。")
        point = wgs_to_gcj(gps)
        raw = await self._call("reverseGeocoder", {"location": to_vendor(point)})
        data = obj(text_address(raw) if isinstance(raw, str) else result_of(raw))
        if not data:
            raise MapError(502, "已取得位置，但腾讯没有返回可识别的地址。请重试定位，或手动选择起点。", "MAP_ADDRESS_FORMAT")
        area = obj(data.get("ad_info", data.get("address_component")))
        parts = dict.fromkeys(field(area.get(k), 40) for k in ("province", "city", "district"))
        address = (field(obj(data.get("formatted_addresses")).get("recommend"), 150) or
                   field(data.get("address"), 150) or " · ".join(v for v in parts if v))
        return {"point": point, "address": address, "city": field(area.get("city"), 40),
                "source": "浏览器定位 · " + MAP_SOURCE}

    async def _geometry(self, mode: str, start: str, end: str) -> dict:
        error = MapError(502, "腾讯道路轨迹暂不可用。", "ROUTE_GEOMETRY")
        if not self._key:
            raise error
        try:
            async with asyncio.timeout(8.5):
                async with self._client().stream("GET", REST_BASE + "direction/v1/" + mode + "/",
                    params={"from": start, "to": end, "key": self._key}, follow_redirects=False, timeout=8.5) as res:
                    if not res.is_success:
                        raise error
                    data = obj(json_load(await _bounded(res, 2_000_000, error)))
                    routes = array(obj(data.get("result")).get("routes"))
                    if data.get("status") not in (0, "0") or not routes or not isinstance(routes[0], dict):
                        raise error
                    return routes[0]
        except (TimeoutError, httpx.HTTPError, ValueError):
            raise error from None

    async def route(self, body: dict) -> dict:
        origin, destination = map_point(body.get("origin")), map_point(body.get("destination"))
        mode = body.get("mode")
        if not origin or not destination or origin == destination or not isinstance(mode, str) or mode not in MODES:
            raise MapError(400, "需选择不同的起终点及有效的出行方式。")
        start, end = to_vendor(origin), to_vendor(destination)
        tool, rest_mode = MODES[mode]
        raw = await self._call(tool, {"from": start, "to": end})
        if isinstance(raw, str):
            route = text_route(raw)
            if route is None:
                if re.fullmatch(r"(?:无结果|未找到(?:合适的)?(?:路线|结果))[。！!]?", raw.strip()):
                    raise MapError(404, "腾讯地图未返回本段可用路线，可换一种出行方式。")
                raise MapError(502, "腾讯地图返回了小途尚不能识别的路线格式。", "MAP_ROUTE_FORMAT")
        else:
            data = result_of(raw)
            if not isinstance(data, dict):
                raise MapError(502, "腾讯地图返回了小途尚不能识别的路线格式。", "MAP_ROUTE_FORMAT")
            routes = data.get("routes")
            if routes == []:
                raise MapError(404, "腾讯地图未返回本段可用路线，可换一种出行方式。")
            if not isinstance(routes, list) or not isinstance(routes[0], dict):
                raise MapError(502, "腾讯地图返回了小途尚不能识别的路线格式。", "MAP_ROUTE_FORMAT")
            route = routes[0]
        polyline = decode_polyline(route.get("polyline"))
        paths = transit_paths(route) if mode == "transit" else []
        geometry_source = MAP_SOURCE if polyline or paths else None
        warning = None
        if body.get("geometry") is True and not geometry_source:
            try:
                extra = await self._geometry(rest_mode, start, end)
                polyline = decode_polyline(extra.get("polyline"))
                if not polyline and mode == "transit":
                    paths = transit_paths(extra)
                if polyline or paths:
                    geometry_source = "腾讯路线 API"
                else:
                    warning = "轨迹接口未返回道路坐标。"
            except MapError:
                warning = "道路轨迹补查失败，保留已查到的路线信息。"
        minutes = number(route.get("duration"), 1e6)
        taxi = route.get("taxi_fare")
        metrics = {
            "taxiEstimate": number(obj(taxi[0] if isinstance(taxi, list) and taxi else taxi).get("fare")),
            "toll": number(route.get("toll")), "fare": number(route.get("fare", route.get("price"))),
            "walkingDistance": number(route.get("walking_distance", route.get("walkingDistance"))),
        }
        return {"source": MAP_SOURCE, "distance": number(route.get("distance")),
                "duration": math.floor(minutes * 60 + .5) if minutes is not None else None,
                "polyline": polyline, "paths": paths, "steps": steps_of(route, mode == "transit"),
                **({"geometrySource": geometry_source} if geometry_source else {}),
                **({"geometryWarning": warning} if warning else {}),
                **{k: v for k, v in metrics.items() if v is not None}}

    async def snapshot(self, body: dict) -> bytes:
        def bad():
            return MapError(400, "静态地图需要有效的地点坐标与道路折线。", "STATIC_INPUT")
        def invalid():
            return MapError(502, "腾讯静态地图未返回可用图片。", "STATIC_RESPONSE")
        def points(value, min_count, max_count):
            if not isinstance(value, list) or not min_count <= len(value) <= max_count:
                raise bad()
            vals = [map_point(v) for v in value]
            if not all(vals):
                raise bad()
            return vals
        markers = points(body.get("markers"), 1, 9)
        poly = points(body.get("polyline") if body.get("polyline") is not None else [], 0, 100)
        raw_paths = body.get("paths") if body.get("paths") is not None else []
        if len(poly) == 1 or not isinstance(raw_paths, list) or len(raw_paths) > 20:
            raise bad()
        paths = ([poly] if poly else []) + [points(p, 2, 100) for p in raw_paths]
        combined = [point for path in paths for point in path]
        if len(combined) > 190:
            raise bad()
        if not self._key:
            raise MapError(503, "尚未配置腾讯地图 Key。", "STATIC_CONFIG")
        all_points = [*markers, *combined]
        params = [("size", "900*410"), ("format", "png"), ("maptype", "roadmap")]
        if all(p == all_points[0] for p in all_points):
            params.extend([("center", to_vendor(all_points[0])), ("zoom", "16")])
        else:
            xs, ys = zip(*(list(map(float, p.split(","))) for p in all_points))
            min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
            px, py = max(.001, (max_x - min_x) * .14), max(.001, (max_y - min_y) * .18)
            bounds = f"{max(-90, min_y-py):.6f},{max(-180, min_x-px):.6f};{min(90, max_y+py):.6f},{min(180, max_x+px):.6f}"
            params.append(("bounds", bounds))
        params.append(("key", self._key))
        params.extend(("markers", f"size:mid|color:blue|label:{i+1}|{to_vendor(p)}") for i, p in enumerate(markers))
        params.extend(("path", "color:0x04766B|weight:5|" + "|".join(map(to_vendor, path))) for path in paths)
        try:
            async with asyncio.timeout(9.5):
                async with self._client().stream("GET", REST_BASE + "staticmap/v2/", params=params,
                    follow_redirects=False, timeout=9.5) as res:
                    mime = res.headers.get("content-type", "").partition(";")[0].strip().lower()
                    if res.is_success and mime == "image/png":
                        data = await _bounded(res, 3_000_000, invalid())
                        if len(data) < 33 or not data.startswith(PNG_START) or data[12:16] != b"IHDR":
                            raise invalid()
                        return data
                    status = None
                    if mime in {"application/json", "text/plain"}:
                        try:
                            status = number(obj(json_load(await _bounded(res, 12_000, invalid()))).get("status"))
                        except MapError:
                            pass
                    if status == 121:
                        raise MapError(429, "腾讯静态地图日额度已用尽。", "STATIC_DAILY_LIMIT")
                    if status == 120 or res.status_code == 429:
                        raise MapError(429, "腾讯静态地图请求过于频繁，请稍后重试。", "STATIC_RATE_LIMIT")
                    if status in {110, 111, 112, 113, 190, 199, 301, 311} or res.status_code in {401, 403}:
                        raise MapError(502, "腾讯静态地图未获授权，请检查当前 Key 的静态图权限和来源限制。", "STATIC_AUTH")
                    raise invalid()
        except (TimeoutError, httpx.TimeoutException):
            raise MapError(502, "腾讯静态地图请求超时。", "STATIC_NETWORK") from None
        except (httpx.HTTPError, ValueError):
            raise MapError(502, "暂时无法连接腾讯静态地图。", "STATIC_NETWORK") from None
