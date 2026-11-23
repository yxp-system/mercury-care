import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict

import httpx
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select, update

from mercury.auth import get_case, load_identity
from mercury.db import scoped
from mercury.errors import DomainError, require
from mercury.models import Approval, Choice, Plan, Run, RunStep, now, uid
from mercury.schemas import PlanInput, RoleOutput, SupervisorOutput
from mercury.services import (
    audit,
    create_plan,
    digest,
    enqueue,
    facts,
    record_dict,
    scope_values,
    search_policies,
    status_message,
)

PROMPTS = Path(__file__).parent / "prompts"


class State(TypedDict, total=False):
    run_id: str
    case_id: str
    next: str
    facts: dict
    rules: dict
    plan_id: str
    draft: str
    released: bool


class Gateway:
    """Bounded structured inference. It has no business-write tools or approval capability."""

    def __init__(self, db, ctx, run_id):
        self.db, self.ctx, self.run_id = db, ctx, run_id

    def call(self, role, context, schema, test_output):
        settings = self.db.settings
        prompt = (PROMPTS / f"{role}.md").read_text(encoding="utf-8")
        request_context = json.dumps(context, ensure_ascii=False)
        require(len(request_context.encode()) <= 32000, "context_limit", "模型上下文超出大小限制")
        reservation = len((prompt + request_context).encode()) + 1024
        with self.db.session(self.ctx, write=True) as s:
            run = s.scalar(select(Run).where(Run.id == self.run_id, *scoped(Run, self.ctx)).with_for_update())
            require(
                run and run.calls < settings.max_model_calls, "budget_exhausted", "模型调用次数预算已耗尽"
            )
            require(
                run.used_tokens + run.reserved_tokens + reservation <= settings.token_budget,
                "budget_exhausted",
                "模型令牌预算不足；未知耗用仍保留占用",
            )
            run.calls += 1
            run.reserved_tokens += reservation
        started = time.monotonic()
        usage = {}
        try:
            if settings.model_mode == "deterministic_test":
                output = schema.model_validate(test_output)
                usage = {"total_tokens": 0, "mode": "deterministic_test", "cost": None}
            else:
                with httpx.Client(timeout=settings.model_timeout, follow_redirects=False) as client:
                    response = client.post(
                        settings.llm_base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                        json={
                            "model": settings.llm_model,
                            "max_completion_tokens": 1024,
                            "messages": [
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": "Untrusted task data:\n" + request_context},
                            ],
                            "response_format": {
                                "type": "json_schema",
                                "json_schema": {
                                    "name": role,
                                    "strict": True,
                                    "schema": schema.model_json_schema(),
                                },
                            },
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    output = schema.model_validate_json(body["choices"][0]["message"]["content"])
                    usage = body.get("usage") or {}
                    usage["provider_request_id"] = response.headers.get("x-request-id")
                    usage["cost"] = None  # Prices are not guessed.
            with self.db.session(self.ctx, write=True) as s:
                run = s.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
                tokens = usage.get("total_tokens")
                if isinstance(tokens, int) and tokens >= 0:
                    run.reserved_tokens -= reservation
                    run.used_tokens += tokens
                else:
                    run.unknown_usage = True
                s.add(
                    RunStep(
                        **scope_values(self.ctx),
                        run_id=run.id,
                        role=role,
                        mode=settings.model_mode,
                        prompt_hash=digest(prompt),
                        summary=getattr(output, "summary", getattr(output, "reason", "")),
                        duration_ms=int((time.monotonic() - started) * 1000),
                        usage=usage,
                    )
                )
            return output
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            with self.db.session(self.ctx, write=True) as s:
                s.execute(update(Run).where(Run.id == self.run_id).values(unknown_usage=True))
            raise DomainError("model_failed", "模型调用或结构验证失败；未切换为测试答案", 502) from None


def start_run(db, ctx, case_id):
    ctx.require_role("agent")
    with db.session(ctx, write=True) as s:
        case = get_case(s, ctx, case_id, True)
        existing = s.scalar(
            select(Run).where(
                *scoped(Run, ctx), Run.case_id == case.id, Run.status.in_(["queued", "running", "waiting"])
            )
        )
        if existing:
            enqueue(s, ctx, "run", existing.id, f"wake:{existing.id}:case:{case.version}")
            return record_dict(existing)
        require(case.status not in {"closed", "resolved"}, "case_closed", "请先重开工单")
        run = Run(
            **scope_values(ctx), id=uid(), case_id=case_id, actor_id=ctx.user_id, input_version=case.version
        )
        s.add(run)
        s.flush()
        enqueue(s, ctx, "run", run.id, "run:" + run.id)
        audit(s, ctx, "run.queued", case_id, {"run_id": run.id})
        return record_dict(run)


@contextmanager
def checkpointer(db):
    if db.engine.dialect.name == "postgresql":
        url = db.settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        with PostgresSaver.from_conn_string(url) as saver:
            yield saver
    else:
        with SqliteSaver.from_conn_string(str(db.settings.data_dir / "checkpoints.sqlite")) as saver:
            yield saver


def build_graph(db, ctx, run_id, saver):
    gateway = Gateway(db, ctx, run_id)

    def supervisor(state):
        if not state.get("facts"):
            allowed = ["facts"]
        elif state["facts"]["case_kind"] == "delivery":
            allowed = ["reply"] if not state.get("draft") else ["finish"]
        elif not state.get("rules"):
            allowed = ["rules", "reply"] if not state.get("draft") else ["rules"]
        elif state["rules"]["status"] != "ok":
            allowed = ["reply"] if not state.get("draft") else ["finish"]
        elif not state.get("plan_id"):
            allowed = ["proposal", "reply"] if not state.get("draft") else ["proposal"]
        elif not state.get("draft"):
            allowed = ["reply"]
        else:
            allowed = ["finish"] if state.get("released") else ["wait"]
        result = gateway.call(
            "supervisor",
            {"allowed_actions": allowed, "case_id": state["case_id"]},
            SupervisorOutput,
            {"action": allowed[0], "reason": "根据已验证的依赖和当前状态选择允许动作"},
        )
        require(result.action in allowed, "invalid_model_action", "模型返回了当前不允许的动作")
        return {"next": result.action}

    def fact_node(state):
        snapshot = facts(db, ctx, state["case_id"])
        model_facts = snapshot | {
            "order": {
                key: snapshot["order"][key]
                for key in (
                    "id",
                    "status",
                    "currency",
                    "paid_minor",
                    "refunded_minor",
                    "refund_reserved_minor",
                )
            }
        }
        output = gateway.call(
            "facts",
            model_facts,
            RoleOutput,
            {
                "summary": f"已查询订单 {snapshot['order']['id']} 的当前记录",
                "references": snapshot["references"],
                "missing": [],
            },
        )
        require(
            set(output.references).issubset(snapshot["references"]),
            "invalid_reference",
            "事实引用不在工具结果中",
        )
        return {"facts": snapshot}

    def rules_node(state):
        retrieved = search_policies(db, ctx, state["case_id"])
        references = [p["id"] for p in retrieved["citations"]]
        output = gateway.call(
            "rules",
            retrieved,
            RoleOutput,
            {
                "summary": "规则依据已检索" if references else "没有适用规则，需要补充依据",
                "references": references,
                "missing": [] if references else ["适用政策"],
            },
        )
        require(
            set(output.references).issubset(references), "invalid_reference", "规则引用不在授权检索结果中"
        )
        return {"rules": retrieved}

    def proposal_node(state):
        with db.session(ctx) as s:
            case = get_case(s, ctx, state["case_id"])
            prior = s.scalar(
                select(Plan).where(
                    *scoped(Plan, ctx), Plan.case_id == case.id, Plan.version == case.plan_version
                )
            )
            if prior:
                return {"plan_id": prior.id}  # Node replay must not create a new proposal.
            version = case.version
        snapshot = state["facts"]
        refs = [p["id"] for p in state["rules"]["citations"]]
        context = {
            "facts": snapshot
            | {
                "order": {
                    key: snapshot["order"][key]
                    for key in (
                        "id",
                        "status",
                        "currency",
                        "paid_minor",
                        "refunded_minor",
                        "refund_reserved_minor",
                    )
                }
            },
            "citations": refs,
            "version": version,
            "allowed_kind": snapshot["case_kind"],
            "currency": snapshot["order"]["currency"],
        }
        output = gateway.call(
            "proposal",
            context,
            PlanInput,
            {
                "version": version,
                "kind": snapshot["case_kind"],
                "quantity": 1,
                "amount_minor": (snapshot["line"]["total_minor"] - snapshot["line"]["remedy_minor"])
                if snapshot["case_kind"] == "refund"
                else 0,
                "address": "",
                "rationale": "依据当前订单与适用合成政策，待客户确认、独立审批和执行前重查。",
                "citations": refs,
            },
        )
        if snapshot["case_kind"] == "address" and not output.address:
            raise DomainError("missing_address", "需要客户提供新地址后再制定方案")
        plan = create_plan(db, ctx, state["case_id"], output)
        return {"plan_id": plan["id"]}

    def reply_node(state):
        with db.session(ctx) as s:
            case = get_case(s, ctx, state["case_id"])
            status = case.status
        status_text = status_message(status)
        output = gateway.call(
            "reply",
            {"status": status, "permitted_facts": status_text},
            RoleOutput,
            {"summary": status_text, "references": [], "missing": []},
        )
        # Model prose is an internal draft. Customer-facing status remains server-authored.
        with db.session(ctx, write=True) as s:
            s.execute(update(Run).where(Run.id == run_id).values(draft=output.summary))
        return {"draft": output.summary}

    def wait_node(state):
        # No side effects before interrupt. Wake payload never carries approval truth.
        interrupt(
            {"reason": "customer_and_approval", "case_id": state["case_id"], "plan_id": state["plan_id"]}
        )
        with db.session(ctx) as s:
            case = get_case(s, ctx, state["case_id"])
            plan = s.get(Plan, state["plan_id"])
            require(
                plan and plan.version == case.plan_version and plan.expires_at > now(),
                "stale_plan",
                "运行引用的方案已失效，需要重新分析",
            )
            choices = s.scalar(select(Choice).where(*scoped(Choice, ctx), Choice.plan_id == plan.id))
            approvals = list(
                s.scalars(select(Approval).where(*scoped(Approval, ctx), Approval.plan_id == plan.id))
            )
            valid = (
                choices
                and choices.accepted
                and any(a.decision == "approved" and a.expires_at > now() for a in approvals)
            )
            valid = valid and not any(a.decision in {"rejected", "revoked"} for a in approvals)
        require(valid, "approval_required", "尚未取得有效客户确认和审批")
        return {"released": True, "draft": ""}

    builder = StateGraph(State)
    for name, node in {
        "supervisor": supervisor,
        "facts": fact_node,
        "rules": rules_node,
        "proposal": proposal_node,
        "reply": reply_node,
        "wait": wait_node,
    }.items():
        builder.add_node(name, node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state["next"],
        {name: name for name in ("facts", "rules", "proposal", "reply", "wait")} | {"finish": END},
    )
    for name in ("facts", "rules", "proposal", "reply", "wait"):
        builder.add_edge(name, "supervisor")
    return builder.compile(checkpointer=saver)


def run_workflow(db, job_ctx, run_id):
    with db.session(job_ctx) as s:
        run = s.scalar(select(Run).where(Run.id == run_id, *scoped(Run, job_ctx)))
        require(run, "run_not_found", "运行不存在", 404)
        if run.status == "completed":
            return
        ctx = load_identity(db, run.actor_id, run.tenant_id, run.store_id)
        ctx.require_role("agent")
        case_id = run.case_id
    with checkpointer(db) as saver:
        graph = build_graph(db, ctx, run_id, saver)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 32}
        snapshot = graph.get_state(config)
        if snapshot.next and any(task.interrupts for task in snapshot.tasks):
            with db.session(ctx, write=True) as s:
                plan_id = snapshot.values.get("plan_id")
                approvals = list(
                    s.scalars(select(Approval).where(*scoped(Approval, ctx), Approval.plan_id == plan_id))
                )
                choice = s.scalar(select(Choice).where(*scoped(Choice, ctx), Choice.plan_id == plan_id))
                plan = s.scalar(select(Plan).where(*scoped(Plan, ctx), Plan.id == plan_id))
                case = get_case(s, ctx, case_id)
                if (
                    not plan
                    or plan.version != case.plan_version
                    or plan.expires_at <= now()
                    or (choice and not choice.accepted)
                    or any(a.decision in {"revoked", "rejected"} for a in approvals)
                ):
                    s.execute(
                        update(Run)
                        .where(Run.id == run_id)
                        .values(status="failed", error="plan_requires_revision", waiting_reason="")
                    )
                    return
                if not (
                    choice
                    and choice.accepted
                    and any(a.decision == "approved" and a.expires_at > now() for a in approvals)
                    and not any(a.decision in {"revoked", "rejected"} for a in approvals)
                ):
                    return  # Waiting does not consume another model call or occupy a worker.
            argument = Command(resume={"wake": True})
        elif snapshot.next:
            argument = None  # Resume the existing checkpoint after a process failure.
        elif snapshot.values:
            with db.session(ctx, write=True) as s:
                s.execute(update(Run).where(Run.id == run_id).values(status="completed"))
            return
        else:
            argument = {"run_id": run_id, "case_id": case_id}
        with db.session(ctx, write=True) as s:
            s.execute(update(Run).where(Run.id == run_id).values(status="running", error=""))
        result = graph.invoke(argument, config)
        waiting = bool(result.get("__interrupt__"))
        with db.session(ctx, write=True) as s:
            s.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    status="waiting" if waiting else "completed",
                    waiting_reason="客户确认 / 人工审批" if waiting else "",
                )
            )
