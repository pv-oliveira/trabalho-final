"""Testes da exportação CSV na Web (issue #5)."""

import csv
import io
import json
import re
from datetime import date
from decimal import Decimal

import psycopg
import pytest

import api
import db

CABECALHO = ["Nº", "Data", "Produto", "Categoria", "Quantidade", "Valor unitário", "Valor total"]


def _eventos(capsys):
    return [json.loads(linha) for linha in capsys.readouterr().out.strip().splitlines()]


def _conexao_quebrada():
    raise psycopg.OperationalError("banco fora do ar")


def _falhar_no_banco(*_args):
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


@pytest.fixture(autouse=True)
def vendas_limpas():
    """Cada teste começa sem vendas, para os resultados serem previsíveis."""
    with db.obter_conexao() as conexao:
        conexao.execute("TRUNCATE venda RESTART IDENTITY")


@pytest.fixture
def cabecalho(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def vendas():
    """10 vendas de 3 categorias em datas diferentes (mais de uma página da tabela). Retorna {apelido: id}."""
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


def _exportar(cliente_http, cabecalho, consulta=""):
    return cliente_http.get(f"/api/web/records/export?{consulta}", headers=cabecalho)


def _linhas_csv(resposta):
    """Lê o CSV da resposta (sem o BOM) e devolve (cabeçalho, linhas)."""
    texto = resposta.get_data().decode("utf-8-sig")
    linhas = list(csv.reader(io.StringIO(texto, newline=""), delimiter=";"))
    return linhas[0], linhas[1:]


def _ids(linhas):
    return [int(linha[0]) for linha in linhas]


# --- autenticação ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cabecalho_auth", [None, "Bearer invalido", "Basic abc", "Bearer "])
def test_exportacao_exige_autenticacao(cliente_http, cabecalho_auth, capsys):
    cabecalhos = {"Authorization": cabecalho_auth} if cabecalho_auth else {}
    capsys.readouterr()
    resposta = cliente_http.get("/api/web/records/export", headers=cabecalhos)
    assert resposta.status_code == 401 and resposta.get_json() == {"erro": "Não autenticado"}
    evento = _eventos(capsys)[-1]
    assert (evento["layer"], evento["operation"], evento["level"]) == ("api", "exportar_vendas", "WARN")


def test_exportacao_com_parametros_invalidos_sem_token_responde_401(cliente_http):
    assert cliente_http.get("/api/web/records/export?data_inicio=xyz").status_code == 401


def test_rota_export_nao_e_confundida_com_id_de_venda(cliente_http, cabecalho):
    resposta = _exportar(cliente_http, cabecalho)
    assert resposta.status_code == 200 and resposta.mimetype == "text/csv"


# --- conteúdo -------------------------------------------------------------------------------------


def test_sem_filtros_exporta_todas_as_vendas(cliente_http, cabecalho, vendas):
    cabecalho_csv, linhas = _linhas_csv(_exportar(cliente_http, cabecalho))
    assert cabecalho_csv == CABECALHO
    # Todas as 10, e não só as 5 da primeira página da tabela.
    assert sorted(_ids(linhas)) == sorted(vendas.values())


@pytest.mark.parametrize(
    "consulta, esperados",
    [
        ("categoria=Alimentos", ["arroz_set", "arroz_out", "arroz_nov", "feijao_out", "feijao_nov"]),
        (
            "data_inicio=2026-10-01&data_fim=2026-10-31",
            ["arroz_out", "feijao_out", "refri_out", "suco_out", "sabonete_out"],
        ),
        ("produto_id={arroz}&data_inicio=2026-10-01", ["arroz_out", "arroz_nov"]),
        ("categoria=Bebidas&data_fim=2026-10-20", ["refri_set", "refri_out"]),
        ("categoria=Eletrônicos", []),
    ],
)
def test_exporta_todas_as_vendas_dos_filtros(cliente_http, cabecalho, vendas, consulta, esperados):
    consulta = consulta.format(arroz=_id_produto("Arroz 5 kg"))
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho, consulta))
    assert sorted(_ids(linhas)) == sorted(vendas[apelido] for apelido in esperados)


def test_filtro_sem_resultado_gera_so_o_cabecalho(cliente_http, cabecalho, vendas):
    resposta = _exportar(cliente_http, cabecalho, "data_inicio=2030-01-01")
    assert resposta.status_code == 200
    assert resposta.get_data().decode("utf-8") == "﻿" + ";".join(CABECALHO) + "\r\n"


def test_ordenacao_padrao_e_data_mais_recente_primeiro(cliente_http, cabecalho, vendas):
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho))
    datas = [date(*map(int, reversed(linha[1].split("/")))) for linha in linhas]
    assert datas == sorted(datas, reverse=True)


@pytest.mark.parametrize("direcao", ["asc", "desc"])
def test_segue_a_ordenacao_da_tabela(cliente_http, cabecalho, vendas, direcao):
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho, f"ordenar=valor_total&direcao={direcao}"))
    chaves = [(Decimal(linha[6].replace(",", ".")), int(linha[0])) for linha in linhas]
    assert chaves == sorted(chaves, reverse=direcao == "desc")


def test_formato_do_csv_para_o_excel_em_portugues(cliente_http, cabecalho, vendas):
    resposta = _exportar(cliente_http, cabecalho, "ordenar=id&direcao=asc")
    bruto = resposta.get_data()
    assert bruto.startswith(b"\xef\xbb\xbf")  # BOM do UTF-8
    texto = bruto.decode("utf-8-sig")
    assert texto.endswith("\r\n") and texto.count("\r\n") == 11  # cabeçalho + 10 vendas
    _, linhas = _linhas_csv(resposta)
    assert linhas[0] == [str(vendas["arroz_set"]), "10/09/2026", "Arroz 5 kg", "Alimentos", "1", "27,90", "27,90"]
    assert linhas[5] == [str(vendas["refri_set"]), "20/09/2026", "Refrigerante 2 L", "Bebidas", "6", "9,90", "59,40"]


def test_cabecalhos_http_do_arquivo(cliente_http, cabecalho, vendas):
    resposta = _exportar(cliente_http, cabecalho)
    assert resposta.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert re.fullmatch(r'attachment; filename="vendas-\d{8}-\d{6}\.csv"', resposta.headers["Content-Disposition"])
    assert resposta.headers["Cache-Control"] == "no-store"


def test_nome_do_arquivo_usa_o_fuso_configurado(cliente_http, cabecalho, monkeypatch):
    monkeypatch.setenv("FUSO_HORARIO", "Pacific/Kiritimati")  # UTC+14: a data costuma diferir da de Fortaleza
    esperado = api.datetime.now(api.fuso_horario()).strftime("vendas-%Y%m%d-")
    assert f'filename="{esperado}' in _exportar(cliente_http, cabecalho).headers["Content-Disposition"]


def test_gerar_csv_escapa_separador_e_aspas():
    linha = {
        "id": 7,
        "data_venda": date(2026, 1, 2),
        "produto_nome": 'Kit "promo"; 2 un',
        "categoria": "Alimentos",
        "quantidade": 10_000,
        "valor_unitario": Decimal("100000.00"),
        "valor_total": Decimal("1000000000.00"),
    }
    texto = api.gerar_csv([linha])
    assert texto.splitlines()[1] == '7;02/01/2026;"Kit ""promo""; 2 un";Alimentos;10000;100000,00;1000000000,00'


# --- validação ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "consulta, campos",
    [
        ("produto_id=abc", {"produto_id"}),
        ("categoria=Bebidas%00", {"categoria"}),
        ("data_inicio=2026-13-01", {"data_inicio"}),
        ("data_inicio=2026-10-31&data_fim=2026-10-01", {"data_fim"}),
        ("ordenar=senha_hash", {"ordenar"}),
        ("direcao=cima", {"direcao"}),
        ("produto_id=x&data_fim=z&ordenar=y", {"produto_id", "data_fim", "ordenar"}),
    ],
)
def test_parametros_invalidos_retornam_400_sem_consultar_o_banco(
    cliente_http, cabecalho, monkeypatch, capsys, consulta, campos
):
    monkeypatch.setattr(db, "exportar_vendas_web", _falhar_no_banco)
    capsys.readouterr()
    resposta = _exportar(cliente_http, cabecalho, consulta)
    assert resposta.status_code == 400
    corpo = resposta.get_json()
    assert corpo["erro"] == "Parâmetros inválidos." and set(corpo["campos"]) == campos
    eventos = [e for e in _eventos(capsys) if e["request_id"] == resposta.headers["X-Request-ID"]]
    assert [(e["layer"], e["level"]) for e in eventos if e["operation"] == "exportar_vendas"] == [("api", "WARN")]


def test_pagina_e_ignorada_na_exportacao(cliente_http, cabecalho, vendas):
    _, linhas = _linhas_csv(_exportar(cliente_http, cabecalho, "pagina=abc"))
    assert len(linhas) == 10


# --- logs -----------------------------------------------------------------------------------------


def test_exportacao_correlaciona_logs_api_e_banco(cliente_http, cabecalho, vendas, capsys, token):
    capsys.readouterr()
    resposta = _exportar(cliente_http, cabecalho, "categoria=Alimentos&data_inicio=2026-10-01")
    saida = capsys.readouterr().out
    assert token not in saida
    request_id = resposta.headers["X-Request-ID"]
    eventos = [json.loads(linha) for linha in saida.strip().splitlines()]
    eventos = [e for e in eventos if e["request_id"] == request_id]
    assert {(e["layer"], e["operation"], e["status"], e["client"], e["level"]) for e in eventos} == {
        ("api", "exportar_vendas", "success", "web", "INFO"),
        ("db", "exportar_vendas", "success", "web", "INFO"),
    }
    for evento in eventos:
        assert "4 vendas" in evento["message"] and "categoria, data_inicio" in evento["message"]
        assert set(evento) == {"timestamp", "level", "layer", "client", "operation", "status", "request_id", "message"}


def test_exportacao_erro_no_banco(cliente_http, cabecalho, monkeypatch, capsys):
    monkeypatch.setattr(db, "exportar_vendas_web", _falhar_no_banco)
    capsys.readouterr()
    resposta = _exportar(cliente_http, cabecalho)
    assert resposta.status_code == 500 and resposta.get_json() == {"erro": "Erro interno"}
    evento = _eventos(capsys)[-1]
    assert (evento["layer"], evento["level"], evento["operation"]) == ("api", "ERROR", "exportar_vendas")


# --- camada de banco ------------------------------------------------------------------------------


def test_db_exportar_vendas_web_combina_filtros_sem_paginar(vendas, capsys):
    filtros = {"categoria": "Alimentos", "data_fim": date(2026, 10, 31)}
    linhas = db.exportar_vendas_web(filtros, "id", "asc", "req-exporta")
    assert [linha["id"] for linha in linhas] == [vendas["arroz_set"], vendas["arroz_out"], vendas["feijao_out"]]
    assert set(linhas[0]) == {
        "id", "data_venda", "produto_nome", "categoria", "quantidade", "valor_unitario", "valor_total"
    }
    evento = _eventos(capsys)[-1]
    assert (evento["layer"], evento["operation"], evento["request_id"]) == ("db", "exportar_vendas", "req-exporta")
    assert "3 vendas" in evento["message"] and "categoria, data_fim" in evento["message"]


def test_db_exportar_vendas_web_sem_filtros(vendas, capsys):
    linhas = db.exportar_vendas_web({}, "id", "desc", "req-exporta")
    assert [linha["id"] for linha in linhas] == sorted(vendas.values(), reverse=True)
    assert "sem filtros" in _eventos(capsys)[-1]["message"]


def test_db_exportar_vendas_web_erro(monkeypatch, capsys):
    monkeypatch.setattr(db, "obter_conexao", _conexao_quebrada)
    with pytest.raises(psycopg.OperationalError):
        db.exportar_vendas_web({"categoria": "Bebidas"}, "id", "asc", "req-erro")
    evento = _eventos(capsys)[-1]
    assert (evento["level"], evento["operation"], evento["status"]) == ("ERROR", "exportar_vendas", "error")
