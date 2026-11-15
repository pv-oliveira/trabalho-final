"""Testes do cadastro e edição de vendas pela CLI (issue #8): rotas /api/cli/*, banco e cli.py."""

import json
from datetime import timedelta

import psycopg
import pytest

import api
import cli
import db
from conftest import SENHA_TESTE, USUARIO_TESTE, eventos_do_log

AMANHA = (api.hoje() + timedelta(days=1)).isoformat()


def _falhar_no_banco(*_args):
    raise psycopg.OperationalError("banco fora do ar")


def _conexao_quebrada():
    raise psycopg.OperationalError("banco fora do ar")


def _id_produto(nome="Arroz 5 kg"):
    with db.obter_conexao() as conexao:
        return conexao.execute("SELECT id FROM produto WHERE nome = %s", (nome,)).fetchone()["id"]


def _venda_valida(**alteracoes):
    venda = {"produto_id": _id_produto(), "quantidade": 3, "valor_unitario": "25.50", "data_venda": "2026-10-15"}
    venda.update(alteracoes)
    return venda


def _da_requisicao(capsys, resposta):
    request_id = resposta.headers["X-Request-ID"]
    return [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]


@pytest.fixture
def token_cli(cliente_http):
    resposta = cliente_http.post("/api/cli/token", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    return resposta.get_json()["token"]


@pytest.fixture
def cabecalho_cli(token_cli):
    return {"Authorization": f"Bearer {token_cli}"}


@pytest.fixture
def venda_criada(cliente_http, cabecalho_cli):
    return cliente_http.post("/api/cli/records", json=_venda_valida(), headers=cabecalho_cli).get_json()


# --- API: autenticação ----------------------------------------------------------------------------


ROTAS_CLI = [("get", "/api/cli/produtos"), ("post", "/api/cli/records"), ("put", "/api/cli/records/1")]


@pytest.mark.parametrize("metodo, rota", ROTAS_CLI)
@pytest.mark.parametrize("cabecalho_auth", [None, "Bearer invalido", "Basic abc"])
def test_rotas_de_venda_da_cli_exigem_token(cliente_http, capsys, metodo, rota, cabecalho_auth):
    capsys.readouterr()
    cabecalhos = {"Authorization": cabecalho_auth} if cabecalho_auth else {}
    resposta = getattr(cliente_http, metodo)(rota, json=_venda_valida(), headers=cabecalhos)
    assert resposta.status_code == 401
    assert [(e["layer"], e["client"], e["level"]) for e in _da_requisicao(capsys, resposta)] == [("api", "cli", "WARN")]


@pytest.mark.parametrize("metodo, rota", ROTAS_CLI)
def test_token_da_web_nao_vale_nas_rotas_de_venda_da_cli(cliente_http, token, metodo, rota):
    resposta = getattr(cliente_http, metodo)(rota, json=_venda_valida(), headers={"Authorization": f"Bearer {token}"})
    assert resposta.status_code == 401


# --- API: produtos --------------------------------------------------------------------------------


def test_cli_listar_produtos_registra_client_cli(cliente_http, cabecalho_cli, capsys):
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/produtos", headers=cabecalho_cli)
    assert resposta.status_code == 200
    produtos = resposta.get_json()["produtos"]
    assert len(produtos) == len(db.PRODUTOS_INICIAIS)
    assert {"id": _id_produto(), "nome": "Arroz 5 kg", "categoria": "Alimentos", "preco": "27.90"} in produtos
    eventos = _da_requisicao(capsys, resposta)
    assert {e["layer"] for e in eventos} == {"api", "db"}
    assert {(e["client"], e["operation"]) for e in eventos} == {("cli", "listar_produtos")}


def test_cli_listar_produtos_erro_no_banco(cliente_http, cabecalho_cli, monkeypatch, capsys):
    monkeypatch.setattr(db, "listar_produtos_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/produtos", headers=cabecalho_cli)
    assert resposta.status_code == 500
    evento = _da_requisicao(capsys, resposta)[-1]
    assert evento["client"] == "cli" and evento["level"] == "ERROR"


# --- API: criação ---------------------------------------------------------------------------------


def test_cli_criar_venda_persiste_visivel_na_web_e_correlaciona_logs(cliente_http, cabecalho_cli, token_cli, token, capsys):
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/records", json=_venda_valida(), headers=cabecalho_cli)
    assert resposta.status_code == 201
    venda = resposta.get_json()
    assert venda["produto_nome"] == "Arroz 5 kg" and venda["valor_total"] == "76.50"

    eventos = _da_requisicao(capsys, resposta)
    assert {e["layer"] for e in eventos} == {"api", "db"}
    assert all(e["client"] == "cli" and e["operation"] == "criar_venda" and e["status"] == "success" for e in eventos)
    assert token_cli not in json.dumps(eventos)

    na_web = cliente_http.get(f"/api/web/records/{venda['id']}", headers={"Authorization": f"Bearer {token}"})
    assert na_web.status_code == 200 and na_web.get_json() == venda


@pytest.mark.parametrize(
    "campo, valor",
    [
        ("produto_id", ""),
        ("produto_id", "1"),
        ("produto_id", 0),
        ("quantidade", 0),
        ("quantidade", "2"),
        ("quantidade", api.QUANTIDADE_MAXIMA + 1),
        ("valor_unitario", "0"),
        ("valor_unitario", "1.234"),
        ("valor_unitario", "100000.01"),
        ("data_venda", "15/10/2026"),
        ("data_venda", "2026-02-30"),
        ("data_venda", AMANHA),
    ],
)
def test_cli_criar_venda_tem_as_mesmas_validacoes_da_web(cliente_http, cabecalho_cli, token, capsys, campo, valor):
    corpo = _venda_valida(**{campo: valor})
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/records", json=corpo, headers=cabecalho_cli)
    assert resposta.status_code == 422
    assert set(resposta.get_json()["campos"]) == {campo}
    # Recusado antes do banco: só a camada api registra.
    assert [(e["layer"], e["client"], e["level"]) for e in _da_requisicao(capsys, resposta)] == [("api", "cli", "WARN")]
    na_web = cliente_http.post("/api/web/records", json=corpo, headers={"Authorization": f"Bearer {token}"})
    assert na_web.get_json() == resposta.get_json()


@pytest.mark.parametrize("corpo", [None, [1], "x"])
def test_cli_criar_venda_corpo_nao_objeto(cliente_http, cabecalho_cli, corpo):
    assert cliente_http.post("/api/cli/records", json=corpo, headers=cabecalho_cli).status_code == 400


def test_cli_criar_venda_produto_inexistente(cliente_http, cabecalho_cli, capsys):
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/records", json=_venda_valida(produto_id=999999), headers=cabecalho_cli)
    assert resposta.status_code == 422
    assert resposta.get_json()["campos"] == {"produto_id": "Produto não encontrado."}
    eventos = _da_requisicao(capsys, resposta)
    assert {(e["layer"], e["client"], e["level"]) for e in eventos} == {("api", "cli", "WARN"), ("db", "cli", "WARN")}


def test_cli_criar_venda_erro_no_banco(cliente_http, cabecalho_cli, monkeypatch, capsys):
    monkeypatch.setattr(db, "criar_venda_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = cliente_http.post("/api/cli/records", json=_venda_valida(), headers=cabecalho_cli)
    assert resposta.status_code == 500
    evento = _da_requisicao(capsys, resposta)[-1]
    assert evento["client"] == "cli" and evento["level"] == "ERROR" and evento["operation"] == "criar_venda"


# --- API: edição ----------------------------------------------------------------------------------


def test_cli_editar_venda_atualiza_e_correlaciona_logs(cliente_http, cabecalho_cli, venda_criada, token, capsys):
    corpo = _venda_valida(produto_id=_id_produto("Sabonete"), quantidade=10, valor_unitario="2.00", data_venda="2026-09-01")
    capsys.readouterr()
    resposta = cliente_http.put(f"/api/cli/records/{venda_criada['id']}", json=corpo, headers=cabecalho_cli)
    assert resposta.status_code == 200
    venda = resposta.get_json()
    assert venda["id"] == venda_criada["id"] and venda["produto_nome"] == "Sabonete" and venda["valor_total"] == "20.00"

    eventos = _da_requisicao(capsys, resposta)
    assert {e["layer"] for e in eventos} == {"api", "db"}
    assert all(e["client"] == "cli" and e["operation"] == "editar_venda" for e in eventos)

    na_web = cliente_http.get(f"/api/web/records/{venda['id']}", headers={"Authorization": f"Bearer {token}"})
    assert na_web.get_json() == venda


@pytest.mark.parametrize("id_venda", [999999, api.ID_MAXIMO + 1])
def test_cli_editar_venda_inexistente(cliente_http, cabecalho_cli, capsys, id_venda):
    capsys.readouterr()
    resposta = cliente_http.put(f"/api/cli/records/{id_venda}", json=_venda_valida(), headers=cabecalho_cli)
    assert resposta.status_code == 404
    assert resposta.get_json() == {"erro": "Venda não encontrada."}
    eventos = _da_requisicao(capsys, resposta)
    assert all(e["client"] == "cli" and e["level"] == "WARN" for e in eventos)


def test_cli_editar_venda_inexistente_registra_as_duas_camadas(cliente_http, cabecalho_cli, capsys):
    capsys.readouterr()
    resposta = cliente_http.put("/api/cli/records/999999", json=_venda_valida(), headers=cabecalho_cli)
    assert {e["layer"] for e in _da_requisicao(capsys, resposta)} == {"api", "db"}


@pytest.mark.parametrize("id_venda", ["-1", "0", "abc", "1.5"])
def test_cli_editar_venda_id_invalido(cliente_http, cabecalho_cli, capsys, id_venda):
    capsys.readouterr()
    resposta = cliente_http.put(f"/api/cli/records/{id_venda}", json=_venda_valida(), headers=cabecalho_cli)
    assert resposta.status_code == 400
    assert resposta.get_json() == {"erro": "Número da venda inválido."}
    assert [(e["layer"], e["client"]) for e in _da_requisicao(capsys, resposta)] == [("api", "cli")]


def test_cli_editar_venda_dados_invalidos_e_produto_inexistente(cliente_http, cabecalho_cli, venda_criada):
    url = f"/api/cli/records/{venda_criada['id']}"
    assert cliente_http.put(url, data="não é json", headers=cabecalho_cli).status_code == 400
    assert cliente_http.put(url, json=_venda_valida(quantidade=0), headers=cabecalho_cli).status_code == 422
    resposta = cliente_http.put(url, json=_venda_valida(produto_id=999999), headers=cabecalho_cli)
    assert resposta.status_code == 422 and set(resposta.get_json()["campos"]) == {"produto_id"}


def test_cli_editar_venda_erro_no_banco(cliente_http, cabecalho_cli, monkeypatch, capsys):
    monkeypatch.setattr(db, "editar_venda_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = cliente_http.put("/api/cli/records/1", json=_venda_valida(), headers=cabecalho_cli)
    assert resposta.status_code == 500
    evento = _da_requisicao(capsys, resposta)[-1]
    assert evento["client"] == "cli" and evento["level"] == "ERROR" and evento["operation"] == "editar_venda"


# --- banco: consultas da CLI ----------------------------------------------------------------------


def _venda_db(**alteracoes):
    venda = {"produto_id": _id_produto(), "quantidade": 2, "valor_unitario": "5.00", "data_venda": "2026-10-01"}
    venda.update(alteracoes)
    return venda


def test_db_consultas_da_cli_registram_client_cli(capsys):
    assert len(db.listar_produtos_cli("req-c1")) == len(db.PRODUTOS_INICIAIS)
    criada = db.criar_venda_cli(_venda_db(), "req-c2")
    assert str(criada["valor_total"]) == "10.00"
    editada = db.editar_venda_cli(criada["id"], _venda_db(quantidade=4), "req-c3")
    assert str(editada["valor_total"]) == "20.00"
    eventos = eventos_do_log(capsys)[-3:]
    assert [(e["operation"], e["request_id"]) for e in eventos] == [
        ("listar_produtos", "req-c1"),
        ("criar_venda", "req-c2"),
        ("editar_venda", "req-c3"),
    ]
    assert all(e["layer"] == "db" and e["client"] == "cli" and e["status"] == "success" for e in eventos)


def test_db_cli_venda_e_produto_inexistentes(capsys):
    assert db.editar_venda_cli(999999, _venda_db(), "req-c4") is None
    evento = eventos_do_log(capsys)[-1]
    assert evento["client"] == "cli" and evento["level"] == "WARN" and evento["request_id"] == "req-c4"
    with pytest.raises(db.ProdutoInexistente):
        db.criar_venda_cli(_venda_db(produto_id=999999), "req-c5")
    evento = eventos_do_log(capsys)[-1]
    assert evento["client"] == "cli" and evento["operation"] == "criar_venda" and evento["level"] == "WARN"


@pytest.mark.parametrize(
    "funcao, argumentos, operacao",
    [
        (db.listar_produtos_cli, (), "listar_produtos"),
        (db.criar_venda_cli, (None,), "criar_venda"),
        (db.editar_venda_cli, (1, None), "editar_venda"),
    ],
)
def test_db_cli_erro_no_banco(monkeypatch, capsys, funcao, argumentos, operacao):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        funcao(*argumentos, "req-c6")
    evento = eventos_do_log(capsys)[-1]
    assert evento["client"] == "cli" and evento["level"] == "ERROR" and evento["operation"] == operacao


# --- CLI (cli.py) contra a API real, num servidor HTTP local --------------------------------------


def _saida_da_cli(capsys):
    """Linhas que a CLI imprimiu no stdout, sem os logs JSON da API (que roda no mesmo processo)."""
    return [linha for linha in capsys.readouterr().out.splitlines() if not linha.startswith("{")]


@pytest.fixture
def com_token(servidor_api, token_cli, monkeypatch):
    monkeypatch.setenv("TOKEN_CLI", token_cli)


OPCOES_VALIDAS = ["--produto", "1", "--quantidade", "3", "--valor-unitario", "25.50", "--data", "2026-10-15"]


def _opcoes(**alteracoes):
    """As 4 opções da venda com o produto real, trocando as pedidas (chave = nome da opção sem os traços)."""
    valores = {"produto": str(_id_produto()), "quantidade": "3", "valor-unitario": "25.50", "data": "2026-10-15"}
    valores.update(alteracoes)
    return [item for opcao, valor in valores.items() for item in (f"--{opcao}", valor)]


def test_ajuda_lista_os_comandos_de_venda(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["--help"])
    assert saida.value.code == 0
    ajuda = capsys.readouterr().out
    for subcomando in ("produtos", "criar", "editar"):
        assert subcomando in ajuda


@pytest.mark.parametrize("comando", [["criar"], ["editar", "1"]])
def test_ajuda_de_criar_e_editar_mostra_os_4_campos(capsys, comando):
    with pytest.raises(SystemExit):
        cli.main([*comando, "--help"])
    ajuda = capsys.readouterr().out
    for opcao in ("--produto", "--quantidade", "--valor-unitario", "--data"):
        assert opcao in ajuda


@pytest.mark.parametrize(
    "comando", [["criar"], ["criar", "--produto", "1"], ["editar", "1"], ["editar", *OPCOES_VALIDAS]]
)
def test_criar_e_editar_sem_os_campos_obrigatorios_e_erro_de_uso(capsys, comando):
    with pytest.raises(SystemExit) as saida:
        cli.main(comando)
    assert saida.value.code == 2


def test_produtos_mostra_o_catalogo(com_token, capsys):
    capsys.readouterr()
    assert cli.main(["produtos"]) == 0
    linhas = _saida_da_cli(capsys)
    assert linhas[0].split() == ["ID", "Produto", "Categoria", "Preço"]
    assert len(linhas) == 1 + len(db.PRODUTOS_INICIAIS)
    arroz = next(linha for linha in linhas if "Arroz 5 kg" in linha)
    assert arroz.split()[0] == str(_id_produto()) and "Alimentos" in arroz and arroz.endswith("R$ 27,90")


def test_criar_cadastra_e_a_venda_aparece_na_web(com_token, cliente_http, token, capsys):
    capsys.readouterr()
    assert cli.main(["criar", *_opcoes()]) == 0
    [saida] = _saida_da_cli(capsys)
    assert saida.startswith("Venda nº ")
    assert saida.endswith("cadastrada: 3 × Arroz 5 kg, total R$ 76,50, em 15/10/2026.")
    id_venda = saida.split()[2]
    na_web = cliente_http.get(f"/api/web/records/{id_venda}", headers={"Authorization": f"Bearer {token}"})
    assert na_web.get_json()["valor_total"] == "76.50"


def test_criar_aceita_virgula_no_valor_unitario(com_token, capsys):
    capsys.readouterr()
    assert cli.main(["criar", *_opcoes(**{"valor-unitario": "25,50"})]) == 0
    assert _saida_da_cli(capsys)[0].endswith("total R$ 76,50, em 15/10/2026.")


def test_virgula_com_casas_demais_e_recusada_pela_api(com_token, capsys):
    capsys.readouterr()
    assert cli.main(["criar", *_opcoes(**{"valor-unitario": "1,234"})]) == cli.SAIDA_ERRO
    assert "  --valor-unitario: O valor unitário deve ser maior que zero" in capsys.readouterr().err


def test_criar_com_campos_invalidos_mostra_cada_erro_com_a_opcao(com_token, capsys):
    capsys.readouterr()
    opcoes = _opcoes(quantidade="0", **{"valor-unitario": "abc", "data": AMANHA})
    assert cli.main(["criar", *opcoes]) == cli.SAIDA_ERRO
    erro = capsys.readouterr().err.splitlines()
    assert erro[0] == "Erro: Dados inválidos. (HTTP 422)"
    assert set(erro[1:]) == {
        f"  --quantidade: A quantidade deve ser um número inteiro entre 1 e {api.QUANTIDADE_MAXIMA}.",
        f"  --valor-unitario: O valor unitário deve ser maior que zero, até {api.VALOR_UNITARIO_MAXIMO}, "
        "com no máximo 2 casas decimais.",
        "  --data: A data da venda não pode ser futura.",
    }


@pytest.mark.parametrize("texto", ["-1", "1.5", "abc", "", "３"])
def test_criar_com_numero_invalido_recebe_a_mensagem_da_api(com_token, capsys, texto):
    capsys.readouterr()
    assert cli.main(["criar", *_opcoes(quantidade=texto)]) == cli.SAIDA_ERRO
    erro = capsys.readouterr().err
    mensagem = "Informe a quantidade." if texto == "" else "A quantidade deve ser um número inteiro entre 1 e"
    assert f"  --quantidade: {mensagem}" in erro


def test_criar_com_produto_inexistente(com_token, capsys):
    capsys.readouterr()
    assert cli.main(["criar", *_opcoes(produto="999999")]) == cli.SAIDA_ERRO
    assert "  --produto: Produto não encontrado." in capsys.readouterr().err


def test_editar_atualiza_a_venda(com_token, venda_criada, capsys):
    capsys.readouterr()
    opcoes = _opcoes(produto=str(_id_produto("Sabonete")), quantidade="10", **{"valor-unitario": "2", "data": "2026-09-01"})
    assert cli.main(["editar", str(venda_criada["id"]), *opcoes]) == 0
    assert _saida_da_cli(capsys) == [
        f"Venda nº {venda_criada['id']} atualizada: 10 × Sabonete, total R$ 20,00, em 01/09/2026."
    ]


def test_editar_venda_inexistente_mostra_erro(com_token, capsys):
    capsys.readouterr()
    assert cli.main(["editar", "999999", *_opcoes()]) == cli.SAIDA_ERRO
    assert capsys.readouterr().err.strip() == "Erro: Venda não encontrada. (HTTP 404)"


@pytest.mark.parametrize("id_venda", ["abc", "-1", "1?x=2", "1#2"])
def test_editar_com_numero_invalido(com_token, capsys, id_venda):
    capsys.readouterr()
    assert cli.main(["editar", id_venda, *_opcoes()]) == cli.SAIDA_ERRO
    assert capsys.readouterr().err.strip() == "Erro: Número da venda inválido. (HTTP 400)"


def test_editar_com_barra_no_numero_nao_edita_outra_venda(com_token, venda_criada, capsys):
    # A barra vai codificada (%2F); o servidor a decodifica e a rota não existe, então nada é editado.
    capsys.readouterr()
    assert cli.main(["editar", f"{venda_criada['id']}/../{venda_criada['id']}", *_opcoes(quantidade="9")]) == cli.SAIDA_ERRO
    assert capsys.readouterr().err.strip() == "Erro: Rota não encontrada (HTTP 404)"
    with db.obter_conexao() as conexao:
        quantidade = conexao.execute("SELECT quantidade FROM venda WHERE id = %s", (venda_criada["id"],)).fetchone()
    assert quantidade["quantidade"] == venda_criada["quantidade"]


@pytest.mark.parametrize("comando", [["produtos"], ["criar", *OPCOES_VALIDAS], ["editar", "1", *OPCOES_VALIDAS]])
def test_comandos_de_venda_sem_token_nao_chamam_a_api(monkeypatch, capsys, comando):
    monkeypatch.delenv("TOKEN_CLI", raising=False)
    monkeypatch.setattr(cli, "abrir_url", lambda _requisicao: pytest.fail("não deveria chamar a API"))
    assert cli.main(comando) == cli.SAIDA_NAO_AUTENTICADO
    assert "comando protegido" in capsys.readouterr().err


@pytest.mark.parametrize("comando", [["produtos"], ["criar", *OPCOES_VALIDAS], ["editar", "1", *OPCOES_VALIDAS]])
def test_comandos_de_venda_com_token_invalido_saem_com_codigo_3(servidor_api, monkeypatch, capsys, comando):
    monkeypatch.setenv("TOKEN_CLI", "token-falso")
    assert cli.main(comando) == cli.SAIDA_NAO_AUTENTICADO
    assert "Erro: Não autenticado (HTTP 401)" in capsys.readouterr().err


def test_erros_por_campo_ignorados_quando_nao_sao_objeto():
    assert cli.ErroDaApi(422, {"erro": "x", "campos": ["quantidade"]}).campos == {}
    assert cli.ErroDaApi(502, None).campos == {}


def test_campo_desconhecido_aparece_com_o_nome_da_api(monkeypatch, capsys):
    def responder_422(*_args, **_kwargs):
        raise cli.ErroDaApi(422, {"erro": "Dados inválidos.", "campos": {"outro": "mensagem"}})

    monkeypatch.setenv("TOKEN_CLI", "token")
    monkeypatch.setattr(cli, "requisitar", responder_422)
    assert cli.main(["criar", *OPCOES_VALIDAS]) == cli.SAIDA_ERRO
    assert "  outro: mensagem" in capsys.readouterr().err


@pytest.mark.parametrize(
    "texto, esperado",
    [("0.50", "R$ 0,50"), ("25.50", "R$ 25,50"), ("1234.50", "R$ 1.234,50"), ("1000000.00", "R$ 1.000.000,00")],
)
def test_moeda_no_formato_brasileiro(texto, esperado):
    assert cli.moeda(texto) == esperado


def test_data_brasileira():
    assert cli.data_brasileira("2026-10-05") == "05/10/2026"


@pytest.mark.parametrize("texto, esperado", [("3", 3), ("007", 7), ("-1", "-1"), ("1.5", "1.5"), ("３", "３"), ("", "")])
def test_inteiro_ou_texto(texto, esperado):
    assert cli.inteiro_ou_texto(texto) == esperado
