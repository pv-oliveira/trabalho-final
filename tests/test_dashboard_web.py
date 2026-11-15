"""Testes do dashboard da Web (issue #6): indicadores na API e no banco."""

import json
from datetime import date

import psycopg
import pytest

import api
import db
from conftest import eventos_do_log

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


@pytest.fixture(autouse=True)
def hoje_fixo(monkeypatch):
    """A janela de 12 meses termina no mês de hoje: fixa a data para os testes não dependerem do relógio."""
    monkeypatch.setattr(api, "hoje", lambda: HOJE)


def _meses_esperados(com_valor):
    """Os 12 meses de nov/2025 a out/2026, com zero onde `com_valor` não tem o mês."""
    meses = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 11)]
    return [{"mes": mes, "total": com_valor.get(mes, "0.00")} for mes in meses]


@pytest.fixture(autouse=True)
def vendas_limpas():
    """Cada teste começa sem vendas, para os indicadores serem previsíveis."""
    with db.obter_conexao() as conexao:
        conexao.execute("TRUNCATE venda RESTART IDENTITY")


@pytest.fixture
def cabecalho(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def vendas():
    """Vendas em 3 meses e 4 categorias. Totais conferidos à mão nos testes."""
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


# --- autenticação ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cabecalho_auth", [None, "Bearer invalido", "Basic abc"])
def test_dashboard_exige_autenticacao(cliente_http, cabecalho_auth, capsys):
    cabecalhos = {"Authorization": cabecalho_auth} if cabecalho_auth else {}
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/dashboard", headers=cabecalhos)
    assert resposta.status_code == 401
    # Recusada antes do banco: nenhum evento da camada db.
    assert all(e["layer"] == "api" for e in eventos_do_log(capsys))


# --- indicadores ----------------------------------------------------------------------------------


def test_dashboard_sem_vendas(cliente_http, cabecalho):
    corpo = cliente_http.get("/api/web/dashboard", headers=cabecalho).get_json()
    assert corpo["resumo"] == {"total_vendas": 0, "faturamento_total": "0.00"}
    # Os 12 meses aparecem mesmo sem vendas, zerados.
    assert corpo["faturamento_por_mes"] == _meses_esperados({})
    assert corpo["top_produtos"] == []
    # Categorias do catálogo aparecem mesmo sem vendas, com total zero.
    assert {c["categoria"] for c in corpo["faturamento_por_categoria"]} == {c for _, c, _ in db.PRODUTOS_INICIAIS}
    assert {c["total"] for c in corpo["faturamento_por_categoria"]} == {"0.00"}


def test_dashboard_com_vendas(cliente_http, cabecalho, vendas):
    resposta = cliente_http.get("/api/web/dashboard", headers=cabecalho)
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


def test_top_produtos_limita_a_cinco_e_desempata_pelo_nome(cliente_http, cabecalho):
    for nome in ("Sabonete", "Arroz 5 kg", "Biscoito recheado", "Detergente 500 ml", "Feijão 1 kg", "Refrigerante 2 L"):
        _inserir_venda(nome, 2, "1.00", "2026-10-01")
    top = cliente_http.get("/api/web/dashboard", headers=cabecalho).get_json()["top_produtos"]
    assert [p["produto_nome"] for p in top] == [
        "Arroz 5 kg",
        "Biscoito recheado",
        "Detergente 500 ml",
        "Feijão 1 kg",
        "Refrigerante 2 L",
    ]


def test_faturamento_por_mes_usa_os_12_meses_de_calendario_ate_hoje(cliente_http, cabecalho):
    _inserir_venda("Sabonete", 1, "2.50", "2025-10-31")  # um dia antes da janela: fica de fora
    _inserir_venda("Sabonete", 2, "2.50", "2025-11-01")  # primeiro dia da janela
    _inserir_venda("Sabonete", 4, "2.50", "2026-01-20")  # dezembro sem venda fica no meio, zerado
    _inserir_venda("Sabonete", 3, "2.50", "2026-10-31")  # último dia da janela
    corpo = cliente_http.get("/api/web/dashboard", headers=cabecalho).get_json()
    assert corpo["faturamento_por_mes"] == _meses_esperados(
        {"2025-11": "5.00", "2026-01": "10.00", "2026-10": "7.50"}
    )
    # O resumo cobre todo o período, inclusive a venda fora da janela do gráfico.
    assert corpo["resumo"] == {"total_vendas": 4, "faturamento_total": "25.00"}


def test_db_indicadores_respeita_a_data_de_referencia(vendas):
    meses = db.indicadores_dashboard_web(date(2026, 9, 15), "req-ref")["faturamento_por_mes"]
    assert [linha["mes"] for linha in meses][-1] == "2026-09" and len(meses) == db.MESES_DASHBOARD
    assert {linha["mes"]: str(linha["total"]) for linha in meses}["2026-09"] == "100.00"


def test_dashboard_reflete_criacao_edicao_e_exclusao(cliente_http, cabecalho):
    def resumo():
        return cliente_http.get("/api/web/dashboard", headers=cabecalho).get_json()["resumo"]

    venda = {"produto_id": _id_produto("Sabonete"), "quantidade": 2, "valor_unitario": "2.50", "data_venda": "2026-10-01"}
    criada = cliente_http.post("/api/web/records", json=venda, headers=cabecalho).get_json()
    assert resumo() == {"total_vendas": 1, "faturamento_total": "5.00"}

    cliente_http.put(f"/api/web/records/{criada['id']}", json={**venda, "quantidade": 4}, headers=cabecalho)
    assert resumo() == {"total_vendas": 1, "faturamento_total": "10.00"}

    cliente_http.delete(f"/api/web/records/{criada['id']}", headers=cabecalho)
    assert resumo() == {"total_vendas": 0, "faturamento_total": "0.00"}


# --- logs e falhas --------------------------------------------------------------------------------


def test_dashboard_correlaciona_logs_api_e_banco(cliente_http, cabecalho, vendas, capsys):
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/dashboard", headers=cabecalho)
    request_id = resposta.headers["X-Request-ID"]
    eventos = [e for e in eventos_do_log(capsys) if e["request_id"] == request_id]
    assert {(e["layer"], e["operation"], e["status"], e["client"]) for e in eventos} == {
        ("api", "dashboard", "success", "web"),
        ("db", "dashboard", "success", "web"),
    }
    assert all(cabecalho["Authorization"].split()[1] not in json.dumps(e) for e in eventos)


def test_dashboard_erro_no_banco(cliente_http, cabecalho, monkeypatch, capsys):
    monkeypatch.setattr(db, "indicadores_dashboard_web", _falhar_no_banco)
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/dashboard", headers=cabecalho)
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}
    evento = eventos_do_log(capsys)[-1]
    assert (evento["level"], evento["operation"]) == ("ERROR", "dashboard")


def test_db_indicadores_erro_registra_log(monkeypatch, capsys):
    def conexao_quebrada():
        raise psycopg.OperationalError("banco fora do ar")

    monkeypatch.setattr(db, "obter_conexao", conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.indicadores_dashboard_web(HOJE, "req-erro")
    evento = eventos_do_log(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["operation"], evento["request_id"]) == (
        "db",
        "ERROR",
        "dashboard",
        "req-erro",
    )
