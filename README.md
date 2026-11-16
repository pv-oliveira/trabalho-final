# trabalho-final

CRUD de vendas de produtos com **Web + CLI** sobre a mesma API REST e o mesmo banco.
Tudo roda via **Docker Compose** — no host basta Docker, Docker Compose, navegador e terminal.

Especificação do projeto: [`docs/especificacao.md`](docs/especificacao.md).

## Stack

| Camada | Tecnologia | Arquivo |
|---|---|---|
| Web | HTML + CSS + JavaScript puro, servido por nginx | `index.html` |
| CLI | Python 3.12, só biblioteca padrão (`argparse`, `urllib`) | `cli.py` |
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
| POST | `/api/web/login` | usuário e senha no corpo | `200 {"token","usuario","nome","validade_segundos"}`; `400` sem campos; `401` credenciais inválidas |
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
4. **Selecionar e excluir:** marque as caixas das vendas (ou a caixa do cabeçalho, para todas as da página) e clique em **Excluir selecionadas (N)**. A seleção vale só para a página exibida: trocar de página ou de ordenação a limpa, para nunca excluir uma venda que não está na tela. Depois da confirmação, as exclusões são feitas em paralelo e a tabela é recarregada; se alguma falhar, a mensagem diz quais, e elas continuam selecionadas para tentar de novo. Se a página ficar vazia, a tabela volta para a última página existente.
5. **Editar:** o botão **Editar** de cada linha carrega a venda no formulário da #2. Se o formulário tiver alterações não salvas, a página pede confirmação antes de descartá-las. Ao salvar ou cadastrar uma venda, a tabela é atualizada.
6. **Falhas:** se a API responder erro ou estiver fora, a tabela continua mostrando a última página carregada com sucesso, e o próximo clique parte dela. Cliques rápidos em sequência mostram sempre o resultado do último.

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

Cada página tem 5 vendas, no mesmo formato devolvido pela #2. A contagem e a página são lidas na mesma transação (`REPEATABLE READ`), então o `total` sempre bate com as vendas devolvidas. Em caso de empate na coluna escolhida, o `id` desempata, para que nenhuma venda se repita ou suma entre as páginas. Uma página depois da última volta com `vendas: []` e o `total_paginas` atual. A coluna de ordenação vem de uma lista fixa no `db.py`, então o texto enviado pelo usuário nunca entra no SQL.

Pelo terminal (com o `TOKEN` obtido como na #2):

```bash
curl -s "http://localhost:8080/api/web/records?pagina=2&ordenar=valor_total&direcao=asc" \
  -H "Authorization: Bearer $TOKEN"
curl -i -X DELETE http://localhost:8080/api/web/records/1 -H "Authorization: Bearer $TOKEN"
```

### Logs

A listagem (leitura) e a exclusão registram uma linha na camada `api` e outra na camada `db`, com `client: "web"` e o mesmo `request_id`. Os valores de `operation` são `listar_vendas` e `excluir_venda`. Excluir uma venda inexistente gera `WARN` com `status: "error"` nas duas camadas. Parâmetros inválidos na listagem são recusados antes de chegar ao banco, então geram `WARN` só na camada `api`. Falhas no banco geram `ERROR`.

## #4 — Busca avançada com filtros combináveis na Web

### Usar pela Web

1. Faça login em <http://localhost:8080>. O quadro **Busca avançada** fica no cartão **Vendas cadastradas**, acima da tabela.
2. Preencha um ou mais filtros e clique em **Buscar**. A consulta é feita em segundo plano (`fetch`), sem recarregar a página, e a tabela passa a mostrar só as vendas encontradas. Abaixo dos botões aparece um resumo dos filtros aplicados, e o rodapé mostra quantas vendas foram encontradas.
3. Paginação, ordenação pelas colunas, edição e exclusão continuam funcionando sobre o resultado da busca. Os filtros aplicados só mudam ao clicar em **Buscar** de novo: editar os campos sem buscar não altera a tabela.
4. **Limpar filtros** apaga os campos e volta à listagem completa da #3, na primeira página.
5. Se a data final for anterior à inicial, a página mostra o erro no campo e não faz a consulta. Se a API recusar algum filtro, a mensagem dela aparece abaixo da tabela, a tabela continua com o último resultado válido e o formulário volta a mostrar os filtros que continuam aplicados.
6. Durante a exclusão de vendas selecionadas, **Buscar**, **Limpar filtros**, a paginação e a ordenação ficam desabilitadas até a exclusão terminar.

### Filtros disponíveis

Os filtros são combinados com **E** (a venda precisa atender a todos os informados). Filtro ausente ou vazio não restringe a busca.

| Parâmetro | No formulário | Regra |
|---|---|---|
| `produto_id` | Produto | Número de um produto do catálogo (inteiro positivo). Um número válido sem produto cadastrado devolve zero vendas |
| `categoria` | Categoria | Nome exato de uma categoria do catálogo (hoje `Alimentos`, `Bebidas`, `Higiene` ou `Limpeza`, com a inicial maiúscula). A lista da tela vem do banco; uma categoria sem produtos devolve zero vendas, como o `produto_id` sem produto |
| `data_inicio` | Data inicial | `AAAA-MM-DD`. Vendas a partir dessa data, inclusive |
| `data_fim` | Data final | `AAAA-MM-DD`. Vendas até essa data, inclusive. Não pode ser anterior a `data_inicio` |

`data_inicio` e `data_fim` formam o filtro de **período** e podem ser usados juntos ou separados. Além dos filtros, a busca aceita `pagina`, `ordenar` e `direcao`, com os mesmos valores e padrões da listagem da #3 (5 vendas por página).

### Endpoint desta issue

Exige `Authorization: Bearer <token>`. Sem token ou com token inválido, responde `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/web/records/search` | `200 {"vendas", "pagina", "itens_por_pagina", "total", "total_paginas", "ordenar", "direcao", "filtros"}`; `400` filtros ou parâmetros inválidos |

A resposta tem o mesmo formato da listagem da #3, mais `filtros`, com os filtros aplicados (`null` nos ausentes). O `total` e o `total_paginas` contam só as vendas encontradas. No `400`, `campos` traz a mensagem de cada parâmetro recusado:

```json
{"erro": "Parâmetros inválidos.", "campos": {"data_fim": "A data final não pode ser anterior à data inicial."}}
```

A consulta SQL é própria da busca Web (`buscar_vendas_web` no `db.py`). As condições do `WHERE` vêm de uma lista fixa (`CONDICOES_BUSCA_WEB`) e só entram as dos filtros informados. A listagem completa da #3 (`listar_vendas_web`) usa a mesma consulta sem filtros, então as duas não divergem. Os valores vão como parâmetros, então o texto enviado pelo usuário nunca entra no SQL. A contagem e a página são lidas na mesma transação, como na listagem.

### Exemplos de uso

Pelo terminal (com o `TOKEN` obtido como na #2):

```bash
# 1 filtro: vendas de Bebidas
curl -s "http://localhost:8080/api/web/records/search?categoria=Bebidas" -H "Authorization: Bearer $TOKEN"

# 2 filtros: vendas do produto 1 a partir de 01/10/2026
curl -s "http://localhost:8080/api/web/records/search?produto_id=1&data_inicio=2026-10-01" \
  -H "Authorization: Bearer $TOKEN"

# 3 filtros: Alimentos em outubro de 2026, maiores valores primeiro, página 2
curl -s "http://localhost:8080/api/web/records/search?categoria=Alimentos&data_inicio=2026-10-01&data_fim=2026-10-31&ordenar=valor_total&direcao=desc&pagina=2" \
  -H "Authorization: Bearer $TOKEN"
```

### Logs

Cada busca registra uma linha na camada `api` e outra na camada `db`, com `client: "web"`, `operation: "buscar_vendas"` e o mesmo `request_id`. A `message` lista os nomes dos filtros usados (ex.: `Busca (categoria, data_inicio): 4 vendas, página 1 de 1`). Filtros ou parâmetros inválidos são recusados antes de chegar ao banco, então geram `WARN` só na camada `api`. Falhas no banco geram `ERROR`.

## #5 — Exportação CSV na Web

### Exportar pela Web

1. Faça login em <http://localhost:8080> e, se quiser, aplique filtros na **Busca avançada** (#4) e escolha a ordenação clicando nas colunas.
2. Clique em **Exportar CSV**, no cabeçalho do cartão **Vendas cadastradas**. O navegador baixa um arquivo `vendas-AAAAMMDD-HHMMSS.csv`, com a data e a hora no fuso `FUSO_HORARIO`.
3. O arquivo traz **todas** as vendas dos filtros aplicados, e não só a página que está na tela, na mesma ordenação da tabela. Sem filtros, traz todas as vendas cadastradas.

Valem os filtros **aplicados**, os que aparecem em "Filtros aplicados": campos editados sem clicar em **Buscar** não entram na exportação. Se a API recusar algum parâmetro ou estiver fora do ar, a mensagem aparece abaixo da tabela. Uma sessão expirada volta para a tela de login.

### Formato do arquivo

O CSV segue o formato que o Excel em português abre direto, com colunas, acentos e números corretos:

- codificação UTF-8 com BOM e linhas terminadas em CRLF;
- separador `;` e campos com `;` ou aspas entre aspas (padrão CSV);
- datas em `DD/MM/AAAA` e valores com vírgula decimal e 2 casas, sem separador de milhar.

| Coluna | Conteúdo |
|---|---|
| `Nº` | Número da venda |
| `Data` | Data da venda |
| `Produto` | Nome do produto |
| `Categoria` | Categoria do produto |
| `Quantidade` | Quantidade vendida |
| `Valor unitário` | Valor unitário praticado na venda |
| `Valor total` | Quantidade × valor unitário |

```text
Nº;Data;Produto;Categoria;Quantidade;Valor unitário;Valor total
12;02/11/2026;Arroz 5 kg;Alimentos;3;26,00;78,00
9;20/10/2026;Refrigerante 2 L;Bebidas;2;9,90;19,80
```

Sem nenhuma venda nos filtros, o arquivo traz só a linha de cabeçalho.

### Endpoint desta issue

Exige `Authorization: Bearer <token>`. Sem token ou com token inválido, responde `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/web/records/export` | `200` com o CSV; `400` filtros ou parâmetros inválidos |

Aceita os filtros da busca da #4 (`produto_id`, `categoria`, `data_inicio`, `data_fim`), com as mesmas regras, e `ordenar`/`direcao` da listagem da #3 (padrão: data mais recente primeiro). Não há `pagina`: o arquivo sempre traz todos os resultados. No `400`, a resposta é JSON, igual à da busca (`{"erro": "Parâmetros inválidos.", "campos": {...}}`).

Cabeçalhos da resposta `200`:

- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="vendas-AAAAMMDD-HHMMSS.csv"`
- `Cache-Control: no-store`, para o navegador não guardar em cache os dados de quem está logado

A consulta SQL é própria da exportação Web (`exportar_vendas_web` no `db.py`): o mesmo `JOIN` e as mesmas condições de filtro da busca (`CONDICOES_BUSCA_WEB`), mas sem `LIMIT`/`OFFSET`. Os valores vão como parâmetros, como na busca.

### Exemplos de uso

Pelo terminal (com o `TOKEN` obtido como na #2), o `-OJ` salva o arquivo com o nome enviado pela API:

```bash
# todas as vendas
curl -s -OJ "http://localhost:8080/api/web/records/export" -H "Authorization: Bearer $TOKEN"

# Bebidas em outubro de 2026, maiores valores primeiro
curl -s -OJ "http://localhost:8080/api/web/records/export?categoria=Bebidas&data_inicio=2026-10-01&data_fim=2026-10-31&ordenar=valor_total&direcao=desc" \
  -H "Authorization: Bearer $TOKEN"
```

### Logs

Cada exportação registra uma linha na camada `api` e outra na camada `db`, com `client: "web"`, `operation: "exportar_vendas"` e o mesmo `request_id`. A `message` traz a quantidade de vendas e os nomes dos filtros usados (ex.: `4 vendas exportadas em vendas-20261113-104500.csv (categoria, data_inicio)`). Parâmetros inválidos geram `WARN` só na camada `api`, e falhas no banco geram `ERROR`.

## #6 — Dashboard com 3 gráficos na Web

### Usar pela Web

Depois do login, o cartão **Dashboard** aparece logo abaixo das boas-vindas, com o total de vendas registradas e o faturamento total (ambos de **todo o período**) e os 3 gráficos. Os valores vêm do banco pela API sempre que a página é aberta ou recarregada, ao clicar em **Atualizar** e automaticamente depois de cadastrar, editar ou excluir uma venda. O horário da última atualização aparece ao lado do botão.

Os gráficos são desenhados em HTML e CSS dentro do `index.html`, sem biblioteca externa. Com até 6 colunas, o valor aparece em cima de cada uma; com mais (o normal, com 12 meses), ele sai da coluna para não sobrepor as vizinhas: passe o mouse sobre a coluna para ver o valor exato (leitores de tela leem o valor de todas). Valor zero não desenha barra. Se a API falhar, os números anteriores saem da tela junto com a mensagem de erro, para não parecerem atuais.

### Os 3 indicadores

| Indicador | O que mede | Como é calculado |
|---|---|---|
| **Faturamento por mês** (colunas) | Quanto foi vendido, em reais, em cada mês | Soma de `venda.valor_total` agrupada pelo mês de `data_venda` (`date_trunc('month', ...)`). Mostra sempre os **12 meses de calendário até o mês atual** (gerados com `generate_series`), do mais antigo para o mais recente; **mês sem venda aparece com R$ 0,00**, para o eixo do tempo não pular meses. O "mês atual" usa o mesmo fuso da validação das vendas (`FUSO_HORARIO`) |
| **Faturamento por categoria** (barras) | Quanto cada categoria do catálogo representa nas vendas | Soma de `venda.valor_total` por `produto.categoria`, partindo da tabela `produto` com `LEFT JOIN` em `venda`: categorias sem venda aparecem com R$ 0,00. Ordenado do maior para o menor faturamento |
| **Top 5 produtos** (barras) | Os produtos mais vendidos em volume | Soma de `venda.quantidade` por produto, ordenada da maior para a menor; empate desempatado pelo nome. Mostra no máximo 5 produtos; produtos sem venda não entram |

O resumo acima dos gráficos traz `COUNT(*)` e `SUM(valor_total)` de **todas** as vendas, por isso o rótulo diz "todo o período": com histórico maior que 12 meses, ele é maior que a soma das colunas do gráfico de meses. Todas as consultas rodam na mesma transação, `REPEATABLE READ` e somente leitura, então o resumo e os 3 gráficos sempre se referem às mesmas vendas, mesmo se outra sessão alterar dados no meio. As consultas são próprias do dashboard Web (`CONSULTAS_DASHBOARD_WEB`, usadas por `indicadores_dashboard_web` no `db.py`).

### Endpoint desta issue

Exige `Authorization: Bearer <token>`; sem token ou com token inválido responde `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/web/dashboard` | `200` com `resumo`, `faturamento_por_mes`, `faturamento_por_categoria` e `top_produtos` |

Valores em reais vêm como texto com 2 casas (`"1234.50"`), com a mesma regra (`valor_json`) usada em `/records` e no CSV.

```bash
curl -s http://localhost:8080/api/web/dashboard -H "Authorization: Bearer $TOKEN"
```

Exemplo com 6 vendas de agosto a outubro de 2026, consultado em outubro (os meses de nov/2025 a jul/2026 vêm com `"0.00"` e foram omitidos aqui):

```json
{
  "resumo": {"total_vendas": 6, "faturamento_total": "198.49"},
  "faturamento_por_mes": [
    {"mes": "2025-11", "total": "0.00"},
    "…",
    {"mes": "2026-08", "total": "55.80"},
    {"mes": "2026-09", "total": "100.00"},
    {"mes": "2026-10", "total": "42.69"}
  ],
  "faturamento_por_categoria": [
    {"categoria": "Alimentos", "total": "140.80"},
    {"categoria": "Bebidas", "total": "29.70"},
    {"categoria": "Higiene", "total": "25.00"},
    {"categoria": "Limpeza", "total": "2.99"}
  ],
  "top_produtos": [
    {"produto_id": 2, "produto_nome": "Feijão 1 kg", "quantidade": 10},
    {"produto_id": 10, "produto_nome": "Sabonete", "quantidade": 10},
    {"produto_id": 4, "produto_nome": "Refrigerante 2 L", "quantidade": 3},
    {"produto_id": 1, "produto_nome": "Arroz 5 kg", "quantidade": 2},
    {"produto_id": 7, "produto_nome": "Detergente 500 ml", "quantidade": 1}
  ]
}
```

### Logs

Cada carregamento registra uma linha na camada `api` e outra na camada `db`, com `operation: "dashboard"`, `client: "web"` e o mesmo `request_id`. A mensagem traz só a quantidade de vendas consideradas. Falha no banco gera `ERROR` nas duas camadas.

```bash
docker compose logs api | grep '"client": "web"' | grep '"operation": "dashboard"'
```

## #7 — Estrutura CLI: ajuda, subcomandos, healthcheck e token

A CLI é o arquivo único `cli.py`. Ela **não acessa o banco**: toda operação passa pelas rotas `/api/cli/*` da mesma API usada pela Web. Roda no serviço `cli` do Docker Compose (perfil `cli`), com a mesma imagem da API, então nada precisa ser instalado no host. Os comandos abaixo sobem `db` e `api` automaticamente, se ainda não estiverem rodando.

### Executar e listar comandos

```bash
docker compose run --rm cli              # sem argumentos: mostra a ajuda
docker compose run --rm cli --help       # ajuda geral, com a lista de subcomandos
docker compose run --rm cli login --help # ajuda de um subcomando
```

O `cli` não tem build próprio: usa a mesma imagem do serviço `api` (`<projeto>-app`). Assim, o `docker compose up --build` também atualiza a CLI, e o `docker compose run` seguinte já roda o `cli.py` novo. Se a imagem ainda não existir, o primeiro `run` a constrói.

| Subcomando | Rota | Autenticação | O que faz |
|---|---|---|---|
| `saude` | `GET /api/cli/health` | pública | Verifica se a API está no ar |
| `login [--usuario U]` | `POST /api/cli/token` | usuário e senha | Obtém um token de acesso |
| `sessao` | `GET /api/cli/sessao` | Bearer (`TOKEN_CLI`) | Mostra o usuário dono do token |

Códigos de saída: `0` sucesso, `1` erro da API, API fora do ar ou resposta inesperada, operação cancelada (ex.: exclusão não confirmada) ou falha ao gravar o CSV exportado (ex.: `exportar --arquivo` num arquivo que já existe), `2` uso incorreto (subcomando ou opção inválidos), `3` não autenticado (sem token, token inválido/expirado ou credenciais inválidas).

Erros são mostrados numa linha no stderr, sem traceback: `Erro: <mensagem da API> (HTTP <status>)` quando a API responde com erro, e `Erro: não foi possível conectar à API em <URL_API> (<motivo>)` quando ela está fora do ar ou a resposta não é da API (não é HTTP, não é um objeto JSON nem o CSV esperado, veio cortada).

### Obter o token e configurar a variável de ambiente

1. Peça o token com o usuário da Web (`ADMIN_USUARIO` / `ADMIN_SENHA`). A senha é digitada sem eco:

   ```bash
   docker compose run --rm cli login --usuario admin
   ```

   Só o token é impresso no stdout; as instruções vão para o stderr. Ele é assinado com `CHAVE_SECRETA` e **expira em 8 horas**.

2. Exporte o token na variável `TOKEN_CLI` do seu terminal. O `docker-compose.yml` repassa essa variável ao serviço `cli`:

   ```bash
   export TOKEN_CLI='<token impresso pelo login>'
   docker compose run --rm cli sessao   # Autenticado como Administrador (admin)
   ```

   Ou, num passo só (sem TTY, a senha vem de `SENHA_CLI`; rode antes o `docker compose up --build`, para o log do build não se misturar ao token). Passe `-e SENHA_CLI` **sem valor**, para o Compose copiar a variável do seu terminal: assim a senha não fica no histórico do shell nem na linha de comando:

   ```bash
   read -rs SENHA_CLI && export SENHA_CLI   # digita a senha sem eco
   export TOKEN_CLI=$(docker compose run --rm -T -e SENHA_CLI cli login --usuario admin)
   unset SENHA_CLI
   ```

Os comandos protegidos enviam `Authorization: Bearer <TOKEN_CLI>`. Espaços e quebras de linha que o copiar/colar trouxer para o `TOKEN_CLI` são ignorados. Sem `TOKEN_CLI`, ou com caracteres que um token nunca tem (ex.: aspas tipográficas `‘…’`), a CLI avisa e nem chama a API; com token inválido ou expirado, a API responde **HTTP 401** e a CLI pede um novo `login`. O token da CLI usa um salt próprio: token da Web não vale na CLI, e vice-versa.

Para usar a CLI contra outra API, defina `URL_API` (padrão `http://api:8000`, o serviço do Compose). A CLI **não segue redirecionamentos** (HTTP 3xx), para o token não ser reenviado a outro endereço: aponte `URL_API` direto para a API.

### Endpoints desta issue

| Método | Rota | Autenticação | Resposta |
|---|---|---|---|
| GET | `/api/cli/health` | pública | `200 {"status":"ok"}` |
| POST | `/api/cli/token` | usuário e senha no corpo | `200 {"token","usuario","nome","validade_segundos"}`; `400` sem usuário ou senha; `401` credenciais inválidas; `500` falha no banco |
| GET | `/api/cli/sessao` | Bearer (token da CLI) | `200 {"usuario","nome"}`; `401` sem token, token inválido, expirado ou emitido pela Web (`/api/web/login`) |

### Logs

Os eventos usam `client: "cli"`. A emissão de token registra `operation: "token"` na camada `api` e a consulta do usuário (`consultar_usuario_cli`, função `db.buscar_usuario_cli`) na camada `db`, com o mesmo `request_id`. Credenciais inválidas (`token`) e token ausente, inválido ou expirado (`sessao`) geram `WARN` com `status: "error"`. **Senha e token nunca são registrados.**

```bash
docker compose logs api | grep '"client": "cli"'
```

## #8 — Cadastro e edição de registros pela CLI

A CLI cadastra e edita vendas com os **mesmos 4 campos e as mesmas validações da Web** (#2). Quem valida é a API, então as mensagens de erro são as mesmas nos dois clientes. A venda fica no mesmo banco e aparece na Web na hora. Todos os comandos abaixo são protegidos: exigem o `TOKEN_CLI` obtido com `login` (#7).

### Comandos

| Subcomando | Rota | O que faz |
|---|---|---|
| `produtos` | `GET /api/cli/produtos` | Lista o catálogo, com o id usado em `--produto` |
| `criar --produto ID --quantidade N --valor-unitario VALOR --data AAAA-MM-DD` | `POST /api/cli/records` | Cadastra uma venda |
| `editar ID --produto ID --quantidade N --valor-unitario VALOR --data AAAA-MM-DD` | `PUT /api/cli/records/{id}` | Edita a venda nº `ID`, substituindo todos os campos |

As 4 opções são obrigatórias nos dois comandos, como no formulário da Web, porque a edição substitui a venda inteira. Opção faltando é erro de uso: código de saída `2`, com a linha de uso do comando e a mensagem em português (ex.: `Erro: faltam as opções obrigatórias: --quantidade, --valor-unitario, --data. Veja 'cli.py criar -h'.`).

| Opção | Campo | Regra (a mesma da Web) |
|---|---|---|
| `--produto` | `produto_id` | Id de um produto existente no catálogo (veja `produtos`) |
| `--quantidade` | `quantidade` | Inteiro de 1 a 10000 |
| `--valor-unitario` | `valor_unitario` | Maior que 0, até 100000.00, com no máximo 2 casas decimais; aceita vírgula ou ponto decimal (`25,50` ou `25.50`) |
| `--data` | `data_venda` | Data válida em `AAAA-MM-DD`, que não pode ser futura no fuso `FUSO_HORARIO` |

### Exemplos

```bash
export TOKEN_CLI='<token impresso pelo login>'   # veja a seção #7
docker compose run --rm cli produtos
docker compose run --rm cli criar --produto 1 --quantidade 3 --valor-unitario 25.50 --data 2026-10-15
docker compose run --rm cli editar 1 --produto 10 --quantidade 5 --valor-unitario 2.00 --data 2026-10-16
```

```text
  ID  Produto                Categoria  Preço
   1  Arroz 5 kg             Alimentos  R$ 27,90
   3  Biscoito recheado      Alimentos  R$ 4,50
 ...
Venda nº 1 cadastrada: 3 × Arroz 5 kg, total R$ 76,50, em 15/10/2026.
Venda nº 1 atualizada: 5 × Sabonete, total R$ 10,00, em 16/10/2026.
```

Os erros vão para o stderr, sem traceback. Num erro de validação, cada campo inválido aparece numa linha, com o nome da opção da CLI; editar um número que não existe mostra o `404` da API:

```text
$ docker compose run --rm cli criar --produto 1 --quantidade 0 --valor-unitario 1.234 --data 2030-01-01
Erro: Dados inválidos. (HTTP 422)
  --data: A data da venda não pode ser futura.
  --quantidade: A quantidade deve ser um número inteiro entre 1 e 10000.
  --valor-unitario: O valor unitário deve ser maior que zero, até 100000.00, com no máximo 2 casas decimais.

$ docker compose run --rm cli editar 999999 --produto 1 --quantidade 5 --valor-unitario 25.50 --data 2026-10-15
Erro: Venda não encontrada. (HTTP 404)
```

Códigos de saída: `0` sucesso, `1` erro da API (validação, venda inexistente, número da venda inválido) ou API fora do ar, `2` uso incorreto e `3` sem token ou com token inválido/expirado.

### Endpoints desta issue

Todos exigem `Authorization: Bearer <token da CLI>`. Sem token, com token inválido ou com token da Web, respondem `401`. As respostas são as mesmas das rotas `/api/web/*` da #2.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/cli/produtos` | `200 {"produtos": [{"id","nome","categoria","preco"}]}` |
| POST | `/api/cli/records` | `201` venda criada; `400` corpo que não é objeto JSON; `422 {"erro","campos"}` campos inválidos ou produto inexistente |
| PUT | `/api/cli/records/{id}` | `200` venda atualizada; `400` id ou corpo inválido; `404` se não existir; `422` campos inválidos |

### Logs

Cada operação registra uma linha na camada `api` e outra na camada `db`, com `client: "cli"` e o mesmo `request_id`. Os valores de `operation` são `listar_produtos`, `criar_venda` e `editar_venda`, os mesmos da Web: o `client` diz de onde veio a operação. O banco usa consultas próprias da CLI (`db.listar_produtos_cli`, `db.criar_venda_cli`, `db.editar_venda_cli`). Venda ou produto inexistente geram `WARN` nas duas camadas; dados ou id inválidos são recusados antes do banco e geram `WARN` só na camada `api`. O token nunca é registrado.

```bash
docker compose logs api | grep '"client": "cli"' | grep -E 'criar_venda|editar_venda'
```

## #9 — Listagem paginada, ordenação e exclusão pela CLI

A CLI lista as vendas uma página por vez, na ordem escolhida, e exclui vendas pelo número. Os dois comandos são protegidos (exigem o `TOKEN_CLI` obtido com `login`, seção #7), usam rotas próprias da CLI e consultas SQL próprias da CLI (`listar_vendas_cli` e `excluir_venda_cli` no `db.py`). A CLI não acessa o banco: uma venda excluída pela CLI some da Web na hora, porque as duas usam o mesmo banco pela API.

### Comandos

| Subcomando | Rota | O que faz |
|---|---|---|
| `listar [--pagina N] [--tamanho N] [--ordenar CAMPO] [--direcao asc\|desc]` | `GET /api/cli/records` | Lista as vendas com paginação e ordenação |
| `excluir ID [--sim]` | `DELETE /api/cli/records/{id}` | Exclui a venda nº `ID` |

| Opção de `listar` | Valores | Padrão |
|---|---|---|
| `--pagina` | inteiro de 1 a 100000 | `1` |
| `--tamanho` | vendas por página, inteiro de 1 a 100 | `10` |
| `--ordenar` | `id`, `data_venda`, `produto_nome`, `categoria`, `quantidade`, `valor_total` | `data_venda` |
| `--direcao` | `asc` (crescente) ou `desc` (decrescente) | `desc` |

Em caso de empate na coluna escolhida, o número da venda desempata, então nenhuma venda se repete ou some entre as páginas. A contagem e a página são lidas na mesma transação, então o total do rodapé sempre bate com as vendas mostradas.

O `excluir` **pede confirmação** (`[s/N]`) antes de apagar, porque a exclusão não pode ser desfeita. Em scripts, ou com `docker compose run -T` (sem terminal para responder), use `--sim` (ou `-s`) para excluir sem perguntar. Qualquer resposta que não seja `s`/`sim`, a falta de terminal sem `--sim` ou Ctrl+C cancelam a exclusão com a mesma mensagem: "Exclusão cancelada; nenhuma venda foi excluída." (código `1`).

### Exemplos

```bash
export TOKEN_CLI='<token impresso pelo login>'   # veja a seção #7
docker compose run --rm cli listar
docker compose run --rm cli listar --pagina 2 --tamanho 5 --ordenar valor_total --direcao asc
docker compose run --rm cli excluir 3            # pergunta antes de excluir
docker compose run --rm -T cli excluir 3 --sim   # sem perguntar
```

```text
Nº  Data        Produto            Categoria  Qtd.  Valor total
 3  03/10/2026  Biscoito recheado  Alimentos     4     R$ 38,00
 4  04/10/2026  Refrigerante 2 L   Bebidas       5     R$ 62,50
 7  07/10/2026  Detergente 500 ml  Limpeza       3     R$ 64,50
11  11/10/2026  Creme dental       Higiene       2     R$ 67,00
 8  08/10/2026  Sabão em pó 1 kg   Limpeza       4     R$ 98,00
Página 2 de 3 · 12 vendas · 5 por página · ordenado por valor_total (crescente)

Excluir a venda nº 3? Esta ação não pode ser desfeita. [s/N] s
Venda nº 3 excluída.
```

Sem vendas, `listar` mostra "Nenhuma venda cadastrada."; uma página além da última mostra "A página 9 não existe: há 3 páginas.". Os erros vão para o stderr, sem traceback, com o nome da opção que causou cada um:

```text
$ docker compose run --rm cli listar --pagina 0 --tamanho 500
Erro: Parâmetros inválidos. (HTTP 400)
  --pagina: A página deve ser um número inteiro entre 1 e 100000.
  --tamanho: O tamanho da página deve ser um número inteiro entre 1 e 100.

$ docker compose run --rm cli excluir 999 --sim
Erro: Venda não encontrada. (HTTP 404)
```

Códigos de saída: `0` sucesso; `1` erro da API (parâmetro inválido, venda inexistente) ou exclusão cancelada; `2` uso incorreto (ex.: `--ordenar preco`, `--direcao cima`); `3` sem token ou token inválido.

### Endpoints desta issue

Exigem `Authorization: Bearer <token da CLI>`; sem token, com token inválido ou com token da Web respondem `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/cli/records?pagina=&tamanho=&ordenar=&direcao=` | `200 {"vendas", "pagina", "itens_por_pagina", "total", "total_paginas", "ordenar", "direcao"}`; `400 {"erro", "campos"}` com parâmetros inválidos |
| DELETE | `/api/cli/records/{id}` | `200 {"id", "mensagem"}`; `400` número inválido; `404` venda inexistente |

### Logs

A listagem (leitura) e a exclusão registram uma linha na camada `api` e outra na camada `db`, com `client: "cli"` e o mesmo `request_id`. Os valores de `operation` são `listar_vendas` e `excluir_venda`. Excluir uma venda inexistente gera `WARN` nas duas camadas; parâmetros ou número inválidos são recusados antes do banco, com `WARN` só na camada `api`; falha no banco gera `ERROR`.

```bash
docker compose logs api | grep '"client": "cli"' | grep -E 'listar_vendas|excluir_venda'
```

## #10 — Busca avançada com filtros combináveis pela CLI

O comando `buscar` filtra as vendas com **os mesmos filtros e as mesmas regras da busca da Web** (#4): os mesmos parâmetros dão as mesmas vendas nos dois clientes. O comando é protegido (exige o `TOKEN_CLI` obtido com `login`, seção #7) e usa uma rota própria da CLI e uma consulta SQL própria da CLI (`buscar_vendas_cli` no `db.py`). A CLI não acessa o banco: tudo passa pela API.

### Comando

| Subcomando | Rota | O que faz |
|---|---|---|
| `buscar [--produto ID] [--categoria NOME] [--data-inicio AAAA-MM-DD] [--data-fim AAAA-MM-DD] [--pagina N] [--tamanho N] [--ordenar CAMPO] [--direcao asc\|desc]` | `GET /api/cli/records/search` | Busca vendas com filtros combináveis |

### Filtros disponíveis

Os filtros são combinados com **E** (a venda precisa atender a todos os informados). Filtro omitido ou vazio não restringe a busca; sem nenhum filtro, `buscar` mostra todas as vendas.

| Opção | Parâmetro | Regra (a mesma da Web) |
|---|---|---|
| `--produto` | `produto_id` | Id de um produto do catálogo (veja `produtos`). Um número válido sem produto cadastrado devolve zero vendas |
| `--categoria` | `categoria` | Nome exato da categoria, com a inicial maiúscula (hoje `Alimentos`, `Bebidas`, `Higiene` ou `Limpeza`). Uma categoria sem produtos devolve zero vendas |
| `--data-inicio` | `data_inicio` | `AAAA-MM-DD`. Vendas a partir dessa data, inclusive |
| `--data-fim` | `data_fim` | `AAAA-MM-DD`. Vendas até essa data, inclusive. Não pode ser anterior a `--data-inicio` |

`--data-inicio` e `--data-fim` formam o filtro de **período** e podem ser usados juntos ou separados. Página, tamanho e ordenação têm as mesmas opções e os mesmos padrões do `listar` (#9): `--pagina` 1, `--tamanho` 10 (de 1 a 100), `--ordenar data_venda`, `--direcao desc`, com o número da venda desempatando.

### Exemplos

```bash
export TOKEN_CLI='<token impresso pelo login>'   # veja a seção #7
# 1 filtro: vendas de Bebidas
docker compose run --rm cli buscar --categoria Bebidas
# 2 filtros: vendas do produto 1 a partir de 01/10/2026
docker compose run --rm cli buscar --produto 1 --data-inicio 2026-10-01
# 3 filtros: Alimentos em outubro de 2026, maiores valores primeiro, 5 por página, página 2
docker compose run --rm cli buscar --categoria Alimentos --data-inicio 2026-10-01 --data-fim 2026-10-31 \
  --ordenar valor_total --direcao desc --tamanho 5 --pagina 2
```

A primeira linha mostra os filtros aplicados; depois vêm a tabela, no formato do `listar`, e o rodapé com o total de vendas encontradas:

```text
Filtros: categoria Alimentos, de 01/10/2026 até 31/10/2026
Nº  Data        Produto      Categoria  Qtd.  Valor total
 4  12/10/2026  Feijão 1 kg  Alimentos     4     R$ 34,00
 2  05/10/2026  Arroz 5 kg   Alimentos     2     R$ 55,80
Página 1 de 1 · 2 vendas encontradas · 10 por página · ordenado por data_venda (decrescente)
```

Sem resultado, `buscar` mostra "Nenhuma venda encontrada com esses filtros."; uma página além da última mostra "A página 5 não existe: há 2 páginas.". Os erros vão para o stderr, sem traceback, com as mesmas mensagens da busca da Web e o nome da opção que causou cada um:

```text
$ docker compose run --rm cli buscar --produto abc --data-inicio 2026-10-31 --data-fim 2026-10-01
Erro: Parâmetros inválidos. (HTTP 400)
  --data-fim: A data final não pode ser anterior à data inicial.
  --produto: O produto deve ser o número de um produto do catálogo.
```

Códigos de saída: `0` sucesso (mesmo sem vendas encontradas); `1` erro da API (filtro ou parâmetro inválido) ou API fora do ar; `2` uso incorreto (ex.: `--ordenar preco`); `3` sem token ou token inválido.

### Endpoint desta issue

Exige `Authorization: Bearer <token da CLI>`; sem token, com token inválido ou com token da Web responde `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/cli/records/search?produto_id=&categoria=&data_inicio=&data_fim=&pagina=&tamanho=&ordenar=&direcao=` | `200 {"vendas", "pagina", "itens_por_pagina", "total", "total_paginas", "ordenar", "direcao", "filtros"}`; `400 {"erro", "campos"}` com filtros ou parâmetros inválidos |

A resposta tem o formato da listagem da CLI (#9), mais `filtros`, com os filtros aplicados (`null` nos ausentes), como na busca da Web. Os filtros são validados pela mesma regra da Web, então os erros `400` são idênticos nos dois clientes. A consulta SQL é própria da busca CLI (`buscar_vendas_cli`): as condições do `WHERE` vêm de uma lista fixa (`CONDICOES_BUSCA_CLI`, igual à da Web) e só entram as dos filtros informados; os valores vão como parâmetros, então o texto enviado nunca entra no SQL. A contagem e a página são lidas na mesma transação, somente leitura.

### Logs

Cada busca registra uma linha na camada `api` e outra na camada `db`, com `client: "cli"`, `operation: "buscar_vendas"` e o mesmo `request_id`. A `message` lista os nomes dos filtros usados (ex.: `Busca (categoria, data_inicio): 2 vendas, página 1 de 1`). Filtros ou parâmetros inválidos são recusados antes do banco, com `WARN` só na camada `api`; falha no banco gera `ERROR`.

```bash
docker compose logs api | grep '"client": "cli"' | grep buscar_vendas
```

## #11 — Exportação CSV pela CLI

O comando `exportar` gera um CSV com **todas** as vendas dos filtros, sem limite de página; sem filtros, exporta todas as vendas cadastradas. Os filtros são os mesmos do `buscar` (#10) e da busca da Web (#4), e o arquivo tem o mesmo formato do CSV da Web (#5): para os mesmos parâmetros, os dois arquivos são idênticos. O comando é protegido (exige o `TOKEN_CLI` obtido com `login`, seção #7) e usa uma rota própria da CLI e uma consulta SQL própria da CLI (`exportar_vendas_cli` no `db.py`). A CLI não acessa o banco: tudo passa pela API.

### Comando

| Subcomando | Rota | O que faz |
|---|---|---|
| `exportar [--produto ID] [--categoria NOME] [--data-inicio AAAA-MM-DD] [--data-fim AAAA-MM-DD] [--ordenar CAMPO] [--direcao asc\|desc] [--arquivo CAMINHO [--substituir]]` | `GET /api/cli/records/export` | Exporta as vendas dos filtros em CSV |

- **Filtros**: `--produto`, `--categoria`, `--data-inicio` e `--data-fim`, combinados com **E** e com as mesmas regras do `buscar` (veja a tabela "Filtros disponíveis" da #10). Filtro omitido ou vazio não restringe.
- **Ordenação**: `--ordenar` e `--direcao`, com as mesmas colunas e os mesmos padrões do `listar` (#9): data mais recente primeiro, com o número da venda desempatando.
- **Sem paginação**: não há `--pagina` nem `--tamanho`; o arquivo sempre traz todos os resultados.
- **Destino**: sem `--arquivo` (`-a`), o CSV vai para a saída padrão; com `--arquivo`, é gravado no caminho dado, **dentro do contêiner**. O resumo ("N vendas exportadas para …") e os erros vão para o stderr, então nunca se misturam ao CSV.
- **Arquivo existente**: `--arquivo` nunca sobrescreve um arquivo sem pedir. Se ele já existir, o comando falha (código `1`) sem tocar nele: "Erro: vendas.csv já existe; use --substituir para sobrescrever.". Com `--substituir`, o arquivo é sobrescrito.

### Como acessar o arquivo gerado

A CLI roda num contêiner, então o arquivo precisa chegar ao host. Há duas formas.

**1. Saída redirecionada no host (recomendada).** O CSV sai no stdout e o shell do host grava o arquivo na pasta atual. Use `-T`: sem ele, o Docker abre um terminal virtual, que troca as quebras de linha e estraga o arquivo.

```bash
export TOKEN_CLI='<token impresso pelo login>'   # veja a seção #7
docker compose run --rm -T cli exportar > vendas.csv
ls -l vendas.csv     # o arquivo está na pasta atual do host
```

Se a API recusar algum parâmetro, o erro aparece no terminal e o código de saída é diferente de `0`, mas o shell já criou `vendas.csv` vazio. Para não sobrar um arquivo vazio, grave num arquivo temporário e só renomeie no sucesso: `docker compose run --rm -T cli exportar > tmp.csv && mv tmp.csv vendas.csv`.

**2. Volume montado.** Monte uma pasta do host no contêiner e use `--arquivo` com um caminho dentro dela. O `--user` faz o arquivo pertencer ao seu usuário do host. Com `--arquivo`, a CLI só grava depois de receber o CSV completo: um erro da API não cria nem altera o arquivo. Para gerar de novo um arquivo que já existe, acrescente `--substituir`.

```bash
docker compose run --rm -v "$PWD:/saida" --user "$(id -u):$(id -g)" cli exportar --arquivo /saida/vendas.csv
ls -l vendas.csv     # na pasta atual do host
```

### Exemplos

```bash
# todas as vendas
docker compose run --rm -T cli exportar > vendas.csv
# 1 filtro: vendas de Bebidas
docker compose run --rm -T cli exportar --categoria Bebidas > bebidas.csv
# 2 filtros: produto 1 a partir de 01/10/2026
docker compose run --rm -T cli exportar --produto 1 --data-inicio 2026-10-01 > arroz.csv
# 3 filtros: Alimentos em outubro de 2026, maiores valores primeiro, num volume montado
docker compose run --rm -v "$PWD:/saida" --user "$(id -u):$(id -g)" cli exportar \
  --categoria Alimentos --data-inicio 2026-10-01 --data-fim 2026-10-31 \
  --ordenar valor_total --direcao desc --arquivo /saida/alimentos-outubro.csv
```

```text
$ docker compose run --rm -T cli exportar --categoria Alimentos --data-inicio 2026-10-01 --data-fim 2026-10-31 > alimentos.csv
2 vendas exportadas para a saída padrão.
$ cat alimentos.csv
Nº;Data;Produto;Categoria;Quantidade;Valor unitário;Valor total
4;12/10/2026;Feijão 1 kg;Alimentos;4;8,50;34,00
2;05/10/2026;Arroz 5 kg;Alimentos;2;27,90;55,80
```

O formato é o da #5 (seção "Formato do arquivo"): UTF-8 com BOM, linhas em CRLF, separador `;`, datas em `DD/MM/AAAA` e vírgula decimal, pronto para abrir no Excel em português. Sem nenhuma venda nos filtros, o arquivo traz só a linha de cabeçalho.

Os erros vão para o stderr, sem traceback, com as mesmas mensagens da busca e o nome da opção que causou cada um:

```text
$ docker compose run --rm -T cli exportar --produto abc --data-inicio 2026-10-31 --data-fim 2026-10-01 > vendas.csv
Erro: Parâmetros inválidos. (HTTP 400)
  --data-fim: A data final não pode ser anterior à data inicial.
  --produto: O produto deve ser o número de um produto do catálogo.
```

Falhas ao gravar mostram o motivo em português: a pasta não existe, sem permissão de escrita, o caminho é uma pasta ou sem espaço em disco (ex.: `Erro: não foi possível gravar /saida/x/vendas.csv (a pasta não existe).`). Se quem lê a saída padrão fecha o pipe antes do fim (ex.: `exportar | head -1`), o comando para em silêncio, sem traceback.

Códigos de saída: `0` sucesso (mesmo sem vendas encontradas); `1` erro da API (filtro ou parâmetro inválido), API fora do ar, resposta que não é o CSV esperado, `--arquivo` que já existe (sem `--substituir`), falha ao gravar o arquivo ou a saída padrão, ou pipe fechado antes do fim; `2` uso incorreto (ex.: `--ordenar preco`); `3` sem token ou token inválido.

### Endpoint desta issue

Exige `Authorization: Bearer <token da CLI>`; sem token, com token inválido ou com token da Web responde `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/cli/records/export?produto_id=&categoria=&data_inicio=&data_fim=&ordenar=&direcao=` | `200` com o CSV; `400 {"erro", "campos"}` com filtros ou parâmetros inválidos |

Os filtros são validados pela mesma regra da busca (#4/#10) e a ordenação aceita as colunas da listagem da CLI (#9). `pagina` e `tamanho` são ignorados. A resposta `200` tem os mesmos cabeçalhos do CSV da Web: `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="vendas-AAAAMMDD-HHMMSS.csv"` e `Cache-Control: no-store`. A consulta SQL é própria da exportação CLI (`exportar_vendas_cli`): o mesmo `JOIN` e as mesmas condições da busca da CLI (`CONDICOES_BUSCA_CLI`), sem `LIMIT`/`OFFSET`, numa transação somente leitura. Os valores vão como parâmetros, então o texto enviado nunca entra no SQL.

### Logs

Cada exportação registra uma linha na camada `api` e outra na camada `db`, com `client: "cli"`, `operation: "exportar_vendas"` e o mesmo `request_id`. A `message` traz a quantidade de vendas e os nomes dos filtros usados (ex.: `5 vendas exportadas em vendas-20261116-143000.csv (categoria, data_inicio)` na `api` e `5 vendas lidas para exportação (categoria, data_inicio)` no `db`). Filtros ou parâmetros inválidos são recusados antes do banco, com `WARN` só na camada `api`; falha no banco gera `ERROR`. O token nunca vai para o log.

```bash
docker compose logs api | grep '"client": "cli"' | grep exportar_vendas
```

## #12 — Indicadores em texto pela CLI

O comando `indicadores` mostra no terminal o **resumo e os 3 indicadores do dashboard da Web (#6)**, na mesma ordem e com os mesmos valores: faturamento por mês, faturamento por categoria e top 5 produtos. Cada linha traz o valor exato e uma barra de texto proporcional ao maior valor do indicador, como as barras dos gráficos da Web.

### Comando

| Subcomando | Rota | O que faz |
|---|---|---|
| `indicadores` | `GET /api/cli/dashboard` | Mostra o resumo e os 3 indicadores em texto |

- **Mesmos indicadores e valores da Web**: as regras são as da tabela "Os 3 indicadores" da #6 — 12 meses de calendário até o mês atual (no fuso `FUSO_HORARIO`), categorias do catálogo mesmo sem venda, top 5 por unidades vendidas com desempate pelo nome. A rota da CLI devolve exatamente o mesmo JSON de `/api/web/dashboard` (os testes comparam as duas respostas).
- **Mensagens de vazio iguais às da Web**: "Sem vendas nos últimos 12 meses." quando todos os meses estão zerados e "Sem vendas registradas." no top 5 sem vendas; as categorias aparecem mesmo zeradas.
- **Formato brasileiro**: valores em reais (`R$ 1.234,50`), meses abreviados como no gráfico (`out/2026`) e milhar com ponto (`1.234 unidades`).
- **Barras**: o maior valor de cada indicador tem 30 blocos (`█`) e os outros, proporcionais. Zero não desenha barra; um valor positivo pequeno tem pelo menos 1 bloco, para não parecer zero.

### Exemplo

Com as mesmas 6 vendas do exemplo da #6, consultado em outubro de 2026:

```text
$ docker compose run --rm cli indicadores
Vendas registradas (todo o período): 6
Faturamento total (todo o período): R$ 198,49

Faturamento por mês (últimos 12 meses)
  nov/2025    R$ 0,00
  …
  jul/2026    R$ 0,00
  ago/2026   R$ 55,80  █████████████████
  set/2026  R$ 100,00  ██████████████████████████████
  out/2026   R$ 42,69  █████████████

Faturamento por categoria
  Alimentos  R$ 140,80  ██████████████████████████████
  Bebidas     R$ 29,70  ██████
  Higiene     R$ 25,00  █████
  Limpeza      R$ 2,99  █

Top 5 produtos (mais unidades vendidas)
  1. Feijão 1 kg        10 unidades  ██████████████████████████████
  2. Sabonete           10 unidades  ██████████████████████████████
  3. Refrigerante 2 L    3 unidades  █████████
  4. Arroz 5 kg          2 unidades  ██████
  5. Detergente 500 ml    1 unidade  ███
```

(Na saída real aparecem os 12 meses; os de dez/2025 a jun/2026 foram omitidos aqui.) Para conferir, abra o dashboard da Web: os números são os mesmos. Depois de cadastrar, editar ou excluir uma venda (pela Web ou pela CLI), basta rodar o comando de novo.

Se a API responder sem os indicadores no formato esperado (ex.: CLI e API de versões diferentes), nada é impresso e o erro diz que a resposta é inesperada, e não que a conexão falhou: `Erro: resposta inesperada da API em http://api:8000 (a resposta não tem os indicadores esperados).`. A resposta é validada inteira (campos, tipos, meses de `01` a `12` e valores com 2 casas) antes de qualquer linha ser escrita. Se quem lê a saída fechar o pipe antes do fim (ex.: `indicadores | head -3`), o comando para em silêncio, sem traceback, como no `exportar`.

Códigos de saída: `0` sucesso (mesmo sem vendas); `1` erro da API, API fora do ar, resposta inesperada, falha ao escrever na saída padrão ou pipe fechado antes do fim; `3` sem token ou token inválido.

### Endpoint desta issue

Exige `Authorization: Bearer <token da CLI>`; sem token, com token inválido ou com token da Web responde `401`.

| Método | Rota | Resposta |
|---|---|---|
| GET | `/api/cli/dashboard` | `200` com `resumo`, `faturamento_por_mes`, `faturamento_por_categoria` e `top_produtos` (mesmo formato de `/api/web/dashboard`) |

As consultas SQL são próprias da CLI (`CONSULTAS_DASHBOARD_CLI`, usadas por `indicadores_dashboard_cli` no `db.py`), com as mesmas regras e as mesmas constantes da Web (`MESES_DASHBOARD = 12`, `TOP_PRODUTOS_DASHBOARD = 5`). A execução é comum aos dois clientes (`_ler_indicadores`): as 4 consultas rodam numa transação `REPEATABLE READ` e somente leitura, então o resumo e os 3 indicadores sempre se referem às mesmas vendas, e o log sai com o `client` de quem chamou. Na API, as duas rotas usam `responder_indicadores`, que só muda a consulta do banco, e o JSON é montado por `indicadores_para_json`, com os valores em reais pela regra única `valor_json`.

```bash
curl -s http://localhost:8080/api/cli/dashboard -H "Authorization: Bearer $TOKEN_CLI"
```

### Logs

Cada consulta registra uma linha na camada `api` e outra na camada `db`, com `client: "cli"`, `operation: "dashboard"` e o mesmo `request_id`. A `message` traz só a quantidade de vendas consideradas (ex.: `Indicadores enviados (6 vendas)` na `api` e `Indicadores calculados sobre 6 vendas` no `db`). Token inválido é recusado antes do banco, com `WARN` só na camada `api`; falha no banco gera `ERROR`. O token nunca vai para o log.

```bash
docker compose logs api | grep '"client": "cli"' | grep '"operation": "dashboard"'
```

## Logs

Cada evento é uma linha JSON no stdout, com `timestamp`, `level`, `layer` (`api`/`db`), `client` (`web`/`cli`), `operation`, `status`, `request_id` e `message`. API e banco compartilham o mesmo `request_id` dentro de uma requisição (também devolvido no header `X-Request-ID`). A inicialização do banco usa `client: "sistema"` e `request_id: "inicializacao"`. Erros não previstos na API viram `500 {"erro": "Erro interno"}` com log `ERROR`. Nas rotas `/api/*`, rota inexistente e método não permitido também respondem em JSON (`404 {"erro": "Rota não encontrada"}`, `405 {"erro": "Método não permitido"}`, este com o header `Allow`), com log `WARN` e o `client` da rota (`cli` em `/api/cli/*`).

```bash
docker compose logs -f api                           # acompanhar
docker compose logs api | grep '"request_id": "req-…"'  # correlacionar uma operação
```

## Testes e cobertura

```bash
docker compose --profile testes run --rm --build testes
```

Sobe um PostgreSQL descartável (`db-testes`, em memória) e roda o pytest exigindo **100% de cobertura de linhas** em `api.py`, `db.py` e `cli.py` (`--cov-fail-under=100`). Os testes da CLI sobem a API num servidor HTTP local e executam os subcomandos de verdade contra ela. Para limpar: `docker compose --profile testes down`.
