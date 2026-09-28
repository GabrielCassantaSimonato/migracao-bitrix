import json
import os

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, CONTATO_PRINCIPAL, DEMAIS_CONTATOS
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
        payload[bfield] = val
    return payload


def main():
    opp = load("opp_mais_preenchida.json")
    val_opp = load("de_para_valores_opportunities.json")

    p = build_payload("opportunities", "deal", opp, val_opp)
    p["TITLE"] = opp.get("Name")
    p["STAGE_ID"] = mapeamento.deal_stage(opp.get("SalesStage"))
    p["ORIGINATOR_ID"] = "oracle"
    p["ORIGIN_ID"] = str(opp.get("OptyId"))
    p["COMPANY_ID"] = 77

    # contato principal (KeyContactId) vs demais contatos (restante da conta)
    contatos_conta = contatos_mod.buscar_contatos_conta(opp.get("TargetPartyId"))
    primary_ct, outros_ct = contatos_mod.separar_negocio(opp, contatos_conta)
    if primary_ct:
        bid = contatos_mod.id_bitrix_contato(primary_ct.get("PartyId"))
        if bid:
            p[CONTATO_PRINCIPAL["deal"]] = bid
    demais_ids = contatos_mod.ids_bitrix_contatos(outros_ct)
    if demais_ids:
        p[DEMAIS_CONTATOS["deal"]] = demais_ids
    print("contato principal (oracle):", primary_ct.get("ContactName") if primary_ct else None,
          "| demais:", [c.get("ContactName") for c in outros_ct])

    for tipo, bfield in VALOR_FIELDS.items():
        v = opp.get(f"Revenue{tipo}_c")
        if v not in (None, ""):
            p[bfield] = v
    vtotal = opp.get("EmailValorTotal_c") or opp.get("Revenue")
    if vtotal not in (None, ""):
        p[VALOR_TOTAL] = vtotal

    print("=== PAYLOAD (negócio 133, enums por ID) ===")
    print(json.dumps(p, ensure_ascii=False, indent=1))

    r = bitrix("crm.deal.update", fields=p, extra={"id": 133})
    print("\n=== update result ===")
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
