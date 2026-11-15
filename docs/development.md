# 小途开发说明

## 模块位置

| 路径 | 用途 | 从哪里扩展 |
| --- | --- | --- |
| `public/index.html`、`app.js`、`styles.css` | 聊天、行程、预算、画像与本地保存 | 新页面、移动端交互、语音与图片入口 |
| `server/index.js` | HTTP 路由、静态资源和本地安全边界 | 新接口从此处挂载，参数校验放在业务模块 |
| `server/config.js`、`.env.example` | 本地环境变量及默认值 | 新配置先加空白示例，真实凭据只放本地 |
| `server/model/qwen.js` | Qwen 兼容请求与错误归类 | 新增模型供应商或 AgentArts 客户端可在同目录独立实现 |
| `server/agent/index.js` | 解析、澄清、工具、拟案、验证、汇总 | 在此新增明确意图或调用策略，禁止模型直接执行任意工具 |
| `server/agent/demo.js` | 规则解析、示范城市与画像建议 | 更新静态案例；不要将其标为实时 |
| `server/agent/budget.js` | 人民币分项区间估算 | 接入实时价格、处理多房型及实际支出 |
| `server/tools/amap.js` | MCP 握手、受控查询、来源标识 | 扩展地理编码、路线模式，逐项确认高德返回字段 |
| `server/agent/rail.js` | 票务缺失状态和官方查询提示 | 取得授权只读数据源后实现独立交通适配 |
| `server/calendar.js` | 日期、授权与 ICS 生成 | 真正的日历写入作为独立授权适配器 |
| `server/domain.js` | 输入过滤、日期及计划结构 | 扩展时间区间和交通衔接约束 |
| `test/agent.test.js` | 业务、模拟供应商及 HTTP 回归 | 新增场景、外部协议契约及失败用例 |

## HTTP 约定

- `GET /api/status`：模型和地图是否已配置、票务与日历能力。仅返回模式，不回传密钥。
- `POST /api/chat`：接收 `message, history, trip, priorPlan, profile`。返回 `mode, reply, trip`，可选 `plan, places, sources, weather, route, profileSuggestion`。信息不足返回部分 trip，不输出完整行程。
- `POST /api/calendar`：接收 `plan, startDate, confirmed:true`，返回 `text/calendar`。按中国时区生成 UTC 时间，仅下载文件；重复导入可能产生重复事件。

## 数据与决策

先解析本次硬约束，缺少目的地、人数或天数时追问；已确认的画像只作为偏好。后端根据已校验的目的地选择白名单内的地图工具。模型返回建议及结构化日程，后端校验天数、时间格式和顺序，再统一计算预算。确认动作不由模型触发。规划草案仍不能替代人工核查开放时间、实际通勤、票务及天气；当前只检测时间点顺序，尚未建立活动时长和交通衔接求解。

## AgentArts 参赛路线

当前代码是**独立 Qwen 网页原型**，不等于已在 AgentArts 编排或部署。参赛版本可把需求提取、条件分支、MCP 查询、知识检索、结构化输出及评测迁移到 [AgentArts 工作流](https://support.huaweicloud.com/lowcode-agentarts/agentarts_05_0041.html)，保留网页接口及后端确定性预算、确认与校验能力。待平台账号、模型接入和工具权限确认后，再真实部署和记录评测结果。
