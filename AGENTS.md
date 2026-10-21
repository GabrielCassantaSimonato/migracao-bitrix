# AGENTS.md — Contexto do projeto

## Objetivo
Migração Oracle (Fusion Sales Cloud) → Bitrix24. Piloto já validado: criação +
atualização de registros com de-para de campos e de valores (listas/lookups).

## Ambiente / Credenciais
- Oracle: https://fa-evip-saasfaprod1.fa.ocs.oraclecloud.com
  - CRM:  /crmRestApi/resources/11.13.18.05
  - FSCM: /fscmRestApi/resources/11.13.18.05  (lookups: standardLookups, commonLookups, setEnabledLookups)
- Bitrix24: https://colabore.synchro.com.br/rest/3894/pou4tuot6wo8dyeo/
- Credenciais em `.env` (ORACLE_USER, ORACLE_PASS, BITRIX_WEBHOOK).

## Scripts (pasta do projeto)
- config.py — env vars, mapeamento de objetos, CAMPO_ID_ORACLE, QUERY_FILTRO, ENTIDADE_ITEM.
- oracle_client.py / bitrix_client.py — clientes REST.
- coletar.py — baixa describes Oracle + fields Bitrix + amostras (data/).
- mapear_automatico.py — de-para de campos por similaridade de nome (usa `listLabel`).
- de_para_valores.py — de-para de VALORES das listas (código Oracle → ID do item Bitrix).
- mapeamento.py — DE_PARA manual, transforms, SUFIXO_PRODUTO, COMPANY_TYPE_MAP,
  NOMES_ESPECIAIS/EMAILS_ESPECIAIS, `resolver_usuario` (user.search centralizado via
  `migrador.get_json` + cache persistente/thread-safe em `data/user_cache.json`).
- migrador.py — reconciliação/dedup (find_existing/upsert) + camada de rate-limit
  (token bucket, detecção 503/429 no status HTTP, rastreio de `time.operating`) + batch idempotente.
- relatorio_campos.py / gerar_excel.py — relatórios (campos_para_revisar.txt / relatorio_migracao.xlsx).
- **migrar_produto.py** — carga de crm.product (grupo no nome `[SE/LU/MA/...]`, idempotente).
- **migrar_parceiro.py** — carga do item1196 (proprietário, contato principal, tel/email, CNPJ, endereço).
- **migrar_oportunidade.py** — carga de crm.deal (todos os campos + produtos + anexos, idempotente).
  Modos: `--contatos` (reparo do contato principal; `--abertas` filtra só negócios abertos),
  `--produtos-anexos` (produtos/anexos dos deals já migrados).
- **migrar_lead.py** — carga de crm.lead (de-para de campos/valores, vínculo empresa/contato/
  parceiro/agente, rótulos de campos "choice"→string, Segmento em cascata, idempotente).
  Flags: `--limite N` (teste) e `--descricao` (inclui os 2 CLOBs DescricaoOportunidade_c/
  DescricaoLead_c — pulados na carga em massa por performance, backfill à parte).
  Build de payload paralelo (ThreadPoolExecutor 8), fetch do Oracle com `fields` (51 campos),
  `segmento_map` cacheado por `Setor_c` (LOV cascata).
- **backfill_descricao_lead.py** — backfill das 2 descrições CLOB do lead (`DescricaoOportunidade_c`
  → `UF_CRM_1779737212811`, `DescricaoLead_c` → `UF_CRM_1779737221467`). Só atualiza os 2 campos,
  e só nos leads com conteúdo (enclosure retorna 404/vazio nos demais). Flag `--limite N`.
- **migrar_mondelez.py** — driver de teste end-to-end da conta Mondelez (conta+contatos+opps+leads).
- **migrar_atividade.py** — carga do item1220 (Atividades) vinculada a conta/negócio/**lead**/contato
  (`AccountId→companyId`, `OpportunityId→parentId2`, `LeadId→parentId1`, `PrimaryContactId→contactId`).
  Fetch único do Oracle com `fields` (16 campos), payload em paralelo (ThreadPoolExecutor 8),
  mapas de empresas/deals/contatos/leads **cacheados em disco** (`data/mapa_crm_*.json`).
  Flags: `--limite N` (teste), `--shard <i> <N>`, e `--reparar-erros[=log]` (re-processa só os
  `ActivityId` que deram ERRO no log — idempotente, cria os faltantes e re-atualiza os existentes).
- **migrar_campanha.py** — carga do item1204 (CriarCampanha_c: nome, data início/fim, proprietário).
- **migrar_campanha_synchro.py** — carga do item1208 (CampanhasSynchro_c) das contas já migradas,
  com vínculo empresa/negócio/campanha/parceiro e proprietário. Loaders roteados pelo `migrador`,
  enclosure `Observacao_c` paralelizado (ThreadPoolExecutor), flag `--limite N` para teste.
- **reparar_owner_campanha.py** — re-atrela `assignedById` das campanhas synchro (de-para de
  proprietário divergente; ver #51).
- **migrar_spv.py** — carga do item1216 (serviceRequests/SPV) das contas já migradas, com vínculo
  empresa/negócio e proprietário. Também hospeda os helpers de vínculo SPV→negócio
  (`spvs_todas_por_negocio`, `spvs_do_negocio`, `vincular_spvs_negocios`) — ver #59.
- **migrar_parcela.py** — carga do item1212 (Parcelas_c) das oportunidades já migradas, com vínculo
  negócio e proprietário.
- **migrar_contato.py** — carga em massa de crm.contact (só CRIAÇÃO dos faltantes; `--limite N`
  para teste, `--descricao` para incluir o CLOB "Descrição"). Não atualiza contas.
- **vincular_contato_conta.py** — vincula o contato principal (`UF_CRM_1768849939`) nas empresas
  (idempotente: re-lê o valor atual e só atualiza quem diverge).
- **vincular_spv_negocio.py** — vincula as SPVs ao negócio no campo `UF_CRM_1792538652`
  ("SPVs da oportunidade", crm multiple → item1216). Idempotente (re-lê o valor atual e só
  atualiza quem diverge). Flag `--limite N` (teste). Backfill completo já rodado (2.401 negócios, 0 erros).
- **data/produto_grupo.json** — mapa InventoryItemId → grupo (gerado por `_mapear_grupos.py`).
- Testes/piloto: importar_oportunidade.py, teste_concreto.py, reaproveitar_deal.py,
  teste_conta.py, corrigir_conta.py.

## Mapeamento de objetos (Oracle → Bitrix)
- opportunities → crm.deal (Negócio)
- accounts → crm.company (Empresa)
- contacts → crm.contact (Contato)
- leads → crm.lead
- partners → item 1196 | serviceRequests → 1216 (SPV) | CampanhasSynchro_c → 1208
- CriarCampanha_c → 1204 | Parcelas_c → 1212 | activities → 1220 | products → crm.product

## Campos de ID Oracle no Bitrix (CAMPO_ID_ORACLE)
- deal: UF_CRM_1789385750762 | company: UF_CRM_1789041431158
- contact: UF_CRM_1789041484569 | lead: UF_CRM_1789385673917 | product: PROPERTY_305
- 1196: ufCrm69_1789386068257 | 1216: ufCrm79_1789386308816 | 1208: ufCrm75_1789386179872
- 1204: ufCrm73_1789386148200 | 1212: ufCrm77_1789386203592 | 1220: ufCrm81_1789386385040

## Descobertas importantes (API)
1. Oracle `opportunities` exige filtro `q=OptyId>0` (sem filtro retorna count=0).
2. Bitrix enum: gravar pelo **ID do item**, NÃO o texto do rótulo (texto → 0).
   O rótulo vem em `listLabel` (crm.*.fields) e os itens em `items` (ID + VALUE).
3. `crm.item.fields` (SPAs) retorna `{"fields": {...}}` aninhado — desembrulhar.
4. Valores Oracle podem ter prefixo `"XX - "` (ex.: "XH - Acima de R$1.5 Bi") — remover antes de casar.
5. LOVs compartilhadas: "PrincipalMotivoDeEncerramento_c" e "MotivoSecundario_c" usam a
   lookup FSCM `standardLookups/PRINCIAL_MOTIVO_ENCERRAMENTO` (não há recurso VO próprio).
6. Tipo de conta: Oracle `Type` = lookup `ZCA_ACCOUNT_TYPE` → Bitrix `COMPANY_TYPE`
   (ZCA_CUSTOMER→CUSTOMER, SUSPECT→PARTNER, EXCLIENTE→COMPETITOR, FORNECEDOR→INTEGRATOR, ...).
7. Produtos: o grupo (`SE/LU/MA/CLOUD/AMS/SLA/MONITORAMENTO/ALL`) NÃO vem de `ProductTypeCode`
   (que é GOODS/SERVICE genérico) nem do objeto `products`. Vem de:
   - `catalogProductGroups` → `ProductGroupId → ProductGroupName` (grupos).
   - `productGroupProducts` → `InventoryItemId → ProdGroupId` (produto→grupo).
   ATENÇÃO: a query default de `productGroupProducts` retorna SÓ o grupo LU; consultar por
   grupo (`q=ProdGroupId=X`) ou, mais confiável, por produto (`q=InventoryItemId=X`). O mapa
   final (per-product) está em `data/produto_grupo.json` (gerado por `_mapear_grupos.py`).
   Nome do produto = `normalizar_nome(nome, grupo)` → remove o token do grupo (palavra/[colchete]/
   (parênteses), case-insensitive) e anexa `[GRUPO]` maiúsculo. Campo de ID do produto: `PROPERTY_305`.
8. Proprietário: de-para por `user.search` (e-mail) → ASSIGNED_BY_ID.
9. Booleanos: enviar "1"/"0" (não true/false).
10. Contato principal × demais contatos (separação via `contatos.py`):
    - Negócio: principal `UF_CRM_1779800744` (single) / demais `UF_CRM_1789409590` (multiple).
      Oracle: principal = `KeyContactId`; demais = contatos **da OPORTUNIDADE**
      (`/opportunities/{OptyNumber}/child/OpportunityContact`, campo `PERPartyId` + `PrimaryFlg`),
      NÃO os da conta. (Contatos da conta seriam via `AccountPartyId`.)
    - Empresa: principal `UF_CRM_1768849939` (single). Oracle: principal = `PrimaryContactPartyId`.
      TODOS os contatos recebem `COMPANY_ID` (o campo "empresa" do contato); o principal é
      apontado separadamente no campo `UF_CRM_1768849939`.
    - Campo multiple tipo `crm`: enviar `fields[UF_CRM_xxx][]` (lista de IDs); para LIMPAR,
      enviar `fields[UF_CRM_xxx][]` com string vazia (lista vazia `[]` NÃO limpa).
11. Empresa: campos de lista que vinham vazios — Segmento (`UF_CRM_1779455633`), Faixa de
    funcionários (`UF_CRM_1780510158496`), Satisfação do cliente (`UF_CRM_SATISFACAO_CLIENTE`,
    campo criado via `crm.company.userfield.add` + itens via `fields[LIST]`).
    - Segmento: LOV está em FSCM `standardLookups/SEGMENTO/child/lookupCodes` (não em VO do CRM);
      ver `LOV_FSCM` em `de_para_valores.py`.
    - Faixa `GRANDE` → "> 1000 (Grande)" não casava por espaço; usar `norm_loose` (fallback).
    - Satisfação: Oracle `OrganizationDEO_SatisfacaoDoCliente_c` (BOA/CRITICO/EXCELENTE/INSATISFEITO/
      REGULAR/RISCO_PERDA). O de-para antigo apontava para "Sigla do cliente" (errado) — corrigido.
12. Gerente de Customer Success (`UF_CRM_1779458529`, employee) = lookup por `user.search` NOME
    (`OrganizationDEO_GerenteDeCustomerService_c`), igual ao proprietário.
13. Descrição da empresa (`OrganizationDEO_DescricaDaEmpresa_c`) é CLOB (código interno
    `EXTN_ATTRIBUTE_CLOB001`); o conteúdo NÃO vem no JSON, mas é acessível via enclosure
    `/accounts/{PartyNumber}/enclosure/OrganizationDEO_DescricaDaEmpresa_c` (usar **PartyNumber**,
    NÃO PartyId). Ver `OracleClient.enclosure` e `contatos.buscar_descricao_empresa`.
14. Dedup: `migrador.find_existing` também consulta o campo custom de ID Oracle (`CAMPO_ID_ORACLE`)
    como fallback (registros antigos sem ORIGINATOR_ID). Migração de conta: `migrar_conta.py`
    (idempotente, aplica payload completo em create E update).
15. Contato (campos): pessoa responsável = `ASSIGNED_BY_ID` (lookup `OwnerEmailAddress`/`OwnerName`);
    pronome de tratamento = `HONORIFIC` (crm_status; map manual `PRONOME_TRATAMENTO_MAP`);
    cargo = `UF_CRM_1779907578` (Oracle `JobTitleCode` = FSCM `standardLookups/RESPONSIBILITY`);
    título do cargo = `POST` (Oracle `JobTitle`, texto livre); status do contato = `UF_CRM_1779908157` (Oracle
    `SalesProfileStatus` = FSCM `standardLookups/ZCA_SALES_ACCOUNT_STATUS`: ACTIVE→Ativo, INACTIVE→Inativo);
    descrição = `UF_CRM_1779911104902` (CLOB `PersonDEO_DESCRICAO_c` via enclosure, PartyNumber);
    bounced `UF_CRM_1779908122` e participante GUS `UF_CRM_1779908089109` (booleanos).
    Telefone/E-mail são `crm_multifield`: enviar `fields[PHONE][0][VALUE]`/`[VALUE_TYPE]` (NÃO como
    string simples; `build_payload` pula `crm_multifield`).
    - Variantes de código: quando o dado real difere da LOV (ex.: "Origem do lead" tem
      `GERENTE_NEGOCIOS` E `GERENTE_DE_NEGOCIOS`), usar `VALORES_MANUAIS` em `de_para_valores.py`.
16. Child resources de `opportunities` (`OpportunityContact`, `ChildRevenue`, `Attachment`,
    e os CLOBs via `enclosure`) funcionam usando **OptyNumber** na URL, NÃO OptyId:
    - `/opportunities/{OptyNumber}/child/OpportunityContact` (contatos da opp).
    - `/opportunities/{OptyNumber}/child/ChildRevenue` (linhas de receita / produtos).
    - `/opportunities/{OptyNumber}/child/Attachment?fields=FileName,FileContents` (anexos, já em base64).
    - `/opportunities/{OptyNumber}/enclosure/{DescriptionTextLong_c|MotivoDoEncerramentoDetalhadoLong_c|InfoAdicionalFaturamento_c}` (CLOBs).
17. Produtos da oportunidade: `ChildRevenue` → `crm.deal.productrows.set`. Usar `PRICE=UnitPrice`
    e `QUANTITY=Quantity` (o `ExpectAmount` tem escala errada — ex.: 49680 em vez de 41400).
    O `productrows.set` recebe `rows[0][PRODUCT_ID]`, `rows[0][PRICE]`, `rows[0][QUANTITY]` (indexado).
18. Valores monetários (VALOR SE/LU/MA/CLOUD/AMS/SLA/MONITORAMENTO e VALOR TOTAL):
    - `EmailValorTotal_c` é STRING em formato BR ("308.905,75") — NÃO mandar para campo double
      (o Bitrix interpreta como 308.91). Usar `parse_valor()` (converte/arredonda).
    - `Revenue{SE|AMS|...}_c` é INCONSISTENTE no Oracle (às vezes total, às vezes 0, às vezes
      valor do grupo errado). Fonte CONFIÁVEL = somar `RevnAmount` por `GrupoProduto_c` do
      `ChildRevenue` (`valores_por_grupo` em `migrar_oportunidade.py`).
    - Um fluxo automático do Bitrix recalcula esses campos usando o PREÇO UNITÁRIO do produto
      (ignora quantidade). Foi corrigido no Bitrix (adicionar condição `ORIGINATOR_ID ≠ "oracle"`
      ou usar PRICE×QUANTITY). Migração já grava os valores corretos.
19. Anexos (file): o campo antigo `UF_CRM_1779801097055` era single. Criar campo múltiplo
    `UF_CRM_ANEXOS` (crm.deal.userfield.add, USER_TYPE_ID=file, MULTIPLE=Y) e subir via
    `crm.item.update` (JSON body) com `fields[ufCrmAnexos]` = array de `[nome, base64]`.
    O mecanismo `fileData` do `crm.deal.update` só aceita UM arquivo; multiple exige
    `crm.item.update` + nome do campo em camelCase (`ufCrmAnexos`).
20. Adicionar itens a campo enum: `crm.deal.userfield.update` com `fields[LIST]` (itens existentes
    com `ID`, novos com `ID="n0","n1",...`).
21. Família de produto do negócio: campos corretos são `UF_CRM_1779799488` ("Família de produto")
    e `UF_CRM_6AB51E4E5474B` ("Família de produto - lead", campo criado no negócio) — AMBOS recebem
    o MESMO valor da oportunidade (`FamiliaDeProdutos_c`). O `UF_CRM_1768850832` ("Família de
    produtos") está vazio/desativado — não usar. De-para de códigos em `FAMILIA_MAP` (deal) e
    `FAMILIA_LEAD_MAP` (campo "-lead"; IDs de item diferentes, ex.: AMS=6619 vs 13983).
    Inclui variante `SYNCHRO_4ME` vs LOV `SYNCHRO4ME`.
22. Pré-venda (`UF_CRM_1789490557`, enum multiple): `PreVenda_c` pode ter valores separados por
    vírgula (múltiplos). De-para em `PREVENDA_MAP`; itens Richard Donzel (13255), Luis Garcia
    (13257), Flavio Pitta (13259) foram adicionados via `crm.deal.userfield.update`.
23. Parceiros (item1196): proprietário = `user.search` por NOME (`OwnerName`; não há e-mail do
    dono). Contato principal = crm.contact (dedup `ORIGIN_ID=partner-{PartyNumber}`). CNPJ =
    `ufCrm69_1789482861108`. Endereço = `ufCrm69_1779816395963` (tipo address; sanitizar `|`/`;`
    do valor, que são delimitadores internos do campo). "usuários parceiros" = `ufCrm69_1788270441`
    (employee multiple) fica vazio para contatos externos (não são usuários Bitrix).
24. `crm.item.list` com `select[]` duplicado: usar lista de tuplas `params=[("select[]","id"),
    ("select[]",CAMPO)]` (dict com chave repetida sobrescreve).
25. Concorrentes do negócio (`UF_CRM_1789560499`, enum **multiple** — campo novo criado porque o
    antigo `UF_CRM_1779805301` era single): NÃO há campo correspondente no JSON da opportunity.
    Vem do child `OpportunityCompetitor2` (via OptyNumber), atributo `PartyName` (nome do
    concorrente; pode haver vários, `PrimaryFlg` marca o principal). De-para nome→item do enum
    por similaridade (`id_concorrente` em `migrar_oportunidade.py`); itens lidos AO VIVO via
    `crm.deal.fields` (o JSON local pode estar defasado). Enviar como LISTA de IDs.
26. Conta informada para referência (`UF_CRM_1779805434`, crm company single): Oracle
    `ContaReferencia_Id_c` (OrganizationProfileId = PartyId da conta) → resolver para o ID
    Bitrix da empresa via o mapa de `UF_CRM_1789041431158` (`carregar_empresas`). Se a conta
    referenciada ainda não foi migrada, o campo fica vazio (re-rodar depois de migrar a conta).
27. Fases do negócio (`STAGE_ID` via `DEAL_STAGE_MAP`): Oracle tem `Encerramento em Aprovação
    Ganho/Cancelado/Perdido` → Bitrix `UC_4ZLXXL`/`UC_QHZUY3`/`UC_SDWSPI`. Sem o de-para, o
    `STAGE_ID` inválido faz o deal cair na primeira fase ("Diagnóstico").
28. Empresa: "Histórico de conquista da conta" (`UF_CRM_1779459961463`) é CLOB
    (`OrganizationDEO_HistoricoDeConquistaDaConta_c`, via enclosure com **PartyNumber**), igual
    à "Descrição da empresa" — ver `contatos.buscar_historico_conquista`.
29. Empresa: campos enum **multiple** ("ERP" `UF_CRM_1779460963` e "Atividade"
    `UF_CRM_1779455884263`) vêm do Oracle como string separada por vírgula (ex.: `ECC_SAP,LEGADO`).
    `build_payload` (migrar_conta.py) divide por vírgula, aplica o de-para por código e envia
    LISTA de IDs (`bitrix()` usa `fields[k][]`).
30. Lead: vínculo com conta/oportunidade. Lead liga à conta via `CustomerId` (= `PartyId` da
    account) — NÃO há vínculo direto à opportunity (o `OpportunityIdForConvert`/`OpportunityName`
    não são queryable). Os leads das contas já migradas (MELI/Audi/Volks) NÃO existem; usar
    contas com leads (ex.: Mondelez). O owner do lead NÃO tem e-mail no JSON (`OwnerPartyName`/
    `OwnerId`); resolver via `/resources?q=PartyId={OwnerId}` → `EmailAddress` → `user.search`
    (`resolver_owner_email`). `OwnerPartyName` pode ter espaço duplo ("Tatiana  Vieira Pereira").
31. Lead (campos extras, além do de-para automático):
    - Contato principal = `PrimaryContactId` → `UF_CRM_1779736432` ("Nome do contato principal",
      crm CONTACT). Se não existir no Bitrix, criar na hora (`criar_contato`).
    - Parceiro Principal = `NomeDoParceiroLead_c` → `UF_CRM_1779734915` (item1196), casar por
      título (`buscar_parceiro`).
    - Agente de prospecção = `AgenteProspeccao_c` → `UF_CRM_1779733909` (employee, por nome).
    - Empresa = `CustomerId` → `COMPANY_ID`.
    - Status = `StatusLead_c` → `STATUS_ID` (`STATUS_LEAD_MAP`/`lead_status_c`): NOVO→NEW,
      QUALIFICADO→IN_PROCESS, CONVERTIDO→CONVERTED, REJEITADO→UC_F09Q1R, NAO_QUALIFICADO→JUNK,
      EM_PROSPECCAO→UC_7YI5UA, EM_APROVACAO→ON_HOLD, ACEITO→RESTORED, INACESSIVEL→1,
      INBOUND→UC_38814A, DEMANDA_*→UC_* (fallback: `StatusCode`→`lead_status`). O `StatusLead_c`
      espelha os estágios reais do lead no Bitrix (o `StatusCode` UNQUALIFIED/QUALIFIED/... é
      genérico e fazia o lead cair em "Novo" mesmo quando era "Rejeitado").
    - Parceiro Principal = `PartnerId` (PartyId do parceiro) → `find_existing("item1196", ...)`;
      fallback por título (`NomeDoParceiroLead_c`). Não usar só nome (pode haver duplicidade).
    - Descrições são CLOB/attachment (NÃO vêm no JSON): `DescricaoOportunidade_c` →
      `UF_CRM_1779737212811` ("Descrição da oportunidade") e `DescricaoLead_c` →
      `UF_CRM_1779737221467` ("Descrição do lead") via `/leads/{LeadId}/enclosure/{attr}`
      (`buscar_clob_lead`).
32. Lead: campos "choice" do Oracle que no Bitrix são "string" (o de-para automático só cobre
    enum). Gravar o RÓTULO (Meaning da LOV), não o código — `build_label_map()` em
    `migrar_lead.py`. Atinge: Setor, Atividade lead, ERP, Solução fiscal, Propriedade, Range de
    faturamento, Faixa de funcionários, Estágio TDF. Ex.: `ECC_SAP`→"ECC (SAP)", `GRANDE`→"> 1000 (Grande)".
33. Lead: `Segmento_c` é LOV em CASCATA (depende de `Setor_c`; `CascadingParentChoiceList=Setor_c`).
    A LOV certa não é a `MklLeadVO_LOV_For_Setor_c` (primeira do `lovResourcePath`), e sim o child
    `LOVVA_For_Segmento_c`, acessível em `/leads/{LeadId}/lov/LOVVA_For_Segmento_c` (retorna os
    segmentos do setor do lead). Resolver via `segmento_map()` (por lead, cache por LeadId).
    Ex.: `ALIMENTO_PROCESSADO`→"Alimento Processado".
34. Lead: "Status do Lead" (`StatusLead_c`: NOVO/CONVERTIDO/REJEITADO) NÃO tem campo próprio no
    Bitrix — é usado como FONTE do `STATUS_ID` (ver #31, `STATUS_LEAD_MAP`). "Tratamento do Lead"
    (`TratamentoLead_c`: SR/SRA/DR/...) é pronome de tratamento, NÃO status; o de-para automático
    (Baixo) aponta errado para "Temperatura do lead" (`UF_CRM_1779733678`) mas é ignorado (só
    "Alto" é usado). Idem "Origem do lead": `OrigemLead_c`→`UF_CRM_1779733625` (Alto) vs
    `IdNovoLead_c` (Baixo, mesmo campo) — só o Alto é usado.
35. Lead: enum "Motivos da rejeição" (`UF_CRM_1779736957`) — os itens Bitrix têm sufixo
    ("Necessidade fora do escopo **da Synchro**"), então só 1/6 casava automaticamente. De-para
    manual em `VALORES_MANUAIS` (de_para_valores.py).
36. Atividades (item1220): vínculo com conta/negócio/contato via campos nativos do SPA —
    `AccountId`→`companyId` (crm_company), `OpportunityId`→`parentId2` (crm_entity Deal, valor =
    ID inteiro do deal), `PrimaryContactId`→`contactId`, `OwnerEmailAddress`→`assignedById`
    (vem no JSON; não precisa `/resources`). Campos nativos SPA usam camelCase (`title`,
    `assignedById`, `companyId`, `parentId2`, `stageId`).
37. Atividades (item1220): de-paras adicionais (não cobertos pelo automático):
    - `StatusCode`→`stageId` (`ATIVIDADE_STAGE_MAP`): NOT_STARTED→`DT1220_111:NEW`,
      COMPLETE→`DT1220_111:PREPARATION` (Concluída), IN_PROGRESS→`DT1220_111:UC_7X9ROH`
      (Em andamento), ON_HOLD→`DT1220_111:CLIENT`, CANCELED→`DT1220_111:UC_P8PT4C`,
      WAITING→`DT1220_111:NEW`. (LOV = fndStaticLookups `ZMM_ACTIVITY_STATUS_CD`.)
    - `PriorityCode` (1/2/3)→`ufCrm81_1779993997990` "Prioridade" (7581=Alta, 7583=Média, 7585=Baixa).
    - `TipoDeObjetivo_c`→`ufCrm81_1779993878245` "Tipo de objetivo" (7575=Conclusão de fase,
      7577=Evolução dentro da fase, 7579=Sem evolução).
    - `Tracao_c` (choice)→`ufCrm81_1779994010984` "Tração" (string: Amarelo/Verde/Vermelho).
38. Booleanos em SPA (`crm.item.*`) usam **"Y"/"N"**, NÃO "1"/"0" (que vale para crm.deal/company/
    contact). Ex.: `ufCrm81_1779993785877` "Compromisso do cliente". Confirmado no item1220.
39. Atividades: `ActivityPartialDescription` já vem no JSON (não é CLOB via enclosure). O `Subject`
    pode ser numérico ("9618") em registros antigos — manter fiel à origem. `ActivityFunctionCode`
    (TASK/APPOINTMENT/CALLREPORT) e `Nome_c`/`RelativoA_c` não têm campo correspondente no item1220.
40. Proprietário não encontrado em opp **fechada**: se `buscar_usuario` não acha o owner no Bitrix
    (usuário desligado) E a fase é fechada (`SalesStage` começa com "Encerr" — `is_stage_fechada`
    em `mapeamento.py`), gravar `ASSIGNED_BY_ID = USUARIO_DESATIVADO` ("5403",
    crm.inativos@synchro.com.br). Sem isso o deal cai sob o usuário do webhook (Gabriel, 3894).
    Para opps ABERTAS sem owner, manter o comportamento padrão. Ajuste em massa já aplicado nas
    opps migradas.
41. CriarCampanha (item1204): mapeamento direto — `Id`→`ufCrm73_1789386148200` (id oracle),
    `RecordName`→`title`, `DataInicio_c`→`ufCrm73_1779970143723` (date),
    `DataFim_c`→`ufCrm73_1779970152890` (date). Proprietário: `OraZcxOwner_Id_c` é
    **ResourceProfileId** (não PartyId) → resolver e-mail via `/resources?q=ResourceProfileId=X`
    → `EmailAddress` → `user.search`; fallback por nome (`OraZcxOwner_c`). NÃO há campo no
    item1204 para `Organization_Id_Conta_CriarCampanha` (conta) nem
    `Opportunity_Id_Oportunidade_CriarCampanha` (oportunidade) — ficam fora. O campo
    `parentId1208` ("Campanhas Synchro") do item1204 é preenchido pela migração do item1208
    (no Oracle a relação é invertida: `CampanhasSynchro_c.Campanha_Id_c` → `CriarCampanha_c`).
42. CampanhasSynchro (item1208): vínculos via campos nativos do SPA — `Conta_Id_c`→`companyId`
    (crm_company), `Oportunidade_Id_c`→`parentId2` (crm_entity Deal), `Campanha_Id_c`→
    `parentId1204`, `Parceiro_Id_c`→`parentId1196` (crm_entity). Proprietário = **`Proprietario_c`**
    (nome do dono comercial, NÃO `OraZcxOwner_c` que é o owner do registro/record). O nome Oracle é
    completo (ex.: "Juliana Grande Quito Alves") e não bate com o Bitrix ("Juliana Quito"); resolver
    por `user.search` + scoring de tokens (`resolver_proprietario`): nome completo → primeiro nome →
    pontua por substrings, preferindo usuário ACTIVE. Etapa = `Status_c`→`stageId`
    (`STATUS_STAGE_MAP`: GANHA_SYNCHRO→`DT1208_105:UC_WC0B3W` "Ganha pela Synchro",
    GANHA_PARCEIRO→`...UC_CKPC9J`, EXCLIENTE_BASEHISTORICA→`...UC_U35HFZ`,
    OPORTUNIDADE_ANDAMENTO→`...UC_6QSEXW`, OPORTUNIDADE_ANDAMENTOPARCEIRO→`...UC_KVLVF4`,
    OPORTUNIDADE_PERDIDA→`...UC_IQSWYG`, OPTOU_CONCORRENTE→`...UC_LYJB6G`,
    IMPLEMENTACAO_PROPRIA→`...UC_VTO0TA`, CONTA_ABORDAR→`...CLIENT`,
    AGUARDANDO_RETORNO→`...UC_ZWK0M3`, CLIENTE_VERSAO_GARANTIA→`...PREPARATION`,
    CLIENTE_NAO_QUER_ATUALIZAR→`...NEW`, JA_UTILIZA_SYNCHRO→`...UC_UOWR35`).
    Contato principal = `ContatoPrincipal_c`→`ufCrm75_1789733976970` (string, campo novo).
    Valor da campanha = `ValorDaCampanha_c`→`opportunity` + `currencyId` (CurrencyCode).
    "Solução atual" = `SolucaoAtual_c`→`ufCrm75_1779972229` (enum sem título; LOV própria
    `CampanhasSynchro_LOV_For_SolucaoAtual_c`, de-para por similaridade — DIFERENTE de `Solucao_c`
    que vai para `ufCrm75_1779971274882`). A LOV de `Solucao_c` tem códigos combinados
    `SYNCHRO4ME_4DFE`/`SYNCHRO4TDF_4DFE` que NÃO têm item no enum "Solução" do item1208 —
    mapear via `SOLUCAO_EXTRA` para Synchro4Me/Synchro4TDF (senão gravam 0). Observação =
    `Observacao_c` é **CLOB** via enclosure
    `/CampanhasSynchro_c/{Id}/enclosure/Observacao_c`→`ufCrm75_1779971360229` (string). Enums
    (choice→enum) resolvidos por `build_value_mapping` (Modalidade, Quem implanta, Solução,
    Complexidade, Valor igual a opp). `DataInicio_c`/`DataFim_c` NÃO têm campo no item1208;
    `DataDoProjeto_c` ("Data do início do projeto")→`ufCrm75_1786458496` (date, campo sem título).
    Filtro: só importa campanhas cuja `Conta_Id_c` já está migrada (resto é pulado). Oportunidade
    (`parentId2`) e parceiro (`parentId1196`) só preenchem se o deal/parceiro já foi migrado.
43. SPV (item1216, serviceRequests): vínculos via campos nativos do SPA — `AccountPartyId`→
    `companyId` (crm_company), `Oportunidade_Id_c`→`parentId2` (crm_entity Deal). Usuários:
    "Relatado por" = `ReportedByEmailAddress`/`ReportedByPartyName`→`assignedById`;
    "Atribuído A" = `AssigneeEmailAddress`/`AssigneePersonName`→`ufCrm79_1779884865` (employee).
    Ambos resolvidos por e-mail → `user.search` (fallback nome, mesma heurística de tokens).
    Nomes com underscores entre letras ("L_u_c_i_l_e_n_e Santos", "B_e_n_e_d_i_t_o Desativado")
    marcam usuários desativados: `mapeamento.normalizar_nome` remove os underscores e a palavra
    "Desativado" e casa com `NOMES_ESPECIAIS` ("lucilene santos"→722 Lucilene Santos,
    "benedito"→712 Benedito Rosa, "a lemos"→672 Alexane Lemos, "arquiteto3"→5403 usuário
    desativado padrão) — reusar na migração
    de oportunidades (o "Benedito Desativado" aparece lá também). "Hora início"/"Hora fim" = `HoraInicio_c`/`HoraFim_c` ("14:00", só hora)
    → `ufCrm79_1788870184951`/`ufCrm79_1788870154046` (datetime): combinar com
    `DataInicio_c`/`DataFim_c` ("YYYY-MM-DD HH:MM:SS"). Etapa =
    `StatusCd`→`stageId` (`STATUS_STAGE_MAP`: ORA_SVC_NEW→`DT1216_109:NEW` "Nova SPV",
    ORA_SVC_INPROGRESS→`...UC_SUMLKY` "Em elaboração", ORA_SVC_RESOLVED/ORA_SVC_CLOSED→
    `...SUCCESS` "Concluído", CANCELADO/REJEITADO→`...FAIL` "Cancelado"). Enums (choice→enum)
    via `build_value_mapping`: `Tipo_c`→`ufCrm79_1779884264` (Tipo), `PreVendaL_c`→
    `ufCrm79_1779884392` (Pré-venda), `Local_c`→`ufCrm79_1779890957` (Local). "Carlos Roberto"
    (pré-venda que saiu da Synchro) não está mais na LOV — de-para manual `CARLOS_ROBERTO`→
    `13383` em `VALORES_MANUAIS` (de_para_valores.py). Enums sem título do item1216 (de-para
    manual no script): `TipoDeProposta_c`→`ufCrm79_1779890459` (Atualização de versão/Cliente
    novo/Migração cloud/Outro), `AtividadesASeremConsideradas_c`→`ufCrm79_1779890513`
    (Funcional/Funcional técnica/Técnica), `ProjetoFiscalSeraImplantadoComERP_c`→
    `ufCrm79_1779890578` (Y→Sim/N→Não), `TipoDeImplantacao_c`→`ufCrm79_1779890614`
    (Misto/Presencial/Remoto), `PoliticaDespesas_c`→`ufCrm79_1779890804` (Cobrável/Embutido),
    `HorasDeDeslocamento_c`→`ufCrm79_1779890839` (idem), `BasesEmQueASynchroDeveAtuar_c`→
    `ufCrm79_1779890878` (enum **multiple**, separado por vírgula → LISTA de IDs; variantes
    `BASE_HOMOLOGACAO_1`/`BASE_DE_HOMOLOGACAO_1` e typo `DESENVOLVIMENTO_PRODUCAO`).
    `DetalhamentoDasDespesas_c`/`Descricao_c` são CLOBs **vazios** no Oracle (enclosure 404).
    Descrição =
    `ProblemDescription`→`ufCrm79_1779888785090`. `DuracaoTotalAtividadeHoras_c` vem como
    "1:00"/"1,0" (parsear para número). Filtro: só importa SPVs cuja `AccountPartyId` já está
    migrada (resto é pulado).
44. Parcelas (item1212): vínculo via campo nativo do SPA — `Oportunidade_Id_c`→`parentId2`
    (crm_entity Deal; a Parcela NÃO tem campo de conta próprio, a conta vem via oportunidade).
    Proprietário = `Proprietario_c` (nome) → `user.search` (mesma heurística). Campos:
    `RecordName`→`title`, `NumeroDaParcela_c`→`ufCrm77_1779974666870` (Número da parcela),
    `TipoDaOportunidade_c`→`ufCrm77_1779974764693` (Tipo de oportunidade, enum via
    `build_value_mapping`),     `Mes_c`→`ufCrm77_1779974836736` (Meses de faturamento),
    `PorcentagemDaParcela_c`→`ufCrm77_1779974787847` (fração → **valor bruto** ×100, ex. 1→"100",
    0.0833→"8.33"), `ValorDaParcela_c`→`opportunity` +
    `currencyId`, `DataDeFechamento_c`→`ufCrm77_1779974811337`, `DataDeVencimento_c`→
    `ufCrm77_1779974822842` (Data de faturamento), `DivisaoDasParcelas_c`→`ufCrm77_1780083021479`
    (enum, de-para `"2X"`→7631...`"12X"`→7651), `ProRata_c`→`ufCrm77_1779974860433` (boolean),
    `DescricaoDaParcela_c` (CLOB via enclosure)→`ufCrm77_1779974849348`. Filtro: só importa
    parcelas cuja `Oportunidade_Id_c` já está migrada (resto é pulado).
45. Proprietário (resolução por e-mail): `resolver_usuario` prioriza o e-mail e, se ele não
    resolver (e-mails genéricos tipo `mkt@synchro.com.br`), **agora cai no de-para por nome**
    (`NOMES_ESPECIAIS`) antes de desistir. Também há `EMAILS_ESPECIAIS` em `mapeamento.py`
    (`mkt@synchro.com.br`→5405 CRM_Prospecção). Sem isso, contas/objetos cujo owner é um e-mail
    genérico caíam no dono padrão do webhook (Gabriel, 3894). Já corrigido em massa (4.039 contas).
46. Setor da conta (`UF_CRM_1779455763`): labels/códigos do Oracle com typo quebravam o de-para
    automático — `ENGEN_CONSTRUCIVIL` ("Engenharia & Costru**ç**ão Civil" → Bitrix "Construção")
    e `SERVICOS_PROFISSIONAIS` (código com espaço no fim). Corrigidos por proximidade (difflib);
    ver `reparar_owner_setor.py` (263 contas).
47. Rate-limit (migrador.py): token bucket thread-safe (Enterprise 5 req/s, burst 250; "Others"
    2/50), configurável via env `BITRIX_RPS`/`BITRIX_BURST`. Detecta 503 `QUERY_LIMIT_EXCEEDED`
    e 429 `OPERATION_TIME_LIMIT` no **status HTTP** (não só no corpo JSON). Backoff diferenciado:
    503 → espera curta + zera o burst (`penalize`); 429 → dorme até `operating_reset_at` (lido do
    objeto `time` de toda resposta). `BITRIX_TOTAL_SHARDS` divide RPS e burst entre processos
    paralelos (o limite de rate é por IP+Bitrix, não por webhook; o `operating` é por webhook/app).
    `_launch_parallel.py`/`_run_contatos_shards.py` setam `BITRIX_TOTAL_SHARDS`. `mapeamento._user_search`
    passou a usar `migrador.get_json` (ganha throttle/backoff) e o cache de usuários é persistente.
48. `crm.deal.update` dispara automação (~3,6s/comando) → `batch_size=50` (50×3,6s=180s) estoura o
    limite de 60s por request do Bitrix e devolve `INTERNAL_SERVER_ERROR` em TODOS os comandos do
    lote. Para `crm.deal.update` usar `batch_size=10` (~36s/lote). SPAs (`crm.item.*`) gravam
    ~0,5s/comando (item1208), então `batch_size=50` é seguro. Medir antes de assumir.
49. Oracle: `registros_todos`/`registros` com `fields=[...]` reduz MUITO o tempo (opportunities:
    ~10s → ~1,1s/página). Usar `fields` quando não precisa do registro completo (ex.: só
    OptyId/KeyContactId/SalesStage). `reparar_contatos` usa `fields` já.
50. `parentId1208` (item1204, "Campanhas Synchro") é **single** (`isMultiple:false`, parent 1208),
    mas a relação `CriarCampanha→CampanhasSynchro` é one-to-many (até 335 campanhas synchro por
    CriarCampanha). Campo single não representa o vínculo inverso; o vínculo útil é o forward
    `parentId1204` no item1208 (já migrado, 2015/2019). `parentId1208` não é preenchido (0 de 18).
51. Proprietários divergentes (`NOMES_ESPECIAIS`): "Patricia Nakamura Vianna"→1026 (Patrícia
    Nakamura) e "Rafael Ferreira"→4447 (Rafael de Paulo Ferreira). "Rafael Ferreira" antes
    resolvia errado pelo scoring de tokens para o usuário "Rafael Ferreira - RFP - ( GCT )"
    (o scoring prefere ACTIVE, e o 4447 é inativo). O de-para explícito corrige; reparo em
    `reparar_owner_campanha.py` (206 campanhas re-atreladas).
52. Contatos: fetch completo de `/contacts` (45.197) leva ~11 min. A "Descrição"
    (`PersonDEO_DESCRICAO_c`) é CLOB via enclosure e é o GARGALO (~1s/contato → ~47 min p/ 22,5k);
    por isso `montar_contato_payload` ganhou `incluir_descricao` (default True; o `migrar_contato.py`
    passa False na massa). Bitrix exige e-mail OU telefone válido — contatos com ambos inválidos
    ("Work E-mail incorrect"/"Work Phone must not be empty") são rejeitados (37 ficam de fora).
53. Atividades (item1220): o "operation time limit" do Bitrix é **por conta/usuário**, NÃO por
    webhook — rodar 6 shards em webhooks do MESMO usuário (3894) NÃO acelera a escrita de SPA e
    ainda divide o RPS de leitura (`BITRIX_TOTAL_SHARDS`). Para gravação em massa de SPA, manter
    **processo único** (igual contatos/leads); o paralelismo útil é o do payload (ThreadPoolExecutor).
    Taxa sustentada ~30–40 gravações/min (480s de operating por janela de ~10min).
54. Atividades: ~428 gravações falharam com `Error creating a message` — erro intermitente de
    automação/timeline do Bitrix (o item é criado/atualizado mas a "mensagem" da linha do tempo
    falha), sem padrão de dado. O reparo `migrar_atividade.py --reparar-erros` recuperou tudo
    (343 criadas + 85 atualizadas, 0 erros na re-rodada). `crm.item.list` tem `total`/`next`;
    `carregar_existentes_item` paginado por `start` funciona, mas o `start` **não é offset seguro
    além de ~5M** (wrappa) — paginar por `start += 50` com corte em `len<50` (mapas `data/mapa_crm_*.json`).
55. Atividades: `Tracao_c` é o campo de "Tração" correto (choice: AMARELO/VERDE/VERMELHO).
    `TracaoAtualizada_c` é fórmula ("Text Formula", `queryable:false`) e hoje SEMPRE igual a
    `Tracao_c` (0 divergências) — ignorar. Dono "Lucas Fernando dos Santos da Silva" (Bitrix 2128)
    resolve por e-mail `lucas.fernando@synchro.com.br`; não houve atividades dele no Oracle
    (a dúvida do usuário se resolveu na própria atualização).
56. Família de produto (deal `UF_CRM_1779799488` "Família de produto" × lead `UF_CRM_1779735693`
    "Família de produto - lead"): as listas foram **unificadas** (mesmos 42 itens nos dois campos)
    via `_unificar_familia.py` (união + rótulos canônicos, ex.: `CONTAB`/`Contab`→`CONTAB`,
    `Opecon`→`OPECON`, `Synchro4me`→`Synchro4Me`, `FIS+CON+DFE+EXP`→`Fis+Con+DFe+EXP`,
    `AE + GOA`→`AE+GOA`). As LOVs Oracle são DIFERENTES por entidade (`FamiliaDeProdutos_c`
    no deal, `FamiliaProdutos_c` no lead), então o de-para continua por entidade: deal via
    `FAMILIA_MAP` (código→ID) e lead via `build_value_mapping` (meaning→VALUE, case-insensitive).
    Atenção: o item deal `6651` foi renomeado de "Governança" para "GOA" (decisão do usuário),
    então o código Oracle `GOVERNANCA` agora grava como "GOA".
57. Família de produto - lead no NEGÓCIO: além do campo `UF_CRM_1779799488` ("Família de produto"),
    o negócio tem o campo `UF_CRM_6AB51E4E5474B` ("Família de produto - lead", enum single, 42 itens
    com IDs próprios: AMS=13983, ...). A migração grava o MESMO valor da oportunidade
    (`FamiliaDeProdutos_c`) nos DOIS campos: `FAMILIA_MAP` (deal) e `FAMILIA_LEAD_MAP` ("-lead").
    Backfill dos negócios já migrados (últimos 10 anos por `LastUpdateDate`):
    `python migrar_oportunidade.py --familia-lead` (idempotente; flags `--limite N`/`--shard i N`/
    `--anos N`).
58. Produtos NÃO usam userfield (`crm.product.userfield.*` → 404): campos custom são **propriedades**
    (`crm.product.property.*`). O campo "Grupo de produto" é uma propriedade de LISTA
    (`PROPERTY_TYPE="L"`), criada idempotentemente por `migrar_produto.obter_propriedade_grupo()`
    (CODE `GRUPO_PRODUTO` → campo `PROPERTY_309`; opções LU/MA/CL/SE/AMS/SLA/MONITORAMENTO/ALL).
    Grava-se pelo **ID do item** do enum (mapa `{VALUE -> ID}` lido de `crm.product.property.get`;
    ex.: LU=395, ALL=409, CL=399). O grupo CLOUD do Oracle é exibido como "CL" no Bitrix
    (`PRODUTO_GRUPO_BITRIX` em config.py → `migrar_produto.traduzir_grupo`). Aplicado em
    `migrar_produto.py` (carga completa) e `atualizacao_diaria.montar_produto` (sync diária).
    1.448 produtos preenchidos (65 sem grupo; MONITORAMENTO=0 hoje).
59. SPV → negócio (campo "SPVs da oportunidade" `UF_CRM_1792538652`): o vínculo SPV→deal fica
    no campo reverso nativo `PARENT_ID_1216` (single, "SPV"), que **fica vazio** mesmo quando a
    SPV tem `parentId2` preenchido via API. O usuário criou um fluxo no Bitrix para preencher isso,
    e para o backfill histórico usou o campo custom `UF_CRM_1792538652` ("SPVs da oportunidade",
    tipo `crm` **multiple**, `isDynamic`, settings `DYNAMIC_1216: "Y"`). Valor = **lista de strings
    com o ID da SPV** (ex.: `["14967"]`; SEM prefixo `DYNAMIC_1216_`). Helpers em `migrar_spv.py`:
    `spvs_todas_por_negocio()` (varre todas as SPVs → `{deal_id: [spv_id, ...]}`), `spvs_do_negocio(deal_id)`
    (via `crm.item.list` com `filter[parentId2]`), `_campo_spv_atual`, `comando_vincular_spv` e
    `vincular_spvs_negocios(deal_ids)` (grava onde diverge, idempotente). Backfill
    `vincular_spv_negocio.py` (2.401 negócios, 0 erros) + integrado ao `atualizacao_diaria.py`
    (`processar_spv` coleta os negócios afetados — novo E antigo `parentId2` — e re-vincula no
    final). Gravar lista de IDs (strings) via `fields[UF_CRM_1792538652][]`; para limpar, `[""]`.

## STATUS atual
- CONTA (empresa) — RESOLVIDA: tipo de conta, campos de lista (por ID), conta pai,
  proprietário (usuário), gerente CS (usuário), URL, endereço completo, logotipo, contatos
  (todos, com dedup). Usar `migrar_conta.py` (dedup idempotente).
- CONTATO — RESOLVIDO (carga em massa `migrar_contato.py` + vínculo `vincular_contato_conta.py`):
  pessoa responsável, pronome (HONORIFIC), cargo, título do cargo (POST), empresa (COMPANY_ID),
  telefone, e-mail, bounced, status do contato, participante do GUS, origem do lead, descrição
  (CLOB via enclosure). 45.160/45.197 migrados (37 com e-mail/telefone inválido no Oracle ficaram
  de fora). "Descrição" (CLOB) pulada na carga em massa por performance — backfill com
  `python migrar_contato.py --descricao`. Contato principal vinculado em 3.227 contas.
- NEGÓCIO (deal) — RESOLVIDO: todos os campos (agente de prospecção, contato envio nota/ativação,
  parceiro principal, pré-venda, família de produto (+ "Família de produto - lead"), demais contatos
  da opp, CLOBs, datas, duração, horas AF/CF/GP/CT, valores por grupo, produtos, anexos,
  concorrentes e conta informada para referência). Usar `migrar_oportunidade.py`.
  Owner de opp FECHADA sem usuário no Bitrix → `USUARIO_DESATIVADO` (5403).
  Fluxos automáticos do Bitrix: usuário ajustou (condição `ORIGINATOR_ID ≠ "oracle"` ou
  PRICE×QUANTITY no cálculo de valores).
- PRODUTOS — RESOLVIDO: grupo no nome (`[GRUPO]`), via `catalogProductGroups` + `productGroupProducts`
  (mapa em `data/produto_grupo.json`), e campo "Grupo de produto" (`PROPERTY_307`, lista). Usar
  `migrar_produto.py`. Vínculo opp→produtos via `ChildRevenue` (OptyNumber) + `crm.deal.productrows.set`.
- PARCEIROS — RESOLVIDO: proprietário, contato principal, telefone/e-mail, CNPJ, endereço,
  tipo/status/selos. Usar `migrar_parceiro.py`. "usuários parceiros" fica vazio (contatos
  externos ainda não são usuários Bitrix).
- LEAD — RESOLVIDO E MIGRADO POR COMPLETO (42.010/42.010): todos os campos do de-para +
  proprietário (via `/resources`→e-mail), empresa (`CustomerId`), contato principal (criado na
  hora se faltar), parceiro principal, agente de prospecção, rótulos de "choice"→string,
  Segmento em cascata, enum "Motivos da rejeição" (de-para manual). Usar `migrar_lead.py`
  (flags `--limite N` / `--descricao`). Obs.: owner externo (não-usuário Bitrix) fica com o
  responsável padrão. Descrições CLOB backfilladas via `backfill_descricao_lead.py`
  (12.436 leads com conteúdo).
- ATIVIDADES (item1220) — RESOLVIDO E MIGRADO POR COMPLETO (33.571 registros): vínculo
  conta/negócio/**lead**/contato (`AccountId→companyId`, `OpportunityId→parentId2`,
  `LeadId→parentId1`, `PrimaryContactId→contactId`), proprietário, etapa (`StatusCode`→`stageId`),
  prioridade, tipo de objetivo, tração (choice→string: Amarelo/Verde/Vermelho), objetivo,
  descrição, data de vencimento, "Compromisso do cliente" (booleano Y/N). Usar
  `migrar_atividade.py` (flags `--limite N` / `--shard <i> <N>` / `--reparar-erros`).
- CRIAR CAMPANHA (item1204) — RESOLVIDO: nome, data início/fim, proprietário (via
  `/resources`→e-mail). Usar `migrar_campanha.py` (18 registros importados).
- CAMPANHAS SYNCHRO (item1208) — RESOLVIDO E MIGRADO POR COMPLETO (2.014/2.014, 638 criadas +
  1.376 atualizadas, 0 erros): vínculo empresa/negócio/campanha/parceiro,
  proprietário (`Proprietario_c` → `user.search` por nome com scoring de tokens), etapa
  (`Status_c`→`stageId`), contato principal, valor da campanha, solução atual, observação
  (CLOB), enums (Modalidade, Quem implanta, Solução, Complexidade, Valor igual a opp), datas,
  horas, strings. Usar `migrar_campanha_synchro.py` (só importa campanhas das contas já
  migradas; demais são puladas). Oportunidade/parceiro só preenchem se já migrados.
  Proprietários divergentes corrigidos via `reparar_owner_campanha.py` (#51).
- SPV (item1216) — RESOLVIDO: vínculo empresa/negócio, "Relatado por" (`ReportedBy`→
  `assignedById`), "Atribuído A" (`Assignee`→`ufCrm79_1779884865`), etapa (`StatusCd`→`stageId`),
  Tipo/Pré-venda/Local (enums), descrição, datas, horas (início/fim), strings, booleanos. Usar
  `migrar_spv.py` (só importa SPVs das contas já migradas). Vínculo SPV→negócio no campo
  "SPVs da oportunidade" (`UF_CRM_1792538652`, multiple): backfill `vincular_spv_negocio.py`
  (2.401 negócios, 0 erros) + atualização automática no `atualizacao_diaria.py` (#59).
- PARCELAS (item1212) — RESOLVIDO: vínculo negócio (`Oportunidade_Id_c`→`parentId2`),
  proprietário (`Proprietario_c`), número da parcela, tipo de oportunidade (enum), valor
  (`opportunity`+moeda), datas, divisão de parcelas, pró-rata, descrição (CLOB). Usar
  `migrar_parcela.py` (só importa parcelas das oportunidades já migradas).

## Pendências (próximos passos)
1. Automação no Negócio: condição `ORIGINATOR_ID ≠ "oracle"` nos fluxos (ou desativar).
2. Replicar o fluxo da Conta (já validado) para as demais entidades (parcelas, SPV, atividades).
3. Refatorar scripts de teste para usar migrador.upsert() (dedup) em todos.
4. "usuários parceiros" (item1196): preencher quando os contatos dos parceiros virarem
   usuários Bitrix.
5. Migrar POR COMPLETO as entidades com gap (Oracle vs Bitrix, medido em 2026-09-22):
   - Leads (crm.lead): FEITO (42.010/42.010, descrições CLOB backfilladas).
   - Atividades (item1220): FEITO (~33,5 mil; 428 falhas intermitentes `Error creating a message`
     recuperadas via `migrar_atividade.py --reparar-erros`).
   - SPV (item1216): 2.280 / 7.359.
   - Parcelas (item1212): 122 / 3.004.
   Obs.: negócios/empresas/parceiros/produtos/campanhas/contatos já ~completos (contatos: 37 inválidos).
