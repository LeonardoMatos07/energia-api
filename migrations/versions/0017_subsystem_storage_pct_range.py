"""Allow the stored-energy percentage of a subsystem above 100 (old years publish it)."""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "storage_pct"  # the naming convention prefixes it: ck_subsystem_storage_daily_storage_pct
TABLE = "subsystem_storage_daily"


def upgrade() -> None:
    op.drop_constraint(op.f(f"ck_{TABLE}_{NAME}"), TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, "storage_pct IS NULL OR storage_pct BETWEEN 0 AND 200")


def downgrade() -> None:
    op.drop_constraint(op.f(f"ck_{TABLE}_{NAME}"), TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, "storage_pct IS NULL OR storage_pct BETWEEN 0 AND 100")
