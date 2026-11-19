from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ._common import AsyncCache, ToolError, clean, utc_now

RAIL_SOURCE = "铁路 12306 · 第三方 MCP"
RAIL_TOOLS = frozenset({"get-tickets", "get-interline-tickets", "get-train-route-stations",
                        "get-stations-code-in-city", "get-station-code-by-names", "get-station-by-telecode"})
TRAIN_RE = re.compile(r"(?:[A-Z]{1,2}[0-9]{1,5}|[0-9]{1,5})\Z")
TIME_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
DURATION_RE = re.compile(r"[0-9]{1,3}:[0-5][0-9]\Z")
STATION_RE = re.compile(r"[A-Z]{3}\Z")


class RailError(ToolError):
    pass


def unavailable() -> RailError:
    return RailError(503, "铁路查询暂不可用，请稍后重试或到铁路 12306 核对。")


def invalid() -> RailError:
    return RailError(502, "铁路服务未返回可识别的数据，请到铁路 12306 核对。")


def china_today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def unwrap_rail(result: Any) -> list | dict:
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if not isinstance(result, dict) or result.get("isError"):
        raise unavailable()
    content = result.get("content")
    if not isinstance(content, list):
        raise unavailable()
    text = next((row.get("text") for row in content if isinstance(row, dict) and row.get("type") == "text"), None)
    if not isinstance(text, str) or len(text) > 750_000:
        raise unavailable()
    text = text.strip()
    if re.fullmatch(r"(?:没有|未)查询到相关车次信息[。！]?", text):
        return []
    if re.match(r"Error: Station not found\.", text, re.I):
        raise RailError(400, "没有识别到车站，请填写准确站名或三字码。")
    if re.match(r"Error: City not found\.", text, re.I):
        raise RailError(400, "没有识别到城市，可以改用具体车站名。")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise unavailable() from None
    if not isinstance(data, (dict, list)):
        raise unavailable()
    return data


class RailMcp:
    """Own the MCP TaskGroup in one supervisor task, not an arbitrary API request."""

    def __init__(self, root: Path, settings: dict | None = None, *, enabled: bool = True):
        settings = settings or {}
        self._entry = Path(root) / "node_modules" / "12306-mcp" / "build" / "index.js"
        self._node = shutil.which("node") if enabled and not settings.get("disabled") else None
        self._allowed = RAIL_TOOLS.intersection(settings.get("allowedTools", RAIL_TOOLS))
        self._runner: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._closed = False
        self._retry_at = 0.0
        self._last = "unchecked"
        self._active = 0

    @property
    def configured(self) -> bool:
        return bool(self._node and self._entry.is_file() and not self._closed)

    def status(self) -> str:
        return self._last

    async def _serve(self, ready: asyncio.Future) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            # The SDK itself allows only safe OS defaults; never pass project/Qwen credentials.
            config = StdioServerParameters(command=self._node, args=[str(self._entry.resolve())],
                                           cwd=str(self._entry.parent.resolve()),
                                           env={"TZ": "Asia/Shanghai", "NODE_TLS_REJECT_UNAUTHORIZED": "1"})
            with open(os.devnull, "w", encoding="utf-8") as ignored:
                async with stdio_client(config, errlog=ignored) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        if not ready.done():
                            ready.set_result(session)
                        await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._last = "unavailable"
            self._retry_at = time.monotonic() + 20
            if not ready.done():
                ready.set_exception(unavailable())
        finally:
            if not ready.done():
                ready.cancel()

    async def _connect(self):
        if not self.configured:
            raise RailError(503, "未启用 12306 MCP，请检查项目开关与 Node 依赖。")
        if time.monotonic() < self._retry_at:
            raise unavailable()
        if self._runner is None or self._runner.done():
            self._ready = asyncio.get_running_loop().create_future()
            # Consume a late initialization exception even if every waiting API task disconnected.
            self._ready.add_done_callback(lambda f: None if f.cancelled() else f.exception())
            self._runner = asyncio.create_task(self._serve(self._ready))
        runner, ready = self._runner, self._ready
        try:
            return await asyncio.wait_for(asyncio.shield(ready), 18)
        except TimeoutError:
            await self._reset(runner, delay=20)
            raise unavailable() from None

    async def _reset(self, runner: asyncio.Task | None, *, delay: int = 0) -> None:
        if runner is self._runner:
            self._runner = None
            self._ready = None
            if delay:
                self._last = "unavailable"
                self._retry_at = time.monotonic() + delay
            if runner:
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        await self._reset(self._runner)

    async def call(self, name: str, args: dict) -> list | dict:
        if name not in RAIL_TOOLS:
            raise RailError(400, "不支持此铁路查询。")
        if name not in self._allowed:
            raise RailError(503, "此铁路工具已在项目配置中关闭。")
        if self._active >= 2:
            raise RailError(429, "铁路查询正在处理，请稍后重试。")
        self._active += 1
        try:
            session = await self._connect()
            runner = self._runner
            try:
                result = await asyncio.wait_for(session.call_tool(name, arguments=args), 15)
            except Exception:
                await self._reset(runner, delay=20)
                raise unavailable() from None
            try:
                value = unwrap_rail(result)
            except RailError as error:
                if error.status == 503:
                    self._last = "unavailable"
                    self._retry_at = time.monotonic() + 10
                raise
            self._last = "ready"
            return value
        finally:
            self._active -= 1


def _matches(pattern: re.Pattern, value: Any) -> bool:
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _station(value: Any) -> str:
    name = clean(unicodedata.normalize("NFKC", value) if isinstance(value, str) else value, 32)
    if re.fullmatch(r"[A-Za-z]{3}", name):
        return name.upper()
    if not name or len(name) > 30 or not all(c in "· " or "CJK UNIFIED IDEOGRAPH" in unicodedata.name(c, "")
                                             or "CJK COMPATIBILITY IDEOGRAPH" in unicodedata.name(c, "") for c in name):
        raise RailError(400, "请填写中文城市、具体车站名或三字码。")
    return name.removesuffix("市")


def _train(value: Any) -> str:
    value = clean(value, 12).upper()
    if not _matches(TRAIN_RE, value):
        raise RailError(400, "请提供具体车次，例如 G39。")
    return value


def _as_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise RailError(400, "请填写出发日期（YYYY-MM-DD）。")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise RailError(400, "请填写出发日期（YYYY-MM-DD）。") from None


def _limit(value: Any) -> int:
    if isinstance(value, (int, str)) and re.fullmatch(r"[0-9]{1,2}", str(value)) and 1 <= int(value) <= 12:
        return int(value)
    return 8


def _normalize_trip(row: Any) -> dict | None:
    if not isinstance(row, dict) or not _matches(TRAIN_RE, row.get("start_train_code")):
        return None
    start, end = clean(row.get("from_station"), 48), clean(row.get("to_station"), 48)
    depart, arrive = row.get("start_time"), row.get("arrive_time")
    if not start or not end or not _matches(TIME_RE, depart) or not _matches(TIME_RE, arrive):
        return None
    seats = []
    for seat in row.get("prices", [])[:18] if isinstance(row.get("prices"), list) else []:
        if not isinstance(seat, dict):
            continue
        name = clean(seat.get("seat_name"), 48)
        avail = str(seat.get("num", ""))
        p = seat.get("price")
        if not name or re.fullmatch(r"[\s-]+", name):
            continue
        item = {"name": name}
        if re.fullmatch(r"(?:[0-9]{1,4}|有|无|候补|充足|暂售|停售|--)", avail):
            item["availability"] = avail
        if (isinstance(p, (float, int)) and not isinstance(p, bool)) or (
                isinstance(p, str) and re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", p)):
            if 0 < float(p) <= 20000:
                item["price"] = float(p)
        if len(item) > 1:
            seats.append(item)
    return {"code": row["start_train_code"], "from": start, "to": end, "depart": depart, "arrive": arrive,
            **({"duration": row["lishi"]} if _matches(DURATION_RE, row.get("lishi")) else {}), "seats": seats}


def _normalize_transfer(row: Any) -> dict | None:
    if not isinstance(row, dict) or not isinstance(row.get("ticketList"), list) or len(row["ticketList"]) != 2:
        return None
    segments = [_normalize_trip(leg) for leg in row["ticketList"]]
    if not all(segments):
        return None
    wait = row.get("wait_time")
    result = {"segments": segments, "middle": segments[0]["to"]}
    if segments[0]["to"] != segments[1]["from"]:
        result["nextStation"] = segments[1]["from"]
    if _matches(DURATION_RE, row.get("lishi")):
        result["duration"] = row["lishi"]
    if isinstance(wait, str) and (_matches(DURATION_RE, wait) or
                                  re.fullmatch(r"(?:[0-9]{1,3}分钟|[0-9]{1,2}小时(?:[0-9]{1,2}分钟)?)", wait)):
        result["wait"] = wait
    return result


def _normalize_station(row: Any) -> dict | None:
    if isinstance(row, dict) and _matches(STATION_RE, row.get("station_code")) and (name := clean(row.get("station_name"), 48)):
        return {"name": name, "code": row["station_code"], "city": clean(row.get("city"), 48),
                "pinyin": clean(row.get("station_pinyin"), 48)}
    return None


def _normalize_stop(row: Any) -> dict | None:
    if not isinstance(row, dict) or not (name := clean(row.get("station_name"), 48)):
        return None
    result = {"name": name, "day": clean(row.get("arrive_day_str"), 48)}
    if _matches(TIME_RE, row.get("start_time")):
        result["depart"] = row["start_time"]
    if _matches(TIME_RE, row.get("arrive_time")):
        result["arrive"] = row["arrive_time"]
    return result


def _rows(data: Any, parser, n: int) -> list[dict]:
    if not isinstance(data, list):
        raise invalid()
    parsed = [row for raw in data if (row := parser(raw)) is not None]
    if data and not parsed:
        raise invalid()
    return parsed[:n]


class RailService:
    def __init__(self, root: Path, settings: dict | None = None, *, enabled=True, mcp=None, today=None):
        self._mcp = mcp if mcp is not None else RailMcp(root, settings, enabled=enabled)
        self._today = today or china_today
        self._cache: AsyncCache[dict] = AsyncCache(70)

    @property
    def configured(self) -> bool:
        return self._mcp.configured

    def status(self) -> str:
        return self._mcp.status()

    async def close(self) -> None:
        await self._cache.close()
        await self._mcp.close()

    async def query(self, body: dict) -> dict:
        action = body.get("action", "tickets")
        if action not in ("tickets", "transfer", "stops", "stations"):
            raise RailError(400, "请选择车票、换乘、经停或车站查询。")
        if not self.configured:
            raise RailError(503, "未启用 12306 MCP，可前往铁路 12306 查询。")
        detail: dict[str, Any] = {}
        fallback = False
        after, train, limit = "00:00", None, 8
        if action == "stations":
            station = _station(body.get("keyword"))
            detail["keyword"] = station
            if _matches(STATION_RE, station):
                name, args = "get-station-by-telecode", {"stationTelecode": station}
            elif body.get("exact") is True or station.endswith("站"):
                name, args = "get-station-code-by-names", {"stationNames": station}
            else:
                name, args = "get-stations-code-in-city", {"city": station}
                fallback = True
        else:
            departure_date, today = _as_date(body.get("date")), _as_date(self._today())
            if departure_date < today:
                raise RailError(400, "出发日期不能早于今天。")
            day = departure_date.isoformat()
            detail["date"] = day
            if action == "stops":
                train = _train(body.get("trainCode"))
                detail["code"] = train
                name, args = "get-train-route-stations", {"trainCode": train, "departDate": day, "format": "json"}
            else:
                if (departure_date - today).days >= 15:
                    raise RailError(400, "日期超出小途查询的常规 15 天预售范围（含今天）。临时延长以铁路 12306 公告为准。")
                start, end = _station(body.get("origin")), _station(body.get("destination"))
                if start.removesuffix("站") == end.removesuffix("站"):
                    raise RailError(400, "出发站与到达站不能相同。")
                after = body.get("departureTime") or "00:00"
                if not _matches(TIME_RE, after):
                    raise RailError(400, "最早出发时间须为 HH:mm。")
                limit = _limit(body.get("limit"))
                train = _train(body.get("trainCode")) if body.get("trainCode") else None
                name = "get-interline-tickets" if action == "transfer" else "get-tickets"
                args = {"date": day, "fromStation": start, "toStation": end, "format": "json",
                        "sortFlag": "startTime", "limitedNum": 0 if train or after != "00:00" else limit}
                if (hour := int(after[:2])):
                    args["earliestStartTime"] = hour
                train_filter = body.get("filter")
                if isinstance(train_filter, str) and re.fullmatch(r"[GDZTKOFS]{1,8}", train_filter):
                    args["trainFilterFlags"] = train_filter
                if action == "transfer":
                    args["limitedNum"] = min(limit, 10) if after == "00:00" else 10
                    if body.get("middle"):
                        args["middleStation"] = _station(body.get("middle"))
                detail.update(origin=start, destination=end)

        async def run():
            used = name
            try:
                data = await self._mcp.call(used, args)
            except RailError as error:
                if not fallback or error.status != 400:
                    raise
                used = "get-station-code-by-names"
                data = await self._mcp.call(used, {"stationNames": detail["keyword"]})
            if action == "stops":
                items = _rows(data, _normalize_stop, 80)
            elif action == "stations":
                if used == "get-station-by-telecode":
                    items = _rows([data], _normalize_station, 1)
                elif used == "get-station-code-by-names":
                    if not isinstance(data, dict):
                        raise invalid()
                    items = _rows(list(data.values()), _normalize_station, 12)
                else:
                    items = _rows(data, _normalize_station, 25)
            elif action == "transfer":
                items = [r for r in _rows(data, _normalize_transfer, 10)
                         if r["segments"][0]["depart"] >= after][:limit]
            else:
                matching = [r for r in _rows(data, _normalize_trip, 500)
                            if r["depart"] >= after and (not train or r["code"] == train)]
                items = sorted(matching, key=lambda r: r["depart"])[:limit]
            return {"action": action, **detail, "source": RAIL_SOURCE, "items": items, "checkedAt": utc_now()}

        key = (name, tuple(args.items()), after, train, limit)
        return await self._cache.get(key, 1800 if action == "stations" else 45, run)
