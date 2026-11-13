"""API REST (Flask) consumida pela Web e, futuramente, pela CLI."""

import csv
import io
import os
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from zoneinfo import ZoneInfo

import psycopg
from flask import Flask, Response, g, jsonify, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

import db

VALIDADE_TOKEN_WEB = 8 * 60 * 60  # 8 horas

# Limites da venda: mantêm quantidade * valor_unitario dentro de NUMERIC(12, 2) e os ids dentro de INTEGER.
# QUANTIDADE_MAXIMA e VALOR_UNITARIO_MAXIMO: manter iguais aos de index.html.
QUANTIDADE_MAXIMA = 10_000
VALOR_UNITARIO_MAXIMO = Decimal("100000.00")
ID_MAXIMO = 2_147_483_647
FORMATO_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
FORMATO_ID = re.compile(r"[0-9]+")  # só dígitos ASCII (\d aceitaria "１")

# Fuso usado para saber qual é a data de "hoje" (data da venda não pode ser futura).
FUSO_HORARIO_PADRAO = "America/Fortaleza"

# Listagem da Web: 5 vendas por página, ordenadas por data (mais recentes primeiro) por padrão.
ITENS_POR_PAGINA = 5
PAGINA_MAXIMA = 100_000

# Hash usado quando o usuário não existe, para o login levar o mesmo tempo nos dois casos
# e não revelar quais usuários estão cadastrados.
HASH_FICTICIO = generate_password_hash("senha-ficticia")

app = Flask(__name__)


def registrar_log(nivel, origem, operacao, status, mensagem):
    """Registra um evento da camada de API com o request_id da requisição atual."""
    db.registrar_log(nivel, origem, operacao, status, g.request_id, mensagem, camada="api")


def serializador_web():
    return URLSafeTimedSerializer(os.environ["CHAVE_SECRETA"], salt="sessao-web")


@app.before_request
def atribuir_request_id():
    g.request_id = f"req-{uuid.uuid4().hex[:12]}"


@app.after_request
def expor_request_id(resposta):
    resposta.headers["X-Request-ID"] = g.request_id
    return resposta


def exigir_autenticacao_web(operacao):
    """Exige `Authorization: Bearer <token>` válido emitido por /api/web/login."""

    def decorador(rota):
        @wraps(rota)
        def envoltorio(*args, **kwargs):
            cabecalho = request.headers.get("Authorization", "")
            if not cabecalho.startswith("Bearer "):
                registrar_log("WARN", "web", operacao, "error", "Requisição sem token")
                return jsonify(erro="Não autenticado"), 401
            try:
                conteudo = serializador_web().loads(cabecalho[7:], max_age=VALIDADE_TOKEN_WEB)
            except BadSignature:
                registrar_log("WARN", "web", operacao, "error", "Token inválido ou expirado")
                return jsonify(erro="Não autenticado"), 401
            g.usuario = conteudo["usuario"]
            g.nome = conteudo["nome"]
            return rota(*args, **kwargs)

        return envoltorio

    return decorador


@app.errorhandler(Exception)
def tratar_erro_inesperado(erro):
    """Responde erros não previstos com JSON e log, em vez da página HTML padrão."""
    if isinstance(erro, HTTPException):
        return erro
    registrar_log("ERROR", "web", request.endpoint or "desconhecida", "error", "Erro inesperado na API")
    return jsonify(erro="Erro interno"), 500


@app.get("/api/web/health")
def web_saude():
    registrar_log("INFO", "web", "health", "success", "Healthcheck OK")
    return jsonify(status="ok"), 200


@app.post("/api/web/login")
def web_login():
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        dados = {}
    usuario = dados.get("usuario")
    senha = dados.get("senha")
    if not isinstance(usuario, str) or not isinstance(senha, str) or not usuario or not senha:
        registrar_log("WARN", "web", "login", "error", "Usuário e senha são obrigatórios")
        return jsonify(erro="Usuário e senha são obrigatórios"), 400

    try:
        registro = db.buscar_usuario(usuario, g.request_id, "web")
    except psycopg.Error:
        registrar_log("ERROR", "web", "login", "error", "Falha ao acessar o banco")
        return jsonify(erro="Erro interno"), 500

    senha_valida = check_password_hash(registro["senha_hash"] if registro else HASH_FICTICIO, senha)
    if registro is None or not senha_valida:
        registrar_log("WARN", "web", "login", "error", "Credenciais inválidas")
        return jsonify(erro="Usuário ou senha inválidos"), 401

    token = serializador_web().dumps({"usuario": registro["usuario"], "nome": registro["nome"]})
    registrar_log("INFO", "web", "login", "success", "Login realizado")
    return jsonify(token=token, usuario=registro["usuario"], nome=registro["nome"]), 200


@app.get("/api/web/sessao")
@exigir_autenticacao_web("sessao")
def web_sessao():
    registrar_log("INFO", "web", "sessao", "success", "Sessão válida")
    return jsonify(usuario=g.usuario, nome=g.nome), 200


def fuso_horario():
    """Fuso de `FUSO_HORARIO` (padrão America/Fortaleza). Lança erro se o nome não existir."""
    return ZoneInfo(os.environ.get("FUSO_HORARIO") or FUSO_HORARIO_PADRAO)


def hoje():
    return datetime.now(fuso_horario()).date()


def converter_data(texto):
    """Converte `AAAA-MM-DD` em date. Retorna None se o formato ou a data forem inválidos."""
    if not FORMATO_DATA.fullmatch(texto):
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def validar_venda(dados):
    """Valida os campos da venda. Retorna (venda, erros), com `erros` indexado pelo nome do campo."""
    erros = {}
    venda = {}

    produto_id = dados.get("produto_id")
    if produto_id is None or produto_id == "":
        erros["produto_id"] = "Informe o produto."
    elif isinstance(produto_id, bool) or not isinstance(produto_id, int) or not 1 <= produto_id <= ID_MAXIMO:
        erros["produto_id"] = "Produto inválido."
    else:
        venda["produto_id"] = produto_id

    quantidade = dados.get("quantidade")
    if quantidade is None or quantidade == "":
        erros["quantidade"] = "Informe a quantidade."
    elif isinstance(quantidade, bool) or not isinstance(quantidade, int) or not 1 <= quantidade <= QUANTIDADE_MAXIMA:
        erros["quantidade"] = f"A quantidade deve ser um número inteiro entre 1 e {QUANTIDADE_MAXIMA}."
    else:
        venda["quantidade"] = quantidade

    valor_unitario = dados.get("valor_unitario")
    if valor_unitario is None or valor_unitario == "":
        erros["valor_unitario"] = "Informe o valor unitário."
    else:
        valor = None
        if not isinstance(valor_unitario, bool) and isinstance(valor_unitario, (int, float, str)):
            try:
                valor = Decimal(str(valor_unitario).strip())
            except InvalidOperation:
                valor = None
        if (
            valor is None
            or not valor.is_finite()
            or not Decimal("0") < valor <= VALOR_UNITARIO_MAXIMO
            or valor != valor.quantize(Decimal("0.01"))
        ):
            erros["valor_unitario"] = (
                f"O valor unitário deve ser maior que zero, até {VALOR_UNITARIO_MAXIMO}, com no máximo 2 casas decimais."
            )
        else:
            venda["valor_unitario"] = valor

    data_venda = dados.get("data_venda")
    if data_venda is None or data_venda == "":
        erros["data_venda"] = "Informe a data da venda."
    else:
        data = converter_data(data_venda) if isinstance(data_venda, str) else None
        if data is None:
            erros["data_venda"] = "Data inválida; use o formato AAAA-MM-DD."
        elif data > hoje():
            erros["data_venda"] = "A data da venda não pode ser futura."
        else:
            venda["data_venda"] = data

    return venda, erros


def venda_para_json(linha):
    return {
        "id": linha["id"],
        "produto_id": linha["produto_id"],
        "produto_nome": linha["produto_nome"],
        "categoria": linha["categoria"],
        "quantidade": linha["quantidade"],
        "valor_unitario": str(linha["valor_unitario"]),
        "valor_total": str(linha["valor_total"]),
        "data_venda": linha["data_venda"].isoformat(),
    }


def ler_venda_da_requisicao(operacao):
    """Lê e valida o corpo JSON. Retorna (venda, None) ou (None, resposta de erro)."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        registrar_log("WARN", "web", operacao, "error", "Corpo da requisição não é um objeto JSON")
        return None, (jsonify(erro="Envie os dados da venda em JSON."), 400)
    venda, erros = validar_venda(dados)
    if erros:
        registrar_log("WARN", "web", operacao, "error", f"Dados inválidos: {', '.join(sorted(erros))}")
        return None, (jsonify(erro="Dados inválidos.", campos=erros), 422)
    return venda, None


def ler_id_venda(texto, operacao):
    """Converte o id da rota. Retorna (id, None) ou (None, resposta de erro)."""
    digitos = texto.lstrip("0") if FORMATO_ID.fullmatch(texto) else ""
    if not digitos:
        registrar_log("WARN", "web", operacao, "error", "Número da venda inválido")
        return None, (jsonify(erro="Número da venda inválido."), 400)
    # Mais dígitos que o maior INTEGER: a venda não existe. Testar o tamanho antes evita converter textos enormes.
    if len(digitos) > len(str(ID_MAXIMO)) or int(digitos) > ID_MAXIMO:
        return None, resposta_venda_nao_encontrada(operacao, "acima do limite")
    return int(digitos), None


def resposta_produto_inexistente(operacao):
    registrar_log("WARN", "web", operacao, "error", "Produto inexistente")
    return jsonify(erro="Dados inválidos.", campos={"produto_id": "Produto não encontrado."}), 422


def resposta_venda_nao_encontrada(operacao, id_venda):
    registrar_log("WARN", "web", operacao, "error", f"Venda {id_venda} não encontrada")
    return jsonify(erro="Venda não encontrada."), 404


def resposta_falha_no_banco(operacao):
    registrar_log("ERROR", "web", operacao, "error", "Falha ao acessar o banco")
    return jsonify(erro="Erro interno"), 500


@app.get("/api/web/produtos")
@exigir_autenticacao_web("listar_produtos")
def web_listar_produtos():
    try:
        linhas = db.listar_produtos_web(g.request_id)
    except psycopg.Error:
        return resposta_falha_no_banco("listar_produtos")
    produtos = [
        {"id": linha["id"], "nome": linha["nome"], "categoria": linha["categoria"], "preco": str(linha["preco"])}
        for linha in linhas
    ]
    registrar_log("INFO", "web", "listar_produtos", "success", f"{len(produtos)} produtos listados")
    return jsonify(produtos=produtos), 200


@app.get("/api/web/records/<id_venda>")
@exigir_autenticacao_web("consultar_venda")
def web_consultar_venda(id_venda):
    id_venda, erro = ler_id_venda(id_venda, "consultar_venda")
    if erro:
        return erro
    try:
        linha = db.consultar_venda_web(id_venda, g.request_id)
    except psycopg.Error:
        return resposta_falha_no_banco("consultar_venda")
    if linha is None:
        return resposta_venda_nao_encontrada("consultar_venda", id_venda)
    registrar_log("INFO", "web", "consultar_venda", "success", f"Venda {id_venda} consultada")
    return jsonify(venda_para_json(linha)), 200


@app.post("/api/web/records")
@exigir_autenticacao_web("criar_venda")
def web_criar_venda():
    venda, erro = ler_venda_da_requisicao("criar_venda")
    if erro:
        return erro
    try:
        linha = db.criar_venda_web(venda, g.request_id)
    except db.ProdutoInexistente:
        return resposta_produto_inexistente("criar_venda")
    except psycopg.Error:
        return resposta_falha_no_banco("criar_venda")
    registrar_log("INFO", "web", "criar_venda", "success", f"Venda {linha['id']} criada")
    return jsonify(venda_para_json(linha)), 201


@app.put("/api/web/records/<id_venda>")
@exigir_autenticacao_web("editar_venda")
def web_editar_venda(id_venda):
    id_venda, erro = ler_id_venda(id_venda, "editar_venda")
    if erro:
        return erro
    venda, erro = ler_venda_da_requisicao("editar_venda")
    if erro:
        return erro
    try:
        linha = db.editar_venda_web(id_venda, venda, g.request_id)
    except db.ProdutoInexistente:
        return resposta_produto_inexistente("editar_venda")
    except psycopg.Error:
        return resposta_falha_no_banco("editar_venda")
    if linha is None:
        return resposta_venda_nao_encontrada("editar_venda", id_venda)
    registrar_log("INFO", "web", "editar_venda", "success", f"Venda {id_venda} atualizada")
    return jsonify(venda_para_json(linha)), 200


def ler_inteiro_positivo(texto, maximo):
    """Converte um inteiro da query string. Retorna o número ou None se não estiver entre 1 e `maximo`.

    Testa o tamanho antes de converter (como o ler_id_venda): o int() do Python recusa
    textos com mais de 4300 dígitos, o que viraria um 500 em vez de 400.
    """
    digitos = texto.lstrip("0") if FORMATO_ID.fullmatch(texto) else ""
    if not digitos or len(digitos) > len(str(maximo)):
        return None
    valor = int(digitos)
    return valor if valor <= maximo else None


def ler_pagina(texto):
    """Converte a página da query string. Retorna o número ou None se for inválido."""
    return ler_inteiro_positivo(texto, PAGINA_MAXIMA)


def ler_ordenacao(erros):
    """Lê `ordenar` e `direcao` da query string, anotando os inválidos em `erros`."""
    ordenar = request.args.get("ordenar", "data_venda")
    direcao = request.args.get("direcao", "desc")
    if ordenar not in db.COLUNAS_ORDENACAO_WEB:
        erros["ordenar"] = f"Ordene por: {', '.join(db.COLUNAS_ORDENACAO_WEB)}."
    if direcao not in ("asc", "desc"):
        erros["direcao"] = "A direção deve ser asc ou desc."
    return ordenar, direcao


def ler_paginacao(erros):
    """Lê `pagina`, `ordenar` e `direcao` da query string, anotando os inválidos em `erros`."""
    numero_pagina = ler_pagina(request.args.get("pagina", "1"))
    if numero_pagina is None:
        erros["pagina"] = f"A página deve ser um número inteiro entre 1 e {PAGINA_MAXIMA}."
    ordenar, direcao = ler_ordenacao(erros)
    return numero_pagina, ordenar, direcao


def resposta_parametros_invalidos(operacao, erros):
    registrar_log("WARN", "web", operacao, "error", f"Parâmetros inválidos: {', '.join(sorted(erros))}")
    return jsonify(erro="Parâmetros inválidos.", campos=erros), 400


def total_de_paginas(total):
    return max(1, -(-total // ITENS_POR_PAGINA))


def corpo_da_pagina(linhas, total, pagina, ordenar, direcao):
    """Campos comuns às respostas da listagem e da busca."""
    return {
        "vendas": [venda_para_json(linha) for linha in linhas],
        "pagina": pagina,
        "itens_por_pagina": ITENS_POR_PAGINA,
        "total": total,
        "total_paginas": total_de_paginas(total),
        "ordenar": ordenar,
        "direcao": direcao,
    }


@app.get("/api/web/records")
@exigir_autenticacao_web("listar_vendas")
def web_listar_vendas():
    erros = {}
    pagina, ordenar, direcao = ler_paginacao(erros)
    if erros:
        return resposta_parametros_invalidos("listar_vendas", erros)

    try:
        linhas, total = db.listar_vendas_web(pagina, ITENS_POR_PAGINA, ordenar, direcao, g.request_id)
    except psycopg.Error:
        return resposta_falha_no_banco("listar_vendas")
    registrar_log("INFO", "web", "listar_vendas", "success", f"Página {pagina} de {total_de_paginas(total)} listada")
    return jsonify(corpo_da_pagina(linhas, total, pagina, ordenar, direcao)), 200


def ler_filtros_busca(erros):
    """Lê os filtros da busca avançada da query string, anotando os inválidos em `erros`.

    Filtro ausente ou vazio não restringe a busca. Retorna só os filtros informados e válidos.
    """
    filtros = {}

    produto_id = request.args.get("produto_id", "")
    if produto_id:
        numero = ler_inteiro_positivo(produto_id, ID_MAXIMO)
        if numero is None:
            erros["produto_id"] = "O produto deve ser o número de um produto do catálogo."
        else:
            filtros["produto_id"] = numero

    # A categoria não é conferida com uma lista: a inexistente só não encontra vendas, como o
    # produto_id sem produto. O banco é a fonte das categorias, a mesma do select da Web.
    categoria = request.args.get("categoria", "")
    if categoria:
        if "\x00" in categoria:  # o PostgreSQL recusa NUL em TEXT, o que viraria 500
            erros["categoria"] = "Categoria inválida."
        else:
            filtros["categoria"] = categoria

    for campo, nome in (("data_inicio", "inicial"), ("data_fim", "final")):
        texto = request.args.get(campo, "")
        if texto:
            data = converter_data(texto)
            if data is None:
                erros[campo] = f"Data {nome} inválida; use o formato AAAA-MM-DD."
            else:
                filtros[campo] = data

    if "data_inicio" in filtros and "data_fim" in filtros and filtros["data_inicio"] > filtros["data_fim"]:
        erros["data_fim"] = "A data final não pode ser anterior à data inicial."

    return filtros


def filtros_para_json(filtros):
    """Todos os filtros da busca, com `null` nos ausentes e as datas em AAAA-MM-DD."""
    return {
        campo: valor.isoformat() if isinstance(valor, date) else valor
        for campo in db.CAMPOS_BUSCA_WEB
        for valor in [filtros.get(campo)]
    }


@app.get("/api/web/records/search")
@exigir_autenticacao_web("buscar_vendas")
def web_buscar_vendas():
    erros = {}
    filtros = ler_filtros_busca(erros)
    pagina, ordenar, direcao = ler_paginacao(erros)
    if erros:
        return resposta_parametros_invalidos("buscar_vendas", erros)

    try:
        linhas, total = db.buscar_vendas_web(filtros, pagina, ITENS_POR_PAGINA, ordenar, direcao, g.request_id)
    except psycopg.Error:
        return resposta_falha_no_banco("buscar_vendas")
    registrar_log(
        "INFO",
        "web",
        "buscar_vendas",
        "success",
        f"Busca ({', '.join(sorted(filtros)) or 'sem filtros'}): {total} vendas, página {pagina} de {total_de_paginas(total)}",
    )
    corpo = corpo_da_pagina(linhas, total, pagina, ordenar, direcao)
    corpo["filtros"] = filtros_para_json(filtros)
    return jsonify(corpo), 200


# CSV da Web no formato do Excel em português: separador ";", vírgula decimal, data DD/MM/AAAA
# e UTF-8 com BOM (sem o BOM, o Excel lê os acentos errado).
CABECALHO_CSV = ("Nº", "Data", "Produto", "Categoria", "Quantidade", "Valor unitário", "Valor total")


def valor_csv(valor):
    """Decimal com 2 casas e vírgula decimal, sem separador de milhar (ex.: 1234,50)."""
    return f"{valor:.2f}".replace(".", ",")


def gerar_csv(linhas):
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(CABECALHO_CSV)
    for linha in linhas:
        escritor.writerow(
            (
                linha["id"],
                linha["data_venda"].strftime("%d/%m/%Y"),
                linha["produto_nome"],
                linha["categoria"],
                linha["quantidade"],
                valor_csv(linha["valor_unitario"]),
                valor_csv(linha["valor_total"]),
            )
        )
    return "\ufeff" + saida.getvalue()  # BOM do UTF-8


@app.get("/api/web/records/export")
@exigir_autenticacao_web("exportar_vendas")
def web_exportar_vendas():
    erros = {}
    filtros = ler_filtros_busca(erros)
    ordenar, direcao = ler_ordenacao(erros)
    if erros:
        return resposta_parametros_invalidos("exportar_vendas", erros)

    try:
        linhas = db.exportar_vendas_web(filtros, ordenar, direcao, g.request_id)
    except psycopg.Error:
        return resposta_falha_no_banco("exportar_vendas")
    nome_arquivo = f"vendas-{datetime.now(fuso_horario()):%Y%m%d-%H%M%S}.csv"
    registrar_log(
        "INFO",
        "web",
        "exportar_vendas",
        "success",
        f"{len(linhas)} vendas exportadas em {nome_arquivo} ({', '.join(sorted(filtros)) or 'sem filtros'})",
    )
    return Response(
        gerar_csv(linhas),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{nome_arquivo}"',
            "Cache-Control": "no-store",  # dados de quem está logado: não guardar em cache
        },
    )


@app.delete("/api/web/records/<id_venda>")
@exigir_autenticacao_web("excluir_venda")
def web_excluir_venda(id_venda):
    id_venda, erro = ler_id_venda(id_venda, "excluir_venda")
    if erro:
        return erro
    try:
        excluida = db.excluir_venda_web(id_venda, g.request_id)
    except psycopg.Error:
        return resposta_falha_no_banco("excluir_venda")
    if not excluida:
        return resposta_venda_nao_encontrada("excluir_venda", id_venda)
    registrar_log("INFO", "web", "excluir_venda", "success", f"Venda {id_venda} excluída")
    return jsonify(id=id_venda, mensagem="Venda excluída."), 200


def inicializar():
    """Cria as tabelas e o usuário inicial a partir das variáveis de ambiente."""
    fuso_horario()  # falha já na inicialização se FUSO_HORARIO for inválido
    senha_hash = generate_password_hash(os.environ["ADMIN_SENHA"])
    db.inicializar_banco(os.environ["ADMIN_USUARIO"], os.environ["ADMIN_NOME"], senha_hash)


def criar_app():
    """Ponto de entrada do gunicorn (`api:criar_app()`): inicializa o banco e devolve a aplicação."""
    inicializar()
    return app
