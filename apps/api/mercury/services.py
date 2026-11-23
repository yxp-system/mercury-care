import hashlib
import json
import math
import re

import httpx
from sqlalchemy import func, select, update

from mercury.auth import get_case
from mercury.db import scoped
from mercury.errors import DomainError, require
from mercury.models import (
    Action,
    Approval,
    Audit,
    BusinessEvent,
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
    ProviderRequest,
    Run,
    StockLedger,
    now,
    uid,
)

TERMINAL = {"delivered", "succeeded", "inspected", "investigated", "failed", "cancelled"}
REPLACEMENTS = {"replacement", "missing", "wrong"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def scope_values(ctx):
    return {"tenant_id": ctx.tenant_id, "store_id": ctx.store_id}


def record_dict(obj):
    return {
        column.name: getattr(obj, column.name)
        for column in obj.__table__.columns
        if column.name not in {"embedding", "password_hash"}
    }


def audit(s, ctx, verb, object_id, detail=None):
    s.add(
        Audit(**scope_values(ctx), actor_id=ctx.user_id, verb=verb, object_id=object_id, detail=detail or {})
    )


def enqueue(s, ctx, kind, target_id, key):
    job = s.scalar(select(Job).where(Job.dedupe_key == key))
    if not job:
        job = Job(**scope_values(ctx), kind=kind, target_id=target_id, dedupe_key=key)
        s.add(job)
    return job


def bump(case):
    case.version += 1
    case.updated_at = now()


def check_version(case, expected):
    require(case.version == expected, "version_conflict", "记录已更新，请刷新后重新操作")


def status_message(status):
    return {
        "waiting_customer": "已形成处理建议，请先确认方案。确认后将交由店铺审核。",
        "waiting_approval": "您已确认方案，目前正在等待店铺审核。",
        "approved": "方案已通过审核，正在等待授权人员安排处理。",
        "processing": "处理请求正在核查中，尚未确认最终结果。",
        "awaiting_fulfillment": "处理请求已受理，后续履约结果仍待核实。",
        "awaiting_resolution": "已收到处理结果，请确认您的问题是否得到解决。",
        "resolved": "已记录您确认问题解决的反馈，工单等待客服核查结案。",
        "closed": "已根据您的解决确认完成结案。如仍有问题，请联系店铺继续处理。",
    }.get(status, "我们正在核实您的订单记录；尚未确认的信息会继续核查。")


def owned(s, model, ctx, identity, lock=False):
    query = select(model).where(model.id == identity, *scoped(model, ctx))
    if lock:
        query = query.with_for_update()
    row = s.scalar(query)
    require(row is not None, "not_found", "记录不存在或无权访问", 404)
    return row


def new_case(db, ctx, payload):
    ctx.require_role("agent", "customer")
    with db.session(ctx, write=True) as s:
        previous = s.scalar(
            select(Message).where(*scoped(Message, ctx), Message.source_id == payload.source_id)
        )
        if previous:
            case = get_case(s, ctx, previous.case_id)
            require(
                previous.body == payload.message
                and case.order_id == payload.order_id
                and case.line_id == payload.line_id
                and case.kind == payload.kind
                and case.subject == payload.subject,
                "idempotency_conflict",
                "相同入站标识对应不同内容",
            )
            return record_dict(case)
        order = owned(s, Order, ctx, payload.order_id)
        line = owned(s, OrderLine, ctx, payload.line_id)
        require(line.order_id == order.id, "wrong_line", "商品不属于此订单", 422)
        require(not ctx.customer_id or order.customer_id == ctx.customer_id, "not_found", "订单不存在", 404)
        case = Case(
            **scope_values(ctx),
            id=uid(),
            order_id=order.id,
            line_id=line.id,
            customer_id=order.customer_id,
            subject=payload.subject,
            kind=payload.kind,
        )
        s.add(case)
        s.flush()
        s.add(
            Message(
                **scope_values(ctx),
                case_id=case.id,
                author_id=ctx.user_id,
                source_id=payload.source_id,
                body=payload.message,
            )
        )
        audit(s, ctx, "case.created", case.id)
        s.flush()
        return record_dict(case)


def add_message(db, ctx, case_id, payload):
    ctx.require_role("agent", "customer")
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        old = s.scalar(select(Message).where(*scoped(Message, ctx), Message.source_id == payload.source_id))
        if old:
            require(
                old.case_id == case_id and old.body == payload.body, "idempotency_conflict", "入站标识冲突"
            )
            return record_dict(old)
        check_version(case, payload.version)
        message = Message(
            **scope_values(ctx),
            case_id=case.id,
            author_id=ctx.user_id,
            source_id=payload.source_id,
            body=payload.body,
        )
        s.add(message)
        bump(case)
        audit(s, ctx, "message.received", case.id)
        s.flush()
        return record_dict(message)


def facts(db, ctx, case_id):
    require(
        db.settings.business_mode == "synthetic",
        "integration_unconfigured",
        "真实 OMS/WMS 读取接口尚未接入，不能将本地记录视为权威外部事实",
        503,
    )
    with db.session(ctx) as s:
        case = get_case(s, ctx, case_id)
        order = owned(s, Order, ctx, case.order_id)
        line = owned(s, OrderLine, ctx, case.line_id)
        stock = s.scalar(select(Inventory).where(*scoped(Inventory, ctx), Inventory.sku == line.sku))
        actions = s.scalars(
            select(Action).where(
                *scoped(Action, ctx),
                Action.case_id.in_(select(Case.id).where(*scoped(Case, ctx), Case.order_id == order.id)),
            )
        ).all()
        return {
            "status": "ok",
            "source": db.settings.business_mode,
            "source_record_id": order.id,
            "queried_at": now(),
            "source_updated_at": order.source_updated_at,
            "order": record_dict(order),
            "line": record_dict(line),
            "inventory": {
                "sku": line.sku,
                "available": stock.on_hand - stock.reserved,
                "on_hand": stock.on_hand,
                "reserved": stock.reserved,
            }
            if stock
            else None,
            "actions": [record_dict(a) for a in actions],
            "case_kind": case.kind,
            "case_id": case.id,
            "references": [order.id, line.id],
            "claims": [
                m.body
                for m in s.scalars(select(Message).where(*scoped(Message, ctx), Message.case_id == case.id))
            ],
        }


def embed(settings, text):
    require(
        settings.model_mode == "real" and settings.embedding_model,
        "embedding_unconfigured",
        "真实嵌入尚未配置",
        503,
    )
    try:
        with httpx.Client(timeout=settings.model_timeout, follow_redirects=False) as client:
            response = client.post(
                settings.llm_base_url.rstrip("/") + "/embeddings",
                headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                json={"model": settings.embedding_model, "input": text[:20000], "dimensions": 1536},
            )
            response.raise_for_status()
            values = response.json()["data"][0]["embedding"]
        require(
            len(values) == 1536 and all(isinstance(v, (float, int)) and math.isfinite(v) for v in values),
            "embedding_invalid",
            "嵌入维度或数值不符合索引契约",
            502,
        )
        return values
    except (httpx.HTTPError, KeyError, TypeError, IndexError):
        raise DomainError("embedding_unavailable", "嵌入服务调用失败，未切换测试结果", 503) from None


def search_policies(db, ctx, case_id, query=""):
    with db.session(ctx) as s:
        case = get_case(s, ctx, case_id)
        order = owned(s, Order, ctx, case.order_id)
        # Scope and applicability are applied before ranking or sending anything to a model.
        candidates = list(
            s.scalars(
                select(Policy).where(
                    *scoped(Policy, ctx),
                    Policy.status == "published",
                    Policy.kind == case.kind,
                    Policy.region == order.region,
                    Policy.effective_from <= order.created_at,
                    Policy.effective_until > order.created_at,
                )
            ).all()
        )
        terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", (query or case.subject).lower()))
        lexical = sorted(candidates, key=lambda p: sum(t in p.body.lower() for t in terms), reverse=True)
        mode = "keyword"
        ranks = {p.id: 1 / (60 + i) for i, p in enumerate(lexical, 1)}
        if db.engine.dialect.name == "postgresql" and candidates:
            ids = [p.id for p in candidates]
            fts = list(
                s.scalars(
                    select(Policy)
                    .where(*scoped(Policy, ctx), Policy.id.in_(ids))
                    .order_by(
                        func.ts_rank_cd(
                            func.to_tsvector("simple", Policy.body),
                            func.plainto_tsquery("simple", query or case.subject),
                        ).desc()
                    )
                )
            )
            for i, policy in enumerate(fts, 1):
                ranks[policy.id] += 1 / (60 + i)
            if db.settings.embedding_model and db.settings.model_mode == "real":
                vector = embed(db.settings, query or case.subject)
                semantic = s.scalars(
                    select(Policy)
                    .where(
                        *scoped(Policy, ctx),
                        Policy.id.in_(ids),
                        Policy.embedding_model == db.settings.embedding_model,
                        Policy.embedding.is_not(None),
                    )
                    .order_by(Policy.embedding.cosine_distance(vector))
                ).all()
                for i, policy in enumerate(semantic, 1):
                    ranks[policy.id] += 1 / (60 + i)
                mode = "hybrid" if semantic else "keyword_no_matching_index"
        candidates.sort(key=lambda p: ranks.get(p.id, 0), reverse=True)
        families = {}
        for policy in candidates:
            families.setdefault(policy.family, []).append(policy.id)
        conflict = any(len(ids) > 1 for ids in families.values())
        return {
            "status": "conflict" if conflict else "ok" if candidates else "no_evidence",
            "mode": mode,
            "event_date_basis": "order_created_at",
            "citations": [record_dict(p) for p in candidates[:10]],
        }


def create_plan(db, ctx, case_id, payload):
    ctx.require_role("agent")
    rules = search_policies(db, ctx, case_id)
    require(rules["status"] == "ok", "policy_unavailable", "缺少适用规则或存在冲突，需人工核查")
    allowed = {p["id"] for p in rules["citations"]}
    require(set(payload.citations).issubset(allowed), "invalid_citation", "引用不属于当前授权的适用规则", 422)
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        check_version(case, payload.version)
        require(case.status not in {"closed", "resolved"}, "case_closed", "请先重开工单")
        require(
            not s.scalar(
                select(Action).where(
                    *scoped(Action, ctx), Action.case_id == case.id, Action.status.not_in(TERMINAL)
                )
            ),
            "action_pending",
            "原动作尚未完成，必须先核查原请求",
        )
        order = owned(s, Order, ctx, case.order_id)
        line = owned(s, OrderLine, ctx, case.line_id)
        require(payload.quantity <= line.quantity, "quantity_invalid", "数量超过原订单行", 422)
        allowed_kinds = {case.kind}
        if case.kind in {"replacement", "missing", "wrong", "return", "delivery"}:
            allowed_kinds |= {"replacement", "refund", "return"}
        require(payload.kind in allowed_kinds, "action_invalid", "处理方式不适用于此工单", 422)
        require(
            payload.kind == "refund" or payload.amount_minor == 0,
            "amount_invalid",
            "此动作不接受退款金额",
            422,
        )
        require(
            payload.kind != "refund" or 0 < payload.amount_minor <= line.total_minor - line.remedy_minor,
            "refund_limit",
            "退款金额必须在未处理权益范围内",
            422,
        )
        require(
            payload.kind != "address" or len(payload.address.strip()) >= 8,
            "address_required",
            "请填写待仓库确认的新地址",
            422,
        )
        content = payload.model_dump(exclude={"version"}) | {"case_id": case.id, "currency": order.currency}
        case.plan_version += 1
        plan = Plan(
            **scope_values(ctx),
            id=uid(),
            case_id=case.id,
            version=case.plan_version,
            kind=payload.kind,
            quantity=payload.quantity,
            amount_minor=payload.amount_minor,
            currency=order.currency,
            address=payload.address,
            rationale=payload.rationale,
            citations=payload.citations,
            fingerprint=digest(content),
            expires_at=now() + db.settings.approval_ttl,
        )
        s.add(plan)
        case.status = "waiting_customer"
        bump(case)
        audit(s, ctx, "plan.created", case.id, {"plan_id": plan.id, "version": plan.version})
        s.flush()
        return record_dict(plan)


def current_plan(s, ctx, case, plan_id):
    plan = owned(s, Plan, ctx, plan_id)
    require(
        plan.case_id == case.id and plan.version == case.plan_version, "stale_plan", "此方案已经失效或被替换"
    )
    require(plan.expires_at > now(), "expired_plan", "方案已过期，需要重新核查事实")
    return plan


def verify_plan_policies(s, ctx, case, plan):
    order = owned(s, Order, ctx, case.order_id)
    policies = list(s.scalars(select(Policy).where(*scoped(Policy, ctx), Policy.id.in_(plan.citations))))
    require(
        len(policies) == len(set(plan.citations))
        and all(
            policy.status == "published"
            and policy.kind == case.kind
            and policy.region == order.region
            and policy.effective_from <= order.created_at < policy.effective_until
            for policy in policies
        ),
        "policy_changed",
        "方案引用的规则已撤回或不再适用，需要重新审核",
    )


def wake_runs(s, ctx, case_id, event_key):
    for run in s.scalars(
        select(Run).where(*scoped(Run, ctx), Run.case_id == case_id, Run.status.in_(["waiting", "queued"]))
    ):
        enqueue(s, ctx, "run", run.id, f"wake:{run.id}:{event_key}")


def customer_choice(db, ctx, case_id, payload):
    ctx.require_role("customer")
    require(ctx.customer_id is not None, "customer_required", "需已认证的订单客户", 403)
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        plan = current_plan(s, ctx, case, payload.plan_id)
        prior = s.scalar(select(Choice).where(*scoped(Choice, ctx), Choice.plan_id == plan.id))
        if prior:
            require(prior.accepted == payload.accepted, "choice_changed", "变更选择需要创建新方案")
            return record_dict(prior)
        choice = Choice(
            **scope_values(ctx),
            plan_id=plan.id,
            actor_id=ctx.user_id,
            accepted=payload.accepted,
            fingerprint=plan.fingerprint,
        )
        s.add(choice)
        case.status = "waiting_approval" if payload.accepted else "open"
        bump(case)
        audit(s, ctx, "customer.choice", case.id, {"plan_id": plan.id, "accepted": payload.accepted})
        wake_runs(s, ctx, case.id, "choice:" + plan.id)
        s.flush()
        return record_dict(choice)


def approve(db, ctx, case_id, payload):
    ctx.require_role("approver")
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        plan = current_plan(s, ctx, case, payload.plan_id)
        require(
            not s.scalar(select(Action).where(*scoped(Action, ctx), Action.plan_id == plan.id)),
            "action_started",
            "动作已准备执行，撤回需要核查原动作",
        )
        prior = s.scalar(
            select(Approval).where(
                *scoped(Approval, ctx), Approval.plan_id == plan.id, Approval.decision == payload.decision
            )
        )
        if prior:
            return record_dict(prior)
        decisions = list(
            s.scalars(select(Approval).where(*scoped(Approval, ctx), Approval.plan_id == plan.id))
        )
        require(
            not decisions
            or (payload.decision == "revoked" and any(a.decision == "approved" for a in decisions)),
            "decision_final",
            "已有决定；重新申请需要新方案",
        )
        if payload.decision == "approved":
            choice = s.scalar(select(Choice).where(*scoped(Choice, ctx), Choice.plan_id == plan.id))
            require(
                choice and choice.accepted and choice.fingerprint == plan.fingerprint,
                "choice_required",
                "客户尚未确认当前方案",
            )
        approval = Approval(
            **scope_values(ctx),
            plan_id=plan.id,
            actor_id=ctx.user_id,
            decision=payload.decision,
            reason=payload.reason,
            fingerprint=plan.fingerprint,
            expires_at=min(plan.expires_at, now() + db.settings.approval_ttl),
        )
        s.add(approval)
        case.status = "approved" if payload.decision == "approved" else "open"
        bump(case)
        audit(s, ctx, "approval." + payload.decision, case.id, {"plan_id": plan.id})
        wake_runs(s, ctx, case.id, "approval:" + plan.id + ":" + payload.decision)
        s.flush()
        return record_dict(approval)


def verify_authorization(s, ctx, case, plan):
    current_plan(s, ctx, case, plan.id)
    verify_plan_policies(s, ctx, case, plan)
    choice = s.scalar(select(Choice).where(*scoped(Choice, ctx), Choice.plan_id == plan.id))
    decisions = list(s.scalars(select(Approval).where(*scoped(Approval, ctx), Approval.plan_id == plan.id)))
    approved = next((a for a in decisions if a.decision == "approved"), None)
    require(
        choice and choice.accepted and choice.fingerprint == plan.fingerprint,
        "choice_required",
        "客户未确认当前方案",
    )
    require(
        approved
        and approved.fingerprint == plan.fingerprint
        and approved.expires_at > now()
        and not any(a.decision in {"revoked", "rejected"} for a in decisions),
        "approval_required",
        "缺少当前方案的有效批准",
        403,
    )
    # A previously authorized approver can be revoked before dispatch.
    from mercury.auth import load_identity

    return approved, load_identity


def prepare_action(db, ctx, case_id, payload):
    ctx.require_role("executor")
    require(db.settings.writes_enabled, "writes_frozen", "业务写入已冻结", 503)
    require(
        db.settings.business_mode == "synthetic",
        "integration_unconfigured",
        "企业业务执行适配器尚未接入",
        503,
    )
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        plan = owned(s, Plan, ctx, payload.plan_id)
        require(plan.case_id == case.id, "not_found", "方案不属于此工单", 404)
        existing = s.scalar(
            select(Action).where(*scoped(Action, ctx), Action.id == payload.business_action_id)
        )
        if existing:
            require(
                existing.plan_id == plan.id and existing.fingerprint == plan.fingerprint,
                "idempotency_conflict",
                "动作标识已关联不同内容",
            )
            return record_dict(existing)
        original = s.scalar(select(Action).where(*scoped(Action, ctx), Action.plan_id == plan.id))
        if original:
            return record_dict(original)
        current_plan(s, ctx, case, plan.id)
        approved, loader = verify_authorization(s, ctx, case, plan)
        loader(db, approved.actor_id, ctx.tenant_id, ctx.store_id).require_role("approver")
        require(
            not db.settings.require_separation_of_duties or approved.actor_id != ctx.user_id,
            "separation_of_duties",
            "批准人与执行人必须为不同身份",
            403,
        )
        order = owned(s, Order, ctx, case.order_id, True)
        line = owned(s, OrderLine, ctx, case.line_id, True)
        # Shared order lock serializes all remedies, even across cases and different UUIDs.
        active = s.scalar(
            select(Action).where(
                *scoped(Action, ctx),
                Action.case_id.in_(select(Case.id).where(*scoped(Case, ctx), Case.order_id == order.id)),
                Action.status.not_in(TERMINAL),
            )
        )
        require(not active, "order_action_pending", "同订单仍有未决动作，请先完成或核查")
        remedy = 0
        if plan.kind in REPLACEMENTS:
            remedy = (line.total_minor * plan.quantity + line.quantity - 1) // line.quantity
        elif plan.kind == "refund":
            remedy = plan.amount_minor
        require(
            line.remedy_minor + remedy <= line.total_minor,
            "duplicate_remedy",
            "此订单行的可处理权益不足，不能换编号重复赔付",
        )
        if plan.kind in {"cancellation", "address"}:
            require(order.status in {"paid", "processing"}, "already_dispatched", "订单已出库，需物流核查")
        if plan.kind in REPLACEMENTS:
            changed = s.execute(
                update(Inventory)
                .where(
                    *scoped(Inventory, ctx),
                    Inventory.sku == line.sku,
                    Inventory.on_hand - Inventory.reserved >= plan.quantity,
                )
                .values(reserved=Inventory.reserved + plan.quantity, version=Inventory.version + 1)
            )
            require(changed.rowcount == 1, "insufficient_stock", "执行前可用库存不足")
        if plan.kind == "refund":
            require(
                order.refunded_minor + order.refund_reserved_minor + plan.amount_minor <= order.paid_minor,
                "refund_limit",
                "已退及处理中金额超过可退额度",
            )
            order.refund_reserved_minor += plan.amount_minor
        line.remedy_minor += remedy
        action = Action(
            **scope_values(ctx),
            id=payload.business_action_id,
            case_id=case.id,
            plan_id=plan.id,
            request_id=uid(),
            kind=plan.kind,
            fingerprint=plan.fingerprint,
            amount_minor=plan.amount_minor,
            quantity=plan.quantity,
            remedy_minor=remedy,
        )
        s.add(action)
        s.flush()
        if plan.kind in REPLACEMENTS:
            s.add(
                StockLedger(
                    **scope_values(ctx),
                    action_id=action.id,
                    sku=line.sku,
                    kind="reserve",
                    quantity=plan.quantity,
                )
            )
        enqueue(s, ctx, "action", action.id, "dispatch:" + action.id)
        case.status = "processing"
        bump(case)
        audit(s, ctx, "action.prepared", case.id, {"action_id": action.id, "request_id": action.request_id})
        return record_dict(action)


def dispatch_action(db, ctx, action_id, *, lose_response=False):
    require(db.settings.writes_enabled, "writes_frozen", "执行已冻结", 503)
    require(db.settings.business_mode == "synthetic", "integration_unconfigured", "执行接口未接入", 503)
    with db.session(ctx, write=True) as s:
        action = owned(s, Action, ctx, action_id, True)
        if action.status not in {"prepared", "unknown"}:
            return record_dict(action)
        original = s.scalar(
            select(ProviderRequest).where(
                *scoped(ProviderRequest, ctx), ProviderRequest.request_id == action.request_id
            )
        )
        if action.status == "unknown" and not original:
            return record_dict(action)  # Not-found is not evidence of non-execution.
        if not original:
            case = get_case(s, ctx, action.case_id, True)
            plan = current_plan(s, ctx, case, action.plan_id)
            approved, loader = verify_authorization(s, ctx, case, plan)
            loader(db, approved.actor_id, ctx.tenant_id, ctx.store_id).require_role("approver")
            original = ProviderRequest(
                **scope_values(ctx),
                request_id=action.request_id,
                fingerprint=action.fingerprint,
                status="accepted",
                receipt={
                    "source": "synthetic",
                    "request_id": action.request_id,
                    "provider_id": "SIM-" + uid(),
                },
            )
            s.add(original)
        require(original.fingerprint == action.fingerprint, "receipt_mismatch", "原请求内容不一致")
        # This transaction is the durable local provider boundary, deliberately separate from receipt observation.
        action.status = "unknown"
        action.updated_at = now()
        request_id = action.request_id
    if lose_response:
        return {"status": "unknown", "request_id": request_id}
    return reconcile_action(db, ctx, action_id)


def reconcile_action(db, ctx, action_id):
    with db.session(ctx, write=True) as s:
        action = owned(s, Action, ctx, action_id, True)
        original = s.scalar(
            select(ProviderRequest).where(
                *scoped(ProviderRequest, ctx), ProviderRequest.request_id == action.request_id
            )
        )
        if not original:
            return record_dict(action)
        require(action.fingerprint == original.fingerprint, "receipt_mismatch", "原请求内容不一致")
        if action.status in {"prepared", "unknown"}:
            action.status = "accepted"
            action.receipt = original.receipt
            action.updated_at = now()
            case = get_case(s, ctx, action.case_id, True)
            case.status = "awaiting_fulfillment"
            bump(case)
            audit(s, ctx, "action.accepted", case.id, {"action_id": action.id})
            wake_runs(s, ctx, case.id, "receipt:" + action.id)
        return record_dict(action)


def apply_event(db, ctx, action_id, payload):
    ctx.require_role("executor")
    require(db.settings.writes_enabled, "writes_frozen", "业务写入已冻结", 503)
    with db.session(ctx, write=True) as s:
        action = owned(s, Action, ctx, action_id, True)
        case = get_case(s, ctx, action.case_id, True)
        event_hash = digest(payload.model_dump() | {"action_id": action_id})
        prior = s.scalar(
            select(BusinessEvent).where(
                *scoped(BusinessEvent, ctx), BusinessEvent.source_id == payload.source_id
            )
        )
        if prior:
            require(prior.digest == event_hash, "event_conflict", "相同事件标识对应不同内容")
            return record_dict(action)
        require(action.status != "unknown", "reconcile_first", "先核查原请求，再接收状态事件")
        order = owned(s, Order, ctx, case.order_id, True)
        line = owned(s, OrderLine, ctx, case.line_id, True)
        allowed = {
            "replacement": {"accepted": {"shipped", "failed", "cancelled"}, "shipped": {"delivered"}},
            "refund": {"accepted": {"succeeded", "failed"}},
            "return": {"accepted": {"received", "cancelled"}, "received": {"inspected"}},
            "delivery": {"accepted": {"investigated", "failed"}},
            "cancellation": {"accepted": {"succeeded", "failed"}},
            "address": {"accepted": {"succeeded", "failed"}},
        }
        flow = allowed["replacement" if action.kind in REPLACEMENTS else action.kind]
        require(
            payload.kind in flow.get(action.status, set()), "invalid_transition", "事件与当前动作状态不匹配"
        )
        require(
            payload.sequence == action.last_sequence + 1,
            "event_out_of_order",
            "事件序号不连续，需补查缺失记录",
        )
        if action.kind in REPLACEMENTS and payload.kind in {"shipped", "cancelled", "failed"}:
            stock = s.scalar(
                select(Inventory).where(*scoped(Inventory, ctx), Inventory.sku == line.sku).with_for_update()
            )
            require(stock and stock.reserved >= action.quantity, "stock_conflict", "预留台账不一致")
            stock.reserved -= action.quantity
            if payload.kind == "shipped":
                stock.on_hand -= action.quantity
                stock.shipped += action.quantity
            stock.version += 1
            s.add(
                StockLedger(
                    **scope_values(ctx),
                    action_id=action.id,
                    sku=line.sku,
                    kind="ship" if payload.kind == "shipped" else "release",
                    quantity=action.quantity,
                )
            )
        if action.kind == "refund" and payload.kind in {"succeeded", "failed"}:
            order.refund_reserved_minor -= action.amount_minor
            if payload.kind == "succeeded":
                order.refunded_minor += action.amount_minor
        if payload.kind in {"failed", "cancelled"}:
            line.remedy_minor -= action.remedy_minor
        if payload.kind == "succeeded" and action.kind in {"cancellation", "address"}:
            require(
                order.status in {"paid", "processing"}, "fulfillment_conflict", "仓库阶段已变化，需人工对账"
            )
            if action.kind == "cancellation":
                order.status = "cancelled"
            else:
                order.address = owned(s, Plan, ctx, action.plan_id).address
        action.status = payload.kind
        action.last_sequence = payload.sequence
        action.updated_at = now()
        action.receipt = action.receipt | {"last_event": payload.model_dump()}
        s.add(
            BusinessEvent(
                **scope_values(ctx),
                action_id=action.id,
                source_id=payload.source_id,
                sequence=payload.sequence,
                kind=payload.kind,
                payload=payload.model_dump(),
                digest=event_hash,
            )
        )
        if payload.kind in TERMINAL:
            case.status = (
                "needs_review"
                if payload.kind in {"failed", "cancelled", "inspected", "investigated"}
                else "awaiting_resolution"
            )
        bump(case)
        audit(s, ctx, "event." + payload.kind, case.id, {"action_id": action.id})
        wake_runs(s, ctx, case.id, payload.source_id)
        return record_dict(action)


def close_case(db, ctx, case_id, payload, *, reopen=False, resolve=False):
    ctx.require_role("customer" if resolve else "agent")
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        check_version(case, payload.version)
        if reopen:
            require(case.status in {"closed", "resolved"}, "not_closed", "工单尚未结案")
            case.status, case.resolved_at = "open", None
        else:
            actions = list(s.scalars(select(Action).where(*scoped(Action, ctx), Action.case_id == case.id)))
            require(all(a.status in TERMINAL for a in actions), "unresolved_action", "仍有未决动作，不能结案")
            if resolve:
                require(ctx.customer_id == case.customer_id, "forbidden", "仅订单客户可确认解决", 403)
                case.resolved_at = now()
                case.status = "resolved"
            else:
                require(
                    case.resolved_at is not None, "resolution_required", "尚无客户解决确认，签收不能直接结案"
                )
                case.status = "closed"
        bump(case)
        audit(s, ctx, "case." + case.status, case.id, {"reason": payload.reason})
        return record_dict(case)


def policy_create(db, ctx, payload):
    ctx.require_role("knowledge")
    require(payload.effective_until > payload.effective_from, "date_invalid", "规则日期范围不合法", 422)
    vector = embed(db.settings, payload.body) if db.settings.embedding_model else None
    with db.session(ctx, write=True) as s:
        version = (
            s.scalar(
                select(func.max(Policy.version)).where(*scoped(Policy, ctx), Policy.family == payload.family)
            )
            or 0
        ) + 1
        policy = Policy(
            **scope_values(ctx),
            **payload.model_dump(),
            version=version,
            author_id=ctx.user_id,
            source="synthetic" if db.settings.env in {"demo", "test"} else "uploaded",
            content_hash=digest(payload.body),
            embedding=vector,
            embedding_model=db.settings.embedding_model if vector else None,
        )
        s.add(policy)
        s.flush()
        audit(s, ctx, "policy.drafted", policy.id)
        return record_dict(policy)


def policy_publish(db, ctx, policy_id, payload, *, withdraw=False):
    ctx.require_role("knowledge_reviewer")
    with db.session(ctx, write=True) as s:
        policy = owned(s, Policy, ctx, policy_id, True)
        require(policy.author_id != ctx.user_id, "separation_of_duties", "规则须由另一位审核人发布", 403)
        if withdraw:
            policy.status = "withdrawn"
        else:
            for case_id in payload.validation_cases:
                case = get_case(s, ctx, case_id)
                order = owned(s, Order, ctx, case.order_id)
                require(
                    case.kind == policy.kind
                    and order.region == policy.region
                    and policy.effective_from <= order.created_at < policy.effective_until,
                    "validation_mismatch",
                    "验证案例不满足规则范围",
                )
            policy.validation = payload.model_dump()
            policy.reviewer_id = ctx.user_id
            policy.status = "published"
        audit(s, ctx, "policy." + policy.status, policy.id)
        return record_dict(policy)


def notify_customer(db, ctx, case_id, payload):
    ctx.require_role("agent")
    require(db.settings.writes_enabled, "writes_frozen", "外发已冻结", 503)
    require(db.settings.business_mode == "synthetic", "integration_unconfigured", "通知服务尚未接入", 503)
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        check_version(case, payload.version)
        body = payload.body.strip()
        require(bool(body), "empty_notification", "请填写通知内容", 422)
        key = digest([ctx.tenant_id, ctx.store_id, case_id, case.customer_id, body])
        notification = s.scalar(select(Notification).where(Notification.intent_hash == key))
        if not notification:
            notification = Notification(
                **scope_values(ctx),
                id=uid(),
                case_id=case_id,
                recipient_id=case.customer_id,
                body=body,
                intent_hash=key,
            )
            s.add(notification)
            enqueue(s, ctx, "notification", notification.id, "notify:" + key)
            audit(s, ctx, "notification.queued", case_id, {"case_version": case.version})
        s.flush()
        return record_dict(notification)


def operations(db, ctx):
    ctx.require_role("agent", "approver", "executor", "operations", "knowledge", "knowledge_reviewer")
    with db.session(ctx) as s:
        cases = list(s.scalars(select(Case).where(*scoped(Case, ctx))))
        orders = list(s.scalars(select(Order).where(*scoped(Order, ctx))))
        actions = list(s.scalars(select(Action).where(*scoped(Action, ctx))))
        issues = len({c.order_id for c in cases})
        return {
            "source": db.settings.business_mode,
            "as_of": now(),
            "order_count": len(orders),
            "issue_order_count": issues,
            "issue_rate": issues / len(orders) if orders else None,
            "case_count": len(cases),
            "open_cases": sum(c.status != "closed" for c in cases),
            "waiting_approval": sum(c.status == "waiting_approval" for c in cases),
            "unknown_actions": sum(a.status == "unknown" for a in actions),
            "refunded_minor": sum(o.refunded_minor for o in orders)
            if len({o.currency for o in orders}) <= 1
            else None,
            "refunds_by_currency": {
                currency: sum(o.refunded_minor for o in orders if o.currency == currency)
                for currency in {o.currency for o in orders}
            },
            "currency": orders[0].currency if orders and len({o.currency for o in orders}) == 1 else "mixed",
            "by_kind": {k: sum(c.kind == k for c in cases) for k in sorted({c.kind for c in cases})},
            "scope": "all_time_current_store",
            "denominator": "distinct orders; repeat contacts counted once",
        }


def create_claim(db, ctx, case_id, payload):
    ctx.require_role("operations")
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id)
        order = owned(s, Order, ctx, case.order_id)
        claim = Claim(
            **scope_values(ctx),
            case_id=case_id,
            owner_id=ctx.user_id,
            currency=order.currency,
            **payload.model_dump(),
        )
        s.add(claim)
        s.flush()
        audit(s, ctx, "claim.created", claim.id)
        return record_dict(claim)


def update_claim(db, ctx, claim_id, payload):
    ctx.require_role("operations")
    with db.session(ctx, write=True) as s:
        claim = owned(s, Claim, ctx, claim_id, True)
        check_version(claim, payload.version)
        transitions = {
            "draft": {"submitted"},
            "submitted": {"accepted", "rejected"},
            "accepted": {"settled", "rejected"},
        }
        require(
            payload.status in transitions.get(claim.status, set()), "invalid_transition", "索赔状态转换无效"
        )
        require(
            payload.status != "settled" or payload.settlement_ref.strip(),
            "settlement_required",
            "到账结算必须提供对账凭证",
        )
        claim.status, claim.settlement_ref = payload.status, payload.settlement_ref
        claim.version += 1
        audit(s, ctx, "claim." + claim.status, claim.id)
        return record_dict(claim)


def create_improvement(db, ctx, payload):
    ctx.require_role("operations")
    with db.session(ctx, write=True) as s:
        for case_id in payload.case_ids:
            get_case(s, ctx, case_id)
        improvement = Improvement(
            **scope_values(ctx),
            title=payload.title,
            hypothesis=payload.hypothesis,
            evidence=payload.case_ids,
            owner_id=ctx.user_id,
            due_at=payload.due_at,
        )
        s.add(improvement)
        s.flush()
        audit(s, ctx, "improvement.created", improvement.id)
        return record_dict(improvement)


def review_improvement(db, ctx, improvement_id, payload):
    ctx.require_role("operations")
    with db.session(ctx, write=True) as s:
        improvement = owned(s, Improvement, ctx, improvement_id, True)
        check_version(improvement, payload.version)
        require(
            not payload.close or payload.sample_size >= db.settings.improvement_min_samples,
            "sample_insufficient",
            f"当前策略要求至少{db.settings.improvement_min_samples}个可比样本；企业阈值需单独确认",
        )
        improvement.measure = payload.measure
        improvement.review = payload.model_dump()
        improvement.status = "closed" if payload.close else "reviewing"
        improvement.version += 1
        audit(s, ctx, "improvement." + improvement.status, improvement.id)
        return record_dict(improvement)
