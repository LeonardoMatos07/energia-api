from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_dispatch",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False),
        sa.Column("sent", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('report', 'analysis')", name="ck_email_dispatch_kind"),
        sa.CheckConstraint("status IN ('running', 'done', 'partial')", name="ck_email_dispatch_status"),
        sa.ForeignKeyConstraint(
            ["report_id"], ["monthly_report.id"], name="fk_email_dispatch_report_id_monthly_report"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_dispatch"),
        sa.UniqueConstraint("kind", "month", name="uq_email_dispatch_kind_month"),
    )


def downgrade() -> None:
    op.drop_table("email_dispatch")
