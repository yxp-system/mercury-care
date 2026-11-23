"""Durable notification submission and original-request reconciliation in explicit synthetic mode."""

from sqlalchemy import select

from mercury.auth import get_case
from mercury.errors import DomainError, require
from mercury.models import Notification, ProviderRequest, now
from mercury.services import audit, digest, owned, record_dict, scope_values


def dispatch_notification(db, ctx, identity, *, lose_receipt=False):
    require(
        db.settings.writes_enabled and db.settings.business_mode == "synthetic",
        "notification_unavailable",
        "通知接口未接入或已冻结",
        503,
    )
    with db.session(ctx, write=True) as s:
        notice = owned(s, Notification, ctx, identity, True)
        if notice.status in {"delivered", "needs_review", "cancelled"}:
            return
        if notice.status in {"submitting", "unknown"}:
            reconcile = True
        else:
            case = get_case(s, ctx, notice.case_id)
            if notice.case_version != case.version:
                notice.status = "needs_review"
                audit(s, ctx, "notification.stale", case.id, {"notification_id": notice.id})
                return
            notice.status, notice.updated_at = "submitting", now()
            notice.attempts += 1
            reconcile = False
    if reconcile:
        reconcile_notification(db, ctx, identity)
        return
    # Persist submission intent before touching the provider, so interrupted dispatch is query-only.
    with db.session(ctx, write=True) as s:
        original = s.scalar(select(ProviderRequest).where(ProviderRequest.request_id == notice.request_id))
        fingerprint = digest([notice.recipient_id, notice.body])
        if not original:
            s.add(
                ProviderRequest(
                    **scope_values(ctx),
                    request_id=notice.request_id,
                    fingerprint=fingerprint,
                    status="delivered",
                    receipt={
                        "source": "synthetic",
                        "request_id": notice.request_id,
                        "description": "本地通知记录；未向外部客户发送",
                    },
                )
            )
        else:
            require(original.fingerprint == fingerprint, "notification_conflict", "原通知请求内容冲突")
    if lose_receipt:
        with db.session(ctx, write=True) as s:
            owned(s, Notification, ctx, identity, True).status = "unknown"
        raise DomainError("notification_unknown", "通知回执未知，请核查原请求", 503)
    reconcile_notification(db, ctx, identity)


def reconcile_notification(db, ctx, identity):
    ctx.require_role("executor", "agent")
    require(db.settings.business_mode == "synthetic", "integration_unconfigured", "通知查询尚未接入", 503)
    with db.session(ctx, write=True) as s:
        notice = owned(s, Notification, ctx, identity, True)
        get_case(s, ctx, notice.case_id)
        original = s.scalar(select(ProviderRequest).where(ProviderRequest.request_id == notice.request_id))
        if notice.status == "delivered":
            return record_dict(notice)
        require(notice.status in {"submitting", "unknown"}, "not_submitted", "通知尚未提交")
        if original is None:
            notice.status = "unknown"
        else:
            require(
                original.fingerprint == digest([notice.recipient_id, notice.body]),
                "notification_conflict",
                "原通知请求内容冲突",
            )
            notice.status, notice.receipt = original.status, original.receipt
        notice.updated_at = now()
        audit(
            s,
            ctx,
            "notification.reconciled",
            notice.case_id,
            {"notification_id": notice.id, "status": notice.status},
        )
        return record_dict(notice)
