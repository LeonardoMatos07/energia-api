import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CurtailmentMensalSin
from tests.conftest import M2M_CLIENT, make_settings

# --- auth --------------------------------------------------------------------------------------


def test_missing_token_401(client: TestClient) -> None:
    r = client.get("/v1/admin/cargas")
    assert r.status_code == 401
    assert r.json() == {"detail": "Autenticação necessária."}
    assert r.headers["www-authenticate"] == "Bearer"


def test_wrong_group_403(client: TestClient, make_token: Callable[..., str]) -> None:
    token = make_token(**{"cognito:groups": ["leitor"]})
    r = client.get("/v1/admin/cargas", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_admin_group_ok(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.get("/v1/admin/cargas", headers=admin_headers)
    assert r.status_code == 200
    assert r.json() == {"itens": []}


def test_m2m_scope_ok(client: TestClient, make_token: Callable[..., str]) -> None:
    token = make_token(client_id=M2M_CLIENT, sub=M2M_CLIENT, scope="energia-api/admin")
    r = client.get("/v1/admin/cargas", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_m2m_other_scope_403(client: TestClient, make_token: Callable[..., str]) -> None:
    token = make_token(client_id=M2M_CLIENT, scope="energia-api/leitura")
    r = client.get("/v1/admin/cargas", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "claims",
    [
        {"token_use": "id"},
        {"client_id": "outro-client"},
        {"iss": "https://cognito-idp.sa-east-1.amazonaws.com/outro-pool"},
        {"exp": 1_000_000},
        {"token_use": None},
        {"kid": "chave-desconhecida"},
    ],
)
def test_invalid_tokens_401(
    client: TestClient, make_token: Callable[..., str], claims: dict[str, Any]
) -> None:
    claims = {"cognito:groups": ["admin"], **claims}
    if "exp" in claims:
        claims["iat"] = claims["exp"] - 60
    token = make_token(**claims)
    r = client.get("/v1/admin/cargas", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_wrong_signing_key_401(client: TestClient, make_token: Callable[..., str]) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = make_token(key=other, **{"cognito:groups": ["admin"]})
    r = client.get("/v1/admin/cargas", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_garbage_token_401(client: TestClient) -> None:
    r = client.get("/v1/admin/cargas", headers={"Authorization": "Bearer nao.e.jwt"})
    assert r.status_code == 401


def test_jwks_is_cached(
    app_factory: Callable[..., TestClient],
    jwks: dict[str, Any],
    admin_headers: dict[str, str],
) -> None:
    client = app_factory()
    calls: list[str] = []

    def fetcher(url: str) -> dict[str, Any]:
        calls.append(url)
        return jwks

    client.app.state.jwks.fetcher = fetcher  # type: ignore[attr-defined]
    for _ in range(3):
        assert client.get("/v1/admin/cargas", headers=admin_headers).status_code == 200
    assert calls == [
        "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TESTPOOL/.well-known/jwks.json"
    ]


def test_auth_desativada_requires_local() -> None:
    with pytest.raises(ValidationError, match="AUTH_DESATIVADA"):
        make_settings(AMBIENTE="prod", AUTH_DESATIVADA=True)


def test_prod_requires_cognito() -> None:
    with pytest.raises(ValidationError, match="COGNITO_USER_POOL_ID"):
        make_settings(AMBIENTE="prod", COGNITO_USER_POOL_ID="")


def test_auth_desativada_local_allows_admin(app_factory: Callable[..., TestClient]) -> None:
    client = app_factory(AUTH_DESATIVADA=True)
    assert client.get("/v1/admin/cargas").status_code == 200


def test_database_url_normalized() -> None:
    s = Settings(_env_file=None, DATABASE_URL="postgresql://u:p@h:5432/d")
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d"


# --- admin routes ------------------------------------------------------------------------------


@pytest.mark.usefixtures("seed")
def test_cargas(client: TestClient, admin_headers: dict[str, str]) -> None:
    itens = client.get("/v1/admin/cargas", headers=admin_headers).json()["itens"]
    assert [i["dataset"] for i in itens] == ["agregados", "wind_plant_curtailment"]
    assert set(itens[0]) == {
        "id",
        "dataset",
        "periodo",
        "arquivo",
        "hash",
        "linhas",
        "inicio",
        "fim",
        "status",
        "detalhe",
    }
    assert itens[0]["periodo"] == "2026-09"
    limited = client.get("/v1/admin/cargas", params={"limite": 1}, headers=admin_headers)
    assert len(limited.json()["itens"]) == 1


@pytest.mark.usefixtures("seed")
def test_admin_relatorios_lists_drafts(client: TestClient, admin_headers: dict[str, str]) -> None:
    itens = client.get("/v1/admin/relatorios", headers=admin_headers).json()["itens"]
    assert {i["slug"] for i in itens} == {"relatorio-agosto-2026", "relatorio-setembro-2026"}


def test_upsert_relatorio(client: TestClient, admin_headers: dict[str, str]) -> None:
    body = {"mes": "2026-09", "titulo": "Curtailment em Setembro", "conteudo": "# Texto"}
    r = client.post("/v1/admin/relatorios", json={**body, "publicar": False}, headers=admin_headers)
    assert r.status_code == 200
    created = r.json()
    assert created["slug"] == "curtailment-em-setembro"
    assert created["mes"] == "2026-09"
    assert created["publicado_em"] is None
    assert client.get("/v1/relatorios/curtailment-em-setembro").status_code == 404

    r = client.post("/v1/admin/relatorios", json={**body, "publicar": True}, headers=admin_headers)
    published = r.json()
    assert published["publicado_em"] is not None
    # Upsert cleared the cache, so the public route sees it immediately.
    assert client.get("/v1/relatorios/curtailment-em-setembro").status_code == 200

    r = client.post(
        "/v1/admin/relatorios",
        json={**body, "resumo": "novo", "publicar": True},
        headers=admin_headers,
    )
    assert r.json()["publicado_em"] == published["publicado_em"]  # original date kept
    assert r.json()["resumo"] == "novo"
    assert len(client.get("/v1/admin/relatorios", headers=admin_headers).json()["itens"]) == 1

    r = client.post(
        "/v1/admin/relatorios",
        json={**body, "slug": "Slug Invalido", "publicar": True},
        headers=admin_headers,
    )
    assert r.status_code == 422


def test_upsert_relatorio_requires_admin(client: TestClient) -> None:
    body = {"mes": "2026-09", "titulo": "x", "conteudo": "y", "publicar": True}
    assert client.post("/v1/admin/relatorios", json=body).status_code == 401


# --- cache + revalidate ------------------------------------------------------------------------


def _add_sin_row(session: Session, mes: date) -> None:
    session.add(
        CurtailmentMensalSin(
            subsistema="SIN",
            fonte="total",
            mes=mes,
            geracao_verificada_mwh=Decimal(1),
            energia_cortada_mwh=Decimal(1),
            corte_rel_mwh=Decimal(0),
            corte_cnf_mwh=Decimal(0),
            corte_ene_mwh=Decimal(0),
            corte_outros_mwh=Decimal(0),
            usinas_afetadas=0,
        )
    )
    session.commit()


@pytest.mark.usefixtures("seed")
def test_cache_and_revalidate(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    web_calls: list[httpx.Request],
) -> None:
    before = client.get("/v1/meses").json()["meses"]
    _add_sin_row(session, date(2026, 10, 1))
    assert client.get("/v1/meses").json()["meses"] == before  # served from cache

    r = client.post(
        "/v1/admin/revalidar", json={"tags": ["curtailment", "usinas"]}, headers=admin_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tags"] == ["curtailment", "usinas"]
    assert body["cache_limpo"] is True and body["entradas_removidas"] >= 1
    assert body["web_revalidado"] is True and body["detalhe"] is None

    assert len(web_calls) == 1
    call = web_calls[0]
    assert str(call.url) == "http://web.test/api/revalidar"
    assert call.headers["x-revalidate-secret"] == "segredo-de-teste"
    assert json.loads(call.content) == {"tags": ["curtailment", "usinas"]}

    assert client.get("/v1/meses").json()["meses"][0] == "2026-10"


def test_revalidate_default_tags(
    client: TestClient, admin_headers: dict[str, str], web_calls: list[httpx.Request]
) -> None:
    r = client.post("/v1/admin/revalidar", headers=admin_headers)
    assert r.json()["tags"] == ["curtailment", "usinas", "relatorios"]
    assert json.loads(web_calls[0].content) == {"tags": ["curtailment", "usinas", "relatorios"]}


def test_revalidate_unknown_tag(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.post("/v1/admin/revalidar", json={"tags": ["outra"]}, headers=admin_headers)
    assert r.status_code == 422


def test_revalidate_web_unreachable(
    app_factory: Callable[..., TestClient], admin_headers: dict[str, str]
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado", request=request)

    client = app_factory(transport=httpx.MockTransport(fail))
    r = client.post("/v1/admin/revalidar", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["cache_limpo"] is True
    assert body["web_revalidado"] is False
    assert "ConnectError" in body["detalhe"]


def test_revalidate_web_error_status(
    app_factory: Callable[..., TestClient], admin_headers: dict[str, str]
) -> None:
    client = app_factory(transport=httpx.MockTransport(lambda req: httpx.Response(401)))
    body = client.post("/v1/admin/revalidar", headers=admin_headers).json()
    assert body["web_revalidado"] is False


def test_revalidate_without_secret(
    app_factory: Callable[..., TestClient],
    admin_headers: dict[str, str],
    web_calls: list[httpx.Request],
) -> None:
    client = app_factory(REVALIDATE_SECRET="")
    body = client.post("/v1/admin/revalidar", headers=admin_headers).json()
    assert body["web_revalidado"] is False and web_calls == []
