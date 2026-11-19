import argparse

import uvicorn

from xiaotu.app import create_app
from xiaotu.config import load_settings


def main():
    parser = argparse.ArgumentParser(prog="python -m xiaotu")
    parser.add_argument("--port", type=int)
    parser.add_argument("--check-capabilities", action="store_true")
    args = parser.parse_args()
    try:
        settings = load_settings()
    except ValueError as failure:
        parser.exit(1, f"小途配置错误：{failure}\n")
    if args.check_capabilities:
        for name, entry in settings.mcp.items():
            print(f"MCP {name}: {'禁用' if entry.get('disabled') else '登记完成（未联网）'}")
        for phase, names in settings.skill_names.items():
            print(f"Skill {phase}: {', '.join(names) or '无'}")
        return
    port = args.port or settings.port
    if not 1024 <= port <= 65535:
        parser.error("端口须介于 1024 和 65535。")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, access_log=False)


if __name__ == "__main__":
    main()
