"""Append-only, independently reviewed and time-limited deployment acceptance evidence."""

from sqlalchemy import select

from mercury.errors import require
from mercury.models import Membership, ReleaseEvidence, now
from mercury.services import audit, record_dict, scope_values

CHECKS = {
    "database_migrations",
    "runtime_database_role",
    "enterprise_identity",
    "attachment_scanner",
    "real_models",
    "integration_OMS",
    "integration_WMS",
    "integration_payments",
    "integration_logistics",
    "integration_notifications",
    "deployment_acceptance",
    "data_governance",
    "model_quality",
}


def list_evidence(db, ctx):
    ctx.require_role("operations", "release_reviewer")
    with db.session(ctx) as session:
        return [
            record_dict(item)
            for item in session.scalars(
                select(ReleaseEvidence)
                .where(
                    ReleaseEvidence.tenant_id == ctx.tenant_id,
                    ReleaseEvidence.store_id == ctx.store_id,
                )
                .order_by(ReleaseEvidence.created_at.desc())
                .limit(200)
            )
        ]


def submit_evidence(db, ctx, payload):
    ctx.require_role("operations")
    stamp = now()
    require(payload.check_name in CHECKS, "release_check_unknown", "上线检查项不存在", 422)
    require(
        payload.tested_at <= stamp and payload.tested_at >= stamp - 366 * 86400,
        "evidence_date_invalid",
        "测试时间须在过去一年内",
        422,
    )
    require(
        stamp < payload.expires_at <= stamp + 366 * 86400,
        "evidence_expiry_invalid",
        "证据须未过期且有效期不得超过一年",
        422,
    )
    require(
        "\n" not in payload.evidence_ref and "\r" not in payload.evidence_ref,
        "evidence_ref_invalid",
        "证据引用须为单行工单或受控资料编号",
        422,
    )
    with db.session(ctx, write=True) as session:
        member = session.scalar(
            select(Membership).where(
                Membership.user_id == payload.owner_id,
                Membership.tenant_id == ctx.tenant_id,
                Membership.store_id == ctx.store_id,
                Membership.active.is_(True),
            )
        )
        require(member is not None, "evidence_owner_invalid", "负责人须为当前店铺有效成员", 422)
        item = ReleaseEvidence(
            **scope_values(ctx), **payload.model_dump(), submitted_by=ctx.user_id, status="submitted"
        )
        session.add(item)
        session.flush()
        audit(
            session,
            ctx,
            "release_evidence.submitted",
            item.id,
            {"check": item.check_name, "environment": item.environment, "owner": item.owner_id},
        )
        return record_dict(item)


def review_evidence(db, ctx, evidence_id, payload):
    ctx.require_role("release_reviewer")
    with db.session(ctx, write=True) as session:
        item = session.scalar(
            select(ReleaseEvidence)
            .where(
                ReleaseEvidence.id == evidence_id,
                ReleaseEvidence.tenant_id == ctx.tenant_id,
                ReleaseEvidence.store_id == ctx.store_id,
            )
            .with_for_update()
        )
        require(item is not None, "not_found", "上线验收记录不存在", 404)
        require(item.status == "submitted", "evidence_reviewed", "验收凭据已审核，不可覆盖", 409)
        require(
            item.submitted_by != ctx.user_id and item.owner_id != ctx.user_id,
            "separation_of_duties",
            "提交人和负责人均不能审核自己的上线凭据",
            403,
        )
        if payload.decision == "accepted":
            require(
                item.expires_at > now() and item.tested_at <= now(),
                "evidence_expired",
                "测试凭据已过期或测试日期无效",
                409,
            )
        item.status = payload.decision
        item.reviewer_id = ctx.user_id
        item.review_note = payload.note
        item.reviewed_at = now()
        audit(
            session,
            ctx,
            "release_evidence." + item.status,
            item.id,
            {"check": item.check_name, "environment": item.environment, "owner": item.owner_id},
        )
        return record_dict(item)
