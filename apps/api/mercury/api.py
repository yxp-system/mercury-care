import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from jwt import PyJWKClient
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mercury import schemas as p
from mercury import services as svc
from mercury.auth import AuthContext, authenticate, get_case, login
from mercury.config import Settings
from mercury.db import Database, scoped
from mercury.errors import DomainError, require
from mercury.models import (
    Action,
    Approval,
    Attachment,
    Audit,
    Case,
    Choice,
    Claim,
    Improvement,
    Inventory,
    Job,
    Message,
    Notification,
    Order,
    OrderLine,
    Plan,
    Policy,
    Run,
    RunStep,
    User,
    now,
    uid,
)
from mercury.workflow import start_run

log = logging.getLogger("mercury.api")
REQUESTS = Counter("mercury_http_requests_total", "API requests", ["method", "status"])
LATENCY = Histogram("mercury_http_duration_seconds", "API latency", ["method"])
Identity = Annotated[AuthContext, Depends(authenticate)]


def create_app(settings=None):
    settings = settings or Settings()
    db = Database(settings)

    @asynccontextmanager
    async def lifespan(_):
        yield
        db.engine.dispose()

    app = FastAPI(title="MercuryCare", version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.db = settings, db
    app.state.jwks = (
        PyJWKClient(settings.oidc_jwks_url, cache_keys=True) if settings.auth_mode == "oidc" else None
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Tenant-Id", "X-Store-Id"],
    )
    login_attempts = defaultdict(deque)

    @app.middleware("http")
    async def telemetry(request, call_next):
        started = time.monotonic()
        request.state.request_id = uid()
        response = await call_next(request)
        REQUESTS.labels(request.method, str(response.status_code)).inc()
        LATENCY.labels(request.method).observe(time.monotonic() - started)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message, "request_id": request.state.request_id}},
            status_code=exc.status,
        )

    @app.exception_handler(IntegrityError)
    async def constraint_error(request, _):
        return JSONResponse(
            {
                "error": {
                    "code": "constraint_conflict",
                    "message": "记录冲突，请刷新并核查原请求",
                    "request_id": request.state.request_id,
                }
            },
            status_code=409,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo passwords, uploaded content or request bodies in validation errors.
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_input",
                    "message": "请求字段不符合接口契约",
                    "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
                    "request_id": request.state.request_id,
                }
            },
            status_code=422,
        )

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        try:
            with db.session() as s:
                s.execute(text("SELECT 1"))
                s.execute(select(User.id).limit(1))
        except Exception:
            return JSONResponse(
                {"status": "not_ready", "database": "unavailable_or_unmigrated"}, status_code=503
            )
        if settings.env in {"staging", "production"}:
            return JSONResponse(
                {
                    "status": "not_ready",
                    "database": "connected",
                    "reason": "required_business_integrations_not_connected",
                },
                status_code=503,
            )
        return {
            "status": "ready",
            "database": "connected",
            "business": settings.business_mode,
            "model": settings.model_mode,
            "external_business": "not_configured",
        }

    @app.post("/api/auth/login")
    def local_login(payload: p.LoginInput, request: Request):
        key = request.client.host if request.client else "unknown"
        attempts = login_attempts[key]
        while attempts and attempts[0] < now() - 60:
            attempts.popleft()
        require(len(attempts) < 15, "rate_limited", "登录尝试过多，请稍后重试", 429)
        attempts.append(now())
        return login(db, payload.username, payload.password, payload.tenant_id, payload.store_id)

    @app.get("/api/me")
    def me(ctx: Identity):
        with db.session() as s:
            user = s.get(User, ctx.user_id)
            return {
                "id": ctx.user_id,
                "name": user.display_name,
                "tenant_id": ctx.tenant_id,
                "store_id": ctx.store_id,
                "roles": sorted(ctx.roles),
                "customer_id": ctx.customer_id,
                "environment": settings.env,
            }

    @app.get("/api/capabilities")
    def capabilities(ctx: Identity):
        return {
            "environment": settings.env,
            "identity": settings.auth_mode,
            "business": settings.business_mode,
            "model": settings.model_mode,
            "model_name": settings.llm_model or None,
            "retrieval": "hybrid_configured_unverified" if settings.embedding_model else "keyword",
            "database": db.engine.dialect.name,
            "writes_enabled": settings.writes_enabled,
            "integrations": [
                {"name": name, "status": "not_connected"} for name in ["OMS", "WMS", "支付", "物流", "通知"]
            ],
        }

    @app.get("/api/orders")
    def orders(ctx: Identity):
        with db.session(ctx) as s:
            query = select(Order).where(*scoped(Order, ctx))
            if ctx.customer_id:
                query = query.where(Order.customer_id == ctx.customer_id)
            items = []
            for order in s.scalars(query):
                lines = s.scalars(
                    select(OrderLine).where(*scoped(OrderLine, ctx), OrderLine.order_id == order.id)
                )
                items.append(svc.record_dict(order) | {"lines": [svc.record_dict(line) for line in lines]})
            return items

    @app.get("/api/cases")
    def cases(ctx: Identity, status: str = "", q: str = ""):
        with db.session(ctx) as s:
            query = select(Case).where(*scoped(Case, ctx))
            if ctx.customer_id:
                query = query.where(Case.customer_id == ctx.customer_id)
            if status:
                query = query.where(Case.status == status)
            if q:
                query = query.where(
                    Case.subject.contains(q[:100], autoescape=True)
                    | Case.order_id.contains(q[:100], autoescape=True)
                )
            result = []
            for case in s.scalars(query.order_by(Case.updated_at.desc()).limit(200)):
                order = svc.owned(s, Order, ctx, case.order_id)
                line = svc.owned(s, OrderLine, ctx, case.line_id)
                result.append(
                    svc.record_dict(case)
                    | {
                        "customer_name": order.customer_name,
                        "sku": line.sku,
                        "product_title": line.title,
                        "currency": order.currency,
                    }
                )
            return result

    @app.post("/api/cases", status_code=201)
    def create_case(payload: p.CaseInput, ctx: Identity):
        return svc.new_case(db, ctx, payload)

    @app.get("/api/cases/{case_id}")
    def case_detail(case_id: str, ctx: Identity):
        with db.session(ctx) as s:
            case = get_case(s, ctx, case_id)
            result = svc.record_dict(case)
            result["notification_suggestion"] = svc.status_message(case.status)
            result["order"] = svc.record_dict(svc.owned(s, Order, ctx, case.order_id))
            result["line"] = svc.record_dict(svc.owned(s, OrderLine, ctx, case.line_id))
            for key, model in [
                ("messages", Message),
                ("plans", Plan),
                ("actions", Action),
                ("attachments", Attachment),
                ("notifications", Notification),
            ]:
                result[key] = [
                    svc.record_dict(row)
                    for row in s.scalars(
                        select(model)
                        .where(*scoped(model, ctx), model.case_id == case_id)
                        .order_by(model.created_at)
                    )
                ]
            plans = [item["id"] for item in result["plans"]]
            for key, model in [("choices", Choice), ("approvals", Approval)]:
                result[key] = [
                    svc.record_dict(row)
                    for row in s.scalars(select(model).where(*scoped(model, ctx), model.plan_id.in_(plans)))
                ]
            if not ctx.customer_id:
                result["runs"] = []
                for run in s.scalars(
                    select(Run).where(*scoped(Run, ctx), Run.case_id == case_id).order_by(Run.created_at)
                ):
                    steps = [
                        svc.record_dict(step)
                        for step in s.scalars(
                            select(RunStep)
                            .where(*scoped(RunStep, ctx), RunStep.run_id == run.id)
                            .order_by(RunStep.created_at)
                        )
                    ]
                    result["runs"].append(svc.record_dict(run) | {"steps": steps})
                result["audit"] = [
                    svc.record_dict(row)
                    for row in s.scalars(
                        select(Audit)
                        .where(*scoped(Audit, ctx), Audit.object_id == case_id)
                        .order_by(Audit.created_at)
                    )
                ]
            else:
                result["approvals"] = [
                    {"plan_id": a["plan_id"], "decision": a["decision"]} for a in result["approvals"]
                ]
            return result

    @app.post("/api/cases/{case_id}/messages")
    def add_message(case_id: str, payload: p.MessageInput, ctx: Identity):
        return svc.add_message(db, ctx, case_id, payload)

    @app.get("/api/cases/{case_id}/facts")
    def case_facts(case_id: str, ctx: Identity):
        ctx.require_role("agent", "approver", "executor", "operations")
        return svc.facts(db, ctx, case_id)

    @app.get("/api/cases/{case_id}/rules")
    def case_rules(case_id: str, ctx: Identity):
        ctx.require_role("agent", "approver", "knowledge", "knowledge_reviewer")
        return svc.search_policies(db, ctx, case_id)

    @app.post("/api/cases/{case_id}/analyze", status_code=202)
    def analyze(case_id: str, ctx: Identity):
        return start_run(db, ctx, case_id)

    @app.post("/api/cases/{case_id}/plans", status_code=201)
    def plan(case_id: str, payload: p.PlanInput, ctx: Identity):
        return svc.create_plan(db, ctx, case_id, payload)

    @app.post("/api/cases/{case_id}/choice")
    def choice(case_id: str, payload: p.ChoiceInput, ctx: Identity):
        return svc.customer_choice(db, ctx, case_id, payload)

    @app.post("/api/cases/{case_id}/approvals")
    def approval(case_id: str, payload: p.ApprovalInput, ctx: Identity):
        return svc.approve(db, ctx, case_id, payload)

    @app.post("/api/cases/{case_id}/actions", status_code=202)
    def execute(case_id: str, payload: p.ExecuteInput, ctx: Identity):
        return svc.prepare_action(db, ctx, case_id, payload)

    @app.post("/api/actions/{action_id}/reconcile")
    def reconcile(action_id: str, ctx: Identity):
        ctx.require_role("executor")
        return svc.reconcile_action(db, ctx, action_id)

    @app.post("/api/demo/actions/{action_id}/events")
    def synthetic_event(action_id: str, payload: p.EventInput, ctx: Identity):
        require(
            settings.env in {"demo", "test"} and settings.business_mode == "synthetic",
            "demo_disabled",
            "模拟事件仅允许本地合成环境",
            403,
        )
        return svc.apply_event(db, ctx, action_id, payload)

    @app.post("/api/webhooks/{tenant_id}/{store_id}/{action_id}")
    async def webhook(tenant_id: str, store_id: str, action_id: str, request: Request):
        secret = settings.webhook_secret.get_secret_value()
        require(bool(secret), "webhook_disabled", "外部回调尚未配置", 503)
        timestamp = request.headers.get("x-mercury-timestamp", "")
        require(
            timestamp.isdigit() and abs(now() - int(timestamp)) <= 300, "expired_event", "回调时间无效", 401
        )
        body = await request.body()
        require(len(body) <= 10000, "payload_large", "回调内容过大", 413)
        expected = hmac.new(
            secret.encode(), (timestamp + ":" + request.url.path + ":").encode() + body, hashlib.sha256
        ).hexdigest()
        require(
            hmac.compare_digest(expected, request.headers.get("x-mercury-signature", "")),
            "invalid_signature",
            "回调签名不正确",
            401,
        )
        payload = p.EventInput.model_validate_json(body)
        ctx = AuthContext("webhook", tenant_id, store_id, frozenset({"executor"}))
        return svc.apply_event(db, ctx, action_id, payload)

    @app.post("/api/cases/{case_id}/resolve")
    def resolve(case_id: str, payload: p.ClosureInput, ctx: Identity):
        return svc.close_case(db, ctx, case_id, payload, resolve=True)

    @app.post("/api/cases/{case_id}/close")
    def close(case_id: str, payload: p.ClosureInput, ctx: Identity):
        return svc.close_case(db, ctx, case_id, payload)

    @app.post("/api/cases/{case_id}/reopen")
    def reopen(case_id: str, payload: p.ClosureInput, ctx: Identity):
        return svc.close_case(db, ctx, case_id, payload, reopen=True)

    @app.post("/api/cases/{case_id}/notifications", status_code=202)
    def notification(case_id: str, payload: p.NotificationInput, ctx: Identity):
        return svc.notify_customer(db, ctx, case_id, payload)

    @app.post("/api/cases/{case_id}/attachments", status_code=201)
    async def attachment(case_id: str, file: UploadFile, ctx: Identity):
        ctx.require_role("agent", "customer")
        with db.session(ctx) as s:
            get_case(s, ctx, case_id)
        allowed = {"image/png", "image/jpeg", "application/pdf", "text/plain"}
        require(file.content_type in allowed, "unsupported_file", "仅支持 PNG、JPEG、PDF 和纯文本", 415)
        content = await file.read(settings.upload_limit + 1)
        require(0 < len(content) <= settings.upload_limit, "file_size", "附件为空或超过5MB", 413)
        magic = {
            "image/png": b"\x89PNG\r\n\x1a\n",
            "image/jpeg": b"\xff\xd8\xff",
            "application/pdf": b"%PDF-",
        }
        require(
            file.content_type not in magic or content.startswith(magic[file.content_type]),
            "file_mismatch",
            "文件内容与声明类型不一致",
            415,
        )
        identity = uid()
        folder = settings.data_dir / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / identity
        try:
            path.write_bytes(content)
            with db.session(ctx, write=True) as s:
                item = Attachment(
                    **svc.scope_values(ctx),
                    id=identity,
                    case_id=case_id,
                    filename=Path(file.filename or "attachment").name[:200],
                    content_type=file.content_type,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    scan_status="unscanned",
                )
                s.add(item)
                svc.audit(s, ctx, "attachment.received", case_id, {"attachment_id": identity})
                s.flush()
                return svc.record_dict(item)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @app.get("/api/attachments/{attachment_id}")
    def download(attachment_id: str, ctx: Identity):
        with db.session(ctx) as s:
            item = svc.owned(s, Attachment, ctx, attachment_id)
            get_case(s, ctx, item.case_id)
            require(
                item.scan_status == "clean" or settings.env in {"demo", "test"},
                "quarantined",
                "附件尚未通过安全扫描",
                423,
            )
            return FileResponse(
                settings.data_dir / "uploads" / item.id,
                filename=item.filename,
                media_type="application/octet-stream",
            )

    @app.get("/api/policies")
    def policies(ctx: Identity):
        ctx.require_role("agent", "approver", "knowledge", "knowledge_reviewer")
        with db.session(ctx) as s:
            return [
                svc.record_dict(row)
                for row in s.scalars(
                    select(Policy).where(*scoped(Policy, ctx)).order_by(Policy.created_at.desc())
                )
            ]

    @app.post("/api/policies", status_code=201)
    def policy_draft(payload: p.PolicyInput, ctx: Identity):
        return svc.policy_create(db, ctx, payload)

    @app.post("/api/policies/{policy_id}/publish")
    def policy_publish(policy_id: str, payload: p.PublishInput, ctx: Identity):
        return svc.policy_publish(db, ctx, policy_id, payload)

    @app.post("/api/policies/{policy_id}/withdraw")
    def policy_withdraw(policy_id: str, payload: p.PublishInput, ctx: Identity):
        return svc.policy_publish(db, ctx, policy_id, payload, withdraw=True)

    @app.get("/api/operations")
    def operations(ctx: Identity):
        return svc.operations(db, ctx)

    @app.get("/api/inventory")
    def inventory(ctx: Identity):
        ctx.require_role("agent", "executor", "operations")
        with db.session(ctx) as s:
            return [
                svc.record_dict(row) | {"available": row.on_hand - row.reserved}
                for row in s.scalars(select(Inventory).where(*scoped(Inventory, ctx)))
            ]

    @app.post("/api/cases/{case_id}/claims", status_code=201)
    def new_claim(case_id: str, payload: p.ClaimInput, ctx: Identity):
        return svc.create_claim(db, ctx, case_id, payload)

    @app.get("/api/claims")
    def claims(ctx: Identity):
        ctx.require_role("operations")
        with db.session(ctx) as s:
            return [svc.record_dict(row) for row in s.scalars(select(Claim).where(*scoped(Claim, ctx)))]

    @app.patch("/api/claims/{claim_id}")
    def claim_update(claim_id: str, payload: p.ClaimUpdate, ctx: Identity):
        return svc.update_claim(db, ctx, claim_id, payload)

    @app.get("/api/improvements")
    def improvements(ctx: Identity):
        ctx.require_role("operations")
        with db.session(ctx) as s:
            return [
                svc.record_dict(row)
                for row in s.scalars(select(Improvement).where(*scoped(Improvement, ctx)))
            ]

    @app.post("/api/improvements", status_code=201)
    def improvement_create(payload: p.ImprovementInput, ctx: Identity):
        return svc.create_improvement(db, ctx, payload)

    @app.patch("/api/improvements/{improvement_id}")
    def improvement_review(improvement_id: str, payload: p.ImprovementUpdate, ctx: Identity):
        return svc.review_improvement(db, ctx, improvement_id, payload)

    @app.get("/api/jobs")
    def jobs(ctx: Identity):
        ctx.require_role("operations", "executor")
        with db.session(ctx) as s:
            return [
                svc.record_dict(row)
                for row in s.scalars(
                    select(Job).where(*scoped(Job, ctx)).order_by(Job.created_at.desc()).limit(100)
                )
            ]

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str, ctx: Identity):
        ctx.require_role("executor")
        with db.session(ctx, write=True) as s:
            job = svc.owned(s, Job, ctx, job_id, True)
            require(job.status == "dead", "job_not_failed", "只有失败任务可以人工重试")
            job.status, job.due_at, job.error = "pending", now(), ""
            svc.audit(s, ctx, "job.retry", job.id)
            return svc.record_dict(job)

    @app.get("/metrics")
    def metrics(ctx: Identity):
        ctx.require_role("operations")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
