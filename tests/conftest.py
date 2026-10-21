import os
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.email import EmailMessage
from app.main import create_app
from app.models import (
    Base,
    LoadRun,
    MonthlyReport,
    Plant,
    PlantMonthlyCurtailment,
    StateMonthlyCurtailment,
    SubsystemMonthlyCurtailment,
)

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
POOL_ID = "sa-east-1_TESTPOOL"
ISSUER = f"https://cognito-idp.sa-east-1.amazonaws.com/{POOL_ID}"
WEB_CLIENT = "web-client-id"
M2M_CLIENT = "m2m-client-id"
KID = "test-key-1"


def _normalize(url: str) -> str:
    return Settings(_env_file=None, DATABASE_URL=url).database_url


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine | None]:
    if not TEST_DATABASE_URL:
        yield None
        return
    from alembic import command
    from alembic.config import Config

    url = _normalize(TEST_DATABASE_URL)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(pg_engine: Engine | None) -> Iterator[Engine]:
    if pg_engine is not None:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        with pg_engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        yield pg_engine
        return
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


@pytest.fixture
def emails() -> FakeEmailSender:
    return FakeEmailSender()


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "ENVIRONMENT": "local",
        "AUTH_DISABLED": False,
        "DATABASE_URL": "sqlite://",
        "WEB_URL": "http://web.test",
        "REVALIDATE_SECRET": "test-secret",
        "COGNITO_REGION": "sa-east-1",
        "COGNITO_USER_POOL_ID": POOL_ID,
        "COGNITO_APP_CLIENT_IDS": f"{WEB_CLIENT}, {M2M_CLIENT}",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwks(rsa_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


@pytest.fixture
def make_token(rsa_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _make(kid: str = KID, key: Any = None, **claims: Any) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": str(uuid.uuid4()),
            "iss": ISSUER,
            "token_use": "access",
            "client_id": WEB_CLIENT,
            "iat": now,
            "exp": now + 600,
        }
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(payload, key or rsa_key, algorithm="RS256", headers={"kid": kid})

    return _make


@pytest.fixture
def web_calls() -> list[Any]:
    return []


@pytest.fixture
def app_factory(
    engine: Engine, emails: FakeEmailSender, jwks: dict[str, Any], web_calls: list[Any]
) -> Callable[..., TestClient]:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        web_calls.append(request)
        return httpx.Response(200, json={"revalidated": True})

    def _factory(
        transport: httpx.BaseTransport | None = None, **settings_overrides: Any
    ) -> TestClient:
        app = create_app(
            make_settings(**settings_overrides),
            engine=engine,
            email_sender=emails,
            web_transport=transport or httpx.MockTransport(handler),
        )
        app.state.jwks.fetcher = lambda url: jwks
        return TestClient(app)

    return _factory


@pytest.fixture
def client(app_factory: Callable[..., TestClient]) -> TestClient:
    return app_factory()


@pytest.fixture
def admin_headers(make_token: Callable[..., str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(**{'cognito:groups': ['admin']})}"}


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as s:
        yield s


def _plant(id_: str, kind: str, name: str, source: str, state: str, **kw: Any) -> Plant:
    return Plant(
        id=id_,
        kind=kind,
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{id_.lower().replace('_', '')}",
        source=source,
        state=state,
        subsystem="NE",
        capacity_mw=Decimal("100.500"),
        **kw,
    )


def _plant_month(plant_id: str, month: date, curtailed: str, **kw: Any) -> PlantMonthlyCurtailment:
    return PlantMonthlyCurtailment(
        plant_id=plant_id,
        month=month,
        verified_generation_mwh=Decimal("1000.000"),
        curtailed_mwh=Decimal(curtailed),
        curtailment_pct=Decimal("10.50"),
        curtailed_rel_mwh=Decimal("1.000"),
        curtailed_cnf_mwh=Decimal("2.000"),
        curtailed_ene_mwh=Decimal("3.000"),
        curtailed_other_mwh=Decimal("0.000"),
        estimated_loss_brl=Decimal("12345.67"),
        price_basis="PLD",
        restricted_hours=Decimal("12.5"),
        **kw,
    )


def _aggregate(
    cls: type[Any], key: dict[str, str], source: str, month: date, curtailed: str
) -> Any:
    return cls(
        **key,
        source=source,
        month=month,
        verified_generation_mwh=Decimal("5000.000"),
        curtailed_mwh=Decimal(curtailed),
        curtailment_pct=Decimal("4.76"),
        curtailed_rel_mwh=Decimal("10.000"),
        curtailed_cnf_mwh=Decimal("20.000"),
        curtailed_ene_mwh=Decimal("30.000"),
        curtailed_other_mwh=Decimal("0.000"),
        estimated_loss_brl=Decimal("999.99"),
        affected_plants=3,
    )


AUG = date(2026, 8, 1)
SEP = date(2026, 9, 1)
JUL = date(2026, 7, 1)


@pytest.fixture
def seed(session: Session) -> None:
    session.add(_plant("CJU_BA1", "cluster", "Alpha Cluster", "wind", "BA"))
    session.flush()
    session.add_all(
        [
            _plant("BAU1", "plant", "Alpha I", "wind", "BA", cluster_id="CJU_BA1"),
            _plant("BAU2", "plant", "Alpha II", "wind", "BA", cluster_id="CJU_BA1"),
            _plant("BAS1", "plant", "Bahia Sun", "solar", "BA"),
            _plant("RNU1", "plant", "Potiguar Winds", "wind", "RN"),
        ]
    )
    session.flush()
    session.add_all(
        [
            _plant_month("CJU_BA1", SEP, "900.000"),
            _plant_month("BAU1", SEP, "500.000", national_rank=2, state_rank=1),
            _plant_month("BAU2", SEP, "400.000", national_rank=3, state_rank=2),
            _plant_month("BAS1", SEP, "450.000", national_rank=1, state_rank=1),
            _plant_month("RNU1", SEP, "800.000", national_rank=1, state_rank=1),
            _plant_month("BAU1", AUG, "100.000", national_rank=1, state_rank=1),
        ]
    )
    for month in (JUL, AUG, SEP):
        session.add(
            _aggregate(SubsystemMonthlyCurtailment, {"subsystem": "SIN"}, "total", month, "100.000")
        )
        session.add(
            _aggregate(SubsystemMonthlyCurtailment, {"subsystem": "NE"}, "total", month, "90.000")
        )
        session.add(
            _aggregate(SubsystemMonthlyCurtailment, {"subsystem": "SIN"}, "wind", month, "80.000")
        )
    for month in (AUG, SEP):
        session.add(_aggregate(StateMonthlyCurtailment, {"state": "BA"}, "total", month, "70.000"))
        session.add(_aggregate(StateMonthlyCurtailment, {"state": "BA"}, "wind", month, "60.000"))
    now = datetime.now(UTC)
    session.add_all(
        [
            MonthlyReport(
                month=AUG,
                title="August",
                slug="report-august-2026",
                summary="August summary",
                content="# August",
                published_at=now,
            ),
            MonthlyReport(
                month=SEP, title="September", slug="report-september-2026", content="# Draft"
            ),
            LoadRun(
                dataset="wind_plant_curtailment",
                period=SEP,
                started_at=datetime(2026, 10, 1, 3, tzinfo=UTC),
                status="success",
                row_count=10,
            ),
            LoadRun(
                dataset="aggregates",
                period=SEP,
                started_at=datetime(2026, 10, 1, 4, tzinfo=UTC),
                status="failed",
                detail="error",
            ),
        ]
    )
    session.commit()
