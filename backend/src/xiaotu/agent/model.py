"""LangChain model pipeline over an OpenAI-compatible chat API in JSON mode.

The request stays on the protocol Qwen accepts; other providers take it as is,
with their own switches passed only when configured (enable_thinking for
DashScope, reasoning_effort for thinking models such as Zhipu GLM).
"""
import asyncio
import json
from urllib.parse import urlsplit

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
import httpx


class ModelError(ValueError):
    pass


# Thinking models reason before the JSON answer and count both against max_tokens,
# so calls that configure a reasoning effort get headroom on tokens and time.
THINKING_TOKENS, THINKING_SECONDS = 6000, 60


class Qwen:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        # Pass the actual JSON through a variable rather than interpreting model or
        # user-controlled braces as template placeholders.
        self.prompt = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{payload}")])

    async def __call__(self, system, payload, *, tokens=1100, timeout=23):
        settings = self.settings
        if settings.effort:
            tokens, timeout = tokens + THINKING_TOKENS, timeout + THINKING_SECONDS
        try:
            endpoint = urlsplit(settings.base)
            if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None or endpoint.query or endpoint.fragment:
                raise ValueError()
            url = settings.base.rstrip("/") + "/chat/completions"
        except ValueError:
            raise ModelError("模型接口地址无效：XIAOTU_MODEL_BASE_URL 应为模型供应商提供的 HTTPS 基础地址。") from None

        async def call(prompt):
            async with self.client.stream("POST", url, follow_redirects=False, timeout=timeout,
                                          headers={"Authorization": "Bearer " + settings.key},
                                          json={"model": settings.model, "temperature": .2, "max_tokens": tokens,
                                                "response_format": {"type": "json_object"},
                                                **({"enable_thinking": settings.thinking} if settings.thinking is not None else {}),
                                                **({"reasoning_effort": settings.effort} if settings.effort else {}),
                                                "messages": [{"role": "system" if x.type == "system" else "user",
                                                              "content": x.content} for x in prompt.to_messages()]}) as response:
                if not response.is_success:
                    raise ModelError(f"模型请求失败（HTTP {response.status_code}），请检查模型权限、额度或接口配置。")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256_000:
                        raise ModelError("模型返回的数据过大。")
            found, truncated = None, False
            try:
                choice = json.loads(raw)["choices"][0]
                truncated = choice.get("finish_reason") == "length"
                text = choice["message"]["content"].strip()
                if text.startswith("```") and text.endswith("```"):
                    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                found = json.loads(text)
                if not isinstance(found, dict):
                    raise ValueError()
            except (ValueError, KeyError, AttributeError, IndexError, TypeError):
                if truncated:
                    hint = ("在 .env 调低 XIAOTU_MODEL_REASONING_EFFORT。" if settings.effort else
                            "思考模型需在 .env 设置 XIAOTU_MODEL_REASONING_EFFORT，以留出思考余量。")
                    raise ModelError(f"模型输出超出长度上限，结果被截断。请重试，或{hint}") from None
                raise ModelError("模型未返回有效的结构化结果。") from None
            return found
        try:
            async with asyncio.timeout(timeout):
                return await (self.prompt | RunnableLambda(call)).ainvoke(
                    {"system": system, "payload": json.dumps(payload, ensure_ascii=False, allow_nan=False)})
        except (httpx.HTTPError, TimeoutError):
            raise ModelError("连接模型超时或失败，请检查网络和 API 地址。") from None
