"""PostgreSQL row policies; the application role must not own tables or bypass RLS."""

from alembic import op

revision = "e4d915178e7a"
down_revision = "d1f3dd48df5a"
branch_labels = None
depends_on = None

TABLES = (
    "orders",
    "order_lines",
    "inventory",
    "cases",
    "messages",
    "plans",
    "choices",
    "approvals",
    "actions",
    "synthetic_provider_requests",
    "business_events",
    "stock_ledger",
    "policies",
    "runs",
    "run_steps",
    "attachments",
    "notifications",
    "claims",
    "improvements",
    "audit",
)


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        expression = (
            "tenant_id = current_setting('app.tenant_id', true) "
            "AND store_id = current_setting('app.store_id', true)"
        )
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({expression}) WITH CHECK ({expression})')


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'DROP POLICY tenant_scope ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
