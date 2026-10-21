import pytest
from fastapi.testclient import TestClient

from app.cache import PUBLIC_CACHE_CONTROL

pytestmark = pytest.mark.usefixtures("seed")

USINA_RESUMO_KEYS = {"id", "tipo", "nome", "slug", "fonte", "uf", "subsistema", "potencia_mw"}
RANKING_KEYS = USINA_RESUMO_KEYS | {
    "posicao",
    "energia_cortada_mwh",
    "percentual_corte",
    "perda_estimada_brl",
}
AGG_KEYS = {
    "fonte",
    "mes",
    "geracao_verificada_mwh",
    "energia_cortada_mwh",
    "percentual_corte",
    "corte_rel_mwh",
    "corte_cnf_mwh",
    "corte_ene_mwh",
    "corte_outros_mwh",
    "perda_estimada_brl",
    "usinas_afetadas",
    "atualizado_em",
}


def test_sin_default_total(client: TestClient) -> None:
    r = client.get("/v1/curtailment/sin")
    assert r.status_code == 200
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    itens = r.json()["itens"]
    assert len(itens) == 6  # 3 months x (SIN, NE)
    assert {i["fonte"] for i in itens} == {"total"}
    assert set(itens[0]) == AGG_KEYS | {"subsistema"}
    assert itens[0]["mes"] == "2026-07"
    assert itens[0]["energia_cortada_mwh"] == 90.0  # numeric -> JSON number
    assert isinstance(itens[0]["perda_estimada_brl"], float)


def test_sin_filters(client: TestClient) -> None:
    r = client.get("/v1/curtailment/sin", params={"fonte": "eolica", "inicio": "2026-08"})
    itens = r.json()["itens"]
    assert [i["mes"] for i in itens] == ["2026-08", "2026-09"]
    assert {i["fonte"] for i in itens} == {"eolica"}

    r = client.get("/v1/curtailment/sin", params={"inicio": "2026-08", "fim": "2026-08"})
    assert {i["mes"] for i in r.json()["itens"]} == {"2026-08"}


@pytest.mark.parametrize("params", [{"fonte": "carvao"}, {"inicio": "2026-13"}, {"fim": "2026-9"}])
def test_sin_invalid_params(client: TestClient, params: dict[str, str]) -> None:
    r = client.get("/v1/curtailment/sin", params=params)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


def test_uf_default(client: TestClient) -> None:
    r = client.get("/v1/curtailment/uf/ba")
    assert r.status_code == 200
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    body = r.json()
    assert set(body) == {"serie", "ranking", "mes_ranking"}
    assert [s["mes"] for s in body["serie"]] == ["2026-08", "2026-09"]
    assert set(body["serie"][0]) == AGG_KEYS | {"uf"}
    assert body["mes_ranking"] == "2026-09"
    ranking = body["ranking"]
    # Only tipo = 'usina' (the conjunto is excluded), ordered by curtailed energy.
    assert [u["id"] for u in ranking] == ["BAU1", "BAS1", "BAU2"]
    assert [u["posicao"] for u in ranking] == [1, 2, 3]
    assert set(ranking[0]) == RANKING_KEYS


def test_uf_fonte_and_mes(client: TestClient) -> None:
    body = client.get("/v1/curtailment/uf/BA", params={"fonte": "eolica", "mes": "2026-08"}).json()
    assert {s["fonte"] for s in body["serie"]} == {"eolica"}
    assert body["mes_ranking"] == "2026-08"
    assert [(u["id"], u["posicao"]) for u in body["ranking"]] == [("BAU1", 1)]


def test_uf_unknown(client: TestClient) -> None:
    assert client.get("/v1/curtailment/uf/XX").status_code == 404
    assert client.get("/v1/curtailment/uf/BAH").status_code == 422


def test_uf_without_data(client: TestClient) -> None:
    body = client.get("/v1/curtailment/uf/SP").json()
    assert body == {"serie": [], "ranking": [], "mes_ranking": None}


def test_usinas_list_and_filters(client: TestClient) -> None:
    r = client.get("/v1/usinas")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    body = r.json()
    assert body["total"] == 5 and body["pagina"] == 1 and body["tamanho"] == 20
    assert set(body["itens"][0]) == USINA_RESUMO_KEYS

    body = client.get("/v1/usinas", params={"tamanho": 2, "pagina": 2}).json()
    assert body["total"] == 5 and len(body["itens"]) == 2 and body["pagina"] == 2

    body = client.get("/v1/usinas", params={"busca": "ALFA"}).json()
    assert {u["id"] for u in body["itens"]} == {"CJU_BA1", "BAU1", "BAU2"}

    body = client.get("/v1/usinas", params={"uf": "ba", "fonte": "solar"}).json()
    assert [u["id"] for u in body["itens"]] == ["BAS1"]

    body = client.get("/v1/usinas", params={"tipo": "conjunto"}).json()
    assert [u["id"] for u in body["itens"]] == ["CJU_BA1"]

    body = client.get("/v1/usinas", params={"busca": "100%"}).json()
    assert body["total"] == 0

    assert client.get("/v1/usinas", params={"tamanho": 101}).status_code == 422


def test_usina_detail_member(client: TestClient) -> None:
    r = client.get("/v1/usinas/alfa-i-bau1")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"usina", "conjunto", "usinas_do_conjunto", "serie"}
    assert set(body["usina"]) == USINA_RESUMO_KEYS | {
        "conjunto_id",
        "ceg",
        "ponto_conexao",
        "agente_operador",
        "atualizado_em",
    }
    assert body["conjunto"]["id"] == "CJU_BA1"
    assert {u["id"] for u in body["usinas_do_conjunto"]} == {"BAU1", "BAU2"}
    assert [s["mes"] for s in body["serie"]] == ["2026-08", "2026-09"]
    assert body["serie"][1]["posicao_uf"] == 1
    assert body["serie"][1]["usina_id"] == "BAU1"


def test_usina_detail_conjunto_and_standalone(client: TestClient) -> None:
    body = client.get("/v1/usinas/conjunto-alfa-cjuba1").json()
    assert body["conjunto"] is None
    assert {u["id"] for u in body["usinas_do_conjunto"]} == {"BAU1", "BAU2"}

    body = client.get("/v1/usinas/sol-baiano-bas1").json()
    assert body["conjunto"] is None and body["usinas_do_conjunto"] == []

    r = client.get("/v1/usinas/nao-existe")
    assert r.status_code == 404
    assert r.json() == {"detail": "Usina não encontrada."}


def test_ranking(client: TestClient) -> None:
    r = client.get("/v1/ranking")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    body = r.json()
    assert body["mes"] == "2026-09"
    assert [u["id"] for u in body["itens"]] == ["RNU1", "BAU1", "BAS1", "BAU2"]
    assert [u["posicao"] for u in body["itens"]] == [1, 2, 3, 4]
    assert set(body["itens"][0]) == RANKING_KEYS

    body = client.get("/v1/ranking", params={"fonte": "eolica"}).json()
    assert [(u["id"], u["posicao"]) for u in body["itens"]] == [
        ("RNU1", 1),
        ("BAU1", 2),
        ("BAU2", 3),
    ]

    body = client.get("/v1/ranking", params={"uf": "BA", "limite": 1}).json()
    assert [u["id"] for u in body["itens"]] == ["BAU1"]

    body = client.get("/v1/ranking", params={"mes": "2026-08"}).json()
    assert body["mes"] == "2026-08" and [u["id"] for u in body["itens"]] == ["BAU1"]

    assert client.get("/v1/ranking", params={"limite": 101}).status_code == 422


def test_meses(client: TestClient) -> None:
    r = client.get("/v1/meses")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert r.json() == {"meses": ["2026-09", "2026-08", "2026-07"]}


def test_relatorios(client: TestClient) -> None:
    r = client.get("/v1/relatorios")
    assert r.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    itens = r.json()["itens"]
    assert [i["slug"] for i in itens] == ["relatorio-agosto-2026"]
    assert set(itens[0]) == {"mes", "titulo", "slug", "resumo", "publicado_em"}
    assert itens[0]["mes"] == "2026-08"

    r = client.get("/v1/relatorios/relatorio-agosto-2026")
    assert r.status_code == 200
    assert r.json()["conteudo"] == "# Agosto"

    r = client.get("/v1/relatorios/relatorio-setembro-2026")  # draft
    assert r.status_code == 404
    assert r.json() == {"detail": "Relatório não encontrado."}


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "banco": "ok"}
