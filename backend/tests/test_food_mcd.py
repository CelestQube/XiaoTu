"""Exactly-once McDonald's ordering under weak networks: the gateway contract.

The official MCP has no idempotency key, so these tests pin the three
weak-network outcomes: a definitively refused call, a request lost before
delivery (safe resend), and a reply lost after commit (reconcile from the
pre-send snapshot — never a second send).
"""
from __future__ import annotations

import asyncio

import pytest

from xiaotu.execution import ReliableExecutor, ToolSendLost, ToolTransient
from xiaotu.food import FoodError, FoodService, McdFoodProvider, mcd_quote_details
from xiaotu.tools.mcd import McdError, McdRefused

ACTOR = "mcd-user"
FAST_RETRY = ReliableExecutor(attempt_timeout=0.2, initial_backoff=0, maximum_backoff=0)


def quote_payload(**overrides) -> dict:
    value = {"provider": "mcd", "storeCode": "STORE01", "beCode": "BE1", "addressId": "301",
             "storeName": "麦当劳南京西路店", "addressText": "上海市静安区某酒店前台",
             "items": [{"productCode": "M1", "name": "巨无霸", "quantity": 2, "priceFen": 2200},
                       {"productCode": "M2", "name": "薯条", "quantity": 1, "priceFen": 1300}],
             "productFen": 5700, "deliveryFen": 900, "payFen": 6600}
    value.update(overrides)
    return value


class FakeMcdService:
    """Duck-typed McdService: order book plus per-call fault injection."""

    def __init__(self, quote: dict):
        self.quote = quote
        self.orders: dict[str, dict] = {}
        self.counter = 0
        self.create_calls = 0
        self.list_calls = 0
        self.create_faults: list = []   # (commit_before, exception), consumed per send
        self.list_faults: list = []     # exception or None, consumed per order_list call
        self.total_override: int | None = None

    def commit_order(self) -> dict:
        self.counter += 1
        order_id = f"MCD{self.counter:04d}"
        total = self.quote["payFen"] if self.total_override is None else self.total_override
        self.orders[order_id] = {
            "orderId": order_id, "orderStatus": "待支付", "storeName": self.quote["storeName"],
            "totalFen": total, "realTotalFen": total,
            "products": [{"name": item["name"], "quantity": item["quantity"], "priceFen": item["priceFen"]}
                         for item in self.quote["items"]],
            "payUrl": "https://pay.example/" + order_id, "createTime": "2026-11-20 12:00:00"}
        return self.orders[order_id]

    async def create_order(self, *, store_code, address_id, items, be_code=""):
        self.create_calls += 1
        if self.create_faults:
            commit_first, fault = self.create_faults.pop(0)
            if commit_first:
                self.commit_order()
            raise fault
        return dict(self.commit_order())

    async def order_list(self):
        self.list_calls += 1
        if self.list_faults:
            fault = self.list_faults.pop(0)
            if fault is not None:
                raise fault
        return [{"orderId": o["orderId"], "orderStatus": o["orderStatus"], "storeName": o["storeName"],
                 "totalFen": o["totalFen"], "createTime": o["createTime"]} for o in self.orders.values()]

    async def order_detail(self, order_id):
        return dict(self.orders[order_id]) if order_id in self.orders else None


def make_service(tmp_path, quote, *, executor=FAST_RETRY, clock=None):
    fake = FakeMcdService(quote)
    options = {"executor": executor}
    if clock is not None:
        options["clock"] = clock
    service = FoodService(tmp_path / "mcd.sqlite3", provider=McdFoodProvider(fake), **options)
    return service, fake


async def record(service, payload=None):
    return await service.record_quote(ACTOR, payload or quote_payload())


async def test_reply_lost_after_commit_reconciles_from_snapshot_exactly_once(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    fake.create_faults = [(True, ToolTransient("response_lost"))]
    result = await service.confirm(ACTOR, quote["quoteId"])
    assert result["status"] == "placed"
    assert result["orderId"] == "MCD0001"
    assert result["payUrl"] == "https://pay.example/MCD0001"
    assert result["route"] == "#/order/" + quote["quoteId"]
    # One send, one committed order: the receipt came from reconciliation.
    assert fake.create_calls == 1 and len(fake.orders) == 1


async def test_reconcile_blocked_now_recovers_after_restart_without_resend(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    fake.create_faults = [(True, ToolTransient("response_lost"))]
    # Snapshot succeeds, the first reconciliation cannot reach the account.
    fake.list_faults = [None, McdError(503, "暂时无法连接麦当劳 MCP。", "MCD_NETWORK")]
    first = await service.confirm(ACTOR, quote["quoteId"])
    assert first["status"] == "unknown" and len(fake.orders) == 1
    # A new process reconnects to the same account: no resend, only a lookup.
    restarted = FoodService(tmp_path / "mcd.sqlite3", provider=McdFoodProvider(fake), executor=FAST_RETRY)
    recovered = await restarted.status_for_quote(ACTOR, quote["quoteId"])
    assert recovered["status"] == "placed" and recovered["orderId"] == "MCD0001"
    assert fake.create_calls == 1 and len(fake.orders) == 1


async def test_request_lost_before_delivery_is_resent_and_still_one_order(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    fake.create_faults = [(False, ToolSendLost("connect_failed"))]
    result = await service.confirm(ACTOR, quote["quoteId"])
    assert result["status"] == "placed" and result["orderId"] == "MCD0001"
    # Two sends, one order: the first send provably never arrived.
    assert fake.create_calls == 2 and len(fake.orders) == 1
    events = [(row["event"], row["code"]) for row in service.ledger.trace_rows(result["operationId"])]
    assert [code for event, code in events if event == "response"] == ["connect_failed", "ok"]
    assert [code for event, code in events if event == "outcome"] == ["placed"]


async def test_all_sends_lost_is_a_definitive_rejection_not_unknown(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    fake.create_faults = [(False, ToolSendLost("connect_failed")) for _ in range(3)]
    result = await service.confirm(ACTOR, quote["quoteId"])
    assert result["status"] == "rejected" and "orderId" not in result
    assert fake.create_calls == 3 and fake.orders == {}


async def test_concurrent_confirmations_share_one_operation_and_one_order(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    results = await asyncio.gather(*(service.confirm(ACTOR, quote["quoteId"]) for _ in range(6)))
    assert len({row["operationId"] for row in results}) == 1
    final = await service.status_for_quote(ACTOR, quote["quoteId"])
    assert final["status"] == "placed"
    assert fake.create_calls == 1 and len(fake.orders) == 1


async def test_snapshot_keeps_standing_identical_order_from_being_claimed(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    # An identical order already sat on the account before we attempted ours.
    standing = fake.commit_order()
    fake.create_faults = [(True, ToolTransient("response_lost"))]
    result = await service.confirm(ACTOR, quote["quoteId"])
    assert result["status"] == "placed"
    assert result["orderId"] == "MCD0002" and result["orderId"] != standing["orderId"]
    assert len(fake.orders) == 2 and fake.create_calls == 1


async def test_without_a_snapshot_reconcile_does_not_guess(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    # The pre-send snapshot cannot be read, so matching is impossible: the
    # operation must stay honestly unknown instead of claiming any order.
    fake.list_faults = [McdError(503, "暂时无法连接麦当劳 MCP。", "MCD_NETWORK")]
    fake.create_faults = [(True, ToolTransient("response_lost"))]
    result = await service.confirm(ACTOR, quote["quoteId"])
    assert result["status"] == "unknown" and len(fake.orders) == 1
    later = await service.status_for_quote(ACTOR, quote["quoteId"])
    assert later["status"] == "unknown"
    assert fake.create_calls == 1 and len(fake.orders) == 1


async def test_business_refusal_and_amount_mismatch_settle_visibly(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    quote = await record(service)
    fake.create_faults = [(False, McdRefused("STORE_CLOSED", "门店休息中"))]
    refused = await service.confirm(ACTOR, quote["quoteId"])
    assert refused["status"] == "rejected" and fake.orders == {}

    other, other_fake = make_service(tmp_path, quote_payload())
    other_quote = await record(other)
    other_fake.total_override = 7100
    mismatch = await other.confirm(ACTOR, other_quote["quoteId"])
    assert mismatch["status"] == "placed" and mismatch["amountMismatch"] is True
    assert mismatch["totalFen"] == 7100


async def test_quote_validation_and_session_isolation(tmp_path):
    service, fake = make_service(tmp_path, quote_payload())
    with pytest.raises(FoodError):
        await service.record_quote(ACTOR, {"provider": "mcd"})
    with pytest.raises(FoodError):
        await service.record_quote(ACTOR, quote_payload(productFen=9999))
    with pytest.raises(FoodError):
        await service.record_quote(ACTOR, quote_payload(
            items=[{"productCode": "M1", "name": "巨无霸", "quantity": 2, "priceFen": 2200},
                   {"productCode": "M1", "name": "巨无霸", "quantity": 1, "priceFen": 2200}]))
    with pytest.raises(FoodError):
        await service.record_quote(ACTOR, quote_payload(payFen="6600"))
    quote = await service.record_quote(ACTOR, quote_payload())
    assert quote["quoteId"].startswith("quote_") and quote["expiresAt"]
    with pytest.raises(FoodError) as stranger:
        await service.status_for_quote("someone-else", quote["quoteId"])
    assert stranger.value.status == 404


async def test_expired_mcd_quote_never_reaches_the_gateway(tmp_path):
    now = 1_800_001_234.0

    def clock():
        return now

    service, fake = make_service(tmp_path, quote_payload(), clock=clock)
    quote = await service.record_quote(ACTOR, quote_payload())
    now += 400
    with pytest.raises(FoodError) as expired:
        await service.confirm(ACTOR, quote["quoteId"])
    assert expired.value.status == 409 and fake.create_calls == 0
