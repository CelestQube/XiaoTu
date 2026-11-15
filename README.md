# 小途 · 旅游出行助手

小途是面向自由行的本地网页原型。通过多轮文字对话生成按天行程、住宿和餐饮候选，以及人民币分项预算。可查看和修改长期偏好，或确认后导出日历文件。

## 启动

需要 Node.js 22 或更高版本。不需要安装 npm 依赖。

```powershell
cd D:\A_Study\University\HUAWEI_ICT\Project
npm start
```

访问 http://127.0.0.1:3210 。若 PowerShell 禁止执行 npm 包装脚本，可直接执行 `node server/index.js`。

## 填写已有的 Qwen API 密钥

打开项目根目录的 **`.env`**（与 `package.json` 同级），填写 `DASHSCOPE_API_KEY=` 等号右侧。无需在聊天中发送密钥。

```dotenv
DASHSCOPE_API_KEY=
QWEN_MODEL=qwen-plus
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

- 上例适用于对应的阿里云百炼兼容接口。若密钥来自其他地域、业务空间、套餐或 Qwen 提供商，另将 `QWEN_BASE_URL`、`QWEN_MODEL` 改为其控制台给出的值。基础地址**不带** `/chat/completions`，模型须支持 Chat Completions JSON 输出。若供应商要求关闭思考模式，可在本地配置添加 QWEN_ENABLE_THINKING=false。参见[百炼接口说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。
- 修改后重启服务。页面显示 Qwen 配置状态不代表鉴权成功；首次聊天由服务器实际向提供商发送请求。模型错误不会静默改成演示结果。
- `.env` 被 Git 忽略且不能从网页读取；`.env.example` 是空白模板。若已设置同名系统环境变量，进程环境优先。
- 可选填 `AMAP_MAPS_API_KEY` 启用[高德地图 MCP](https://developer.amap.com/api/mcp-server/gettingstarted)。未填高德密钥时仍可使用 Qwen，地点卡片会标注“本地演示”。

## 在「我的行程」查看高德地图

- 生成行程后打开「我的行程」，切换日期或点击具体安排下的「查看此地点的图片与评价」。地点按城市和名称（或高德别名）匹配，同名冲突、通用地点和缺失字段不猜测。图片、评分、地址、当日营业时间、人均消费只展示高德实际返回的内容。公开 POI 接口不返回逐条点评，可通过「在高德看详情与点评」打开对应地点。
- 地图按天标注地点。选择已匹配的终点、起点和步行或驾车，再点「查看此段路线」，页面会显示高德返回的路程、耗时、路线步骤及实线；「在高德导航」打开高德网页或手机 App，不在小途内播报。
- 「使用我的位置」只在用户点击且浏览器授权后读取 GPS；坐标仅用于高德转换、逆地理编码、路线和底图。不会写入本地存储或发给 Qwen；离开行程页或点「移除位置」即清除。不授权可正常用行程地点作为起点。
- 使用已有的高德 **Web 服务** Key。默认复用 AMAP_MAPS_API_KEY；如果现有 Key 绑定其他平台，或希望和 MCP 分开，在根目录 .env 添加 AMAP_WEB_SERVICE_KEY= 后填写 Web 服务 Key、重启。地图组件不需要 Web 端（JS API）Key 或安全密钥。高德报 10009 表示 Key 与请求平台不符；若报 10005，按控制台要求检查服务器出口 IP。
- [高德地点搜索与评分](https://lbs.amap.com/api/webservice/guide/api/newpoisearch) · [路线规划](https://lbs.amap.com/api/webservice/guide/api/newroute) · [静态地图](https://lbs.amap.com/api/webservice/guide/api/staticmaps) · [错误码说明](https://lbs.amap.com/api/webservice/guide/tools/info)

## 试用与能力边界

1. 发送「从上海出发去杭州玩3天，两人，总预算1800元，2026-12-11出发」。
2. 追问「把第二天下午改成室内活动」，查看行程与预算。日期、时刻、商家和费用应以官方结果核实。
3. 发送「我常住上海，平时坐高铁，旅行喜欢美食」，在页面上确认后保存画像。
4. 检查出发日期后，明确确认导出 `.ics` 并手动导入日历；不会自动写入第三方账户。

无密钥演示的数据为静态起步样例，覆盖杭州、成都、厦门、北京；其他城市可能只生成待核实的通用模板。预算是区间估算，不含城际交通、实时房价或下单。余票与票价尚无授权数据源，请在[铁路 12306](https://www.12306.cn/)核实。

服务默认仅监听回环地址。画像和草案可能保存在当前浏览器，非多用户账号或数据库。使用 Qwen 时，本次出行需求、有限对话上下文、用于生成方案的相关偏好会发送至配置的提供商；高德仅接收地图查询参数。不要输入身份证号、账号密码或其他不必要的个人敏感信息。

## 检查

```powershell
npm test
```

测试不调用真实 Qwen 或高德。模块位置和继续开发入口见 [开发说明](docs/development.md)。
