from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from xiaotu.execution import ToolRejected, ToolTransient, ToolUncertain

from .store import Database, canonical, fingerprint

ACTOR = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
OPERATION = re.compile(r"op_[a-f0-9]{32}\Z")
ORDER = re.compile(r"test_[a-f0-9]{32}\Z")
QUOTE = re.compile(r"quote_[a-f0-9]{32}\Z")


class FoodProvider(Protocol):
    supports_idempotent_create: bool
    supports_order_snapshot: bool
    reconcile_timeout: float

    async def create_order(self, actor: str, operation_id: str, details: dict) -> dict: ...

    async def find_order(self, actor: str, operation_id: str, details: dict,
                         snapshot: list[str] | None) -> dict | None: ...

    async def recent_orders(self, actor: str) -> list[str] | None: ...

    def verify_receipt(self, receipt: dict, operation_id: str, details: dict) -> str: ...

    async def live_order(self, order_id: str) -> dict | None: ...


def verify_sandbox_receipt(receipt: dict, operation_id: str, details: dict) -> str:
    """Require the sandbox receipt to echo the stable key and quoted amount."""
    if (not isinstance(receipt, dict) or receipt.get("sandbox") is not True or
            receipt.get("operationId") != operation_id or type(receipt.get("totalFen")) is not int or
            receipt.get("totalFen") != details["totalFen"] or not isinstance(receipt.get("orderId"), str) or
            not ORDER.fullmatch(receipt["orderId"])):
        raise ToolUncertain("invalid_receipt")
    return receipt["orderId"]


class SandboxMerchant:
    """Fake seller only. Its own transaction defines the side-effect boundary."""

    supports_idempotent_create = True
    supports_order_snapshot = False
    reconcile_timeout = 0.8
    sandbox = True
    verify_receipt = staticmethod(verify_sandbox_receipt)

    def __init__(self, path: str | Path):
        self.db = Database(path)
        self.db.schema("""
            CREATE TABLE IF NOT EXISTS sandbox_orders (
                order_id TEXT PRIMARY KEY, actor TEXT NOT NULL,
                operation_id TEXT NOT NULL, digest TEXT NOT NULL, receipt TEXT NOT NULL,
                UNIQUE(actor, operation_id)
            );
        """)

    async def create_order(self, actor: str, operation_id: str, details: dict) -> dict:
        return await asyncio.to_thread(self._create, actor, operation_id, details)

    def _create(self, actor: str, operation_id: str, details: dict) -> dict:
        if not ACTOR.fullmatch(actor) or not OPERATION.fullmatch(operation_id):
            raise ToolRejected(400, "bad_operation")
        if type(details.get("totalFen")) is not int or details["totalFen"] <= 0:
            raise ToolRejected(400, "bad_quote")
        digest = fingerprint(details)
        with self.db.connection(write=True) as db:
            row = db.execute("SELECT digest, receipt FROM sandbox_orders WHERE actor = ? AND operation_id = ?",
                             (actor, operation_id)).fetchone()
            if row is not None:
                if row["digest"] != digest:
                    raise ToolRejected(409, "key_conflict")
                return json.loads(row["receipt"])
            receipt = {"sandbox": True, "operationId": operation_id, "orderId": "test_" + uuid4().hex,
                       "totalFen": details["totalFen"]}
            db.execute("""INSERT INTO sandbox_orders(actor, operation_id, order_id, digest, receipt)
                          VALUES (?, ?, ?, ?, ?)""",
                       (actor, operation_id, receipt["orderId"], digest, canonical(receipt)))
            return receipt

    async def find_order(self, actor: str, operation_id: str, details: dict = None,
                         snapshot: list[str] | None = None) -> dict | None:
        return await asyncio.to_thread(self._find, actor, operation_id)

    def _find(self, actor: str, operation_id: str) -> dict | None:
        with self.db.connection() as db:
            row = db.execute("SELECT receipt FROM sandbox_orders WHERE actor = ? AND operation_id = ?",
                             (actor, operation_id)).fetchone()
            return json.loads(row["receipt"]) if row is not None else None

    async def recent_orders(self, actor: str) -> list[str] | None:
        return None

    async def live_order(self, order_id: str) -> dict | None:
        return None

    def count(self) -> int:
        with self.db.connection() as db:
            return db.execute("SELECT COUNT(*) FROM sandbox_orders").fetchone()[0]


class HttpFoodProvider:
    """A loopback-only client for the simulated seller; no real orders or credentials."""

    supports_idempotent_create = True
    supports_order_snapshot = False
    reconcile_timeout = 0.8
    sandbox = True
    verify_receipt = staticmethod(verify_sandbox_receipt)

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None):
        try:
            base = urlsplit(base_url)
            port = base.port
        except ValueError:
            raise ValueError("外卖实验只允许回环 HTTP 地址。") from None
        if (base.scheme != "http" or base.hostname != "127.0.0.1" or port is None or
                not (1024 <= port <= 65535) or base.username is not None or base.password is not None or
                base.query or base.fragment or base.path not in {"", "/"}):
            raise ValueError("外卖实验只允许回环 HTTP 地址。")
        self.base_url = f"http://127.0.0.1:{port}"
        self.client = client

    async def _send(self, method: str, route: str, **kwargs) -> httpx.Response:
        try:
            if self.client is not None:
                return await self.client.request(method, self.base_url + route,
                                                 follow_redirects=False, **kwargs)
            async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                return await client.request(method, self.base_url + route, follow_redirects=False, **kwargs)
        except httpx.TransportError:
            raise ToolTransient("network") from None

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        status = response.status_code
        if status in {408, 429, 500, 502, 503, 504}:
            hint = response.headers.get("Retry-After", "").strip()
            seconds = float(hint) if re.fullmatch(r"\d{1,6}", hint) else None
            raise ToolTransient("http_" + str(status), seconds)
        if status in {400, 401, 403, 409, 422}:
            raise ToolRejected(status, "http_" + str(status))
        if status not in {200, 201} or len(response.content) > 16000:
            raise ToolUncertain("unexpected_status")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
        except ValueError:
            raise ToolUncertain("invalid_response") from None
        return result

    async def create_order(self, actor: str, operation_id: str, details: dict) -> dict:
        response = await self._send("POST", "/orders", headers={"Idempotency-Key": operation_id},
                                    json={"actor": actor, "details": details})
        return self._parse(response)

    async def find_order(self, actor: str, operation_id: str, details: dict = None,
                         snapshot: list[str] | None = None) -> dict | None:
        response = await self._send("GET", "/orders/" + operation_id, params={"actor": actor})
        if response.status_code == 404:
            return None
        return self._parse(response)

    async def recent_orders(self, actor: str) -> list[str] | None:
        return None

    async def live_order(self, order_id: str) -> dict | None:
        return None
