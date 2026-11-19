from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from ._common import ToolError

MAP_SOURCE = "腾讯地图 MCP"
MAP_MCP_URL = "https://mcp.map.qq.com/mcp"
MAP_TOOLS = frozenset({"placeSuggestion", "placeDetail", "geocoder", "reverseGeocoder", "weather",
                       "directionWalking", "directionDriving", "directionBicycling", "directionTransit"})
PROTOCOL = "2025-03-26"
SUPPORTED_VERSIONS = {PROTOCOL, "2025-06-18", "2025-11-25"}
MAX_REPLY = 2 * 1024 * 1024


class _PrivateQueryLogging(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx logs the URL at INFO; Tencent requires keys (and sometimes GPS) in the query.
        args = record.args if isinstance(record.args, tuple) else ()
        return not any(isinstance(arg, httpx.URL) and arg.host in {"mcp.map.qq.com", "apis.map.qq.com"}
                       and bool(arg.query) for arg in args)


logging.getLogger("httpx").addFilter(_PrivateQueryLogging())


class MapError(ToolError):
    pass


def invalid() -> MapError:
    return MapError(502, "腾讯地图 MCP 返回了无法识别的数据。", "MCP_RESPONSE")


def vendor_error(value) -> MapError | None:
    raw = value.get("status", value.get("code")) if isinstance(value, dict) else value
    text = value.get("message", value.get("msg")) if isinstance(value, dict) else value
    code = int(str(raw)) if re.fullmatch(r"[0-9]{3}", str(raw)) else None
    text = text if isinstance(text, str) else ""
    if code == 121 or re.search(r"每日调用量|日(?:调用量|配额).{0,12}(?:超限|达到上限|已用尽)", text):
        return MapError(429, "腾讯地图日调用量已到上限，请检查控制台配额。", "TENCENT_DAILY_LIMIT")
    if code == 120 or re.search(r"每秒.{0,14}(?:超限|达到上限)|rate limit", text, re.I):
        return MapError(429, "腾讯地图请求过于频繁，请稍后重试。", "TENCENT_RATE_LIMIT")
    if code in {327, 328, 329, 335, 344, 347, 377, 378, 379, 384, 387, 393}:
        return MapError(404, "腾讯地图未返回符合条件的结果。", "TENCENT_NO_RESULT")
    if code == 326:
        return MapError(400, "起终点过近，请选择两个不同的地点。", "TENCENT_ARGUMENTS")
    messages = {
        110: "当前请求来源未获腾讯地图授权，请检查 Key 的来源限制。",
        111: "腾讯地图签名验证失败，请检查 Key 的授权配置。",
        112: "当前请求 IP 未获腾讯地图授权，请检查 Key 的 IP 限制。",
        113: "当前 Key 未获此地图工具需要的接口授权。",
        190: "腾讯地图 Key 无效，请核对 TENCENT_MAP_API_KEY。",
        199: "腾讯地图 Key 尚未启用 WebService API。",
        301: "腾讯地图 MCP 未收到 WebService Key。",
        311: "腾讯地图 WebService Key 格式不正确。",
    }
    if code in messages:
        return MapError(502, messages[code], "TENCENT_AUTH")
    if re.search(r"\b(?:invalid|missing|unauthorized)\s+(?:api\s+)?key\b|无效的?KEY|缺少.{0,12}Key", text, re.I):
        return MapError(502, "腾讯地图 WebService Key 缺失或无效，请检查配置。", "TENCENT_AUTH")
    return None


def json_load(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return None


def _reply(value, ident: int):
    rows = value if isinstance(value, list) else [value]
    return next((row for row in rows if isinstance(row, dict) and row.get("id") == ident
                 and row.get("jsonrpc") == "2.0" and ("result" in row or "error" in row)), None)


def _event(block: bytes, ident: int):
    data = b"\n".join(line[5:].lstrip(b" ") for line in block.splitlines() if line.startswith(b"data:"))
    return _reply(json_load(data), ident)


async def _envelope(res: httpx.Response, ident: int):
    mime = res.headers.get("content-type", "").partition(";")[0].strip().lower()
    if mime not in {"application/json", "text/event-stream"}:
        raise invalid()
    tail = b""
    size = 0
    async for chunk in res.aiter_bytes():
        size += len(chunk)
        if size > MAX_REPLY:
            raise invalid()
        tail += chunk
        if mime == "text/event-stream":
            blocks = re.split(rb"\r?\n\r?\n", tail)
            for block in blocks[:-1]:
                reply = _event(block, ident)
                if reply:
                    return reply
            tail = blocks[-1]
    if mime == "text/event-stream":
        return _event(tail, ident)
    parsed = json_load(tail)
    return _reply(parsed, ident) or parsed


class TencentMcp:
    """Compatibility transport: handles Tencent's observed batched SSE and sanitized HTTP errors."""

    def __init__(self, key: str, settings: dict | None = None, *, http_client: httpx.AsyncClient | None = None):
        settings = settings or {}
        self._key = key.strip()
        self._disabled = bool(settings.get("disabled"))
        self._tools = MAP_TOOLS.intersection(settings.get("allowedTools", MAP_TOOLS))
        self._http = http_client
        self._own_client = http_client is None
        self._seq = 0
        self._state: tuple[str, str | None, set[str]] | None = None
        self._handshake = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._key) and not self._disabled

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=8.5)
        return self._http

    async def close(self) -> None:
        if self._own_client and self._http is not None:
            await self._http.aclose()
            self._http = None
        self._state = None

    async def _request(self, method: str, params=None, state=None):
        self._seq += 1
        ident = self._seq
        notify = method == "notifications/initialized"
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if state:
            headers["MCP-Protocol-Version"] = state[0]
            if state[1]:
                headers["Mcp-Session-Id"] = state[1]
        body = {"jsonrpc": "2.0", "method": method}
        if not notify:
            body["id"] = ident
        if params is not None:
            body["params"] = params
        try:
            async with asyncio.timeout(8.5):
                async with self._client().stream(
                    "POST", MAP_MCP_URL, params={"key": self._key, "format": "1"}, json=body, headers=headers,
                    follow_redirects=False, timeout=8.5,
                ) as res:
                    status = res.status_code
                    if status == 404 and state and state[1]:
                        raise MapError(502, "腾讯地图 MCP 会话已失效。", "MCP_SESSION")
                    if not res.is_success:
                        if status in (401, 403):
                            raise vendor_error(190)
                        if status == 429:
                            raise vendor_error(120)
                        raise MapError(502, f"腾讯地图 MCP 请求失败（HTTP {status}）。", "MCP_HTTP")
                    if notify:
                        return {}, None
                    reply = await _envelope(res, ident)
                    if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0" or reply.get("id") != ident:
                        raise vendor_error(reply) or invalid()
                    if "error" in reply:
                        error = reply["error"]
                        raise (vendor_error(error.get("data")) if isinstance(error, dict) else None) or vendor_error(error) or (
                            MapError(502, "腾讯地图 MCP 拒绝了工具请求。", "MCP_RPC"))
                    result = reply.get("result")
                    if not isinstance(result, dict):
                        raise invalid()
                    session = res.headers.get("mcp-session-id", "")
                    return result, session if re.fullmatch(r"[\x21-\x7e]{1,512}", session) else None
        except (TimeoutError, httpx.TimeoutException):
            raise MapError(502, "腾讯地图 MCP 请求超时。", "MCP_NETWORK") from None
        except (httpx.HTTPError, ValueError):
            raise MapError(502, "暂时无法连接腾讯地图 MCP。", "MCP_NETWORK") from None

    async def _connection(self) -> tuple[str, str | None, set[str]]:
        async with self._handshake:
            if self._state is not None:
                return self._state
            value, session = await self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "xiaotu-tencent-map", "version": "0.1.0"}})
            version, caps = value.get("protocolVersion"), value.get("capabilities")
            if not isinstance(version, str) or version not in SUPPORTED_VERSIONS or not isinstance(caps, dict) or "tools" not in caps:
                raise invalid()
            names: set[str] = set()
            state = (version, session, names)
            await self._request("notifications/initialized", state=state)
            cursor = None
            for _ in range(4):
                data, _ = await self._request("tools/list", {"cursor": cursor} if cursor else {}, state)
                if not isinstance(tools := data.get("tools"), list):
                    raise invalid()
                names.update(t["name"] for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)
                             and t["name"] in self._tools)
                next_cursor = data.get("nextCursor")
                if not next_cursor:
                    self._state = state
                    return state
                if not isinstance(next_cursor, str) or len(next_cursor) > 512 or cursor == next_cursor:
                    break
                cursor = next_cursor
            raise invalid()

    async def _invoke(self, name: str, args: dict, state):
        if name not in state[2]:
            raise MapError(503, "当前腾讯地图 MCP 未提供所需工具。", "MCP_TOOL_MISSING")
        result, _ = await self._request("tools/call", {"name": name, "arguments": args}, state)
        content = result.get("content")
        values = []
        if isinstance(content, list):
            for row in content:
                if isinstance(row, dict) and row.get("type") == "text" and isinstance(text := row.get("text"), str):
                    text = re.sub(r"^```(?:json)?\s*([\s\S]*?)\s*```$", r"\1", text.strip(), flags=re.I)
                    parsed = json_load(text)
                    values.append(text if parsed is None else parsed)
        value = result.get("structuredContent")
        if value is None and values:
            value = values[0]
        if result.get("isError") or (isinstance(value, dict) and value.get("status") not in (0, "0", None)):
            fail = vendor_error(value) or next((err for v in values if (err := vendor_error(v))), None)
            raise fail or MapError(502, "腾讯地图 MCP 暂未返回可用结果。", "TENCENT_TOOL")
        if isinstance(value, str):
            if fail := vendor_error(value):
                raise fail
        elif not isinstance(value, (dict, list)):
            raise invalid()
        return value

    async def call(self, name: str, args: dict):
        if not self.configured:
            if self._disabled:
                raise MapError(503, "腾讯地图 MCP 已在项目配置中关闭。", "MCP_CONFIG")
            raise MapError(503, "尚未配置腾讯地图 MCP，请填写 TENCENT_MAP_API_KEY。", "MCP_CONFIG")
        if name not in MAP_TOOLS:
            raise invalid()
        if name not in self._tools:
            raise MapError(503, "此腾讯地图工具已在项目配置中关闭。", "MCP_TOOL_DISABLED")
        state = await self._connection()
        try:
            return await self._invoke(name, args, state)
        except MapError as error:
            if error.code != "MCP_SESSION":
                raise
            if self._state is state:
                self._state = None
            return await self._invoke(name, args, await self._connection())
