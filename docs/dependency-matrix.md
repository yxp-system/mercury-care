# 依赖与依据

检索日期 2026-11-23。直接依赖取自 PyPI/npm 元数据，实际安装结果锁定于 `uv.lock` 和 `apps/web/package-lock.json`。不使用运行时自动漂移的 latest 镜像。

| 组件 | 锁定版本 | 用途 |
|---|---|---|
| Python | 开发3.12.9，容器3.12.12 | 后端/worker；项目允许3.12–3.13 |
| Node.js | 开发24.18.0，容器24.11.1 | 前端构建/运行 |
| FastAPI / Uvicorn | 0.165.0 / 0.54.0 | HTTP服务 |
| SQLAlchemy / Alembic | 2.1.9 / 1.20.1 | ORM、约束和迁移 |
| psycopg / pgvector Python | 3.3.9 / 0.5.1 | PostgreSQL 与向量 |
| LangGraph | 1.2.24 | 受限工作流 |
| PostgreSQL / SQLite checkpoint | 3.2.1 / 3.1.3 | 持久化恢复 |
| PyJWT / pwdlib | 2.15.1 / 0.4.2 | 验签/Argon2 |
| Next.js | 16.4.6 | 工作台与同源代理 |
| React | 19.2.4 | 工作台组件 |
| TypeScript | 5.9.3 | 静态类型 |
| pgvector 容器 | 0.8.1-pg17 | 本地部署和CI测试服务 |

已读取官方资料并按实际安装 API 编写：

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：使用持久 saver，不能用内存表示跨进程恢复。
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：节点恢复会重放，`Command(resume)` 只是唤醒；重新读取批准。
- [PostgreSQL RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)：受限角色测试，不能用超级用户结果判断隔离。
- [FastAPI deployment](https://fastapi.tiangolo.com/deployment/concepts/)：进程、重启、资源和 HTTPS 需实际部署处理。
- [Next.js](https://nextjs.org/docs)：App Router、Route Handler 与 standalone 构建。

供应方调用格式仍需真实账号与模型能力联调；本地结构测试不是提供方兼容认证。
