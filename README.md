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

O resumo acima dos gráficos traz `COUNT(*)` e `SUM(valor_total)` de **todas** as vendas, por isso o rótulo diz "todo o período": com histórico maior que 12 meses, ele é maior que a soma das colunas do gráfico de meses. Todas as consultas rodam na mesma transação, `REPEATABLE READ` e somente leitura, então o resumo e os 3 gráficos sempre se referem às mesmas vendas, mesmo se outra sessão alterar dados no meio. As consultas são próprias do dashboard Web (`indicadores_dashboard_web` no `db.py`).

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
docker compose logs api | grep '"operation": "dashboard"'
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

Códigos de saída: `0` sucesso, `1` erro da API ou API fora do ar, `2` uso incorreto (subcomando ou opção inválidos), `3` não autenticado (sem token, token inválido/expirado ou credenciais inválidas).

Erros são mostrados numa linha no stderr, sem traceback: `Erro: <mensagem da API> (HTTP <status>)` quando a API responde com erro, e `Erro: não foi possível conectar à API em <URL_API> (<motivo>)` quando ela está fora do ar ou a resposta não é da API (não é HTTP, não é um objeto JSON, veio cortada).

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

As 4 opções são obrigatórias nos dois comandos, como no formulário da Web, porque a edição substitui a venda inteira. Opção faltando é erro de uso (código de saída `2`, com a ajuda do comando).

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
