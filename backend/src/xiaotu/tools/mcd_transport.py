from __future__ import annotations

import asyncio
import json
import re

import httpx

from xiaotu.execution import ToolSendLost, ToolTransient, ToolUncertain

from ._common import ToolError, clean

MCD_SOURCE = "麦当劳 MCP"
MCD_MCP_URL = "https://mcp.mcd.cn"
MCD_TOOLS = frozenset({
    "delivery-query-addresses", "delivery-query-stores", "query-meals", "query-meal-detail",
    "query-store-coupons", "calculate-price", "query-order", "order-list", "create-order",
})
PROTOCOL = "2025-03-26"
SUPPORTED_VERSIONS = {PROTOCOL, "2025-06-18", "2025-11-25"}
MAX_REPLY = 2 * 1024 * 1024
SESSION = re.compile(r"[\x21-\x7e]{1,512}")


class McdError(ToolError):
    pass


def invalid() -> McdError:
    return McdError(502, "麦当劳 MCP 返回了无法识别的数据。", "MCD_RESPONSE")


def unavailable() -> McdError:
    return McdError(503, "暂时无法连接麦当劳 MCP。", "MCD_NETWORK")


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
        raise ToolUncertain("invalid_content_type")
    tail = b""
    size = 0
    async for chunk in res.aiter_bytes():
        size += len(chunk)
        if size > MAX_REPLY:
            raise ToolUncertain("oversized_reply")
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


class McdMcp:
    """Streamable-HTTP client for the official McDonald's MCP server.

    Transport failures are classified by phase so the reliable gateway can
    tell "the request never reached the server" (ToolSendLost, resending the
    same logical request is safe) apart from "the response was lost or
    unusable after the server may have committed" (ToolTransient or
    ToolUncertain, never resend a write). Definitive refusals before any
    execution surface as McdError for callers to map onto ToolRejected.
    """

    def __init__(self, token: str, settings: dict | None = None, *, http_client: httpx.AsyncClient | None = None):
        settings = settings or {}
        self._token = token.strip()
        self._disabled = bool(settings.get("disabled"))
        self._tools = MCD_TOOLS.intersection(settings.get("allowedTools", MCD_TOOLS))
        self._http = http_client
        self._own_client = http_client is None
        self._seq = 0
        self._state: tuple[str, str | None, set[str]] | None = None
        self._handshake = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._token) and not self._disabled

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
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "Authorization": "Bearer " + self._token}
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
                    "POST", MCD_MCP_URL, json=body, headers=headers, follow_redirects=False, timeout=8.5,
                ) as res:
                    status = res.status_code
                    if status == 404 and state and state[1]:
                        # The server rejected the unknown session before executing: no side effect.
                        raise McdError(502, "麦当劳 MCP 会话已失效。", "MCP_SESSION")
                    if status in (401, 403):
                        raise McdError(502, "麦当劳 MCP Token 无效或未授权，请检查 MCD_MCP_TOKEN。", "MCD_AUTH")
                    if status == 429:
                        # Rejected by the rate limiter before execution: no side effect.
                        raise McdError(429, "麦当劳 MCP 请求过于频繁，请稍后重试。", "MCD_RATE_LIMIT")
                    if status >= 500:
                        # The request may have reached the business layer; stay ambiguous.
                        raise ToolTransient("http_" + str(status))
                    if not res.is_success:
                        raise McdError(502, f"麦当劳 MCP 请求失败（HTTP {status}）。", "MCP_HTTP")
                    if notify:
                        return {}, None
                    reply = await _envelope(res, ident)
                    if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0" or reply.get("id") != ident:
                        # The request was processed but the reply is unusable: unknown outcome.
                        raise ToolUncertain("invalid_reply")
                    if "error" in reply:
                        error = reply["error"]
                        text = error.get("message") if isinstance(error, dict) else ""
                        code = error.get("code") if isinstance(error, dict) else None
                        raise McdError(502, f"麦当劳 MCP 拒绝了请求：{clean(code, 60) or clean(text, 120) or '协议错误'}。",
                                       "MCD_RPC")
                    result = reply.get("result")
                    if not isinstance(result, dict):
                        raise ToolUncertain("invalid_result")
                    session = res.headers.get("mcp-session-id", "")
                    return result, session if SESSION.fullmatch(session) else None
        except (McdError, ToolSendLost, ToolTransient, ToolUncertain):
            raise
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            # The connection was never established, so this request cannot have
            # been delivered; resending the same logical request is safe.
            raise ToolSendLost("connect_failed") from None
        except TimeoutError:
            # The overall budget expired while waiting: delivery state unknown.
            raise ToolTransient("timeout") from None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError, httpx.HTTPError, OSError):
            # Read/write-phase failure after the request left: the server may
            # have committed, so a resend could duplicate a write.
            raise ToolTransient("response_lost") from None

    async def _connection(self) -> tuple[str, str | None, set[str]]:
        async with self._handshake:
            if self._state is not None:
                return self._state
            value, session = await self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "xiaotu-mcd", "version": "0.1.0"}})
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
            raise McdError(503, "当前麦当劳 MCP 未提供所需工具。", "MCD_TOOL_MISSING")
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
        if result.get("isError"):
            # The tool executed and reported an error. Only a recognizable
            # business envelope settles the outcome; anything else is unknown.
            if isinstance(value, dict) and isinstance(value.get("success"), bool):
                return value
            raise ToolUncertain("tool_error")
        if not isinstance(value, (dict, list)):
            raise ToolUncertain("invalid_content")
        return value

    async def call(self, name: str, args: dict):
        if not self.configured:
            if self._disabled:
                raise McdError(503, "麦当劳 MCP 已在项目配置中关闭。", "MCP_CONFIG")
            raise McdError(503, "尚未配置麦当劳 MCP，请填写 MCD_MCP_TOKEN。", "MCD_CONFIG")
        if name not in MCD_TOOLS:
            raise invalid()
        if name not in self._tools:
            raise McdError(503, "此麦当劳工具已在项目配置中关闭。", "MCD_TOOL_DISABLED")
        state = await self._connection()
        try:
            return await self._invoke(name, args, state)
        except McdError as error:
            if error.code != "MCP_SESSION":
                raise
            # The dropped session rejected the call before execution, so one
            # replay over a fresh handshake cannot duplicate a write.
            if self._state is state:
                self._state = None
            return await self._invoke(name, args, await self._connection())
