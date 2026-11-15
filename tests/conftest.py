import json
import os
import sys

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import api  # noqa: E402
import db  # noqa: E402

USUARIO_TESTE = "testador"
SENHA_TESTE = "senha-de-teste"
NOME_TESTE = "Pessoa de Teste"


def eventos_do_log(capsys):
    """Eventos de log (linhas JSON) capturados no stdout desde a última leitura do `capsys`."""
    return [json.loads(linha) for linha in capsys.readouterr().out.splitlines() if linha.startswith("{")]


@pytest.fixture(autouse=True)
def banco_populado():
    db.inicializar_banco(USUARIO_TESTE, NOME_TESTE, generate_password_hash(SENHA_TESTE))
    yield


@pytest.fixture
def cliente_http():
    api.app.config.update(TESTING=True)
    with api.app.test_client() as c:
        yield c


@pytest.fixture
def token(cliente_http):
    resposta = cliente_http.post("/api/web/login", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    return resposta.get_json()["token"]
