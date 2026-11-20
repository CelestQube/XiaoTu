from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from uuid import uuid4

from xiaotu.execution import ReliableExecutor, ToolRejected, ToolSendLost, ToolUncertain

from .provider import ACTOR, OPERATION, QUOTE, FoodProvider, SandboxMerchant
from .store import Ledger, fingerprint

MERCHANT = "小途模拟餐厅"
DEMO_ADDRESS = "模拟酒店前台（测试）"
MENU = (
    {"id": "beef-rice", "name": "番茄牛腩饭", "merchant": MERCHANT, "priceFen": 2890},
    {"id": "veg-noodles", "name": "菌菇蔬菜面", "merchant": MERCHANT, "priceFen": 2180},
    {"id": "soy-milk", "name": "原味豆浆", "merchant": MERCHANT, "priceFen": 500},
)
MENU_BY_ID = {item["id"]: item for item in MENU}
MESSAGES = {
    "awaiting_confirmation": "尚未提交，等待你确认测试订单。",
    "pending": "已记录本次确认，等待提交测试订单。",
    "confirming": "正在提交测试订单。",
    "placed": "测试订单已创建，不会真实下单或扣款。",
    "unknown": "结果待核对，请查看原订单状态，不要重新下单。",
    "rejected": "测试订单未提交成功，请检查报价是否过期后重新确认。",
}
MCD_MESSAGES = {
    "awaiting_confirmation": "报价已生成。确认后才会向麦当劳提交订单。",
    "pending": "已记录本次确认，等待提交麦当劳订单。",
    "confirming": "正在提交麦当劳订单，请勿重复操作。",
    "placed": "麦当劳订单已创建。请打开支付链接完成支付；不支付不会扣款。",
    "unknown": "提交结果待核对。请稍后查询本次报价的状态，不要重新下单。",
    "rejected": "订单未创建。请查看失败原因后重新报价。",
}
MCD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
MCD_BE_CODE = re.compile(r"[A-Za-z0-9_-]{0,64}\Z")
MAX_FEN = 100_000_000


class FoodError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _actor(actor: str) -> None:
    if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
        raise FoodError(403, "没有有效的外卖会话。")


def _quote_id(quote_id: str) -> None:
    if not isinstance(quote_id, str) or not QUOTE.fullmatch(quote_id):
        raise FoodError(404, "未找到本会话的报价。")


def _op_id(operation_id: str) -> None:
    if not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id):
        raise FoodError(404, "未找到本会话的订单操作。")


def _stamp(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def mcd_quote_details(raw) -> dict:
    """Validate an agent-built McDonald's quote before it is allowed to persist.

    The quote is composed server-side from calculate-price and matched menu
    items, and the ledger replays it verbatim into create-order, so every
    field is checked here and amounts must stay self-consistent.
    """
    if not isinstance(raw, dict) or raw.get("provider") != "mcd":
        raise FoodError(400, "麦当劳报价格式不正确。")
    store_code, address_id = raw.get("storeCode"), raw.get("addressId")
    be_code = raw.get("beCode", "")
    store_name, address_text = raw.get("storeName", ""), raw.get("addressText", "")
    if (not isinstance(store_code, str) or not MCD_ID.fullmatch(store_code)
            or not isinstance(address_id, str) or not MCD_ID.fullmatch(address_id)
            or not isinstance(be_code, str) or not MCD_BE_CODE.fullmatch(be_code)
            or not isinstance(store_name, str) or not 1 <= len(store_name) <= 80
            or not isinstance(address_text, str) or not 1 <= len(address_text) <= 160):
        raise FoodError(400, "麦当劳门店或配送地址无效。")
    items = raw.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise FoodError(400, "请选择 1–20 种麦当劳餐品。")
    checked, seen = [], set()
    for item in items:
        code, name = (item or {}).get("productCode"), (item or {}).get("name")
        quantity, price = (item or {}).get("quantity"), (item or {}).get("priceFen")
        if (not isinstance(item, dict) or not isinstance(code, str) or not MCD_ID.fullmatch(code)
                or code in seen or not isinstance(name, str) or not 1 <= len(name) <= 80
                or type(quantity) is not int or not 1 <= quantity <= 10
                or type(price) is not int or not 0 <= price <= 10_000_000):
            raise FoodError(400, "餐品必须来自菜单、不重复，数量为 1–10。")
        seen.add(code)
        checked.append({"productCode": code, "name": name, "quantity": quantity, "priceFen": price})
    amounts = {}
    for key in ("productFen", "deliveryFen", "payFen"):
        amount = raw.get(key)
        if type(amount) is not int or not 0 <= amount <= MAX_FEN:
            raise FoodError(400, "报价金额无效。")
        amounts[key] = amount
    if amounts["productFen"] != sum(x["priceFen"] * x["quantity"] for x in checked):
        raise FoodError(400, "餐品金额与报价合计不一致。")
    details = {"provider": "mcd", "storeCode": store_code, "addressId": address_id,
               "storeName": store_name, "addressText": address_text, "items": checked,
               "productFen": amounts["productFen"], "deliveryFen": amounts["deliveryFen"],
               "payFen": amounts["payFen"]}
    if be_code:
        details["beCode"] = be_code
    discount = raw.get("discountFen")
    if type(discount) is int and 0 <= discount <= amounts["productFen"]:
        details["discountFen"] = discount
    return details


def _sandbox_output(row: dict) -> dict:
    details = json.loads(row["details"])
    value = {"sandbox": True, "operationId": row["id"], "quoteId": row["quote_id"],
             "status": row["status"], "message": MESSAGES[row["status"]],
             "merchant": details["merchant"], "totalFen": details["totalFen"]}
    if row["order_id"]:
        value["orderId"] = row["order_id"]
    return value


def _mcd_output(row: dict) -> dict:
    details = json.loads(row["details"])
    value = {"provider": "mcd", "operationId": row["id"], "quoteId": row["quote_id"],
             "status": row["status"], "message": MCD_MESSAGES[row["status"]],
             "storeName": details["storeName"], "addressText": details["addressText"],
             "items": details["items"], "productFen": details["productFen"],
             "deliveryFen": details["deliveryFen"], "payFen": details["payFen"],
             "expiresAt": details.get("expiresAt", ""), "route": "#/order/" + row["quote_id"]}
    if row["order_id"]:
        value["orderId"] = row["order_id"]
    return value


class FoodService:
    """App-facing order service. Its caller owns explicit user confirmation.

    The ledger persists a stable operation per server-generated quote before
    sending an order. Sandbox sellers deduplicate across sends; the McDonald's
    provider instead snapshots recent orders before its single send and
    reconciles a lost reply by diffing that snapshot.
    """

    def __init__(self, db_path: str | Path, *, provider: FoodProvider | None = None,
                 timeout: float = 2.4, quote_ttl: float = 300,
                 clock: Callable[[], float] = time.time, executor: ReliableExecutor | None = None):
        if not isfinite(timeout) or not 0 < timeout <= 15 or not isfinite(quote_ttl) or not 0 < quote_ttl <= 3600:
            raise ValueError("外卖的超时或报价有效期无效。")
        self.ledger = Ledger(db_path)
        self.provider = provider if provider is not None else SandboxMerchant(db_path)
        self.timeout, self.quote_ttl = timeout, quote_ttl
        self.clock = clock
        self.executor = executor or ReliableExecutor()

    def _output(self, row: dict) -> dict:
        details = json.loads(row["details"])
        return _mcd_output(row) if details.get("provider") == "mcd" else _sandbox_output(row)

    async def _enrich(self, value: dict) -> dict:
        """Overlay live McDonald's order data for display; never changes status."""
        if value.get("provider") != "mcd" or not value.get("orderId"):
            return value
        live = await self.provider.live_order(value["orderId"])
        if not isinstance(live, dict):
            return value
        for key in ("orderStatus", "payUrl", "pickupCode", "lockerCode", "createTime"):
            if live.get(key) and not value.get(key):
                value[key] = live[key]
        if isinstance(live.get("products"), list) and not value.get("products"):
            value["products"] = live["products"]
        total = live.get("realTotalFen")
        if type(total) is not int:
            total = live.get("totalFen")
        if type(total) is int:
            value["totalFen"] = total
            if total != value.get("payFen"):
                value["amountMismatch"] = True
        return value

    async def menu(self, actor: str, keyword: str = "") -> dict:
        _actor(actor)
        if not getattr(self.provider, "sandbox", False):
            raise FoodError(409, "当前外卖服务不是模拟实验。")
        if not isinstance(keyword, str) or len(keyword) > 80:
            raise FoodError(400, "请输入 80 字以内的餐品关键词。")
        term = keyword.strip().casefold()
        return {"sandbox": True, "items": [dict(row) for row in MENU
                                           if not term or term in (row["name"] + " " + row["id"]).casefold()]}

    async def quote(self, actor: str, items: list[dict], address: str = DEMO_ADDRESS) -> dict:
        _actor(actor)
        if not getattr(self.provider, "sandbox", False):
            raise FoodError(409, "当前外卖服务不是模拟实验。")
        if address != DEMO_ADDRESS:
            raise FoodError(400, "实验只允许使用模拟酒店，不接收真实地址。")
        if not isinstance(items, list) or not 1 <= len(items) <= len(MENU):
            raise FoodError(400, "请选择至少一种测试餐品。")
        picked = {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise FoodError(400, "测试餐品格式不正确。")
            item_id, quantity = item["id"], item.get("quantity")
            if item_id not in MENU_BY_ID or item_id in picked or type(quantity) is not int or not 1 <= quantity <= 10:
                raise FoodError(400, "餐品必须来自测试菜单、不重复，数量为 1–10。")
            picked[item_id] = quantity
        server_items = [{"id": item_id, "name": MENU_BY_ID[item_id]["name"], "quantity": qty,
                         "priceFen": MENU_BY_ID[item_id]["priceFen"]} for item_id, qty in sorted(picked.items())]
        expires = self.clock() + self.quote_ttl
        details = {"sandbox": True, "quoteId": "quote_" + uuid4().hex, "merchant": MERCHANT,
                   "items": server_items, "totalFen": sum(x["priceFen"] * x["quantity"] for x in server_items),
                   "addressMasked": DEMO_ADDRESS, "expiresAt": _stamp(expires)}
        await asyncio.to_thread(self.ledger.quote, actor, details, expires)
        return details

    async def record_quote(self, actor: str, raw: dict) -> dict:
        """Validate and persist an agent-built McDonald's quote for later confirm."""
        _actor(actor)
        details = mcd_quote_details(raw)
        expires = self.clock() + self.quote_ttl
        details = {**details, "quoteId": "quote_" + uuid4().hex, "expiresAt": _stamp(expires)}
        await asyncio.to_thread(self.ledger.quote, actor, details, expires)
        return details

    async def prepare(self, actor: str, quote_id: str) -> dict:
        _actor(actor)
        _quote_id(quote_id)
        result, row = await asyncio.to_thread(self.ledger.prepare, actor, quote_id, self.clock())
        if result == "expired":
            raise FoodError(409, "报价已过期，请重新报价并确认。")
        if row is None:
            raise FoodError(404, "未找到本会话的报价。")
        return self._output(row)

    async def confirm(self, actor: str, quote_id: str) -> dict:
        operation = await self.prepare(actor, quote_id)
        return await self.execute(actor, operation["operationId"])

    def _verify(self, receipt: dict, operation_id: str, details: dict) -> str:
        return self.provider.verify_receipt(receipt, operation_id, details)

    @staticmethod
    def _merge_receipt(value: dict, receipt: dict | None) -> dict:
        """Carry display fields from a fresh provider receipt into the output.

        Persisted state stays minimal (status, order id, snapshot); the receipt
        enriches this response, and later status queries refresh it live.
        """
        if not isinstance(receipt, dict) or value.get("provider") != "mcd":
            return value
        for key in ("orderStatus", "payUrl", "pickupCode", "lockerCode", "createTime",
                    "products", "totalFen", "amountMismatch", "storeName"):
            if receipt.get(key) is not None:
                value[key] = receipt[key]
        return value

    async def execute(self, actor: str, operation_id: str) -> dict:
        _actor(actor)
        _op_id(operation_id)
        row, lease = await asyncio.to_thread(self.ledger.claim, actor, operation_id, self.clock(), self.timeout)
        if row is None:
            raise FoodError(404, "未找到本会话的订单操作。")
        if lease is None:
            return await self._status(actor, row)
        details = json.loads(row["details"])
        if fingerprint(details) != row["digest"]:
            raise FoodError(500, "订单参数校验失败。")

        async def trace(event: str, attempt: int, code: str) -> None:
            await asyncio.to_thread(self.ledger.trace, operation_id, self.clock(), event, attempt, code)

        snapshot = None
        if self.provider.supports_order_snapshot:
            # Snapshot before the first send: a lost reply is reconciled by
            # diffing, and the snapshot must survive a process restart.
            snapshot = await self.provider.recent_orders(actor)
            await asyncio.to_thread(self.ledger.set_snapshot, actor, operation_id, snapshot)

        fresh: dict = {}

        async def submit() -> str:
            receipt = await self.provider.create_order(actor, operation_id, details)
            fresh.update(receipt)
            return self._verify(receipt, operation_id, details)

        try:
            order_id = await self.executor.call(
                submit,
                budget=row["deadline_at"] - self.clock(),
                retry_safe=self.provider.supports_idempotent_create,
                trace=trace,
            )
        except ToolRejected:
            result = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "rejected", lease=lease)
        except ToolSendLost:
            # Every send provably failed to leave the machine: nothing was ordered.
            result = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "rejected", lease=lease)
        except ToolUncertain:
            result = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "unknown", lease=lease)
            remaining = min(getattr(self.provider, "reconcile_timeout", 0.8),
                            row["deadline_at"] - self.clock())
            if remaining > 0 and result["status"] == "unknown":
                return await self._reconcile(actor, result, remaining)
        else:
            result = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "placed", order_id=order_id)
        await trace("outcome", 0, result["status"])
        return self._merge_receipt(await self._enrich(self._output(result)), fresh or None)

    async def _reconcile(self, actor: str, row: dict, timeout: float = 0.8) -> dict:
        operation_id = row["id"]
        details = json.loads(row["details"])
        snapshot = json.loads(row["snapshot"]) if row.get("snapshot") else None
        found: dict | None = None
        try:
            receipt = await asyncio.wait_for(
                self.provider.find_order(actor, operation_id, details, snapshot), timeout)
            if receipt is not None:
                order_id = self._verify(receipt, operation_id, details)
                row = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "placed", order_id=order_id)
                found = receipt
            code = "placed" if receipt is not None else "not_found_yet"
        except (ToolUncertain, ToolRejected, OSError, asyncio.TimeoutError):
            code = "unavailable"
        await asyncio.to_thread(self.ledger.trace, operation_id, self.clock(), "reconcile", 0, code)
        return self._merge_receipt(await self._enrich(self._output(row)), found)

    async def _status(self, actor: str, row: dict) -> dict:
        if row["status"] == "confirming" and row["lease_until"] <= self.clock():
            row = await asyncio.to_thread(self.ledger.settle, actor, row["id"], "unknown", lease=row["lease_id"])
        if row["status"] == "unknown":
            return await self._reconcile(actor, row)
        return await self._enrich(self._output(row))

    async def status(self, actor: str, operation_id: str) -> dict:
        _actor(actor)
        _op_id(operation_id)
        row = await asyncio.to_thread(self.ledger.get, actor, operation_id)
        if row is None:
            raise FoodError(404, "未找到本会话的订单操作。")
        return await self._status(actor, row)

    async def status_for_quote(self, actor: str, quote_id: str) -> dict:
        _actor(actor)
        _quote_id(quote_id)
        quote, row = await asyncio.to_thread(self.ledger.by_quote, actor, quote_id)
        if row is not None:
            return await self._status(actor, row)
        if quote is None:
            raise FoodError(404, "未找到本会话的报价。")
        details = json.loads(quote["details"])
        if details.get("provider") == "mcd":
            return {"provider": "mcd", "quoteId": quote_id, "status": "awaiting_confirmation",
                    "message": MCD_MESSAGES["awaiting_confirmation"], "expiresAt": _stamp(quote["expires_at"]),
                    "storeName": details["storeName"], "addressText": details["addressText"],
                    "items": details["items"], "productFen": details["productFen"],
                    "deliveryFen": details["deliveryFen"], "payFen": details["payFen"],
                    "route": "#/order/" + quote_id}
        return {"sandbox": True, "quoteId": quote_id, "status": "awaiting_confirmation",
                "expiresAt": _stamp(quote["expires_at"]), "message": MESSAGES["awaiting_confirmation"]}
