# Plano Pro (alertas e API): relatório da Etapa 0 (descoberta)

Consulta de 2026-10-30. Spec: "Plano Pro do Spillway: alertas e API". Nenhum código de produção, conta, cadastro ou segredo foi criado; as consultas ao banco foram só de leitura. Leitura prática de licenças, não parecer jurídico.

## Resumo executivo

1. **Dois tipos de alerta não são entregáveis como a spec descreve.** `curtailment_threshold` ("% acima de X em um dia"): `hourly_curtailment` não é série temporal, é um perfil (mês, tipo de dia, hora), e os agregados de curtailment são mensais. `storage_threshold` hoje chega com até ~1 mês de atraso porque o DAG de hidrologia é mensal.
2. **Licenças**: PLD (CCEE, CC BY 4.0), ONS (CC BY declarado, conferido numa página) e EPE (CC BY 4.0) permitem uso com atribuição. ANEEL é ODbL (share-alike em base derivada): alertas ok, exportação em lote não. INMET não declara licença: fora.
3. **Gatilho**: só os DAGs ONS publicam Asset do Airflow; PLD, ANEEL, notícias e DOU não. É preciso adicionar `outlets`.
4. **Relay** serve, com 3 pontos: supressão só aparece como HTTP 422 no envio; não há campo para cabeçalho `List-Unsubscribe`; o pipeline Python não tem cliente do gateway.
5. **Cobrança (recomendação)**: Asaas (Pix e cartão recorrentes em reais, link de pagamento, NFS-e, sem mensalidade). Decisão do usuário.

## 1. Auditoria de licença

| Conjunto | Licença declarada | Uso comercial | Atribuição | Link / evidência | No plano pago? |
|---|---|---|---|---|---|
| ONS (EAR, ENA, geração, restrição eólica/solar) | CC BY ("Creative Commons Atribuição") na página do conjunto EAR diário; dados "como estão", podem ser revisados | Não citado na página; CC BY permite | Creditar o ONS e informar alterações | https://dados.ons.org.br/dataset/ear-diario-por-subsistema (2026-10-30) | Sim para EAR. **Pendente por conjunto**: a página dos conjuntos de curtailment não abriu (URL tentada deu 404; o pipeline lê o bucket `ons-aws-prod-opendata`). Conferir cada página na Etapa 1 antes de prometer |
| CCEE (PLD horário, diário, semanal; consumo) | CC BY 4.0 (o "Termo Legal" liga ao aceite da licença) | Não citado; CC BY permite | Creditar a CCEE e indicar alterações | https://dadosabertos.ccee.org.br/dataset/pld_horario (2026-10-30). A página diz "Mensal" e "Todos os dias" ao mesmo tempo | Sim |
| ANEEL (DEC/FEC, bandeiras) | ODbL 1.0 | Permitido | Atribuição + **share-alike** para base derivada | Verificado em 2026-10-30 em `energia-data/docs/data-sources-aneel.md` e `data_source.license_verified_at`. Nova consulta de hoje falhou (conexão fechada); não reconfirmei | Alertas (obra produzida) sim. **Exportação em lote das tabelas ANEEL fica fora** até decisão jurídica: pode ser "base derivada" e exigir ODbL |
| EPE (BEN) | CC BY 4.0 | Permitido com atribuição | EPE + ano-base | https://www.epe.gov.br/pt/publicacoes-dados-abertos/publicacoes/balanco-energetico-nacional-2025 (2026-10-30) | Sem alerta previsto; pode entrar no `source` da API |
| INMET | **Não declara** licença nem termos (`energia-data/docs/data-sources-climate.md`; reconsulta de hoje deu 503) | Desconhecido | n/d | https://portal.inmet.gov.br/dadoshistoricos | **Fora** até resposta do INMET |
| NASA POWER | CC BY 4.0 (LICENSE.txt do bucket) | Permitido | Citar NASA POWER | `energia-data/docs/data-sources-climate.md` | Sem alerta previsto |
| Notícias ANEEL, MME, ANP | CC BY-ND 3.0 | CC BY-ND permite uso comercial, **sem obras derivadas** | Fonte + link | tabela `news_source`; `energia-data/docs/news-sources.md` | `news_topic` só com **título verbatim, data e link**; sem resumo reescrito |
| Notícias EPE | CC BY 4.0 | Sim | Sim | `news_source` | Sim |
| Agência Gov | "Todos os direitos reservados" | Não | n/d | `news-sources.md` | **Fora** (já fora do pipeline) |
| Atos do DOU (`regulatory_act`) | Ato oficial (Lei 9.610, art. 8º, IV); fonte `dou` desligada (`enabled=false`), tabela local com 0 linhas | n/d | n/d | `news_source` id 5 | **Fora por ora**: sem coleta, não há alerta |
| spillway-forecast | Modelo próprio treinado em dados ONS (CC BY). `MODEL_CARD.md`: "Not intended for trading or dispatch decisions without human review" | Herda a atribuição ONS | ONS + aviso de incerteza | `spillway-forecast/MODEL_CARD.md` | Sim, com aviso. A previsão é "do modelo Spillway", nunca "do ONS" |

Observações: `data_source` (o registro de licença que a spec manda citar em cada e-mail) só tem 4 linhas: `ccee-pld`, `aneel-reliability`, `aneel-tariff-flags`, `ccee-market`. **Não há linha para ONS nem para a previsão**; a Etapa 1 precisa semeá-las antes de qualquer e-mail citar a fonte. Nenhum órgão pode ser apresentado como emissor do serviço.

## 2. Defasagem real por tipo de alerta

Medida em 2026-10-30 ~18:57 UTC no warehouse local (`localhost:5433`, só SELECT). **Limitação**: o banco local foi carregado de uma vez (todas as `load_run` terminam em 2026-10-30) e não há `loaded_at` por fato; então "defasagem" aqui é "fato mais recente vs. agora" mais o agendamento dos DAGs. A defasagem de produção só se mede depois de algumas semanas rodando o cron.

| Dado (tabela) | Fato mais recente no banco | vs. agora | DAG (cron em UTC, fuso padrão do Airflow) |
|---|---|---|---|
| `pld_hourly` (oficial) | 2026-10-30 23:00 (hora local), à frente de agora | **adiantado** (o preço do dia sai antes) | `ccee_pld_pipeline` diário 08:30 |
| `pld_daily` / `pld_weekly` | 2026-10-30 / semana de 2026-10-24 | 0 dia | idem |
| `hourly_curtailment` | 2026-09 (mês); **sem data, só perfil** | ~30 a 60 dias | `ons_curtailment_pipeline` mensal, dia 3 |
| `subsystem_monthly_curtailment`, `plant_monthly_curtailment` | mês 2026-09 | ~30 dias | idem |
| `forecast_hourly` (run 2) | criado 2026-10-28, dados até 2026-10-27, horizonte até 2026-11-03 23:00 | 2 dias da criação, 3 dos dados | **Sem DAG**: execução manual (nenhuma referência a forecast em `energia-data`) |
| `subsystem_storage_daily` (EAR) | 2026-09-30 | **30 dias** | `ons_hydrology_pipeline` mensal, dia 3. O ONS declara atualização diária às 12h e 19h: o limite é o nosso agendamento |
| `reliability_monthly` | mês 2026-08 | ~60 dias | `aneel_reliability_pipeline` dia 6 |
| `tariff_flag_monthly` | 2026-10 | 0 | `aneel_tariff_flags_pipeline` dia 6 (a bandeira do mês seguinte só entra depois do dia 6) |
| `news_item` | publicada 2026-10-30 17:20 UTC, coletada 18:00 | <1 h | `news_pipeline` de hora em hora (a mediana de 3,7 dias entre publicação e coleta vem do backfill desde 2026-08-17) |
| `regulatory_act` | vazia | n/d | `dou_acts_pipeline` diário 09:30, mas a fonte está desligada |

Tabela "tipo de alerta, defasagem típica, texto honesto":

| Tipo | Defasagem típica | Texto honesto no produto | Situação |
|---|---|---|---|
| `pld_threshold`, `pld_floor_hours` | Preço do dia já carregado de manhã (08:30 UTC), sujeito à publicação da CCEE | "Avisamos quando o PLD publicado pela CCEE cruza o limite. O PLD sai da CCEE com antecedência; não é tempo real." | Entregável |
| `curtailment_threshold` | Mensal (~30 dias), só agregado mensal | "Avisamos quando o ONS publica o mês fechado." Não prometer "em um dia" | **Redefinir** como `curtailment_month_published` (% do mês acima de X). Alerta diário exige nova ingestão (restrição diária do ONS): mudança de escopo, parar e perguntar |
| `curtailment_forecast` | Previsão manual, dados 2-3 dias atrás | "Previsão do modelo Spillway, gerada em DD/MM com dados até DD/MM; não é recomendação operacional." | Só se a previsão ganhar DAG e dado fresco; hoje o alerta nasceria velho |
| `storage_threshold` | Até ~30 dias | "Avisamos quando um novo dado de EAR é publicado." Com DAG diário cai para ~1 dia | Entregável como "novo dado"; para ser útil, passar o DAG a diário (decisão do usuário, mexe em outra spec) |
| `reliability_new` | ~60 dias após o mês | "Avisamos quando a ANEEL publica o mês novo de DEC/FEC." | Entregável |
| `tariff_flag_change` | Mensal | "Avisamos quando a bandeira do mês muda." | Entregável |
| `news_topic` | <1 h | "Notícias de fontes oficiais, com título, data e link." | Entregável |
| atos do DOU | n/d | n/d | **Fora** (fonte desligada) |

Trava de qualidade (princípio 4): o avaliador compara `data_as_of` com um limite por tipo (ex.: PLD até 36 h; tabelas mensais só disparam em fato novo) e grava `skipped_stale` com o motivo.

## 3. Como o Airflow avisa que uma carga terminou

- Airflow 3 (`airflow.sdk.Asset`). Os DAGs ONS gerados por `dags/pipeline_factory.py` declaram `outlets`: `record()` emite `Asset("s3://energy-lake/silver/<pipeline>")` e `notify_portal()` emite `Asset("postgres://warehouse-db:5432/warehouse/public/<tabela>_tables")` (curtailment: `plant_monthly_curtailment`).
- **`ccee_pld_pipeline`, `aneel_*`, `news_pipeline` e `dou_acts_pipeline` não têm nenhum `outlets`.** E `load_run` (`dataset, period, status, finished_at, row_count, checksum`) só é gravada pelos pipelines ONS; PLD e ANEEL não deixam registro lá. Falhas ONS gravam `load_run.status='failed'` via `on_failure_callback` (`steps.record_failure`).
- `notify_portal` já é o fim de cada carga (revalida a web). O avaliador deve vir logo depois.
- Proposta:
  1. Em cada DAG, a última tarefa de carga declara `outlets=[Asset(".../public/<tabela>")]`, um Asset por tabela que alimenta alertas (`pld_hourly`, `pld_daily`, `subsystem_storage_daily`, `reliability_monthly`, `tariff_flag_monthly`, `news_item`, `forecast_hourly`).
  2. DAG `alerts_evaluator` com `schedule=[asset_a, asset_b, ...]` (agendamento por Asset), sem relógio. Cada execução lê os eventos de Asset que a dispararam e avalia só as regras dos tipos afetados.
  3. O Asset dispara mesmo quando a carga não trouxe fato novo; a regra decide por `event_key` determinístico e por `data_as_of`, nunca pelo disparo em si.
  4. Carga que falhou não emite evento de Asset: o avaliador não roda. Ele também confere `load_run.status` quando existir.
- Não mexi no Airflow nem abri a UI; conclusões vêm do código (scheduler em `energia-data-airflow-scheduler-1`).

## 4. Como o Relay entrega e-mail hoje (código lido; nada subiu)

Caminho: API `app/email.py` (`GatewayEmailSender`) -> `relay-api-gateway` `POST /v1/messages` -> Relay (mensagem + outbox na mesma transação, Kafka, worker, SES) -> SES -> SNS -> SQS -> `relay-receipts` (Lambdas) -> `POST /v1/receipts` no Relay.

| Item | Valor |
|---|---|
| Variáveis da API | `EMAIL_BACKEND=gateway`, `GATEWAY_URL`, `GATEWAY_API_KEY` (vai em `x-api-key`), `GATEWAY_TIMEOUT_SECONDS` (10), `EMAIL_SENDER`, `WEB_URL`, `EVENT_HASH_SECRET` (assina o link de descadastro) |
| Variáveis do gateway | `RELAY_URL`, `RELAY_API_KEY`, `GATEWAY_API_KEYS` (vazia aceita qualquer um), `ALLOWED_CHANNELS`, `MAX_BODY_BYTES`, `RELAY_TIMEOUT_MS` |
| Corpo | `{channel:"email", to, subject, body (texto, até 4.000), html, variables{nome:valor}, metadata{<=20 strings}}`. A API envia `metadata.product="energia"` e `metadata.template` |
| `variables` | Relay preenche `{{nome}}` em subject, body e html (escapa HTML no html); nomes `[A-Za-z_][A-Za-z0-9_]*` |
| `Idempotency-Key` | regex `^[A-Za-z0-9_\-:.]{8,128}$`. Mesma chave e mesma mensagem: 200 com `Idempotent-Replayed: true`; mesma chave com mensagem diferente: **409** |
| Respostas | 202 `{id,status:"accepted"}`; 400; 401; 409; 413; 422 (destinatário suprimido); 502; 504. `GET /v1/messages/{id}` devolve status `accepted/delivered/failed`, tentativas, último erro e `receipts[]` |
| Recibo (relay-receipts) | `id, provider, providerMessageId, relayMessageId, channel, recipient, type (sent/delivered/delayed/bounced/complained/rejected), permanent, reason, occurredAt`; at-least-once, deduplicado por ledger DynamoDB e id único no Relay |
| Supressão | Hard bounce (`bounced` com `permanent`) ou `complained` insere em `suppressions(channel, recipient)`. **Não existe GET de supressão**: o único sinal é o envio devolver **422**. Há `DELETE /v1/suppressions/{channel}/{recipient}` (operador) |
| Uso hoje | Envio mensal (`app/dispatch.py`, tabela `email_dispatch` com UNIQUE `(kind, month)`, chave por `(tipo, mês, endereço)`) e e-mails transacionais. `email_dispatch` não serve para alertas |

Lacunas para alertas:

- **Cliente no pipeline**: `energia-data` não tem cliente do gateway (`GATEWAY_*` não aparece na config). Opção A: pequeno cliente `httpx` no pipeline com `GATEWAY_URL/GATEWAY_API_KEY` como segredo do Airflow (o usuário cria). Opção B: a API expõe endpoint interno de envio (mais acoplamento). Recomendo A.
- **Descadastro de 1 clique**: o gateway só valida os campos acima; não há como pôr cabeçalho `List-Unsubscribe`/`List-Unsubscribe-Post`. Dá para ter link no corpo; o cabeçalho exigiria mudar o Relay (parar e perguntar). Reaproveitar o token assinado de `app/dispatch.py` (`unsubscribe_token`, HMAC com `EVENT_HASH_SECRET`).
- **Supressão**: tratar 422 como `suppressed` em `alert_delivery` e pausar o canal; não há consulta prévia.

Chamada proposta (determinística, para reenvio seguro):

```
POST {GATEWAY_URL}/v1/messages
x-api-key: <GATEWAY_API_KEY>
Idempotency-Key: <rule_id>:<event_key>:<canal>      # ex. 2c1e...:pld.SE.2026-10-31.gt300:email

{"channel":"email","to":"<member.email>","subject":"{{subject}}","body":"{{text}}","html":"...",
 "variables":{"valor":...,"limiar":...,"data_as_of":...,"fonte":...,"licenca":...,"regra":...,
              "link_portal":...,"link_descadastro":...},
 "metadata":{"product":"energia","template":"alert","rule_type":"pld_threshold"}}
```

Cuidados: `event_key` só com `[A-Za-z0-9_-:.]`; chave total <= 128 (UUID de 36 + `:` + `event_key` + `:email`, então `event_key` <= ~80); o conteúdo precisa ser estável entre tentativas (nada de horário de envio nas variáveis), senão volta 409.

## 5. Provedor de cobrança (sites oficiais, consultados em 2026-10-30; nada criado)

| Critério | Stripe | Mercado Pago | Asaas | Pagar.me |
|---|---|---|---|---|
| Assinatura recorrente em R$ | Sim (Stripe Billing) | Sim (API `/preapproval`, planos) | Sim (assinaturas: cartão, Pix, boleto) | Sim, mas a doc lida lista só `credit_card` e `boleto` |
| Cartão recorrente | Sim | Sim (retentativa automática) | Sim (debita no vencimento) | Sim |
| Pix recorrente | **Pix Automático** desde 2026-04-22 (changelog); exige mandato do pagador | Pix listado na visão geral de assinaturas; detalhe da recorrência não confirmado | Duas vias: cobrança Pix por assinatura e **Pix Automático** (autorização por QR Code) | Pix **não** consta em assinaturas na doc lida |
| Checkout hospedado | Sim, Stripe Checkout (sem taxa extra; domínio próprio US$ 10/mês) | Sim (`init_point` da assinatura) | Sim (link de pagamento, sem taxa separada listada) | Não confirmado |
| Webhooks | Sim (documentação Stripe; não reverifiquei hoje) | Não confirmei nos textos lidos | Sim; a doc recomenda acompanhar cada cobrança por webhook | Não confirmei nos textos lidos |
| Nota fiscal | Página de preços não informa tarifa | Não confirmado | NFS-e a R$ 0,49 por nota emitida | Não confirmado |
| Taxas publicadas | Cartão nacional 3,99% + R$ 0,39; internacional +2%; Pix 1,19% **somente por convite**; boleto R$ 3,45; Billing 0,7% do volume | **Página oficial deu 403**; só há valores de terceiros (cartão ~4% a 5%, Pix 0,99%), sem validade: **não consegui ver taxa pública oficial** | Pix R$ 1,99 (promo R$ 0,99 por 3 meses); boleto R$ 1,99; cartão à vista R$ 0,49 + 2,99%; 2 a 6x 3,49%; sem mensalidade nem adesão | Pix 0,99%, crédito à vista 4,19%; boleto, assinatura e checkout **não publicados** |
| Links | https://stripe.com/br/pricing ; https://docs.stripe.com/changelog/dahlia/2026-04-22/pix-recurring-payments-support | https://www.mercadopago.com.br/developers/pt/docs/subscriptions/overview ; https://www.mercadopago.com.br/costs-section/release-options (403) | https://www.asaas.com/precos-e-taxas ; https://docs.asaas.com/docs/assinaturas-recorrência ; https://docs.asaas.com/docs/diferença-entre-pix-automático-e-assinaturas | https://www.pagar.me/precos ; https://docs.pagar.me/docs/assinaturas-1 |

Ressalvas: a página da Asaas diz que as taxas são do plano padrão e que o contrato real aparece em Menu > Taxas na conta; o exemplo de cartão da página tem inconsistência numérica; a promoção dura 3 meses. Não verifiquei o mecanismo de assinatura de webhook de nenhum provedor (necessário na Etapa 5).

**Recomendação: Asaas** para começar: Pix e cartão recorrentes em reais, link de pagamento hospedado, NFS-e integrada a R$ 0,49, sem mensalidade, taxas públicas. Stripe é a alternativa se houver pagantes com cartão internacional ou se você quiser o Billing mais completo (custo maior: 3,99% + R$ 0,39 e 0,7% do volume). Mercado Pago e Pagar.me ficam atrás pela falta de taxa oficial acessível e de confirmação de Pix recorrente. Decisão é sua, e só na Etapa 5.

## 6. Pontos que dependem do usuário (não resolvidos aqui)

- Tipo de empresa (MEI/ME/PF): define se e como emite NFS-e, município, CNAE e teto de faturamento.
- Nota fiscal: obrigatoriedade, quem emite (provedor ou contador), integração.
- Contrato de trabalho: cláusulas de exclusividade e propriedade intelectual sobre o produto.
- Termos de uso e política de privacidade do plano pago (finalidade e retenção para alertas, WhatsApp e API).
- Direito de arrependimento (CDC, 7 dias na venda à distância), cancelamento e reembolso.
- Decisões desta etapa: redefinir `curtailment_threshold`; passar o DAG de hidrologia a diário; dar DAG à previsão; ligar a fonte DOU; exportação em lote da ANEEL (ODbL); mudar o Relay para `List-Unsubscribe`.
- Conferir a licença na página de cada conjunto ONS de curtailment antes de vender; pedir licença ao INMET.
- Conversar com contador e advogado antes de cobrar.

## 7. Modelo de dados revisado contra o schema real

Convenções reais: Alembic `0001` a `0015` (próxima: `0016`); PK de membro é `member.id` **UUID**; constraints com `op.f(...)`; timestamps `timestamptz`; papéis `energia_api` e `energia_data` em `energia-infra/scripts/create-db-users.sql`; contrato em `docs/phase1-contract.md` (que ainda cita `spillway-*`; as pastas locais são `energia-*`).

| Item da proposta | Correção |
|---|---|
| `plan`, `subscription` | `member_id` é `uuid`. `subscription`: índice único parcial `(member_id) WHERE status IN ('active','past_due')`. Semear `free` e `pro` na migração (como o seed de `data_source`) |
| `alert_rule.params jsonb` | O repositório usa `sa.JSON()`; usar JSONB com CHECK por `type`. Subsistemas só `N, NE, S, SE` (`SIN` onde o contrato permite); fonte `wind/solar/total` |
| `alert_channel.destino/address_hash` | **Hash não serve para enviar.** E-mail: usar `member.email` (sem cópia). WhatsApp: guardar E.164 recuperável (`member.phone` é texto livre) ou cifrado; hash só para dedupe |
| `alert_event` | Manter UNIQUE `(rule_id, event_key)`. Acrescentar status `failed`/`delivered` e `digest_sent_at` para o resumo diário. `event_key` nos caracteres aceitos pelo Relay |
| `alert_delivery` | `relay_message_id` UUID; guardar `http_status`; `status` inclui `suppressed` (422). Único `(event_id, channel)` |
| Pipeline precisa dos destinatários | `energia_data` teria de ler `member` (contém `password_hash`). **Criar a view `alert_recipient`** (`member_id, email, name, email_verified_at`) e dar SELECT só nela; exigir `email_verified_at IS NOT NULL` |
| `api_key` | Membro tem sessão própria (`member_session`, com hash) e admin usa Cognito; a chave de API é um terceiro mecanismo: tabela e dependência separadas |
| `api_usage_daily` e limites | `app/ratelimit.py` é **em memória, por processo, por IP**: não serve para cota por chave nem com 2 instâncias. `app/usage.py` é análise sobre `usage_event`, não medição. Cota mensal precisa de contador no Postgres |
| `data_source` | Reaproveitar para `source` nas respostas e e-mails; semear ONS e previsão (faltam) |
| Grants (`energia-infra`) | `energia_api` escreve `plan, subscription, alert_rule, alert_channel, api_*, webhook_endpoint, plan_interest`; `energia_data` lê `alert_rule, alert_channel, plan` e a view, e escreve `alert_event, alert_delivery` |
| `email_dispatch` | Não reaproveitável (UNIQUE `(kind, month)`); criar tabela própria |
| Outras specs | Nenhuma tabela existente é alterada; só tabelas novas |

Reaproveitamento confirmado: cadastro e sessão de membro (`0002_members.py`, `app/routers/auth.py`), token de descadastro assinado (`app/dispatch.py`), `GatewayEmailSender`, `data_source`. Corrigido em relação à spec: `app/auth.py` é só do admin (Cognito); `hourly_curtailment` é perfil, não série; `ratelimit` e `usage` não medem por chave.

## Próximos passos sugeridos (Etapa 1)

1. Decisões do item 6, em especial curtailment, DAG de EAR e DAG da previsão.
2. Migração `0016` (tabelas, view `alert_recipient`, seed de `plan`, `data_source` de ONS e previsão) e grants.
3. `outlets` de Asset em PLD, ANEEL, notícias e previsão.
