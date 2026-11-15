import json

import psycopg
import pytest

import db
from conftest import USUARIO_TESTE, eventos_do_log


def _conexao_quebrada():
    raise psycopg.OperationalError("banco fora do ar")


def test_registrar_log_emite_linha_json(capsys):
    db.registrar_log("INFO", "web", "insert", "success", "req-1", "ok")
    evento = json.loads(capsys.readouterr().out.strip())
    assert evento["layer"] == "db"
    assert evento["request_id"] == "req-1"
    assert evento["timestamp"].endswith("Z")
    assert set(evento) == {"timestamp", "level", "layer", "client", "operation", "status", "request_id", "message"}


def test_registrar_log_aceita_camada_api(capsys):
    db.registrar_log("INFO", "web", "login", "success", "req-1", "ok", camada="api")
    assert json.loads(capsys.readouterr().out.strip())["layer"] == "api"


def test_inicializar_banco_registra_origem_sistema(capsys):
    db.inicializar_banco(USUARIO_TESTE, "Nome", "hash")
    evento = eventos_do_log(capsys)[-1]
    assert evento["client"] == "sistema" and evento["request_id"] == "inicializacao"


def test_inicializar_banco_e_idempotente_e_atualiza_nome_e_hash():
    db.inicializar_banco(USUARIO_TESTE, "Nome Novo", "hash-novo")
    registro = db.buscar_usuario(USUARIO_TESTE, "req-2", "web")
    assert registro["senha_hash"] == "hash-novo"
    assert registro["nome"] == "Nome Novo"


def test_buscar_usuario_encontrado_e_registrado(capsys):
    registro = db.buscar_usuario(USUARIO_TESTE, "req-3", "web")
    assert registro["usuario"] == USUARIO_TESTE
    evento = eventos_do_log(capsys)[-1]
    assert evento["operation"] == "consultar_usuario" and evento["status"] == "success"
    assert evento["request_id"] == "req-3"


def test_buscar_usuario_cli_registra_client_cli(capsys):
    registro = db.buscar_usuario_cli(USUARIO_TESTE, "req-cli")
    assert registro["usuario"] == USUARIO_TESTE and registro["senha_hash"]
    evento = eventos_do_log(capsys)[-1]
    assert evento["layer"] == "db" and evento["client"] == "cli" and evento["request_id"] == "req-cli"
    assert evento["operation"] == "consultar_usuario_cli" and evento["message"] == "Usuário encontrado"
    assert db.buscar_usuario_cli("ninguem", "req-cli") is None
    assert eventos_do_log(capsys)[-1]["message"] == "Usuário não encontrado"


def test_buscar_usuario_cli_erro_no_banco(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.buscar_usuario_cli(USUARIO_TESTE, "req-cli")
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["client"] == "cli" and evento["operation"] == "consultar_usuario_cli"


def test_buscar_usuario_inexistente():
    assert db.buscar_usuario("ninguem", "req-4", "web") is None


def test_buscar_usuario_erro_no_banco(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.buscar_usuario(USUARIO_TESTE, "req-5", "web")
    evento = eventos_do_log(capsys)[-1]
    assert evento["level"] == "ERROR" and evento["status"] == "error"


def test_inicializar_banco_erro(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.inicializar_banco("x", "y", "z")
    evento = eventos_do_log(capsys)[-1]
    assert evento["operation"] == "inicializar_banco" and evento["status"] == "error"


def test_inicializar_banco_carrega_catalogo_sem_duplicar():
    db.inicializar_banco(USUARIO_TESTE, "Nome", "hash")
    with db.obter_conexao() as conexao:
        total = conexao.execute("SELECT COUNT(*) AS total FROM produto").fetchone()["total"]
    assert total == len(db.PRODUTOS_INICIAIS)


def test_venda_calcula_valor_total():
    with db.obter_conexao() as conexao:
        produto = conexao.execute("SELECT id FROM produto WHERE nome = %s", ("Arroz 5 kg",)).fetchone()
        venda = conexao.execute(
            "INSERT INTO venda (produto_id, quantidade, valor_unitario, data_venda) "
            "VALUES (%s, %s, %s, %s) RETURNING valor_total, data_venda",
            (produto["id"], 3, "27.90", "2026-10-15"),
        ).fetchone()
        conexao.rollback()
    assert str(venda["valor_total"]) == "83.70"
    assert str(venda["data_venda"]) == "2026-10-15"
