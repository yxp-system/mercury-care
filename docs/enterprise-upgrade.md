# v0.2 企业能力升级

本轮依据原始阶段 03、06、10、14、15、17、18、19 继续实现。企业身份、业务平台和部署目标均由使用方标为“未定”，所以真实接入与生产验收仍未完成。

## 已落地的应用能力

| 能力 | 实现与关键边界 | 验证入口 |
|---|---|---|
| 企业浏览器登录 | OIDC 授权码、PKCE S256、state、nonce、RS256、issuer/audience/azp/at_hash；固定提供方地址；浏览器持随机 HttpOnly 会话句柄 | tests/test_enterprise.py |
| 会话管理 | 数据库可撤销会话；刷新令牌 Fernet 加密；支持密钥轮换；绝对与空闲有效期；刷新失败撤销会话，不复用可能已轮换的令牌 | sessions.py |
| 多实例限流 | 数据库原子窗口，账号与来源共同限制；不存原始账号/IP；过期窗口清理 | test_shared_login_rate_limit |
| 运行接管 | 持久取消、活动任务时限、等待时限、超时交接、租约续期、旧 fence 拒绝写入、PostgreSQL 锁连接身份复查 | runtime.py、worker.py |
| 附件隔离 | ClamAV INSTREAM；超时或未知响应保留隔离；下载重新核对 SHA256；感染文件不能通过演示例外下载 | attachments.py |
| 知识导入 | Markdown/纯文本持久导入队列、章节分块、可选真实嵌入、人员/岗位 ACL、审核分离、完整索引原子发布和旧版本撤回 | knowledge.py |
| 分批履约 | 包裹关联动作、分包数量、逐包出库/送达、累计数量上限、重复事件防重；全部包裹送达后才进入解决确认 | Shipment / apply_event |
| 通知恢复 | 保存发送意图与稳定原请求、投递前复核工单版本、未知结果仅核查原请求、独立记录尝试和回执 | notifications.py |
| 工作台 | 企业登录入口、知识导入与审核表单、停止分析、包裹与通知记录、交接与上线门禁、鉴权 SSE 刷新 | Next.js 工作台 |
| 交付运维 | 显式 release-check、受限容器覆盖配置、Prometheus 队列/未知动作指标、告警样例、隔离 PostgreSQL 备份恢复脚本 | CLI / deploy / scripts |

所有通知和履约用例仍为显式 synthetic。OIDC 测试使用本地生成 RSA 密钥和模拟 token endpoint；ClamAV 测试使用本地 TCP 协议服务。它们验证实现和失败边界，不代表企业 IdP 或已部署杀毒服务的联调证明。

## 配置企业身份

使用提供方官方 metadata 填写 `.env.example` 中的固定端点、client ID、issuer、JWKS 和 redirect URI。回调为 `https://<站点>/api/auth/oidc/callback`，不接受客户端指定任意回调地址。刷新模式请求 `offline_access`，机密客户端使用 client_secret_basic。仅支持 RS256；不支持该契约的提供方需另行适配并验证。

`MERCURY_SESSION_ENCRYPTION_KEYS` 为 Fernet key 的 JSON 数组。新 key 放第一位，旧 key 保留到旧会话过期后再移除；密钥通过部署环境注入，不进入仓库。撤销企业应用会话会清空保存的 refresh token；提供方全局退出/令牌撤销取决于所选 IdP，当前未调用未知供应商端点。

使用 `scripts/provision_member.py` 将官方 issuer 的 subject 映射到成员关系。组织/店铺输入只选择已有授权，不会自动授予权限。管理员在受控终端运行，客户身份不能同时获得员工岗位。

## 迁移与恢复

新增迁移 `238f42efcd92`。现有已出库动作从原 `ship` 台账回填兼容包裹记录，保留原动作与原请求；历史未发送通知的版本默认 0，需要人工复核。旧迁移保持原样。

升级前停止 API/worker 并备份；升级后先以冻结写入模式启动和核查。`scripts/postgres_recovery.py` 使用官方 `pg_dump`/`pg_restore` 客户端，密码经进程环境传入。可用 `MERCURY_PG_CLIENT_CONTAINER` 指定已有 PostgreSQL 容器中的同版本工具，归档经标准输入/输出流传输，不把密码放进命令参数。恢复只允许空的新数据库且名字包含 `_restore_`，验证归档和附件哈希、撤销恢复出来的会话、输出恢复记录；不会自动解除冻结。完整外部动作恢复窗口、旧实例隔离和生产 RPO/RTO 需要在选定环境实测。

```bash
# 先停止消费者，受控环境设置 MERCURY_WRITES_ENABLED=false
uv run python scripts/postgres_recovery.py backup .data/backup-001 --workers-stopped
# 指向新建的隔离 *_restore_* 数据库与新的 DATA_DIR 后运行
uv run python scripts/postgres_recovery.py restore .data/backup-001 --workers-stopped
uv run python -m mercury.cli release-check
```

release-check 返回非零退出码表示上线条件仍有缺项。当前 OMS/WMS、支付、物流和真实通知没有选定供应商，门禁明确阻塞；不能用修改门禁或切换环境名代替接入。

CI 中 `scripts/recovery_drill.py` 仅允许显式 test 环境和包含 test 的数据库名。prepare 通过应用 API 建立客户选择、独立批准和原请求，再注入合成丢回执、附件和一次性会话；verify 检查恢复后的原请求、库存占用、附件哈希、会话撤销、登录事务清理和禁止派发。测试记录与生产外部动作窗口的验证分开，实际执行结果见 [验收记录](acceptance.md)。

## 待选型后完成

官方接口、沙箱与认证；订单/仓储真实源和状态映射；远程原子预留/过期/续期；支付最终结算和退款查询；物流包裹及事件完整性；通知渠道偏好/幂等/查询；对象存储和扫描部署；真实模型/检索质量集；目标环境容量、灾备和人工接管责任。尚未实现的其他业务与运维边界见 [limitations.md](limitations.md)。
