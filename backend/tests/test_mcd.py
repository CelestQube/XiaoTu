from __future__ import annotations

import json

import httpx
import pytest

from xiaotu.execution import ReliableExecutor, ToolSendLost, ToolTransient, ToolUncertain
from xiaotu.tools.mcd import McdRefused, McdService, price_fen, yuan_fen
from xiaotu.tools.mcd_transport import MCD_TOOLS, McdError, McdMcp

FAST_RETRY = ReliableExecutor(attempt_timeout=0.2, initial_backoff=0, maximum_backoff=0)


async def quiet(*args):
    pass


# --- ReliableExecutor: request-loss resends are safe for non-retry-safe tools ---

async def test_send_lost_resends_without_retry_safe_and_succeeds():
    traces, sends = [], 0

    async def invoke():
        nonlocal sends
        sends += 1
        if sends == 1:
            raise ToolSendLost("connect_failed")
        return "receipt"

    assert await FAST_RETRY.call(invoke, budget=1, retry_safe=False, trace=quiet) == "receipt"
    assert sends == 2


async def test_send_lost_exhaustion_stays_definitive_not_unknown():
    sends = 0

    async def invoke():
        nonlocal sends
        sends += 1
        raise ToolSendLost("connect_failed")

    with pytest.raises(ToolSendLost):
        await FAST_RETRY.call(invoke, budget=1, retry_safe=False, trace=quiet)
    assert sends == 3


async def test_send_lost_then_ambiguous_becomes_uncertain():
    sends = 0

    async def invoke():
        nonlocal sends
        sends += 1
        if sends == 1:
            raise ToolSendLost("connect_failed")
        raise ToolTransient("response_lost")

    with pytest.raises(ToolUncertain):
        await FAST_RETRY.call(invoke, budget=1, retry_safe=False, trace=quiet)
    assert sends == 2


# --- Transport: a minimal McDonald's MCP server over httpx.MockTransport ---

class FakeMcd:
    def __init__(self, tools=("delivery-query-addresses", "query-meals", "create-order")):
        self.tools = list(tools)
        self.calls = []
        self.headers = []
        self.results = {}
        self.rpc_error = None
        self.http_error = None
        self.raw_body = None
        self.fail_first_call = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(dict(request.headers))
        body = json.loads(request.content)
        method, ident = body.get("method"), body.get("id")
        self.calls.append((method, body.get("params", {})))
        if method == "initialize":
            return self._reply(ident, {"result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}},
                               {"mcp-session-id": "sess-1"})
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return self._reply(ident, {"result": {"tools": [{"name": name} for name in self.tools]}})
        if method == "tools/call":
            name = body["params"]["name"]
            if self.fail_first_call:
                self.fail_first_call = False
                return httpx.Response(404)
            if self.http_error is not None:
                status, self.http_error = self.http_error, None
                return httpx.Response(status)
            if self.raw_body is not None:
                raw, self.raw_body = self.raw_body, None
                return httpx.Response(200, content=raw, headers={"content-type": "application/json"})
            if self.rpc_error is not None:
                code, message, self.rpc_error = *self.rpc_error, None
                return self._reply(ident, {"error": {"code": code, "message": message}})
            result = self.results[name]
            if isinstance(result, Exception):
                raise result
            return self._reply(ident, {"result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
        return httpx.Response(404)

    @staticmethod
    def _reply(ident, payload, headers=None):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": ident, **payload}, headers=headers or {})


def make_mcp(fake, token="token-1"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), trust_env=False)
    return McdMcp(token, {}, http_client=client), client


async def test_transport_handshake_bearer_and_business_envelope():
    fake = FakeMcd()
    fake.results["delivery-query-addresses"] = {"success": True, "data": {"list": [
        {"addressId": "301", "address": "上海市黄浦区", "addressDetail": "某酒店前台"}]}}
    mcp, client = make_mcp(fake)
    try:
        value = await mcp.call("delivery-query-addresses", {})
        assert value["success"] is True
        assert all(h.get("authorization") == "Bearer token-1" for h in fake.headers)
        assert [method for method, _ in fake.calls][:3] == [
            "initialize", "notifications/initialized", "tools/list"]
        assert [method for method, _ in fake.calls].count("tools/call") == 1
    finally:
        await mcp.close()
        await client.aclose()


async def test_transport_classifies_packet_loss_by_phase():
    fake = FakeMcd(tools=["create-order"])
    fake.results["create-order"] = httpx.ConnectError("no route")
    mcp, client = make_mcp(fake)
    try:
        with pytest.raises(ToolSendLost):
            await mcp.call("create-order", {"storeCode": "S1"})
        fake.results["create-order"] = httpx.ConnectTimeout("connect timed out")
        with pytest.raises(ToolSendLost):
            await mcp.call("create-order", {"storeCode": "S1"})
        fake.results["create-order"] = httpx.ReadTimeout("read timed out")
        with pytest.raises(ToolTransient):
            await mcp.call("create-order", {"storeCode": "S1"})
        fake.results["create-order"] = httpx.ReadError("connection reset")
        with pytest.raises(ToolTransient):
            await mcp.call("create-order", {"storeCode": "S1"})
    finally:
        await mcp.close()
        await client.aclose()


async def test_transport_maps_definitive_http_and_rpc_failures():
    fake = FakeMcd(tools=["create-order"])
    mcp, client = make_mcp(fake)
    try:
        fake.http_error = 500
        with pytest.raises(ToolTransient) as server_error:
            await mcp.call("create-order", {})
        assert server_error.value.code == "http_500"
        fake.http_error = 401
        with pytest.raises(McdError) as auth_error:
            await mcp.call("create-order", {})
        assert auth_error.value.code == "MCD_AUTH"
        fake.http_error = 429
        with pytest.raises(McdError) as rate_error:
            await mcp.call("create-order", {})
        assert rate_error.value.code == "MCD_RATE_LIMIT"
        fake.rpc_error = (-32602, "Invalid params")
        with pytest.raises(McdError) as rpc_error:
            await mcp.call("create-order", {})
        assert rpc_error.value.code == "MCD_RPC"
    finally:
        await mcp.close()
        await client.aclose()


async def test_transport_unusable_reply_and_session_recovery():
    fake = FakeMcd(tools=["create-order"])
    mcp, client = make_mcp(fake)
    try:
        fake.raw_body = b'{"foo": 1}'
        with pytest.raises(ToolUncertain):
            await mcp.call("create-order", {})
        fake.calls.clear()
        fake.results["create-order"] = {"success": True, "data": {"orderId": "ORD7"}}
        fake.fail_first_call = True
        value = await mcp.call("create-order", {})
        assert value["data"]["orderId"] == "ORD7"
        methods = [method for method, _ in fake.calls]
        assert methods == ["tools/call", "initialize", "notifications/initialized",
                           "tools/list", "tools/call"]
    finally:
        await mcp.close()
        await client.aclose()


async def test_transport_gates_configuration_and_tool_whitelist():
    offline = McdMcp("", {})
    with pytest.raises(McdError) as unconfigured:
        await offline.call("query-meals", {})
    assert unconfigured.value.code == "MCD_CONFIG"
    disabled = McdMcp("token", {"disabled": True})
    with pytest.raises(McdError) as off:
        await disabled.call("query-meals", {})
    assert off.value.code == "MCP_CONFIG"
    restricted = McdMcp("token", {"allowedTools": ["query-meals"]})
    assert restricted.configured
    with pytest.raises(McdError):
        await offline.call("not-a-real-tool", {})
    assert "create-order" in MCD_TOOLS


# --- Service: normalization, refusal semantics, and read-path error mapping ---

class FakeTransport:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    @property
    def configured(self):
        return True

    async def call(self, name, args):
        self.calls.append((name, args))
        result = self.results.get(name)
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self):
        pass


def test_money_units_are_normalized_to_fen():
    assert yuan_fen("22") == 2200
    assert yuan_fen("22.50") == 2250
    assert yuan_fen("22.5") == 2250
    assert yuan_fen("0") == 0
    assert yuan_fen(22) is None
    assert yuan_fen("-1") is None
    assert yuan_fen("1.234") is None
    assert price_fen(2200) == 2200
    assert price_fen("22") == 2200
    assert price_fen(None) is None
    assert price_fen(-5) is None


async def test_service_addresses_hide_contact_details_and_cache():
    transport = FakeTransport({"delivery-query-addresses": {"success": True, "data": {"list": [
        {"addressId": "301", "address": "上海市黄浦区", "addressDetail": "某酒店前台",
         "contactName": "张三", "phone": "13800000000"},
        {"addressId": "302", "address": "杭州市西湖区"}, "not-a-dict"]}}})
    service = McdService(transport=transport)
    try:
        rows = await service.addresses()
        assert rows == [{"addressId": "301", "address": "上海市黄浦区", "addressDetail": "某酒店前台"},
                        {"addressId": "302", "address": "杭州市西湖区", "addressDetail": ""}]
        assert "phone" not in rows[0] and "contactName" not in rows[0]
        assert len(await service.addresses()) == 2  # second call served from cache
        assert len(transport.calls) == 1
    finally:
        await service.close()


async def test_service_meals_normalize_prices_to_fen():
    transport = FakeTransport({"query-meals": {"success": True, "data": {
        "meals": {"M1": {"name": "巨无霸", "currentPrice": 2200, "originalPrice": 2500},
                  "M2": {"name": "麦辣鸡腿堡", "currentPrice": 1950},
                  "bad": {"name": "无价餐品"}},
        "categories": [{"name": "汉堡类", "meals": [{"code": "M1"}, {"code": "M2"}, {"code": "bad"}]},
                       {"name": "空类", "meals": []}],
        "frequent": ["M1", "unknown", "M2"]}}})
    service = McdService(transport=transport)
    try:
        menu = await service.meals("S1", "B1")
        assert menu["meals"]["M1"] == {"name": "巨无霸", "priceFen": 2200, "originalPriceFen": 2500}
        assert menu["meals"]["M2"] == {"name": "麦辣鸡腿堡", "priceFen": 1950}
        assert "bad" not in menu["meals"]
        assert menu["categories"] == [{"name": "汉堡类", "codes": ["M1", "M2"]}]
        assert menu["frequent"] == ["M1", "M2"]
        assert transport.calls[0][1] == {"storeCode": "S1", "orderType": 2, "beType": 2, "beCode": "B1"}
    finally:
        await service.close()


async def test_service_calculate_price_parses_fen_and_rejects_missing_amount():
    transport = FakeTransport({"calculate-price": {"success": True, "data": {
        "productPrice": 2200, "deliveryPrice": 900, "payPrice": 3100, "couponPrice": 100}}})
    service = McdService(transport=transport)
    try:
        quote = await service.calculate_price(store_code="S1", address_id="301", be_code="B1",
                                              items=[{"productCode": "M1", "quantity": 1}])
        assert quote == {"productFen": 2200, "payFen": 3100, "deliveryFen": 900, "discountFen": 100}
        transport.results["calculate-price"] = {"success": True, "data": {"productPrice": 2200}}
        with pytest.raises(McdError) as missing:
            await service.calculate_price(store_code="S1", address_id="301",
                                          items=[{"productCode": "M1", "quantity": 1}])
        assert missing.value.code == "MCD_RESPONSE"
    finally:
        await service.close()


async def test_service_business_refusal_and_item_validation():
    transport = FakeTransport({"create-order": {"success": False, "code": "STORE_CLOSED",
                                                "message": "门店休息中"}})
    service = McdService(transport=transport)
    try:
        with pytest.raises(McdRefused) as refused:
            await service.create_order(store_code="S1", address_id="301",
                                       items=[{"productCode": "M1", "quantity": 1}])
        assert refused.value.code == "STORE_CLOSED"
        with pytest.raises(McdError):
            await service.create_order(store_code="S1", address_id="301",
                                       items=[{"productCode": "M1", "quantity": 1},
                                              {"productCode": "M1", "quantity": 2}])
        with pytest.raises(McdError):
            await service.calculate_price(store_code="S1", address_id="301",
                                          items=[{"productCode": "M1", "quantity": 11}])
    finally:
        await service.close()


async def test_service_order_receipts_convert_yuan_strings():
    transport = FakeTransport({
        "order-list": {"success": True, "data": {"list": [
            {"orderId": "ORD1", "orderStatus": "待支付", "totalAmount": "22",
             "storeName": "麦当劳南京西路店", "createTime": "2026-11-20 12:00:00"},
            {"orderId": "", "totalAmount": "1"}]}},
        "create-order": {"success": True, "data": {
            "orderId": "ORD9", "payId": "P1", "payH5Url": "https://pay.example/h5",
            "orderDetail": {"orderStatus": "待支付", "storeName": "麦当劳南京西路店",
                            "totalAmount": "31", "realTotalAmount": "31", "productPrice": "22",
                            "deliveryPrice": "9",
                            "orderProductList": [{"productName": "巨无霸", "quantity": 1, "price": "22"}]}}}})
    service = McdService(transport=transport)
    try:
        orders = await service.order_list()
        assert orders == [{"orderId": "ORD1", "orderStatus": "待支付", "storeName": "麦当劳南京西路店",
                           "createTime": "2026-11-20 12:00:00", "totalFen": 2200}]
        receipt = await service.create_order(store_code="S1", address_id="301",
                                             items=[{"productCode": "M1", "quantity": 1}])
        assert receipt["orderId"] == "ORD9" and receipt["totalFen"] == 3100
        assert receipt["payUrl"] == "https://pay.example/h5"
        assert receipt["products"] == [{"name": "巨无霸", "quantity": 1, "priceFen": 2200}]
        transport.results["create-order"] = {"success": True, "data": {"orderStatus": "待支付"}}
        with pytest.raises(ToolUncertain):
            await service.create_order(store_code="S1", address_id="301",
                                       items=[{"productCode": "M1", "quantity": 1}])
    finally:
        await service.close()


async def test_service_reads_map_transport_loss_to_unavailable():
    transport = FakeTransport({"query-meals": ToolSendLost("connect_failed")})
    service = McdService(transport=transport)
    try:
        with pytest.raises(McdError) as offline:
            await service.meals("S1")
        assert offline.value.code == "MCD_NETWORK"
    finally:
        await service.close()
