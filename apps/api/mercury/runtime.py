"""Durable cancellation, wall-clock deadlines and fenced task leases."""

import threading
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select, update

from mercury.auth import AuthContext, get_case
from mercury.db import scoped
from mercury.errors import require
from mercury.models import Case, Escalation, Job, Membership, Run, now

lease_guard = ContextVar("mercury_lease_guard", default=None)


def ensure_lease():
    guard = lease_guard.get()
    if guard:
        guard()


@contextmanager
def renew_lease(db, job, owner, lock_guard=lambda: None):
    stop, lost = threading.Event(), threading.Event()

    def renew():
        while not stop.wait(max(1, db.settings.worker_lease_seconds / 3)):
            try:
                with db.session(write=True) as s:
                    updated = s.execute(
                        update(Job)
                        .where(
                            Job.id == job["id"],
                            Job.fence == job["fence"],
                            Job.lease_owner == owner,
                            Job.status == "running",
                        )
                        .values(lease_until=now() + db.settings.worker_lease_seconds)
                    ).rowcount
                    if updated != 1:
                        lost.set()
                        return
            except Exception:
                lost.set()
                return

    def guard():
        lock_guard()
        require(not lost.is_set(), "stale_worker", "任务租约已丢失")
        with db.session() as s:
            current = s.get(Job, job["id"])
            require(
                current
                and current.fence == job["fence"]
                and current.lease_owner == owner
                and current.status == "running"
                and current.lease_until > now(),
                "stale_worker",
                "任务租约已被替换",
            )

    thread = threading.Thread(target=renew, daemon=True)
    token = lease_guard.set(guard)
    thread.start()
    try:
        guard()
        yield
        guard()
    finally:
        lease_guard.reset(token)
        stop.set()
        thread.join(timeout=5)


def ensure_run(db, ctx, run_id):
    ensure_lease()
    with db.session(ctx) as s:
        run = s.get(Run, run_id)
        require(
            run and not run.cancelled_at and run.status not in {"cancelled", "timed_out"},
            "run_cancelled",
            "协作任务已停止",
        )
        require(not run.deadline_at or run.deadline_at > now(), "run_timeout", "协作任务已超过时限")


def cancel_run(db, ctx, run_id, reason):
    from mercury.services import audit, owned, record_dict

    ctx.require_role("agent", "operations")
    with db.session(ctx, write=True) as s:
        run = owned(s, Run, ctx, run_id, True)
        get_case(s, ctx, run.case_id)
        require(run.status not in {"completed", "failed", "timed_out"}, "run_terminal", "任务已结束")
        run.cancelled_at, run.status, run.waiting_reason = now(), "cancelled", reason
        s.execute(
            update(Job)
            .where(*scoped(Job, ctx), Job.kind == "run", Job.target_id == run_id, Job.status == "pending")
            .values(status="cancelled")
        )
        audit(s, ctx, "run.cancelled", run.case_id, {"run_id": run.id, "reason": reason})
        return record_dict(run)


def schedule_escalations(db):
    """Durable handoff records; timeouts never cancel a remote business action."""
    from mercury.services import audit, scope_values

    with db.session() as s:
        scopes = s.execute(select(Membership.tenant_id, Membership.store_id).distinct()).all()
    for tenant, store in scopes:
        ctx = AuthContext("scheduler", tenant, store, frozenset({"operations"}))
        with db.session(ctx, write=True) as s:
            cases = list(
                s.scalars(select(Case).where(*scoped(Case, ctx), Case.status.not_in(["closed", "resolved"])))
            )
            for case in cases:
                reason = None
                runs = list(
                    s.scalars(
                        select(Run).where(
                            *scoped(Run, ctx),
                            Run.case_id == case.id,
                            Run.status.in_(["queued", "running", "waiting"]),
                        )
                    )
                )
                for run in runs:
                    if run.deadline_at and run.deadline_at < now():
                        run.status, run.error = "timed_out", "run_timeout"
                        reason = "run_timeout"
                if case.updated_at < now() - db.settings.overdue_hours * 3600:
                    reason = reason or "case_overdue"
                if reason:
                    prior = s.scalar(
                        select(Escalation).where(
                            *scoped(Escalation, ctx),
                            Escalation.case_id == case.id,
                            Escalation.scope_version == case.version,
                            Escalation.reason == reason,
                        )
                    )
                    if not prior:
                        s.add(
                            Escalation(
                                **scope_values(ctx),
                                case_id=case.id,
                                scope_version=case.version,
                                reason=reason,
                            )
                        )
                        audit(s, ctx, "case.escalated", case.id, {"reason": reason})
