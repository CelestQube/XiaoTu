from datetime import datetime
import json
import shutil

from fastapi.testclient import TestClient
import httpx
import pytest

from xiaotu.agent.model import ModelError, Qwen
from xiaotu.app import create_app
from xiaotu.config import ROOT, Settings, capabilities, load_settings
from xiaotu.domain import CHINA
from xiaotu.tools.rail import RailError


class Maps:
    configured = True
    static_configured = True

    def __init__(self):
        self.calls = []

    async def close(self):
        pass

    async def forecast(self, city):
        self.calls.append("forecast")
        today = datetime.now(CHINA).date().isoformat()
        return {"days": [{"date": today, "day": "晴", "night": "多云"}]}

    async def candidates(self, city):
        self.calls.append("candidates")
        return [{"id":"A1","name":"西湖","city":city+"市","point":"120.15,30.25","type":"景点","address":"西湖区"}]

    async def day(self, data):
        return {"stops":[{"index":0, "status":"matched", "poi":(await self.candidates(data["city"]))[0]}], "source":"腾讯地图 MCP"}

    async def search(self, data):
        return {"places": await self.candidates(data["city"]), "source":"腾讯地图 MCP"}

    async def snapshot(self, data):
        return b"\x89PNG\r\n\x1a\n"


class Rail:
    configured = True

    def __init__(self):
        self.calls = []

    def status(self):
        return "unchecked"

    async def close(self):
        pass

    async def query(self, args):
        self.calls.append(args)
        return {**args,"source":"铁路 12306","items":[],"checkedAt":"2026-11-18T07:00:00Z"}


def browser(app, **kwargs):
    return TestClient(app, base_url="http://127.0.0.1:3212", **kwargs)


def test_local_boundary_and_demo_plan_and_binary_calendar():
    with browser(create_app(Settings(rail_enabled=False))) as client:
        home = client.get("/")
        assert home.status_code == 200 and "小途" in home.text
        assert client.get("/.env").status_code == 404
        assert client.get("/api/status", headers={"host":"test.invalid"}).status_code == 403
        assert client.get("/api/status").json()["backend"] == "python"
        assert client.post("/api/food/menu", json={}).status_code == 404
        assert client.post("/api/chat", content="invalid").status_code == 415
        assert client.post("/api/chat", json={"message":"x"*100_000}).status_code == 413
        assert client.post("/api/chat", json={"message":"杭州"}, headers={"origin":"https://example.invalid"}).status_code == 403
        missing = client.post("/api/chat", json={"message":"去杭州"}).json()
        assert missing["trip"]["city"] == "杭州" and "plan" not in missing
        data = client.post("/api/chat", json={"message":"从上海出发去杭州玩3天，两人，总预算1800元，2026-12-11出发"}).json()
        assert data["intent"] == "plan" and len(data["plan"]["days"]) == 3
        assert data["plan"]["travel"]["rail"]["status"] == "unavailable"
        assert [p["low"] for p in data["plan"]["budget"]] == [90,320,390,0]
        body = {"plan":data["plan"],"startDate":"2026-12-11","confirmed":True}
        cal = client.post("/api/calendar", json=body)
        assert cal.headers["content-type"].startswith("text/calendar")
        assert b"DTSTART:20261211T010000Z" in cal.content
        assert all(len(line) <= 75 for line in cal.content.split(b"\r\n"))
        assert client.post("/api/calendar", json={**body, "confirmed":False}).status_code == 400


def test_qwen_model_graph_weather_follows_explicit_skills_and_signed_place_plan():
    maps = Maps()
    calls = []

    async def fake_model(system, payload, **kwargs):
        calls.append((system, payload))
        if "天气怎么" in payload["message"]:
            return {"intent":"lookup", "query":{"kind":"weather","city":"杭州"}}
        if "只返回 JSON {title" in system:
            assert payload["weather"]["days"] and "PLAN_SKILL" in system
            p = payload["places"][0]
            return {"days":[{"day":1,"items":[{"time":"10:30","title":"游览","location":p["name"],"placeRef":p["ref"]}]}]}
        return {"intent":"plan","trip":{"city":"杭州","days":1,"people":2,"startDate":"2026-11-18"}}

    with browser(create_app(Settings(tencent_key="fixture",skills={"plan":"PLAN_SKILL"}), maps=maps, model=fake_model)) as client:
        data = client.post("/api/chat", json={"message":"杭州天气怎么样？"}).json()
        assert data["intent"] == "lookup" and data["cards"][0]["days"][0]["night"] == "多云" and "PLAN_SKILL" not in calls[0][0]
        response = client.post("/api/chat", json={"message":"去杭州玩1天，两人，日期没定"})
        assert response.status_code == 200
        data = response.json()
        assert "startDate" not in data["trip"] and data["plan"]["travel"]["dateDefault"]
        item = data["plan"]["days"][0]["items"][0]
        assert item["mapStatus"] == "verified" and item["poi"]["proof"]
        signed = client.post("/api/maps/search", json={"city":"杭州","keyword":"西湖"}).json()["places"][0]
        assert signed["proof"] == item["poi"]["proof"]
        invalid = {**signed, "point":"120.16,30.25"}
        base = {"trip":data["trip"],"plan":data["plan"],"day":0,"index":0}
        assert client.post("/api/plan/place-preview", json={**base,"choice":{"poi":invalid}}).status_code == 400
        preview = client.post("/api/plan/place-preview", json={**base,"choice":{"poi":signed}})
        assert preview.status_code == 200 and preview.json()["options"][0]["days"][0]["items"][0]["locked"]
        assert client.post("/api/maps/snapshot", json={}).headers["content-type"] == "image/png"


def test_travel_fallback_only_after_confirmed_empty():
    rail = Rail()
    config = Settings()
    with browser(create_app(config, railway=rail, clock=lambda:datetime(2026,11,18,15,2,tzinfo=CHINA))) as client:
        result = client.post("/api/travel", json={"origin":"上海","destination":"杭州","mode":"auto"}).json()
        assert [q["action"] for q in rail.calls] == ["tickets", "transfer"]
        assert result["date"] == "2026-11-18" and result["time"] == "15:02"
        assert result["rail"]["status"] == "empty" and result["dateDefault"]


def test_travel_requires_origin_and_does_not_try_transfer_on_rail_failure():
    class FailingRail(Rail):
        async def query(self, args):
            self.calls.append(args)
            raise RailError(503, "铁路暂不可用")

    rail = FailingRail()
    with browser(create_app(Settings(), railway=rail, clock=lambda:datetime(2026,11,18,15,2,tzinfo=CHINA))) as client:
        args = {"destination":"杭州","mode":"auto"}
        missing = client.post("/api/travel", json=args).json()
        assert missing["rail"]["status"] == "origin-needed" and not rail.calls
        unavailable = client.post("/api/travel", json={**args,"origin":"上海","date":"2026-11-18","time":"10:15"}).json()
        assert unavailable["rail"]["status"] == "unavailable"
        assert unavailable["request"]["time"] == "10:15" and unavailable["timeAdjusted"]
        assert [(q["action"], q["departureTime"]) for q in rail.calls] == [("tickets","15:02")]


def test_checkout_pauses_survives_process_restart_and_rejects_other_actor(tmp_path):
    # Only public assets, never credentials, are shared with this isolated configuration.
    (tmp_path / "public").mkdir()
    config = Settings(root=tmp_path, rail_enabled=False, food_enabled=True)
    with browser(create_app(config)) as a:
        quote = a.post("/api/food/quote", json={"itemId":"beef-rice","quantity":2})
        assert quote.status_code == 200 and quote.json()["name"] and quote.json()["total"] > 0
        ident = quote.json()["quoteId"]
        cookie = a.cookies.get("xiaotu_demo")
        assert a.post("/api/food/status", json={"quoteId":ident}).json()["status"] == "awaiting_confirmation"
        assert a.post("/api/food/confirm", json={"quoteId":ident,"confirmed":False}).status_code == 400
        assert a.post("/api/chat", json={"message":"模拟外卖直接下单"}).status_code == 200
        assert a.post("/api/food/status", json={"quoteId":ident}).json()["status"] == "awaiting_confirmation"

    # Reopen a new graph, ledger handle and browser to exercise real persisted state.
    with browser(create_app(config)) as b:
        args = {"quoteId":ident,"confirmed":True}
        assert b.post("/api/food/confirm", json=args).status_code == 404
        b.cookies.set("xiaotu_demo", cookie)
        accepted = b.post("/api/food/confirm", json=args)
        assert accepted.status_code == 200, accepted.text
        first = accepted.json()
        assert first["status"] == "succeeded" and first["orderId"].startswith("test_")
        assert b.post("/api/food/confirm", json=args).json() == first
        assert b.post("/api/food/status", json={"quoteId":ident}).json() == first


async def test_langchain_qwen_adapter_uses_configured_endpoint_only():
    async def mocked(request):
        assert str(request.url) == "https://llm.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer FAKE"
        value = json.loads(request.content)
        assert value["response_format"] == {"type":"json_object"}
        assert "reasoning_effort" not in value and "enable_thinking" not in value
        assert json.loads(value["messages"][1]["content"]) == {"message":"{试试}"}
        return httpx.Response(200, json={"choices":[{"message":{"content":"{\"intent\":\"chat\"}"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mocked)) as client:
        assert await Qwen(Settings(base="https://llm.invalid/v1",key="FAKE"), client)("系统", {"message":"{试试}"}) == {"intent":"chat"}


async def test_thinking_model_gets_headroom_and_names_truncated_output():
    sent = []

    async def mocked(request):
        sent.append((json.loads(request.content), request.extensions["timeout"]["read"]))
        return httpx.Response(200, json={"choices":[{"finish_reason":"length","message":{"content":""}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mocked)) as client:
        thinking = Settings(base="https://llm.invalid/v1",key="FAKE",model="glm-5.3-flash",effort="high")
        with pytest.raises(ModelError, match="截断.*调低 XIAOTU_MODEL_REASONING_EFFORT"):
            await Qwen(thinking, client)("系统", {"message":"{试试}"}, tokens=3100, timeout=45)
        body, read = sent.pop()
        assert (body["reasoning_effort"], body["max_tokens"], read) == ("high", 9100, 105)
        assert "enable_thinking" not in body
        with pytest.raises(ModelError, match="截断.*设置 XIAOTU_MODEL_REASONING_EFFORT"):
            await Qwen(Settings(base="https://llm.invalid/v1",key="FAKE"), client)("系统", {"message":"{试试}"}, tokens=3100, timeout=45)
        body, read = sent.pop()
        assert (body["max_tokens"], read) == (3100, 45) and "reasoning_effort" not in body


def test_model_settings_prefer_neutral_names_and_keep_qwen_aliases(tmp_path):
    for folder in ("config", "skills"):
        shutil.copytree(ROOT / folder, tmp_path / folder)
    names = dict(XIAOTU_MODEL_API_KEY="k1", XIAOTU_MODEL="glm-5.3-flash",
                 XIAOTU_MODEL_BASE_URL="https://open.bigmodel.cn/api/coding/paas/v4",
                 XIAOTU_MODEL_REASONING_EFFORT="High", XIAOTU_MODEL_THINKING="true",
                 DASHSCOPE_API_KEY="k2", QWEN_MODEL="qwen-plus", QWEN_ENABLE_THINKING="false")
    current = load_settings(tmp_path, names)
    assert (current.key, current.model, current.base, current.effort, current.thinking) == (
        "k1", "glm-5.3-flash", "https://open.bigmodel.cn/api/coding/paas/v4", "high", True)
    legacy = load_settings(tmp_path, {k: v for k, v in names.items() if not k.startswith("XIAOTU_")})
    assert (legacy.key, legacy.model, legacy.thinking, legacy.effort) == ("k2", "qwen-plus", False, "")
    assert legacy.base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    with pytest.raises(ValueError, match="XIAOTU_MODEL_REASONING_EFFORT"):
        load_settings(tmp_path, {"XIAOTU_MODEL_REASONING_EFFORT": "hgih"})
    with browser(create_app(Settings(key="FAKE", model="glm-5.3-flash", rail_enabled=False))) as client:
        state = client.get("/api/status").json()
        assert (state["mode"], state["model"]) == ("qwen", "glm-5.3-flash")


def test_capabilities_current_files():
    settings, texts, names = capabilities(ROOT)
    assert "get-tickets" in settings["12306"]["allowedTools"]
    assert "itinerary-planning" in names["plan"]
    assert "itinerary-planning" in texts["plan"]
    assert "create-order" in settings["mcd"]["allowedTools"]
    assert "mcd-delivery" in names["route"] and "mcd-delivery" in texts["route"]
