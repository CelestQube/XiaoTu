import asyncio
import json
import logging
import shutil

import httpx
import pytest

from xiaotu.tools.rail import RailError, RailMcp, RailService, unwrap_rail
from xiaotu.tools.tencent import MapError, TencentMap
from xiaotu.tools.tencent_transport import TencentMcp


class Transport:
    configured = True

    def __init__(self, handle):
        self.handle = handle
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        await asyncio.sleep(0)
        return self.handle(name, args)

    async def close(self):
        self.configured = False

    def status(self):
        return "ready"


@pytest.mark.asyncio
async def test_tencent_negotiates_batched_sse_and_reconnects_only_for_expired_session(caplog):
    methods = []

    def handler(req: httpx.Request):
        assert req.url.host == "mcp.map.qq.com"
        assert req.url.path == "/mcp"
        assert dict(req.url.params) == {"key": "test&format=0", "format": "1"}
        assert req.headers.get("Authorization") is None
        payload = json.loads(req.content)
        method = payload["method"]
        methods.append(method)
        def message(result):
            return {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        if method == "initialize":
            return httpx.Response(200, json=message({"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}),
                                  headers={"Mcp-Session-Id": "test-session"})
        assert req.headers["Mcp-Session-Id"] == "test-session"
        if method == "notifications/initialized":
            assert "id" not in payload
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(200, json=message({"tools": [{"name": "weather"}, {"name": "not-allowed"}]}))
        if methods.count("tools/call") == 1:
            return httpx.Response(404)
        event = message({"content": [{"type": "text", "text": '{"status":0,"result":{"weather":"晴"}}'}]})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text="event: message\r\ndata: " + json.dumps([{"jsonrpc": "2.0", "method": "notice"}, event]) + "\r\n\r\n")

    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mcp = TencentMcp("test&format=0", http_client=client)
        assert (await mcp.call("weather", {}))["result"]["weather"] == "晴"
        assert (await mcp.call("weather", {}))["status"] == 0
        with pytest.raises(MapError) as error:
            await mcp.call("not-allowed", {})
        assert error.value.code == "MCP_RESPONSE"
        assert methods.count("initialize") == 2
        assert methods.count("tools/call") == 3
    assert "mcp.map.qq.com" not in caplog.text
    assert "test-session" not in caplog.text


@pytest.mark.asyncio
async def test_weather_text_day_is_valid_and_place_lookup_exact_city_cache():
    places = [
        {"id": "poi456", "title": "雷峰塔", "location": {"lng": 120.14, "lat": 30.23}, "city": "杭州市", "type": 0},
        {"id": "wrong", "title": "雷峰塔", "location": {"lng": 120.14, "lat": 30.23}, "city": "宁波市", "type": 0},
    ]
    def handle(tool, args):
        if tool == "geocoder":
            return "市：杭州市\n行政区划代码：330100\n经度：120.1\n纬度：30.2"
        if tool == "weather":
            assert args["adcode"] == "330100"
            return "杭州未来 3 天天气预报\n2026-11-18（星期三）\n白天：晴，20°C\n夜晚：阴\n2026-11-31\n白天：中雨"
        if tool == "placeSuggestion":
            return {"status": 0, "data": places}
        if tool == "reverseGeocoder":
            assert args["location"].startswith("30.22")
            return {"result": {"address": "西湖", "ad_info": {"city": "杭州市"}}}
        pytest.fail(tool)
    transport = Transport(handle)
    maps = TencentMap(transport=transport)
    assert (await maps.forecast("杭州"))["days"] == [{"date": "2026-11-18", "day": "晴", "night": "阴", "dayTemp": 20}]
    assert (await maps.locate({"gps": "120.14,30.23"}))["address"] == "西湖"
    result = await maps.day({"city": "杭州", "locations": ["雷峰塔", "雷峰塔", "活动地点（待选）"]})
    assert [r["status"] for r in result["stops"]] == ["matched", "matched", "unresolved"]
    assert result["stops"][0]["poi"]["id"] == "poi456"
    assert [name for name, _ in transport.calls].count("placeSuggestion") == 1
    await maps.close()


@pytest.mark.asyncio
async def test_static_png_and_route_geometry_opt_in_only():
    calls = []
    png = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR" + b"\0" * 17

    def handler(req):
        calls.append(req)
        assert req.url.host == "apis.map.qq.com"
        if req.url.path == "/ws/direction/v1/walking/":
            assert req.url.params["from"] == "30.2,120.1"
            return httpx.Response(200, json={"status": 0, "result": {"routes": [{"polyline": [30.2, 120.1, 1000, 1000]}]}})
        assert req.url.path == "/ws/staticmap/v2/"
        assert req.url.params.get_list("markers") == ["size:mid|color:blue|label:1|30.2,120.1"]
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        maps = TencentMap("map-test", transport=Transport(
            lambda *args: "路线总距离：100米\n路线预估用时：2分钟"), http_client=client)
        params = {"origin": "120.1,30.2", "destination": "120.101,30.201", "mode": "walk"}
        out = await maps.route(params)
        assert out["duration"] == 120 and not out["polyline"] and not calls
        assert (await maps.route({**params, "geometry": True}))["polyline"] == [params["origin"], params["destination"]]
        assert len(calls) == 1
        assert await maps.snapshot({"markers": [params["origin"]]}) == png
        with pytest.raises(MapError) as error:
            await maps.snapshot({"markers": [params["origin"]], "paths": [["invalid"]]})
        assert error.value.code == "STATIC_INPUT"
        assert len(calls) == 2


TRAIN = {"start_train_code": "G39", "from_station": "北京南", "to_station": "杭州东", "start_time": "07:56",
         "arrive_time": "12:37", "lishi": "04:41",
         "prices": [{"seat_name": "二等座", "num": "候补", "price": 495}, {"seat_name": "无座", "num": "--", "price": 0}]}


@pytest.mark.asyncio
async def test_rail_selects_only_allowed_reads_and_preserves_seats_transfer_and_cache(tmp_path):
    second = {**TRAIN, "start_train_code": "D1", "from_station": "杭州西", "to_station": "黄山北", "start_time": "13:00"}
    def handle(tool, args):
        if tool == "get-stations-code-in-city":
            raise RailError(400, "没有识别到城市")
        if tool == "get-station-code-by-names":
            return {"杭州东": {"station_name": "杭州东", "station_code": "HGH"}}
        if tool == "get-train-route-stations":
            return [{"station_name": "北京南", "arrive_time": "----", "start_time": "07:56"}]
        if tool == "get-interline-tickets":
            return [{"wait_time": "35分钟", "lishi": "07:12", "ticketList": [TRAIN, second]}]
        assert tool == "get-tickets"
        assert args["fromStation"] == "北京" and args["earliestStartTime"] == 7
        return [TRAIN]
    tr = Transport(handle)
    rail = RailService(tmp_path, mcp=tr, today=lambda: "2026-11-17")
    q = {"origin": "北京市", "destination": "杭州东", "date": "2026-11-18", "departureTime": "07:30"}
    a, b = await asyncio.gather(rail.query(q), rail.query(q))
    assert a == b and len(tr.calls) == 1
    assert a["items"][0]["seats"] == [{"name": "二等座", "availability": "候补", "price": 495},
                                    {"name": "无座", "availability": "--"}]
    assert (await rail.query({**q, "departureTime": "07:57"}))["items"] == []
    assert (await rail.query({**q, "action": "transfer"}))["items"][0]["nextStation"] == "杭州西"
    assert (await rail.query({"action": "stations", "keyword": "杭州东"}))["items"][0]["code"] == "HGH"
    assert "arrive" not in (await rail.query({**q, "action": "stops", "trainCode": "g39"}))["items"][0]
    with pytest.raises(RailError) as error:
        await rail.query({**q, "date": "2026-12-08"})
    assert error.value.status == 400


def test_rail_distinguishes_no_results_from_unavailable_and_hides_payload():
    def wrap(text):
        return {"content": [{"type": "text", "text": text}]}
    assert unwrap_rail(wrap("没有查询到相关车次信息")) == []
    with pytest.raises(RailError) as error:
        unwrap_rail(wrap("secret actual upstream payload"))
    assert error.value.status == 503 and "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_python_mcp_client_uses_stdio_persistent_allowed_subprocess(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    if not shutil.which("node"):
        pytest.skip("Node runtime required for stdio subprocess regression")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "NOT-PASSED-TO-NODE")
    entry = tmp_path / "node_modules" / "12306-mcp" / "build" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        """const rl = require('node:readline').createInterface({ input: process.stdin });
let requests = 0;
rl.on('line', line => {
  const q = JSON.parse(line);
  if (q.id === undefined) return;
  const result = q.method === 'initialize'
    ? {protocolVersion: q.params.protocolVersion, serverInfo: {name: 'fake-rail', version: '1'}, capabilities: {tools: {}}}
    : q.method === 'tools/list'
      ? {tools: [{name: 'get-tickets', inputSchema: {type: 'object'}}]}
      : {content: [{type: 'text', text: JSON.stringify({requests: ++requests, secret: !!process.env.DASHSCOPE_API_KEY})}]};
  process.stdout.write(JSON.stringify({jsonrpc: '2.0', id: q.id, result}) + '\\n');
});
""", encoding="utf-8")
    rail = RailMcp(tmp_path, {"allowedTools": ["get-tickets"]})
    try:
        with pytest.raises(RailError) as error:
            await rail.call("get-interline-tickets", {})
        assert error.value.status == 503
        assert await rail.call("get-tickets", {}) == {"requests": 1, "secret": False}
        assert await rail.call("get-tickets", {}) == {"requests": 2, "secret": False}
        assert rail.status() == "ready"
    finally:
        await rail.close()
    assert not rail.configured
