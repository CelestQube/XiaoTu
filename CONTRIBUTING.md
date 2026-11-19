# 小途开发守则

以 **GitHub 私有仓库**为唯一协作主仓：Issues 管任务，Pull Requests 做评审，GitHub Actions 跑检查。每位成员用自己的 GitHub 账号、SSH 密钥和本地开发配置。码道、Codex 或 AgentArts 可以按需使用，不要求成员更换 IDE 或把核心编排迁到托管平台。

## 开始开发

1. 通过 GitHub 仓库的 SSH 地址克隆项目，按 [README](README.md#启动) 安装 Python 3.12 和 Node.js 22+ 依赖。小途后端只用 Python；Node.js 用于铁路 MCP 和前端测试。
2. 先用空白配置跑通演示和离线测试。需要验证真实上游时，各自在本地 `.env` 填自己的测试凭据并控制调用量；不要共享私人密钥或用生产配置跑测试。
3. 从最新 `main` 创建短分支，例如 `feat/42-weather-card`、`fix/51-rail-timeout`、`docs/54-api-contract`。一个分支专注一个 Issue，暂不完成的边界写清楚，不整仓格式化。

## 分工

| 维护面 | 目录 | 跨模块约定 |
| --- | --- | --- |
| 网页与交互 | `public/`、`test/frontend/` | 与后端确定 API 形状、空状态和失败展示 |
| Agent 与旅行领域 | `backend/src/xiaotu/agent/`、`domain.py`、`skills/` | 模型提出意图与草案，业务层决定权限、数据校验与人工确认 |
| 数据工具 | `backend/src/xiaotu/tools/`、`config/mcp_settings.json` | 新 MCP 先登记适配器和最小白名单，明确只读或写入、额度、超时及错误。12306 首版保留 Python 客户端通过 `stdio` 启动锁定版本 |
| 可靠执行与部署 | `backend/src/xiaotu/execution/`、`food/` | 新写操作和数据库结构变化至少一位其他维护者复核；课程实验只接模拟商家 |

## 改动边界

- **Agent 与 UI**：闲聊和只读查询不得修改行程；只有创建、补全或修改的明确意图才能走规划。天气与铁路卡片用实际工具数据，不伪造报价、余票或气温。网页和 App 共用小途 API，不让客户端直接持有模型或地图密钥。
- **MCP 与 Skill**：可以独立提交适配器、离线夹具或尚未启用的 Skill。Skill 只提供业务指引，不授予执行权限；不允许模型调用未登记工具。接入方式见[能力管理](docs/capabilities.md)。
- **有副作用的工具**：报价、确认、执行和查单分开。写入须显式确认；同一次确认复用操作键。超时先用原键查结果，不擅自补单。真实预订、支付和取消不在课程实验范围。
- **敏感资料**：不提交真实 `.env`、密钥、IDE 私人设置、工作区备份、数据库、日志、用户行程或截图中的个人信息。示例配置只放空值或明确的假数据。密钥若曾提交，先撤销并轮换，再由维护者协调清理历史。

## 合并与发布

1. PR 关联 Issue，附改动范围、测试结果和回滚方式。跨模块先商定请求、响应、空结果及失败语义；接口变更同步更新文档、夹具和使用方。
2. 首次上传建好基线后，由仓库管理员按套餐支持情况[保护 `main`](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)：日常不直接推送或强推，至少一位非作者批准且 CI 通过后再合并。若仓库暂不支持强制保护，团队仍按同一流程执行。
3. 本地按[开发指南](docs/development.md#检查命令)跑配置检查、Python 与前端测试。CI 不访问真实供应商、不使用项目服务密钥。新增工具至少覆盖成功、无数据和失败；写操作另测重复、并发与重启恢复。真实联调单独记录，不把模拟结果写成线上验证。
4. 发布给稳定版本打标签，记录变更和回滚点；数据库变更先验证备份及恢复。暂未完成账号和数据隔离时，只做受限演示，不直接对公网开放本地开发服务。
