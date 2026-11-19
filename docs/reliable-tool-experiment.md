# 课程实验：模拟外卖与可靠工具执行

实验与小途共用 Python 后端、确认界面和执行层。在模拟商家已提交订单、前端没有收到回执时，对照三种策略。固定菜单和 SQLite 回执均为测试数据，不接触真实商家、支付或地址。

## 怎么运行

先按[启动说明](../README.md#启动)准备环境。从根目录使用激活的虚拟环境（或把命令中的 `python` 换成虚拟环境的 Python）。普通演示只需设置 `XIAOTU_FOOD_DEMO=true` 后启动小途。

要实际复现回环 HTTP 丢回包，在两个终端分别运行：

~~~powershell
# A：模拟商家，每个新操作提交后截断第一次响应
python -m xiaotu.food.sandbox --db .runtime/food-sandbox.sqlite3 --port 3221 --drop-first-response
# B：小途
$env:XIAOTU_FOOD_DEMO = 'true'
$env:XIAOTU_FOOD_SANDBOX_URL = 'http://127.0.0.1:3221'
python -m xiaotu
~~~

打开顶栏「模拟外卖」，看报价后确认，再查看同一报价的结果；不要让两进程共用数据库。LangGraph 检查点保存审批进度，商家和执行层的持久回执确认是否已写入。提交响应未知时按原操作键重试或核对，不创建新的操作来补单。

## 对照实验

~~~powershell
python -m pytest -q -s backend/tests/test_reliability_food.py -k comparison_after_commit_response_drop
python -m xiaotu.food.benchmark --tasks 20 --seeds 17 81 --loss 0 10 20 30
~~~

第一条用本机 HTTP 验证商家提交后断连；矩阵则通过离线模拟传输、同一模拟商家接口和临时数据库跑成对样本，不依赖公网或模型。故障按「种子、任务、尝试」配对，模拟提交后 0%、10%、20%、30% 丢响应，而非所有弱网故障。

| 策略 | 含义 |
| --- | --- |
| 不重试 | 仅发送一次；已提交但丢回包时可能无法确认。 |
| 盲重试 | 重新发送但不复用操作键；可能收到回执同时重复提交。 |
| 可靠网关 | 使用稳定键、单次超时、总截止与有限退避。同一业务键查回执；重复确认不创建第二单。 |

输出 JSONL。成功表示截止时间内收到回执且数据库正好一条提交，另报重复订单、请求与提交次数、未拿到回执的数量和 P95。实验报告用实际输出填表；不可用本机模拟延迟代表真实网络或用本地幂等证明第三方幂等。其他故障、权限和重启靠专项测试验证。
