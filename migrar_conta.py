import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, CONTATO_PRINCIPAL, CAMPO_ID_ORACLE, USUARIO_DESATIVADO
import mapeamento
import contatos as contatos_mod
import migrador
from de_para_valores import build_value_mapping
from oracle_client import OracleClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WEBHOOK = BITRIX_WEBHOOK.rstrip("/")

CAMPO_CONTA_PAI = "UF_CRM_1779458340"
CAMPO_GERENTE_CS = "UF_CRM_1779458529"
CAMPO_DESCRICAO = "UF_CRM_1779459867870"
CAMPO_HISTORICO = "UF_CRM_1779459961463"
CAMPO_DESCRICAO_MOTIVO = "UF_CRM_1779458579405"


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def build_payload(oracle_obj, bitrix_ent, rec, val_map):
    dp = load("de_para_completo.json")[oracle_obj]["campos"]
    bf = load(f"bitrix_{bitrix_ent}_fields.json")
    payload = {}
    for c in dp:
        if not c["bitrix"] or c["confianca"] != "Alto":
            continue
        bfield = c["bitrix"]
        bmeta = bf.get(bfield) or {}
        btype = bmeta.get("type", "")
        if btype in ("crm", "employee", "file", "crm_multifield"):
            continue
        if c.get("transform") in ("fk", "fk_company"):
            continue
        val = mapeamento.aplicar_transform(c["oracle"], bfield, c.get("transform"), rec)
        if val is None or val == "" or str(val).strip() in ("", '""'):
            continue
        if btype == "enumeration" and bfield in val_map:
            de_para = val_map[bfield]["de_para"]
            if bmeta.get("isMultiple"):
                itens = []
                for code in str(val).split(","):
                    code = code.strip()
                    item = de_para.get(code)
                    if item and item not in itens:
                        itens.append(item)
                val = itens if itens else None
            else:
                val = de_para.get(str(val), val)
            if val in (None, ""):
                continue
        if btype == "boolean":
            val = "1" if val in (True, "true", "True", "Y", "y", 1, "1") else "0"
        payload[bfield] = val
    return payload


def carregar_empresas_bitrix():
    """{oracle_party_id -> bitrix_id} das empresas já migradas."""
    mapa = {}
    start = 0
    while True:
        r = requests.get(f"{WEBHOOK}/crm.company.list", params=[
            ("select[]", "ID"), ("select[]", CAMPO_ID_ORACLE["company"]), ("start", start)], timeout=120)
        res = r.json().get("result", [])
        for c in res:
            v = c.get(CAMPO_ID_ORACLE["company"])
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(c["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def carregar_contatos_bitrix():
    """{oracle_party_id -> bitrix_id} dos contatos já migrados."""
    mapa = {}
    start = 0
    while True:
        r = requests.get(f"{WEBHOOK}/crm.contact.list", params=[
            ("select[]", "ID"), ("select[]", CAMPO_ID_ORACLE["contact"]), ("start", start)], timeout=120)
        res = r.json().get("result", [])
        for c in res:
            v = c.get(CAMPO_ID_ORACLE["contact"])
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(c["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def _build_contato(ct, val_ct):
    return contatos_mod.montar_contato_payload(ct, val_ct)


def _build_conta(rec, val_acc, val_ct):
    """Monta (conta_payload, lista de (ct_payload, multi, party_id, is_primary)) de UMA conta."""
    p = build_payload("accounts", "company", rec, val_acc)
    p["TITLE"] = rec.get("OrganizationName")
    p["COMPANY_TYPE"] = mapeamento.company_type(rec.get("Type"))
    p["ORIGINATOR_ID"] = "oracle"
    p["ORIGIN_ID"] = str(rec.get("PartyId"))
    owner_id = mapeamento.resolver_usuario(rec.get("OwnerEmailAddress"), rec.get("OwnerName"))
    if owner_id:
        p["ASSIGNED_BY_ID"] = owner_id
    gerente_nome = rec.get("OrganizationDEO_GerenteDeCustomerService_c")
    if gerente_nome:
        gid = mapeamento.resolver_usuario(None, gerente_nome)
        p[CAMPO_GERENTE_CS] = gid or USUARIO_DESATIVADO
    descricao = contatos_mod.buscar_descricao_empresa(rec.get("PartyNumber"))
    if descricao:
        p[CAMPO_DESCRICAO] = descricao
    historico = contatos_mod.buscar_historico_conquista(rec.get("PartyNumber"))
    if historico:
        p[CAMPO_HISTORICO] = historico
    desc_motivo = contatos_mod.buscar_descricao_motivo(rec.get("PartyNumber"))
    if desc_motivo and desc_motivo.strip():
        p[CAMPO_DESCRICAO_MOTIVO] = desc_motivo

    contatos_lista = contatos_mod.buscar_contatos_conta(rec.get("PartyId"))
    primary_party = rec.get("PrimaryContactPartyId")
    ct_payloads = []
    for ct in contatos_lista:
        if ct.get("PartyId") is None:
            continue
        pc, multi = _build_contato(ct, val_ct)
        is_primary = primary_party is not None and str(ct.get("PartyId")) == str(primary_party)
        ct_payloads.append((pc, multi, str(ct.get("PartyId")), is_primary))
    return p, ct_payloads


def sincronizar(limite=None, so_faltantes=False):
    o = OracleClient()
    val_acc = build_value_mapping("accounts", "company")
    val_ct = build_value_mapping("contacts", "contact")
    empresas = carregar_empresas_bitrix()
    contatos = carregar_contatos_bitrix()
    print(f"empresas já migradas: {len(empresas)} | contatos já migrados: {len(contatos)}")

    contas = o.registros_todos("accounts")
    if so_faltantes:
        total = len(contas)
        contas = [c for c in contas if str(c.get("PartyId")) not in empresas]
        print(f"{total} contas no Oracle | {len(contas)} faltantes (so_faltantes=True)")
    else:
        print(f"{len(contas)} contas no Oracle")
    if limite:
        contas = contas[:limite]

    # ---- monta payloads em paralelo (leituras Oracle/CLOBs) ----
    print("Montando payloads (paralelo)...", flush=True)
    dados = [None] * len(contas)
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(_build_conta, rec, val_acc, val_ct): i for i, rec in enumerate(contas)}
        done = 0
        for f in as_completed(fut):
            i = fut[f]
            dados[i] = f.result()
            done += 1
            if done % 500 == 0:
                print(f"  ... {done}/{len(contas)} payloads", flush=True)

    # ---- PASSO 1: empresas (create/update) ----
    print("PASSO 1: empresas...", flush=True)
    comandos = []
    for rec, (conta_payload, _cts) in zip(contas, dados):
        if conta_payload is None:
            continue
        key = str(rec.get("PartyId"))
        if key in empresas:
            comandos.append(migrador.command("crm.company.update", fields=conta_payload, extra={"id": empresas[key]}))
        else:
            comandos.append(migrador.command("crm.company.add", fields=conta_payload))
    res, errs = migrador.batch(comandos)
    for rec, r, e in zip(contas, res, errs):
        key = str(rec.get("PartyId"))
        if e:
            print(f"  ERRO empresa {key} {rec.get('OrganizationName')}: {e.get('error_description') or e.get('error')}", flush=True)
        elif r and key not in empresas:
            empresas[key] = int(r)

    # ---- PASSO 2: contatos (create/update) ----
    print("PASSO 2: contatos...", flush=True)
    ccomandos = []
    cmeta = []  # (conta_key, ct_party_id, is_primary)
    for rec, (conta_payload, cts) in zip(contas, dados):
        company_id = empresas.get(str(rec.get("PartyId")))
        if not company_id:
            continue
        for pc, multi, ct_party_id, is_primary in cts:
            pc["COMPANY_ID"] = company_id
            if ct_party_id in contatos:
                ccomandos.append(migrador.command("crm.contact.update", fields=pc, multi=multi, extra={"id": contatos[ct_party_id]}))
            else:
                ccomandos.append(migrador.command("crm.contact.add", fields=pc, multi=multi))
            cmeta.append((str(rec.get("PartyId")), ct_party_id, is_primary))
    cres, cerrs = migrador.batch(ccomandos)
    primary_por_conta = {}
    for (conta_key, ct_party_id, is_primary), r, e in zip(cmeta, cres, cerrs):
        if e:
            print(f"  ERRO contato {ct_party_id}: {e.get('error_description') or e.get('error')}", flush=True)
        elif r and ct_party_id not in contatos:
            contatos[ct_party_id] = int(r)
        if r and is_primary:
            primary_por_conta[conta_key] = contatos.get(ct_party_id, r)

    # ---- PASSO 3: conta pai + contato principal ----
    print("PASSO 3: conta pai + contato principal...", flush=True)
    ucomandos = []
    umeta = []
    for rec, (conta_payload, cts) in zip(contas, dados):
        key = str(rec.get("PartyId"))
        company_id = empresas.get(key)
        if not company_id:
            continue
        up = {}
        pai_key = str(rec.get("ParentAccountPartyId")) if rec.get("ParentAccountPartyId") is not None else None
        if pai_key and pai_key in empresas:
            up[CAMPO_CONTA_PAI] = empresas[pai_key]
        if key in primary_por_conta:
            up[CONTATO_PRINCIPAL["company"]] = primary_por_conta[key]
        if up:
            ucomandos.append(migrador.command("crm.company.update", fields=up, extra={"id": company_id}))
            umeta.append(key)
    if ucomandos:
        _, uerrs = migrador.batch(ucomandos)
        for key, e in zip(umeta, uerrs):
            if e:
                print(f"  ERRO update empresa {key}: {e.get('error_description') or e.get('error')}", flush=True)

    print("=" * 60)
    print(f"contas processadas: {len(contas)} | empresas no Bitrix: {len(empresas)} | contatos: {len(contatos)}")


if __name__ == "__main__":
    import sys
    sincronizar(so_faltantes="--faltantes" in sys.argv)
