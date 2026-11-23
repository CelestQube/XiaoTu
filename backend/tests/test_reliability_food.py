from __future__ import annotations

import asyncio
import socket
from uuid import uuid4

import httpx
import pytest
import uvicorn

from xiaotu.execution import ReliableExecutor, ToolRejected, ToolTransient, ToolUncertain
from xiaotu.food import DEMO_ADDRESS, FoodError, FoodService, HttpFoodProvider
from xiaotu.food.benchmark import STRATEGIES, drops_response, run_case
from xiaotu.food.provider import SandboxMerchant, verify_sandbox_receipt
from xiaotu.food.sandbox import create_app


ACTOR = "lab-user"
ITEMS = [{"id": "beef-rice", "quantity": 2}, {"id": "soy-milk", "quantity": 1}]
FAST_RETRY = ReliableExecutor(attempt_timeout=0.2, initial_backoff=0, maximum_backoff=0)


def check_status(error, status):
    assert error.value.status == status


def test_quote_does_not_authorize_and_pending_can_resume_without_duplicate(tmp_path):
    async def scenario():
        now = 1_800_001_234.0
        def clock():
            return now
        path = tmp_path / "food.sqlite3"
        food = FoodService(path, clock=clock, quote_ttl=60)
        assert (await food.menu(ACTOR, "豆浆"))["items"][0]["id"] == "soy-milk"
        assert (await food.menu(ACTOR, "无此餐品"))["items"] == []
        with pytest.raises(FoodError) as invalid:
            await food.quote(ACTOR, ITEMS, "实际地址 13800000000")
        check_status(invalid, 400)
        quote = await food.quote(ACTOR, ITEMS)
        quote_id = quote["quoteId"]
        assert quote["addressMasked"] == DEMO_ADDRESS and quote["totalFen"] == 6280
        assert (await food.status_for_quote(ACTOR, quote_id))["status"] == "awaiting_confirmation"
        with pytest.raises(FoodError) as wrong_user:
            await food.confirm("other-user", quote_id)
        check_status(wrong_user, 404)
        assert food.provider.count() == 0
        prepared = await food.prepare(ACTOR, quote_id)
        op = prepared["operationId"]
        assert prepared["status"] == "pending" and food.provider.count() == 0
        # A new app can resume the prepared action with its original ID.
        resumed = FoodService(path, clock=clock)
        result = await resumed.execute(ACTOR, op)
        assert result["status"] == "placed" and result["orderId"].startswith("test_")
        now += 65
        assert await resumed.confirm(ACTOR, quote_id) == result  # replay, not a new expired quote
        assert await resumed.status_for_quote(ACTOR, quote_id) == result
        assert resumed.provider.count() == 1

    asyncio.run(scenario())


def test_duplicate_confirmations_share_one_order_and_new_quote_is_new_intent(tmp_path):
    async def scenario():
        food = FoodService(tmp_path / "food.sqlite3")
        quote = (await food.quote(ACTOR, ITEMS))["quoteId"]
        results = await asyncio.gather(*(food.confirm(ACTOR, quote) for _ in range(6)))
        assert len({row["operationId"] for row in results}) == 1
        final = await food.status_for_quote(ACTOR, quote)
        assert final["status"] == "placed"
        assert food.provider.count() == 1
        another = (await food.quote(ACTOR, ITEMS))["quoteId"]
        new = await food.confirm(ACTOR, another)
        assert new["orderId"] != final["orderId"] and food.provider.count() == 2

    asyncio.run(scenario())


def test_expired_or_invalid_quote_does_not_write(tmp_path):
    async def scenario():
        now = 1_800_001_234.0
        def clock():
            return now
        food = FoodService(tmp_path / "food.sqlite3", clock=clock, quote_ttl=1)
        for qty in (-1, 0, 11, True, 1.4, "3"):
            with pytest.raises(FoodError):
                await food.quote(ACTOR, [{"id": "soy-milk", "quantity": qty}])
        quote = (await food.quote(ACTOR, [{"id": "soy-milk", "quantity": 10}]))["quoteId"]
        now += 2
        with pytest.raises(FoodError) as expired:
            await food.confirm(ACTOR, quote)
        check_status(expired, 409)
        assert food.provider.count() == 0

    asyncio.run(scenario())


def test_sandbox_http_contract_deduplicates_and_checks_same_key_different_body(tmp_path):
    async def scenario():
        merchant = create_app(tmp_path / "vendor.sqlite3")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=merchant), trust_env=False) as client:
            provider = HttpFoodProvider("http://127.0.0.1:3221", client=client)
            food = FoodService(tmp_path / "app.sqlite3", provider=provider)
            details = await food.quote(ACTOR, ITEMS)
            result = await food.confirm(ACTOR, details["quoteId"])
            same = await provider.create_order(ACTOR, result["operationId"], details)
            assert same["orderId"] == result["orderId"]
            alternate = await food.quote(ACTOR, [{"id": "soy-milk", "quantity": 5}])
            with pytest.raises(ToolRejected) as conflict:
                await provider.create_order(ACTOR, result["operationId"], alternate)
            check_status(conflict, 409)
            assert await provider.find_order("other-user", result["operationId"]) is None
            assert merchant.state.vendor.count() == 1

    for unsafe in ("https://127.0.0.1:3221", "http://localhost:3221", "http://127.0.0.1@outside.test:3221",
                   "http://127.0.0.1:3221?key=s"):
        with pytest.raises(ValueError):
            HttpFoodProvider(unsafe)
    asyncio.run(scenario())


def test_unknown_looks_up_original_operation_after_restart_and_cannot_resubmit(tmp_path):
    class UnknownAfterCommit:
        supports_idempotent_create = True
        supports_order_snapshot = False
        sandbox = True
        reconcile_timeout = 0.8
        verify_receipt = staticmethod(verify_sandbox_receipt)

        def __init__(self, merchant):
            self.merchant, self.creates, self.available = merchant, 0, False

        async def create_order(self, actor, operation_id, details):
            self.creates += 1
            await self.merchant.create_order(actor, operation_id, details)
            raise ToolTransient("network")

        async def find_order(self, actor, operation_id, details=None, snapshot=None):
            if not self.available:
                raise ToolTransient("network")
            return await self.merchant.find_order(actor, operation_id)

        async def recent_orders(self, actor):
            return None

        async def live_order(self, order_id):
            return None

    async def scenario():
        merchant = SandboxMerchant(tmp_path / "vendor.sqlite3")
        provider = UnknownAfterCommit(merchant)
        path = tmp_path / "app.sqlite3"
        service = FoodService(path, provider=provider, executor=FAST_RETRY)
        quote = await service.quote(ACTOR, ITEMS)
        first = await service.confirm(ACTOR, quote["quoteId"])
        assert first["status"] == "unknown"
        assert provider.creates == 3 and merchant.count() == 1
        provider.available = True
        recovered = await FoodService(path, provider=provider).status_for_quote(ACTOR, quote["quoteId"])
        assert recovered["status"] == "placed" and recovered["operationId"] == first["operationId"]
        assert provider.creates == 3 and merchant.count() == 1

    asyncio.run(scenario())


def test_restart_during_in_flight_commit_recovers_same_order(tmp_path):
    class CommitThenCancel:
        supports_idempotent_create = True
        supports_order_snapshot = False
        sandbox = True
        reconcile_timeout = 0.8
        verify_receipt = staticmethod(verify_sandbox_receipt)

        def __init__(self, merchant):
            self.merchant, self.creates = merchant, 0

        async def create_order(self, actor, operation_id, details):
            self.creates += 1
            await self.merchant.create_order(actor, operation_id, details)
            raise asyncio.CancelledError

        async def find_order(self, actor, operation_id, details=None, snapshot=None):
            return await self.merchant.find_order(actor, operation_id)

        async def recent_orders(self, actor):
            return None

        async def live_order(self, order_id):
            return None

    async def scenario():
        now = 1_800_001_234.0
        def clock():
            return now
        merchant = SandboxMerchant(tmp_path / "vendor.sqlite3")
        provider = CommitThenCancel(merchant)
        path = tmp_path / "app.sqlite3"
        food = FoodService(path, provider=provider, clock=clock)
        quote = await food.quote(ACTOR, ITEMS)
        with pytest.raises(asyncio.CancelledError):
            await food.confirm(ACTOR, quote["quoteId"])
        persisted = await food.status_for_quote(ACTOR, quote["quoteId"])
        assert persisted["status"] == "confirming"
        now += 3
        restarted = FoodService(path, provider=provider, clock=clock)
        result = await restarted.status_for_quote(ACTOR, quote["quoteId"])
        assert result["status"] == "placed"
        assert result["operationId"] == persisted["operationId"]
        assert provider.creates == merchant.count() == 1

    asyncio.run(scenario())


def test_deadline_does_not_repeat_non_idempotent_tool_or_unapproved_retry_after():
    async def scenario():
        traces = []
        async def trace(*args):
            traces.append(args)
        async def response():
            raise ToolTransient("http_503", retry_after=10)
        for supported in (False, True):
            with pytest.raises(ToolUncertain, match="retry_or_deadline_exhausted"):
                await FAST_RETRY.call(response, budget=0.1, retry_safe=supported, trace=trace)
        assert [event for event, _, _ in traces].count("attempt") == 2
        assert all(event != "retry" for event, _, _ in traces)

    asyncio.run(scenario())


def test_real_loopback_cannot_acknowledge_truncated_response(tmp_path):
    async def scenario(bound_socket):
        merchant = create_app(tmp_path / "tcp_vendor.sqlite3", drop_first_response=True)
        server = uvicorn.Server(uvicorn.Config(merchant, lifespan="off", log_config=None,
                                               access_log=False, timeout_graceful_shutdown=2))
        serving = asyncio.create_task(server.serve(sockets=[bound_socket]))
        try:
            async def wait_for_server():
                while not server.started and not serving.done():
                    await asyncio.sleep(0.01)
                assert server.started
            await asyncio.wait_for(wait_for_server(), 4)
            port = bound_socket.getsockname()[1]
            food = FoodService(tmp_path / "tcp_app.sqlite3",
                               provider=HttpFoodProvider(f"http://127.0.0.1:{port}"))
            quote = await food.quote(ACTOR, ITEMS)
            result = await food.confirm(ACTOR, quote["quoteId"])
            assert result["status"] == "placed" and merchant.state.vendor.count() == 1
            trace = food.ledger.trace_rows(result["operationId"])
            assert [x["code"] for x in trace if x["event"] == "response"] == ["network", "ok"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 4)

    with socket.socket() as bound_socket:
        bound_socket.bind(("127.0.0.1", 0))
        asyncio.run(scenario(bound_socket))


def test_comparison_after_commit_response_drop(tmp_path):
    """Micro experiment: same fault, identical business input, three transports/policies."""

    async def scenario():
        measured = []
        for strategy in ("no_retry", "blind_retry", "reliable_gateway"):
            fake_app = create_app(tmp_path / (strategy + "_vendor.sqlite3"))
            wrapped = httpx.ASGITransport(app=fake_app)
            sends = 0

            async def lose_after_commit(req):
                nonlocal sends
                reply = await wrapped.handle_async_request(req)
                if req.method == "POST":
                    sends += 1
                    if sends == 1:
                        assert reply.status_code == 201 and fake_app.state.vendor.count() == 1
                        await reply.aclose()
                        raise httpx.ReadError("fixture: response lost after commit", request=req)
                return reply

            async with httpx.AsyncClient(transport=httpx.MockTransport(lose_after_commit), trust_env=False) as client:
                transport = HttpFoodProvider("http://127.0.0.1:3221", client=client)
                svc = FoodService(tmp_path / (strategy + "_app.sqlite3"), provider=transport, executor=FAST_RETRY)
                quote = await svc.quote(ACTOR, ITEMS)
                if strategy == "reliable_gateway":
                    result = await svc.confirm(ACTOR, quote["quoteId"])
                    assert result["status"] == "placed"
                    saved = await transport.find_order(ACTOR, result["operationId"])
                    assert saved["orderId"] == result["orderId"]
                    assert any(row["event"] == "retry" for row in svc.ledger.trace_rows(result["operationId"]))
                    returned = True
                else:
                    with pytest.raises(ToolTransient):
                        await transport.create_order(ACTOR, "op_" + uuid4().hex, quote)
                    returned = False
                    if strategy == "blind_retry":
                        # Deliberately lacks the stable key of the reliable gateway.
                        await transport.create_order(ACTOR, "op_" + uuid4().hex, quote)
                        returned = True
                measured.append((strategy, sends, fake_app.state.vendor.count(), returned))
        assert measured == [
            ("no_retry", 1, 1, False),
            ("blind_retry", 2, 2, True),
            ("reliable_gateway", 2, 1, True),
        ]
        for strategy, requests, writes, returned in measured:
            print(f"{strategy}: requests={requests}, commits={writes}, receipt={returned}")

    asyncio.run(scenario())


def test_matrix_boundary_rates_and_paired_schedule(tmp_path):
    schedule = [(task, attempt) for task in range(1, 50) for attempt in range(1, 4)]
    assert [drops_response(17, task, attempt, 20) for task, attempt in schedule] == [
        drops_response(17, task, attempt, 20) for task, attempt in reversed(schedule)
    ][::-1]
    assert all(not drops_response(17, task, attempt, 10) or drops_response(17, task, attempt, 30)
               for task, attempt in schedule)

    async def scenario():
        for strategy in STRATEGIES:
            clean = await run_case(tmp_path / f"{strategy}_clean", seed=17, tasks=1, loss=0, strategy=strategy)
            assert (clean["success"], clean["duplicates"], clean["attempts"]) == (1, 0, 1)
        bad = [await run_case(tmp_path / f"{strategy}_bad", seed=17, tasks=1, loss=100, strategy=strategy)
               for strategy in STRATEGIES]
        assert [(r["success"], r["duplicates"], r["attempts"], r["requests"]) for r in bad] == [
            (0, 0, 1, 1), (0, 1, 3, 3), (1, 0, 3, 4),
        ]

    asyncio.run(scenario())
