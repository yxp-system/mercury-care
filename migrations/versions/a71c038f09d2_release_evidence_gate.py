"""Versioned reviewed deployment evidence."""

import sqlalchemy as sa
from alembic import op

revision = "a71c038f09d2"
down_revision = "238f42efcd92"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release_evidence",
        sa.Column("check_name", sa.String(length=80), nullable=False),
        sa.Column("environment", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=160), nullable=False),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("evidence_ref", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tested_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("submitted_by", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reviewer_id", sa.String(length=80), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Float(), nullable=True),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("store_id", sa.String(length=80), nullable=False),
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "store_id", "id"),
    )
    with op.batch_alter_table("release_evidence") as batch:
        batch.create_index("ix_release_evidence_store_id", ["store_id"])
        batch.create_index("ix_release_evidence_tenant_id", ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        expression = "tenant_id = current_setting('app.tenant_id', true) AND store_id = current_setting('app.store_id', true)"
        op.execute('ALTER TABLE "release_evidence" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "release_evidence" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_scope ON "release_evidence" USING ({expression}) WITH CHECK ({expression})'
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute('DROP POLICY tenant_scope ON "release_evidence"')
        op.execute('ALTER TABLE "release_evidence" DISABLE ROW LEVEL SECURITY')
    op.drop_table("release_evidence")
