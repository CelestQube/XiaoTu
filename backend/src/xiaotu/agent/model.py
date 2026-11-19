"""LangChain model pipeline with the existing Qwen JSON protocol."""
import asyncio
import json
from urllib.parse import urlsplit

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
import httpx


class ModelError(ValueError):
    pass


class Qwen:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        # Pass the actual JSON through a variable rather than interpreting model or
        # user-controlled braces as template placeholders.
        self.prompt = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{payload}")])

    async def __call__(self, system, payload, *, tokens=1100, timeout=23):
        settings = self.settings
        try:
            endpoint = urlsplit(settings.base)
            if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None or endpoint.query or endpoint.fragment:
                raise ValueError()
            url = settings.base.rstrip("/") + "/chat/completions"
        except ValueError:
            raise ModelError("QWEN_BASE_URL 无效：应为模型供应商提供的 HTTPS 基础地址。") from None

        async def call(prompt):
            async with self.client.stream("POST", url, follow_redirects=False, timeout=timeout,
                                          headers={"Authorization": "Bearer " + settings.key},
                                          json={"model": settings.model, "temperature": .2, "max_tokens": tokens,
                                                "response_format": {"type": "json_object"},
                                                **({"enable_thinking": settings.thinking} if settings.thinking is not None else {}),
                                                "messages": [{"role": "system" if x.type == "system" else "user",
                                                              "content": x.content} for x in prompt.to_messages()]}) as response:
                if not response.is_success:
                    raise ModelError(f"Qwen 请求失败（HTTP {response.status_code}），请检查模型权限、额度或接口配置。")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256_000:
                        raise ModelError("Qwen 返回的数据过大。")
            try:
                text = json.loads(raw)["choices"][0]["message"]["content"].strip()
                if text.startswith("```") and text.endswith("```"):
                    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                found = json.loads(text)
                if not isinstance(found, dict):
                    raise ValueError()
                return found
            except (ValueError, KeyError, AttributeError, IndexError, TypeError):
                raise ModelError("Qwen 未返回有效的结构化结果。") from None
        try:
            async with asyncio.timeout(timeout):
                return await (self.prompt | RunnableLambda(call)).ainvoke(
                    {"system": system, "payload": json.dumps(payload, ensure_ascii=False, allow_nan=False)})
        except (httpx.HTTPError, TimeoutError):
            raise ModelError("连接 Qwen 超时或失败，请检查网络和 API 地址。") from None
