# 麦当劳外卖：意图分支与可靠下单网关

小途在意图识别后拉出第四条并行分支处理外卖需求（与闲聊、单项查询、行程创建并列）。分支内只调用麦当劳官方 MCP（`https://mcp.mcd.cn`，Streamable HTTP，Bearer Token，在 [open.mcd.cn](https://open.mcd.cn/mcp/doc) 控制台申请）。**Token 绑定个人麦当劳账号：下单是在该账号里创建真实的待支付订单**，返回支付链接，支付由用户自己完成，小途不代付、不自动扣款。

## 意图分类

| 说法 | 分支动作 |
| --- | --- |
| 麦当劳有什么套餐 / 卖什么 | `menu` 菜单卡片 |
| 能送到哪些门店 / 配送范围 | `stores` 可配送门店 |
| 有哪些优惠券 | `coupons` 优惠券 |
| 订单到哪了 / 外卖进度 | `orders` 最近订单 |
| 帮我点份外卖 / 来一份巨无霸 | `order` 报价 → 显式确认 → 下单 |
| 麦当劳和肯德基哪个好吃 / 什么是麦乐送 | `chat`，不调用工具 |

分类先用确定性的正则规则（`agent/intent.py` 的 `food_request`），无法命中时回退到 Qwen 路由提案（`query.kind="food"`），route 阶段的 `mcd-delivery` Skill 提供补充约束。多地址时分支会反问，短回复（「用第二个」）沿用上一轮上下文继续。

## 查询直连，下单走网关

- **查询类工具**（地址、门店、菜单、优惠券、算价、查单）由对话分支直接调用，只读，失败仅提示。
- **下单（`create-order`）只经可靠网关**执行：对话分支的终点是一张报价卡片，用户点「确认下单」后由 `POST /api/food/confirm`（必须 `confirmed:true`）进入网关。聊天本身永远不会创建订单。

## 弱网下的三类故障与判定

官方 MCP **没有幂等键**（无 Idempotency-Key、无 outTradeNo），防重复下单完全由我方网关负责：

| 故障 | 判定依据 | 网关动作 |
| --- | --- | --- |
| ① 工具未成功调用 | JSON-RPC 错误、业务拒绝（`success:false`）、401/429 | `ToolRejected`：确定没有订单，落盘 `rejected` |
| ② 请求包丢失 | 连接阶段失败（TCP/TLS 未建立、连接被拒）——请求肯定没到服务器 | `ToolSendLost`：允许按同一操作键重发（重发≠重复下单）；全部未送达时终态为 `rejected` |
| ③ 应答包丢失 | 请求已发出但读超时、连接中断、响应损坏 | `ToolTransient`/`ToolUncertain`：**绝不重发**；进入快照对账，对不上就保持 `unknown` |

判定实现在 `tools/mcd_transport.py`（按 httpx 异常阶段分类），语义在 `execution/gateway.py` 的 `ReliableExecutor`。

## 快照对账：让下单只成功执行一次

`create-order` 对每个操作键只发送一次。发送前，网关把账号最近的订单号列表（`order-list`）作为**快照**写入操作行（`food/store.py` 的 `snapshot` 列），因此快照能跨进程重启存活。应答丢失后按以下规则对账（`food/mcd_provider.py` 的 `find_order`）：

1. 重新拉取 `order-list`，剔除快照内已有的订单；
2. 按「门店名 + 应付金额」筛选新订单；
3. 恰好一笔 → 取详情校验并落盘 `placed`；零笔 → `not_found_yet`，稍后查询状态会再次对账；多笔或无快照 → 保持 `unknown`，提示用户去麦当劳 App 核对，**不猜**。

快照的价值：如果账号里本来就有一笔同门店同金额的常点订单，它已在快照内，绝不会被误认成本次操作的单。没有快照时宁可 `unknown` 也不匹配——宁可多查一次，不可认错一单。

金额统一用「分」。注意：`calculate-price` 返回分，`create-order`/`query-order` 的 `orderDetail` 是元字符串，解析集中在 `tools/mcd.py`。实付与报价不一致时订单照常落盘，但回执带 `amountMismatch` 标记，界面提示核对后再支付。

## 配置与试用

```powershell
# .env
MCD_MCP_TOKEN=你的Token            # 只填 token：查询可用，下单被 403 拦截
XIAOTU_MCD_ORDER=true              # 显式开启真实下单
```

`config/mcp_settings.json` 的 `mcd.disabled` 可整体停用；`allowedTools` 是最小白名单（含 `create-order`）。`python -m xiaotu --check-capabilities` 自检登记。限流 600 次/分钟/Token，超限按确定未执行处理。

试用：聊天里发「帮我点份外卖」（建议栏也有入口）→ 选地址 → 说「来一份巨无霸和一份薯条」→ 核对报价卡片 → 点「确认下单」→ 得到订单卡片（支付链接 + 订单详情路由 `#/order/<quoteId>`）。订单详情页可随时刷新状态；弱网下若结果待核对，页面会明确提示**不要重新下单**。

## 测试

全部离线（不访问真实麦当劳）：`pytest -q backend` 覆盖传输层丢包分类、金额换算、意图路由、报价校验，以及网关的关键性质——应答丢失后对账恰好一单、请求丢失安全重发仍是一单、全部未送达判 rejected、并发确认共享一个操作、快照排除既有同款订单、重启后恢复。`npm test` 覆盖前端卡片转义与接线。

真机联调注意：响应字段名按官方文档实现，个别字段（金额键名、列表包裹结构）若与实际返回不符，只需调整 `tools/mcd.py` 中集中的解析候选。官方条款面向个人非商业用途；商用或代下单场景请先与麦当劳（mcp-support@cn.mcd.com）确认合规边界。
