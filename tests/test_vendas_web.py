"""Testes do cadastro e edição de vendas pela Web (issue #2)."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
import pytest

import api
import db
from conftest import eventos_do_log

HOJE = api.hoje()


def _conexao_quebrada():
    raise psycopg.OperationalError("banco fora do ar")


def _falhar_no_banco(*_args):
    raise psycopg.OperationalError("banco fora do ar")


def _id_produto(nome="Arroz 5 kg"):
    with db.obter_conexao() as conexao:
        return conexao.execute("SELECT id FROM produto WHERE nome = %s", (nome,)).fetchone()["id"]


def _venda_valida(**alteracoes):
    venda = {"produto_id": _id_produto(), "quantidade": 3, "valor_unitario": "25.50", "data_venda": "2026-10-15"}
    venda.update(alteracoes)
    return venda


@pytest.fixture
def cabecalho(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def venda_criada(cliente_http, cabecalho):
    return cliente_http.post("/api/web/records", json=_venda_valida(), headers=cabecalho).get_json()


# --- autenticação ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metodo, rota",
    [
        ("get", "/api/web/produtos"),
        ("get", "/api/web/records/1"),
        ("post", "/api/web/records"),
        ("put", "/api/web/records/1"),
    ],
)
@pytest.mark.parametrize("cabecalho_auth", [None, "Bearer invalido", "Basic abc"])
def test_rotas_de_venda_exigem_autenticacao(cliente_http, metodo, rota, cabecalho_auth):
    cabecalhos = {"Authorization": cabecalho_auth} if cabecalho_auth else {}
    resposta = getattr(cliente_http, metodo)(rota, json=_venda_valida(), headers=cabecalhos)
    assert resposta.status_code == 401


# --- produtos -------------------------------------------------------------------------------------


def test_listar_produtos(cliente_http, cabecalho, capsys):
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/produtos", headers=cabecalho)
    assert resposta.status_code == 200
    produtos = resposta.get_json()["produtos"]
    assert len(produtos) == len(db.PRODUTOS_INICIAIS)
    arroz = next(p for p in produtos if p["nome"] == "Arroz 5 kg")
    assert arroz == {"id": _id_produto(), "nome": "Arroz 5 kg", "categoria": "Alimentos", "preco": "27.90"}
    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]
    assert {e["layer"] for e in eventos} == {"api", "db"}
    assert {e["operation"] for e in eventos} == {"listar_produtos"}


def test_listar_produtos_erro_no_banco(cliente_http, cabecalho, monkeypatch):
    monkeypatch.setattr(db, "listar_produtos_web", _falhar_no_banco)
    assert cliente_http.get("/api/web/produtos", headers=cabecalho).status_code == 500


# --- criação --------------------------------------------------------------------------------------


def test_criar_venda_persiste_e_correlaciona_logs(cliente_http, cabecalho, capsys, token):
    capsys.readouterr()
    resposta = cliente_http.post("/api/web/records", json=_venda_valida(), headers=cabecalho)
    assert resposta.status_code == 201
    venda = resposta.get_json()
    assert venda["produto_nome"] == "Arroz 5 kg" and venda["categoria"] == "Alimentos"
    assert venda["quantidade"] == 3 and venda["valor_unitario"] == "25.50"
    assert venda["valor_total"] == "76.50" and venda["data_venda"] == "2026-10-15"

    with db.obter_conexao() as conexao:
        gravada = conexao.execute("SELECT quantidade, valor_total FROM venda WHERE id = %s", (venda["id"],)).fetchone()
    assert gravada["quantidade"] == 3 and str(gravada["valor_total"]) == "76.50"

    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]
    assert {e["layer"] for e in eventos} == {"api", "db"}
    assert all(e["client"] == "web" and e["operation"] == "criar_venda" for e in eventos)
    assert token not in json.dumps(eventos)


def test_criar_venda_aceita_numeros_e_data_de_hoje(cliente_http, cabecalho):
    corpo = _venda_valida(valor_unitario=10, data_venda=HOJE.isoformat())
    resposta = cliente_http.post("/api/web/records", json=corpo, headers=cabecalho)
    assert resposta.status_code == 201
    assert resposta.get_json()["valor_unitario"] == "10.00"
    corpo = _venda_valida(valor_unitario=7.9)
    assert cliente_http.post("/api/web/records", json=corpo, headers=cabecalho).get_json()["valor_unitario"] == "7.90"


@pytest.mark.parametrize("corpo", [None, [1], "x", 5])
def test_criar_venda_corpo_nao_objeto(cliente_http, cabecalho, corpo):
    resposta = cliente_http.post("/api/web/records", json=corpo, headers=cabecalho)
    assert resposta.status_code == 400


def test_criar_venda_sem_campos_lista_todos_os_erros(cliente_http, cabecalho):
    resposta = cliente_http.post("/api/web/records", json={}, headers=cabecalho)
    assert resposta.status_code == 422
    assert set(resposta.get_json()["campos"]) == {"produto_id", "quantidade", "valor_unitario", "data_venda"}


AMANHA = (HOJE + timedelta(days=1)).isoformat()


@pytest.mark.parametrize(
    "campo, valor",
    [
        ("produto_id", ""),
        ("produto_id", "1"),
        ("produto_id", True),
        ("produto_id", 0),
        ("produto_id", api.ID_MAXIMO + 1),
        ("quantidade", None),
        ("quantidade", 0),
        ("quantidade", -1),
        ("quantidade", 1.5),
        ("quantidade", "2"),
        ("quantidade", False),
        ("quantidade", api.QUANTIDADE_MAXIMA + 1),
        ("valor_unitario", ""),
        ("valor_unitario", 0),
        ("valor_unitario", "-1"),
        ("valor_unitario", "abc"),
        ("valor_unitario", "1.234"),
        ("valor_unitario", "100000.01"),
        ("valor_unitario", "NaN"),
        ("valor_unitario", "Infinity"),
        ("valor_unitario", True),
        ("valor_unitario", [1]),
        ("data_venda", None),
        ("data_venda", "15/10/2026"),
        ("data_venda", "20261015"),
        ("data_venda", "2026-02-30"),
        ("data_venda", 20261015),
        ("data_venda", AMANHA),
    ],
)
def test_criar_venda_rejeita_campo_invalido(cliente_http, cabecalho, campo, valor):
    resposta = cliente_http.post("/api/web/records", json=_venda_valida(**{campo: valor}), headers=cabecalho)
    assert resposta.status_code == 422
    assert set(resposta.get_json()["campos"]) == {campo}


def test_criar_venda_produto_inexistente(cliente_http, cabecalho, capsys):
    capsys.readouterr()
    resposta = cliente_http.post("/api/web/records", json=_venda_valida(produto_id=999999), headers=cabecalho)
    assert resposta.status_code == 422
    assert set(resposta.get_json()["campos"]) == {"produto_id"}
    request_id = resposta.headers["X-Request-ID"]
    camadas = {e["layer"] for e in eventos_do_log(capsys) if e["request_id"] == request_id}
    assert camadas == {"api", "db"}


def test_criar_venda_erro_no_banco(cliente_http, cabecalho, monkeypatch):
    monkeypatch.setattr(db, "criar_venda_web", _falhar_no_banco)
    assert cliente_http.post("/api/web/records", json=_venda_valida(), headers=cabecalho).status_code == 500


# --- consulta -------------------------------------------------------------------------------------


def test_consultar_venda(cliente_http, cabecalho, venda_criada):
    resposta = cliente_http.get(f"/api/web/records/{venda_criada['id']}", headers=cabecalho)
    assert resposta.status_code == 200
    assert resposta.get_json() == venda_criada


@pytest.mark.parametrize("id_venda", [999999, "000999999", api.ID_MAXIMO + 1, "9" * 20, "9" * 5000, "0" * 5000 + "9" * 11])
def test_consultar_venda_inexistente(cliente_http, cabecalho, id_venda):
    resposta = cliente_http.get(f"/api/web/records/{id_venda}", headers=cabecalho)
    assert resposta.status_code == 404
    assert resposta.get_json() == {"erro": "Venda não encontrada."}


def test_consultar_venda_inexistente_registra_warn_nas_duas_camadas(cliente_http, cabecalho, capsys):
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/records/999999", headers=cabecalho)
    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]
    assert {e["layer"] for e in eventos} == {"api", "db"}
    assert all(e["level"] == "WARN" and e["status"] == "error" for e in eventos)


@pytest.mark.parametrize("metodo", ["get", "put"])
@pytest.mark.parametrize("id_venda", ["-1", "0", "000", "abc", "+1", "1.5", "１"])
def test_id_de_venda_invalido(cliente_http, cabecalho, capsys, metodo, id_venda):
    capsys.readouterr()
    resposta = getattr(cliente_http, metodo)(f"/api/web/records/{id_venda}", json=_venda_valida(), headers=cabecalho)
    assert resposta.status_code == 400
    assert resposta.get_json() == {"erro": "Número da venda inválido."}
    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]
    assert [(e["layer"], e["level"]) for e in eventos] == [("api", "WARN")]


def test_id_de_venda_invalido_sem_token_responde_401(cliente_http):
    assert cliente_http.get("/api/web/records/-1").status_code == 401
    assert cliente_http.put("/api/web/records/abc", json=_venda_valida()).status_code == 401


def test_consultar_venda_erro_no_banco(cliente_http, cabecalho, monkeypatch):
    monkeypatch.setattr(db, "consultar_venda_web", _falhar_no_banco)
    assert cliente_http.get("/api/web/records/1", headers=cabecalho).status_code == 500


# --- edição ---------------------------------------------------------------------------------------


def test_editar_venda_atualiza_e_correlaciona_logs(cliente_http, cabecalho, venda_criada, capsys):
    corpo = _venda_valida(produto_id=_id_produto("Sabonete"), quantidade=10, valor_unitario="2.00", data_venda="2026-09-01")
    capsys.readouterr()
    resposta = cliente_http.put(f"/api/web/records/{venda_criada['id']}", json=corpo, headers=cabecalho)
    assert resposta.status_code == 200
    venda = resposta.get_json()
    assert venda["id"] == venda_criada["id"]
    assert venda["produto_nome"] == "Sabonete" and venda["categoria"] == "Higiene"
    assert venda["valor_total"] == "20.00" and venda["data_venda"] == "2026-09-01"

    consulta = cliente_http.get(f"/api/web/records/{venda_criada['id']}", headers=cabecalho).get_json()
    assert consulta == venda

    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]
    assert {e["layer"] for e in eventos} == {"api", "db"}
    assert all(e["operation"] == "editar_venda" for e in eventos)


@pytest.mark.parametrize("id_venda", [999999, api.ID_MAXIMO + 1])
def test_editar_venda_inexistente(cliente_http, cabecalho, id_venda):
    resposta = cliente_http.put(f"/api/web/records/{id_venda}", json=_venda_valida(), headers=cabecalho)
    assert resposta.status_code == 404


def test_editar_venda_dados_invalidos(cliente_http, cabecalho, venda_criada):
    url = f"/api/web/records/{venda_criada['id']}"
    assert cliente_http.put(url, data="não é json", headers=cabecalho).status_code == 400
    resposta = cliente_http.put(url, json=_venda_valida(quantidade=0), headers=cabecalho)
    assert resposta.status_code == 422


def test_editar_venda_produto_inexistente(cliente_http, cabecalho, venda_criada):
    url = f"/api/web/records/{venda_criada['id']}"
    resposta = cliente_http.put(url, json=_venda_valida(produto_id=999999), headers=cabecalho)
    assert resposta.status_code == 422


def test_editar_venda_erro_no_banco(cliente_http, cabecalho, monkeypatch):
    monkeypatch.setattr(db, "editar_venda_web", _falhar_no_banco)
    assert cliente_http.put("/api/web/records/1", json=_venda_valida(), headers=cabecalho).status_code == 500


# --- fuso horário ---------------------------------------------------------------------------------


@pytest.mark.parametrize("fuso", ["Etc/GMT-14", "Etc/GMT+12", "America/Fortaleza"])
def test_data_futura_usa_fuso_configurado(cliente_http, cabecalho, monkeypatch, fuso):
    monkeypatch.setenv("FUSO_HORARIO", fuso)
    hoje_no_fuso = datetime.now(ZoneInfo(fuso)).date()
    corpo = _venda_valida(data_venda=hoje_no_fuso.isoformat())
    assert cliente_http.post("/api/web/records", json=corpo, headers=cabecalho).status_code == 201
    corpo = _venda_valida(data_venda=(hoje_no_fuso + timedelta(days=1)).isoformat())
    resposta = cliente_http.post("/api/web/records", json=corpo, headers=cabecalho)
    assert resposta.status_code == 422 and set(resposta.get_json()["campos"]) == {"data_venda"}


@pytest.mark.parametrize("valor", [None, ""])
def test_fuso_padrao_e_fortaleza(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("FUSO_HORARIO", raising=False)
    else:
        monkeypatch.setenv("FUSO_HORARIO", valor)
    assert api.fuso_horario() == ZoneInfo("America/Fortaleza")


def test_fuso_invalido_impede_inicializacao(monkeypatch):
    monkeypatch.setenv("FUSO_HORARIO", "Lugar/Inexistente")
    with pytest.raises(ZoneInfoNotFoundError):
        api.inicializar()


# --- camada de banco ------------------------------------------------------------------------------


def _venda_db(**alteracoes):
    venda = {"produto_id": _id_produto(), "quantidade": 2, "valor_unitario": "5.00", "data_venda": date(2026, 10, 1)}
    venda.update(alteracoes)
    return venda


def test_db_criar_consultar_e_editar_venda(capsys):
    criada = db.criar_venda_web(_venda_db(), "req-v1")
    assert criada["produto_nome"] == "Arroz 5 kg" and str(criada["valor_total"]) == "10.00"
    assert db.consultar_venda_web(criada["id"], "req-v2") == criada
    editada = db.editar_venda_web(criada["id"], _venda_db(quantidade=4), "req-v3")
    assert str(editada["valor_total"]) == "20.00"
    eventos = eventos_do_log(capsys)
    assert [e["operation"] for e in eventos[-3:]] == ["criar_venda", "consultar_venda", "editar_venda"]
    assert all(e["layer"] == "db" and e["client"] == "web" and e["status"] == "success" for e in eventos[-3:])


def test_db_venda_inexistente(capsys):
    assert db.consultar_venda_web(999999, "req-v4") is None
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "WARN" and evento["status"] == "error" and evento["request_id"] == "req-v4"
    assert db.editar_venda_web(999999, _venda_db(), "req-v5") is None
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "WARN" and evento["status"] == "error" and evento["request_id"] == "req-v5"


def test_db_produto_inexistente(capsys):
    with pytest.raises(db.ProdutoInexistente):
        db.criar_venda_web(_venda_db(produto_id=999999), "req-v6")
    criada = db.criar_venda_web(_venda_db(), "req-v7")
    with pytest.raises(db.ProdutoInexistente):
        db.editar_venda_web(criada["id"], _venda_db(produto_id=999999), "req-v8")
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "WARN" and evento["operation"] == "editar_venda"


@pytest.mark.parametrize(
    "funcao, argumentos, operacao",
    [
        (db.listar_produtos_web, (), "listar_produtos"),
        (db.consultar_venda_web, (1,), "consultar_venda"),
        (db.criar_venda_web, (None,), "criar_venda"),
        (db.editar_venda_web, (1, None), "editar_venda"),
    ],
)
def test_db_erro_no_banco(monkeypatch, capsys, funcao, argumentos, operacao):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        funcao(*argumentos, "req-v9")
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["operation"] == operacao and evento["request_id"] == "req-v9"
