from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from uuid import uuid4

from xiaotu.execution import ReliableExecutor, ToolRejected, ToolUncertain

from .provider import ACTOR, OPERATION, ORDER, QUOTE, FoodProvider, SandboxMerchant
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


class FoodError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _actor(actor: str) -> None:
    if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
        raise FoodError(403, "没有有效的外卖实验会话。")


def _quote_id(quote_id: str) -> None:
    if not isinstance(quote_id, str) or not QUOTE.fullmatch(quote_id):
        raise FoodError(404, "未找到本会话的测试报价。")


def _op_id(operation_id: str) -> None:
    if not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id):
        raise FoodError(404, "未找到本会话的测试操作。")


def _stamp(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _output(row: dict) -> dict:
    details = json.loads(row["details"])
    value = {"sandbox": True, "operationId": row["id"], "quoteId": row["quote_id"],
             "status": row["status"], "message": MESSAGES[row["status"]],
             "merchant": details["merchant"], "totalFen": details["totalFen"]}
    if row["order_id"]:
        value["orderId"] = row["order_id"]
    return value


class FoodService:
    """App-facing sandbox order service. Its caller owns explicit user confirmation.

    The ledger persists a stable operation per server-generated quote before
    sending an order. The mock seller independently deduplicates across sends.
    """

    def __init__(self, db_path: str | Path, *, provider: FoodProvider | None = None,
                 timeout: float = 2.4, quote_ttl: float = 300,
                 clock: Callable[[], float] = time.time, executor: ReliableExecutor | None = None):
        if not isfinite(timeout) or not 0 < timeout <= 15 or not isfinite(quote_ttl) or not 0 < quote_ttl <= 3600:
            raise ValueError("测试外卖的超时或报价有效期无效。")
        self.ledger = Ledger(db_path)
        self.provider = provider if provider is not None else SandboxMerchant(db_path)
        self.timeout, self.quote_ttl = timeout, quote_ttl
        self.clock = clock
        self.executor = executor or ReliableExecutor()

    async def menu(self, actor: str, keyword: str = "") -> dict:
        _actor(actor)
        if not isinstance(keyword, str) or len(keyword) > 80:
            raise FoodError(400, "请输入 80 字以内的餐品关键词。")
        term = keyword.strip().casefold()
        return {"sandbox": True, "items": [dict(row) for row in MENU
                                           if not term or term in (row["name"] + " " + row["id"]).casefold()]}

    async def quote(self, actor: str, items: list[dict], address: str = DEMO_ADDRESS) -> dict:
        _actor(actor)
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

    async def prepare(self, actor: str, quote_id: str) -> dict:
        _actor(actor)
        _quote_id(quote_id)
        result, row = await asyncio.to_thread(self.ledger.prepare, actor, quote_id, self.clock())
        if result == "expired":
            raise FoodError(409, "测试报价已过期，请重新报价并确认。")
        if row is None:
            raise FoodError(404, "未找到本会话的测试报价。")
        return _output(row)

    async def confirm(self, actor: str, quote_id: str) -> dict:
        operation = await self.prepare(actor, quote_id)
        return await self.execute(actor, operation["operationId"])

    @staticmethod
    def _verify(receipt: dict, operation_id: str, amount: int) -> str:
        if (not isinstance(receipt, dict) or receipt.get("sandbox") is not True or
                receipt.get("operationId") != operation_id or type(receipt.get("totalFen")) is not int or
                receipt.get("totalFen") != amount or not isinstance(receipt.get("orderId"), str) or
                not ORDER.fullmatch(receipt["orderId"])):
            raise ToolUncertain("invalid_receipt")
        return receipt["orderId"]

    async def execute(self, actor: str, operation_id: str) -> dict:
        _actor(actor)
        _op_id(operation_id)
        row, lease = await asyncio.to_thread(self.ledger.claim, actor, operation_id, self.clock(), self.timeout)
        if row is None:
            raise FoodError(404, "未找到本会话的测试操作。")
        if lease is None:
            return await self._status(actor, row)
        details = json.loads(row["details"])
        if fingerprint(details) != row["digest"]:
            raise FoodError(500, "测试订单参数校验失败。")

        async def trace(event: str, attempt: int, code: str) -> None:
            await asyncio.to_thread(self.ledger.trace, operation_id, self.clock(), event, attempt, code)

        async def submit() -> str:
            receipt = await self.provider.create_order(actor, operation_id, details)
            return self._verify(receipt, operation_id, details["totalFen"])

        try:
            order_id = await self.executor.call(
                submit,
                budget=row["deadline_at"] - self.clock(),
                retry_safe=self.provider.supports_idempotent_create,
                trace=trace,
            )
        except ToolRejected:
            result = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "rejected", lease=lease)
        except ToolUncertain:
            result = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "unknown", lease=lease)
            remaining = min(0.8, row["deadline_at"] - self.clock())
            if remaining > 0 and result["status"] == "unknown":
                return await self._reconcile(actor, result, remaining)
        else:
            result = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "placed", order_id=order_id)
        await trace("outcome", 0, result["status"])
        return _output(result)

    async def _reconcile(self, actor: str, row: dict, timeout: float = 0.8) -> dict:
        operation_id = row["id"]
        try:
            receipt = await asyncio.wait_for(self.provider.find_order(actor, operation_id), timeout)
            if receipt is not None:
                order_id = self._verify(receipt, operation_id, json.loads(row["details"])["totalFen"])
                row = await asyncio.to_thread(self.ledger.settle, actor, operation_id, "placed", order_id=order_id)
            code = "placed" if receipt is not None else "not_found_yet"
        except (ToolUncertain, ToolRejected, OSError, asyncio.TimeoutError):
            code = "unavailable"
        await asyncio.to_thread(self.ledger.trace, operation_id, self.clock(), "reconcile", 0, code)
        return _output(row)

    async def _status(self, actor: str, row: dict) -> dict:
        if row["status"] == "confirming" and row["lease_until"] <= self.clock():
            row = await asyncio.to_thread(self.ledger.settle, actor, row["id"], "unknown", lease=row["lease_id"])
        if row["status"] == "unknown":
            return await self._reconcile(actor, row)
        return _output(row)

    async def status(self, actor: str, operation_id: str) -> dict:
        _actor(actor)
        _op_id(operation_id)
        row = await asyncio.to_thread(self.ledger.get, actor, operation_id)
        if row is None:
            raise FoodError(404, "未找到本会话的测试操作。")
        return await self._status(actor, row)

    async def status_for_quote(self, actor: str, quote_id: str) -> dict:
        _actor(actor)
        _quote_id(quote_id)
        quote, row = await asyncio.to_thread(self.ledger.by_quote, actor, quote_id)
        if row is not None:
            return await self._status(actor, row)
        if quote is None:
            raise FoodError(404, "未找到本会话的测试报价。")
        return {"sandbox": True, "quoteId": quote_id, "status": "awaiting_confirmation",
                "expiresAt": _stamp(quote["expires_at"]), "message": MESSAGES["awaiting_confirmation"]}
