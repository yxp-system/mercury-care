# 运行、发布与恢复

## 本地

启动见 README。API 和 worker 必须使用同一配置与数据库；缺 worker 时分析/执行会保留在队列。`/health/live` 只表示进程可回应；`/health/ready` 同时检查数据库和表存在，企业模式缺必要集成返回503。工作台运行页显示队列状态、次数与错误。日志保留任务类型/错误码，不打印秘密或完整提示词。

演示停止可用 `scripts/stop-demo.ps1`，它核对保存 PID 的启动时间，仅停止本任务进程。容器用 `docker compose down` 保留卷。日常不得删库、重置卷或变更原请求编号解决未决动作。

## 发布

1. 验证依赖锁、CI、迁移和分支业务测试，制品与提交关联。
2. 先在隔离环境运行迁移，迁移身份与应用身份分离。非兼容变更需独立迁移策略，禁止生产自动清表。
3. 配置固定 OIDC/模型地址、企业资料权限、TLS、Secure Cookie、精确 Origin、集中限流和秘密服务。当前浏览器 SSO 仍待实现。
4. 企业业务适配器完成前不开放生产，readiness 会失败。有限试运行范围必须对应真实接入证据及明确业务门禁。

应用回滚不撤销已经发生的退款或物流动作。回滚前核对数据库向后兼容，必要时执行向前修复。当前 compose 为单机测试部署配置，生产容量与高可用需另行设计并测量。

## 备份和隔离恢复

SQLite 演示先停止 API/worker，再执行：

```bash
uv run python scripts/backup.py .data/backups/isolated-demo
uv run python scripts/verify_backup.py .data/backups/isolated-demo
```

备份脚本使用 SQLite backup API、复制检查点与附件并计算 SHA256，排除 `.env`。现有备份目录不会覆盖。校验程序验证路径、摘要、数据库完整性和外键，再输出业务记录数量。恢复时复制到新的隔离目录，修改数据库与 data_dir，先设置 `MERCURY_WRITES_ENABLED=false`，核对记录和未决请求后再决定开放。

PostgreSQL 需单独为目标环境配置加密 `pg_dump`/物理备份、WAL 与附件版本备份，秘密通过受控配置恢复。owner/migration 凭据不能分发到业务容器。当前仓库未完成生产恢复演练。

真实恢复顺序：冻结写入/外发 → 停止并隔离旧worker及在途请求出口 → 恢复隔离库/附件 → 核对备份水位至故障时刻完整窗口 → 补回外部已完成但本地丢失的动作与原请求 → 核查未知、库存、退款、通知及 Inbox/Outbox → 负责人决定是否解冻。窗口不完整时保持冻结，不创建新动作ID补偿。

## 未知与故障

prepared 表示意图已保存；unknown 表示未取得可信结果，不能当失败释放金额或库存。先调用原动作 `/reconcile`；合成提供方按 request_id 查询，查无记录仍保留 unknown。真实接口不支持幂等或查询时需人工核查策略，不能把本地防重当提供方保证。

worker 的 `dead` 任务可由 executor 明确重试原任务；不会重置预算或原请求关联。预算耗尽要由负责人诊断，不通过改 run_id 绕过费用边界。模型真实模式故障明确失败。

Prometheus `/metrics` 需 operations 身份，含 HTTP 次数和时延，标签不含工单ID。队列积压、未知动作时长、审批超期、模型错误与恢复告警的实际通知接入尚未完成，须由目标运维平台落地并演练。
