"""Migration checks (Postgres only: set TEST_DATABASE_URL)."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

from tests.conftest import ROOT, TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL (Postgres)"
)

TABLES = {
    "usina",
    "curtailment_mensal_usina",
    "curtailment_mensal_uf",
    "curtailment_mensal_sin",
    "carga_execucao",
    "relatorio_mensal",
    "assinante_newsletter",
    "organizacao",
    "usuario",
}


def _config(engine: Engine) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    url = engine.url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def test_downgrade_upgrade_roundtrip_and_no_drift(pg_engine: Engine) -> None:
    config = _config(pg_engine)
    command.downgrade(config, "base")
    assert TABLES.isdisjoint(inspect(pg_engine).get_table_names(schema="public"))
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names(schema="public")) >= TABLES
    command.check(config)  # raises if models and migrations diverge
