"""Camada de acesso ao banco (PostgreSQL)."""

import json
import os
import sys
from datetime import datetime, timezone

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def registrar_log(nivel, origem, operacao, status, request_id, mensagem, camada="db"):
    """Emite uma linha JSON no stdout. `camada` é `db` ou `api`."""
    evento = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": nivel,
        "layer": camada,
        "client": origem,
        "operation": operacao,
        "status": status,
        "request_id": request_id,
        "message": mensagem,
    }
    print(json.dumps(evento, ensure_ascii=False), file=sys.stdout, flush=True)


# Catálogo fixo de produtos (nome, categoria, preço), carregado na inicialização.
PRODUTOS_INICIAIS = [
    ("Arroz 5 kg", "Alimentos", 27.90),
    ("Feijão 1 kg", "Alimentos", 8.50),
    ("Biscoito recheado", "Alimentos", 4.50),
    ("Refrigerante 2 L", "Bebidas", 9.90),
    ("Suco de laranja 1 L", "Bebidas", 7.90),
    ("Água mineral 1,5 L", "Bebidas", 3.50),
    ("Detergente 500 ml", "Limpeza", 2.99),
    ("Sabão em pó 1 kg", "Limpeza", 14.90),
    ("Desinfetante 2 L", "Limpeza", 11.90),
    ("Sabonete", "Higiene", 2.50),
    ("Creme dental", "Higiene", 5.90),
    ("Papel higiênico 12 un", "Higiene", 19.90),
]

# Categorias do catálogo, aceitas pelo filtro `categoria` da busca da Web.
CATEGORIAS = tuple(sorted({categoria for _, categoria, _ in PRODUTOS_INICIAIS}))


def obter_conexao():
    return psycopg.connect(os.environ["URL_BANCO"], row_factory=dict_row)


def inicializar_banco(usuario, nome, senha_hash, request_id="inicializacao", origem="sistema"):
    """Cria as tabelas, o catálogo de produtos e o usuário inicial (idempotente)."""
    try:
        with obter_conexao() as conexao:
            conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS usuario (
                    id SERIAL PRIMARY KEY,
                    usuario TEXT UNIQUE NOT NULL,
                    nome TEXT NOT NULL,
                    senha_hash TEXT NOT NULL,
                    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conexao.execute(
                """
                INSERT INTO usuario (usuario, nome, senha_hash) VALUES (%s, %s, %s)
                ON CONFLICT (usuario) DO UPDATE SET nome = EXCLUDED.nome, senha_hash = EXCLUDED.senha_hash
                """,
                (usuario, nome, senha_hash),
            )
            conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS produto (
                    id SERIAL PRIMARY KEY,
                    nome TEXT UNIQUE NOT NULL,
                    categoria TEXT NOT NULL,
                    preco NUMERIC(10, 2) NOT NULL CHECK (preco > 0)
                )
                """
            )
            with conexao.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO produto (nome, categoria, preco) VALUES (%s, %s, %s) ON CONFLICT (nome) DO NOTHING",
                    PRODUTOS_INICIAIS,
                )
            conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS venda (
                    id SERIAL PRIMARY KEY,
                    produto_id INTEGER NOT NULL REFERENCES produto (id),
                    quantidade INTEGER NOT NULL CHECK (quantidade >= 1),
                    valor_unitario NUMERIC(10, 2) NOT NULL CHECK (valor_unitario > 0),
                    valor_total NUMERIC(12, 2) GENERATED ALWAYS AS (quantidade * valor_unitario) STORED,
                    data_venda DATE NOT NULL
                )
                """
            )
    except psycopg.Error:
        registrar_log("ERROR", origem, "inicializar_banco", "error", request_id, "Falha ao inicializar o banco")
        raise
    registrar_log(
        "INFO", origem, "inicializar_banco", "success", request_id, "Banco inicializado com catálogo de produtos e usuário inicial"
    )


def buscar_usuario(usuario, request_id, origem):
    """Retorna o usuário (dict) ou None se não existir."""
    try:
        with obter_conexao() as conexao:
            linha = conexao.execute(
                "SELECT id, usuario, nome, senha_hash FROM usuario WHERE usuario = %s",
                (usuario,),
            ).fetchone()
    except psycopg.Error:
        registrar_log("ERROR", origem, "consultar_usuario", "error", request_id, "Falha ao consultar usuário")
        raise
    mensagem = "Usuário encontrado" if linha else "Usuário não encontrado"
    registrar_log("INFO", origem, "consultar_usuario", "success", request_id, mensagem)
    return linha


class ProdutoInexistente(Exception):
    """O `produto_id` informado não existe no catálogo."""


def _venda_completa(conexao, id_venda):
    """Busca a venda com o nome e a categoria do produto (consulta da Web)."""
    return conexao.execute(
        """
        SELECT venda.id, venda.produto_id, produto.nome AS produto_nome, produto.categoria,
               venda.quantidade, venda.valor_unitario, venda.valor_total, venda.data_venda
        FROM venda JOIN produto ON produto.id = venda.produto_id
        WHERE venda.id = %s
        """,
        (id_venda,),
    ).fetchone()


def listar_produtos_web(request_id):
    """Retorna o catálogo de produtos para o select do formulário da Web."""
    try:
        with obter_conexao() as conexao:
            linhas = conexao.execute(
                "SELECT id, nome, categoria, preco FROM produto ORDER BY categoria, nome"
            ).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "web", "listar_produtos", "error", request_id, "Falha ao listar produtos")
        raise
    registrar_log("INFO", "web", "listar_produtos", "success", request_id, f"{len(linhas)} produtos listados")
    return linhas


def consultar_venda_web(id_venda, request_id):
    """Retorna a venda (dict) ou None se não existir."""
    try:
        with obter_conexao() as conexao:
            linha = _venda_completa(conexao, id_venda)
    except psycopg.Error:
        registrar_log("ERROR", "web", "consultar_venda", "error", request_id, "Falha ao consultar venda")
        raise
    if linha is None:
        registrar_log("WARN", "web", "consultar_venda", "error", request_id, f"Venda {id_venda} não encontrada")
    else:
        registrar_log("INFO", "web", "consultar_venda", "success", request_id, f"Venda {id_venda} encontrada")
    return linha


def criar_venda_web(venda, request_id):
    """Insere a venda e a retorna completa. Lança ProdutoInexistente se o produto não existir."""
    try:
        with obter_conexao() as conexao:
            id_venda = conexao.execute(
                """
                INSERT INTO venda (produto_id, quantidade, valor_unitario, data_venda)
                VALUES (%(produto_id)s, %(quantidade)s, %(valor_unitario)s, %(data_venda)s)
                RETURNING id
                """,
                venda,
            ).fetchone()["id"]
            linha = _venda_completa(conexao, id_venda)
    except psycopg.errors.ForeignKeyViolation:
        registrar_log("WARN", "web", "criar_venda", "error", request_id, "Produto inexistente")
        raise ProdutoInexistente from None
    except psycopg.Error:
        registrar_log("ERROR", "web", "criar_venda", "error", request_id, "Falha ao criar venda")
        raise
    registrar_log("INFO", "web", "criar_venda", "success", request_id, f"Venda {id_venda} criada")
    return linha


def editar_venda_web(id_venda, venda, request_id):
    """Atualiza todos os campos da venda e a retorna completa, ou None se ela não existir.

    Lança ProdutoInexistente se o produto não existir.
    """
    try:
        with obter_conexao() as conexao:
            atualizada = conexao.execute(
                """
                UPDATE venda SET produto_id = %(produto_id)s, quantidade = %(quantidade)s,
                                 valor_unitario = %(valor_unitario)s, data_venda = %(data_venda)s
                WHERE id = %(id)s
                RETURNING id
                """,
                {**venda, "id": id_venda},
            ).fetchone()
            linha = _venda_completa(conexao, id_venda) if atualizada else None
    except psycopg.errors.ForeignKeyViolation:
        registrar_log("WARN", "web", "editar_venda", "error", request_id, "Produto inexistente")
        raise ProdutoInexistente from None
    except psycopg.Error:
        registrar_log("ERROR", "web", "editar_venda", "error", request_id, "Falha ao editar venda")
        raise
    if linha is None:
        registrar_log("WARN", "web", "editar_venda", "error", request_id, f"Venda {id_venda} não encontrada")
    else:
        registrar_log("INFO", "web", "editar_venda", "success", request_id, f"Venda {id_venda} atualizada")
    return linha


# Colunas pelas quais a listagem da Web pode ser ordenada (nome do parâmetro -> coluna SQL).
# A API só aceita essas chaves, então nenhum texto do usuário entra na consulta.
COLUNAS_ORDENACAO_WEB = {
    "id": "venda.id",
    "data_venda": "venda.data_venda",
    "produto_nome": "produto.nome",
    "categoria": "produto.categoria",
    "quantidade": "venda.quantidade",
    "valor_total": "venda.valor_total",
}


def listar_vendas_web(pagina, itens_por_pagina, ordenar, direcao, request_id):
    """Retorna (vendas da página, total de vendas) para a tabela da Web.

    `ordenar` deve ser uma chave de COLUNAS_ORDENACAO_WEB e `direcao`, "asc" ou "desc".
    O id desempata a ordenação, para a paginação ser estável.
    """
    sentido = sql.SQL("DESC" if direcao == "desc" else "ASC")
    consulta = sql.SQL(
        """
        SELECT venda.id, venda.produto_id, produto.nome AS produto_nome, produto.categoria,
               venda.quantidade, venda.valor_unitario, venda.valor_total, venda.data_venda
        FROM venda JOIN produto ON produto.id = venda.produto_id
        ORDER BY {coluna} {sentido}, venda.id {sentido}
        LIMIT %s OFFSET %s
        """
    ).format(coluna=sql.SQL(COLUNAS_ORDENACAO_WEB[ordenar]), sentido=sentido)
    try:
        with obter_conexao() as conexao:
            # REPEATABLE READ: a contagem e a página veem o mesmo snapshot, então o total
            # bate com as linhas devolvidas mesmo se outra sessão alterar vendas no meio.
            conexao.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            total = conexao.execute("SELECT COUNT(*) AS total FROM venda").fetchone()["total"]
            linhas = conexao.execute(consulta, (itens_por_pagina, (pagina - 1) * itens_por_pagina)).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "web", "listar_vendas", "error", request_id, "Falha ao listar vendas")
        raise
    registrar_log(
        "INFO", "web", "listar_vendas", "success", request_id, f"Página {pagina}: {len(linhas)} de {total} vendas"
    )
    return linhas, total


def buscar_vendas_web(filtros, pagina, itens_por_pagina, ordenar, direcao, request_id):
    """Retorna (vendas da página, total de vendas encontradas) para a busca avançada da Web.

    `filtros` pode ter `produto_id`, `categoria`, `data_inicio` e `data_fim`; os ausentes não
    restringem a busca e os presentes são combinados com AND. O texto do usuário só entra como
    parâmetro: as condições e a coluna de ordenação vêm de listas fixas deste módulo.
    """
    condicoes = [
        sql.SQL(condicao)
        for campo, condicao in (
            ("produto_id", "venda.produto_id = %(produto_id)s"),
            ("categoria", "produto.categoria = %(categoria)s"),
            ("data_inicio", "venda.data_venda >= %(data_inicio)s"),
            ("data_fim", "venda.data_venda <= %(data_fim)s"),
        )
        if campo in filtros
    ]
    onde = sql.SQL("WHERE ") + sql.SQL(" AND ").join(condicoes) if condicoes else sql.SQL("")
    sentido = sql.SQL("DESC" if direcao == "desc" else "ASC")
    contagem = sql.SQL(
        "SELECT COUNT(*) AS total FROM venda JOIN produto ON produto.id = venda.produto_id {onde}"
    ).format(onde=onde)
    consulta = sql.SQL(
        """
        SELECT venda.id, venda.produto_id, produto.nome AS produto_nome, produto.categoria,
               venda.quantidade, venda.valor_unitario, venda.valor_total, venda.data_venda
        FROM venda JOIN produto ON produto.id = venda.produto_id
        {onde}
        ORDER BY {coluna} {sentido}, venda.id {sentido}
        LIMIT %(limite)s OFFSET %(deslocamento)s
        """
    ).format(onde=onde, coluna=sql.SQL(COLUNAS_ORDENACAO_WEB[ordenar]), sentido=sentido)
    parametros = {**filtros, "limite": itens_por_pagina, "deslocamento": (pagina - 1) * itens_por_pagina}
    try:
        with obter_conexao() as conexao:
            # Mesmo snapshot para a contagem e a página, como na listagem.
            conexao.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            total = conexao.execute(contagem, filtros).fetchone()["total"]
            linhas = conexao.execute(consulta, parametros).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "web", "buscar_vendas", "error", request_id, "Falha ao buscar vendas")
        raise
    registrar_log(
        "INFO",
        "web",
        "buscar_vendas",
        "success",
        request_id,
        f"Busca ({', '.join(sorted(filtros)) or 'sem filtros'}), página {pagina}: {len(linhas)} de {total} vendas",
    )
    return linhas, total


def excluir_venda_web(id_venda, request_id):
    """Exclui a venda. Retorna True se ela existia e foi excluída, False se não existia."""
    try:
        with obter_conexao() as conexao:
            excluida = conexao.execute("DELETE FROM venda WHERE id = %s RETURNING id", (id_venda,)).fetchone()
    except psycopg.Error:
        registrar_log("ERROR", "web", "excluir_venda", "error", request_id, "Falha ao excluir venda")
        raise
    if excluida is None:
        registrar_log("WARN", "web", "excluir_venda", "error", request_id, f"Venda {id_venda} não encontrada")
        return False
    registrar_log("INFO", "web", "excluir_venda", "success", request_id, f"Venda {id_venda} excluída")
    return True
