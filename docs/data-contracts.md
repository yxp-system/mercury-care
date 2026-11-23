# 数据与 API

金额为整数最小单位，与订单币种绑定；不支持混币相加。时间存 UTC Unix seconds，工作台按浏览器时区展示。所有业务实体带 tenant_id、store_id；相关实体通过复合外键约束跨租户关联。

| 实体 | 关键关联/约束 |
|---|---|
| users / memberships | 认证主体、有效状态、店铺岗位、客户范围；不相信 JWT 的岗位字段 |
| orders / order_lines | 实付、已退、处理中占用、行权益；余额不可为负或超付 |
| inventory / stock_ledger | 在库 ≥ 预留 ≥ 0；动作+台账类型唯一 |
| cases / messages / attachments | 订单行归属、版本、入站 source_id、附件哈希与扫描状态 |
| plans / choices / approvals | 方案版本唯一、内容摘要、选择、不可覆盖决定、有效期 |
| actions / provider requests | plan_id 唯一、稳定 request_id、未知与可信回执分开 |
| business_events | 可信来源或显式合成入口、source_id 去重、序号与内容摘要 |
| runs / run_steps / jobs | 运行身份、输入版本、预算、模式、Prompt 哈希、错误、租约与 fence |
| policies | 原文、版本、适用期、来源、审核、向量模型与状态 |
| notifications | 固定接收客户、内容意图哈希、稳定发送关联、合成回执 |
| claims / improvements / audit | 责任主体、证据、版本、复查/结算依据与追加审计 |

核心工单状态：open、waiting_customer、waiting_approval、approved、processing、awaiting_fulfillment、awaiting_resolution、needs_review、resolved、closed。运行状态：queued、running、waiting、completed、failed。动作状态随分支执行显式转换；不可从 unknown 跳过原请求核查。

输入使用 Pydantic 严格字段契约，未知字段拒绝；版本变更冲突返回 409。资源无权与不存在统一 404；岗位不足为 403；身份无效为 401；未配置真实服务为 503。错误含 request_id，不回显密码或完整请求正文。

实际可调用接口以 `/docs`、`/openapi.json` 为准：

- `/api/auth/login`、`/api/me`：身份和当前岗位。
- `/api/orders`、`/api/cases`、`/api/cases/{id}`：业务记录及受理。
- 工单下 `/messages`、`/facts`、`/rules`、`/attachments`、`/analyze`：材料与分析。
- 工单下 `/plans`、`/choice`、`/approvals`、`/actions`：版本化方案、选择、审批、执行。
- `/api/actions/{id}/reconcile`：沿原请求核查；`/api/demo/actions/{id}/events` 只允许演示/测试。
- `/api/webhooks/{tenant}/{store}/{action}`：绑定路径、时间戳和正文的 HMAC，五分钟窗口，事件去重。
- 工单下 `/resolve`、`/close`、`/reopen`、`/notifications`、`/claims`：处理后续。
- `/api/policies`、`/api/policies/{id}/publish|withdraw`：版本与独立审核。
- `/api/operations`、`/api/inventory`、`/api/improvements`、`/api/claims`、`/api/jobs`：经营与运行。

写入 JSON 示例请在 API 文档查看当前 Schema；不需要手工猜字段。Cookie 代理把 token 留在服务端同源路由，客户端不使用 localStorage 存令牌。
