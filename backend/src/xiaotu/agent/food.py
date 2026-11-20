"""The parallel food branch: McDonald's queries and quote cards, never a send.

This node may call read tools (addresses, deliverable stores, menu, coupons,
order status, calculate-price). Placing an order ends here as a persisted
quote plus a confirmation card; the actual create-order happens only through
the explicit confirm endpoint behind the reliable gateway.
"""
from __future__ import annotations

import re

from xiaotu.domain import clean
from xiaotu.food import FoodError
from xiaotu.tools.mcd import MCD_SOURCE, McdError, McdRefused

NUM = {**dict(zip("一二三四五六七八九十", range(1, 11))), "两": 2}
ORDINAL = re.compile(r"(?:第|用|选)\s*([一两二三四五六七八九十1-9])\s*(?:个|号|家)?")
COUNT = r"([一两二三四五六七八九十1-9]|10)"
CORE_SUFFIX = re.compile(r"(?:中|大|小)?(?:套餐|杯|盒|份|块)\Z")
MEAL_DISCOUNT = "真实订单：确认后才会提交，支付需你自己在麦当劳完成。"


def _answer(reply, card=None, *, status="live"):
    return {"reply": reply, "sources": [{"name": MCD_SOURCE, "status": status, "note": reply}],
            **({"cards": [card]} if card else {})}


def _money(fen) -> str:
    return f"¥{(fen or 0) / 100:.2f}"


def _address_text(row) -> str:
    detail = clean(row.get("addressDetail", ""), 40)
    return clean(row.get("address", ""), 80) + (f"（{detail}）" if detail else "")


def _count(digit) -> int:
    if isinstance(digit, str) and digit.isdigit():
        return int(digit)
    return NUM.get(digit, 1)


def _ordinal(message):
    if match := ORDINAL.search(message):
        return _count(match[1])
    return None


def _core(name: str) -> str:
    return CORE_SUFFIX.sub("", name) or name


def _match_items(message: str, menu: dict) -> list[dict]:
    """Match spoken dish names against the menu; exact names win over cores."""
    picks: dict[str, dict] = {}
    for code, meal in menu["meals"].items():
        name = meal["name"]
        if name in message:
            picks[code] = {"productCode": code, "name": name, "priceFen": meal["priceFen"]}
    if not picks:
        cores = {}
        for code, meal in menu["meals"].items():
            stem = _core(meal["name"])
            if stem and stem in message:
                cores.setdefault(stem, []).append((code, meal))
        for stem, rows in cores.items():
            # Prefer the plain product over its combo when both match.
            plain = next(((code, meal) for code, meal in rows if meal["name"] == stem), rows[0])
            picks[plain[0]] = {"productCode": plain[0], "name": plain[1]["name"],
                               "priceFen": plain[1]["priceFen"]}
    out = []
    for pick in picks.values():
        quantity = 1
        if before := re.search(COUNT + r"\s*份?" + re.escape(pick["name"]), message):
            quantity = _count(before[1])
        elif after := re.search(re.escape(pick["name"]) + r"\s*(?:x|×)?\s*" + COUNT, message):
            quantity = _count(after[1])
        pick["quantity"] = max(1, min(10, quantity))
        out.append(pick)
    return out[:20]


def _menu_card(store: dict, menu: dict) -> dict:
    categories = []
    for group in menu["categories"][:8]:
        items = [{"code": code, "name": menu["meals"][code]["name"],
                  "priceFen": menu["meals"][code]["priceFen"],
                  **({"originalPriceFen": menu["meals"][code]["originalPriceFen"]}
                     if "originalPriceFen" in menu["meals"][code] else {})}
                 for code in group["codes"][:12]]
        if items:
            categories.append({"name": group["name"], "items": items})
    return {"type": "food-menu", "storeName": store["storeName"], "categories": categories,
            "source": MCD_SOURCE}


async def _resolve_address(message, history, mcd):
    """Return (address_id, address_text, question); a question means ask first."""
    rows = await mcd.addresses()
    if not rows:
        return "", "", ("你的麦当劳账号还没有收货地址。请先在麦当劳 App 或小程序添加；"
                        "小途不会代填地址和联系电话。")
    if (index := _ordinal(message)) and 1 <= index <= len(rows):
        row = rows[index - 1]
        return row["addressId"], _address_text(row), ""
    if len(rows) == 1:
        return rows[0]["addressId"], _address_text(rows[0]), ""
    return "", "", "请选择收货地址：" + "；".join(
        f"{i + 1}. {_address_text(row)}" for i, row in enumerate(rows[:6]))


async def _resolve_store(message, mcd, address_id):
    stores = await mcd.delivery_stores(address_id)
    if not stores:
        return None, None
    named = next((store for store in stores
                  if store["storeName"] and store["storeName"].rstrip("店") in message), stores[0])
    return named, stores


async def _addresses(message, history, mcd, food, actor) -> dict:
    rows = await mcd.addresses()
    if not rows:
        return _answer("你的麦当劳账号还没有收货地址。请先在麦当劳 App 或小程序添加。")
    card = {"type": "food-addresses", "addresses": [
        {"addressId": row["addressId"], "address": clean(row["address"], 80),
         "addressDetail": clean(row["addressDetail"], 60)} for row in rows[:10]], "source": MCD_SOURCE}
    return _answer("以下是账号里保存的收货地址（不含联系方式）：", card)


async def _stores(message, history, mcd, food, actor) -> dict:
    address_id, address_text, question = await _resolve_address(message, history, mcd)
    if question:
        return _answer(question)
    stores = await mcd.delivery_stores(address_id)
    if not stores:
        return _answer(f"{address_text} 附近暂时没有可配送的麦当劳门店。")
    card = {"type": "food-stores", "addressText": address_text, "stores": [
        {"storeCode": row["storeCode"], "storeName": row["storeName"],
         "address": row["address"], "businessStatus": row["businessStatus"],
         "businessStartTime": row["businessStartTime"], "businessEndTime": row["businessEndTime"]}
        for row in stores[:8]], "source": MCD_SOURCE}
    return _answer(f"{address_text} 可由以下门店配送：", card)


async def _menu(message, history, mcd, food, actor) -> dict:
    address_id, address_text, question = await _resolve_address(message, history, mcd)
    if question:
        return _answer(question)
    store, _ = await _resolve_store(message, mcd, address_id)
    if store is None:
        return _answer(f"{address_text} 附近暂时没有可配送的麦当劳门店。")
    menu = await mcd.meals(store["storeCode"], store["beCode"])
    return _answer(f"这是「{store['storeName']}」的麦乐送菜单，说「来一份巨无霸」就能报价：",
                   _menu_card(store, menu))


async def _coupons(message, history, mcd, food, actor) -> dict:
    address_id, address_text, question = await _resolve_address(message, history, mcd)
    if question:
        return _answer(question)
    store, _ = await _resolve_store(message, mcd, address_id)
    if store is None:
        return _answer(f"{address_text} 附近暂时没有可配送的麦当劳门店。")
    coupons = await mcd.coupons(store["storeCode"], store["beCode"])
    card = {"type": "food-coupons", "storeName": store["storeName"], "coupons": coupons[:10],
            "source": MCD_SOURCE}
    return _answer(f"「{store['storeName']}」当前可用的优惠券如下（本版下单暂不自动用券）：", card)


async def _orders(message, history, mcd, food, actor) -> dict:
    orders = await mcd.order_list()
    if not orders:
        return _answer("你的麦当劳账号最近没有订单。")
    card = {"type": "food-orders", "orders": [
        {"orderId": row["orderId"], "orderStatus": row["orderStatus"],
         "storeName": row["storeName"], "createTime": row["createTime"],
         **({"totalFen": row["totalFen"]} if row.get("totalFen") is not None else {})}
        for row in orders[:10]], "source": MCD_SOURCE}
    return _answer("以下是你麦当劳账号的最近订单（以麦当劳 App 为准）：", card)


async def _order(message, history, mcd, food, actor) -> dict:
    address_id, address_text, question = await _resolve_address(message, history, mcd)
    if question:
        return _answer(question)
    store, stores = await _resolve_store(message, mcd, address_id)
    if store is None:
        return _answer(f"{address_text} 附近暂时没有可配送的麦当劳门店。")
    menu = await mcd.meals(store["storeCode"], store["beCode"])
    picks = _match_items(message, menu)
    if not picks:
        return _answer(f"已选「{store['storeName']}」配送到 {address_text}。"
                       "想吃什么？可以这样说：「来一份巨无霸和一份薯条」。", _menu_card(store, menu))
    note = f"默认选了「{store['storeName']}」，回复门店名可更换。" if len(stores) > 1 else ""
    items = [{"productCode": pick["productCode"], "name": clean(pick["name"], 80),
              "quantity": pick["quantity"], "priceFen": pick["priceFen"]} for pick in picks]
    quote = await mcd.calculate_price(store_code=store["storeCode"], address_id=address_id,
                                      items=items, be_code=store["beCode"])
    payload = {"provider": "mcd", "storeCode": store["storeCode"],
               "beCode": store["beCode"], "addressId": address_id,
               "storeName": clean(store["storeName"], 80), "addressText": clean(address_text, 160),
               "items": items, "productFen": quote["productFen"],
               "deliveryFen": quote["deliveryFen"], "payFen": quote["payFen"]}
    if quote.get("discountFen") is not None:
        payload["discountFen"] = quote["discountFen"]
    if not food or not actor:
        return _answer("报价已算好，但下单服务当前不可用，请检查服务配置。")
    try:
        recorded = await food.record_quote(actor, payload)
    except FoodError as exc:
        return _answer(f"报价未通过校验，未发起下单：{exc}")
    card = {"type": "food-quote", "quoteId": recorded["quoteId"],
            "storeName": payload["storeName"], "addressText": payload["addressText"],
            "items": items, "productFen": payload["productFen"],
            "deliveryFen": payload["deliveryFen"], "payFen": payload["payFen"],
            **({"discountFen": payload["discountFen"]} if "discountFen" in payload else {}),
            "expiresAt": recorded["expiresAt"], "orderable": True, "source": MCD_SOURCE}
    summary = "、".join(f"{item['name']}×{item['quantity']}" for item in items)
    reply = (f"已出报价：{summary}，由「{payload['storeName']}」配送到 {payload['addressText']}，"
             f"餐品 {_money(payload['productFen'])} + 配送费 {_money(payload['deliveryFen'])}"
             + (f" - 优惠 {_money(payload['discountFen'])}" if "discountFen" in payload else "")
             + f"，合计 {_money(payload['payFen'])}。{note}{MEAL_DISCOUNT}"
               "点卡片上的「确认下单」后才会创建订单。")
    return _answer(reply, card)


async def food_chat(parsed, mcd, food, actor) -> dict:
    """Handle one food-intent turn. Read tools only; orders stop at a quote."""
    query, message, history = (parsed.get(k) for k in ("query", "message", "history"))
    action = query.get("foodAction") if isinstance(query, dict) else None
    handler = {"addresses": _addresses, "stores": _stores, "menu": _menu,
               "coupons": _coupons, "orders": _orders, "order": _order}.get(action, _order)
    try:
        return await handler(message, history or [], mcd, food, actor)
    except McdRefused as exc:
        return _answer(f"麦当劳暂时不能处理这个请求：{exc.message}", status="unavailable")
    except McdError as exc:
        status = "unconfigured" if exc.code in ("MCD_CONFIG", "MCP_CONFIG") else "unavailable"
        return _answer(str(exc), status=status)
