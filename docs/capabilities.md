# 小途能力管理：MCP 与 Skill

小途有自己的后端，不依赖码道或 Codex 来执行用户的旅行请求。两种配置都是项目级的，可以随代码版本管理：

| 管理内容 | 入口 | 例子 |
| --- | --- | --- |
| MCP：连接真实数据与操作 | `config/mcp_settings.json` | 12306 查车票、腾讯地图核实地点和查天气 |
| Skill：告诉小途在什么阶段、按什么工作方法处理 | `config/skills.json` 和 `skills/<名称>/SKILL.md` | 减少当天来回跨区，没确认抵达时间就不安排必须提前预约的项目 |
| 私有凭据 | 根目录 `.env`（不要提交） | `TENCENT_MAP_API_KEY`、`DASHSCOPE_API_KEY` |

## 现有 MCP 怎么安装、怎么关闭

| 服务名 | 工作方式 | 安装或准备 |
| --- | --- | --- |
| `tencent-maps` | 远程 Streamable HTTP。后端连接腾讯官方 `https://mcp.map.qq.com/mcp`，按需调用地点、地理编码、天气和路线工具。 | 不在本机安装服务。把有权限的腾讯 WebService Key 填入 `.env`，继续沿用现有 Key。 |
| `12306` | 本地 stdio。首次铁路查询才由 Python MCP 客户端启动 Node 子进程；无需铁路账号或额外监听端口。 | `npm.cmd ci --ignore-scripts` 安装锁定的 `12306-mcp@0.3.10` 及其依赖。不单独启动 Node 服务。 |

配置结构借鉴了 [CodeArts 码道 IDE 的 MCP 管理](https://support.huaweicloud.com/usermanual-codeartsagent/codeartsagent_ug_0010.html)，但文件与能力不是互通的。项目中若已有 `.codeartsdoer/mcp/mcp_settings.json`，它是码道自己的配置，修改它不会改变小途的运行。

1. 在小途的 `mcpServers` 下找到服务名。将 `disabled` 改为 `true` 停用，改回 `false` 启用。省略某项也视为停用。
2. `allowedTools` 可删除不想使用的工具，但不能添加未在适配器注册的工具；若省略此字段，使用该适配器的完整只读名单。比如关掉腾讯的 `weather`，天气功能会降级；若关掉依赖的 `geocoder`，按城市名查天气也会受影响。
3. 使用虚拟环境运行 `python -m xiaotu --check-capabilities`，再重启服务。检查在本地加载配置，不启动 MCP，也不验证上游鉴权、配额和数据。若铁路仍关闭，检查是否设置了 `RAIL_MCP_ENABLED=false`。腾讯静态底图 API 和本地日历 ICS 导出不是 MCP。

### 接入第三个 MCP

本版是**已适配 MCP 的集中管理**，不是通用 MCP 客户端。不要把供应商给的任意 `command`、`args`、`url` 或认证头直接粘进配置并期待模型能用。做法如下：

1. 确认传输方式（[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)）、正式地址或固定包版本、认证变量、所需工具名及其输入/输出。
2. 在 `backend/src/xiaotu/tools/` 实现适配器，校验参数、认证去向、响应和失败方式。在 `backend/src/xiaotu/config.py` 注册固定连接和最小工具集合，再写入 `config/mcp_settings.json`。新增本地包须固定版本，勿在服务运行时用 `npx @latest` 下载执行。
3. 在 `backend/src/xiaotu/agent/` 将能力接入明确意图或行程阶段，在 `backend/tests/` 加模拟测试；需要前端交互时在 `backend/src/xiaotu/app.py` 挂业务接口。发现 MCP 工具不等于向模型授予执行权限。打车、订房等写操作须独立确认，不能由 Skill 授权。

## 添加或停用 Skill

在项目中创建 `skills/你的英文名称/SKILL.md`。目录名与 YAML 的 `name` 一致，只用小写字母、数字和短横线。目录结构参考 [Agent Skills 规范](https://agentskills.io/specification)：

```markdown
---
name: slow-trip
description: 用于为希望少走路或降低旅行强度的用户安排休息和相邻活动。
---

- 用户表示行程太累时，优先删去需跨区赶路的可选项目。
- 如果没有可核实的无障碍设施信息，标为待确认，不猜测。
```

把 `"slow-trip"` 加入 `config/skills.json` 的 `phases.plan` 数组，检查并重启：

- `route` 是第一个 Qwen 阶段，负责判断用户意图和抽取字段；这里的 Skill 对每次 Qwen 路由生效，适合放简短的分类规则。
- `plan` 仅在生成或重排行程时传给 Qwen，适合行程节奏、路线衔接、偏好和活动取舍。默认启用 `itinerary-planning`。移除配置里的名称即可停用，不必删文件。
- 小途目前只发已启用 Skill 的 Markdown 正文给对应阶段的 Qwen。不自动读取 `references/`、`assets/`，不运行 `scripts/`，不让元数据新增工具权限。未启用的 Skill 文件不会被读取。每个阶段至多 8 个，正文合计不超过 24 KB。

来自外部的 Skill 先审阅再启用；正文会发往 Qwen，不要写入密钥、精确定位或其他隐私信息。码道、Codex 用于开发小途的 Skill 与这里的小途业务 Skill 是两套能力，不会自动互载。
