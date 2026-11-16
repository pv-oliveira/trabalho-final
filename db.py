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


def _consultar_usuario(usuario, request_id, origem, operacao):
    """Busca o usuário pelo login. Retorna o registro (dict) ou None se não existir."""
    try:
        with obter_conexao() as conexao:
            linha = conexao.execute(
                "SELECT id, usuario, nome, senha_hash FROM usuario WHERE usuario = %s",
                (usuario,),
            ).fetchone()
    except psycopg.Error:
        registrar_log("ERROR", origem, operacao, "error", request_id, "Falha ao consultar usuário")
        raise
    mensagem = "Usuário encontrado" if linha else "Usuário não encontrado"
    registrar_log("INFO", origem, operacao, "success", request_id, mensagem)
    return linha


def buscar_usuario(usuario, request_id, origem):
    """Retorna o usuário (dict) ou None se não existir. Usado pelo login da Web."""
    return _consultar_usuario(usuario, request_id, origem, "consultar_usuario")


def buscar_usuario_cli(usuario, request_id):
    """Consulta da CLI: usuário que pede token em /api/cli/token, com log `client: cli`."""
    return _consultar_usuario(usuario, request_id, "cli", "consultar_usuario_cli")


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


def listar_produtos_cli(request_id):
    """Consulta da CLI: catálogo para o comando `produtos`, por categoria e nome."""
    try:
        with obter_conexao() as conexao:
            linhas = conexao.execute(
                "SELECT id, nome, categoria, preco FROM produto ORDER BY categoria, nome"
            ).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "cli", "listar_produtos", "error", request_id, "Falha ao listar produtos")
        raise
    registrar_log("INFO", "cli", "listar_produtos", "success", request_id, f"{len(linhas)} produtos listados")
    return linhas


def criar_venda_cli(venda, request_id):
    """Consulta da CLI: insere a venda e a retorna com o produto, numa única instrução.

    Lança ProdutoInexistente se o produto não existir.
    """
    try:
        with obter_conexao() as conexao:
            linha = conexao.execute(
                """
                WITH nova AS (
                    INSERT INTO venda (produto_id, quantidade, valor_unitario, data_venda)
                    VALUES (%(produto_id)s, %(quantidade)s, %(valor_unitario)s, %(data_venda)s)
                    RETURNING *
                )
                SELECT nova.id, nova.produto_id, produto.nome AS produto_nome, produto.categoria,
                       nova.quantidade, nova.valor_unitario, nova.valor_total, nova.data_venda
                FROM nova JOIN produto ON produto.id = nova.produto_id
                """,
                venda,
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        registrar_log("WARN", "cli", "criar_venda", "error", request_id, "Produto inexistente")
        raise ProdutoInexistente from None
    except psycopg.Error:
        registrar_log("ERROR", "cli", "criar_venda", "error", request_id, "Falha ao criar venda")
        raise
    registrar_log("INFO", "cli", "criar_venda", "success", request_id, f"Venda {linha['id']} criada")
    return linha


def editar_venda_cli(id_venda, venda, request_id):
    """Consulta da CLI: atualiza todos os campos e retorna a venda com o produto, ou None se ela não existir.

    Lança ProdutoInexistente se o produto não existir.
    """
    try:
        with obter_conexao() as conexao:
            linha = conexao.execute(
                """
                WITH atualizada AS (
                    UPDATE venda SET produto_id = %(produto_id)s, quantidade = %(quantidade)s,
                                     valor_unitario = %(valor_unitario)s, data_venda = %(data_venda)s
                    WHERE id = %(id)s
                    RETURNING *
                )
                SELECT atualizada.id, atualizada.produto_id, produto.nome AS produto_nome, produto.categoria,
                       atualizada.quantidade, atualizada.valor_unitario, atualizada.valor_total,
                       atualizada.data_venda
                FROM atualizada JOIN produto ON produto.id = atualizada.produto_id
                """,
                {**venda, "id": id_venda},
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        registrar_log("WARN", "cli", "editar_venda", "error", request_id, "Produto inexistente")
        raise ProdutoInexistente from None
    except psycopg.Error:
        registrar_log("ERROR", "cli", "editar_venda", "error", request_id, "Falha ao editar venda")
        raise
    if linha is None:
        registrar_log("WARN", "cli", "editar_venda", "error", request_id, f"Venda {id_venda} não encontrada")
    else:
        registrar_log("INFO", "cli", "editar_venda", "success", request_id, f"Venda {id_venda} atualizada")
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


# Filtros da busca avançada da Web (nome do parâmetro -> condição SQL), combinados com AND.
# Os valores só entram como parâmetros, nunca no texto da consulta.
CONDICOES_BUSCA_WEB = {
    "produto_id": "venda.produto_id = %(produto_id)s",
    "categoria": "produto.categoria = %(categoria)s",
    "data_inicio": "venda.data_venda >= %(data_inicio)s",
    "data_fim": "venda.data_venda <= %(data_fim)s",
}
CAMPOS_BUSCA_WEB = tuple(CONDICOES_BUSCA_WEB)


def _onde_busca_web(filtros):
    """Monta o WHERE com as condições dos filtros informados (vazio se não houver nenhum)."""
    condicoes = [sql.SQL(CONDICOES_BUSCA_WEB[campo]) for campo in CAMPOS_BUSCA_WEB if campo in filtros]
    return sql.SQL("WHERE ") + sql.SQL(" AND ").join(condicoes) if condicoes else sql.SQL("")


def buscar_vendas_web(filtros, pagina, itens_por_pagina, ordenar, direcao, request_id, operacao="buscar_vendas"):
    """Retorna (vendas da página, total de vendas encontradas) para a tabela da Web.

    `filtros` pode ter as chaves de CONDICOES_BUSCA_WEB; os ausentes não restringem a busca.
    `ordenar` deve ser uma chave de COLUNAS_ORDENACAO_WEB e `direcao`, "asc" ou "desc".
    O id desempata a ordenação, para a paginação ser estável. `operacao` vai para os logs.
    """
    onde = _onde_busca_web(filtros)
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
            # REPEATABLE READ: a contagem e a página veem o mesmo snapshot, então o total
            # bate com as linhas devolvidas mesmo se outra sessão alterar vendas no meio.
            conexao.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            total = conexao.execute(contagem, filtros).fetchone()["total"]
            linhas = conexao.execute(consulta, parametros).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "web", operacao, "error", request_id, "Falha ao consultar vendas")
        raise
    registrar_log(
        "INFO",
        "web",
        operacao,
        "success",
        request_id,
        f"Página {pagina}: {len(linhas)} de {total} vendas ({', '.join(sorted(filtros)) or 'sem filtros'})",
    )
    return linhas, total


def listar_vendas_web(pagina, itens_por_pagina, ordenar, direcao, request_id):
    """Retorna (vendas da página, total de vendas) para a listagem completa da Web."""
    return buscar_vendas_web({}, pagina, itens_por_pagina, ordenar, direcao, request_id, operacao="listar_vendas")


def exportar_vendas_web(filtros, ordenar, direcao, request_id):
    """Retorna todas as vendas que atendem aos filtros, sem paginação, para o CSV da Web.

    Mesmos filtros (`filtros`) e mesma ordenação (`ordenar`, `direcao`) da tabela, com o id
    desempatando. Sem filtros, retorna todas as vendas.
    """
    consulta = sql.SQL(
        """
        SELECT venda.id, venda.data_venda, produto.nome AS produto_nome, produto.categoria,
               venda.quantidade, venda.valor_unitario, venda.valor_total
        FROM venda JOIN produto ON produto.id = venda.produto_id
        {onde}
        ORDER BY {coluna} {sentido}, venda.id {sentido}
        """
    ).format(
        onde=_onde_busca_web(filtros),
        coluna=sql.SQL(COLUNAS_ORDENACAO_WEB[ordenar]),
        sentido=sql.SQL("DESC" if direcao == "desc" else "ASC"),
    )
    try:
        with obter_conexao() as conexao:
            linhas = conexao.execute(consulta, filtros).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "web", "exportar_vendas", "error", request_id, "Falha ao exportar vendas")
        raise
    registrar_log(
        "INFO",
        "web",
        "exportar_vendas",
        "success",
        request_id,
        f"{len(linhas)} vendas lidas para exportação ({', '.join(sorted(filtros)) or 'sem filtros'})",
    )
    return linhas


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


# Listagem e exclusão pela CLI (#9). Consultas próprias da CLI, separadas das da Web.
# Colunas pelas quais a listagem da CLI pode ser ordenada (nome do parâmetro -> coluna SQL).
# A API só aceita essas chaves, então nenhum texto do usuário entra na consulta.
COLUNAS_ORDENACAO_CLI = {
    "id": "venda.id",
    "data_venda": "venda.data_venda",
    "produto_nome": "produto.nome",
    "categoria": "produto.categoria",
    "quantidade": "venda.quantidade",
    "valor_total": "venda.valor_total",
}


def listar_vendas_cli(pagina, tamanho, ordenar, direcao, request_id):
    """Retorna (vendas da página, total de vendas) para o comando `listar` da CLI.

    `tamanho` é o número de vendas por página, escolhido por quem usa a CLI. `ordenar` deve ser uma
    chave de COLUNAS_ORDENACAO_CLI e `direcao`, "asc" ou "desc". O id desempata a ordenação, para a
    paginação ser estável.
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
    ).format(coluna=sql.SQL(COLUNAS_ORDENACAO_CLI[ordenar]), sentido=sentido)
    try:
        with obter_conexao() as conexao:
            # A contagem e a página veem o mesmo snapshot, então o total bate com as linhas devolvidas.
            conexao.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conexao.read_only = True
            total = conexao.execute("SELECT COUNT(*) AS total FROM venda").fetchone()["total"]
            linhas = conexao.execute(consulta, (tamanho, (pagina - 1) * tamanho)).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "cli", "listar_vendas", "error", request_id, "Falha ao listar vendas")
        raise
    registrar_log(
        "INFO", "cli", "listar_vendas", "success", request_id, f"Página {pagina}: {len(linhas)} de {total} vendas"
    )
    return linhas, total


def excluir_venda_cli(id_venda, request_id):
    """Exclui a venda pelo comando `excluir` da CLI. Retorna True se ela existia, False se não existia."""
    try:
        with obter_conexao() as conexao:
            excluida = conexao.execute("DELETE FROM venda WHERE id = %s RETURNING id", (id_venda,)).fetchone()
    except psycopg.Error:
        registrar_log("ERROR", "cli", "excluir_venda", "error", request_id, "Falha ao excluir venda")
        raise
    if excluida is None:
        registrar_log("WARN", "cli", "excluir_venda", "error", request_id, f"Venda {id_venda} não encontrada")
        return False
    registrar_log("INFO", "cli", "excluir_venda", "success", request_id, f"Venda {id_venda} excluída")
    return True


# Busca avançada pela CLI (#10). Consulta própria da CLI, com os mesmos filtros e a mesma regra da
# busca da Web (#4), para os mesmos parâmetros darem o mesmo resultado nos dois clientes.
# Filtros (nome do parâmetro -> condição SQL), combinados com AND. Os valores só entram como
# parâmetros, nunca no texto da consulta.
CONDICOES_BUSCA_CLI = {
    "produto_id": "venda.produto_id = %(produto_id)s",
    "categoria": "produto.categoria = %(categoria)s",
    "data_inicio": "venda.data_venda >= %(data_inicio)s",
    "data_fim": "venda.data_venda <= %(data_fim)s",
}
CAMPOS_BUSCA_CLI = tuple(CONDICOES_BUSCA_CLI)


def _onde_busca_cli(filtros):
    """Monta o WHERE da busca e da exportação da CLI com as condições dos filtros informados (vazio se não houver)."""
    condicoes = [sql.SQL(CONDICOES_BUSCA_CLI[campo]) for campo in CAMPOS_BUSCA_CLI if campo in filtros]
    return sql.SQL("WHERE ") + sql.SQL(" AND ").join(condicoes) if condicoes else sql.SQL("")


def buscar_vendas_cli(filtros, pagina, tamanho, ordenar, direcao, request_id):
    """Retorna (vendas da página, total de vendas encontradas) para o comando `buscar` da CLI.

    `filtros` pode ter as chaves de CONDICOES_BUSCA_CLI; os ausentes não restringem a busca.
    `tamanho` é o número de vendas por página. `ordenar` deve ser uma chave de COLUNAS_ORDENACAO_CLI
    e `direcao`, "asc" ou "desc". O id desempata a ordenação, para a paginação ser estável.
    """
    onde = _onde_busca_cli(filtros)
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
    ).format(onde=onde, coluna=sql.SQL(COLUNAS_ORDENACAO_CLI[ordenar]), sentido=sentido)
    parametros = {**filtros, "limite": tamanho, "deslocamento": (pagina - 1) * tamanho}
    try:
        with obter_conexao() as conexao:
            # A contagem e a página veem o mesmo snapshot, então o total bate com as linhas devolvidas.
            conexao.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conexao.read_only = True
            total = conexao.execute(contagem, filtros).fetchone()["total"]
            linhas = conexao.execute(consulta, parametros).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "cli", "buscar_vendas", "error", request_id, "Falha ao buscar vendas")
        raise
    registrar_log(
        "INFO",
        "cli",
        "buscar_vendas",
        "success",
        request_id,
        f"Página {pagina}: {len(linhas)} de {total} vendas ({', '.join(sorted(filtros)) or 'sem filtros'})",
    )
    return linhas, total


# Exportação CSV pela CLI (#11). Consulta própria da CLI: os mesmos filtros da busca da CLI (#10),
# sem LIMIT/OFFSET, para o arquivo trazer todas as vendas dos filtros, e não só uma página.
def exportar_vendas_cli(filtros, ordenar, direcao, request_id):
    """Retorna todas as vendas que atendem aos filtros, sem paginação, para o comando `exportar` da CLI.

    `filtros` pode ter as chaves de CONDICOES_BUSCA_CLI; os ausentes não restringem (sem filtros,
    retorna todas as vendas). `ordenar` deve ser uma chave de COLUNAS_ORDENACAO_CLI e `direcao`,
    "asc" ou "desc", com o id desempatando.
    """
    consulta = sql.SQL(
        """
        SELECT venda.id, venda.data_venda, produto.nome AS produto_nome, produto.categoria,
               venda.quantidade, venda.valor_unitario, venda.valor_total
        FROM venda JOIN produto ON produto.id = venda.produto_id
        {onde}
        ORDER BY {coluna} {sentido}, venda.id {sentido}
        """
    ).format(
        onde=_onde_busca_cli(filtros),
        coluna=sql.SQL(COLUNAS_ORDENACAO_CLI[ordenar]),
        sentido=sql.SQL("DESC" if direcao == "desc" else "ASC"),
    )
    try:
        with obter_conexao() as conexao:
            conexao.read_only = True
            linhas = conexao.execute(consulta, filtros).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "cli", "exportar_vendas", "error", request_id, "Falha ao exportar vendas")
        raise
    registrar_log(
        "INFO",
        "cli",
        "exportar_vendas",
        "success",
        request_id,
        f"{len(linhas)} vendas lidas para exportação ({', '.join(sorted(filtros)) or 'sem filtros'})",
    )
    return linhas


# Indicadores do dashboard da Web (#6). Consultas próprias, lidas no mesmo snapshot.
MESES_DASHBOARD = 12
TOP_PRODUTOS_DASHBOARD = 5


def indicadores_dashboard_web(data_referencia, request_id):
    """Retorna os 3 indicadores do dashboard da Web e um resumo, todos do mesmo snapshot.

    - resumo: total de vendas e faturamento de todo o período;
    - faturamento_por_mes: soma de valor_total em cada um dos MESES_DASHBOARD meses de calendário
      que terminam no mês de `data_referencia` (mês sem venda = 0, para o eixo do tempo não enganar);
    - faturamento_por_categoria: soma de valor_total por categoria do catálogo (categoria sem venda = 0);
    - top_produtos: os TOP_PRODUTOS_DASHBOARD produtos com mais unidades vendidas.
    """
    try:
        with obter_conexao() as conexao:
            # REPEATABLE READ: os três gráficos e o resumo veem as mesmas vendas.
            # Somente leitura: impede que uma escrita entre aqui por engano.
            conexao.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conexao.read_only = True
            resumo = conexao.execute(
                "SELECT COUNT(*) AS total_vendas, COALESCE(SUM(valor_total), 0) AS faturamento_total FROM venda"
            ).fetchone()
            por_mes = conexao.execute(
                """
                SELECT to_char(meses.mes, 'YYYY-MM') AS mes, COALESCE(SUM(venda.valor_total), 0) AS total
                FROM generate_series(
                    date_trunc('month', %(referencia)s::timestamp) - make_interval(months => %(meses)s - 1),
                    date_trunc('month', %(referencia)s::timestamp),
                    interval '1 month'
                ) AS meses(mes)
                LEFT JOIN venda ON date_trunc('month', venda.data_venda::timestamp) = meses.mes
                GROUP BY meses.mes
                ORDER BY meses.mes
                """,
                {"referencia": data_referencia, "meses": MESES_DASHBOARD},
            ).fetchall()
            por_categoria = conexao.execute(
                """
                SELECT produto.categoria, COALESCE(SUM(venda.valor_total), 0) AS total
                FROM produto LEFT JOIN venda ON venda.produto_id = produto.id
                GROUP BY produto.categoria
                ORDER BY total DESC, produto.categoria
                """
            ).fetchall()
            top_produtos = conexao.execute(
                """
                SELECT produto.id AS produto_id, produto.nome AS produto_nome, SUM(venda.quantidade) AS quantidade
                FROM venda JOIN produto ON produto.id = venda.produto_id
                GROUP BY produto.id, produto.nome
                ORDER BY quantidade DESC, produto.nome
                LIMIT %s
                """,
                (TOP_PRODUTOS_DASHBOARD,),
            ).fetchall()
    except psycopg.Error:
        registrar_log("ERROR", "web", "dashboard", "error", request_id, "Falha ao calcular os indicadores")
        raise
    registrar_log(
        "INFO",
        "web",
        "dashboard",
        "success",
        request_id,
        f"Indicadores calculados sobre {resumo['total_vendas']} vendas",
    )
    return {
        "resumo": resumo,
        "faturamento_por_mes": por_mes,
        "faturamento_por_categoria": por_categoria,
        "top_produtos": top_produtos,
    }
