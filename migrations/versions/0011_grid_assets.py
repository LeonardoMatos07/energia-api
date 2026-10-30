from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _text(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Text(), nullable=True)


def _num(name: str, precision: int, scale: int = 2) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Numeric(precision, scale), nullable=True)


def _date(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Date(), nullable=True)


def _int(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer(), nullable=True)


def _versioned(key: str) -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(key, sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        _date("valid_to"),
        sa.Column("row_hash", sa.Text(), nullable=False),
    ]


def _current_index(table: str, key: str) -> None:
    op.create_index(
        f"uq_{table}_current",
        table,
        [key],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
        sqlite_where=sa.text("valid_to IS NULL"),
    )


def upgrade() -> None:
    op.create_table(
        "transmission_line",
        *_versioned("equipment_code"),
        _text("name"),
        _num("voltage_kv", 7),
        _text("line_type"),
        _text("network_type"),
        _text("owner_agent"),
        _text("from_substation"),
        _text("to_substation"),
        sa.Column("from_subsystem", sa.String(3), nullable=True),
        sa.Column("to_subsystem", sa.String(3), nullable=True),
        sa.Column("from_state", sa.String(3), nullable=True),
        sa.Column("to_state", sa.String(3), nullable=True),
        _num("length_km", 9, 3),
        _num("positive_seq_resistance", 16, 6),
        _num("positive_seq_reactance", 16, 6),
        _num("positive_seq_shunt", 16, 6),
        _num("capacity_long_no_limit_a", 10),
        _num("capacity_long_with_limit_a", 10),
        _num("capacity_short_no_limit_a", 10),
        _num("capacity_short_with_limit_a", 10),
        _date("commissioned_on"),
        _date("decommissioned_on"),
        _date("planned_on"),
        _int("from_bus"),
        _int("to_bus"),
        sa.PrimaryKeyConstraint("equipment_code", "valid_from", name=op.f("pk_transmission_line")),
    )
    _current_index("transmission_line", "equipment_code")
    op.create_index("ix_transmission_line_voltage_kv", "transmission_line", ["voltage_kv"])

    op.create_table(
        "substation",
        *_versioned("station_id"),
        _text("substation_id"),
        _text("name"),
        _num("voltage_kv", 7),
        sa.Column("subsystem", sa.String(3), nullable=True),
        sa.Column("state", sa.String(3), nullable=True),
        _text("main_agent"),
        _int("bus_number"),
        _num("latitude", 10, 6),
        _num("longitude", 10, 6),
        sa.PrimaryKeyConstraint("station_id", "valid_from", name=op.f("pk_substation")),
    )
    _current_index("substation", "station_id")
    op.create_index("ix_substation_state", "substation", ["state"])

    op.create_table(
        "transformer_capacity",
        *_versioned("equipment_code"),
        _text("name"),
        _text("transformer_type"),
        _text("substation"),
        _text("owner_agent"),
        sa.Column("subsystem", sa.String(3), nullable=True),
        sa.Column("state", sa.String(3), nullable=True),
        _num("primary_kv", 7),
        _num("secondary_kv", 7),
        _num("tertiary_kv", 7),
        _num("rated_mva", 9),
        _text("network_type"),
        _date("commissioned_on"),
        _date("decommissioned_on"),
        _int("primary_bus"),
        _int("secondary_bus"),
        sa.PrimaryKeyConstraint(
            "equipment_code", "valid_from", name=op.f("pk_transformer_capacity")
        ),
    )
    _current_index("transformer_capacity", "equipment_code")


def downgrade() -> None:
    op.drop_index("uq_transformer_capacity_current", table_name="transformer_capacity")
    op.drop_table("transformer_capacity")
    op.drop_index("ix_substation_state", table_name="substation")
    op.drop_index("uq_substation_current", table_name="substation")
    op.drop_table("substation")
    op.drop_index("ix_transmission_line_voltage_kv", table_name="transmission_line")
    op.drop_index("uq_transmission_line_current", table_name="transmission_line")
    op.drop_table("transmission_line")
