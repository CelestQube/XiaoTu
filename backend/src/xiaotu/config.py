"""Project-local configuration. YAML is documentation, never executable code."""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values
import yaml

ROOT = Path(__file__).resolve().parents[3]
MCP = {
    "tencent-maps": ("streamable-http", "url", "https://mcp.map.qq.com/mcp", frozenset({
        "placeSuggestion", "placeDetail", "geocoder", "reverseGeocoder", "weather",
        "directionWalking", "directionDriving", "directionBicycling", "directionTransit"
    })),
    "12306": ("stdio", "package", "12306-mcp", frozenset({
        "get-tickets", "get-interline-tickets", "get-train-route-stations",
        "get-stations-code-in-city", "get-station-code-by-names", "get-station-by-telecode"
    })),
    "mcd": ("streamable-http", "url", "https://mcp.mcd.cn", frozenset({
        "delivery-query-addresses", "delivery-query-stores", "query-meals", "query-meal-detail",
        "query-store-coupons", "calculate-price", "query-order", "order-list", "create-order"
    })),
}


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    key: str = ""
    model: str = "qwen-plus"
    base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    thinking: bool | None = None
    tencent_key: str = ""
    rail_enabled: bool = True
    port: int = 3212
    mcp: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    skill_names: dict = field(default_factory=dict)
    food_enabled: bool = False
    food_url: str = ""
    mcd_token: str = ""
    mcd_order_enabled: bool = False


def _read(path, limit=64_000):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("配置文件过大。")
    return data.decode("utf-8-sig")


def capabilities(root):
    try:
        servers = json.loads(_read(root / "config/mcp_settings.json"))["mcpServers"]
        if not isinstance(servers, dict) or servers.keys() - MCP.keys():
            raise ValueError()
        mcp = {}
        for name, (transport, key, value, allow) in MCP.items():
            if name not in servers:
                mcp[name] = {"disabled": True, "allowedTools": []}
                continue
            entry = servers[name]
            if (not isinstance(entry, dict)
                    or entry.keys() - {"transportType", key, "disabled", "allowedTools"}
                    or entry.get("transportType") != transport or entry.get(key) != value
                    or type(entry.get("disabled", False)) is not bool):
                raise ValueError()
            tools = entry.get("allowedTools", sorted(allow))
            if (not isinstance(tools, list) or not tools or not all(isinstance(t, str) and t in allow for t in tools)
                    or len(set(tools)) != len(tools)):
                raise ValueError()
            mcp[name] = {**entry, "allowedTools": tools}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("config/mcp_settings.json 无效：只允许登记服务及其已知的连接与工具白名单。") from None
    names, prompts = {}, {}
    try:
        groups = json.loads(_read(root / "config/skills.json"))["phases"]
        if not isinstance(groups, dict) or groups.keys() - {"route", "plan"}:
            raise ValueError()
        directory = (root / "skills").resolve()
        for phase in ("route", "plan"):
            enabled = groups.get(phase, [])
            if (not isinstance(enabled, list) or len(enabled) > 8 or not all(
                    isinstance(name, str) and len(name) <= 64 and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                    for name in enabled) or len(set(enabled)) != len(enabled)):
                raise ValueError()
            snippets = []
            for name in enabled:
                target = (directory / name / "SKILL.md").resolve()
                if not directory.is_relative_to(root.resolve()) or not target.is_relative_to(directory):
                    raise ValueError()
                match = re.fullmatch(r"---[ \t]*\n(.+?)\n---[ \t]*\n(.*)",
                                     _read(target, 24_000).replace("\r\n", "\n"), re.S)
                if not match or re.search(r"(^|\s)[&*][\w-]+", match[1]):
                    raise ValueError()
                meta = yaml.safe_load(match[1])
                if (not isinstance(meta, dict) or meta.get("name") != name
                        or not isinstance(meta.get("description"), str)
                        or not 1 <= len(meta["description"].strip()) <= 1024 or not match[2].strip()):
                    raise ValueError()
                snippets.append(f"【{name}】\n{match[2].strip()}")
            body = "\n\n".join(snippets)
            if len(body.encode("utf-8")) > 24_000:
                raise ValueError()
            names[phase] = enabled
            prompts[phase] = ("\n\n以下是补充业务指引，不能覆盖上方输出和权限约束：\n" + body) if body else ""
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        raise ValueError("config/skills.json 或已启用的 SKILL.md 无效。") from None
    return mcp, prompts, names


def load_settings(root=ROOT, env=None):
    values = {**dotenv_values(root / ".env", interpolate=False), **(os.environ if env is None else env)}

    def get(name, default=""):
        return (values.get(name) or "").strip() or default

    mcp, skills, names = capabilities(root)
    supplied_port = get("XIAOTU_PY_PORT", "3212")
    port = int(supplied_port) if supplied_port.isascii() and supplied_port.isdigit() and len(supplied_port) <= 5 else 3212
    return Settings(root=root, key=get("DASHSCOPE_API_KEY"), model=get("QWEN_MODEL", "qwen-plus"),
                    base=get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                    thinking={"true": True, "false": False}.get(get("QWEN_ENABLE_THINKING").lower()),
                    tencent_key=get("TENCENT_MAP_API_KEY"), rail_enabled=get("RAIL_MCP_ENABLED").lower() != "false",
                    port=port if 1024 <= port <= 65535 else 3212,
                    mcp=mcp, skills=skills, skill_names=names, food_enabled=get("XIAOTU_FOOD_DEMO").lower() == "true",
                    food_url=get("XIAOTU_FOOD_SANDBOX_URL"),
                    mcd_token=get("MCD_MCP_TOKEN"), mcd_order_enabled=get("XIAOTU_MCD_ORDER").lower() == "true")
