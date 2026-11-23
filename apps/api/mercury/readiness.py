"""Machine-readable release conditions; unselected providers cannot become green by configuration."""

from sqlalchemy import text


def report(db):
    settings = db.settings
    checks = []

    def add(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    try:
        with db.session() as s:
            revision = s.scalar(text("SELECT version_num FROM alembic_version"))
            add("database_migrations", revision == "238f42efcd92", revision)
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
    for name in ("OMS", "WMS", "payments", "logistics", "notifications"):
        add(
            "integration_" + name,
            False,
            "Provider and official contract not selected; external adapter not enabled",
        )
    add(
        "deployment_acceptance",
        False,
        "Target environment, backup/failover drill and business approval not supplied",
    )
    return {
        "environment": settings.env,
        "release_ready": all(item["passed"] for item in checks),
        "checks": checks,
        "business_writes_enabled": settings.writes_enabled,
    }
