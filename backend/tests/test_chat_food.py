"""The parallel food branch: intent routing, quote flow and the confirm gate."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xiaotu.agent.intent import resolve_route
from xiaotu.app import create_app
from xiaotu.config import Settings

STORE = {"storeCode": "STORE01", "beCode": "BE1", "storeName": "麦当劳南京西路店",
         "address": "上海市静安区南京西路100号", "businessStatus": "营业中",
         "businessStartTime": "07:00", "businessEndTime": "23:00", "distance": 800}
MENU = {"categories": [{"name": "汉堡类", "codes": ["M1", "M2"]}],
        "meals": {"M1": {"name": "巨无霸", "priceFen": 2200},
                  "M2": {"name": "巨无霸中套餐", "priceFen": 3900}},
        "frequent": ["M1"]}


class FakeMcd:
    configured = True

    def __init__(self):
        self.calls = []
        self.creates = 0

    async def close(self):
        pass

    async def addresses(self):
        self.calls.append("addresses")
        return [{"addressId": "301", "address": "上海市黄浦区", "addressDetail": "某酒店前台"}]

    async def delivery_stores(self, address_id):
        self.calls.append(("stores", address_id))
        return [dict(STORE)]

    async def meals(self, store_code, be_code=""):
        self.calls.append(("meals", store_code))
        return {"categories": [dict(group) for group in MENU["categories"]],
                "meals": {code: dict(meal) for code, meal in MENU["meals"].items()},
                "frequent": list(MENU["frequent"])}

    async def coupons(self, store_code, be_code=""):
        return [{"title": "麦乐送满59减10", "couponId": "C1", "description": "满59可用"}]

    async def calculate_price(self, *, store_code, address_id, items, be_code=""):
        total = sum(item["priceFen"] * item["quantity"] for item in items)
        return {"productFen": total, "deliveryFen": 900, "payFen": total + 900}

    async def order_list(self):
        return [{"orderId": "MCD9001", "orderStatus": "已送达", "storeName": STORE["storeName"],
                 "createTime": "2026-11-19 12:00:00", "totalFen": 3100}]

    async def order_detail(self, order_id):
        return None

    async def create_order(self, *, store_code, address_id, items, be_code=""):
        self.creates += 1
        prices = {code: meal["priceFen"] for code, meal in MENU["meals"].items()}
        names = {code: meal["name"] for code, meal in MENU["meals"].items()}
        total = sum(prices[item["productCode"]] * item["quantity"] for item in items) + 900
        return {"orderId": "MCD0001", "orderStatus": "待支付", "storeName": STORE["storeName"],
                "totalFen": total, "realTotalFen": total,
                "products": [{"name": names[item["productCode"]], "quantity": item["quantity"],
                              "priceFen": prices[item["productCode"]]} for item in items],
                "payUrl": "https://pay.example/MCD0001", "pickupCode": "A12"}


def route(message, history=None, proposal=None):
    return resolve_route(message, history or [], {}, proposal or {})


def test_food_intent_branches_off_the_router():
    assert route("帮我点份麦当劳外卖")[2]["foodAction"] == "order"
    assert route("麦当劳有什么套餐")[2]["foodAction"] == "menu"
    assert route("我的麦当劳外卖到哪了")[2]["foodAction"] == "orders"
    assert route("查一下麦当劳的优惠券")[2]["foodAction"] == "coupons"
    assert route("看看麦当劳能送到哪些门店")[2]["foodAction"] == "stores"
    assert route("我账号里有哪些收货地址") == ("chat", "我账号里有哪些收货地址", {})
    assert route("麦当劳和肯德基哪个好吃")[0] == "chat"
    assert route("什么是麦乐送")[0] == "chat"
    assert route("杭州明天天气怎么样")[2]["kind"] == "weather"
    continuation = [{"role": "user", "content": "帮我点份麦当劳外卖"},
                    {"role": "assistant", "content": "请选择收货地址：1. 上海市黄浦区（某酒店）；2. …"}]
    assert route("用第二个", continuation)[0] == "food"
    fuzzy = route("晚上整点吃的", proposal={"intent": "food",
                                           "query": {"kind": "food", "foodAction": "menu"}})
    assert fuzzy[0] == "food" and fuzzy[2]["foodAction"] == "menu"


def browser(app):
    return TestClient(app, base_url="http://127.0.0.1:3212")


def open_app(tmp_path, mcd, **flags):
    (tmp_path / "public").mkdir(exist_ok=True)
    config = Settings(root=tmp_path, rail_enabled=False, **flags)
    return browser(create_app(config, mcd=mcd))


def chat(client, message, history=None):
    response = client.post("/api/chat", json={"message": message, **({"history": history} if history else {})})
    assert response.status_code == 200
    return response.json()


def test_food_branch_menu_query_uses_read_tools_only(tmp_path):
    mcd = FakeMcd()
    with open_app(tmp_path, mcd) as client:
        result = chat(client, "麦当劳有什么套餐")
        assert result["intent"] == "food"
        assert result["cards"][0]["type"] == "food-menu"
        assert result["cards"][0]["storeName"] == STORE["storeName"]
        assert result["sources"][0]["name"] == "麦当劳 MCP"
        assert mcd.creates == 0


def test_food_branch_order_quote_and_gateway_confirm(tmp_path):
    mcd = FakeMcd()
    with open_app(tmp_path, mcd, mcd_token="token", mcd_order_enabled=True) as client:
        quoted = chat(client, "帮我点一份巨无霸外卖")
        assert quoted["intent"] == "food"
        card = quoted["cards"][0]
        assert card["type"] == "food-quote" and card["payFen"] == 3100
        assert card["items"] == [{"productCode": "M1", "name": "巨无霸",
                                  "quantity": 1, "priceFen": 2200}]
        assert "确认" in quoted["reply"] and mcd.creates == 0

        confirmed = client.post("/api/food/confirm",
                                json={"quoteId": card["quoteId"], "confirmed": True}).json()
        assert confirmed["status"] == "succeeded" and confirmed["provider"] == "mcd"
        assert confirmed["orderId"] == "MCD0001" and mcd.creates == 1
        assert confirmed["route"] == "#/order/" + card["quoteId"]
        assert confirmed["payUrl"] == "https://pay.example/MCD0001"

        again = client.post("/api/food/confirm",
                            json={"quoteId": card["quoteId"], "confirmed": True}).json()
        assert again["status"] == "succeeded" and mcd.creates == 1  # replay, not a second order
        status = client.post("/api/food/status", json={"quoteId": card["quoteId"]}).json()
        assert status["status"] == "succeeded" and status["sandbox"] is False


def test_food_confirm_is_gated_behind_the_order_flag(tmp_path):
    mcd = FakeMcd()
    with open_app(tmp_path, mcd, mcd_token="token", mcd_order_enabled=False) as client:
        quoted = chat(client, "来一份巨无霸套餐外卖")
        card = quoted["cards"][0]
        refused = client.post("/api/food/confirm",
                              json={"quoteId": card["quoteId"], "confirmed": True})
        assert refused.status_code == 403 and mcd.creates == 0
        assert "XIAOTU_MCD_ORDER" in refused.json()["error"]


def test_food_orders_query_and_status_endpoint(tmp_path):
    mcd = FakeMcd()
    with open_app(tmp_path, mcd) as client:
        result = chat(client, "我的麦当劳订单到哪了")
        assert result["cards"][0]["type"] == "food-orders"
        assert result["cards"][0]["orders"][0]["orderId"] == "MCD9001"
        state = client.get("/api/status").json()
        assert state["mcd"] == "mcd-mcp" and state["mcdOrder"] is False
        missing = client.post("/api/food/status", json={"quoteId": "quote_" + "0" * 32})
        assert missing.status_code == 404


def test_food_comparison_stays_chat_and_never_touches_tools(tmp_path):
    mcd = FakeMcd()
    with open_app(tmp_path, mcd) as client:
        result = chat(client, "麦当劳和肯德基哪个好吃")
        assert result["intent"] == "chat" and "cards" not in result
        assert mcd.calls == []
