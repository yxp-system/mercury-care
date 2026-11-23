# 验收记录

执行日期：2026-11-23。环境：Windows、Python 3.12.9、Node.js 24.18.0；业务源为 synthetic，模型为 deterministic_test。所有数据为仓库种子和隔离测试生成。

已执行：

```text
uv sync
uv run python -m mercury.cli configure
uv run python -m mercury.cli migrate
uv run python -m mercury.cli seed
uv run pytest -q --tb=short --junitxml=.data/local-tests.xml
```

当前后端结果：**41 passed, 1 skipped**，133.49 秒。跳过项为需 PostgreSQL 的受限角色 RLS 测试；本机未安装 PostgreSQL/Docker。其余测试使用真实 SQLite 数据库、真实应用服务与显式测试模型。

覆盖证据：

- `test_business.py`：五角色完整补发、审批、库存守恒、客户确认与结案/重开；退款、退货、物流、少件、错件、取消、改址；重复请求/事件30次、并发预留、退款未知占用和失败释放。
- `test_security.py`：JWT伪造、即时撤权、租户/客户隔离、岗位拒绝、跨租户外键、附件类型与越权、入站去重、乐观版本、政策适用期/审核/撤回、Webhook签名绑定路径、生产模式拒绝替身。
- `test_recovery.py`：新 worker 进程读取持久 checkpoint、等待不增加预算、批准后新进程恢复、规则撤回拒绝执行、通知防重、索赔独立、整改样本门禁、未知查无不重发、持久预算、审批执行身份分离、原请求过期后仍可查询、二次赔付权益限制。

附加验证：真实模式通过 MockTransport 模拟非法动作和鉴权失败，验证无越权执行、无静默替身、未知用量继续占用；这不是对真实模型提供商的调用证明。

隔离 SQLite 备份已执行，SHA256、integrity_check、foreign_key_check 均通过，16个合成工单完整。独立 API 与 worker 默认配置启动成功，主案例运行到 waiting，记录9次模型角色调用。

后续前端构建、浏览器和远程 CI 验证结果会追加于本文件。当前不填未经运行的真实模型效果、生产吞吐、企业收益或上线签署。
