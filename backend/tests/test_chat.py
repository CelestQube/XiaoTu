from datetime import datetime

from fastapi.testclient import TestClient

from xiaotu.app import create_app
from xiaotu.config import Settings
from xiaotu.domain import CHINA
from xiaotu.tools.rail import RailError


TRIP = {"city":"杭州","origin":"上海","days":2,"people":2,"startDate":"2026-11-19"}
PLAN = {"days":[{"day":1,"items":[{"time":"10:00","location":"西湖","title":"散步"}]},
                {"day":2,"items":[{"time":"11:00","location":"西溪","title":"散步"}]}]}


class MapFixture:
    configured = True
    static_configured = True

    def __init__(self):
        self.calls = []

    async def forecast(self, city):
        self.calls.append(city)
        return {"days":[{"date":"2026-11-18","day":"晴","night":"多云"},
                        {"date":"2026-11-19","day":"小雨","night":"阴","dayTemp":20}]}

    async def close(self):
        pass


class RailwayFixture:
    configured = True

    def __init__(self):
        self.calls = []

    async def query(self, args):
        self.calls.append(args)
        if args.get("origin") == "南京":
            raise RailError(503, "铁路服务暂不可用。")
        hits = [{"code":"G42","from":"上海虹桥","to":"杭州东","depart":"09:02","arrive":"09:55",
                 "duration":"00:53","seats":[{"name":"二等座","price":69,"availability":"4"}]}]
        return {**args,"source":"铁路 12306 · 第三方 MCP","checkedAt":"2026-11-18T07:00:00Z",
                "items": hits if args.get("destination") == "杭州" else []}

    async def close(self):
        pass


def open_app(maps, rail, model=None):
    return TestClient(create_app(Settings(), maps=maps, railway=rail, model=model,
                      clock=lambda:datetime(2026,11,18,15,2,tzinfo=CHINA)), base_url="http://127.0.0.1:3212")


def chat(client, message, **kwargs):
    response = client.post("/api/chat", json={"message":message, "trip":TRIP, "priorPlan":PLAN, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def test_greeting_goes_directly_to_qwen_without_draft_or_tools():
    maps, rail, calls = MapFixture(), RailwayFixture(), []

    async def model(system, payload, **kwargs):
        calls.append((system, payload))
        assert set(payload) == {"message","history"}
        return {"reply":"你好呀！今天过得怎么样？","intent":"plan","trip":{"days":4},"cards":[{"type":"rail"}]}

    with open_app(maps, rail, model) as client:
        result = chat(client, "你好！")
    assert len(calls) == 1 and "不要擅自切到行程规划" in calls[0][0]
    assert result == {"mode":"qwen","intent":"chat","reply":"你好呀！今天过得怎么样？"}
    assert maps.calls == rail.calls == []


def test_general_chat_uses_natural_model_reply_and_discards_non_authorized_plan():
    calls = []
    async def model(system, payload, **kwargs):
        calls.append(payload["message"])
        if payload["message"] == "为什么坐高铁时会感觉耳朵不舒服？":
            return {"intent":"chat","reply":"快速通过隧道时，气压变化可能让耳朵暂时不适。"}
        if "previous" in payload:
            return {"intent":"plan","planAction":"update","evidence":"今天想聊点别的","trip":{"days":4},"reply":"行程已刷新"}
        return {"reply":"没问题，我们可以聊聊你最近喜欢的事。"}

    maps, rail = MapFixture(), RailwayFixture()
    with open_app(maps, rail, model) as client:
        knowledge = chat(client, "为什么坐高铁时会感觉耳朵不舒服？")
        normal = chat(client, "今天想聊点别的")
    assert knowledge["intent"] == "chat" and "气压变化" in knowledge["reply"]
    assert normal["intent"] == "chat" and "最近喜欢" in normal["reply"]
    assert "plan" not in normal and "trip" not in normal and len(calls) == 3
    assert maps.calls == rail.calls == []


def test_demo_requires_a_real_plan_request_even_with_existing_trip_and_allows_continuation():
    with TestClient(create_app(Settings(rail_enabled=False)), base_url="http://127.0.0.1:3212") as client:
        for message in ("你好","谢谢","杭州是一座怎样的城市","我今天很开心","什么是行程规划","先不规划行程",
                        "去杭州旅游要带什么？","去杭州旅游有什么好玩的？"):
            result = chat(client, message)
            assert result["intent"] == "chat" and "trip" not in result
        # Prefix verbs and a bare trailing request must plan without waiting for the model.
        planned = chat(client, "计划去厦门玩3天，两个人，希望住在方便出行的地方，每晚住宿预算400元，请帮我规划。")
        assert planned["intent"] == "plan" and planned["trip"]["city"] == "厦门"
        assert chat(client, "安排去苏州玩2天")["intent"] == "plan"
        assert chat(client, "可以帮我安排一下吗")["intent"] == "plan"
        assert chat(client, "别帮我规划")["intent"] == "chat"
        first = chat(client, "去杭州", trip={}, priorPlan=None)
        assert first["intent"] == "plan" and "plan" not in first
        history = [{"role":"user","content":"去杭州"},{"role":"assistant","content":first["reply"]}]
        second = chat(client, "玩两天，两个人", trip=first["trip"], priorPlan=None, history=history)
        edited = chat(client, "把第二天改成室内")
        assert second["intent"] == "plan" and len(second["plan"]["days"]) == 2
        assert edited["intent"] == "plan" and "室内" in edited["plan"]["days"][1]["items"][2]["title"]


def test_weather_card_uses_asked_day_and_marks_outside_forecast_without_planning():
    maps, rail = MapFixture(), RailwayFixture()
    async def wrong_model(*args, **kwargs):
        return {"intent":"plan","trip":{"days":4},"cards":[{"type":"weather", "city":"无效"}]}

    with open_app(maps, rail, wrong_model) as client:
        result = chat(client, "明天杭州天气怎么样？")
        outside = chat(client, "查杭州 2026-12-03 的天气")
        unclear = chat(client, "明天天气怎么样？")
        follow = chat(client, "杭州", history=[{"role":"user","content":"明天天气怎么样？"},
                      {"role":"assistant","content":unclear["reply"]}])
    assert result["intent"] == "lookup" and "trip" not in result and "plan" not in result
    card = result["cards"][0]
    assert card["type"] == "weather" and card["city"] == "杭州"
    assert card["days"] == [{"date":"2026-11-19","day":"小雨","night":"阴","dayTemp":20}]
    assert "未覆盖" in outside["reply"] and outside["cards"][0]["days"] == []
    assert "哪个城市" in unclear["reply"] and follow["cards"][0]["days"] == card["days"]
    assert maps.calls == ["杭州","杭州","杭州"] and rail.calls == []


def test_rail_card_completes_date_on_next_turn_and_distinguishes_empty_and_failure():
    maps, rail = MapFixture(), RailwayFixture()
    with open_app(maps, rail) as client:
        question = "查一下上海到杭州的高铁票"
        missing = chat(client, question)
        assert "出发日期" in missing["reply"] and "cards" not in missing and not rail.calls
        result = chat(client, "明天", history=[{"role":"user","content":question},
                      {"role":"assistant","content":missing["reply"]}])
        empty = chat(client, "查明天上海到苏州的高铁")
        unavailable = chat(client, "查明天南京到杭州的高铁")
    card = result["cards"][0]
    assert result["intent"] == "lookup" and "plan" not in result and "trip" not in result
    assert card["date"] == "2026-11-19" and card["origin"] == "上海" and card["type"] == "rail"
    assert card["items"][0]["seats"] == [{"name":"二等座","price":69,"availability":"4"}]
    assert empty["cards"][0]["items"] == [] and "不代表停运或售罄" in empty["reply"]
    assert "暂不可用" in unavailable["reply"] and "cards" not in unavailable
    assert unavailable["sources"][0]["status"] == "unavailable" and not maps.calls
