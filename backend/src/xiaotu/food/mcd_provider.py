"""McDonald's order provider: single-send create-order with snapshot reconciliation.

The official MCP exposes no idempotency key, so duplicate prevention is ours:
- create-order is sent at most once per operation; the gateway only resends
  after a proven connect-phase loss, which cannot double-write;
- before the send we snapshot the account's recent order ids, and a lost or
  unusable reply is reconciled by diffing that snapshot — never by resending.
An identical standing order that predates the snapshot is never claimed.
"""
from __future__ import annotations

from xiaotu.execution import ToolRejected, ToolSendLost, ToolTransient, ToolUncertain
from xiaotu.tools.mcd import McdError, McdRefused, McdService

FAILURES = (McdError, McdRefused, ToolSendLost, ToolTransient, ToolUncertain)


class McdFoodProvider:
    supports_idempotent_create = False
    supports_order_snapshot = True
    reconcile_timeout = 3.0
    sandbox = False

    def __init__(self, service: McdService):
        self.service = service

    async def recent_orders(self, actor: str) -> list[str] | None:
        """Order ids visible before the send; None when the account is unreachable."""
        try:
            return [row["orderId"] for row in await self.service.order_list()]
        except FAILURES:
            return None

    async def create_order(self, actor: str, operation_id: str, details: dict) -> dict:
        try:
            receipt = await self.service.create_order(
                store_code=details["storeCode"], address_id=details["addressId"],
                items=[{"productCode": item["productCode"], "quantity": item["quantity"]}
                       for item in details["items"]],
                be_code=details.get("beCode", ""))
        except McdRefused as exc:
            # The tool ran and the business layer refused: no order exists.
            raise ToolRejected(409, "mcd_refused_" + exc.code) from None
        except McdError as exc:
            # Definitive pre-execution failures: auth, rate limit, arguments.
            raise ToolRejected(exc.status, "mcd_" + (exc.code or "error")) from None
        return self.receipt(receipt, operation_id, details)

    def receipt(self, receipt: dict, operation_id: str, details: dict) -> dict:
        """Wrap a normalized order view into the provider receipt contract."""
        total = receipt.get("realTotalFen")
        if type(total) is not int:
            total = receipt.get("totalFen")
        value = {"provider": "mcd", "operationId": operation_id, "orderId": receipt["orderId"],
                 "totalFen": total if type(total) is int else 0}
        if type(total) is int and total != details["payFen"]:
            # The order exists with its own pricing; surface the difference
            # instead of pretending the quote held.
            value["amountMismatch"] = True
        for key in ("orderStatus", "storeName", "payUrl", "pickupCode", "lockerCode", "createTime"):
            if receipt.get(key):
                value[key] = receipt[key]
        if isinstance(receipt.get("products"), list):
            value["products"] = receipt["products"]
        return value

    def verify_receipt(self, receipt: dict, operation_id: str, details: dict) -> str:
        if (not isinstance(receipt, dict) or receipt.get("provider") != "mcd"
                or receipt.get("operationId") != operation_id
                or not isinstance(receipt.get("orderId"), str)
                or type(receipt.get("totalFen")) is not int):
            raise ToolUncertain("invalid_receipt")
        return receipt["orderId"]

    async def find_order(self, actor: str, operation_id: str, details: dict,
                         snapshot: list[str] | None) -> dict | None:
        """Reconcile a lost reply by diffing the pre-send snapshot.

        Without a snapshot we do not guess: this account may have standing
        orders that look identical to the one we attempted.
        """
        if snapshot is None:
            return None
        try:
            orders = await self.service.order_list()
        except FAILURES:
            return None
        expected_store = details.get("storeName", "")
        candidates = []
        for order in orders:
            if order["orderId"] in snapshot:
                continue
            if expected_store and order.get("storeName") and order["storeName"] != expected_store:
                continue
            total = order.get("totalFen")
            if type(total) is int and total != details["payFen"]:
                continue
            candidates.append(order)
        if len(candidates) != 1:
            return None
        try:
            return self.receipt(await self.service.order_detail(candidates[0]["orderId"]),
                                operation_id, details)
        except FAILURES:
            return None

    async def live_order(self, order_id: str) -> dict | None:
        """Best-effort live refresh for display; never changes settlement."""
        try:
            return await self.service.order_detail(order_id)
        except FAILURES:
            return None
