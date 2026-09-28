import json
import os

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, CONTATO_PRINCIPAL
import mapeamento
import contatos as contatos_mod
from de_para_valores import build_value_mapping

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WEBHOOK = BITRIX_WEBHOOK.rstrip("/")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def bitrix(method, fields=None, extra=None):
    params = {}
    if fields:
        for k, v in fields.items():
            params[f"fields[{k}]"] = v
    if extra:
        params.update(extra)
    r = requests.post(f"{WEBHOOK}/{method}", data=params, timeout=120)
    return r.json()


def buscar_usuario(email, nome=None):
    return mapeamento.resolver_usuario(email, nome)


def build_payload(oracle_obj, bitrix_ent, rec, val_map):
    dp = load("de_para_completo.json")[oracle_obj]["campos"]
    bf = load(f"bitrix_{bitrix_ent}_fields.json")
    payload = {}
    for c in dp:
        if not c["bitrix"] or c["confianca"] != "Alto":
            continue
        bfield = c["bitrix"]
        btype = (bf.get(bfield) or {}).get("type", "")
        if btype in ("crm", "employee", "file"):
            continue
        if c.get("transform") in ("fk", "fk_company"):
            continue
        val = mapeamento.aplicar_transform(c["oracle"], bfield, c.get("transform"), rec)
        if val in (None, ""):
            continue
        if btype == "enumeration" and bfield in val_map:
            val = val_map[bfield]["de_para"].get(str(val), val)
        if btype == "boolean":
            val = "1" if val in (True, "true", "True", "Y", "y", 1, "1") else "0"
        payload[bfield] = val
    return payload


def main():
    audi = load("conta_teste.json")
    volks = load("conta_pai_teste.json")
    val_acc = build_value_mapping("accounts", "company")
    val_ct = build_value_mapping("contacts", "contact")

    # ---------- usuário proprietário ----------
    owner_id = buscar_usuario(audi.get("OwnerEmailAddress"), audi.get("OwnerName"))
    print("Owner Bitrix ID:", owner_id)

    # ---------- 1. cria conta pai (Volks) ----------
    print("\n=== Criando conta pai (Volks) ===")
    p = build_payload("accounts", "company", volks, val_acc)
    p["TITLE"] = volks.get("OrganizationName")
    p["COMPANY_TYPE"] = mapeamento.company_type(volks.get("Type"))
    p["ORIGINATOR_ID"] = "oracle"
    p["ORIGIN_ID"] = str(volks.get("PartyId"))
    if owner_id:
        p["ASSIGNED_BY_ID"] = owner_id
    r = bitrix("crm.company.add", p)
    volks_id = r.get("result")
    print("volks_id =", volks_id, "| erro:", r.get("error"))

    # ---------- 2. atualiza Audi: conta pai, owner, url, endereço, logotipo ----------
    print("\n=== Atualizando Audi ===")
    upd = {}
    if volks_id:
        upd["UF_CRM_1779458340"] = volks_id
    if owner_id:
        upd["ASSIGNED_BY_ID"] = owner_id
    upd["UF_CRM_1779458158591"] = audi.get("URL") or ""
    upd["ADDRESS"] = audi.get("AddressLine1") or ""
    if audi.get("City"):
        upd["ADDRESS_CITY"] = audi.get("City")
    if audi.get("State"):
        upd["ADDRESS_PROVINCE"] = audi.get("State")
    upd["ADDRESS_COUNTRY"] = audi.get("Country") or ""
    upd["ADDRESS_POSTAL_CODE"] = audi.get("PostalCode") or ""
    upd["UF_CRM_1779470175089"] = "1" if audi.get("OrganizationDEO_LogotipoNAODivulgar_c") else "0"
    print(json.dumps(upd, ensure_ascii=False, indent=1))
    r = bitrix("crm.company.update", fields=upd, extra={"id": 77})
    print("update result:", r.get("result"), "| erro:", r.get("error"))

    # ---------- 3. cria TODOS os contatos ----------
    print("\n=== Criando contatos da conta ===")
    contatos = load("contatos_audi.json") if os.path.exists(os.path.join(DATA_DIR, "contatos_audi.json")) else []
    if not contatos:
        from oracle_client import OracleClient
        o = OracleClient()
        contatos = o.registros("contacts", limit=50, q="AccountPartyId=300000016304510").get("items", [])

    primary_ct, _ = contatos_mod.separar_conta(audi, contatos)
    primary_bitrix_id = None

    for ct in contatos:
        pc = build_payload("contacts", "contact", ct, val_ct)
        pc["NAME"] = ct.get("FirstName")
        pc["LAST_NAME"] = ct.get("LastName")
        if ct.get("EmailAddress"):
            pc["EMAIL"] = [{"VALUE": ct["EmailAddress"], "VALUE_TYPE": "WORK"}]
        if ct.get("PhoneNumber"):
            pc["PHONE"] = [{"VALUE": ct["PhoneNumber"], "VALUE_TYPE": "WORK"}]
        is_primary = primary_ct is not None and str(ct.get("PartyId")) == str(primary_ct.get("PartyId"))
        if not is_primary:
            pc["COMPANY_ID"] = 77
        pc["ORIGINATOR_ID"] = "oracle"
        pc["ORIGIN_ID"] = str(ct.get("PartyId"))
        r = bitrix("crm.contact.add", pc)
        cid = r.get("result")
        if is_primary:
            primary_bitrix_id = cid
        print(f"  {ct.get('ContactName')} ->", cid, "| erro:", r.get("error"), "| primario:", is_primary)

    # ---------- 3b. vincula o contato principal na empresa ----------
    if primary_bitrix_id:
        r = bitrix("crm.company.update", fields={CONTATO_PRINCIPAL["company"]: primary_bitrix_id}, extra={"id": 77})
        print("\ncontato principal da empresa ->", primary_bitrix_id, "| erro:", r.get("error"))
    else:
        print("\n!! nenhum contato principal encontrado (PrimaryContactPartyId =", audi.get("PrimaryContactPartyId"), ")")


if __name__ == "__main__":
    main()
