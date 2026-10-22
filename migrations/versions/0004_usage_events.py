from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column(
            "properties",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("member_id", sa.Uuid(), nullable=True),
        sa.Column("member_role", sa.Text(), nullable=True),
        sa.Column("member_segment", sa.Text(), nullable=True),
        sa.Column("visitor_id", sa.Text(), nullable=True),
        sa.Column("session_key", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("device", sa.Text(), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["member.id"],
            name=op.f("fk_usage_event_member_id_member"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_event")),
    )
    op.create_index("ix_usage_event_occurred_at", "usage_event", ["occurred_at"])
    op.create_index(
        "ix_usage_event_member_id_occurred_at", "usage_event", ["member_id", "occurred_at"]
    )
    op.create_index(
        "ix_usage_event_category_action_occurred_at",
        "usage_event",
        ["category", "action", "occurred_at"],
    )
    op.create_index("ix_usage_event_section_occurred_at", "usage_event", ["section", "occurred_at"])
    op.create_index("ix_usage_event_entity", "usage_event", ["entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_table("usage_event")
