"""Testes da #7: rotas /api/cli/* (health, token, sessao) e a CLI (cli.py)."""

import http.client
import http.server
import io
import json
import runpy
import threading
import time
import urllib.error
from pathlib import Path

import psycopg
import pytest
from werkzeug.serving import make_server

import api
import cli
import db
from conftest import NOME_TESTE, SENHA_TESTE, USUARIO_TESTE, eventos_do_log


def _token_cli(cliente_http):
    resposta = cliente_http.post("/api/cli/token", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    return resposta.get_json()["token"]


# --- API: rotas /api/cli/* ------------------------------------------------------------


def test_cli_saude_e_publica_e_registra_client_cli(cliente_http, capsys):
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/health")
    assert resposta.status_code == 200
    assert resposta.get_json() == {"status": "ok"}
    evento = eventos_do_log(capsys)[-1]
    assert evento["layer"] == "api" and evento["client"] == "cli" and evento["operation"] == "health"
    assert evento["request_id"] == resposta.headers["X-Request-ID"]


def test_cli_token_emitido_correlaciona_logs_sem_expor_segredos(cliente_http, capsys):
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/token", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["usuario"] == USUARIO_TESTE and corpo["nome"] == NOME_TESTE and corpo["token"]
    assert corpo["validade_segundos"] == api.VALIDADE_TOKEN_CLI

    eventos = eventos_do_log(capsys)
    request_id = resposta.headers["X-Request-ID"]
    da_requisicao = [e for e in eventos if e["request_id"] == request_id]
    assert {e["layer"] for e in da_requisicao} == {"api", "db"}
    assert {e["client"] for e in da_requisicao} == {"cli"}
    assert [e["operation"] for e in da_requisicao if e["layer"] == "db"] == ["consultar_usuario_cli"]
    final = da_requisicao[-1]
    assert final["operation"] == "token" and final["status"] == "success" and final["level"] == "INFO"
    bruto = json.dumps(eventos)
    assert SENHA_TESTE not in bruto and corpo["token"] not in bruto


@pytest.mark.parametrize(
    ("usuario", "senha"),
    [(USUARIO_TESTE, "errada"), ("ninguem", SENHA_TESTE)],
)
def test_cli_token_credenciais_invalidas_retorna_401_e_registra_falha(cliente_http, capsys, usuario, senha):
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/token", json={"usuario": usuario, "senha": senha})
    assert resposta.status_code == 401
    assert resposta.get_json() == {"erro": "Usuário ou senha inválidos"}
    evento = eventos_do_log(capsys)[-1]
    assert evento["client"] == "cli" and evento["operation"] == "token"
    assert evento["level"] == "WARN" and evento["status"] == "error"
    assert senha not in json.dumps(evento)


def test_cli_token_usuario_inexistente_tambem_verifica_hash(cliente_http, monkeypatch):
    verificados = []
    original = api.check_password_hash

    def espiar(senha_hash, senha):
        verificados.append(senha_hash)
        return original(senha_hash, senha)

    monkeypatch.setattr(api, "check_password_hash", espiar)
    assert cliente_http.post("/api/cli/token", json={"usuario": "ninguem", "senha": "x"}).status_code == 401
    assert verificados == [api.HASH_FICTICIO]


def test_cli_token_campos_ausentes(cliente_http):
    for corpo in ({"usuario": USUARIO_TESTE}, {"usuario": 1, "senha": 2}, {"usuario": "", "senha": ""}, [1], None):
        assert cliente_http.post("/api/cli/token", json=corpo).status_code == 400
    assert cliente_http.post("/api/cli/token", data="não é json").status_code == 400


def test_cli_token_erro_no_banco(cliente_http, monkeypatch, capsys):
    def falhar(*_args):
        raise psycopg.OperationalError("fora do ar")

    monkeypatch.setattr(db, "buscar_usuario_cli", falhar)
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/token", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    assert resposta.status_code == 500
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["client"] == "cli" and evento["operation"] == "token"


def test_cli_sessao_com_token_valido(cliente_http, capsys):
    token = _token_cli(cliente_http)
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/sessao", headers={"Authorization": f"Bearer {token}"})
    assert resposta.status_code == 200
    assert resposta.get_json() == {"usuario": USUARIO_TESTE, "nome": NOME_TESTE}
    evento = eventos_do_log(capsys)[-1]
    assert evento["client"] == "cli" and evento["operation"] == "sessao" and evento["status"] == "success"
    assert token not in json.dumps(evento)


@pytest.mark.parametrize(
    ("cabecalhos", "mensagem"),
    [
        ({}, "Requisição sem token"),
        ({"Authorization": "Basic abc"}, "Requisição sem token"),
        ({"Authorization": "Bearer invalido"}, "Token inválido ou expirado"),
    ],
)
def test_cli_sessao_sem_token_ou_invalido_retorna_401(cliente_http, capsys, cabecalhos, mensagem):
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/sessao", headers=cabecalhos)
    assert resposta.status_code == 401
    assert resposta.get_json() == {"erro": "Não autenticado"}
    evento = eventos_do_log(capsys)[-1]
    assert evento["client"] == "cli" and evento["operation"] == "sessao"
    assert evento["level"] == "WARN" and evento["message"] == mensagem
    assert "invalido" not in json.dumps(evento)


def test_cli_sessao_token_expirado_retorna_401(cliente_http, monkeypatch):
    emitido_em = time.time() - api.VALIDADE_TOKEN_CLI - 60
    with monkeypatch.context() as m:
        m.setattr("itsdangerous.timed.time.time", lambda: emitido_em)
        token = api.serializador_cli().dumps({"usuario": USUARIO_TESTE, "nome": NOME_TESTE})
    resposta = cliente_http.get("/api/cli/sessao", headers={"Authorization": f"Bearer {token}"})
    assert resposta.status_code == 401


def test_login_da_web_informa_a_validade_do_token(cliente_http):
    resposta = cliente_http.post("/api/web/login", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    assert resposta.get_json()["validade_segundos"] == api.VALIDADE_TOKEN_WEB


def test_token_da_web_nao_vale_na_cli_e_vice_versa(cliente_http, token):
    assert cliente_http.get("/api/cli/sessao", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    token_cli = _token_cli(cliente_http)
    assert cliente_http.get("/api/web/sessao", headers={"Authorization": f"Bearer {token_cli}"}).status_code == 401


def test_erro_inesperado_em_rota_cli_registra_client_cli(cliente_http, monkeypatch, capsys):
    def falhar(*_args):
        raise KeyError("URL_BANCO")

    monkeypatch.setattr(db, "buscar_usuario_cli", falhar)
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/token", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    assert resposta.status_code == 500
    assert resposta.get_json() == {"erro": "Erro interno"}
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["client"] == "cli" and evento["operation"] == "cli_token"


# --- CLI (cli.py) contra a API real, num servidor HTTP local --------------------------


@pytest.fixture
def servidor_api(monkeypatch):
    """Sobe a API num servidor HTTP de verdade (porta livre) e aponta URL_API da CLI para ele."""
    servidor = make_server("127.0.0.1", 0, api.app, threaded=True)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("URL_API", f"http://127.0.0.1:{servidor.server_port}/")
    monkeypatch.delenv("TOKEN_CLI", raising=False)
    monkeypatch.delenv("SENHA_CLI", raising=False)
    yield
    servidor.shutdown()
    thread.join()


def _token_pela_cli(capsys, monkeypatch):
    monkeypatch.setenv("SENHA_CLI", SENHA_TESTE)
    assert cli.main(["login", "--usuario", USUARIO_TESTE]) == 0
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_ajuda_lista_os_subcomandos(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["--help"])
    assert saida.value.code == 0
    ajuda = capsys.readouterr().out
    for subcomando in ("saude", "login", "sessao", "TOKEN_CLI"):
        assert subcomando in ajuda


def test_sem_subcomando_e_erro_de_uso(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main([])
    assert saida.value.code == 2
    assert "<comando>" in capsys.readouterr().err


def test_saude_chama_health_da_cli(servidor_api, capsys):
    assert cli.main(["saude"]) == 0
    saida = capsys.readouterr().out
    assert "API OK (ok)" in saida
    evento = [json.loads(linha) for linha in saida.splitlines() if linha.startswith("{")][-1]
    assert evento["client"] == "cli" and evento["operation"] == "health"


def test_login_imprime_so_o_token_no_stdout(servidor_api, capsys, monkeypatch):
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: SENHA_TESTE)
    monkeypatch.setattr("builtins.input", lambda: USUARIO_TESTE)
    assert cli.main(["login"]) == 0
    capturado = capsys.readouterr()
    linhas = [linha for linha in capturado.out.splitlines() if not linha.startswith("{")]
    assert len(linhas) == 1
    token = linhas[0]
    assert api.serializador_cli().loads(token)["usuario"] == USUARIO_TESTE
    assert capturado.err.startswith("Usuário: ")
    assert "export TOKEN_CLI=" in capturado.err and NOME_TESTE in capturado.err
    assert "válido por 8 horas" in capturado.err
    assert token not in capturado.err


def test_login_le_senha_de_senha_cli(servidor_api, capsys, monkeypatch):
    def nao_perguntar(_prompt):
        raise AssertionError("não devia pedir a senha")

    monkeypatch.setattr(cli.getpass, "getpass", nao_perguntar)
    token = _token_pela_cli(capsys, monkeypatch)
    assert api.serializador_cli().loads(token)["nome"] == NOME_TESTE


def test_login_senha_errada_sai_com_codigo_3(servidor_api, capsys, monkeypatch):
    monkeypatch.setenv("SENHA_CLI", "errada")
    assert cli.main(["login", "--usuario", USUARIO_TESTE]) == cli.SAIDA_NAO_AUTENTICADO
    erro = capsys.readouterr().err
    assert "Usuário ou senha inválidos (HTTP 401)" in erro
    assert "Gere outro" not in erro


def test_sessao_envia_bearer_do_ambiente(servidor_api, capsys, monkeypatch):
    token = _token_pela_cli(capsys, monkeypatch)
    # espaços e quebras de linha do copiar/colar, nas pontas e no meio (terminal que quebra a linha longa)
    monkeypatch.setenv("TOKEN_CLI", f"  {token[:30]}\n{token[30:60]}\r\n {token[60:]}\n")
    assert cli.main(["sessao"]) == 0
    saida = capsys.readouterr().out
    assert f"Autenticado como {NOME_TESTE} ({USUARIO_TESTE})" in saida
    assert token not in saida  # nem a CLI nem os logs da API mostram o token


def test_sessao_sem_token_nao_chama_a_api(servidor_api, capsys, monkeypatch):
    monkeypatch.setattr(cli, "requisitar", lambda *_a, **_k: pytest.fail("não devia chamar a API"))
    monkeypatch.setenv("TOKEN_CLI", "   ")
    assert cli.main(["sessao"]) == cli.SAIDA_NAO_AUTENTICADO
    assert "defina TOKEN_CLI" in capsys.readouterr().err


def test_sessao_com_token_invalido_recebe_401(servidor_api, capsys, monkeypatch):
    monkeypatch.setenv("TOKEN_CLI", "token-falso")
    assert cli.main(["sessao"]) == cli.SAIDA_NAO_AUTENTICADO
    capturado = capsys.readouterr()
    assert "Não autenticado (HTTP 401)" in capturado.err and "Gere outro" in capturado.err
    assert "token-falso" not in capturado.out  # o log da API não expõe o token


def test_erro_da_api_diferente_de_401_sai_com_codigo_1(servidor_api, capsys, monkeypatch):
    def falhar(*_args):
        raise psycopg.OperationalError("fora do ar")

    monkeypatch.setattr(db, "buscar_usuario_cli", falhar)
    monkeypatch.setenv("SENHA_CLI", SENHA_TESTE)
    assert cli.main(["login", "--usuario", USUARIO_TESTE]) == cli.SAIDA_ERRO
    assert "Erro interno (HTTP 500)" in capsys.readouterr().err


def test_erro_da_api_sem_json_mostra_o_status_uma_vez(monkeypatch, capsys):
    def responder_502(_requisicao):
        raise urllib.error.HTTPError("http://api", 502, "Bad Gateway", {}, io.BytesIO(b"<html>"))

    monkeypatch.setattr(cli, "abrir_url", responder_502)
    assert cli.main(["saude"]) == cli.SAIDA_ERRO
    erro = capsys.readouterr().err
    assert "Erro: resposta inesperada da API (HTTP 502)" in erro
    assert erro.count("HTTP 502") == 1


def test_erro_da_api_com_corpo_cortado(monkeypatch, capsys):
    class CorpoCortado(io.BytesIO):
        def read(self, *_args):
            raise http.client.IncompleteRead(b"{")

    def responder_500(_requisicao):
        raise urllib.error.HTTPError("http://api", 500, "Erro", {}, CorpoCortado())

    monkeypatch.setattr(cli, "abrir_url", responder_500)
    assert cli.main(["saude"]) == cli.SAIDA_ERRO
    assert "Erro: resposta inesperada da API (HTTP 500)" in capsys.readouterr().err


@pytest.mark.parametrize(
    "excecao",
    [http.client.BadStatusLine("SSH-2.0"), http.client.IncompleteRead(b"{"), http.client.LineTooLong("header line")],
)
def test_resposta_que_nao_e_http_nao_gera_traceback(monkeypatch, capsys, excecao):
    def falhar(_requisicao):
        raise excecao

    monkeypatch.setattr(cli, "abrir_url", falhar)
    assert cli.main(["saude"]) == cli.SAIDA_ERRO
    assert "Erro: não foi possível conectar à API em" in capsys.readouterr().err


def test_servico_que_nao_e_http_nao_gera_traceback(monkeypatch, capsys):
    """URL_API apontando para um serviço que não fala HTTP (ex.: o PostgreSQL em db:5432)."""

    class NaoHttp(http.server.BaseHTTPRequestHandler):
        def handle(self):
            self.rfile.readline()
            self.wfile.write(b"isto nao e HTTP\r\n")

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), NaoHttp)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("URL_API", f"http://127.0.0.1:{servidor.server_port}")
        assert cli.main(["saude"]) == cli.SAIDA_ERRO
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert "Erro: não foi possível conectar à API em" in capsys.readouterr().err


def test_redirecionamento_nao_reenvia_o_token(monkeypatch, capsys):
    """Um 3xx não é seguido: o Authorization não vai para o endereço do redirecionamento."""
    recebidas = []

    class Redirecionador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            recebidas.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/outro-host")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirecionador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("URL_API", f"http://127.0.0.1:{servidor.server_port}")
        monkeypatch.setenv("TOKEN_CLI", "token-secreto")
        assert cli.main(["sessao"]) == cli.SAIDA_ERRO
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert recebidas == [("/api/cli/sessao", "Bearer token-secreto")]
    assert "Erro: resposta inesperada da API (HTTP 302)" in capsys.readouterr().err


def test_url_api_invalida_nao_expoe_o_token(monkeypatch, capsys):
    monkeypatch.setenv("URL_API", "sem-esquema")
    monkeypatch.setenv("TOKEN_CLI", "token-secreto")
    assert cli.main(["sessao"]) == cli.SAIDA_ERRO
    erro = capsys.readouterr().err
    assert "não foi possível conectar à API em sem-esquema (URL ou requisição inválida)" in erro
    assert "token-secreto" not in erro


def test_cabecalho_recusado_nao_expoe_o_token(monkeypatch, capsys):
    def recusar(requisicao):
        raise ValueError(f"Invalid header value {requisicao.get_header('Authorization')!r}")

    monkeypatch.setattr(cli, "abrir_url", recusar)
    with pytest.raises(cli.ApiIndisponivel) as erro:
        cli.requisitar("GET", "/sessao", token="token-secreto")
    assert "token-secreto" not in str(erro.value)


class _RespostaFixa(io.BytesIO):
    """Resposta HTTP 200 com o corpo dado, usável no `with` como a de `urlopen`."""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_resposta_de_sucesso_sem_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "abrir_url", lambda _requisicao: _RespostaFixa(b"<html>"))
    assert cli.main(["saude"]) == cli.SAIDA_ERRO
    assert "(a resposta não é JSON)" in capsys.readouterr().err


@pytest.mark.parametrize("comando", [["saude"], ["login", "--usuario", USUARIO_TESTE]])
@pytest.mark.parametrize("corpo", [b"[]", b'"ok"', b"null", b"1"])
def test_resposta_json_que_nao_e_objeto(monkeypatch, capsys, comando, corpo):
    monkeypatch.setenv("SENHA_CLI", SENHA_TESTE)
    monkeypatch.setattr(cli, "abrir_url", lambda _requisicao: _RespostaFixa(corpo))
    assert cli.main(comando) == cli.SAIDA_ERRO
    assert "(a resposta não é um objeto JSON)" in capsys.readouterr().err


@pytest.mark.parametrize("token", ["\u2018token\u2019", "tokén", "token\x07"])
def test_token_com_caracteres_invalidos_nao_chama_a_api(monkeypatch, capsys, token):
    monkeypatch.setattr(cli, "requisitar", lambda *_a, **_k: pytest.fail("não devia chamar a API"))
    monkeypatch.setenv("TOKEN_CLI", token)
    assert cli.main(["sessao"]) == cli.SAIDA_NAO_AUTENTICADO
    erro = capsys.readouterr().err
    assert "TOKEN_CLI tem caracteres inválidos" in erro
    assert "conectar" not in erro


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [
        (8 * 3600, "8 horas"),
        (3600, "1 hora"),
        (90 * 60, "90 minutos"),
        (60, "1 minuto"),
        (90, "90 segundos"),
        (30, "30 segundos"),
        (1, "1 segundo"),
        (0, "0 segundos"),
    ],
)
def test_descrever_validade(segundos, texto):
    assert cli.descrever_validade(segundos) == texto


def test_api_fora_do_ar(monkeypatch, capsys):
    monkeypatch.setenv("URL_API", "http://127.0.0.1:1")
    assert cli.main(["saude"]) == cli.SAIDA_ERRO
    assert "não foi possível conectar à API em http://127.0.0.1:1" in capsys.readouterr().err


def test_url_api_padrao_e_o_servico_do_compose(monkeypatch):
    monkeypatch.delenv("URL_API", raising=False)
    assert cli.url_api() == "http://api:8000"


def test_cancelar_no_prompt(monkeypatch, capsys):
    def cancelar():
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", cancelar)
    assert cli.main(["login"]) == cli.SAIDA_ERRO
    assert "Cancelado." in capsys.readouterr().err


def test_executar_como_script(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cli.py", "--help"])
    with pytest.raises(SystemExit) as saida:
        runpy.run_path(str(Path(cli.__file__)), run_name="__main__")
    assert saida.value.code == 0
    assert "saude" in capsys.readouterr().out
