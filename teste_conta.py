import json
import os

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK
import mapeamento
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
    conta = load("conta_teste.json")
    val_acc = build_value_mapping("accounts", "company")

    payload = build_payload("accounts", "company", conta, val_acc)
    payload["TITLE"] = conta.get("OrganizationName")
    payload["COMPANY_TYPE"] = mapeamento.company_type(conta.get("Type"))
    payload["ORIGINATOR_ID"] = "oracle"
    payload["ORIGIN_ID"] = str(conta.get("PartyId"))

    print("=== PAYLOAD (conta Audi) ===")
    print(json.dumps(payload, ensure_ascii=False, indent=1))

    r = bitrix("crm.company.update", fields=payload, extra={"id": 77})
    print("\n=== update result ===")
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
