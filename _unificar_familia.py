# -*- coding: utf-8 -*-
"""
Unifica as listas dos campos "Família de produto" (deal) e "Família de produtos"
(lead) para ficarem idênticas (união das duas, com rótulos canônicos).

Uso:
    python _unificar_familia.py --dry-run
    python _unificar_familia.py            # aplica no Bitrix
"""
import re
import sys
import unicodedata

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK

WB = BITRIX_WEBHOOK.rstrip("/")

CAMPO_DEAL = "UF_CRM_1779799488"   # "Família de produto" (deal)
CAMPO_LEAD = "UF_CRM_1779735693"   # "Família de produtos" (lead)

# Rótulos canônicos (união deal + lead, caixa/espaço normalizados).
CANONICAL = [
    "AE",
    "AE+GOA",
    "AMS",
    "CIAP",
    "CNPJs Adicionais",
    "CONTAB",
    "CredPIS",
    "CTe",
    "DFe Manager",
    "ECD",
    "ECD+ECF",
    "ECF",
    "EFD Contribuições",
    "EFD ICMS IPI",
    "EFD PIS/COFINS Synchro4Me",
    "Emissor Fiscal",
    "Fis",
    "Fis+Con",
    "Fis+Con+DFe+EXP",
    "Fis+DFe",
    "Fis+Gov",
    "GDT",
    "GOA",
    "Inbound",
    "Insight",
    "Int ORA",
    "Int SAP",
    "MDest",
    "Monitoramento",
    "NFSe",
    "NFe",
    "Obrigações",
    "OPECON",
    "REINF",
    "Recebimento Fiscal",
    "Recup ST",
    "Sustentação",
    "Synchro4DFe",
    "Synchro4DRC",
    "Synchro4Me",
    "Synchro4TDF",
    "eTaxEngine",
]


def key(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def get_userfield(entity, campo):
    r = requests.get(f"{WB}/crm.{entity}.userfield.list",
                     params={"filter[FIELD_NAME]": campo}, timeout=120)
    r.raise_for_status()
    items = r.json().get("result", [])
    return items[0] if items else None


def current_items(entity, campo):
    """Retorna [(ID, VALUE, SORT)] dos itens atuais do campo."""
    r = requests.get(f"{WB}/crm.{entity}.fields", timeout=120)
    r.raise_for_status()
    meta = r.json()["result"].get(campo, {})
    items = meta.get("items", [])
    out = []
    for i in items:
        out.append((str(i["ID"]), i["VALUE"], int(i.get("SORT", 0))))
    return out


def build_list(items):
    by_key = {key(v): (iid, v, sort) for iid, v, sort in items}
    out = []
    new_idx = 0
    for cval in CANONICAL:
        k = key(cval)
        if k in by_key:
            iid, _, _ = by_key[k]
            out.append({"ID": iid, "VALUE": cval, "SORT": str((len(out) + 1) * 100)})
        else:
            out.append({"ID": f"n{new_idx}", "VALUE": cval, "SORT": str((len(out) + 1) * 100)})
            new_idx += 1
    return out


def apply_update(entity, userfield, lista, dry_run):
    params = {"id": userfield["ID"]}
    for i, item in enumerate(lista):
        params[f"fields[LIST][{i}][ID]"] = item["ID"]
        params[f"fields[LIST][{i}][VALUE]"] = item["VALUE"]
        params[f"fields[LIST][{i}][SORT]"] = item["SORT"]
    if dry_run:
        print(f"  [dry-run] crm.{entity}.userfield.update id={userfield['ID']} ({len(lista)} itens)")
        return None
    r = requests.post(f"{WB}/crm.{entity}.userfield.update", data=params, timeout=120)
    return r.json()


def main():
    dry_run = "--dry-run" in sys.argv

    for entity, campo in (("deal", CAMPO_DEAL), ("lead", CAMPO_LEAD)):
        uf = get_userfield(entity, campo)
        if not uf:
            print(f"ERRO: userfield {campo} ({entity}) nao encontrado")
            return
        items = current_items(entity, campo)
        lista = build_list(items)

        old_by_id = {iid: v for iid, v, _ in items}
        adds = [x for x in lista if str(x["ID"]).startswith("n")]
        renames = [(x["ID"], old_by_id.get(x["ID"]), x["VALUE"])
                   for x in lista if not str(x["ID"]).startswith("n")
                   and old_by_id.get(x["ID"]) != x["VALUE"]]

        print(f"\n[{entity}] {campo}: {len(items)} -> {len(lista)} itens "
              f"({len(adds)} novos, {len(renames)} renomeados)")
        for a in adds:
            print(f"   + {a['VALUE']}")
        for iid, old, new in renames:
            print(f"   ~ {iid}: {old!r} -> {new!r}")

        res = apply_update(entity, uf, lista, dry_run)
        if res is not None and "result" not in res:
            print(f"   !! erro: {res}")

    print("\nConcluido." + (" (dry-run)" if dry_run else ""))


if __name__ == "__main__":
    main()
