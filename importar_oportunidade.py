import json
import os

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, CAMPO_ID_ORACLE
import mapeamento

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    rec = load("opp_mais_preenchida.json")
    dp = load("de_para_completo.json")["opportunities"]["campos"]
    bf = load("bitrix_deal_fields.json")

    payload = {}
    skip_tipos = {"crm", "employee", "file"}
    skip_transforms = {"fk", "fk_company"}
    for c in dp:
        if not c["bitrix"] or c["confianca"] != "Alto":
            continue
        bfield = c["bitrix"]
        btype = (bf.get(bfield) or {}).get("type", "")
        if btype in skip_tipos:
            continue
        if c.get("transform") in skip_transforms:
            continue
        val = mapeamento.aplicar_transform(c["oracle"], bfield, c.get("transform"), rec)
        if val in (None, ""):
            continue
        payload[bfield] = val

    print("=" * 70)
    print("PAYLOAD (preview)")
    print("=" * 70)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("=" * 70)

    # monta query params fields[KEY]=VALUE
    params = {}
    for k, v in payload.items():
        params[f"fields[{k}]"] = v

    r = requests.post(f"{BITRIX_WEBHOOK.rstrip('/')}/crm.deal.add", data=params, timeout=120)
    print("HTTP", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
