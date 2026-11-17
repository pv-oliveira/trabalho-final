# Trabalho final

CRUD de vendas de produtos com **Web + CLI** sobre a mesma API REST e o mesmo banco.
Tudo roda via **Docker Compose** — no host basta Docker, Docker Compose, navegador e terminal.

Especificação do projeto: [`docs/especificacao.md`](docs/especificacao.md).

## Integrantes

- 2518007 Moisés Martins Bezerra Fonteles

- 2650488 Paulo Victor Morais Oliveira

- 2650668 Tibet Brasil Teixeira

## Stack

| Camada | Tecnologia | Arquivo |
|---|---|---|
| Web | HTML + CSS + JavaScript puro, servido por nginx | `index.html` |
| API | Python 3.12 + Flask | `api.py` |
| Banco | PostgreSQL 16 (driver psycopg 3) | `db.py` |
| Testes | pytest + pytest-cov | `tests/` |

## #1 — Estrutura Web

### Iniciar

```bash
docker compose up --build
```

Sobe três serviços:

- `db` — PostgreSQL (dados persistidos no volume `dados-banco`);
- `api` — Flask servido pelo gunicorn na porta interna 8000, sem root e sem as dependências de teste; na inicialização cria as tabelas (`usuario`, `produto`, `venda`), carrega o catálogo de produtos e o usuário inicial;
- `web` — nginx em <http://localhost:8080>, servindo `index.html` e encaminhando `/api/*` para a API.

Para parar: `docker compose down` (adicione `-v` para apagar os dados).

### Configuração e usuário inicial

As variáveis são lidas do ambiente ou de um arquivo `.env` na raiz (veja `.env.example`). Sem `.env`, valem os padrões de desenvolvimento do `docker-compose.yml`:

| Variável | Padrão | Uso |
|---|---|---|
| `ADMIN_USUARIO` | `admin` | Login do usuário inicial |
| `ADMIN_NOME` | `Administrador` | Nome exibido após o login |
| `ADMIN_SENHA` | `admin123` | Senha do usuário inicial (somente para desenvolvimento) |
| `CHAVE_SECRETA` | valor de desenvolvimento | Assina os tokens de sessão Web |
| `SENHA_BANCO` | `app` | Senha do PostgreSQL |
| `PORTA_WEB` | `8080` | Porta da Web no host |

```bash
cp .env.example .env   # edite os valores antes de subir
```

A senha **nunca é armazenada em texto puro**: na inicialização a API gera um hash (scrypt, via `werkzeug.security`) e grava apenas o hash na tabela `usuario`. Se `ADMIN_SENHA` ou `ADMIN_NOME` mudarem, os valores do usuário `ADMIN_USUARIO` são atualizados no próximo start. Se o próprio `ADMIN_USUARIO` mudar, um novo usuário é criado e o anterior **continua ativo** (não há edição nem remoção de usuários); para descartá-lo, recrie o banco com `docker compose down -v`. Senhas e tokens não são registrados em log.

### Login

1. Abra <http://localhost:8080>.
2. Entre com `ADMIN_USUARIO` / `ADMIN_SENHA`.
3. A página inicial mostra o nome do usuário logado (`ADMIN_NOME`) e o indicador de status da API (healthcheck). "Sair" encerra a sessão no navegador.

O token Web é assinado e expira em 8 horas; fica no `sessionStorage` e é enviado como `Authorization: Bearer <token>`.

### Endpoints desta issue

| Método | Rota | Autenticação | Resposta |
|---|---|---|---|
| GET | `/api/web/health` | pública | `200 {"status":"ok"}` |
| POST | `/api/web/login` | — | `200 {"token","usuario","nome"}`; `400` sem campos; `401` credenciais inválidas |
| GET | `/api/web/sessao` | Bearer | `200 {"usuario","nome"}`; `401` sem token ou token inválido |

Pelo terminal:

```bash
curl -i http://localhost:8080/api/web/health
curl -i -X POST http://localhost:8080/api/web/login \
  -H 'Content-Type: application/json' -d '{"usuario":"admin","senha":"admin123"}'
```

## Logs

Cada evento é uma linha JSON no stdout, com `timestamp`, `level`, `layer` (`api`/`db`), `client` (`web`/`cli`), `operation`, `status`, `request_id` e `message`. API e banco compartilham o mesmo `request_id` dentro de uma requisição (também devolvido no header `X-Request-ID`). A inicialização do banco usa `client: "sistema"` e `request_id: "inicializacao"`. Erros não previstos na API viram `500 {"erro": "Erro interno"}` com log `ERROR`.

```bash
docker compose logs -f api                           # acompanhar
docker compose logs api | grep '"request_id": "req-…"'  # correlacionar uma operação
```

## Testes e cobertura

```bash
docker compose --profile testes run --rm --build testes
```

Sobe um PostgreSQL descartável (`db-testes`, em memória) e roda o pytest exigindo **100% de cobertura de linhas** em `api.py` e `db.py` (`--cov-fail-under=100`). Para limpar: `docker compose --profile testes down`.
