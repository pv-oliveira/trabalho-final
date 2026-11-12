"""Testes da listagem paginada, ordenação e exclusão de vendas pela Web (issue #3)."""

import json

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
    """Cada teste começa sem vendas, para os totais e a paginação serem previsíveis."""
    with db.obter_conexao() as conexao:
        conexao.execute("TRUNCATE venda RESTART IDENTITY")


@pytest.fixture
def cabecalho(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sete_vendas():
    """7 vendas (2 páginas). Retorna os ids na ordem de inserção."""
    dados = [
        ("Arroz 5 kg", 2, "27.90", "2026-10-01"),
        ("Sabonete", 10, "2.50", "2026-10-05"),
        ("Refrigerante 2 L", 1, "9.90", "2026-10-03"),
        ("Detergente 500 ml", 4, "2.99", "2026-10-07"),
        ("Feijão 1 kg", 3, "8.50", "2026-10-02"),
        ("Creme dental", 6, "5.90", "2026-10-06"),
        ("Suco de laranja 1 L", 5, "3.50", "2026-10-04"),
    ]
    return [_inserir_venda(*venda) for venda in dados]


# --- autenticação ---------------------------------------------------------------------------------


@pytest.mark.parametrize("metodo, rota", [("get", "/api/web/records"), ("delete", "/api/web/records/1")])
@pytest.mark.parametrize("cabecalho_auth", [None, "Bearer invalido", "Basic abc"])
def test_rotas_exigem_autenticacao(cliente_http, metodo, rota, cabecalho_auth):
    cabecalhos = {"Authorization": cabecalho_auth} if cabecalho_auth else {}
    resposta = getattr(cliente_http, metodo)(rota, headers=cabecalhos)
    assert resposta.status_code == 401


# --- listagem -------------------------------------------------------------------------------------


def test_listar_sem_vendas(cliente_http, cabecalho):
    resposta = cliente_http.get("/api/web/records", headers=cabecalho)
    assert resposta.status_code == 200
    assert resposta.get_json() == {
        "vendas": [],
        "pagina": 1,
        "itens_por_pagina": 5,
        "total": 0,
        "total_paginas": 1,
        "ordenar": "data_venda",
        "direcao": "desc",
    }


def test_listar_padrao_cinco_por_pagina_mais_recentes_primeiro(cliente_http, cabecalho, sete_vendas):
    corpo = cliente_http.get("/api/web/records", headers=cabecalho).get_json()
    assert corpo["total"] == 7 and corpo["total_paginas"] == 2 and corpo["itens_por_pagina"] == 5
    assert [v["data_venda"] for v in corpo["vendas"]] == [
        "2026-10-07",
        "2026-10-06",
        "2026-10-05",
        "2026-10-04",
        "2026-10-03",
    ]
    primeira = corpo["vendas"][0]
    assert primeira == {
        "id": sete_vendas[3],
        "produto_id": _id_produto("Detergente 500 ml"),
        "produto_nome": "Detergente 500 ml",
        "categoria": "Limpeza",
        "quantidade": 4,
        "valor_unitario": "2.99",
        "valor_total": "11.96",
        "data_venda": "2026-10-07",
    }


def test_segunda_pagina_traz_o_restante(cliente_http, cabecalho, sete_vendas):
    corpo = cliente_http.get("/api/web/records?pagina=2", headers=cabecalho).get_json()
    assert corpo["pagina"] == 2
    assert [v["data_venda"] for v in corpo["vendas"]] == ["2026-10-02", "2026-10-01"]


def test_pagina_alem_do_fim_vem_vazia(cliente_http, cabecalho, sete_vendas):
    corpo = cliente_http.get("/api/web/records?pagina=3", headers=cabecalho).get_json()
    assert corpo["vendas"] == [] and corpo["total"] == 7 and corpo["total_paginas"] == 2


@pytest.mark.parametrize(
    "ordenar, direcao, esperado",
    [
        ("produto_nome", "asc", ["Arroz 5 kg", "Creme dental", "Detergente 500 ml", "Feijão 1 kg", "Refrigerante 2 L"]),
        ("quantidade", "desc", [10, 6, 5, 4, 3]),
        ("valor_total", "asc", ["9.90", "11.96", "17.50", "25.00", "25.50"]),
        ("data_venda", "asc", ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]),
        ("categoria", "asc", ["Alimentos", "Alimentos", "Bebidas", "Bebidas", "Higiene"]),
    ],
)
def test_ordenacao_por_coluna(cliente_http, cabecalho, sete_vendas, ordenar, direcao, esperado):
    corpo = cliente_http.get(
        f"/api/web/records?ordenar={ordenar}&direcao={direcao}", headers=cabecalho
    ).get_json()
    assert corpo["ordenar"] == ordenar and corpo["direcao"] == direcao
    assert [v[ordenar] for v in corpo["vendas"]] == esperado


def test_pagina_com_zeros_a_esquerda_e_aceita(cliente_http, cabecalho, sete_vendas):
    corpo = cliente_http.get("/api/web/records?pagina=" + "0" * 5000 + "2", headers=cabecalho).get_json()
    assert corpo["pagina"] == 2 and len(corpo["vendas"]) == 2


def test_ordenacao_por_id_e_estavel_entre_paginas(cliente_http, cabecalho, sete_vendas):
    ids = []
    for pagina in (1, 2):
        corpo = cliente_http.get(f"/api/web/records?ordenar=id&direcao=asc&pagina={pagina}", headers=cabecalho).get_json()
        ids += [v["id"] for v in corpo["vendas"]]
    assert ids == sorted(sete_vendas)


def test_empate_e_desfeito_pelo_id(cliente_http, cabecalho):
    ids = [_inserir_venda("Sabonete", 1, "2.50", "2026-10-01") for _ in range(3)]
    corpo = cliente_http.get("/api/web/records?ordenar=data_venda&direcao=desc", headers=cabecalho).get_json()
    assert [v["id"] for v in corpo["vendas"]] == sorted(ids, reverse=True)


@pytest.mark.parametrize(
    "consulta, campo",
    [
        ("pagina=0", "pagina"),
        ("pagina=-1", "pagina"),
        ("pagina=abc", "pagina"),
        ("pagina=1.5", "pagina"),
        ("pagina=%C2%B2", "pagina"),  # "²" passa em str.isdigit(), mas não é página válida
        ("pagina=100001", "pagina"),
        ("pagina=" + "9" * 5000, "pagina"),  # acima do limite de dígitos do int(): não pode virar 500
        ("pagina=" + "1" * 5000, "pagina"),
        ("pagina=", "pagina"),
        ("ordenar=senha_hash", "ordenar"),
        ("ordenar=venda.id;DROP TABLE venda", "ordenar"),
        ("direcao=cima", "direcao"),
    ],
)
def test_parametros_invalidos(cliente_http, cabecalho, consulta, campo, capsys):
    capsys.readouterr()
    resposta = cliente_http.get(f"/api/web/records?{consulta}", headers=cabecalho)
    assert resposta.status_code == 400
    corpo = resposta.get_json()
    assert corpo["erro"] == "Parâmetros inválidos." and set(corpo["campos"]) == {campo}
    eventos = [e for e in _eventos(capsys) if e["request_id"] == resposta.headers["X-Request-ID"]]
    assert [e["layer"] for e in eventos if e["operation"] == "listar_vendas"] == ["api"]
    assert eventos[-1]["level"] == "WARN"


def test_listagem_correlaciona_logs_api_e_banco(cliente_http, cabecalho, sete_vendas, capsys):
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/records", headers=cabecalho)
    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in _eventos(capsys) if e["request_id"] == request_id]
    assert {(e["layer"], e["operation"], e["status"], e["client"]) for e in eventos} == {
        ("api", "listar_vendas", "success", "web"),
        ("db", "listar_vendas", "success", "web"),
    }


def test_listagem_erro_no_banco(cliente_http, cabecalho, monkeypatch, capsys):
    monkeypatch.setattr(db, "listar_vendas_web", _falhar_no_banco)
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/records", headers=cabecalho)
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}
    evento = _eventos(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["operation"] == "listar_vendas"


# --- exclusão -------------------------------------------------------------------------------------


def test_excluir_remove_do_banco_e_da_listagem(cliente_http, cabecalho, sete_vendas, capsys):
    alvo = sete_vendas[0]
    capsys.readouterr()
    resposta = cliente_http.delete(f"/api/web/records/{alvo}", headers=cabecalho)
    assert resposta.status_code == 200
    assert resposta.get_json() == {"id": alvo, "mensagem": "Venda excluída."}

    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in _eventos(capsys) if e["request_id"] == request_id]
    assert {(e["layer"], e["operation"], e["status"]) for e in eventos} == {
        ("api", "excluir_venda", "success"),
        ("db", "excluir_venda", "success"),
    }

    assert cliente_http.get(f"/api/web/records/{alvo}", headers=cabecalho).status_code == 404
    corpo = cliente_http.get("/api/web/records?pagina=2", headers=cabecalho).get_json()
    assert corpo["total"] == 6 and alvo not in [v["id"] for v in corpo["vendas"]]


def test_excluir_inexistente_retorna_404_com_logs(cliente_http, cabecalho, capsys):
    capsys.readouterr()
    resposta = cliente_http.delete("/api/web/records/999", headers=cabecalho)
    assert resposta.status_code == 404 and resposta.get_json() == {"erro": "Venda não encontrada."}
    eventos = [e for e in _eventos(capsys) if e["request_id"] == resposta.headers["X-Request-ID"]]
    assert {(e["layer"], e["level"]) for e in eventos if e["operation"] == "excluir_venda"} == {
        ("api", "WARN"),
        ("db", "WARN"),
    }


def test_excluir_id_fora_do_intervalo_nao_consulta_o_banco(cliente_http, cabecalho, monkeypatch):
    monkeypatch.setattr(db, "excluir_venda_web", _falhar_no_banco)
    resposta = cliente_http.delete(f"/api/web/records/{api.ID_MAXIMO + 1}", headers=cabecalho)
    assert resposta.status_code == 404


@pytest.mark.parametrize("id_venda", ["-1", "0", "abc", "1.5", "１"])
def test_excluir_id_invalido_retorna_400_sem_consultar_o_banco(cliente_http, cabecalho, monkeypatch, capsys, id_venda):
    monkeypatch.setattr(db, "excluir_venda_web", _falhar_no_banco)
    capsys.readouterr()
    resposta = cliente_http.delete(f"/api/web/records/{id_venda}", headers=cabecalho)
    assert resposta.status_code == 400 and resposta.get_json() == {"erro": "Número da venda inválido."}
    evento = _eventos(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["operation"]) == ("api", "WARN", "excluir_venda")


def test_excluir_id_invalido_sem_token_responde_401(cliente_http):
    assert cliente_http.delete("/api/web/records/abc").status_code == 401


def test_excluir_erro_no_banco(cliente_http, cabecalho, monkeypatch):
    monkeypatch.setattr(db, "excluir_venda_web", _falhar_no_banco)
    resposta = cliente_http.delete("/api/web/records/1", headers=cabecalho)
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}


def test_excluir_duas_vezes(cliente_http, cabecalho, sete_vendas):
    rota = f"/api/web/records/{sete_vendas[1]}"
    assert cliente_http.delete(rota, headers=cabecalho).status_code == 200
    assert cliente_http.delete(rota, headers=cabecalho).status_code == 404


# --- camada de banco ------------------------------------------------------------------------------


def test_db_listar_vendas_web_retorna_pagina_e_total(sete_vendas, capsys):
    linhas, total = db.listar_vendas_web(2, 3, "id", "asc", "req-lista")
    assert total == 7 and [linha["id"] for linha in linhas] == sorted(sete_vendas)[3:6]
    evento = _eventos(capsys)[-1]
    assert evento["layer"] == "db" and evento["operation"] == "listar_vendas" and evento["request_id"] == "req-lista"


def test_db_listar_vendas_web_erro(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.listar_vendas_web(1, 5, "id", "asc", "req-erro")
    evento = _eventos(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["operation"] == "listar_vendas"


def test_db_excluir_venda_web(sete_vendas):
    assert db.excluir_venda_web(sete_vendas[0], "req-x") is True
    assert db.excluir_venda_web(sete_vendas[0], "req-y") is False


def test_db_excluir_venda_web_erro(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.excluir_venda_web(1, "req-erro")
    evento = _eventos(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["operation"] == "excluir_venda"
