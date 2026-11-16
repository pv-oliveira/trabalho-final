"""Testes da exportação CSV pela CLI (issue #11): rota /api/cli/records/export, banco e comando `exportar`."""

import csv
import io
import json
import re
from datetime import date

import psycopg
import pytest

import cli
import db
from conftest import SENHA_TESTE, USUARIO_TESTE, eventos_do_log

CABECALHO = ["Nº", "Data", "Produto", "Categoria", "Quantidade", "Valor unitário", "Valor total"]


def _falhar_no_banco(*_args):
    raise psycopg.OperationalError("banco fora do ar")


def _conexao_quebrada():
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


def _da_requisicao(capsys, resposta):
    request_id = resposta.headers["X-Request-ID"]
    return [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]


@pytest.fixture(autouse=True)
def vendas_limpas():
    """Cada teste começa sem vendas, para os resultados serem previsíveis."""
    with db.obter_conexao() as conexao:
        conexao.execute("TRUNCATE venda RESTART IDENTITY")


@pytest.fixture
def token_cli(cliente_http):
    resposta = cliente_http.post("/api/cli/token", json={"usuario": USUARIO_TESTE, "senha": SENHA_TESTE})
    return resposta.get_json()["token"]


@pytest.fixture
def cabecalho_cli(token_cli):
    return {"Authorization": f"Bearer {token_cli}"}


@pytest.fixture
def cabecalho_web(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def vendas():
    """12 vendas de 3 categorias em datas diferentes (mais que uma página da listagem). Retorna {apelido: id}."""
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
        "arroz_nov_2": ("Arroz 5 kg", 1, "26.00", "2026-11-12"),
        "refri_nov": ("Refrigerante 2 L", 3, "9.90", "2026-11-14"),
    }
    return {apelido: _inserir_venda(*venda) for apelido, venda in dados.items()}


def _exportar(cliente_http, cabecalho, consulta="", rota="/api/cli/records/export"):
    return cliente_http.get(f"{rota}?{consulta}", headers=cabecalho)


def _linhas_csv(conteudo):
    """Lê o CSV (bytes, com o BOM) e devolve (cabeçalho, linhas)."""
    linhas = list(csv.reader(io.StringIO(conteudo.decode("utf-8-sig"), newline=""), delimiter=";"))
    return linhas[0], linhas[1:]


def _ids(linhas):
    return [int(linha[0]) for linha in linhas]


# --- API: autenticação ----------------------------------------------------------------------------


@pytest.mark.parametrize("cabecalho", [None, "Bearer invalido", "Basic abc", "Bearer "])
def test_exportacao_exige_token_da_cli(cliente_http, cabecalho, monkeypatch, capsys):
    monkeypatch.setattr(db, "exportar_vendas_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = _exportar(cliente_http, {"Authorization": cabecalho} if cabecalho else {}, "categoria=Bebidas")
    assert resposta.status_code == 401 and resposta.get_json() == {"erro": "Não autenticado"}
    eventos = _da_requisicao(capsys, resposta)
    assert [(e["layer"], e["level"], e["client"], e["operation"]) for e in eventos] == [
        ("api", "WARN", "cli", "exportar_vendas")
    ]


def test_token_da_web_nao_vale_na_exportacao_da_cli(cliente_http, cabecalho_web):
    assert _exportar(cliente_http, cabecalho_web).status_code == 401


def test_parametros_invalidos_sem_token_respondem_401(cliente_http):
    assert _exportar(cliente_http, {}, "produto_id=abc&ordenar=senha_hash").status_code == 401


# --- API: conteúdo do arquivo ---------------------------------------------------------------------


def test_sem_filtros_exporta_todas_as_vendas_sem_limite_de_pagina(cliente_http, cabecalho_cli, vendas):
    resposta = _exportar(cliente_http, cabecalho_cli)
    assert resposta.status_code == 200
    cabecalho, linhas = _linhas_csv(resposta.get_data())
    assert cabecalho == CABECALHO
    # 12 vendas: mais que o tamanho padrão de página da CLI (10), todas no mesmo arquivo.
    assert sorted(_ids(linhas)) == sorted(vendas.values())


@pytest.mark.parametrize(
    "consulta, esperados",
    [
        ("categoria=Bebidas", {"refri_set", "refri_out", "suco_out", "refri_nov"}),
        ("data_inicio=2026-11-01", {"arroz_nov", "feijao_nov", "sabonete_nov", "arroz_nov_2", "refri_nov"}),
        ("categoria=Alimentos&data_fim=2026-10-31", {"arroz_set", "arroz_out", "feijao_out"}),
        ("categoria=Alimentos&data_inicio=2026-10-01&data_fim=2026-10-31", {"arroz_out", "feijao_out"}),
        ("categoria=Bebidas&data_inicio=2026-10-01&data_fim=2026-10-31", {"refri_out", "suco_out"}),
        ("categoria=&produto_id=&data_inicio=&data_fim=", None),  # filtros vazios não restringem
    ],
)
def test_exporta_todas_as_vendas_dos_filtros_combinados(cliente_http, cabecalho_cli, vendas, consulta, esperados):
    esperados = set(vendas) if esperados is None else esperados
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho_cli, consulta).get_data())
    assert sorted(_ids(linhas)) == sorted(vendas[apelido] for apelido in esperados)


def test_quatro_filtros_juntos(cliente_http, cabecalho_cli, vendas):
    consulta = f"produto_id={_id_produto('Arroz 5 kg')}&categoria=Alimentos&data_inicio=2026-10-01&data_fim=2026-11-30"
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho_cli, consulta).get_data())
    assert sorted(_ids(linhas)) == sorted([vendas["arroz_out"], vendas["arroz_nov"], vendas["arroz_nov_2"]])


def test_filtro_sem_resultado_gera_so_o_cabecalho(cliente_http, cabecalho_cli, vendas):
    resposta = _exportar(cliente_http, cabecalho_cli, "categoria=Limpeza")
    assert resposta.status_code == 200
    assert resposta.get_data() == "﻿Nº;Data;Produto;Categoria;Quantidade;Valor unitário;Valor total\r\n".encode()


def test_ordenacao_padrao_e_data_mais_recente_primeiro(cliente_http, cabecalho_cli, vendas):
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho_cli).get_data())
    datas = [date(*map(int, reversed(linha[1].split("/")))) for linha in linhas]
    assert datas == sorted(datas, reverse=True)


def test_ordenacao_escolhida_com_desempate_pelo_id(cliente_http, cabecalho_cli, vendas):
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho_cli, "ordenar=quantidade&direcao=asc").get_data())
    chaves = [(int(linha[4]), int(linha[0])) for linha in linhas]
    assert chaves == sorted(chaves)


def test_formato_igual_ao_csv_da_web(cliente_http, cabecalho_cli, vendas):
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho_cli, "ordenar=id&direcao=asc").get_data())
    assert linhas[0] == [str(vendas["arroz_set"]), "10/09/2026", "Arroz 5 kg", "Alimentos", "1", "27,90", "27,90"]
    assert linhas[5] == [str(vendas["refri_set"]), "20/09/2026", "Refrigerante 2 L", "Bebidas", "6", "9,90", "59,40"]


@pytest.mark.parametrize(
    "consulta",
    [
        "",
        "categoria=Alimentos&data_inicio=2026-10-01",
        "produto_id=1&data_fim=2026-10-31&ordenar=valor_total&direcao=asc",
        "categoria=Bebidas&ordenar=produto_nome&direcao=desc",
    ],
)
def test_mesmos_parametros_mesmo_arquivo_da_web(cliente_http, cabecalho_cli, cabecalho_web, vendas, consulta):
    web = _exportar(cliente_http, cabecalho_web, consulta, rota="/api/web/records/export")
    assert _exportar(cliente_http, cabecalho_cli, consulta).get_data() == web.get_data()


def test_cabecalhos_http_do_arquivo(cliente_http, cabecalho_cli, vendas):
    resposta = _exportar(cliente_http, cabecalho_cli)
    assert resposta.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert re.fullmatch(r'attachment; filename="vendas-\d{8}-\d{6}\.csv"', resposta.headers["Content-Disposition"])
    assert resposta.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("consulta", ["pagina=2", "tamanho=1", "pagina=abc&tamanho=0"])
def test_pagina_e_tamanho_sao_ignorados(cliente_http, cabecalho_cli, vendas, consulta):
    resposta = _exportar(cliente_http, cabecalho_cli, consulta)
    assert resposta.status_code == 200 and len(_linhas_csv(resposta.get_data())[1]) == len(vendas)


# --- API: validação, logs e erros -----------------------------------------------------------------


@pytest.mark.parametrize(
    "consulta, campos",
    [
        ("produto_id=abc", {"produto_id"}),
        ("produto_id=" + "9" * 5000, {"produto_id"}),
        ("categoria=Bebidas%00", {"categoria"}),
        ("data_inicio=2026-13-01", {"data_inicio"}),
        ("data_fim=01/10/2026", {"data_fim"}),
        ("data_inicio=2026-10-31&data_fim=2026-10-01", {"data_fim"}),
        ("ordenar=senha_hash", {"ordenar"}),
        ("direcao=cima", {"direcao"}),
        ("produto_id=x&data_fim=z&ordenar=y", {"produto_id", "data_fim", "ordenar"}),
    ],
)
def test_parametros_invalidos_400_sem_chegar_ao_banco(cliente_http, cabecalho_cli, monkeypatch, capsys, consulta, campos):
    monkeypatch.setattr(db, "exportar_vendas_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = _exportar(cliente_http, cabecalho_cli, consulta)
    assert resposta.status_code == 400
    corpo = resposta.get_json()
    assert corpo["erro"] == "Parâmetros inválidos." and set(corpo["campos"]) == campos
    eventos = _da_requisicao(capsys, resposta)
    assert [(e["layer"], e["level"], e["client"], e["operation"]) for e in eventos] == [
        ("api", "WARN", "cli", "exportar_vendas")
    ]


def test_mesmos_erros_de_filtro_da_web(cliente_http, cabecalho_cli, cabecalho_web):
    consulta = "produto_id=abc&data_inicio=2026-10-31&data_fim=2026-10-01&ordenar=preco"
    web = _exportar(cliente_http, cabecalho_web, consulta, rota="/api/web/records/export")
    assert _exportar(cliente_http, cabecalho_cli, consulta).get_json() == web.get_json()


def test_exportacao_correlaciona_logs_api_e_banco(cliente_http, cabecalho_cli, token_cli, vendas, capsys):
    capsys.readouterr()
    resposta = _exportar(cliente_http, cabecalho_cli, "categoria=Alimentos&data_inicio=2026-10-01")
    saida = capsys.readouterr().out
    assert token_cli not in saida
    eventos = [e for e in (json.loads(linha) for linha in saida.splitlines() if linha.startswith("{")) if e["request_id"] == resposta.headers["X-Request-ID"]]
    assert {(e["layer"], e["operation"], e["status"], e["client"], e["level"]) for e in eventos} == {
        ("api", "exportar_vendas", "success", "cli", "INFO"),
        ("db", "exportar_vendas", "success", "cli", "INFO"),
    }
    for evento in eventos:
        assert "5 vendas" in evento["message"] and "(categoria, data_inicio)" in evento["message"]
        assert set(evento) == {"timestamp", "level", "layer", "client", "operation", "status", "request_id", "message"}


def test_exportacao_erro_no_banco(cliente_http, cabecalho_cli, monkeypatch, capsys):
    monkeypatch.setattr(db, "exportar_vendas_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = _exportar(cliente_http, cabecalho_cli)
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}
    evento = eventos_do_log(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["client"], evento["operation"]) == (
        "api",
        "ERROR",
        "cli",
        "exportar_vendas",
    )


# --- banco ----------------------------------------------------------------------------------------


def test_db_exportar_vendas_cli_combina_filtros_sem_paginar(vendas, capsys):
    filtros = {"categoria": "Alimentos", "data_fim": date(2026, 10, 31)}
    linhas = db.exportar_vendas_cli(filtros, "id", "asc", "req-exportar")
    assert [linha["id"] for linha in linhas] == [vendas["arroz_set"], vendas["arroz_out"], vendas["feijao_out"]]
    assert set(linhas[0]) == {
        "id", "data_venda", "produto_nome", "categoria", "quantidade", "valor_unitario", "valor_total"
    }
    evento = eventos_do_log(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["client"], evento["operation"], evento["request_id"]) == (
        "db",
        "INFO",
        "cli",
        "exportar_vendas",
        "req-exportar",
    )
    assert evento["message"] == "3 vendas lidas para exportação (categoria, data_fim)"


def test_db_exportar_vendas_cli_sem_filtros(vendas, capsys):
    linhas = db.exportar_vendas_cli({}, "data_venda", "desc", "req-todas")
    assert len(linhas) == len(vendas)
    assert eventos_do_log(capsys)[-1]["message"] == f"{len(vendas)} vendas lidas para exportação (sem filtros)"


def test_db_exportar_vendas_cli_erro_registra_log(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.exportar_vendas_cli({}, "id", "asc", "req-erro")
    evento = eventos_do_log(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["client"], evento["operation"]) == (
        "db",
        "ERROR",
        "cli",
        "exportar_vendas",
    )


def test_busca_da_cli_continua_igual_apos_extrair_o_where(vendas):
    filtros = {"categoria": "Bebidas", "data_inicio": date(2026, 10, 1)}
    linhas, total = db.buscar_vendas_cli(filtros, 1, 100, "id", "asc", "req-busca")
    exportadas = db.exportar_vendas_cli(filtros, "id", "asc", "req-exportar")
    assert total == 3 and [linha["id"] for linha in linhas] == [linha["id"] for linha in exportadas]


# --- comando `exportar` (contra a API num servidor HTTP real) -------------------------------------


@pytest.fixture
def com_token(servidor_api, token_cli, monkeypatch):
    monkeypatch.setenv("TOKEN_CLI", token_cli)


def _erro(capsys):
    """stderr da CLI, sem as linhas de log da API (que roda no mesmo processo durante o teste)."""
    return "\n".join(linha for linha in capsys.readouterr().err.splitlines() if not linha.startswith("{"))


def test_exportar_para_a_saida_padrao_igual_ao_csv_da_web(com_token, cliente_http, cabecalho_web, vendas, capsys):
    consulta = "categoria=Alimentos&data_inicio=2026-10-01&ordenar=valor_total&direcao=asc"
    web = _exportar(cliente_http, cabecalho_web, consulta, rota="/api/web/records/export").get_data()
    capsys.readouterr()
    argv = ["exportar", "--categoria", "Alimentos", "--data-inicio", "2026-10-01", "--ordenar", "valor_total", "--direcao", "asc"]
    assert cli.main(argv) == cli.SAIDA_SUCESSO
    capturado = capsys.readouterr()
    # Os bytes da API vão sem mudança (BOM e CRLF); os logs da API de teste ficam antes, em outras linhas.
    assert web.decode("utf-8") in capturado.out
    assert capturado.err.strip().splitlines()[-1] == "5 vendas exportadas para a saída padrão."


def test_exportar_para_arquivo_com_tres_filtros(com_token, vendas, tmp_path, capsys):
    destino = tmp_path / "vendas.csv"
    argv = [
        "exportar", "--categoria", "Alimentos", "--data-inicio", "2026-10-01", "--data-fim", "2026-10-31",
        "--arquivo", str(destino),
    ]
    assert cli.main(argv) == cli.SAIDA_SUCESSO
    cabecalho, linhas = _linhas_csv(destino.read_bytes())
    assert cabecalho == CABECALHO
    assert _ids(linhas) == [vendas["feijao_out"], vendas["arroz_out"]]  # data mais recente primeiro
    assert _erro(capsys).splitlines()[-1] == f"2 vendas exportadas para {destino}."


def test_exportar_sem_filtros_traz_todas_alem_de_uma_pagina(com_token, vendas, tmp_path, capsys):
    destino = tmp_path / "todas.csv"
    assert cli.main(["exportar", "-a", str(destino)]) == cli.SAIDA_SUCESSO
    assert sorted(_ids(_linhas_csv(destino.read_bytes())[1])) == sorted(vendas.values())
    assert "12 vendas exportadas" in _erro(capsys)


def test_exportar_por_produto_uma_venda_no_singular(com_token, vendas, tmp_path, capsys):
    destino = tmp_path / "suco.csv"
    argv = ["exportar", "--produto", str(_id_produto("Suco de laranja 1 L")), "--arquivo", str(destino)]
    assert cli.main(argv) == cli.SAIDA_SUCESSO
    assert _ids(_linhas_csv(destino.read_bytes())[1]) == [vendas["suco_out"]]
    assert _erro(capsys).splitlines()[-1] == f"1 venda exportada para {destino}."


def test_exportar_sem_resultado_gera_so_o_cabecalho(com_token, vendas, tmp_path, capsys):
    destino = tmp_path / "vazio.csv"
    assert cli.main(["exportar", "--categoria", "Limpeza", "--arquivo", str(destino)]) == cli.SAIDA_SUCESSO
    assert _linhas_csv(destino.read_bytes()) == (CABECALHO, [])
    assert "0 vendas exportadas" in _erro(capsys)


def test_exportar_categoria_com_e_comercial_vai_codificada(com_token, vendas, tmp_path, capsys):
    destino = tmp_path / "vendas.csv"
    assert cli.main(["exportar", "--categoria", "Higiene & Beleza", "-a", str(destino)]) == cli.SAIDA_SUCESSO
    assert _linhas_csv(destino.read_bytes()) == (CABECALHO, [])


def test_exportar_filtros_invalidos_mostra_a_opcao_e_nao_cria_arquivo(com_token, tmp_path, capsys):
    destino = tmp_path / "vendas.csv"
    argv = ["exportar", "--produto", "abc", "--data-inicio", "2026-10-31", "--data-fim", "2026-10-01", "-a", str(destino)]
    assert cli.main(argv) == cli.SAIDA_ERRO
    erro = _erro(capsys).splitlines()
    assert erro[0] == "Erro: Parâmetros inválidos. (HTTP 400)"
    assert "  --produto: O produto deve ser o número de um produto do catálogo." in erro
    assert "  --data-fim: A data final não pode ser anterior à data inicial." in erro
    assert not destino.exists()


@pytest.mark.parametrize("argv", [["exportar", "--ordenar", "preco"], ["exportar", "--direcao", "cima"]])
def test_exportar_escolha_invalida_e_erro_de_uso(servidor_api, capsys, argv):
    with pytest.raises(SystemExit) as saida:
        cli.main(argv)
    assert saida.value.code == 2
    erro = capsys.readouterr().err
    assert "Erro: argumento" in erro and "escolha inválida" in erro


def test_exportar_sem_token_nao_chama_a_api(servidor_api, tmp_path, capsys):
    destino = tmp_path / "vendas.csv"
    assert cli.main(["exportar", "-a", str(destino)]) == cli.SAIDA_NAO_AUTENTICADO
    assert "comando protegido" in _erro(capsys) and not destino.exists()


def test_exportar_com_token_invalido_sai_com_codigo_3(servidor_api, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TOKEN_CLI", "token-falso")
    destino = tmp_path / "vendas.csv"
    assert cli.main(["exportar", "-a", str(destino)]) == cli.SAIDA_NAO_AUTENTICADO
    assert "HTTP 401" in _erro(capsys) and not destino.exists()


def test_exportar_para_pasta_inexistente_mostra_o_erro(com_token, vendas, tmp_path, capsys):
    destino = tmp_path / "nao-existe" / "vendas.csv"
    assert cli.main(["exportar", "--arquivo", str(destino)]) == cli.SAIDA_ERRO
    assert f"Erro: não foi possível gravar {destino} (No such file or directory)." in _erro(capsys)


class _RespostaFixa(io.BytesIO):
    """Resposta HTTP 200 com o corpo e o Content-Type dados, usável no `with` como a de `urlopen`."""

    def __init__(self, corpo, tipo):
        super().__init__(corpo)
        self.headers = {"Content-Type": tipo}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


@pytest.mark.parametrize(
    "corpo, tipo, mensagem",
    [
        (b"<html></html>", "text/html; charset=utf-8", "(a resposta não é CSV)"),
        (b"\xff\xfe;;", "text/csv; charset=utf-8", "(o CSV recebido é inválido)"),
        (b"", "text/csv; charset=utf-8", "(o CSV recebido não tem o cabeçalho esperado)"),
        ("\r\nNº;Data\r\n".encode(), "text/csv", "(o CSV recebido não tem o cabeçalho esperado)"),
        (b"id;data\r\n1;2\r\n", "text/csv", "(o CSV recebido não tem o cabeçalho esperado)"),
    ],
)
def test_exportar_resposta_que_nao_e_o_csv_esperado(monkeypatch, tmp_path, capsys, corpo, tipo, mensagem):
    monkeypatch.setenv("TOKEN_CLI", "token")
    monkeypatch.setattr(cli, "abrir_url", lambda _requisicao: _RespostaFixa(corpo, tipo))
    destino = tmp_path / "vendas.csv"
    assert cli.main(["exportar", "-a", str(destino)]) == cli.SAIDA_ERRO
    assert mensagem in capsys.readouterr().err and not destino.exists()


def test_exportar_pede_csv_a_api(monkeypatch, capsys):
    recebidas = []

    def responder(requisicao):
        recebidas.append((requisicao.full_url, requisicao.get_header("Accept")))
        return _RespostaFixa("﻿Nº;Data\r\n".encode(), "text/csv; charset=utf-8")

    monkeypatch.setenv("TOKEN_CLI", "token")
    monkeypatch.setattr(cli, "abrir_url", responder)
    assert cli.main(["exportar", "--categoria", "Bebidas", "--direcao", "asc"]) == cli.SAIDA_SUCESSO
    url, aceitar = recebidas[0]
    assert url.endswith("/api/cli/records/export?categoria=Bebidas&direcao=asc") and aceitar == "text/csv"
    assert capsys.readouterr().err == "0 vendas exportadas para a saída padrão.\n"


def test_ajuda_lista_exportar_e_as_opcoes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "exportar" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["exportar", "--help"])
    ajuda = capsys.readouterr().out
    assert all(
        opcao in ajuda
        for opcao in ("--produto", "--categoria", "--data-inicio", "--data-fim", "--ordenar", "--direcao", "--arquivo")
    )
    assert "--pagina" not in ajuda and "--tamanho" not in ajuda
