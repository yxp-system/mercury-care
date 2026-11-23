# 架构与可靠性

Next.js 工作台通过同源代理访问 FastAPI。浏览器登录状态使用 HttpOnly、SameSite=Strict Cookie；写请求校验 Origin。API 验证 Bearer 签名、issuer/audience/有效期，再逐次读取服务端成员授权。租户/店铺字段不能自行赋权。

`services.py` 持有业务事务，`models.py` 提供金额、库存、方案、动作和跨租户外键约束。API 负责输入、权限入口和错误转换。`workflow.py` 是唯一外层 LangGraph；每个角色实际加载版本可哈希的 Prompt，网关记录模式、预算、耗时与未知用量。Supervisor 只能选择代码计算的候选，物流走短路径，其他分支受证据依赖约束。

`jobs` 是事务 Outbox。独立 worker 领取任务，通过数据库租约与 fence 识别旧 owner。PostgreSQL advisory lock 或本地文件锁覆盖整个目标任务，防止仅租约过期后两个进程同时写图检查点。网络调用前预留预算；异常保留未知用量，等待不占常驻线程。SQLite 仅用于单机演示，使用 WAL 和 BEGIN IMMEDIATE 序列化写事务。

数据库和 LangGraph checkpoint 不假定跨表组件自动双写原子性。先提交业务意图与 Outbox，再执行图节点或派发。节点重放查询现有方案/动作；恢复传入的 `wake` 只唤醒，授权再次读取业务数据库。检查点使用 PostgresSaver 或 SqliteSaver，不使用内存 saver 冒充持久化。

本地提供方在独立提交边界保存稳定 request_id 与内容摘要，再观察回执。故障注入可模拟“提供方已保存、调用方没收到”，核查复用原请求。此实现验证本地契约；它不证明任何真实 WMS 或支付方的跨系统一次效果。

政策按租户、店铺、类型、地区和订单日期筛选后排序。PostgreSQL 使用全文检索与已配置 pgvector 的排名融合；SQLite 明确只用关键词。真实嵌入绑定模型与 1536 维索引；模型变化需新版本重建。原文、哈希、审核和撤回状态保留。当前政策为整篇条款级记录，批量解析/分块导入和细粒度 ACL 见限制清单。

当前单机拓扑：web、API、worker、PostgreSQL，共享附件卷。Compose 不具备数据库高可用或对象存储冗余；需要生产部署方另行完成。
