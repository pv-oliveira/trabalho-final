"""CLI de vendas: acessa a API somente pelas rotas `/api/cli/*`, nunca o banco diretamente.

Execução (via Docker Compose, sem instalar nada no host):

    docker compose run --rm cli --help
    docker compose run --rm cli saude
    docker compose run --rm cli login --usuario admin
    TOKEN_CLI=<token> docker compose run --rm cli sessao
"""

import argparse
import getpass
import http.client
import json
import os
import sys
import urllib.error
import urllib.request

URL_API_PADRAO = "http://api:8000"
TEMPO_LIMITE = 10  # segundos
VARIAVEL_TOKEN = "TOKEN_CLI"

# Códigos de saída: 0 sucesso, 1 erro da API ou de conexão, 2 uso incorreto (argparse), 3 não autenticado.
SAIDA_SUCESSO = 0
SAIDA_ERRO = 1
SAIDA_NAO_AUTENTICADO = 3


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


def comando_sessao(_argumentos):
    """Comando protegido: GET /api/cli/sessao com Authorization: Bearer <TOKEN_CLI>."""
    corpo = requisitar("GET", "/sessao", token=token_do_ambiente())
    print(f"Autenticado como {corpo['nome']} ({corpo['usuario']})")
    return SAIDA_SUCESSO


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
