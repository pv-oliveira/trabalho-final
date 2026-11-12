"""Testes da busca avançada com filtros combináveis na Web (issue #4)."""

import json
from datetime import date

import psycopg
import pytest

import api
import db


def _eventos(capsys):
    return [json.loads(linha) for linha in capsys.readouterr().out.strip().splitlines()]


def _conexao_quebrada():
    raise psycopg.OperationalError("banco fora do ar")


def _falhar_no_banco(*_args):
    raise psycopg.OperationalError("banco fora do ar")


def _id_produto(nome):
    with db.obter_conexao() as conexao:
        return conexao.execute("SELECT id FROM produto WHERE nome = %s", (nome,)).fetchone()["id"]


def _inserir_venda(produto, quantidade, valor_unitario, data_venda):
    with db.obter_conexao() as conexao:
        return conexao.execute(
            "INSERT INTO venda (produto_id, quantidade, valor_unitario, data_venda) VALUES (%s, %s, %s, %s) RETURNING id",
            (_id_produto(produto), quantidade, valor_unitario, data_venda),
        ).fetchone()["id"]


@pytest.fixture(autouse=True)
def vendas_limpas():
    """Cada teste começa sem vendas, para os resultados serem previsíveis."""
    with db.obter_conexao() as conexao:
        conexao.execute("TRUNCATE venda RESTART IDENTITY")


@pytest.fixture
def cabecalho(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def vendas():
    """10 vendas de 3 categorias em datas diferentes. Retorna {apelido: id}."""
    dados = {
        "arroz_set": ("Arroz 5 kg", 1, "27.90", "2026-09-10"),
        "arroz_out": ("Arroz 5 kg", 2, "27.90", "2026-10-05"),
        "arroz_nov": ("Arroz 5 kg", 3, "26.00", "2026-11-02"),
        "feijao_out": ("Feijão 1 kg", 4, "8.50", "2026-10-12"),
        "feijao_nov": ("Feijão 1 kg", 1, "8.50", "2026-11-05"),
        "refri_set": ("Refrigerante 2 L", 6, "9.90", "2026-09-20"),
        "refri_out": ("Refrigerante 2 L", 2, "9.90", "2026-10-20"),
        "suco_out": ("Suco de laranja 1 L", 1, "7.90", "2026-10-25"),
        "sabonete_out": ("Sabonete", 10, "2.50", "2026-10-15"),
        "sabonete_nov": ("Sabonete", 5, "2.50", "2026-11-10"),
    }
    return {apelido: _inserir_venda(*venda) for apelido, venda in dados.items()}


def _buscar(cliente_http, cabecalho, consulta=""):
    return cliente_http.get(f"/api/web/records/search?{consulta}", headers=cabecalho)


def _ids_encontrados(cliente_http, cabecalho, consulta):
    """Percorre todas as páginas da busca (ordenada por id) e devolve os ids encontrados."""
    ids, pagina = [], 1
    while True:
        corpo = _buscar(cliente_http, cabecalho, f"{consulta}&ordenar=id&direcao=asc&pagina={pagina}").get_json()
        ids += [venda["id"] for venda in corpo["vendas"]]
        if pagina >= corpo["total_paginas"]:
            assert len(ids) == corpo["total"]
            return ids
        pagina += 1


# --- autenticação ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cabecalho_auth", [None, "Bearer invalido", "Basic abc", "Bearer "])
def test_busca_exige_autenticacao(cliente_http, cabecalho_auth, capsys):
    cabecalhos = {"Authorization": cabecalho_auth} if cabecalho_auth else {}
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/records/search?categoria=Bebidas", headers=cabecalhos)
    assert resposta.status_code == 401 and resposta.get_json() == {"erro": "Não autenticado"}
    evento = _eventos(capsys)[-1]
    assert (evento["layer"], evento["operation"], evento["level"]) == ("api", "buscar_vendas", "WARN")


def test_busca_com_parametros_invalidos_sem_token_responde_401(cliente_http):
    assert cliente_http.get("/api/web/records/search?data_inicio=xyz").status_code == 401


def test_rota_search_nao_e_confundida_com_id_de_venda(cliente_http, cabecalho, vendas):
    resposta = _buscar(cliente_http, cabecalho)
    assert resposta.status_code == 200 and "filtros" in resposta.get_json()


# --- filtros combináveis (1, 2 e 3 filtros) --------------------------------------------------------


# Os 3 filtros da especificação (§4.4): produto, categoria e período (data_inicio e/ou data_fim).
OUTUBRO = {"data_inicio": "2026-10-01", "data_fim": "2026-10-31"}


@pytest.mark.parametrize(
    "filtros, esperados",
    [
        pytest.param(
            {},
            [
                "arroz_set", "arroz_out", "arroz_nov", "feijao_out", "feijao_nov",
                "refri_set", "refri_out", "suco_out", "sabonete_out", "sabonete_nov",
            ],
            id="0-filtros",
        ),
        # 1 filtro
        pytest.param({"produto_id": "Arroz 5 kg"}, ["arroz_set", "arroz_out", "arroz_nov"], id="1-produto"),
        pytest.param({"categoria": "Bebidas"}, ["refri_set", "refri_out", "suco_out"], id="1-categoria"),
        pytest.param(
            OUTUBRO, ["arroz_out", "feijao_out", "refri_out", "suco_out", "sabonete_out"], id="1-periodo-completo"
        ),
        pytest.param({"data_inicio": "2026-11-01"}, ["arroz_nov", "feijao_nov", "sabonete_nov"], id="1-periodo-so-inicio"),
        pytest.param({"data_fim": "2026-09-30"}, ["arroz_set", "refri_set"], id="1-periodo-so-fim"),
        pytest.param({"categoria": "Limpeza"}, [], id="1-categoria-sem-vendas"),
        # 2 filtros
        pytest.param({"produto_id": "Sabonete", "categoria": "Higiene"}, ["sabonete_out", "sabonete_nov"], id="2-produto-categoria"),
        pytest.param({"produto_id": "Arroz 5 kg", "categoria": "Bebidas"}, [], id="2-produto-de-outra-categoria"),
        pytest.param({"produto_id": "Arroz 5 kg", **OUTUBRO}, ["arroz_out"], id="2-produto-periodo"),
        pytest.param({"produto_id": "Refrigerante 2 L", "data_fim": "2026-09-30"}, ["refri_set"], id="2-produto-periodo-so-fim"),
        pytest.param({"categoria": "Alimentos", **OUTUBRO}, ["arroz_out", "feijao_out"], id="2-categoria-periodo"),
        pytest.param(
            {"categoria": "Alimentos", "data_inicio": "2026-11-01"}, ["arroz_nov", "feijao_nov"], id="2-categoria-periodo-so-inicio"
        ),
        # 3 filtros
        pytest.param({"produto_id": "Refrigerante 2 L", "categoria": "Bebidas", **OUTUBRO}, ["refri_out"], id="3-todos"),
        pytest.param(
            {"produto_id": "Feijão 1 kg", "categoria": "Alimentos", "data_inicio": "2026-11-01"},
            ["feijao_nov"],
            id="3-todos-periodo-so-inicio",
        ),
        pytest.param({"produto_id": "Arroz 5 kg", "categoria": "Bebidas", **OUTUBRO}, [], id="3-produto-de-outra-categoria"),
        pytest.param(
            {"produto_id": "Sabonete", "categoria": "Higiene", "data_inicio": "2026-10-13", "data_fim": "2026-10-14"},
            [],
            id="3-periodo-sem-vendas",
        ),
    ],
)
def test_filtros_combinados_retornam_as_vendas_certas(cliente_http, cabecalho, vendas, filtros, esperados):
    parametros = dict(filtros)
    if "produto_id" in parametros:
        parametros["produto_id"] = _id_produto(parametros["produto_id"])
    consulta = "&".join(f"{campo}={valor}" for campo, valor in parametros.items())
    assert _ids_encontrados(cliente_http, cabecalho, consulta) == sorted(vendas[apelido] for apelido in esperados)


def test_periodo_inclui_as_datas_dos_limites(cliente_http, cabecalho, vendas):
    ids = _ids_encontrados(cliente_http, cabecalho, "data_inicio=2026-10-05&data_fim=2026-10-05")
    assert ids == [vendas["arroz_out"]]


def test_resposta_traz_paginacao_ordenacao_e_filtros_aplicados(cliente_http, cabecalho, vendas):
    resposta = _buscar(cliente_http, cabecalho, "categoria=Alimentos&data_inicio=2026-10-01&ordenar=valor_total&direcao=asc")
    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["filtros"] == {"produto_id": None, "categoria": "Alimentos", "data_inicio": "2026-10-01", "data_fim": None}
    assert (corpo["pagina"], corpo["itens_por_pagina"], corpo["total"], corpo["total_paginas"]) == (1, 5, 4, 1)
    assert (corpo["ordenar"], corpo["direcao"]) == ("valor_total", "asc")
    assert [v["valor_total"] for v in corpo["vendas"]] == ["8.50", "34.00", "55.80", "78.00"]
    assert corpo["vendas"][0] == {
        "id": vendas["feijao_nov"],
        "produto_id": _id_produto("Feijão 1 kg"),
        "produto_nome": "Feijão 1 kg",
        "categoria": "Alimentos",
        "quantidade": 1,
        "valor_unitario": "8.50",
        "valor_total": "8.50",
        "data_venda": "2026-11-05",
    }


def test_busca_sem_filtros_padrao_igual_a_listagem(cliente_http, cabecalho, vendas):
    corpo = _buscar(cliente_http, cabecalho).get_json()
    listagem = cliente_http.get("/api/web/records", headers=cabecalho).get_json()
    assert corpo.pop("filtros") == {"produto_id": None, "categoria": None, "data_inicio": None, "data_fim": None}
    assert corpo == listagem


def test_busca_paginada(cliente_http, cabecalho, vendas):
    primeira = _buscar(cliente_http, cabecalho, "data_inicio=2026-10-01&ordenar=data_venda&direcao=desc").get_json()
    segunda = _buscar(cliente_http, cabecalho, "data_inicio=2026-10-01&ordenar=data_venda&direcao=desc&pagina=2").get_json()
    assert primeira["total"] == 8 and primeira["total_paginas"] == 2
    assert [v["data_venda"] for v in primeira["vendas"]] == [
        "2026-11-10", "2026-11-05", "2026-11-02", "2026-10-25", "2026-10-20",
    ]
    assert [v["data_venda"] for v in segunda["vendas"]] == ["2026-10-15", "2026-10-12", "2026-10-05"]


def test_pagina_alem_do_fim_da_busca_vem_vazia(cliente_http, cabecalho, vendas):
    corpo = _buscar(cliente_http, cabecalho, "categoria=Bebidas&pagina=2").get_json()
    assert corpo["vendas"] == [] and corpo["total"] == 3 and corpo["total_paginas"] == 1


def test_busca_sem_resultado(cliente_http, cabecalho, vendas):
    corpo = _buscar(cliente_http, cabecalho, "categoria=Limpeza").get_json()
    assert corpo["vendas"] == [] and corpo["total"] == 0 and corpo["total_paginas"] == 1


def test_filtros_vazios_sao_ignorados(cliente_http, cabecalho, vendas):
    corpo = _buscar(cliente_http, cabecalho, "produto_id=&categoria=&data_inicio=&data_fim=").get_json()
    assert corpo["total"] == 10
    assert corpo["filtros"] == {"produto_id": None, "categoria": None, "data_inicio": None, "data_fim": None}


def test_produto_inexistente_nao_e_erro(cliente_http, cabecalho, vendas):
    resposta = _buscar(cliente_http, cabecalho, f"produto_id={api.ID_MAXIMO}")
    assert resposta.status_code == 200 and resposta.get_json()["total"] == 0


def test_produto_com_zeros_a_esquerda(cliente_http, cabecalho, vendas):
    consulta = "produto_id=" + "0" * 5000 + str(_id_produto("Sabonete"))
    assert _buscar(cliente_http, cabecalho, consulta).get_json()["total"] == 2


def test_texto_do_filtro_nao_entra_no_sql(cliente_http, cabecalho, vendas):
    resposta = _buscar(cliente_http, cabecalho, "categoria=Bebidas'%20OR%20'1'='1")
    assert resposta.status_code == 200 and resposta.get_json()["total"] == 0
    assert _buscar(cliente_http, cabecalho).get_json()["total"] == 10


@pytest.mark.parametrize("categoria", ["bebidas", "Eletrônicos", "Outra"])
def test_categoria_fora_do_catalogo_nao_encontra_vendas(cliente_http, cabecalho, vendas, categoria):
    resposta = _buscar(cliente_http, cabecalho, f"categoria={categoria}")
    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["total"] == 0 and corpo["vendas"] == [] and corpo["filtros"]["categoria"] == categoria


# --- validação ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "consulta, campos",
    [
        ("produto_id=0", {"produto_id"}),
        ("produto_id=-1", {"produto_id"}),
        ("produto_id=abc", {"produto_id"}),
        ("produto_id=1.5", {"produto_id"}),
        ("produto_id=%EF%BC%91", {"produto_id"}),  # "１" (dígito de largura total)
        (f"produto_id={api.ID_MAXIMO + 1}", {"produto_id"}),
        ("produto_id=" + "9" * 5000, {"produto_id"}),
        ("categoria=Bebidas%00", {"categoria"}),
        ("data_inicio=2026-13-01", {"data_inicio"}),
        ("data_inicio=01/10/2026", {"data_inicio"}),
        ("data_fim=2026-02-30", {"data_fim"}),
        ("data_fim=amanha", {"data_fim"}),
        ("data_inicio=2026-10-31&data_fim=2026-10-01", {"data_fim"}),
        ("pagina=0", {"pagina"}),
        ("ordenar=senha_hash", {"ordenar"}),
        ("direcao=cima", {"direcao"}),
        ("produto_id=x&categoria=%00&data_inicio=z&pagina=0", {"produto_id", "categoria", "data_inicio", "pagina"}),
    ],
)
def test_parametros_invalidos_retornam_400_sem_consultar_o_banco(
    cliente_http, cabecalho, monkeypatch, capsys, consulta, campos
):
    monkeypatch.setattr(db, "buscar_vendas_web", _falhar_no_banco)
    capsys.readouterr()
    resposta = _buscar(cliente_http, cabecalho, consulta)
    assert resposta.status_code == 400
    corpo = resposta.get_json()
    assert corpo["erro"] == "Parâmetros inválidos." and set(corpo["campos"]) == campos
    eventos = [e for e in _eventos(capsys) if e["request_id"] == resposta.headers["X-Request-ID"]]
    assert [(e["layer"], e["level"]) for e in eventos if e["operation"] == "buscar_vendas"] == [("api", "WARN")]


def test_periodo_invertido_explica_o_erro(cliente_http, cabecalho):
    corpo = _buscar(cliente_http, cabecalho, "data_inicio=2026-10-31&data_fim=2026-10-01").get_json()
    assert corpo["campos"] == {"data_fim": "A data final não pode ser anterior à data inicial."}


def test_categoria_com_nul_e_recusada(cliente_http, cabecalho):
    corpo = _buscar(cliente_http, cabecalho, "categoria=Bebidas%00").get_json()
    assert corpo["campos"] == {"categoria": "Categoria inválida."}


# --- logs -----------------------------------------------------------------------------------------


def test_busca_correlaciona_logs_api_e_banco(cliente_http, cabecalho, vendas, capsys, token):
    capsys.readouterr()
    resposta = _buscar(cliente_http, cabecalho, "categoria=Bebidas&data_inicio=2026-10-01")
    saida = capsys.readouterr().out
    assert token not in saida
    request_id = resposta.headers["X-Request-ID"]
    eventos = [json.loads(linha) for linha in saida.strip().splitlines()]
    eventos = [e for e in eventos if e["request_id"] == request_id]
    assert {(e["layer"], e["operation"], e["status"], e["client"], e["level"]) for e in eventos} == {
        ("api", "buscar_vendas", "success", "web", "INFO"),
        ("db", "buscar_vendas", "success", "web", "INFO"),
    }
    for evento in eventos:
        assert "categoria, data_inicio" in evento["message"]
        assert set(evento) == {"timestamp", "level", "layer", "client", "operation", "status", "request_id", "message"}


def test_busca_erro_no_banco(cliente_http, cabecalho, monkeypatch, capsys):
    monkeypatch.setattr(db, "buscar_vendas_web", _falhar_no_banco)
    capsys.readouterr()
    resposta = _buscar(cliente_http, cabecalho, "categoria=Bebidas")
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}
    evento = _eventos(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["operation"]) == ("api", "ERROR", "buscar_vendas")


# --- camada de banco ------------------------------------------------------------------------------


def test_db_campos_busca_web():
    assert db.CAMPOS_BUSCA_WEB == ("produto_id", "categoria", "data_inicio", "data_fim")


def test_db_buscar_vendas_web_combina_filtros(vendas, capsys):
    filtros = {"categoria": "Alimentos", "data_inicio": date(2026, 10, 1), "data_fim": date(2026, 10, 31)}
    linhas, total = db.buscar_vendas_web(filtros, 1, 5, "id", "asc", "req-busca")
    assert total == 2 and [linha["id"] for linha in linhas] == [vendas["arroz_out"], vendas["feijao_out"]]
    evento = _eventos(capsys)[-1]
    assert (evento["layer"], evento["operation"], evento["request_id"]) == ("db", "buscar_vendas", "req-busca")
    assert "categoria, data_fim, data_inicio" in evento["message"]


def test_db_buscar_vendas_web_sem_filtros_pagina(vendas, capsys):
    linhas, total = db.buscar_vendas_web({}, 2, 3, "id", "desc", "req-busca")
    assert total == 10 and [linha["id"] for linha in linhas] == sorted(vendas.values(), reverse=True)[3:6]
    assert "sem filtros" in _eventos(capsys)[-1]["message"]


def test_db_buscar_vendas_web_erro(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.buscar_vendas_web({"categoria": "Bebidas"}, 1, 5, "id", "asc", "req-erro")
    evento = _eventos(capsys)[-1]
    assert (evento["level"], evento["operation"], evento["status"]) == ("ERROR", "buscar_vendas", "error")
