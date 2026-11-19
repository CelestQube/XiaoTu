"""Opt-in loopback HTTP merchant. Run only in development or lab profiles."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from xiaotu.execution import ToolRejected

from .provider import ACTOR, OPERATION, SandboxMerchant
from .service import DEMO_ADDRESS, MENU_BY_ID, MERCHANT


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=35)
    quantity: int = Field(ge=1, le=10)
    priceFen: int = Field(ge=1, le=100_000)


class QuoteDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sandbox: bool
    quoteId: str = Field(pattern=r"^quote_[a-f0-9]{32}$")
    merchant: str = Field(min_length=1, max_length=40)
    items: list[Product] = Field(min_length=1, max_length=3)
    totalFen: int = Field(ge=1, le=300_000)
    addressMasked: str = Field(min_length=1, max_length=40)
    expiresAt: str = Field(min_length=1, max_length=32)


class OrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    actor: str = Field(min_length=1, max_length=160)
    details: QuoteDetails


class _DropAfterCommit(Exception):
    pass


def create_app(db_path: str | Path, *, drop_first_response: bool = False) -> FastAPI:
    vendor = SandboxMerchant(db_path)
    app = FastAPI(title="小途模拟餐厅（无支付）", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.vendor = vendor
    dropped = False
    lock = asyncio.Lock()

    @app.post("/orders", status_code=201)
    async def create_order(body: OrderBody, key: Annotated[str, Header(alias="Idempotency-Key")]):
        nonlocal dropped
        data = body.details
        if (not data.sandbox or data.merchant != MERCHANT or data.addressMasked != DEMO_ADDRESS or
                len({p.id for p in data.items}) != len(data.items) or
                data.totalFen != sum(p.quantity * p.priceFen for p in data.items) or
                any(p.id not in MENU_BY_ID or p.priceFen != MENU_BY_ID[p.id]["priceFen"] or
                    p.name != MENU_BY_ID[p.id]["name"] for p in data.items)):
            raise HTTPException(400, "invalid_sandbox_quote")
        try:
            result = await vendor.create_order(body.actor, key, data.model_dump())
        except ToolRejected as exc:
            raise HTTPException(exc.status, exc.code) from None
        if drop_first_response:
            async with lock:
                if not dropped:
                    dropped = True

                    async def abort_stream():
                        # The SQLite transaction above has committed. Emit part of the
                        # response and abort the HTTP stream instead of reporting a 500
                        # before the write. This switch lives only in the mock process.
                        yield b'{"sandbox":'
                        raise _DropAfterCommit("sandbox response dropped after database commit")

                    return StreamingResponse(abort_stream(), media_type="application/json", status_code=201)
        return result

    @app.get("/orders/{operation_id}")
    async def find_order(operation_id: str, actor: Annotated[str, Query(min_length=1, max_length=160)]):
        if not ACTOR.fullmatch(actor) or not OPERATION.fullmatch(operation_id):
            raise HTTPException(404, "not_found")
        receipt = await vendor.find_order(actor, operation_id)
        if receipt is None:
            raise HTTPException(404, "not_found")
        return receipt

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="小途本地模拟餐厅（不会下真实订单）")
    parser.add_argument("--db", type=Path, required=True, help="模拟商家 SQLite 文件")
    parser.add_argument("--port", type=int, default=3221)
    parser.add_argument("--drop-first-response", action="store_true",
                        help="仅首个订单提交后主动断开响应")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port 需介于 1024–65535")
    uvicorn.run(create_app(args.db, drop_first_response=args.drop_first_response),
                host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
