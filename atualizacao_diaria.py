"""Atualização diária incremental (Oracle -> Bitrix).

Detecta registros criados/atualizados nos últimos N dias (filtro no servidor),
migra (cria/atualiza) com diff campo a campo, gera log .xlsx e envia por e-mail.

Uso:
  python atualizacao_diaria.py --dias 14          # primeira rodada (2 semanas)
  python atualizacao_diaria.py --dias 1           # delta diario
  python atualizacao_diaria.py --dias 14 --sem-email   # so gera o xlsx, sem enviar
"""

import sys

from config import ENTIDADE_ITEM, USUARIO_DESATIVADO, CONTATO_PRINCIPAL
from de_para_valores import build_value_mapping
from oracle_client import OracleClient
import mapeamento
import migrador
import contatos as contatos_mod
import migrar_conta
import migrar_oportunidade
import migrar_lead
import migrar_spv
import migrar_parceiro
import migrar_campanha
import migrar_campanha_synchro
import migrar_parcela
import migrar_atividade
import migrar_produto
from incremental import (
    buscar_delta, LazyMap, carregar_labels, carregar_enum_items, status_labels_para,
    diferenciar, obter_atual, Log, enviar_email, montar_corpo, periodo,
)


def _push(comandos, entidade, payload, multi, existing_id):
    if entidade.startswith("item"):
        eid = ENTIDADE_ITEM[entidade]
        extra = {"entityTypeId": eid}
        if existing_id is not None:
            extra["id"] = existing_id
            comandos.append(migrador.command("crm.item.update", fields=payload, multi=multi, extra=extra))
        else:
            comandos.append(migrador.command("crm.item.add", fields=payload, multi=multi, extra=extra))
    elif entidade == "product":
        if existing_id is not None:
            comandos.append(migrador.command("crm.product.update", fields=payload, extra={"id": existing_id}))
        else:
            comandos.append(migrador.command("crm.product.add", fields=payload))
    else:
        if existing_id is not None:
            comandos.append(migrador.command(f"crm.{entidade}.update", fields=payload, multi=multi, extra={"id": existing_id}))
        else:
            comandos.append(migrador.command(f"crm.{entidade}.add", fields=payload, multi=multi))


def processar(entidade, nome_display, registros, montar, id_field, nome_fn, log, batch_size=None):
    """Upsert + diff + log para uma lista de registros de uma entidade.

    CRM nativo (company/contact/lead/deal) usa batch_size=10 porque o update
    dispara automação (~3,6s/comando) e lotes maiores estouram os 60s do Bitrix.
    SPAs (crm.item.*) e produto gravam ~0,5s/comando e seguem com 50."""
    if batch_size is None:
        batch_size = 50 if (entidade.startswith("item") or entidade == "product") else 10
    labels = carregar_labels(entidade)
    enum_items = carregar_enum_items(entidade)
    status_labels = status_labels_para(entidade)
    comandos, metas = [], []
    for rec in registros:
        oid = rec.get(id_field)
        if oid is None:
            continue
        nome = nome_fn(rec) if callable(nome_fn) else (rec.get(nome_fn) or str(oid))
        try:
            p = montar(rec)
        except Exception as e:
            log.erro(nome_display)
            print(f"  !! {nome_display} montar {oid}: {e}", flush=True)
            continue
        if p is None:
            continue
        multi = None
        if isinstance(p, tuple):
            p, multi = p
        existing = migrador.find_existing(entidade, str(oid))
        if existing is None:
            _push(comandos, entidade, p, multi, None)
            metas.append((nome_display, str(oid), nome, "created", None))
        else:
            atual = obter_atual(entidade, existing)
            mud = diferenciar(atual, p, labels, enum_items, status_labels)
            if mud:
                _push(comandos, entidade, p, multi, existing)
                metas.append((nome_display, str(oid), nome, "updated", mud))
            else:
                log.sem_alteracao(nome_display)

    res, errs = migrador.batch(comandos, batch_size=batch_size)
    for meta, r, e in zip(metas, res, errs):
        nome_display, oid, nome, action, mud = meta
        if e:
            log.erro(nome_display)
            print(f"  ERRO {nome_display} {oid} {nome}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            log.criado(nome_display, nome)
        else:
            log.atualizado(nome_display, nome, mud)


# ---------------------------------------------------------------------------
# montar_* por entidade (reuso da lógica dos migrar_*.py)
# ---------------------------------------------------------------------------

def montar_conta(rec, ctx):
    p = migrar_conta.build_payload("accounts", "company", rec, ctx["val_acc"])
    p["TITLE"] = rec.get("OrganizationName")
    p["COMPANY_TYPE"] = mapeamento.company_type(rec.get("Type"))
    p["ORIGINATOR_ID"] = "oracle"
    p["ORIGIN_ID"] = str(rec.get("PartyId"))
    owner = mapeamento.resolver_usuario(rec.get("OwnerEmailAddress"), rec.get("OwnerName"))
    if owner:
        p["ASSIGNED_BY_ID"] = owner
    g = rec.get("OrganizationDEO_GerenteDeCustomerService_c")
    if g:
        p[migrar_conta.CAMPO_GERENTE_CS] = mapeamento.resolver_usuario(None, g) or USUARIO_DESATIVADO
    d = contatos_mod.buscar_descricao_empresa(rec.get("PartyNumber"))
    if d:
        p[migrar_conta.CAMPO_DESCRICAO] = d
    h = contatos_mod.buscar_historico_conquista(rec.get("PartyNumber"))
    if h:
        p[migrar_conta.CAMPO_HISTORICO] = h
    dm = contatos_mod.buscar_descricao_motivo(rec.get("PartyNumber"))
    if dm and dm.strip():
        p[migrar_conta.CAMPO_DESCRICAO_MOTIVO] = dm
    return p


def montar_contato(rec, ctx):
    acct = rec.get("AccountPartyId")
    company_id = ctx["lazy_company"].get(acct) if acct is not None else None
    return contatos_mod.montar_contato_payload(rec, ctx["val_ct"], company_id=company_id, incluir_descricao=False)


def vincular_contato_principal(registros):
    """Vincula o contato principal (`PrimaryContactPartyId`) ao campo
    `CONTATO_PRINCIPAL["company"]` das empresas do período. Deve rodar DEPOIS da
    migração dos contatos (o contato precisa existir no Bitrix para resolver o ID).
    Retorna (atualizados, erros). Idempotente: re-lê o valor atual e só atualiza quem diverge."""
    comandos, metas = [], []
    for rec in registros:
        primary = rec.get("PrimaryContactPartyId")
        if primary is None:
            continue
        company_id = migrador.find_existing("company", str(rec.get("PartyId")))
        if not company_id:
            continue
        contact_id = migrador.find_existing("contact", str(primary))
        if not contact_id:
            continue
        atual = obter_atual("company", company_id)
        cur = atual.get(CONTATO_PRINCIPAL["company"])
        cur_val = cur.get("value") if isinstance(cur, dict) else cur
        if cur_val is not None and str(cur_val) == str(contact_id):
            continue
        comandos.append(migrador.command("crm.company.update",
                                         fields={CONTATO_PRINCIPAL["company"]: contact_id},
                                         extra={"id": company_id}))
        metas.append((rec.get("PartyId"), rec.get("OrganizationName")))
    if not comandos:
        return 0, 0
    _, errs = migrador.batch(comandos, batch_size=10)
    erros = sum(1 for e in errs if e)
    for (pid, nome), e in zip(metas, errs):
        if e:
            print(f"  ERRO contato principal {pid} {nome}: {e.get('error_description') or e.get('error')}", flush=True)
    return len(comandos) - erros, erros


def processar_opps(ctx, log, dias):
    """Oportunidades: upsert + diff + log, e aplica produtos + anexos (criação E atualização)."""
    o = ctx["o"]
    cri, atu = buscar_delta(o, "opportunities", dias, extra_q="OptyId>0")
    print(f"Oportunidades: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    registros = cri + atu

    labels = carregar_labels("deal")
    enum_items = carregar_enum_items("deal")
    status_labels = status_labels_para("deal")
    lazy_product = ctx["lazy_product"]

    comandos, metas = [], []
    reprodutos = []  # (deal_id, opty_number, linhas, opty_id) para re-aplicar produtos mesmo sem alteracao
    for rec in registros:
        oid = rec.get("OptyId")
        if oid is None:
            continue
        nome = rec.get("Name") or str(oid)
        cid = ctx["lazy_company"].get(rec.get("TargetPartyId"))
        if not cid:
            continue
        try:
            payload, opty_id, opty_number, linhas = migrar_oportunidade.montar_payload_opp(
                o, rec, ctx["val_opp"], ctx["lazy_company"], cid)
        except Exception as e:
            log.erro("Oportunidades")
            print(f"  !! Oportunidades montar {oid}: {e}", flush=True)
            continue
        existing = migrador.find_existing("deal", str(oid))
        if existing is None:
            _push(comandos, "deal", payload, None, None)
            metas.append(("Oportunidades", str(oid), nome, "created", None, opty_number, linhas, opty_id, None))
        else:
            atual = obter_atual("deal", existing)
            mud = diferenciar(atual, payload, labels, enum_items, status_labels)
            if mud:
                _push(comandos, "deal", payload, None, existing)
                metas.append(("Oportunidades", str(oid), nome, "updated", mud, opty_number, linhas, opty_id, existing))
            else:
                log.sem_alteracao("Oportunidades")
                reprodutos.append((existing, opty_number, linhas, opty_id))

    res, errs = migrador.batch(comandos, batch_size=10)

    for meta, r, e in zip(metas, res, errs):
        nome_display, oid, nome, action, mud, opty_number, linhas, opty_id, deal_id = meta
        if e:
            log.erro(nome_display)
            print(f"  ERRO {nome_display} {oid} {nome}: {e.get('error_description') or e.get('error')}", flush=True)
            continue
        if action == "created":
            log.criado(nome_display, nome)
            deal_id = r  # novo ID do deal
        else:
            log.atualizado(nome_display, nome, mud)
        if deal_id:
            migrar_oportunidade.aplicar_produtos(deal_id, linhas, lazy_product, opty_id)
            anexos = migrar_oportunidade.anexos_oportunidade(o, opty_number)
            migrar_oportunidade.aplicar_anexos(deal_id, anexos, opty_id)

    # Negocios sem alteracao de campos: ainda re-aplica produtos/anexos, para corrigir
    # registros cujo ChildRevenue veio vazio numa rodada anterior (produtos ficaram sem migrar).
    for deal_id, opty_number, linhas, opty_id in reprodutos:
        migrar_oportunidade.aplicar_produtos(deal_id, linhas, lazy_product, opty_id)
        anexos = migrar_oportunidade.anexos_oportunidade(o, opty_number)
        migrar_oportunidade.aplicar_anexos(deal_id, anexos, opty_id)


def montar_lead(rec, ctx):
    return migrar_lead.migrar_lead_rec(
        ctx["o"], rec, ctx["val_lead"], ctx["lazy_company"], ctx["label_map"], incluir_descricao=True)


def montar_spv(rec, ctx):
    return migrar_spv.montar_payload(
        rec, ctx["val_spv"], ctx["lazy_company"], ctx["lazy_deal"], None,
        ctx.get("det_spv"))


def processar_spv(ctx, log, dias):
    """SPVs: upsert + diff + log, e aplica anexos (criação E atualização)."""
    o = ctx["o"]
    cri, atu = buscar_delta(o, "serviceRequests", dias)
    print(f"SPVs: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    registros = cri + atu
    ctx["det_spv"] = migrar_spv.buscar_detalhamentos(registros, o) if registros else {}

    labels = carregar_labels("item1216")
    enum_items = carregar_enum_items("item1216")
    status_labels = status_labels_para("item1216")

    comandos, metas = [], []
    deal_ids_afetados = set()
    for rec in registros:
        sr_id = rec.get("SrId")
        if sr_id is None:
            continue
        nome = rec.get("Title") or f"SPV {rec.get('SrNumber')}"
        try:
            p = montar_spv(rec, ctx)
        except Exception as e:
            log.erro("SPVs")
            print(f"  !! SPVs montar {sr_id}: {e}", flush=True)
            continue
        opty = rec.get("Oportunidade_Id_c")
        novo_deal = ctx["lazy_deal"].get(opty) if opty is not None else None
        if novo_deal:
            deal_ids_afetados.add(int(novo_deal))
        existing = migrador.find_existing("item1216", str(sr_id))
        if existing is None:
            _push(comandos, "item1216", p, None, None)
            metas.append(("SPVs", str(sr_id), nome, "created", None, rec.get("SrNumber"), None))
        else:
            atual = obter_atual("item1216", existing)
            old_p = atual.get("parentId2")
            old_deal = old_p.get("value") if isinstance(old_p, dict) else old_p
            if old_deal:
                deal_ids_afetados.add(int(old_deal))
            mud = diferenciar(atual, p, labels, enum_items, status_labels)
            if mud:
                _push(comandos, "item1216", p, None, existing)
                metas.append(("SPVs", str(sr_id), nome, "updated", mud, rec.get("SrNumber"), existing))
            else:
                log.sem_alteracao("SPVs")

    res, errs = migrador.batch(comandos)

    for meta, r, e in zip(metas, res, errs):
        nome_display, sr_id, nome, action, mud, srn, bitrix_id = meta
        if e:
            log.erro(nome_display)
            print(f"  ERRO {nome_display} {sr_id} {nome}: {e.get('error_description') or e.get('error')}", flush=True)
            continue
        if action == "created":
            log.criado(nome_display, nome)
            bitrix_id = migrar_spv._id_do_resultado(r)
        else:
            log.atualizado(nome_display, nome, mud)
        if bitrix_id and srn:
            anexos = migrar_spv.anexos_spv(o, srn)
            migrar_spv.aplicar_anexos_spv(bitrix_id, anexos, srn)

    # vínculo SPV -> negócio (campo "SPVs da oportunidade" no deal)
    if deal_ids_afetados:
        atu, errs_v = migrar_spv.vincular_spvs_negocios(deal_ids_afetados)
        print(f"  SPVs -> negocio: {atu} negocios atualizados, {errs_v} erros", flush=True)
        for _ in range(errs_v):
            log.erro("SPVs (negócio)")


def montar_parceiro(rec, ctx):
    return migrar_parceiro.montar_payload(rec, ctx["val_parc"])


def montar_campanha(rec, ctx):
    return migrar_campanha.montar_payload(rec, ctx["o"])


def montar_cs(rec, ctx):
    return migrar_campanha_synchro.montar_payload(
        rec, ctx["obs_cs"], ctx["val_cs"], ctx["solucao_atual_map"],
        ctx["lazy_company"], ctx["lazy_deal"], ctx["lazy_1196"], ctx["lazy_1204"])


def montar_parcela(rec, ctx):
    return migrar_parcela.montar_payload(rec, ctx["o"], ctx["val_parcela"], ctx["lazy_deal"])


def montar_atividade(rec, ctx):
    return migrar_atividade.montar_payload(
        rec, ctx["lazy_company"], ctx["lazy_deal"], ctx["lazy_contact"], ctx["lazy_lead"])


def montar_produto(rec, ctx):
    iid = rec.get("InventoryItemId")
    name = rec.get("Name") or ""
    desc = rec.get("Description") or ""
    grupos = ctx["prod_grupos"].get(iid, set())
    nome_final = migrar_produto.normalizar_nome(name, sorted(grupos)[0]) if grupos else name
    payload = {"NAME": nome_final, "DESCRIPTION": desc, "PROPERTY_305": str(iid)}
    if grupos:
        grupo = sorted(grupos)[0]
        grupo_campo, grupo_id_map = ctx["prod_grupo_campo"]
        gid = grupo_id_map.get(migrar_produto.traduzir_grupo(grupo))
        if grupo_campo and gid is not None:
            payload[grupo_campo] = gid
    return payload


def _nome_contato(rec):
    return rec.get("ContactName") or " ".join(filter(None, [rec.get("FirstName"), rec.get("LastName")])) or "?"


def _nome_cs(rec):
    return rec.get("RecordName") or f"Campanha Synchro {rec.get('RecordNumber')}"


def _nome_atividade(rec):
    return rec.get("Subject") or f"Atividade {rec.get('ActivityNumber')}"


def main():
    dias = 14
    sem_email = "--sem-email" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--dias" and i + 1 < len(sys.argv):
            try:
                dias = int(sys.argv[i + 1])
            except ValueError:
                pass
        elif a.startswith("--dias="):
            try:
                dias = int(a.split("=", 1)[1])
            except ValueError:
                pass

    o = OracleClient()
    log = Log()

    print(f"Carregando de-para de valores...", flush=True)
    ctx = {
        "o": o,
        "val_acc": build_value_mapping("accounts", "company"),
        "val_ct": build_value_mapping("contacts", "contact"),
        "val_opp": build_value_mapping("opportunities", "deal"),
        "val_lead": build_value_mapping("leads", "lead"),
        "val_spv": build_value_mapping("serviceRequests", "item1216"),
        "val_cs": build_value_mapping("CampanhasSynchro_c", "item1208"),
        "val_parc": build_value_mapping("partners", "item1196"),
        "val_parcela": build_value_mapping("Parcelas_c", "item1212"),
        "lazy_company": LazyMap("company"),
        "lazy_deal": LazyMap("deal"),
        "lazy_contact": LazyMap("contact"),
        "lazy_lead": LazyMap("lead"),
        "lazy_1196": LazyMap("item1196"),
        "lazy_1204": LazyMap("item1204"),
        "lazy_product": LazyMap("product"),
        "label_map": migrar_lead.build_label_map(),
        "solucao_atual_map": migrar_campanha_synchro.build_solucao_atual_map(),
        "prod_grupos": migrar_produto.carregar_grupos_salvos(),
        "prod_grupo_campo": migrar_produto.obter_propriedade_grupo(),
    }
    print(f"  ok. janela = {dias} dia(s)", flush=True)

    # 1. contas
    cri, atu = buscar_delta(o, "accounts", dias)
    registros_conta = cri + atu
    print(f"Contas: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("company", "Contas", registros_conta, lambda r: montar_conta(r, ctx), "PartyId", "OrganizationName", log)

    # 2. contatos
    cri, atu = buscar_delta(o, "contacts", dias)
    print(f"Contatos: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("contact", "Contatos", cri + atu, lambda r: montar_contato(r, ctx), "PartyId", _nome_contato, log)

    # 2b. contato principal das contas (após os contatos: o contato precisa existir)
    atu_cp, errs_cp = vincular_contato_principal(registros_conta)
    print(f"Contato principal das contas: {atu_cp} atualizados, {errs_cp} erros", flush=True)
    for _ in range(errs_cp):
        log.erro("Contas (contato principal)")

    # 3. oportunidades
    processar_opps(ctx, log, dias)

    # 4. leads
    cri, atu = buscar_delta(o, "leads", dias)
    print(f"Leads: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("lead", "Leads", cri + atu, lambda r: montar_lead(r, ctx), "LeadId", "Name", log)

    # 5. SPVs
    processar_spv(ctx, log, dias)

    # 6. parceiros
    cri, atu = buscar_delta(o, "partners", dias)
    print(f"Parceiros: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("item1196", "Parceiros", cri + atu, lambda r: montar_parceiro(r, ctx), "PartyId", "OrganizationName", log)

    # 7. criar campanha
    cri, atu = buscar_delta(o, "CriarCampanha_c", dias)
    print(f"CriarCampanha: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("item1204", "CriarCampanha", cri + atu, lambda r: montar_campanha(r, ctx), "Id", "RecordName", log)

    # 8. campanhas synchro (pre-busca observacoes CLOB)
    cri, atu = buscar_delta(o, "CampanhasSynchro_c", dias)
    print(f"CampanhasSynchro: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    registros_cs = cri + atu
    ctx["obs_cs"] = migrar_campanha_synchro.buscar_observacoes(registros_cs, o) if registros_cs else {}
    processar("item1208", "CampanhasSynchro", registros_cs, lambda r: montar_cs(r, ctx), "Id", _nome_cs, log)

    # 9. produtos
    cri, atu = buscar_delta(o, "products", dias)
    print(f"Produtos: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("product", "Produtos", cri + atu, lambda r: montar_produto(r, ctx), "InventoryItemId", "Name", log)

    # 10. parcelas
    cri, atu = buscar_delta(o, "Parcelas_c", dias)
    print(f"Parcelas: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("item1212", "Parcelas", cri + atu, lambda r: montar_parcela(r, ctx), "Id", "RecordName", log)

    # 11. atividades
    cri, atu = buscar_delta(o, "activities", dias, fields=migrar_atividade._activity_fields())
    print(f"Atividades: {len(cri)} criados, {len(atu)} atualizados", flush=True)
    processar("item1220", "Atividades", cri + atu, lambda r: montar_atividade(r, ctx), "ActivityId", _nome_atividade, log)

    # ---- relatorio (xlsx + email) ----
    print("=" * 60, flush=True)
    for entidade, criados, atualizados, sem, erros in log.resumo():
        print(f"  {entidade}: criados={criados} atualizados={atualizados} sem_alteracao={sem} erros={erros}", flush=True)

    xlsx = log.gerar_xlsx("log_atualizacao.xlsx")
    print(f"xlsx gerado: {xlsx}", flush=True)

    if sem_email:
        print("(--sem-email) e-mail nao enviado", flush=True)
        return

    corpo = montar_corpo(log, dias)
    assunto = f"Atualizacao diaria Bitrix - {periodo(dias)}"
    try:
        enviar_email(assunto, corpo, xlsx)
        print("e-mail enviado", flush=True)
    except Exception as e:
        print(f"!! erro ao enviar e-mail: {e}", flush=True)


if __name__ == "__main__":
    main()
