"""Testes da busca avançada com filtros combináveis pela CLI (issue #10): rota /api/cli/records/search, banco e cli.py."""

from datetime import date

import psycopg
import pytest

import cli
import db
from conftest import SENHA_TESTE, USUARIO_TESTE, eventos_do_log


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
    """10 vendas de 3 categorias em datas diferentes (as mesmas da busca da Web). Retorna {apelido: id}."""
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
    return cliente_http.get(f"/api/cli/records/search?{consulta}", headers=cabecalho)


def _ids_encontrados(cliente_http, cabecalho, rota, consulta):
    """Percorre todas as páginas da busca `rota` (ordenada por id) e devolve os ids encontrados."""
    ids, pagina = [], 1
    while True:
        corpo = cliente_http.get(
            f"{rota}?{consulta}&ordenar=id&direcao=asc&pagina={pagina}", headers=cabecalho
        ).get_json()
        ids += [venda["id"] for venda in corpo["vendas"]]
        if pagina >= corpo["total_paginas"]:
            assert len(ids) == corpo["total"]
            return ids
        pagina += 1


# --- API: autenticação ----------------------------------------------------------------------------


@pytest.mark.parametrize("cabecalho", [None, "Bearer invalido", "Basic abc"])
def test_busca_exige_token_da_cli(cliente_http, cabecalho, monkeypatch, capsys):
    monkeypatch.setattr(db, "buscar_vendas_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = _buscar(cliente_http, {"Authorization": cabecalho} if cabecalho else {}, "categoria=Bebidas")
    assert resposta.status_code == 401 and resposta.get_json() == {"erro": "Não autenticado"}
    eventos = _da_requisicao(capsys, resposta)
    assert [(e["layer"], e["level"], e["client"], e["operation"]) for e in eventos] == [
        ("api", "WARN", "cli", "buscar_vendas")
    ]


def test_token_da_web_nao_vale_na_busca_da_cli(cliente_http, cabecalho_web):
    assert _buscar(cliente_http, cabecalho_web).status_code == 401


# --- API: filtros ---------------------------------------------------------------------------------


def test_sem_filtros_retorna_todas_com_padrao_da_cli(cliente_http, cabecalho_cli, vendas):
    corpo = _buscar(cliente_http, cabecalho_cli).get_json()
    assert {k: corpo[k] for k in ("pagina", "itens_por_pagina", "total", "total_paginas", "ordenar", "direcao")} == {
        "pagina": 1,
        "itens_por_pagina": 10,
        "total": 10,
        "total_paginas": 1,
        "ordenar": "data_venda",
        "direcao": "desc",
    }
    assert corpo["filtros"] == {"produto_id": None, "categoria": None, "data_inicio": None, "data_fim": None}
    assert corpo["vendas"][0] == {
        "id": vendas["sabonete_nov"],
        "produto_id": _id_produto("Sabonete"),
        "produto_nome": "Sabonete",
        "categoria": "Higiene",
        "quantidade": 5,
        "valor_unitario": "2.50",
        "valor_total": "12.50",
        "data_venda": "2026-11-10",
    }


@pytest.mark.parametrize(
    "consulta, esperados",
    [
        ("categoria=Bebidas", {"refri_set", "refri_out", "suco_out"}),
        ("data_inicio=2026-11-01", {"arroz_nov", "feijao_nov", "sabonete_nov"}),
        ("data_fim=2026-09-30", {"arroz_set", "refri_set"}),
        ("data_inicio=2026-10-05&data_fim=2026-10-15", {"arroz_out", "feijao_out", "sabonete_out"}),
        ("categoria=Alimentos&data_inicio=2026-10-01&data_fim=2026-10-31", {"arroz_out", "feijao_out"}),
        ("categoria=Higiene&data_inicio=2026-11-10&data_fim=2026-11-10", {"sabonete_nov"}),
        ("categoria=Limpeza", set()),
        ("categoria=bebidas", set()),  # nome exato, como na Web
        ("categoria=Bebidas&data_fim=2026-09-01", set()),
    ],
)
def test_filtros_combinados_com_e(cliente_http, cabecalho_cli, vendas, consulta, esperados):
    ids = _ids_encontrados(cliente_http, cabecalho_cli, "/api/cli/records/search", consulta)
    assert ids == sorted(vendas[apelido] for apelido in esperados)


def test_filtro_por_produto_combinado_com_periodo(cliente_http, cabecalho_cli, vendas):
    arroz = _id_produto("Arroz 5 kg")
    corpo = _buscar(cliente_http, cabecalho_cli, f"produto_id={arroz}&data_inicio=2026-10-01").get_json()
    assert {v["id"] for v in corpo["vendas"]} == {vendas["arroz_out"], vendas["arroz_nov"]}
    assert corpo["filtros"] == {"produto_id": arroz, "categoria": None, "data_inicio": "2026-10-01", "data_fim": None}


def test_quatro_filtros_juntos(cliente_http, cabecalho_cli, vendas):
    feijao = _id_produto("Feijão 1 kg")
    consulta = f"produto_id={feijao}&categoria=Alimentos&data_inicio=2026-10-01&data_fim=2026-10-31"
    corpo = _buscar(cliente_http, cabecalho_cli, consulta).get_json()
    assert [v["id"] for v in corpo["vendas"]] == [vendas["feijao_out"]] and corpo["total"] == 1


def test_produto_e_categoria_que_nao_combinam_nao_encontram_nada(cliente_http, cabecalho_cli, vendas):
    consulta = f"produto_id={_id_produto('Arroz 5 kg')}&categoria=Bebidas"
    corpo = _buscar(cliente_http, cabecalho_cli, consulta).get_json()
    assert corpo["vendas"] == [] and corpo["total"] == 0 and corpo["total_paginas"] == 1


def test_filtros_vazios_nao_restringem(cliente_http, cabecalho_cli, vendas):
    corpo = _buscar(cliente_http, cabecalho_cli, "produto_id=&categoria=&data_inicio=&data_fim=").get_json()
    assert corpo["total"] == 10
    assert corpo["filtros"] == {"produto_id": None, "categoria": None, "data_inicio": None, "data_fim": None}


def test_produto_sem_cadastro_nao_encontra_nada(cliente_http, cabecalho_cli, vendas):
    corpo = _buscar(cliente_http, cabecalho_cli, "produto_id=999999").get_json()
    assert corpo["total"] == 0 and corpo["filtros"]["produto_id"] == 999999


# --- API: equivalência com a busca da Web (#4) ----------------------------------------------------


@pytest.mark.parametrize(
    "consulta",
    [
        "",
        "categoria=Bebidas",
        "categoria=Alimentos&data_inicio=2026-10-01&data_fim=2026-10-31",
        "data_inicio=2026-10-05&data_fim=2026-10-15",
        "data_fim=2026-10-12",
        "categoria=Higiene&data_inicio=2026-11-01",
        "categoria=Limpeza",
        "produto_id=999999",
    ],
)
def test_mesmos_parametros_mesmo_resultado_da_web(cliente_http, cabecalho_cli, cabecalho_web, vendas, consulta):
    web = _ids_encontrados(cliente_http, cabecalho_web, "/api/web/records/search", consulta)
    cli_ = _ids_encontrados(cliente_http, cabecalho_cli, "/api/cli/records/search", consulta)
    assert cli_ == web


@pytest.mark.parametrize("produto", ["Arroz 5 kg", "Sabonete", "Detergente 500 ml"])
def test_mesmo_produto_mesmo_resultado_da_web(cliente_http, cabecalho_cli, cabecalho_web, vendas, produto):
    consulta = f"produto_id={_id_produto(produto)}&data_inicio=2026-10-01"
    web = _ids_encontrados(cliente_http, cabecalho_web, "/api/web/records/search", consulta)
    assert _ids_encontrados(cliente_http, cabecalho_cli, "/api/cli/records/search", consulta) == web


def test_mesma_ordem_e_mesmas_vendas_da_web(cliente_http, cabecalho_cli, cabecalho_web, vendas):
    consulta = "categoria=Alimentos&ordenar=valor_total&direcao=desc"
    web = cliente_http.get(f"/api/web/records/search?{consulta}", headers=cabecalho_web).get_json()
    cli_ = _buscar(cliente_http, cabecalho_cli, consulta).get_json()
    assert cli_["vendas"] == web["vendas"] and cli_["filtros"] == web["filtros"] and cli_["total"] == web["total"]


@pytest.mark.parametrize(
    "consulta",
    [
        "produto_id=abc",
        "categoria=a%00b",
        "data_inicio=2026-13-01",
        "data_inicio=2026-10-31&data_fim=2026-10-01",
    ],
)
def test_mesmos_erros_de_filtro_da_web(cliente_http, cabecalho_cli, cabecalho_web, consulta):
    web = cliente_http.get(f"/api/web/records/search?{consulta}", headers=cabecalho_web)
    cli_ = _buscar(cliente_http, cabecalho_cli, consulta)
    assert cli_.status_code == web.status_code == 400
    assert cli_.get_json() == web.get_json()


# --- API: paginação, ordenação e validação --------------------------------------------------------


def test_paginacao_e_tamanho_sobre_o_resultado(cliente_http, cabecalho_cli, vendas):
    ids = []
    for pagina in (1, 2):
        corpo = _buscar(
            cliente_http, cabecalho_cli, f"data_inicio=2026-10-01&tamanho=4&pagina={pagina}&ordenar=id&direcao=asc"
        ).get_json()
        assert corpo["total"] == 8 and corpo["total_paginas"] == 2 and corpo["itens_por_pagina"] == 4
        ids += [v["id"] for v in corpo["vendas"]]
    assert len(ids) == 8 == len(set(ids))


def test_pagina_alem_do_fim_vem_vazia(cliente_http, cabecalho_cli, vendas):
    corpo = _buscar(cliente_http, cabecalho_cli, "categoria=Bebidas&pagina=3&tamanho=2").get_json()
    assert corpo["vendas"] == [] and corpo["total"] == 3 and corpo["total_paginas"] == 2


def test_ordenacao_por_quantidade(cliente_http, cabecalho_cli, vendas):
    corpo = _buscar(cliente_http, cabecalho_cli, "categoria=Alimentos&ordenar=quantidade&direcao=asc").get_json()
    assert [v["quantidade"] for v in corpo["vendas"]] == [1, 1, 2, 3, 4]


def test_empate_e_desfeito_pelo_id(cliente_http, cabecalho_cli):
    ids = [_inserir_venda("Sabonete", 1, "2.50", "2026-10-01") for _ in range(3)]
    corpo = _buscar(cliente_http, cabecalho_cli, "categoria=Higiene&ordenar=data_venda&direcao=asc").get_json()
    assert [v["id"] for v in corpo["vendas"]] == ids


@pytest.mark.parametrize(
    "consulta, campos",
    [
        ("produto_id=abc", {"produto_id"}),
        ("produto_id=0", {"produto_id"}),
        ("produto_id=-1", {"produto_id"}),
        ("produto_id=" + "9" * 5000, {"produto_id"}),  # não pode virar 500 no int()
        ("categoria=a%00b", {"categoria"}),
        ("data_inicio=01/10/2026", {"data_inicio"}),
        ("data_fim=2026-02-30", {"data_fim"}),
        ("data_inicio=2026-10-31&data_fim=2026-10-01", {"data_fim"}),
        ("tamanho=101", {"tamanho"}),
        ("pagina=0", {"pagina"}),
        ("ordenar=senha_hash", {"ordenar"}),
        ("direcao=cima", {"direcao"}),
        ("produto_id=x&data_inicio=x&tamanho=0", {"produto_id", "data_inicio", "tamanho"}),
    ],
)
def test_parametros_invalidos_400_sem_chegar_ao_banco(cliente_http, cabecalho_cli, monkeypatch, capsys, consulta, campos):
    monkeypatch.setattr(db, "buscar_vendas_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = _buscar(cliente_http, cabecalho_cli, consulta)
    assert resposta.status_code == 400
    corpo = resposta.get_json()
    assert corpo["erro"] == "Parâmetros inválidos." and set(corpo["campos"]) == campos
    eventos = _da_requisicao(capsys, resposta)
    assert [(e["layer"], e["level"], e["client"], e["operation"]) for e in eventos] == [
        ("api", "WARN", "cli", "buscar_vendas")
    ]


# --- API: logs e erros ----------------------------------------------------------------------------


def test_busca_correlaciona_logs_api_e_banco(cliente_http, cabecalho_cli, vendas, capsys):
    capsys.readouterr()
    resposta = _buscar(cliente_http, cabecalho_cli, "categoria=Bebidas&data_inicio=2026-10-01")
    eventos = _da_requisicao(capsys, resposta)
    assert {(e["layer"], e["operation"], e["status"], e["client"]) for e in eventos} == {
        ("api", "buscar_vendas", "success", "cli"),
        ("db", "buscar_vendas", "success", "cli"),
    }
    api_evento = next(e for e in eventos if e["layer"] == "api")
    assert api_evento["message"] == "Busca (categoria, data_inicio): 2 vendas, página 1 de 1"


def test_busca_erro_no_banco(cliente_http, cabecalho_cli, monkeypatch, capsys):
    monkeypatch.setattr(db, "buscar_vendas_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = _buscar(cliente_http, cabecalho_cli, "categoria=Bebidas")
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}
    evento = eventos_do_log(capsys)[-1]
    assert (evento["level"], evento["client"], evento["operation"]) == ("ERROR", "cli", "buscar_vendas")


# --- banco ----------------------------------------------------------------------------------------


def test_db_buscar_vendas_cli_filtra_pagina_e_registra_log(vendas, capsys):
    filtros = {"categoria": "Alimentos", "data_inicio": date(2026, 10, 1)}
    linhas, total = db.buscar_vendas_cli(filtros, 1, 2, "id", "asc", "req-busca")
    assert total == 4 and [linha["id"] for linha in linhas] == [vendas["arroz_out"], vendas["arroz_nov"]]
    evento = eventos_do_log(capsys)[-1]
    assert (evento["layer"], evento["client"], evento["operation"], evento["request_id"]) == (
        "db",
        "cli",
        "buscar_vendas",
        "req-busca",
    )
    assert evento["message"] == "Página 1: 2 de 4 vendas (categoria, data_inicio)"


def test_db_buscar_vendas_cli_sem_filtros(vendas, capsys):
    linhas, total = db.buscar_vendas_cli({}, 1, 100, "data_venda", "desc", "req-todas")
    assert total == 10 and len(linhas) == 10
    assert eventos_do_log(capsys)[-1]["message"] == "Página 1: 10 de 10 vendas (sem filtros)"


def test_db_buscar_vendas_cli_erro_registra_log(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.buscar_vendas_cli({}, 1, 10, "id", "asc", "req-erro")
    evento = eventos_do_log(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["client"], evento["operation"]) == (
        "db",
        "ERROR",
        "cli",
        "buscar_vendas",
    )


def test_filtros_da_cli_iguais_aos_da_web():
    assert db.CONDICOES_BUSCA_CLI == db.CONDICOES_BUSCA_WEB
    assert set(cli.OPCOES_BUSCA) == set(db.CAMPOS_BUSCA_CLI)


# --- comando `buscar` (contra a API num servidor HTTP real) ---------------------------------------


def _saida(capsys):
    """Saída e erro da CLI, sem as linhas de log da API (que roda no mesmo processo durante o teste)."""
    capturado = capsys.readouterr()
    sem_log = lambda texto: [linha for linha in texto.splitlines() if not linha.startswith("{")]  # noqa: E731
    return sem_log(capturado.out), "\n".join(sem_log(capturado.err))


@pytest.fixture
def com_token(servidor_api, token_cli, monkeypatch):
    monkeypatch.setenv("TOKEN_CLI", token_cli)


def test_buscar_com_tres_filtros(com_token, vendas, capsys):
    argv = ["buscar", "--categoria", "Alimentos", "--data-inicio", "2026-10-01", "--data-fim", "2026-10-31"]
    assert cli.main(argv) == cli.SAIDA_SUCESSO
    saida, _ = _saida(capsys)
    assert saida[0] == "Filtros: categoria Alimentos, de 01/10/2026 até 31/10/2026"
    assert saida[1].split() == ["Nº", "Data", "Produto", "Categoria", "Qtd.", "Valor", "total"]
    assert "12/10/2026" in saida[2] and "Feijão 1 kg" in saida[2] and saida[2].endswith("R$ 34,00")
    assert "05/10/2026" in saida[3] and "Arroz 5 kg" in saida[3] and saida[3].endswith("R$ 55,80")
    assert saida[-1] == "Página 1 de 1 · 2 vendas encontradas · 10 por página · ordenado por data_venda (decrescente)"
    assert len(saida) == 5  # filtros + cabeçalho + 2 vendas + rodapé


def test_buscar_por_produto_com_paginacao_e_ordenacao(com_token, vendas, capsys):
    argv = ["buscar", "--produto", str(_id_produto("Arroz 5 kg")), "--tamanho", "2", "--ordenar", "id", "--direcao", "asc"]
    assert cli.main(argv) == cli.SAIDA_SUCESSO
    saida, _ = _saida(capsys)
    assert saida[0] == f"Filtros: produto {_id_produto('Arroz 5 kg')}"
    assert [linha.split()[0] for linha in saida[2:-1]] == [str(vendas["arroz_set"]), str(vendas["arroz_out"])]
    assert saida[-1] == "Página 1 de 2 · 3 vendas encontradas · 2 por página · ordenado por id (crescente)"


def test_buscar_sem_filtros_mostra_todas(com_token, vendas, capsys):
    assert cli.main(["buscar"]) == cli.SAIDA_SUCESSO
    saida, _ = _saida(capsys)
    assert saida[0] == "Filtros: nenhum (todas as vendas)"
    assert "· 10 vendas encontradas ·" in saida[-1]


def test_buscar_uma_venda_no_singular(com_token, vendas, capsys):
    cli.main(["buscar", "--categoria", "Higiene", "--data-inicio", "2026-11-01"])
    saida, _ = _saida(capsys)
    assert saida[0] == "Filtros: categoria Higiene, a partir de 01/11/2026"
    assert "· 1 venda encontrada ·" in saida[-1]


def test_buscar_sem_resultado(com_token, vendas, capsys):
    assert cli.main(["buscar", "--categoria", "Limpeza", "--data-fim", "2026-10-31"]) == cli.SAIDA_SUCESSO
    assert _saida(capsys)[0] == [
        "Filtros: categoria Limpeza, até 31/10/2026",
        "Nenhuma venda encontrada com esses filtros.",
        "Página 1 de 1 · 0 vendas encontradas · 10 por página · ordenado por data_venda (decrescente)",
    ]


def test_buscar_pagina_que_nao_existe(com_token, vendas, capsys):
    assert cli.main(["buscar", "--categoria", "Bebidas", "--pagina", "5", "--tamanho", "2"]) == cli.SAIDA_SUCESSO
    assert _saida(capsys)[0] == ["Filtros: categoria Bebidas", "A página 5 não existe: há 2 páginas."]


def test_buscar_mostra_as_mesmas_vendas_da_web(com_token, cliente_http, cabecalho_web, vendas, capsys):
    consulta = "categoria=Alimentos&data_inicio=2026-10-01&ordenar=id&direcao=asc"
    web = cliente_http.get(f"/api/web/records/search?{consulta}", headers=cabecalho_web).get_json()
    argv = ["buscar", "--categoria", "Alimentos", "--data-inicio", "2026-10-01", "--ordenar", "id", "--direcao", "asc"]
    capsys.readouterr()
    cli.main(argv)
    saida, _ = _saida(capsys)
    assert [int(linha.split()[0]) for linha in saida[2:-1]] == [v["id"] for v in web["vendas"]]


def test_buscar_filtros_invalidos_mostra_a_opcao(com_token, capsys):
    argv = ["buscar", "--produto", "abc", "--data-inicio", "2026-10-31", "--data-fim", "2026-10-01", "--tamanho", "0"]
    assert cli.main(argv) == cli.SAIDA_ERRO
    erro = _saida(capsys)[1].splitlines()
    assert erro[0] == "Erro: Parâmetros inválidos. (HTTP 400)"
    assert "  --produto: O produto deve ser o número de um produto do catálogo." in erro
    assert "  --data-fim: A data final não pode ser anterior à data inicial." in erro
    assert "  --tamanho: O tamanho da página deve ser um número inteiro entre 1 e 100." in erro


def test_buscar_data_invalida_mostra_a_opcao(com_token, capsys):
    assert cli.main(["buscar", "--data-inicio", "01/10/2026"]) == cli.SAIDA_ERRO
    assert "  --data-inicio: Data inicial inválida; use o formato AAAA-MM-DD." in _saida(capsys)[1]


def test_buscar_categoria_com_espaco_e_acento_vai_codificada(com_token, capsys):
    # "Higiene & Beleza" não existe: a busca só não encontra nada (o "&" não vira outro parâmetro).
    assert cli.main(["buscar", "--categoria", "Higiene & Beleza"]) == cli.SAIDA_SUCESSO
    saida, _ = _saida(capsys)
    assert saida[:2] == ["Filtros: categoria Higiene & Beleza", "Nenhuma venda encontrada com esses filtros."]


@pytest.mark.parametrize("argv", [["buscar", "--ordenar", "preco"], ["buscar", "--direcao", "cima"]])
def test_buscar_escolha_invalida_e_erro_de_uso(servidor_api, capsys, argv):
    with pytest.raises(SystemExit) as saida:
        cli.main(argv)
    assert saida.value.code == 2
    erro = capsys.readouterr().err
    assert "Erro: argumento" in erro and "escolha inválida" in erro


def test_buscar_sem_token(servidor_api, capsys):
    assert cli.main(["buscar", "--categoria", "Bebidas"]) == cli.SAIDA_NAO_AUTENTICADO
    assert "comando protegido" in _saida(capsys)[1]


def test_buscar_com_token_invalido_sai_com_codigo_3(servidor_api, monkeypatch, capsys):
    monkeypatch.setenv("TOKEN_CLI", "token-falso")
    assert cli.main(["buscar"]) == cli.SAIDA_NAO_AUTENTICADO
    assert "HTTP 401" in _saida(capsys)[1]


def test_ajuda_lista_buscar_e_os_filtros(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "buscar" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["buscar", "--help"])
    ajuda = capsys.readouterr().out
    assert all(opcao in ajuda for opcao in ("--produto", "--categoria", "--data-inicio", "--data-fim", "--tamanho"))
