# 小途开发指南

FastAPI 是唯一应用后端，同时提供网页和同源 JSON 接口。LangChain 封装 Qwen 的结构化输出，LangGraph 编排路由、查询、行程拟稿以及模拟订单确认。模型不会获准执行任意 MCP 工具，也不会直接下单。

## 目录与开发入口

| 目录 / 文件 | 用途与扩展方式 |
| --- | --- |
| `public/`、`test/frontend/` | 网页、地图、地点替换、铁路表单和模拟外卖；新增交互使用固定 API 夹具。见[前端设计](design.md)。 |
| `backend/src/xiaotu/__main__.py`、`app.py` | CLI、FastAPI 路由、请求约束、同源检查和静态文件；新增 HTTP 接口在此登记，业务规则放在对应模块。 |
| `backend/src/xiaotu/config.py`、`config/`、`skills/` | 配置、已登记的 MCP 适配器白名单和分阶段 Skill；新服务先写适配器再注册。见[能力管理](capabilities.md)。 |
| `backend/src/xiaotu/agent/` | `graphs.py` 和 `intent.py` 区分自然聊天、只读查询和行程编排，`model.py` 管 Qwen，`lookup.py` 产出查询卡片，`journey.py` 管交通候选与默认值，`places.py` 管地点签名及编辑预览，`weather.py` 管预报证据，`demo.py` 提供无密钥演示。 |
| `backend/src/xiaotu/domain.py` | 旅行日期、行程校验、预算与日历文件。 |
| `backend/src/xiaotu/tools/` | `rail.py` 管 12306 stdio、校验和缓存；`tencent.py`、`tencent_transport.py`、`tencent_fields.py` 分离地图业务、HTTP 传输和结果解析。 |
| `backend/src/xiaotu/execution/`、`food/` | 执行截止、重试、持久化查单及模拟商家和对照基准；麦当劳下单走同一网关的单次发送 + 快照对账。见[麦当劳外卖](mcd-delivery.md)与[弱网实验](reliable-tool-experiment.md)。 |
| `backend/tests/` | FastAPI 契约、适配器协议和可靠执行回归；使用离线夹具、临时数据库或本机回环。 |
| `backend/pyproject.toml`、`constraints.txt`；`package.json`、`package-lock.json` | Python 依赖和版本约束；npm 只用于安装固定版本的 12306 MCP（及其依赖）和运行网页测试。 |

## HTTP 接口速查

请求和响应的可执行示例以 `backend/tests/` 为准。大多数接口使用同源 `POST` JSON。

| 接口 | 主要约定 |
| --- | --- |
| `GET /api/status` | 返回模型、地图、铁路等配置状态，不验证上游鉴权、不返回密钥。 |
| `POST /api/chat` | 输入 `message` 和可选 `history, trip, priorPlan, profile`；返回 `mode, intent, reply`。`intent` 为 `chat/lookup/plan/food`：仅 `plan` 返回行程更新；`lookup` 与 `food` 可返回 `cards`（`type=weather/rail/food-*`，由实际工具结果构建，不接受模型生成的数据卡片）。外卖分支只做只读查询和报价，聊天本身不会创建订单。前端在原消息下渲染，缺字段不填造。 |
| `POST /api/travel` | 入参 `origin, destination, date, time, mode`，可指定 `middle`。返回原查询要求、实际查询日期/时间、铁路与自驾候选；仅在直达明确无匹配时按 `auto` 补查一次换乘。 |
| `POST /api/rail/query` | `action=tickets/transfer/stops/stations`。票务输入起终点与日期；经停输入车次与日期；车站输入关键词。空结果与工具故障分开，不伪造席别、价格或余票。 |
| `POST /api/maps/day, search, detail, locate, route, snapshot` | 核实当日地点、同城搜索、POI 详情、GPS 反查、分段路线和静态 PNG。坐标对外为「经度,纬度」。缺折线时只在 `geometry:true` 按需尝试补查，失败不画假轨迹。 |
| `POST /api/plan/place-options`、`place-preview` | `day, index` 是从零开始的索引。地点必须经过签名校验；手填标待核实。预览不落盘，用户选定后才由客户端应用到草案并保留该地点。 |
| `POST /api/calendar` | 校验 `plan, startDate, confirmed:true`，下载 ICS，不触发第三方写入；重复导入可能产生重复事件。 |
| `POST /api/food/menu, quote, confirm, status` | `menu/quote` 仅模拟外卖实验开启时可用。`confirm/status` 按报价标识自动路由到麦当劳或模拟账本（同源匿名会话隔离）：确认必须明确提供 `confirmed:true`；麦当劳下单另需 `XIAOTU_MCD_ORDER=true`，经可靠网关单次发送并快照对账，回执含 `route: "#/order/<quoteId>"` 与订单详情。 |

地点规划会先利用地图已核实的候选和短期天气，未核实点不当作地图证据；必要时给可替换候选或留空待选。用户确认并保留的地点优先不动；跨日移动或同日换序须由用户选择预览。直线距离只是编辑参考，不代表最优车程；天气只展示可核实预报和风险提示，不把预报当权威预警。

## 检查命令

在项目根目录使用已激活的 Python 3.12 虚拟环境（或用环境内的 Python 可执行文件替换命令）：

~~~powershell
python -m pip install -c backend/constraints.txt -e "./backend[dev]"
npm.cmd ci --ignore-scripts
python -m xiaotu --check-capabilities
python -m pytest -q backend
npm.cmd test
~~~

自动化测试不访问真实 Qwen、地图或铁路服务；stdio 测试启动本机伪 MCP 子进程，故障测试需要回环网络。若要换端口，在 `python -m xiaotu` 后加 `--port`；改端口前先考虑浏览器草案按源隔离。同一端口只运行一个服务，改 Python 配置或源代码后重启进程。
