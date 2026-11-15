"""CLI de vendas: acessa a API somente pelas rotas `/api/cli/*`, nunca o banco diretamente.

Execução (via Docker Compose, sem instalar nada no host):

    docker compose run --rm cli --help
    docker compose run --rm cli saude
    docker compose run --rm cli login --usuario admin
    TOKEN_CLI=<token> docker compose run --rm cli sessao
    TOKEN_CLI=<token> docker compose run --rm cli produtos
    TOKEN_CLI=<token> docker compose run --rm cli criar --produto 1 --quantidade 3 --valor-unitario 25.50 --data 2026-10-15
    TOKEN_CLI=<token> docker compose run --rm cli editar 7 --produto 1 --quantidade 5 --valor-unitario 25.50 --data 2026-10-15
"""

import argparse
import getpass
import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

URL_API_PADRAO = "http://api:8000"
TEMPO_LIMITE = 10  # segundos
VARIAVEL_TOKEN = "TOKEN_CLI"

# Códigos de saída: 0 sucesso, 1 erro da API ou de conexão, 2 uso incorreto (argparse), 3 não autenticado.
SAIDA_SUCESSO = 0
SAIDA_ERRO = 1
SAIDA_NAO_AUTENTICADO = 3

# Campos da venda na API -> opção da CLI, para mostrar cada erro do 422 junto da opção que o causou.
OPCOES_VENDA = {
    "produto_id": "--produto",
    "quantidade": "--quantidade",
    "valor_unitario": "--valor-unitario",
    "data_venda": "--data",
}


class ErroDaApi(Exception):
    """A API respondeu com erro (status HTTP >= 400)."""

    def __init__(self, status, corpo):
        super().__init__(status)
        self.status = status
        self.corpo = corpo

    @property
    def mensagem(self):
        if isinstance(self.corpo, dict) and isinstance(self.corpo.get("erro"), str):
            return self.corpo["erro"]
        return "resposta inesperada da API"

    @property
    def campos(self):
        """Erros por campo (`{"campo": "mensagem"}`) que a API devolve no 422, ou {} se não houver."""
        campos = self.corpo.get("campos") if isinstance(self.corpo, dict) else None
        return campos if isinstance(campos, dict) else {}


class ApiIndisponivel(Exception):
    """Não foi possível conectar à API."""


class TokenAusente(Exception):
    """O comando é protegido e TOKEN_CLI não foi informado."""


class TokenInvalido(Exception):
    """TOKEN_CLI tem caracteres que um token nunca tem (ex.: aspas tipográficas do copiar/colar)."""


class _SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    """Não segue redirecionamentos: o urllib reenviaria o `Authorization` para o novo endereço.

    A API nunca redireciona; um 3xx vira HTTPError e é tratado como resposta inesperada.
    """

    def redirect_request(self, *_args, **_kwargs):
        return None


_ABRIDOR = urllib.request.build_opener(_SemRedirecionamento)


def abrir_url(requisicao):
    """Envia a requisição, sem seguir redirecionamentos."""
    return _ABRIDOR.open(requisicao, timeout=TEMPO_LIMITE)


def url_api():
    return (os.environ.get("URL_API") or URL_API_PADRAO).rstrip("/")


def token_do_ambiente():
    """Token lido de TOKEN_CLI. Lança TokenAusente se estiver vazio e TokenInvalido se não for ASCII imprimível.

    Remove todo espaço e quebra de linha (das pontas e do meio), que o copiar/colar costuma trazer:
    o token assinado nunca os contém, e no cabeçalho HTTP eles fariam a requisição ser recusada.
    """
    token = "".join(os.environ.get(VARIAVEL_TOKEN, "").split())
    if not token:
        raise TokenAusente
    if not (token.isascii() and token.isprintable()):
        raise TokenInvalido
    return token


def requisitar(metodo, caminho, corpo=None, token=None):
    """Chama `/api/cli<caminho>` e retorna o JSON da resposta. Lança ErroDaApi ou ApiIndisponivel."""
    cabecalhos = {"Accept": "application/json"}
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"
    if token is not None:
        cabecalhos["Authorization"] = f"Bearer {token}"
    try:
        requisicao = urllib.request.Request(
            f"{url_api()}/api/cli{caminho}", data=dados, headers=cabecalhos, method=metodo
        )
        with abrir_url(requisicao) as resposta:
            conteudo = resposta.read()
    except urllib.error.HTTPError as erro:
        with erro:
            try:
                conteudo = json.loads(erro.read().decode("utf-8"))
            except (ValueError, http.client.HTTPException, OSError):
                conteudo = None  # corpo que não é JSON, ou cortado no meio
        raise ErroDaApi(erro.code, conteudo) from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as erro:
        # HTTPException: resposta que não é HTTP (ex.: URL_API apontando para o banco) ou cortada no meio.
        raise ApiIndisponivel(str(erro) or type(erro).__name__) from None
    except ValueError:
        # URL_API inválida ou cabeçalho recusado. Sem str(erro): a mensagem do Python repete o cabeçalho, com o token.
        raise ApiIndisponivel("URL ou requisição inválida") from None
    try:
        resultado = json.loads(conteudo.decode("utf-8"))
    except ValueError:
        raise ApiIndisponivel("a resposta não é JSON") from None
    if not isinstance(resultado, dict):
        raise ApiIndisponivel("a resposta não é um objeto JSON")
    return resultado


def _quantidade(numero, singular, plural):
    return f"{numero} {singular if numero == 1 else plural}"


def descrever_validade(segundos):
    """Validade do token em texto, na maior unidade exata: "8 horas", "1 hora", "90 minutos", "30 segundos"."""
    if segundos and segundos % 3600 == 0:
        return _quantidade(segundos // 3600, "hora", "horas")
    if segundos and segundos % 60 == 0:
        return _quantidade(segundos // 60, "minuto", "minutos")
    return _quantidade(segundos, "segundo", "segundos")


def perguntar(texto):
    """Lê uma resposta do terminal com o prompt no stderr, para o stdout ter só o resultado."""
    print(texto, end="", file=sys.stderr, flush=True)
    return input()


def comando_saude(_argumentos):
    """Healthcheck público: GET /api/cli/health."""
    corpo = requisitar("GET", "/health")
    print(f"API OK ({corpo.get('status')}) em {url_api()}")
    return SAIDA_SUCESSO


def comando_login(argumentos):
    """Obtém um token: POST /api/cli/token. Só o token vai para o stdout; o resto vai para o stderr."""
    usuario = argumentos.usuario or perguntar("Usuário: ")
    senha = os.environ.get("SENHA_CLI") or getpass.getpass("Senha: ")
    corpo = requisitar("POST", "/token", {"usuario": usuario, "senha": senha})
    print(corpo["token"])
    print(
        f"Token emitido para {corpo['nome']} ({corpo['usuario']}), válido por {descrever_validade(corpo['validade_segundos'])}.\n"
        f"Defina a variável de ambiente para usar os comandos protegidos:\n"
        f"  export {VARIAVEL_TOKEN}=<token acima>",
        file=sys.stderr,
    )
    return SAIDA_SUCESSO


def moeda(texto):
    """Valor da API ("1234.50") em reais no formato brasileiro ("R$ 1.234,50")."""
    return "R$ " + f"{Decimal(texto):,.2f}".translate(str.maketrans(",.", ".,"))


def data_brasileira(texto):
    """Data da API ("2026-10-15") no formato brasileiro ("15/10/2026")."""
    ano, mes, dia = texto.split("-")
    return f"{dia}/{mes}/{ano}"


def inteiro_ou_texto(texto):
    """Converte para int só o que for número inteiro; o resto vai como texto e a API o recusa com a mensagem da Web."""
    return int(texto) if texto.isascii() and texto.isdigit() else texto


def venda_dos_argumentos(argumentos):
    """Corpo JSON da venda, com os mesmos campos do formulário da Web."""
    return {
        "produto_id": inteiro_ou_texto(argumentos.produto),
        "quantidade": inteiro_ou_texto(argumentos.quantidade),
        "valor_unitario": argumentos.valor_unitario,
        "data_venda": argumentos.data,
    }


def resumo_da_venda(venda):
    return (
        f"{venda['quantidade']} × {venda['produto_nome']}, total {moeda(venda['valor_total'])}, "
        f"em {data_brasileira(venda['data_venda'])}"
    )


def comando_sessao(_argumentos):
    """Comando protegido: GET /api/cli/sessao com Authorization: Bearer <TOKEN_CLI>."""
    corpo = requisitar("GET", "/sessao", token=token_do_ambiente())
    print(f"Autenticado como {corpo['nome']} ({corpo['usuario']})")
    return SAIDA_SUCESSO


def comando_produtos(_argumentos):
    """Comando protegido: GET /api/cli/produtos. Mostra o catálogo com o id usado em --produto."""
    produtos = requisitar("GET", "/produtos", token=token_do_ambiente())["produtos"]
    largura_nome = max([len("Produto"), *(len(p["nome"]) for p in produtos)])
    largura_categoria = max([len("Categoria"), *(len(p["categoria"]) for p in produtos)])
    print(f"{'ID':>4}  {'Produto':<{largura_nome}}  {'Categoria':<{largura_categoria}}  Preço")
    for produto in produtos:
        print(
            f"{produto['id']:>4}  {produto['nome']:<{largura_nome}}  "
            f"{produto['categoria']:<{largura_categoria}}  {moeda(produto['preco'])}"
        )
    return SAIDA_SUCESSO


def comando_criar(argumentos):
    """Comando protegido: POST /api/cli/records."""
    venda = requisitar("POST", "/records", venda_dos_argumentos(argumentos), token=token_do_ambiente())
    print(f"Venda nº {venda['id']} cadastrada: {resumo_da_venda(venda)}.")
    return SAIDA_SUCESSO


def comando_editar(argumentos):
    """Comando protegido: PUT /api/cli/records/{id}. Substitui todos os campos, como a edição da Web."""
    caminho = f"/records/{urllib.parse.quote(argumentos.id, safe='')}"
    venda = requisitar("PUT", caminho, venda_dos_argumentos(argumentos), token=token_do_ambiente())
    print(f"Venda nº {venda['id']} atualizada: {resumo_da_venda(venda)}.")
    return SAIDA_SUCESSO


def _adicionar_campos_venda(parser):
    """As 4 opções obrigatórias da venda, as mesmas do formulário da Web."""
    obrigatorias = parser.add_argument_group("campos da venda (obrigatórios)")
    obrigatorias.add_argument("--produto", required=True, metavar="ID", help="id do produto (veja o comando 'produtos')")
    obrigatorias.add_argument("--quantidade", required=True, metavar="N", help="número inteiro de 1 a 10000")
    obrigatorias.add_argument(
        "--valor-unitario", required=True, metavar="VALOR", help="em reais, com ponto decimal (ex.: 25.50); até 100000.00"
    )
    obrigatorias.add_argument("--data", required=True, metavar="AAAA-MM-DD", help="data da venda, que não pode ser futura")


def _traduzir_ajuda(parser):
    """Títulos e ajuda do -h em português (o argparse os escreve em inglês)."""
    parser._optionals.title = "opções"
    parser._positionals.title = "argumentos"
    parser._actions[0].help = "mostra esta ajuda e sai"
    return parser


def criar_parser():
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="CLI de vendas. Usa a API em URL_API (padrão: http://api:8000).",
        epilog=f"Comandos protegidos leem o token da variável de ambiente {VARIAVEL_TOKEN} (obtenha com 'login').",
    )
    _traduzir_ajuda(parser)
    subcomandos = parser.add_subparsers(title="comandos", dest="comando", metavar="<comando>", required=True)

    saude = subcomandos.add_parser("saude", help="verifica se a API está no ar (público)")
    _traduzir_ajuda(saude).set_defaults(funcao=comando_saude)

    login = subcomandos.add_parser(
        "login",
        help="obtém um token de acesso",
        description="Obtém um token de acesso. A senha é pedida no terminal (ou lida de SENHA_CLI).",
    )
    _traduzir_ajuda(login).add_argument("--usuario", help="usuário (se omitido, é perguntado)")
    login.set_defaults(funcao=comando_login)

    sessao = subcomandos.add_parser("sessao", help=f"mostra o usuário do token em {VARIAVEL_TOKEN} (protegido)")
    _traduzir_ajuda(sessao).set_defaults(funcao=comando_sessao)

    produtos = subcomandos.add_parser("produtos", help="lista o catálogo de produtos (protegido)")
    _traduzir_ajuda(produtos).set_defaults(funcao=comando_produtos)

    criar = subcomandos.add_parser(
        "criar", help="cadastra uma venda (protegido)", description="Cadastra uma venda. Mesmos campos e validações da Web."
    )
    _adicionar_campos_venda(_traduzir_ajuda(criar))
    criar.set_defaults(funcao=comando_criar)

    editar = subcomandos.add_parser(
        "editar",
        help="edita uma venda (protegido)",
        description="Edita uma venda, substituindo todos os campos (como a edição da Web).",
    )
    _traduzir_ajuda(editar).add_argument("id", help="número da venda")
    _adicionar_campos_venda(editar)
    editar.set_defaults(funcao=comando_editar)

    return parser


def main(argv=None):
    argumentos = criar_parser().parse_args(argv)
    try:
        return argumentos.funcao(argumentos)
    except TokenAusente:
        print(f"Erro: comando protegido. Obtenha um token com 'login' e defina {VARIAVEL_TOKEN}.", file=sys.stderr)
        return SAIDA_NAO_AUTENTICADO
    except TokenInvalido:
        print(
            f"Erro: {VARIAVEL_TOKEN} tem caracteres inválidos (ex.: aspas tipográficas). "
            f"Copie o token de novo, sem aspas, ou gere outro com 'login'.",
            file=sys.stderr,
        )
        return SAIDA_NAO_AUTENTICADO
    except ErroDaApi as erro:
        print(f"Erro: {erro.mensagem} (HTTP {erro.status})", file=sys.stderr)
        for campo, mensagem in erro.campos.items():
            print(f"  {OPCOES_VENDA.get(campo, campo)}: {mensagem}", file=sys.stderr)
        if erro.status == 401 and argumentos.comando != "login":
            print(f"Token ausente, inválido ou expirado. Gere outro com 'login' e atualize {VARIAVEL_TOKEN}.", file=sys.stderr)
        return SAIDA_NAO_AUTENTICADO if erro.status == 401 else SAIDA_ERRO
    except ApiIndisponivel as erro:
        print(f"Erro: não foi possível conectar à API em {url_api()} ({erro}).", file=sys.stderr)
        return SAIDA_ERRO
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado.", file=sys.stderr)
        return SAIDA_ERRO


if __name__ == "__main__":
    sys.exit(main())
