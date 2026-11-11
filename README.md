# trabalho-final

CRUD de vendas de produtos com **Web + CLI** sobre a mesma API REST e o mesmo banco.
Tudo roda via **Docker Compose** — no host basta Docker, Docker Compose, navegador e terminal.

Especificação do projeto: [`docs/especificacao.md`](docs/especificacao.md).

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
| `FUSO_HORARIO` | `America/Fortaleza` | Fuso (nome IANA) que define a data de "hoje" na validação da data da venda |

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

## #2 — Cadastro e edição de registros na Web

Cada registro é uma **venda** (tabela `venda`) de um produto do catálogo fixo (tabela `produto`).

### Campos e validação

Todos os campos são obrigatórios e validados duas vezes: no navegador, antes do envio, e na API, que é quem decide.

| Campo | No formulário | Regra |
|---|---|---|
| `produto_id` | Produto (lista agrupada por categoria) | Inteiro de um produto existente no catálogo |
| `quantidade` | Quantidade | Inteiro de 1 a 10000 |
| `valor_unitario` | Valor unitário (R$) | Maior que 0, até 100000.00, com no máximo 2 casas decimais. Ao escolher o produto, o formulário sugere o preço do catálogo, que pode ser alterado (ex.: desconto) |
| `data_venda` | Data da venda | Data válida no formato `AAAA-MM-DD`, que não pode ser futura. O formulário sugere a data de hoje |

"Futura" quer dizer depois de hoje no fuso `FUSO_HORARIO` (padrão `America/Fortaleza`), e não em UTC. Assim a API, a Web e a CLI usam o mesmo "hoje", e às 22h em Fortaleza a data de amanhã continua sendo recusada. O formulário usa a data local do navegador, que é a mesma quando ele está nesse fuso. Se `FUSO_HORARIO` tiver um nome inválido, a API não sobe.

O `valor_total` (`quantidade × valor_unitario`) é calculado pelo banco e exibido no formulário enquanto os campos são preenchidos. Os limites de quantidade e valor mantêm o total dentro da coluna `NUMERIC(12,2)`.

### Cadastrar e editar pela Web

1. Faça login em <http://localhost:8080>.
2. **Cadastrar:** no cartão "Nova venda", escolha o produto, preencha quantidade, valor e data e clique em **Cadastrar venda**. A mensagem de sucesso mostra o número da venda criada.
3. **Editar:** informe o número da venda em **Editar venda nº** e clique em **Carregar**. O formulário passa a "Editar venda nº X". Altere os campos e clique em **Salvar alterações**, ou em **Cancelar edição** para voltar ao cadastro. Na edição, trocar o produto não altera o valor unitário, para não sobrescrever o valor já registrado na venda.

Erros de validação aparecem abaixo de cada campo, tanto os do navegador quanto os devolvidos pela API. Se a sessão expirar, a página volta para o login.

As vendas ficam no volume `dados-banco` e continuam lá depois de `docker compose down` / `up`. Só `docker compose down -v` apaga os dados.

### Endpoints desta issue

Todos exigem `Authorization: Bearer <token>`. Sem token ou com token inválido, respondem `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/web/produtos` | `200 {"produtos": [{"id","nome","categoria","preco"}]}` |
| GET | `/api/web/records/{id}` | `200` venda; `400` id inválido; `404` se não existir. Usado para carregar a venda na edição |
| POST | `/api/web/records` | `201` venda criada; `400` corpo que não é objeto JSON; `422` campos inválidos |
| PUT | `/api/web/records/{id}` | `200` venda atualizada; `400` id ou corpo inválido; `404` se não existir; `422` campos inválidos |

O `{id}` precisa ser um inteiro positivo. Valores como `-1`, `0` ou `abc` recebem `400 {"erro": "Número da venda inválido."}` (depois da autenticação: sem token, continua `401`).

A venda é devolvida como `{"id","produto_id","produto_nome","categoria","quantidade","valor_unitario","valor_total","data_venda"}`, com valores em texto (`"25.50"`) para não perder precisão. O `PUT` substitui todos os campos, então o corpo precisa ter os quatro. No `422`, o campo `campos` traz a mensagem de cada campo inválido, inclusive `produto_id` quando o produto não existe:

```json
{"erro": "Dados inválidos.", "campos": {"quantidade": "A quantidade deve ser um número inteiro entre 1 e 10000."}}
```

Pelo terminal:

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/web/login -H 'Content-Type: application/json' \
  -d '{"usuario":"admin","senha":"admin123"}' | sed 's/.*"token": *"\([^"]*\)".*/\1/')
curl -s http://localhost:8080/api/web/produtos -H "Authorization: Bearer $TOKEN"
curl -i -X POST http://localhost:8080/api/web/records -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"produto_id":1,"quantidade":3,"valor_unitario":"25.50","data_venda":"2026-10-15"}'
curl -i -X PUT http://localhost:8080/api/web/records/1 -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"produto_id":1,"quantidade":5,"valor_unitario":"25.50","data_venda":"2026-10-15"}'
```

### Logs

Cada operação registra uma linha na camada `api` e outra na camada `db`, com `client: "web"` e o mesmo `request_id`. Os valores de `operation` são `listar_produtos`, `consultar_venda`, `criar_venda` e `editar_venda`. Venda inexistente e produto inexistente geram `WARN` com `status: "error"` nas duas camadas. Dados ou id inválidos são recusados antes de chegar ao banco, então geram `WARN` só na camada `api`. Falhas no banco geram `ERROR`.

## #3 — Listagem paginada, ordenação e exclusão na Web

### Usar pela Web

1. Faça login em <http://localhost:8080>. O cartão **Vendas cadastradas** carrega as vendas do banco assim que a página abre.
2. **Colunas:** Nº, Data, Produto, Categoria, Qtd. e Valor total, com **5 vendas por página**. Use **‹ Anterior** e **Próxima ›** para navegar; o rodapé mostra a página atual, o total de páginas e o total de vendas.
3. **Ordenar:** clique no título de uma coluna. Um clique ordena por ela; outro inverte a direção (▲ crescente, ▼ decrescente). A ordem inicial é por data, da mais recente para a mais antiga.
4. **Selecionar e excluir:** marque as caixas das vendas (ou a caixa do cabeçalho, para todas as da página) e clique em **Excluir selecionadas (N)**. Depois da confirmação, as vendas são excluídas do banco e a tabela é recarregada. Se a página ficar vazia, a tabela volta para a última página existente.
5. **Editar:** o botão **Editar** de cada linha carrega a venda no formulário da #2. Ao salvar ou cadastrar uma venda, a tabela é atualizada.

### Endpoints desta issue

Ambos exigem `Authorization: Bearer <token>`. Sem token ou com token inválido, respondem `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/web/records` | `200 {"vendas", "pagina", "itens_por_pagina", "total", "total_paginas", "ordenar", "direcao"}`; `400` parâmetros inválidos |
| DELETE | `/api/web/records/{id}` | `200 {"id", "mensagem"}`; `400` id inválido (mesma regra da #2); `404` se a venda não existir |

Parâmetros da listagem, na query string (todos opcionais):

| Parâmetro | Valores | Padrão |
|---|---|---|
| `pagina` | inteiro de 1 a 100000 | `1` |
| `ordenar` | `id`, `data_venda`, `produto_nome`, `categoria`, `quantidade`, `valor_total` | `data_venda` |
| `direcao` | `asc` ou `desc` | `desc` |

Cada página tem 5 vendas, no mesmo formato devolvido pela #2. Em caso de empate na coluna escolhida, o `id` desempata, para que nenhuma venda se repita ou suma entre as páginas. Uma página depois da última volta com `vendas: []` e o `total_paginas` atual. A coluna de ordenação vem de uma lista fixa no `db.py`, então o texto enviado pelo usuário nunca entra no SQL.

Pelo terminal (com o `TOKEN` obtido como na #2):

```bash
curl -s "http://localhost:8080/api/web/records?pagina=2&ordenar=valor_total&direcao=asc" \
  -H "Authorization: Bearer $TOKEN"
curl -i -X DELETE http://localhost:8080/api/web/records/1 -H "Authorization: Bearer $TOKEN"
```

### Logs

A listagem (leitura) e a exclusão registram uma linha na camada `api` e outra na camada `db`, com `client: "web"` e o mesmo `request_id`. Os valores de `operation` são `listar_vendas` e `excluir_venda`. Excluir uma venda inexistente gera `WARN` com `status: "error"` nas duas camadas. Parâmetros inválidos na listagem são recusados antes de chegar ao banco, então geram `WARN` só na camada `api`. Falhas no banco geram `ERROR`.

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
