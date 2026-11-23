from asyncio import Lock
from contextlib import asynccontextmanager
import hashlib
import json
import re
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from xiaotu.agent.graphs import build_chat_graph, build_checkout_graph
from xiaotu.agent.journey import Journey, JourneyError, rail_reply
from xiaotu.agent.model import ModelError, Qwen
from xiaotu.agent import places
from xiaotu.config import Settings, load_settings
from xiaotu.domain import calendar_file, clean
from xiaotu.food import FoodError, FoodService, HttpFoodProvider, McdFoodProvider
from xiaotu.tools._common import ToolError
from xiaotu.tools.mcd import McdService
from xiaotu.tools.rail import RailService
from xiaotu.tools.tencent import TencentMap

COOKIE = "xiaotu_demo"
HOST = re.compile(r"(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?")
TOKEN = re.compile(r"[a-zA-Z0-9_-]{43}")
QUOTE = re.compile(r"quote_[a-f0-9]{32}")
POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
          "img-src 'self' data: blob: https://*.map.qq.com https://map.qq.com https://*.qpic.cn https://qpic.cn; "
          "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def error(status, message, code=None):
    return JSONResponse({"error": message, **({"code": code} if code else {})}, status_code=status)


class InputError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


async def body(request):
    content = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content != "application/json":
        raise InputError(415, "请求需要 application/json。")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 96000:
            raise InputError(413, "请求太大。")
        data.extend(chunk)
    try:
        def invalid(_):
            raise ValueError()
        obj = json.loads(bytes(data).decode("utf-8"), parse_constant=invalid)
        if not isinstance(obj, dict):
            raise ValueError()
        return obj
    except (ValueError, RecursionError):
        raise InputError(400, "无效的 JSON 请求。") from None


class MenuInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    keyword: str = Field(default="", max_length=80)


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    itemId: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=10)


class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    quoteId: str = Field(pattern=r"^quote_[a-f0-9]{32}$")


class ConfirmInput(StatusInput):
    confirmed: bool


def actor_of(request):
    # Anonymous browser sandbox only. Do not accept actor or operation identifiers
    # from JSON. Test orders contain no identity, address, payment or credentials.
    token = request.cookies.get(COOKIE, "")
    new = not TOKEN.fullmatch(token)
    if new:
        token = secrets.token_urlsafe(32)
    return "demo:" + hashlib.sha256(token.encode()).hexdigest(), token if new else None


def browser_reply(payload, token=None):
    response = JSONResponse(payload)
    if token:
        response.set_cookie(COOKIE, token, max_age=7*86400, httponly=True, samesite="strict", path="/")
    return response


def receipt(result):
    state = {"placed":"succeeded", "confirming":"pending", "unknown":"pending", "pending":"pending",
             "awaiting_confirmation":"awaiting_confirmation", "rejected":"failed", "expired":"failed"}
    status = state.get(result.get("status"), "failed")
    value = {"quoteId": result["quoteId"], "status": status,
             **{k: result[k] for k in ("operationId","orderId","message") if result.get(k)}}
    if result.get("provider") == "mcd":
        value["provider"] = "mcd"
        value["sandbox"] = False
        for key in ("storeName","addressText","items","productFen","deliveryFen","payFen",
                    "packingFen","totalFen","orderStatus","payUrl","pickupCode","createTime","route",
                    "amountMismatch","expiresAt"):
            if result.get(key) is not None:
                value[key] = result[key]
    else:
        value["sandbox"] = True
    return value


def create_app(config: Settings | None = None, *, maps=None, railway=None, model=None, food=None,
               mcd=None, clock=None):
    config = config or load_settings()
    maps = maps or TencentMap(config.tencent_key, config.mcp.get("tencent-maps"))
    rail = railway or RailService(config.root, config.mcp.get("12306"), enabled=config.rail_enabled)
    journey, proof = Journey(maps, rail, clock), places.Proof(config.tencent_key)
    state_dir = config.root / ".runtime"

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(follow_redirects=False) as client:
            mcd_service = mcd or (McdService(config.mcd_token, config.mcp.get("mcd"))
                                  if config.mcd_token else None)
            app.state.mcd = mcd_service
            app.state.mcd_food = None
            if mcd_service is not None:
                state_dir.mkdir(parents=True, exist_ok=True)
                app.state.mcd_food = FoodService(state_dir / "food-mcd.sqlite3",
                                                 provider=McdFoodProvider(mcd_service))
            app.state.chat = build_chat_graph(config, model or (Qwen(config, client) if config.key else None),
                                             maps, rail, journey, proof,
                                             mcd=app.state.mcd, food=app.state.mcd_food)
            app.state.food, app.state.checkout = None, None
            try:
                if config.food_enabled:
                    state_dir.mkdir(parents=True, exist_ok=True)
                    provider = HttpFoodProvider(config.food_url) if config.food_url else None
                    app.state.food = food or FoodService(state_dir / "food-demo.sqlite3", provider=provider)
                    async with AsyncSqliteSaver.from_conn_string(str(state_dir / "food-checkpoints.sqlite3")) as saver:
                        app.state.checkout = build_checkout_graph(app.state.food, saver)
                        yield
                else:
                    yield
            finally:
                if mcd_service is not None:
                    await mcd_service.close()
                await maps.close()
                await rail.close()

    app = FastAPI(title="小途", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    active = {"chat": 0, "maps": 0, "travel": 0}
    caps = {"chat": 2, "maps": 3, "travel": 2}
    locks = [Lock() for _ in range(32)]

    @asynccontextmanager
    async def slot(name):
        if active[name] >= caps[name]:
            raise InputError(429, "查询正在处理，请稍后重试。")
        active[name] += 1
        try:
            yield
        finally:
            active[name] -= 1

    @app.middleware("http")
    async def local_boundary(request, call):
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if not HOST.fullmatch(host):
            return error(403, "仅允许本地访问。")
        if origin and origin != f"http://{host}":
            return error(403, "拒绝跨站请求。")
        response = await call(request)
        response.headers.update({"x-content-type-options": "nosniff", "referrer-policy": "no-referrer",
                                 "permissions-policy": "geolocation=(self)", "content-security-policy": POLICY,
                                 "cache-control": "no-store"})
        return response

    @app.exception_handler(InputError)
    @app.exception_handler(ToolError)
    @app.exception_handler(FoodError)
    async def known(request, exc):
        return error(exc.status, str(exc), getattr(exc, "code", None))

    @app.exception_handler(places.PlaceError)
    @app.exception_handler(JourneyError)
    async def bad(request, exc):
        return error(400, str(exc))

    @app.exception_handler(ModelError)
    async def model_error(request, exc):
        return error(502, str(exc))

    @app.exception_handler(ValidationError)
    async def malformed(request, exc):
        return error(400, "请求参数无效。")

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return error(500, "本地服务处理失败，请重试。")

    @app.get("/api/status")
    async def status():
        mcd_ready = app.state.mcd is not None and app.state.mcd.configured
        return dict(backend="python", mode="qwen" if config.key or model else "demo",
                    map="tencent-mcp" if maps.configured else "unconfigured", mapMcp=maps.configured,
                    mapStatic=maps.static_configured, rail="12306-mcp" if rail.configured else "manual", railState=rail.status(),
                    calendar="ics", planPoi=True, journeyFlow=True, placeEditor=True, departureEditor=True,
                    foodDemo=config.food_enabled,
                    mcd="mcd-mcp" if mcd_ready else "unconfigured",
                    mcdOrder=mcd_ready and config.mcd_order_enabled)

    @app.post("/api/chat")
    async def chat(request: Request):
        args = await body(request)
        message = args.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 1600:
            raise InputError(400, "请输入 1–1600 字的行程需求。")
        async with slot("chat"):
            actor, token = actor_of(request)
            mcd_ready = app.state.mcd is not None and app.state.mcd.configured
            if config.food_enabled and not mcd_ready and re.search(r"模拟外卖|测试外卖|外卖测试", message):
                return browser_reply({"mode": "qwen" if config.key or model else "demo", "intent": "chat",
                                      "reply": "外卖仅为模拟实验。请打开顶栏的「模拟外卖」，查看报价并手动确认；聊天不能直接下单。"}, token)
            result = (await app.state.chat.ainvoke({"request": args, "actor": actor}))["reply"]
            return browser_reply(result, token)

    @app.post("/api/travel")
    async def transport(request: Request):
        args = await body(request)
        async with slot("travel"):
            return await journey(args)

    @app.post("/api/rail/query")
    async def tickets(request: Request):
        result = await rail.query(await body(request))
        return {**result, "reply": rail_reply(result)}

    @app.post("/api/maps/{action}")
    async def map_action(action: str, request: Request):
        if action not in {"day","route","locate","detail","snapshot","search"}:
            raise InputError(404, "地图接口不存在。")
        args = await body(request)
        async with slot("maps"):
            data = await getattr(maps, action)(args)
        if action == "snapshot":
            return Response(data, media_type="image/png")
        if action == "search":
            return {**data, "places": [p for x in data["places"] if (p := proof.issue(clean(args.get("city"), 40), x))]}
        if action == "day":
            for stop in data["stops"]:
                if stop.get("poi") and (p := proof.issue(clean(args.get("city"), 50), stop["poi"])):
                    stop["poi"] = {**stop["poi"], **p}
        return data

    @app.post("/api/plan/place-options")
    async def place_options(request: Request):
        args = await body(request)
        async with slot("maps"):
            return await places.options(args, proof, maps)

    @app.post("/api/plan/place-preview")
    async def place_preview(request: Request):
        return places.preview(await body(request), proof)

    @app.post("/api/calendar")
    async def calendar(request: Request):
        data = calendar_file(await body(request))
        if not data:
            raise InputError(400, "需确认导出、填写有效出发日期并拥有合法行程。")
        return Response(data, media_type="text/calendar", headers={"content-disposition":"attachment; filename=xiaotu-trip.ics"})

    def service():
        if not config.food_enabled:
            raise InputError(404, "模拟外卖未启用。")
        return app.state.food

    async def owning_food(actor: str, quote_id: str):
        """Find the order service that owns this quote, with its current state.

        Both ledgers are actor-scoped, so probing them cannot leak another
        session's quote; a miss is a plain miss.
        """
        for service in (app.state.mcd_food, app.state.food):
            if service is None:
                continue
            try:
                return service, await service.status_for_quote(actor, quote_id)
            except FoodError:
                continue
        raise InputError(404, "未找到本会话的报价。")

    @app.post("/api/food/menu")
    async def food_menu(request: Request):
        food = service()
        args = MenuInput.model_validate(await body(request))
        actor, token = actor_of(request)
        result = await food.menu(actor, args.keyword)
        return browser_reply({"sandbox": True, "items": [{"id": x["id"], "name": x["name"], "merchant": x["merchant"],
                                                        "price": x["priceFen"]/100} for x in result["items"]]}, token)

    @app.post("/api/food/quote")
    async def food_quote(request: Request):
        food = service()
        args = QuoteInput.model_validate(await body(request))
        actor, token = actor_of(request)
        result = await food.quote(actor, [{"id": args.itemId, "quantity": args.quantity}])
        thread = {"configurable": {"thread_id": f"{actor}:{result['quoteId']}"}}
        await app.state.checkout.ainvoke({"actor": actor, "quote_id": result["quoteId"]}, thread)
        item = result["items"][0]
        return browser_reply({"quoteId": result["quoteId"], "itemId": item["id"], "name": item["name"],
                              "quantity": item["quantity"], "merchant": result["merchant"], "total": result["totalFen"]/100,
                              "expiresAt": result["expiresAt"], "sandbox": True, "status":"awaiting_confirmation"}, token)

    @app.post("/api/food/status")
    async def food_status(request: Request):
        args = StatusInput.model_validate(await body(request))
        actor, _ = actor_of(request)
        _, current = await owning_food(actor, args.quoteId)
        return receipt(current)

    @app.post("/api/food/confirm")
    async def food_confirm(request: Request):
        args = ConfirmInput.model_validate(await body(request))
        if args.confirmed is not True:
            raise InputError(400, "须明确确认订单。")
        actor, _ = actor_of(request)
        service, current = await owning_food(actor, args.quoteId)
        if service is app.state.mcd_food:
            if not config.mcd_order_enabled:
                raise InputError(403, "麦当劳下单未启用：在 .env 设置 XIAOTU_MCD_ORDER=true 后才能创建真实订单。")
            if current["status"] in ("awaiting_confirmation", "pending", "confirming", "unknown"):
                # The explicit validated request is the approval; the ledger is
                # the durable state machine, so no interrupt graph is needed.
                current = await service.confirm(actor, args.quoteId)
            return receipt(current)
        food = service
        if current["status"] not in ("awaiting_confirmation", "pending"):
            return receipt(current)
        thread = {"configurable": {"thread_id": f"{actor}:{args.quoteId}"}}
        async with locks[int(args.quoteId[-2:], 16) % len(locks)]:
            current = await food.status_for_quote(actor, args.quoteId)
            if current["status"] not in ("awaiting_confirmation", "pending"):
                return receipt(current)
            graph = app.state.checkout
            snapshot = await graph.aget_state(thread)
            if not snapshot.values.get("quote_id"):
                await graph.ainvoke({"actor": actor, "quote_id": args.quoteId}, thread)
                snapshot = await graph.aget_state(thread)
            # If an earlier worker stopped after receiving approval, continue that
            # pending node; otherwise resume only with the explicit validated input.
            resumed = await graph.ainvoke(Command(resume=True) if snapshot.interrupts else None, thread)
            return receipt(resumed.get("result") or await food.status_for_quote(actor, args.quoteId))

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def missing(rest: str):
        return error(404, "接口不存在。")

    app.mount("/", StaticFiles(directory=config.root / "public", html=True), name="public")
    return app
