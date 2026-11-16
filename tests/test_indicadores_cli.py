"""Testes dos indicadores pela CLI (issue #12): rota /api/cli/dashboard, banco e comando `indicadores`."""

import json
from datetime import date

import psycopg
import pytest

import api
import cli
import db
from conftest import SENHA_TESTE, USUARIO_TESTE, RespostaFixa, eventos_do_log

HOJE = date(2026, 10, 31)


def _falhar_no_banco(*_args):
    raise psycopg.OperationalError("banco fora do ar")


def _id_produto(nome):
    with db.obter_conexao() as conexao:
        return conexao.execute("SELECT id FROM produto WHERE nome = %s", (nome,)).fetchone()["id"]


def _inserir_venda(produto, quantidade, valor_unitario, data_venda):
    with db.obter_conexao() as conexao:
        conexao.execute(
            "INSERT INTO venda (produto_id, quantidade, valor_unitario, data_venda) VALUES (%s, %s, %s, %s)",
            (_id_produto(produto), quantidade, valor_unitario, data_venda),
        )


def _meses_esperados(com_valor):
    """Os 12 meses de nov/2025 a out/2026, com zero onde `com_valor` não tem o mês."""
    meses = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 11)]
    return [{"mes": mes, "total": com_valor.get(mes, "0.00")} for mes in meses]


def _saida(capsys):
    """Saída e erro da CLI, sem as linhas de log da API (que roda no mesmo processo durante o teste)."""
    capturado = capsys.readouterr()
    sem_log = lambda texto: [linha for linha in texto.splitlines() if not linha.startswith("{")]  # noqa: E731
    return sem_log(capturado.out), "\n".join(sem_log(capturado.err))


@pytest.fixture(autouse=True)
def hoje_fixo(monkeypatch):
    """A janela de 12 meses termina no mês de hoje: fixa a data para os testes não dependerem do relógio."""
    monkeypatch.setattr(api, "hoje", lambda: HOJE)


@pytest.fixture(autouse=True)
def vendas_limpas():
    """Cada teste começa sem vendas, para os indicadores serem previsíveis."""
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
def com_token(servidor_api, token_cli, monkeypatch):
    monkeypatch.setenv("TOKEN_CLI", token_cli)


@pytest.fixture
def vendas():
    """As mesmas vendas dos testes do dashboard da Web (#6): 3 meses e 4 categorias, totais conferidos à mão."""
    dados = [
        ("Arroz 5 kg", 2, "27.90", "2026-08-10"),  # Alimentos 55.80
        ("Feijão 1 kg", 10, "8.50", "2026-09-02"),  # Alimentos 85.00
        ("Sabonete", 6, "2.50", "2026-09-15"),  # Higiene 15.00
        ("Refrigerante 2 L", 3, "9.90", "2026-10-01"),  # Bebidas 29.70
        ("Sabonete", 4, "2.50", "2026-10-20"),  # Higiene 10.00
        ("Detergente 500 ml", 1, "2.99", "2026-10-31"),  # Limpeza 2.99
    ]
    for venda in dados:
        _inserir_venda(*venda)


# --- autenticação da rota -------------------------------------------------------------------------


@pytest.mark.parametrize("cabecalho_auth", [None, "Bearer invalido", "Basic abc"])
def test_rota_exige_token_da_cli(cliente_http, cabecalho_auth, capsys):
    cabecalhos = {"Authorization": cabecalho_auth} if cabecalho_auth else {}
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/dashboard", headers=cabecalhos)
    assert resposta.status_code == 401
    # Recusada antes do banco: nenhum evento da camada db.
    assert all(e["layer"] == "api" and e["client"] == "cli" for e in eventos_do_log(capsys))


def test_rota_recusa_token_da_web(cliente_http, cabecalho_web):
    assert cliente_http.get("/api/cli/dashboard", headers=cabecalho_web).status_code == 401


def test_token_da_cli_nao_abre_o_dashboard_da_web(cliente_http, cabecalho_cli):
    assert cliente_http.get("/api/web/dashboard", headers=cabecalho_cli).status_code == 401


# --- indicadores na API ---------------------------------------------------------------------------


def test_rota_sem_vendas(cliente_http, cabecalho_cli):
    corpo = cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli).get_json()
    assert corpo["resumo"] == {"total_vendas": 0, "faturamento_total": "0.00"}
    assert corpo["faturamento_por_mes"] == _meses_esperados({})
    assert corpo["top_produtos"] == []
    # Categorias do catálogo aparecem mesmo sem vendas, com total zero.
    assert {c["categoria"] for c in corpo["faturamento_por_categoria"]} == {c for _, c, _ in db.PRODUTOS_INICIAIS}
    assert {c["total"] for c in corpo["faturamento_por_categoria"]} == {"0.00"}


def test_rota_com_vendas(cliente_http, cabecalho_cli, vendas):
    resposta = cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli)
    assert resposta.status_code == 200
    corpo = resposta.get_json()
    assert corpo["resumo"] == {"total_vendas": 6, "faturamento_total": "198.49"}
    assert corpo["faturamento_por_mes"] == _meses_esperados(
        {"2026-08": "55.80", "2026-09": "100.00", "2026-10": "42.69"}
    )
    assert corpo["faturamento_por_categoria"] == [
        {"categoria": "Alimentos", "total": "140.80"},
        {"categoria": "Bebidas", "total": "29.70"},
        {"categoria": "Higiene", "total": "25.00"},
        {"categoria": "Limpeza", "total": "2.99"},
    ]
    assert corpo["top_produtos"] == [
        {"produto_id": _id_produto("Feijão 1 kg"), "produto_nome": "Feijão 1 kg", "quantidade": 10},
        {"produto_id": _id_produto("Sabonete"), "produto_nome": "Sabonete", "quantidade": 10},
        {"produto_id": _id_produto("Refrigerante 2 L"), "produto_nome": "Refrigerante 2 L", "quantidade": 3},
        {"produto_id": _id_produto("Arroz 5 kg"), "produto_nome": "Arroz 5 kg", "quantidade": 2},
        {"produto_id": _id_produto("Detergente 500 ml"), "produto_nome": "Detergente 500 ml", "quantidade": 1},
    ]


@pytest.mark.parametrize("com_vendas", [False, True])
def test_rota_da_cli_tem_os_mesmos_valores_da_web(cliente_http, cabecalho_cli, cabecalho_web, com_vendas):
    if com_vendas:
        for venda in [
            ("Arroz 5 kg", 7, "27.90", "2025-11-01"),
            ("Sabonete", 1, "2.50", "2025-10-31"),  # fora da janela de 12 meses, mas no resumo
            ("Biscoito recheado", 3, "4.25", "2026-03-15"),
            ("Feijão 1 kg", 3, "8.50", "2026-10-31"),
        ]:
            _inserir_venda(*venda)
    cli_ = cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli).get_json()
    web = cliente_http.get("/api/web/dashboard", headers=cabecalho_web).get_json()
    assert cli_ == web


def test_top_produtos_limita_a_cinco_e_desempata_pelo_nome(cliente_http, cabecalho_cli):
    for nome in ("Sabonete", "Arroz 5 kg", "Biscoito recheado", "Detergente 500 ml", "Feijão 1 kg", "Refrigerante 2 L"):
        _inserir_venda(nome, 2, "1.00", "2026-10-01")
    top = cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli).get_json()["top_produtos"]
    assert [p["produto_nome"] for p in top] == [
        "Arroz 5 kg",
        "Biscoito recheado",
        "Detergente 500 ml",
        "Feijão 1 kg",
        "Refrigerante 2 L",
    ]


def test_faturamento_por_mes_usa_os_12_meses_de_calendario_ate_hoje(cliente_http, cabecalho_cli):
    _inserir_venda("Sabonete", 1, "2.50", "2025-10-31")  # um dia antes da janela: fica de fora
    _inserir_venda("Sabonete", 2, "2.50", "2025-11-01")  # primeiro dia da janela
    _inserir_venda("Sabonete", 4, "2.50", "2026-01-20")  # dezembro sem venda fica no meio, zerado
    _inserir_venda("Sabonete", 3, "2.50", "2026-10-31")  # último dia da janela
    corpo = cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli).get_json()
    assert corpo["faturamento_por_mes"] == _meses_esperados(
        {"2025-11": "5.00", "2026-01": "10.00", "2026-10": "7.50"}
    )
    # O resumo cobre todo o período, inclusive a venda fora da janela do gráfico.
    assert corpo["resumo"] == {"total_vendas": 4, "faturamento_total": "25.00"}


def test_db_indicadores_cli_respeita_a_data_de_referencia(vendas):
    meses = db.indicadores_dashboard_cli(date(2026, 9, 15), "req-ref")["faturamento_por_mes"]
    assert [linha["mes"] for linha in meses][-1] == "2026-09" and len(meses) == db.MESES_DASHBOARD
    assert {linha["mes"]: str(linha["total"]) for linha in meses}["2026-09"] == "100.00"


def test_rota_reflete_criacao_edicao_e_exclusao_pela_cli(cliente_http, cabecalho_cli):
    def resumo():
        return cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli).get_json()["resumo"]

    venda = {"produto_id": _id_produto("Sabonete"), "quantidade": 2, "valor_unitario": "2.50", "data_venda": "2026-10-01"}
    criada = cliente_http.post("/api/cli/records", json=venda, headers=cabecalho_cli).get_json()
    assert resumo() == {"total_vendas": 1, "faturamento_total": "5.00"}

    cliente_http.put(f"/api/cli/records/{criada['id']}", json={**venda, "quantidade": 4}, headers=cabecalho_cli)
    assert resumo() == {"total_vendas": 1, "faturamento_total": "10.00"}

    cliente_http.delete(f"/api/cli/records/{criada['id']}", headers=cabecalho_cli)
    assert resumo() == {"total_vendas": 0, "faturamento_total": "0.00"}


# --- logs e falhas --------------------------------------------------------------------------------


def test_rota_correlaciona_logs_api_e_banco_com_client_cli(cliente_http, cabecalho_cli, vendas, capsys):
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli)
    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]
    assert {(e["layer"], e["operation"], e["status"], e["client"]) for e in eventos} == {
        ("api", "dashboard", "success", "cli"),
        ("db", "dashboard", "success", "cli"),
    }
    assert {e["message"] for e in eventos} == {
        "Indicadores enviados (6 vendas)",
        "Indicadores calculados sobre 6 vendas",
    }
    assert all(cabecalho_cli["Authorization"].split()[1] not in json.dumps(e) for e in eventos)


def test_rota_usa_a_consulta_propria_da_cli(cliente_http, cabecalho_cli, monkeypatch):
    monkeypatch.setattr(db, "indicadores_dashboard_web", _falhar_no_banco)
    assert cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli).status_code == 200


def test_rota_erro_no_banco(cliente_http, cabecalho_cli, monkeypatch, capsys):
    monkeypatch.setattr(db, "indicadores_dashboard_cli", _falhar_no_banco)
    capsys.readouterr()
    resposta = cliente_http.get("/api/cli/dashboard", headers=cabecalho_cli)
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}
    evento = eventos_do_log(capsys)[-1]
    assert (evento["level"], evento["operation"], evento["client"]) == ("ERROR", "dashboard", "cli")


def test_db_indicadores_cli_erro_registra_log(monkeypatch, capsys):
    def conexao_quebrada():
        raise psycopg.OperationalError("banco fora do ar")

    monkeypatch.setattr(db, "obter_conexao", conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.indicadores_dashboard_cli(HOJE, "req-erro")
    evento = eventos_do_log(capsys)[-1]
    assert (evento["level"], evento["layer"], evento["client"], evento["request_id"]) == ("ERROR", "db", "cli", "req-erro")


# --- comando `indicadores` ------------------------------------------------------------------------

SAIDA_COM_VENDAS = [
    "Vendas registradas (todo o período): 6",
    "Faturamento total (todo o período): R$ 198,49",
    "",
    "Faturamento por mês (últimos 12 meses)",
    "  nov/2025    R$ 0,00",
    "  dez/2025    R$ 0,00",
    "  jan/2026    R$ 0,00",
    "  fev/2026    R$ 0,00",
    "  mar/2026    R$ 0,00",
    "  abr/2026    R$ 0,00",
    "  mai/2026    R$ 0,00",
    "  jun/2026    R$ 0,00",
    "  jul/2026    R$ 0,00",
    "  ago/2026   R$ 55,80  " + "█" * 17,
    "  set/2026  R$ 100,00  " + "█" * 30,
    "  out/2026   R$ 42,69  " + "█" * 13,
    "",
    "Faturamento por categoria",
    "  Alimentos  R$ 140,80  " + "█" * 30,
    "  Bebidas     R$ 29,70  " + "█" * 6,
    "  Higiene     R$ 25,00  " + "█" * 5,
    "  Limpeza      R$ 2,99  " + "█",
    "",
    "Top 5 produtos (mais unidades vendidas)",
    "  1. Feijão 1 kg        10 unidades  " + "█" * 30,
    "  2. Sabonete           10 unidades  " + "█" * 30,
    "  3. Refrigerante 2 L    3 unidades  " + "█" * 9,
    "  4. Arroz 5 kg          2 unidades  " + "█" * 6,
    "  5. Detergente 500 ml    1 unidade  " + "█" * 3,
]


@pytest.mark.parametrize("comando", ["indicadores", "dashboard"])
def test_indicadores_mostra_os_3_indicadores_em_texto(com_token, vendas, capsys, comando):
    assert cli.main([comando]) == cli.SAIDA_SUCESSO
    saida, erro = _saida(capsys)
    assert saida == SAIDA_COM_VENDAS
    assert erro == ""


def test_indicadores_sem_vendas_usa_as_mensagens_da_web(com_token, capsys):
    assert cli.main(["indicadores"]) == cli.SAIDA_SUCESSO
    saida, _ = _saida(capsys)
    assert saida == [
        "Vendas registradas (todo o período): 0",
        "Faturamento total (todo o período): R$ 0,00",
        "",
        "Faturamento por mês (últimos 12 meses)",
        "  Sem vendas nos últimos 12 meses.",
        "",
        "Faturamento por categoria",
        "  Alimentos  R$ 0,00",
        "  Bebidas    R$ 0,00",
        "  Higiene    R$ 0,00",
        "  Limpeza    R$ 0,00",
        "",
        "Top 5 produtos (mais unidades vendidas)",
        "  Sem vendas registradas.",
    ]


def test_indicadores_mostram_os_mesmos_valores_que_a_web_recebe(com_token, cliente_http, cabecalho_web, vendas, capsys):
    """O texto da CLI é o JSON do dashboard da Web formatado: mesmos indicadores, mesmos valores, mesma ordem."""
    web = cliente_http.get("/api/web/dashboard", headers=cabecalho_web).get_json()
    capsys.readouterr()
    assert cli.main(["indicadores"]) == cli.SAIDA_SUCESSO
    assert _saida(capsys)[0] == cli.formatar_indicadores(web)


def test_indicadores_reflete_venda_criada_pela_cli(com_token, capsys):
    assert cli.main(["criar", "--produto", str(_id_produto("Sabonete")), "--quantidade", "1234",
                     "--valor-unitario", "2,50", "--data", "2026-10-31"]) == cli.SAIDA_SUCESSO
    capsys.readouterr()
    assert cli.main(["indicadores"]) == cli.SAIDA_SUCESSO
    saida, _ = _saida(capsys)
    assert saida[:2] == ["Vendas registradas (todo o período): 1", "Faturamento total (todo o período): R$ 3.085,00"]
    assert saida[-1] == "  1. Sabonete  1.234 unidades  " + "█" * 30


def test_indicadores_sem_token(servidor_api, capsys):
    assert cli.main(["indicadores"]) == cli.SAIDA_NAO_AUTENTICADO
    assert "comando protegido" in _saida(capsys)[1]


def test_indicadores_com_token_invalido(servidor_api, monkeypatch, capsys):
    monkeypatch.setenv("TOKEN_CLI", "token-falso")
    assert cli.main(["indicadores"]) == cli.SAIDA_NAO_AUTENTICADO
    saida, erro = _saida(capsys)
    assert saida == [] and "HTTP 401" in erro


def test_indicadores_erro_no_banco(com_token, monkeypatch, capsys):
    monkeypatch.setattr(db, "indicadores_dashboard_cli", _falhar_no_banco)
    assert cli.main(["indicadores"]) == cli.SAIDA_ERRO
    saida, erro = _saida(capsys)
    assert saida == [] and erro == "Erro: Erro interno (HTTP 500)"


def test_indicadores_api_fora_do_ar(monkeypatch, capsys):
    monkeypatch.setenv("URL_API", "http://127.0.0.1:9")
    monkeypatch.setenv("TOKEN_CLI", "qualquer")
    assert cli.main(["indicadores"]) == cli.SAIDA_ERRO
    assert "não foi possível conectar à API" in _saida(capsys)[1]


@pytest.mark.parametrize(
    "corpo",
    [
        {},  # sem as chaves
        {"resumo": {"total_vendas": 1, "faturamento_total": "1.00"}},  # faltam os indicadores
        {"resumo": [], "faturamento_por_mes": [], "faturamento_por_categoria": [], "top_produtos": []},
        {
            "resumo": {"total_vendas": "um", "faturamento_total": "1.00"},  # não é número
            "faturamento_por_mes": [],
            "faturamento_por_categoria": [],
            "top_produtos": [],
        },
        {
            "resumo": {"total_vendas": 1, "faturamento_total": "1.00"},
            "faturamento_por_mes": [{"mes": "2026-13", "total": "1.00"}],  # mês inexistente
            "faturamento_por_categoria": [],
            "top_produtos": [],
        },
        {
            "resumo": {"total_vendas": 1, "faturamento_total": "1.00"},
            "faturamento_por_mes": [{"mes": "2026-10", "total": "abc"}],  # valor que não é número
            "faturamento_por_categoria": [],
            "top_produtos": [],
        },
        {
            "resumo": {"total_vendas": 1, "faturamento_total": "1.00"},
            "faturamento_por_mes": [{"mes": 202610, "total": "1.00"}],  # mês que não é texto
            "faturamento_por_categoria": [],
            "top_produtos": [],
        },
    ],
)
def test_indicadores_resposta_inesperada_nao_imprime_nada_pela_metade(monkeypatch, capsys, corpo):
    monkeypatch.setenv("TOKEN_CLI", "qualquer")
    monkeypatch.setattr(cli, "abrir_url", lambda _requisicao: RespostaFixa(json.dumps(corpo).encode()))
    assert cli.main(["indicadores"]) == cli.SAIDA_ERRO
    saida, erro = _saida(capsys)
    assert saida == []
    assert erro.endswith("(a resposta não tem os indicadores esperados).")


@pytest.mark.parametrize(
    ("valor", "maximo", "blocos"),
    [
        (0, 100, 0),  # zero não desenha barra
        (0, 0, 0),  # tudo zerado
        (100, 100, cli.LARGURA_BARRA),  # o maior ocupa a largura toda
        (50, 100, cli.LARGURA_BARRA // 2),
        (1, 10_000, 1),  # positivo pequeno ainda aparece
    ],
)
def test_barra_de_texto_e_proporcional_ao_maior_valor(valor, maximo, blocos):
    assert cli.barra_de_texto(valor, maximo) == cli.BLOCO_BARRA * blocos


def test_formatos_brasileiros():
    assert cli.inteiro_brasileiro(1234567) == "1.234.567"
    assert cli.inteiro_brasileiro(10) == "10"
    assert cli.mes_brasileiro("2026-01") == "jan/2026"
    assert cli.mes_brasileiro("2025-12") == "dez/2025"


def test_ajuda_lista_o_comando_indicadores(capsys):
    with pytest.raises(SystemExit) as saida:
        cli.main(["--help"])
    assert saida.value.code == 0
    ajuda = capsys.readouterr().out
    assert "indicadores (dashboard)" in ajuda and "3 indicadores do dashboard da Web" in ajuda


def test_ajuda_do_comando_indicadores(capsys):
    with pytest.raises(SystemExit):
        cli.main(["indicadores", "-h"])
    ajuda = capsys.readouterr().out
    assert "faturamento por mês (últimos 12 meses)" in ajuda and "mostra esta ajuda e sai" in ajuda
