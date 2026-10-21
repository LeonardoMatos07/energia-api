# Contrato da Fase 1

Fonte única de verdade entre os quatro repositórios. Qualquer mudança aqui exige
atualizar o `energia-api` (dono do schema) primeiro.

| Repositório | Papel |
|---|---|
| `energia-data` | Airflow: ingere o ONS, calcula os agregados e **só escreve** nas tabelas abaixo |
| `energia-api` | FastAPI: **dono do schema** (Alembic), leitura pública, newsletter, admin |
| `energia-web` | Next.js: portal público com ISR, área admin |
| `energia-infra` | Terraform: AWS `sa-east-1` |

## Convenções

- Nomes de tabelas, colunas, rotas e parâmetros em **português**, `snake_case`, sem acentos.
- Código (variáveis, funções, classes, comentários) em inglês. Documentação em português.
- `fonte`: `eolica`, `solar` ou `total` (soma das duas, só nos agregados).
- `subsistema`: `N`, `NE`, `S`, `SE`; `SIN` é o total nacional (só em `curtailment_mensal_sin`).
- `uf`: sigla com 2 letras maiúsculas.
- `mes`: `date` com o primeiro dia do mês (`2026-09-01`). Na API, `YYYY-MM`.
- Energia em MWh, dinheiro em R$ (`numeric`), percentuais de 0 a 100 com 2 casas.
- Horários do ONS são de Brasília; colunas `timestamptz` guardam UTC.
- Python: `uv`, `ruff` (lint e format), `mypy`, `pytest`. TypeScript: `pnpm`, ESLint,
  Prettier, Vitest. Commits em Conventional Commits.
- Versões fixadas exatamente, publicadas há pelo menos duas semanas.

## Schema do Postgres (dono: `energia-api`, Alembic)

O Postgres guarda só o que o portal lê. O histórico detalhado fica no S3.

### `usina`
Cadastro de usinas e conjuntos. Usinas despachadas individualmente (ONS "Tipo I") são
`tipo = 'usina'` com `conjunto_id` nulo.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | `text` PK | `id_ons` da usina ou do conjunto (`CJU_...`) |
| `tipo` | `text` NOT NULL | `usina` ou `conjunto` |
| `nome` | `text` NOT NULL | |
| `slug` | `text` NOT NULL UNIQUE | estável: `slug(nome)-slug(id)`, ex.: `sao-fernando-4-rnesf4` |
| `fonte` | `text` NOT NULL | `eolica` ou `solar` |
| `uf` | `char(2)` NOT NULL | |
| `subsistema` | `varchar(3)` NOT NULL | |
| `conjunto_id` | `text` NULL FK → `usina.id` | |
| `ceg` | `text` NULL | código ANEEL |
| `potencia_mw` | `numeric(10,3)` NULL | soma das unidades geradoras em operação |
| `ponto_conexao` | `text` NULL | nome do ponto de conexão à rede (conjuntos) |
| `agente_operador` | `text` NULL | |
| `atualizado_em` | `timestamptz` NOT NULL DEFAULT now() | |

Índices: `slug` (único), `(uf, fonte)`, `conjunto_id`, e busca por nome com
`lower(nome)` (ou `pg_trgm`, se disponível).

### `curtailment_mensal_usina`
Uma linha por usina ou conjunto e mês. Para conjuntos, os números são os oficiais do ONS
(GNRa); para usinas, a GNRa rateada (soma das usinas = conjunto).

| Coluna | Tipo |
|---|---|
| `usina_id` | `text` FK → `usina.id` |
| `mes` | `date` |
| `geracao_verificada_mwh` | `numeric(14,3)` NOT NULL |
| `energia_cortada_mwh` | `numeric(14,3)` NOT NULL |
| `percentual_corte` | `numeric(6,2)` NULL (cortada / (verificada + cortada) × 100) |
| `corte_rel_mwh`, `corte_cnf_mwh`, `corte_ene_mwh`, `corte_outros_mwh` | `numeric(14,3)` NOT NULL |
| `perda_estimada_brl` | `numeric(16,2)` NULL |
| `base_preco` | `text` NULL (`PLD`, `CMO`, `PLD_FILLED`, `CMO_FILLED`: a base predominante no mês) |
| `horas_restritas` | `numeric(7,1)` NOT NULL |
| `percentual_corte_medio_uf` | `numeric(6,2)` NULL (média da UF e fonte no mês, para comparação) |
| `posicao_nacional` | `integer` NULL (por fonte e mês, por energia cortada; só `tipo = 'usina'`) |
| `posicao_uf` | `integer` NULL (por UF, fonte e mês; só `tipo = 'usina'`) |
| `atualizado_em` | `timestamptz` NOT NULL DEFAULT now() |

PK `(usina_id, mes)`. Índices: `(mes, posicao_nacional)`, `(mes, posicao_uf)`.

### `curtailment_mensal_uf`
| Coluna | Tipo |
|---|---|
| `uf` | `char(2)` |
| `fonte` | `text` (`eolica`, `solar`, `total`) |
| `mes` | `date` |
| `geracao_verificada_mwh`, `energia_cortada_mwh` | `numeric(16,3)` NOT NULL |
| `percentual_corte` | `numeric(6,2)` NULL |
| `corte_rel_mwh`, `corte_cnf_mwh`, `corte_ene_mwh`, `corte_outros_mwh` | `numeric(16,3)` NOT NULL |
| `perda_estimada_brl` | `numeric(16,2)` NULL |
| `usinas_afetadas` | `integer` NOT NULL (usinas com corte > 0) |
| `atualizado_em` | `timestamptz` NOT NULL DEFAULT now() |

PK `(uf, fonte, mes)`.

### `curtailment_mensal_sin`
Mesmas colunas de `curtailment_mensal_uf`, trocando `uf` por `subsistema`
(`N`, `NE`, `S`, `SE`, `SIN`). PK `(subsistema, fonte, mes)`.

### `carga_execucao`
Registro de cada carga do pipeline (lido por `/v1/admin/cargas`).

| Coluna | Tipo |
|---|---|
| `id` | `bigint` identity PK |
| `dataset` | `text` NOT NULL (nome da fonte, ex. `wind_plant_curtailment`, ou `agregados`) |
| `periodo` | `date` NULL (mês processado) |
| `arquivo` | `text` NULL (URL de origem) |
| `hash` | `text` NULL (sha256) |
| `linhas` | `integer` NULL |
| `inicio` | `timestamptz` NOT NULL |
| `fim` | `timestamptz` NULL |
| `status` | `text` NOT NULL (`em_andamento`, `sucesso`, `falha`) |
| `detalhe` | `text` NULL (avisos de qualidade ou erro) |

Índice `(inicio DESC)`.

### `relatorio_mensal`
| Coluna | Tipo |
|---|---|
| `id` | `bigint` identity PK |
| `mes` | `date` NOT NULL |
| `titulo` | `text` NOT NULL |
| `slug` | `text` NOT NULL UNIQUE |
| `resumo` | `text` NULL |
| `conteudo` | `text` NOT NULL (Markdown) |
| `publicado_em` | `timestamptz` NULL (nulo = rascunho) |
| `criado_em`, `atualizado_em` | `timestamptz` NOT NULL DEFAULT now() |

### `assinante_newsletter`
| Coluna | Tipo |
|---|---|
| `id` | `bigint` identity PK |
| `email` | `text` NOT NULL UNIQUE (minúsculo) |
| `consentimento_em` | `timestamptz` NOT NULL |
| `confirmado_em` | `timestamptz` NULL |
| `descadastrado_em` | `timestamptz` NULL |
| `token_confirmacao_hash` | `text` NULL (sha256 do token; o token nunca é guardado) |
| `token_confirmacao_expira_em` | `timestamptz` NULL |
| `token_descadastro_hash` | `text` NOT NULL (descadastro em um clique, sem expirar) |
| `origem` | `text` NULL |
| `criado_em` | `timestamptz` NOT NULL DEFAULT now() |

### `organizacao` e `usuario` (base da Fase 2, criadas vazias)
- `organizacao`: `id uuid` PK, `nome text NOT NULL`, `criado_em`.
- `usuario`: `id uuid` PK, `cognito_sub text UNIQUE NOT NULL`, `email text UNIQUE NOT NULL`,
  `nome text NULL`, `organizacao_id uuid FK NULL`, `papel text NOT NULL`
  (`dono`, `membro`, `admin`), `criado_em`.

### Como o `energia-data` escreve
- `usina`: upsert por `id`.
- `curtailment_mensal_*`: para cada mês processado, apaga as linhas daquele `mes` e
  insere as novas, numa transação (idempotente).
- `carga_execucao`: insere uma linha por fonte e mês, e uma linha `agregados` por mês.
- Usuário de banco próprio, com `SELECT, INSERT, UPDATE, DELETE` só nessas tabelas.

## Data lake (S3)

```
bronze/ons/<fonte>/<particao>/<arquivo original>   como veio do ONS
prata/<fonte>/year=YYYY/month=MM/data.parquet       padronizado (e modelado, no curtailment)
ouro/<tabela>/year=YYYY/month=MM/data.parquet       os mesmos agregados do Postgres
```

## API (`energia-api`)

Base `/v1`. JSON com as mesmas chaves das colunas. Valores `numeric` saem como número.
Públicos: sem autenticação, com `Cache-Control: public, max-age=300, s-maxage=3600,
stale-while-revalidate=86400`. Erros no formato `{"detail": "..."}`.

| Método | Rota | Acesso | Retorna |
|---|---|---|---|
| GET | `/v1/curtailment/sin?fonte=&inicio=&fim=` | Público | `{"itens": [linha de curtailment_mensal_sin]}`; `fonte` padrão `total`; `inicio`/`fim` em `YYYY-MM` |
| GET | `/v1/curtailment/uf/{uf}?fonte=&mes=` | Público | `{"serie": [linhas de curtailment_mensal_uf], "ranking": [UsinaRanking], "mes_ranking": "YYYY-MM"}`; ranking do `mes` (padrão: último mês disponível), top 20 |
| GET | `/v1/usinas?busca=&uf=&fonte=&tipo=&pagina=1&tamanho=20` | Público | `{"itens": [UsinaResumo], "total": n, "pagina": p, "tamanho": t}` |
| GET | `/v1/usinas/{slug}` | Público | `{"usina": Usina, "conjunto": UsinaResumo|null, "usinas_do_conjunto": [UsinaResumo], "serie": [linhas de curtailment_mensal_usina]}` |
| GET | `/v1/ranking?mes=&uf=&fonte=&limite=20` | Público | `{"mes": "YYYY-MM", "itens": [UsinaRanking]}` (`limite` ≤ 100) |
| GET | `/v1/meses` | Público | `{"meses": ["YYYY-MM", ...]}` meses com dados, decrescente |
| GET | `/v1/relatorios` | Público | `{"itens": [RelatorioResumo]}` só publicados |
| GET | `/v1/relatorios/{slug}` | Público | `Relatorio` (404 se rascunho) |
| POST | `/v1/newsletter` | Público, limite de taxa (5/min por IP) | `202 {"mensagem": "..."}`; corpo `{"email", "consentimento": true, "origem"?}`; sempre 202, mesmo se já inscrito (não vaza quem está na lista) |
| GET | `/v1/newsletter/confirmar?token=` | Público | `{"confirmado": true}` ou 400 |
| GET | `/v1/newsletter/descadastrar?token=` | Público | `{"descadastrado": true}` ou 400 |
| GET | `/v1/admin/cargas?limite=50` | Admin | `{"itens": [linha de carga_execucao]}` |
| GET | `/v1/admin/relatorios` | Admin | todos, inclusive rascunhos |
| POST | `/v1/admin/relatorios` | Admin | cria ou atualiza por `slug`; corpo `{"mes": "YYYY-MM", "titulo", "resumo"?, "conteudo", "publicar": bool}` |
| POST | `/v1/admin/revalidar` | Admin | `{"tags": [...]}` (padrão: todas); limpa o cache da API e chama o `energia-web` |
| GET | `/health` | Interno | `{"status": "ok", "banco": "ok"}` |

Formatos:
- `UsinaResumo`: `id, tipo, nome, slug, fonte, uf, subsistema, potencia_mw`
- `Usina`: `UsinaResumo` + `conjunto_id, ceg, ponto_conexao, agente_operador, atualizado_em`
- `UsinaRanking`: `UsinaResumo` + `posicao, energia_cortada_mwh, percentual_corte, perda_estimada_brl`
- `RelatorioResumo`: `mes, titulo, slug, resumo, publicado_em`; `Relatorio`: + `conteudo`

## Autenticação

- Cognito User Pool, login por e-mail, MFA obrigatório. Grupo `admin`. Cadastro aberto desligado.
- **Admin (pessoa)**: `energia-web` faz login OAuth com PKCE (Auth.js, provedor Cognito),
  sessão em cookie `httpOnly`; chama a API no servidor com o *access token*.
- **Máquina (`energia-data` → API)**: app client com *client credentials* e resource server
  `energia-api` com escopo `energia-api/admin`.
- A API aceita em `/v1/admin/*` um JWT do Cognito (RS256, JWKS do pool, `iss` do pool,
  `token_use = access`) que tenha o grupo `admin` em `cognito:groups` **ou** o escopo
  `energia-api/admin`.
- Local: `AUTH_DESATIVADA=true` libera o admin **só** com `AMBIENTE=local` (a API recusa subir
  com essa flag em outro ambiente).

## Revalidação do portal

1. Ao fim de cada carga, o `energia-data` chama `POST {API_URL}/v1/admin/revalidar` com
   `{"tags": ["curtailment", "usinas"]}`.
2. A API limpa o próprio cache e chama `POST {WEB_URL}/api/revalidar` com o cabeçalho
   `x-revalidate-secret: {REVALIDATE_SECRET}` e o mesmo corpo.
3. O `energia-web` chama `revalidateTag` para cada tag. Tags usadas nos `fetch`:
   `curtailment`, `usinas`, `relatorios`.

## Ambiente local

| Serviço | Endereço |
|---|---|
| Postgres | `localhost:5433`, banco `warehouse` (usuário/senha `warehouse` em dev) |
| S3 (SeaweedFS) | `http://localhost:8333`, bucket `energy-lake` |
| API | `http://localhost:8000` |
| Web | `http://localhost:3000` |
| Airflow | `http://localhost:8080` |

Variáveis principais: `DATABASE_URL` (API), `WAREHOUSE_DSN` (data), `API_URL`, `WEB_URL`,
`REVALIDATE_SECRET`, `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_IDS`,
`AMBIENTE` (`local` ou `prod`).

## CI/CD (GitHub Actions)

- Todo PR: lint, checagem de tipos e testes.
- Merge na `main` de `energia-api` e `energia-data`: imagem Docker publicada no ECR
  (autenticação AWS por OIDC) e deploy.
- `energia-web`: deploy pela Vercel (preview por PR); o CI roda lint, tipos e testes.
- `energia-infra`: `terraform plan` no PR; `apply` na `main` com aprovação manual
  (environment `producao` com revisores obrigatórios).
