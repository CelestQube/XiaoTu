"""Pairwise local experiment: lose HTTP responses *after* simulated order commit."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import tempfile
import time
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx

from xiaotu.execution import ReliableExecutor, ToolTransient

from .provider import HttpFoodProvider
from .sandbox import create_app
from .service import FoodService

ACTOR = "benchmark-user"
STRATEGIES = ("no_retry", "blind_retry", "reliable_gateway")
DEADLINE = 2.4
MAX_SENDS = 3
ATTEMPT_TIMEOUT = 0.8


def drops_response(seed: int, task: int, attempt: int, loss: int) -> bool:
    """No strategy name or request-arrival order in the seeded fault key."""
    token = f"{seed}:food:{task}:{attempt}".encode("ascii")
    return int.from_bytes(sha256(token).digest()[:8]) % 10_000 < loss * 100


async def run_case(root: Path, *, seed: int, tasks: int, loss: int, strategy: str) -> dict:
    if strategy not in STRATEGIES or not 0 <= loss <= 100 or tasks < 1:
        raise ValueError("无效的实验组配置。")
    merchant = create_app(root / "seller.sqlite3")
    db = merchant.state.vendor
    mock_http = httpx.ASGITransport(app=merchant)
    task_id = post_for_task = 0
    sends = posts = losses = commits = completed = duplicates = receipts = unknown = 0
    durations: list[float] = []

    async def fault_proxy(request: httpx.Request) -> httpx.Response:
        nonlocal sends, posts, post_for_task, losses
        sends += 1
        response = await mock_http.handle_async_request(request)
        if request.method == "POST":
            posts += 1
            post_for_task += 1
            if response.status_code == 201 and drops_response(seed, task_id, post_for_task, loss):
                # The sandbox returns 201 only after its transaction is committed.
                losses += 1
                await response.aclose()
                raise httpx.ReadError("benchmark: simulated reply loss after commit", request=request)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(fault_proxy), trust_env=False) as client:
        provider = HttpFoodProvider("http://127.0.0.1:3221", client=client)
        food = FoodService(root / "app.sqlite3", provider=provider, timeout=DEADLINE,
                           executor=ReliableExecutor(attempt_timeout=ATTEMPT_TIMEOUT, max_attempts=MAX_SENDS))
        for task_id in range(1, tasks + 1):
            post_for_task = 0
            quote = await food.quote(ACTOR, [{"id": "beef-rice", "quantity": 1}])
            start_count = db.count()
            start = time.perf_counter()
            acknowledged = False
            if strategy == "reliable_gateway":
                reply = await food.confirm(ACTOR, quote["quoteId"])
                acknowledged = reply["status"] == "placed" and bool(reply.get("orderId"))
            else:
                # Blind retries deliberately do not retain an operation key, so
                # the very same vendor can exhibit a duplicate side effect.
                for _ in range(1 if strategy == "no_retry" else MAX_SENDS):
                    left = min(ATTEMPT_TIMEOUT, DEADLINE - (time.perf_counter() - start))
                    if left <= 0:
                        break
                    try:
                        response = await asyncio.wait_for(
                            provider.create_order(ACTOR, "op_" + uuid4().hex, quote), left)
                    except (ToolTransient, TimeoutError):
                        continue
                    acknowledged = bool(response.get("orderId"))
                    break
            duration = time.perf_counter() - start
            durations.append(duration)
            changed = db.count() - start_count
            commits += changed
            duplicates += changed > 1
            receipts += acknowledged
            unknown += not acknowledged
            completed += acknowledged and changed == 1 and duration <= DEADLINE

    p95 = sorted(durations)[math.ceil(tasks * 0.95) - 1]
    return {
        "scenario": "post_commit_response_loss", "seed": seed, "loss_pct": loss, "strategy": strategy,
        "tasks": tasks, "success": completed, "success_rate": round(completed / tasks, 6),
        "duplicates": duplicates, "duplicate_rate": round(duplicates / tasks, 6),
        "receipts": receipts, "unknown": unknown, "order_commits": commits,
        "attempts": posts, "requests": sends, "dropped_responses": losses,
        "mean_attempts": round(posts / tasks, 6), "p95_ms": round(p95 * 1000, 3),
    }


async def _matrix(root: Path, tasks: int, seeds: list[int], loss: list[int]):
    for run, seed in enumerate(seeds):
        for case, probability in enumerate(loss):
            for strategy in STRATEGIES:
                yield await run_case(root / f"{run}_{case}_{strategy}", seed=seed, tasks=tasks,
                                     loss=probability, strategy=strategy)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="小途本地可靠工具实验：用成对故障样本比较三种策略，输出 JSONL（不访问真实外卖）。"
    )
    parser.add_argument("--tasks", type=int, default=12, help="每组逻辑确认数（默认 12）")
    parser.add_argument("--seeds", type=int, nargs="+", default=[17], help="配对随机种子（默认 17）")
    parser.add_argument("--loss", type=int, nargs="+", default=[0, 10, 20, 30],
                        help="提交后响应丢失百分比（默认 0/10/20/30）")
    args = parser.parse_args()
    if not 1 <= args.tasks <= 1000 or len(args.seeds) > 12 or len(args.loss) > 11 or any(
        rate not in range(101) for rate in args.loss
    ):
        parser.error("tasks 必须为 1–1000；最多 12 个种子和 11 个丢包率；丢包率为 0–100。")

    async def run(root: Path) -> None:
        async for row in _matrix(root, args.tasks, args.seeds, args.loss):
            print(json.dumps(row, ensure_ascii=False, separators=(",", ":")), flush=True)

    with tempfile.TemporaryDirectory(prefix="xiaotu-benchmark-") as tmp:
        asyncio.run(run(Path(tmp)))


if __name__ == "__main__":
    main()
