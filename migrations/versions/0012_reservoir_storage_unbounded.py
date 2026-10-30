from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "ck_reservoir_daily_storage_pct"


def upgrade() -> None:
    # A reservoir's stored energy is negative below its minimum operating volume (drought years
    # such as 2015 show -80%) and above 100% while it spills (up to ~380%): no fixed range holds.
    with op.batch_alter_table("reservoir_daily") as batch:
        batch.drop_constraint(CHECK, type_="check")


def downgrade() -> None:
    with op.batch_alter_table("reservoir_daily") as batch:
        batch.create_check_constraint(
            CHECK, sa.text("storage_pct IS NULL OR storage_pct BETWEEN 0 AND 200")
        )
