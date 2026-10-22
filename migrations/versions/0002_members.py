from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "member",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("job_title", sa.Text(), nullable=True),
        sa.Column("segment", sa.Text(), nullable=True),
        sa.Column("company_size", sa.Text(), nullable=True),
        sa.Column("institution", sa.Text(), nullable=True),
        sa.Column("study_level", sa.Text(), nullable=True),
        sa.Column("regions", sa.JSON(), nullable=False),
        sa.Column("plants", sa.JSON(), nullable=False),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("referral", sa.Text(), nullable=True),
        sa.Column("signup_source", sa.Text(), nullable=True),
        sa.Column("marketing_opt_in", sa.Boolean(), nullable=False),
        sa.Column("privacy_consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lead_score", sa.Integer(), nullable=False),
        sa.Column("lead_tier", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_member_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member")),
        sa.UniqueConstraint("email", name=op.f("uq_member_email")),
    )
    op.create_index("ix_member_lead_score", "member", ["lead_score"])

    op.create_table(
        "member_token",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        sa.CheckConstraint("kind IN ('verify', 'reset')", name=op.f("ck_member_token_kind")),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["member.id"],
            name=op.f("fk_member_token_member_id_member"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_member_token_token_hash")),
    )
    op.create_index("ix_member_token_member_id", "member_token", ["member_id"])

    op.create_table(
        "member_session",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["member.id"],
            name=op.f("fk_member_session_member_id_member"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_member_session_token_hash")),
    )
    op.create_index("ix_member_session_member_id", "member_session", ["member_id"])


def downgrade() -> None:
    op.drop_table("member_session")
    op.drop_table("member_token")
    op.drop_table("member")
