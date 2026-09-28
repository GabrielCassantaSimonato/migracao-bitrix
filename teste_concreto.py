import json
import os

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, CAMPO_ID_ORACLE, CONTATO_PRINCIPAL, DEMAIS_CONTATOS
import mapeamento
import contatos as contatos_mod
from de_para_valores import build_value_mapping, norm

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WEBHOOK = BITRIX_WEBHOOK.rstrip("/")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def bitrix(method, fields=None, extra=None):
    params = {}
    if fields:
        for k, v in fields.items():
            if isinstance(v, (list, tuple)):
                params[f"fields[{k}][]"] = v
            else:
                params[f"fields[{k}]"] = v
    if extra:
        params.update(extra)
    r = requests.post(f"{WEBHOOK}/{method}", data=params, timeout=120)
    return r.json()


VALOR_FIELDS = {
    "SE": "UF_CRM_1785759782270",
    "LU": "UF_CRM_1785503561603",
    "MA": "UF_CRM_1785507451643",
    "CLOUD": "UF_CRM_1785507463090",
    "SLA": "UF_CRM_1785507492227",
    "AMS": "UF_CRM_1785853113897",
    "MONITORAMENTO": "UF_CRM_1785853129238",
}
VALOR_TOTAL = "UF_CRM_1786131567648"


def build_payload(oracle_obj, bitrix_ent, rec, val_map, include_link_fields=False):
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
        if c.get("transform") in ("fk", "fk_company") and not include_link_fields:
            continue
        val = mapeamento.aplicar_transform(c["oracle"], bfield, c.get("transform"), rec)
        if val in (None, ""):
            continue
        if btype == "enumeration" and bfield in val_map:
            val = val_map[bfield]["de_para"].get(str(val), val)
        payload[bfield] = val
    return payload


def main():
    opp = load("opp_mais_preenchida.json")
    conta = load("conta_teste.json")
    contato = load("contato_teste.json")

    val_opp = load("de_para_valores_opportunities.json")
    val_acc = build_value_mapping("accounts", "company")
    val_ct = build_value_mapping("contacts", "contact")

    # ---------- EMPRESA ----------
    print("=== Criando EMPRESA ===")
    p_acc = build_payload("accounts", "company", conta, val_acc)
    p_acc["TITLE"] = conta.get("OrganizationName") or conta.get("OrganizationDEO_RazaoSocial_c")
    p_acc["COMPANY_TYPE"] = mapeamento.company_type(conta.get("Type"))
    r = bitrix("crm.company.add", p_acc)
    company_id = r.get("result")
    print("company_id =", company_id, "| erro:", r.get("error"))

    # ---------- CONTATOS (principal vs demais) ----------
    print("\n=== Criando CONTATOS da conta ===")
    contatos_conta = contatos_mod.buscar_contatos_conta(conta.get("PartyId"))
    if not contatos_conta:
        contatos_conta = [contato]
    primary_ct, outros_ct = contatos_mod.separar_conta(conta, contatos_conta)
    primary_bitrix_id = None
    contato_por_party = {}

    for ct in contatos_conta:
        p_ct = build_payload("contacts", "contact", ct, val_ct)
        p_ct["NAME"] = ct.get("FirstName")
        p_ct["LAST_NAME"] = ct.get("LastName")
        is_primary = primary_ct is not None and str(ct.get("PartyId")) == str(primary_ct.get("PartyId"))
        if company_id and not is_primary:
            p_ct["COMPANY_ID"] = company_id
        r = bitrix("crm.contact.add", p_ct)
        cid = r.get("result")
        contato_por_party[str(ct.get("PartyId"))] = cid
        if is_primary:
            primary_bitrix_id = cid
        print("  contato ->", ct.get("ContactName"), cid, "| erro:", r.get("error"), "| primario:", is_primary)

    if company_id and primary_bitrix_id:
        bitrix("crm.company.update", fields={CONTATO_PRINCIPAL["company"]: primary_bitrix_id}, extra={"id": company_id})

    # ---------- ATUALIZA NEGÓCIO 133 ----------
    print("\n=== Atualizando NEGÓCIO 133 ===")
    p_opp = build_payload("opportunities", "deal", opp, val_opp)
    p_opp["TITLE"] = opp.get("Name")
    p_opp["STAGE_ID"] = mapeamento.deal_stage(opp.get("SalesStage"))
    p_opp["ORIGINATOR_ID"] = "oracle"
    p_opp["ORIGIN_ID"] = str(opp.get("OptyId"))
    if company_id:
        p_opp["COMPANY_ID"] = company_id

    opp_primary, opp_outros = contatos_mod.separar_negocio(opp, contatos_conta)
    if opp_primary:
        bid = contato_por_party.get(str(opp_primary.get("PartyId")))
        if bid:
            p_opp[CONTATO_PRINCIPAL["deal"]] = bid
    demais_ids = [contato_por_party[str(c.get("PartyId"))] for c in opp_outros if str(c.get("PartyId")) in contato_por_party]
    if demais_ids:
        p_opp[DEMAIS_CONTATOS["deal"]] = demais_ids
    # campos de valor (split por tipo)
    for tipo, bfield in VALOR_FIELDS.items():
        v = opp.get(f"Revenue{tipo}_c")
        if v not in (None, ""):
            p_opp[bfield] = v
    vtotal = opp.get("EmailValorTotal_c") or opp.get("Revenue")
    if vtotal not in (None, ""):
        p_opp[VALOR_TOTAL] = vtotal

    print("campos enviados:", len(p_opp))
    print(json.dumps(p_opp, ensure_ascii=False, indent=1)[:3000])
    r = bitrix("crm.deal.update", fields=p_opp, extra={"id": 133})
    print("\nupdate result:", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
