"""McDonald's MCP business layer: read tools for the chat agent, one write tool.

All amounts are normalized to fen (分) ints. calculate-price reports fen
directly while order payloads use yuan strings, so both are accepted on the
way in and never emitted back out. Response field names follow the official
documentation; where the documentation is silent, tolerant candidates are
tried and parsing stays in this module so a live mismatch is a one-file fix.
"""
from __future__ import annotations

import re

from xiaotu.execution import ToolSendLost, ToolTransient, ToolUncertain

from ._common import AsyncCache, clean
from .mcd_transport import MCD_SOURCE, MCD_TOOLS, McdError, McdMcp, invalid, unavailable

__all__ = ["MCD_SOURCE", "McdError", "McdRefused", "McdService"]

ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
MEAL_CODE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
YUAN = re.compile(r"\d{1,9}(?:\.\d{1,2})?\Z")


class McdRefused(Exception):
    """The McDonald's tool executed and its business layer refused the request."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def yuan_fen(value) -> int | None:
    """Convert a yuan amount like "22" or "22.50" into fen; otherwise None."""
    if isinstance(value, str) and YUAN.fullmatch(text := value.strip()):
        whole, _, fraction = text.partition(".")
        return int(whole) * 100 + int((fraction + "00")[:2])
    return None


def price_fen(value) -> int | None:
    """Accept fen ints (calculate-price style) and yuan strings (order style)."""
    if type(value) is int and value >= 0:
        return value
    return yuan_fen(value)


def _data(value):
    """Unwrap the business envelope {success, code, message, data}."""
    if isinstance(value, dict) and isinstance(value.get("success"), bool):
        if value["success"]:
            return value.get("data")
        code = value.get("code")
        raise McdRefused(code if isinstance(code, str) and 1 <= len(code) <= 64 else "MCD_BUSINESS",
                         clean(value.get("message"), 200) or "麦当劳业务处理失败。")
    return value


def _list(value, *keys) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in (*keys, "list", "records"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def _fen_first(data: dict, names) -> int | None:
    if not isinstance(data, dict):
        return None
    for name in names:
        if (amount := price_fen(data.get(name))) is not None:
            return amount
    return None


def _id(value) -> str:
    return clean(value, 64)


def _checked_items(items) -> list[dict]:
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise McdError(400, "请选择 1–20 种餐品。", "MCD_ARGUMENTS")
    checked, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise McdError(400, "餐品格式不正确。", "MCD_ARGUMENTS")
        code, quantity = _id(item.get("productCode", item.get("code"))), item.get("quantity")
        if not MEAL_CODE.fullmatch(code) or code in seen or type(quantity) is not int or not 1 <= quantity <= 10:
            raise McdError(400, "餐品必须来自菜单、不重复，数量为 1–10。", "MCD_ARGUMENTS")
        seen.add(code)
        checked.append({"productCode": code, "quantity": quantity})
    return checked


def order_view(value) -> dict:
    """Normalize a create-order / query-order payload into the receipt subset."""
    if not isinstance(value, dict):
        raise ToolUncertain("invalid_receipt")
    merged = dict(value)
    if isinstance(inner := value.get("orderDetail"), dict):
        for key, item in inner.items():
            merged.setdefault(key, item)
    order_id = _id(merged.get("orderId"))
    if not ID.fullmatch(order_id):
        raise ToolUncertain("invalid_receipt")
    products = []
    for row in _list(merged.get("orderProductList"), "products", "productList")[:40]:
        if not isinstance(row, dict):
            continue
        name = clean(row.get("productName", row.get("name")), 80)
        if name:
            products.append({"name": name, "quantity": row.get("quantity") if type(row.get("quantity")) is int else None,
                             "priceFen": price_fen(row.get("price", row.get("productPrice")))})
    # Order payloads report yuan strings; a bare int is read as fen.
    view = {"orderId": order_id, "orderStatus": clean(merged.get("orderStatus", merged.get("status")), 40),
            "storeName": clean(merged.get("storeName"), 80),
            "productFen": yuan_fen(merged.get("productPrice")),
            "deliveryFen": yuan_fen(merged.get("deliveryPrice")),
            "totalFen": yuan_fen(merged.get("totalAmount")),
            "realTotalFen": yuan_fen(merged.get("realTotalAmount", merged.get("payAmount"))),
            "products": products}
    for key in ("payH5Url", "payUrl"):
        if url := clean(merged.get(key), 500):
            view["payUrl"] = url
            break
    for key in ("pickupCode", "lockerCode", "createTime"):
        if text := clean(merged.get(key), 40):
            view[key] = text
    return view


class McdService:
    """Read tools may be called directly; create-order belongs to the gateway."""

    def __init__(self, token: str = "", settings: dict | None = None, *,
                 transport: McdMcp | None = None, http_client=None):
        self._mcp = transport if transport is not None else McdMcp(token, settings, http_client=http_client)
        self._own_transport = transport is None
        self._cache: AsyncCache = AsyncCache(120)

    @property
    def configured(self) -> bool:
        return self._mcp.configured

    async def close(self) -> None:
        await self._cache.close()
        if self._own_transport:
            await self._mcp.close()

    async def _read(self, name: str, args: dict):
        if not self.configured:
            raise McdError(503, "尚未配置麦当劳 MCP，请填写 MCD_MCP_TOKEN。", "MCD_CONFIG")
        try:
            return await self._mcp.call(name, args)
        except (ToolSendLost, ToolTransient, ToolUncertain):
            raise unavailable() from None

    async def addresses(self) -> list[dict]:
        """Delivery addresses saved on the token's account; no contact details."""
        async def run():
            rows = _list(_data(await self._read("delivery-query-addresses", {})),
                         "addresses", "addressList")
            out = []
            for row in rows[:20]:
                if not isinstance(row, dict):
                    continue
                address_id = _id(row.get("addressId", row.get("id")))
                if not ID.fullmatch(address_id):
                    continue
                # The account's fullAddress is the pick target; contact name and
                # phone stay server-side and never reach a card.
                text = clean(row.get("fullAddress", row.get("address")), 160)
                out.append({"addressId": address_id, "address": text,
                            "addressDetail": clean(row.get("addressDetail"), 120)})
            return out
        return await self._cache.get(("mcd-addresses",), 60, run)

    async def delivery_stores(self, address_id: str) -> list[dict]:
        """Stores that can deliver to the address (麦乐送, beType=2)."""
        if not ID.fullmatch(address_id := _id(address_id)):
            raise McdError(400, "配送地址无效，请从账号地址中选择。", "MCD_ARGUMENTS")
        async def run():
            rows = _list(_data(await self._read("delivery-query-stores",
                                                {"addressId": address_id, "beType": 2})),
                         "stores", "storeList")
            out = []
            for row in rows[:20]:
                if not isinstance(row, dict):
                    continue
                code = _id(row.get("storeCode"))
                if not ID.fullmatch(code):
                    continue
                status = row.get("businessStatus")
                open_text = {True: "营业中", False: "休息中"}.get(status, clean(status, 40))
                out.append({"storeCode": code, "beCode": clean(row.get("beCode"), 64),
                            "storeName": clean(row.get("storeName"), 80),
                            "address": clean(row.get("address", row.get("storeAddress")), 120),
                            "businessStatus": open_text,
                            "businessStartTime": clean(row.get("businessStartTime"), 20),
                            "businessEndTime": clean(row.get("businessEndTime"), 20),
                            "distance": row.get("distance") if type(row.get("distance")) is int else None})
            return out
        return await self._cache.get(("mcd-stores", address_id), 120, run)

    async def meals(self, store_code: str, be_code: str = "") -> dict:
        """Sellable menu for delivery (orderType=2, beType=2), normalized to fen."""
        if not ID.fullmatch(store_code := _id(store_code)) or not re.fullmatch(r"[A-Za-z0-9_-]{0,64}", be_code := _id(be_code)):
            raise McdError(400, "门店无效，请重新选择可配送门店。", "MCD_ARGUMENTS")
        async def run():
            data = _data(await self._read("query-meals", {"storeCode": store_code, "orderType": 2, "beType": 2,
                                                          **({"beCode": be_code} if be_code else {})}))
            raw = data.get("meals") if isinstance(data, dict) else None
            meals: dict[str, dict] = {}
            if isinstance(raw, dict):
                for code, row in list(raw.items())[:200]:
                    if not isinstance(code, str) or not MEAL_CODE.fullmatch(code) or not isinstance(row, dict):
                        continue
                    price = price_fen(row.get("currentPrice"))
                    name = clean(row.get("name"), 80)
                    if not name or price is None:
                        continue
                    entry = {"name": name, "priceFen": price}
                    if (original := price_fen(row.get("originalPrice"))) is not None and original > price:
                        entry["originalPriceFen"] = original
                    meals[code] = entry
            categories = []
            for row in _list(data.get("categories") if isinstance(data, dict) else None, "meals")[:40]:
                if not isinstance(row, dict):
                    continue
                name = clean(row.get("name", row.get("categoryName")), 40)
                codes = []
                for item in _list(row.get("meals"))[:50]:
                    code = item if isinstance(item, str) else _id(item.get("code")) if isinstance(item, dict) else ""
                    if MEAL_CODE.fullmatch(code) and code in meals:
                        codes.append(code)
                if name and codes:
                    categories.append({"name": name, "codes": codes})
            frequent = [code for code in _list(data.get("frequent") if isinstance(data, dict) else None)[:20]
                        if isinstance(code, str) and code in meals]
            return {"categories": categories, "meals": meals, "frequent": frequent}
        return await self._cache.get(("mcd-meals", store_code, be_code), 120, run)

    async def coupons(self, store_code: str, be_code: str = "") -> list[dict]:
        """Coupons usable at this store for the current order mode (display only)."""
        if not ID.fullmatch(store_code := _id(store_code)):
            raise McdError(400, "门店无效，请重新选择可配送门店。", "MCD_ARGUMENTS")
        async def run():
            rows = _list(_data(await self._read("query-store-coupons",
                                                {"storeCode": store_code, "orderType": 2, "beType": 2,
                                                 **({"beCode": be_code} if be_code else {})})),
                         "coupons", "couponList")
            out = []
            for row in rows[:50]:
                if not isinstance(row, dict):
                    continue
                coupon_id = _id(row.get("couponId", row.get("id")))
                title = clean(row.get("title", row.get("couponName", row.get("name"))), 80)
                if not title:
                    continue
                entry = {"title": title}
                if ID.fullmatch(coupon_id):
                    entry["couponId"] = coupon_id
                if text := clean(row.get("description", row.get("content")), 160):
                    entry["description"] = text
                out.append(entry)
            return out
        return await self._cache.get(("mcd-coupons", store_code, be_code), 120, run)

    async def calculate_price(self, *, store_code: str, address_id: str, items: list[dict],
                              be_code: str = "") -> dict:
        """Fresh delivery quote in fen. Never cached: it precedes a real order."""
        if not ID.fullmatch(store_code := _id(store_code)) or not ID.fullmatch(address_id := _id(address_id)):
            raise McdError(400, "门店或配送地址无效。", "MCD_ARGUMENTS")
        checked = _checked_items(items)
        data = _data(await self._read("calculate-price", {
            "storeCode": store_code, "orderType": 2, "beType": 2, "addressId": address_id,
            **({"beCode": be_code} if be_code else {}), "items": checked}))
        product = _fen_first(data, ("productPrice", "productAmount", "foodPrice", "foodAmount"))
        pay = _fen_first(data, ("payPrice", "payablePrice", "price", "totalPrice", "realPayPrice",
                                "payAmount", "orderAmount"))
        if product is None or pay is None:
            raise invalid()
        quote = {"productFen": product, "payFen": pay,
                 "deliveryFen": _fen_first(data, ("deliveryPrice", "deliveryFee", "shippingPrice",
                                                  "shippingFee")) or 0}
        # Packing fee is real on 麦乐送 and must be shown instead of hiding the gap.
        packing = _fen_first(data, ("packingPrice", "packingFee", "packingOriginalPrice"))
        if packing:
            quote["packingFen"] = packing
        if (discount := _fen_first(data, ("discountPrice", "couponPrice", "preferentialPrice",
                                          "discountAmount", "couponAmount", "discount"))) is not None:
            quote["discountFen"] = discount
        return quote

    async def order_list(self) -> list[dict]:
        """The account's recent orders (up to 10), for status queries and snapshots."""
        rows = []
        for row in _list(_data(await self._read("order-list", {})), "orders", "orderList")[:10]:
            if not isinstance(row, dict):
                continue
            order_id = _id(row.get("orderId"))
            if not ID.fullmatch(order_id):
                continue
            entry = {"orderId": order_id, "orderStatus": clean(row.get("orderStatus", row.get("status")), 40),
                     "storeName": clean(row.get("storeName"), 80), "createTime": clean(row.get("createTime"), 40)}
            if (total := price_fen(row.get("realTotalAmount", row.get("totalAmount", row.get("totalPrice"))))) is not None:
                entry["totalFen"] = total
            rows.append(entry)
        return rows

    async def order_detail(self, order_id: str) -> dict:
        if not ID.fullmatch(order_id := _id(order_id)):
            raise McdError(400, "订单号无效。", "MCD_ARGUMENTS")
        return order_view(_data(await self._read("query-order", {"orderId": order_id})))

    async def create_order(self, *, store_code: str, address_id: str, items: list[dict],
                           be_code: str = "") -> dict:
        """Send create-order once. Retries and reconciliation belong to the gateway.

        Business refusals raise McdRefused (the tool ran, no order was created);
        transport failures keep their gateway classification.
        """
        if not self.configured:
            raise McdError(503, "尚未配置麦当劳 MCP，请填写 MCD_MCP_TOKEN。", "MCD_CONFIG")
        if not ID.fullmatch(store_code := _id(store_code)) or not ID.fullmatch(address_id := _id(address_id)):
            raise McdError(400, "门店或配送地址无效。", "MCD_ARGUMENTS")
        args = {"storeCode": store_code, "orderType": 2, "beType": 2, "addressId": address_id,
                "items": _checked_items(items)}
        if be_code := _id(be_code):
            args["beCode"] = be_code
        return order_view(_data(await self._mcp.call("create-order", args)))
