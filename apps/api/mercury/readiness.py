"""Machine-readable release conditions; unselected providers cannot become green by configuration."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from mercury.db import scoped
from mercury.models import ReleaseEvidence, now


def report(db, ctx=None):
    settings = db.settings
    checks = []
    accepted_evidence = {}

    def add(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    try:
        with db.session(ctx) as s:
            filters = [ReleaseEvidence.environment == settings.env]
            if ctx:
                filters.extend(scoped(ReleaseEvidence, ctx))
            candidates = s.scalars(
                select(ReleaseEvidence).where(*filters).order_by(ReleaseEvidence.created_at.desc())
            )
            for evidence in candidates:
                accepted_evidence.setdefault(
                    evidence.check_name,
                    {
                        "owner_id": evidence.owner_id,
                        "provider": evidence.provider,
                        "evidence_ref": evidence.evidence_ref,
                        "summary": evidence.summary,
                        "tested_at": evidence.tested_at,
                        "expires_at": evidence.expires_at,
                        "status": evidence.status,
                        "reviewer_id": evidence.reviewer_id,
                        "review_note": evidence.review_note,
                    },
                )
            revision = s.scalar(text("SELECT version_num FROM alembic_version"))
            root = Path(__file__).resolve().parents[3]
            expected_revision = ScriptDirectory.from_config(
                Config(str(root / "alembic.ini"))
            ).get_current_head()
            add("database_migrations", revision == expected_revision, revision)
            if db.engine.dialect.name == "postgresql":
                role = s.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                ).one()
                owner = s.scalar(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user"
                    )
                )
                add(
                    "runtime_database_role",
                    not any(role) and owner == 0,
                    "Requires non-owner without SUPERUSER/BYPASSRLS",
                )
            else:
                add("runtime_database_role", False, "PostgreSQL is required for enterprise deployment")
    except Exception:
        add("database_migrations", False, "Database or migration version unavailable")
    add(
        "enterprise_identity",
        settings.auth_mode == "oidc" and settings.oidc_client_id,
        "OIDC code/PKCE configured; provider integration acceptance is separately required",
    )
    add(
        "attachment_scanner",
        settings.scanner_mode == "clamav",
        "ClamAV required; availability must be probed in deployment",
    )
    add(
        "real_models",
        settings.model_mode == "real" and settings.embedding_model,
        "Real chat and embedding configuration; quality evaluation is separately required",
    )
    governance_configured = bool(
        settings.privacy_retention_days > 0
        and settings.audit_retention_days >= settings.privacy_retention_days
        and settings.privacy_policy_ref
        and settings.security_contact
    )
    add(
        "data_governance",
        governance_configured,
        "Retention policy reference, approved data-class schedules and incident owner required",
    )
    add(
        "model_quality",
        settings.model_mode == "real",
        f"De-identified human-reviewed benchmark with at least {settings.model_eval_min_cases} cases and signed evaluation report required",
    )
    for name in ("OMS", "WMS", "payments", "logistics", "notifications"):
        add(
            "integration_" + name,
            False,
            "Provider and official contract not selected; external adapter not enabled",
        )
    add(
        "deployment_acceptance",
        settings.env in {"staging", "production"},
        "Requires independently reviewed deployment evidence for the target environment",
    )
    for item in checks:
        evidence = accepted_evidence.get(item["name"])
        valid = bool(evidence and evidence["status"] == "accepted" and evidence["expires_at"] > now())
        item["configured"] = item["passed"]
        item["evidence_status"] = (
            "missing"
            if evidence is None
            else "expired"
            if evidence["expires_at"] <= now()
            else evidence["status"]
        )
        item["owner"] = evidence["owner_id"] if evidence else None
        item["provider"] = evidence["provider"] if evidence else None
        item["evidence_ref"] = evidence["evidence_ref"] if evidence else None
        item["summary"] = evidence["summary"] if evidence else None
        item["tested_at"] = evidence["tested_at"] if evidence else None
        item["expires_at"] = evidence["expires_at"] if evidence else None
        item["reviewer"] = evidence["reviewer_id"] if evidence else None
        item["review_note"] = evidence["review_note"] if evidence else None
        item["passed"] = bool(item["configured"] and valid)
        item["detail"] += "; " + (
            "valid evidence accepted" if valid else "independently reviewed, unexpired evidence required"
        )
    return {
        "environment": settings.env,
        "release_ready": all(item["passed"] for item in checks),
        "checks": checks,
        "business_writes_enabled": settings.writes_enabled,
    }
