import json

import psycopg
import pytest
from werkzeug.exceptions import ServiceUnavailable
from werkzeug.security import check_password_hash

import api
import db
from conftest import NOME_TESTE, SENHA_TESTE, USUARIO_TESTE, eventos_do_log


def test_saude_e_publica(cliente_http, capsys):
    resposta = cliente_http.get("/api/web/health")
    assert resposta.status_code == 200
    assert resposta.get_json() == {"status": "ok"}
    assert resposta.headers["X-Request-ID"].startswith("req-")
    evento = eventos_do_log(capsys)[-1]
    assert evento["layer"] == "api" and evento["operation"] == "health"


def test_login_sucesso_correlaciona_logs_api_e_banco(cliente_http, capsys):
    capsys.readouterr()
    resposta = cliente_http.post("/api/web/login", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["usuario"] == USUARIO_TESTE and corpo["nome"] == NOME_TESTE and corpo["token"]

    eventos = eventos_do_log(capsys)
    request_id = resposta.headers["X-Request-ID"]
    camadas = {e["layer"] for e in eventos if e["request_id"] == request_id}
    assert camadas == {"api", "db"}
    bruto = json.dumps(eventos)
    assert SENHA_TESTE not in bruto and corpo["token"] not in bruto


def test_login_senha_errada(cliente_http):
    resposta = cliente_http.post("/api/web/login", json={"usuario": USUARIO_TESTE, "senha": "errada"})
    assert resposta.status_code == 401


def test_login_usuario_inexistente(cliente_http):
    resposta = cliente_http.post("/api/web/login", json={"usuario": "ninguem", "senha": "x"})
    assert resposta.status_code == 401


def test_login_campos_ausentes(cliente_http):
    assert cliente_http.post("/api/web/login", json={"usuario": USUARIO_TESTE}).status_code == 400
    assert cliente_http.post("/api/web/login", data="não é json").status_code == 400
    assert cliente_http.post("/api/web/login", json={"usuario": 1, "senha": 2}).status_code == 400
    for corpo in ([1], "x", 5, None):
        assert cliente_http.post("/api/web/login", json=corpo).status_code == 400


def test_login_usuario_inexistente_tambem_verifica_hash(cliente_http, monkeypatch):
    verificados = []
    original = api.check_password_hash

    def espiar(senha_hash, senha):
        verificados.append(senha_hash)
        return original(senha_hash, senha)

    monkeypatch.setattr(api, "check_password_hash", espiar)
    resposta = cliente_http.post("/api/web/login", json={"usuario": "ninguem", "senha": "x"})
    assert resposta.status_code == 401
    assert verificados == [api.HASH_FICTICIO]


def test_erro_inesperado_responde_json_e_registra_log(cliente_http, monkeypatch, capsys):
    def falhar(*_args):
        raise KeyError("URL_BANCO")

    monkeypatch.setattr(db, "buscar_usuario", falhar)
    capsys.readouterr()
    resposta = cliente_http.post("/api/web/login", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    assert resposta.status_code == 500
    assert resposta.get_json() == {"erro": "Erro interno"}
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["layer"] == "api" and evento["operation"] == "web_login"


@pytest.mark.parametrize(("rota", "cliente"), [("/api/web/nao-existe", "web"), ("/api/cli/nao-existe", "cli")])
def test_rota_inexistente_em_api_responde_404_em_json_com_log(cliente_http, capsys, rota, cliente):
    capsys.readouterr()
    resposta = cliente_http.get(rota)
    assert resposta.status_code == 404
    assert resposta.get_json() == {"erro": "Rota não encontrada"}
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "WARN" and evento["status"] == "error" and evento["layer"] == "api"
    assert evento["client"] == cliente and evento["operation"] == "erro_http"
    assert evento["request_id"] == resposta.headers["X-Request-ID"] and rota in evento["message"]


@pytest.mark.parametrize(("rota", "cliente"), [("/api/web/health", "web"), ("/api/cli/health", "cli")])
def test_metodo_nao_permitido_em_api_responde_405_em_json_com_log(cliente_http, capsys, rota, cliente):
    capsys.readouterr()
    resposta = cliente_http.delete(rota)
    assert resposta.status_code == 405
    assert resposta.get_json() == {"erro": "Método não permitido"}
    assert "GET" in resposta.headers["Allow"]
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "WARN" and evento["client"] == cliente and "DELETE" in evento["message"]


def test_erro_http_fora_de_api_mantem_a_resposta_padrao(cliente_http):
    resposta = cliente_http.get("/nao-existe")
    assert resposta.status_code == 404
    assert resposta.get_json(silent=True) is None


def test_erro_http_5xx_em_api_registra_error(cliente_http, capsys, monkeypatch):
    def indisponivel():
        raise ServiceUnavailable()

    monkeypatch.setitem(api.app.view_functions, "web_saude", indisponivel)
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/health")
    assert resposta.status_code == 503
    assert resposta.get_json() == {"erro": "Erro interno"}
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["operation"] == "web_saude"


def test_login_erro_no_banco(cliente_http, monkeypatch):
    def falhar(*_args):
        raise psycopg.OperationalError("fora do ar")

    monkeypatch.setattr(db, "buscar_usuario", falhar)
    resposta = cliente_http.post("/api/web/login", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    assert resposta.status_code == 500


def test_sessao_com_token_valido(cliente_http, token):
    resposta = cliente_http.get("/api/web/sessao", headers={"Authorization": f"Bearer {token}"})
    assert resposta.status_code == 200
    assert resposta.get_json() == {"usuario": USUARIO_TESTE, "nome": NOME_TESTE}


def test_sessao_sem_token(cliente_http):
    assert cliente_http.get("/api/web/sessao").status_code == 401


def test_sessao_com_token_invalido(cliente_http):
    resposta = cliente_http.get("/api/web/sessao", headers={"Authorization": "Bearer invalido"})
    assert resposta.status_code == 401


def test_inicializar_cria_usuario_inicial(monkeypatch):
    monkeypatch.setenv("ADMIN_USUARIO", "admin-inicial")
    monkeypatch.setenv("ADMIN_NOME", "Admin Inicial")
    monkeypatch.setenv("ADMIN_SENHA", "segredo")
    api.inicializar()
    with api.app.test_request_context():
        registro = db.buscar_usuario("admin-inicial", "req-inicial", "web")
    assert registro["nome"] == "Admin Inicial"
    assert registro["senha_hash"] != "segredo"
    assert check_password_hash(registro["senha_hash"], "segredo")


def test_criar_app_inicializa_e_devolve_app(monkeypatch):
    chamadas = []
    monkeypatch.setattr(api, "inicializar", lambda: chamadas.append("inicializar"))
    assert api.criar_app() is api.app
    assert chamadas == ["inicializar"]
