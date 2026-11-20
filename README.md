# 小途 · 旅游出行助手

小途是一个面向自由行的本地网页应用。聊天生成分日行程和预算草案；可核实地点、比较路线、查询铁路候选、调整行程及导出日历。账本、偏好和旅行草案留在当前浏览器。

## 启动

从项目根目录执行。推荐 Python 3.12。本机已有 `.runtime\py` 环境时，可直接通过 `.\.runtime\py\Scripts\python.exe -m xiaotu` 启动；其他机器首次安装：

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c backend/constraints.txt -e "./backend[dev]"
npm.cmd ci --ignore-scripts
if (!(Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
.\.venv\Scripts\python.exe -m xiaotu
~~~

打开 [小途](http://127.0.0.1:3212/)。铁路查询需要 Node.js 22+：**Python 在首次查票时通过 stdio 启动已锁定的第三方 12306 MCP，无需单独启动 Node 服务**。不测试铁路和前端时可跳过 npm 安装，核心网页及演示仍可运行。默认仅监听本机，`Ctrl+C` 停止；如需沿用旧地址的浏览器草案，先确保旧端口已空闲，再加 `--port 3210` 并从原来的地址访问。不同浏览器源不共享草案。

## 配置和试用

| 能力 | 配置或用法 |
| --- | --- |
| Qwen 对话 | 本地 `.env` 填 `DASHSCOPE_API_KEY`、`QWEN_MODEL`、`QWEN_BASE_URL`。地址不含 `/chat/completions`，模型需支持 JSON 输出。未配置密钥时使用演示，不会在真实模型报错时悄悄改用样例。 |
| 腾讯地图 | `.env` 填 `TENCENT_MAP_API_KEY`，在服务商控制台确认 WebService 权限与配额。地点、定位、天气、路线通过 MCP；静态底图和缺失折线的按需补查使用同一 Key 调用腾讯 WebService。照片、评分、评论只展示上游实际返回的数据。 |
| 12306 | 安装 npm 依赖后，从「数据与连接」独立查询，或在「我的行程 → 出发与抵达」调整起点、时间和换乘。结果只供参考，不登录、不抢票、不下单。余票与票价以[铁路 12306](https://www.12306.cn/)为准。 |
| 麦当劳外卖 | `.env` 填 `MCD_MCP_TOKEN`（[open.mcd.cn](https://open.mcd.cn/mcp/doc) 控制台生成）；聊天里说「帮我点份外卖」即可查菜单、门店、优惠券、订单进度并下单。下单需再设 `XIAOTU_MCD_ORDER=true`，且每次都要在报价卡片上显式确认。创建的是个人账号里真实的**待支付**订单，支付由你自己完成，小途不代付。 |
| 模拟外卖 | 默认关闭。先设 `$env:XIAOTU_FOOD_DEMO = 'true'` 再启动，从顶栏打开。只用模拟菜单、报价和订单，不联网找餐、不真实支付。 |

试着发送「从上海出发去杭州玩 3 天，两人，总预算 1800 元」，然后在行程里查看地图、调整地点或确认下载 ICS。未填写的查询日期默认为北京时间今天，不写成已确认的行程日期；定位需要浏览器授权，精确坐标不会持久保存或发送给 Qwen。预算是估算，日历是文件下载，不自动写入第三方日历。

`.env` 不入仓、不从网页读取；现有密钥无需重填，修改配置后重启。接入 Qwen 后，规划所需的消息上下文与已启用的画像会发送至配置的模型提供商。不要输入证件号、账户密码等无关敏感信息。

## 开发入口

- [开发指南](docs/development.md)：目录、接口、验收与测试。
- [能力管理](docs/capabilities.md)：如何配置 MCP、添加业务 Skill。
- [前端设计](docs/design.md)：页面、交互、本地数据及 Logo 评审。
- [麦当劳外卖](docs/mcd-delivery.md)：意图分支、弱网下单网关与快照对账。
- [弱网实验](docs/reliable-tool-experiment.md)：模拟订单、故障注入和可复现对照。
- [路线图](docs/roadmap.md)与[开发守则](CONTRIBUTING.md)：迭代顺序、GitHub 协作、分工和合并门禁。
