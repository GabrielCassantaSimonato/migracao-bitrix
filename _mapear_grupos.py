import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import ORACLE_USER, ORACLE_PASS, ORACLE_CRM_BASE
from oracle_client import OracleClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

GRUPOS_EXCLUIR = {"SYNCHRO", "SYNCHRO "}


def carregar_grupos(o):
    items = o.registros_todos("catalogProductGroups")
    mapa = {}
    for i in items:
        gid = i.get("ProductGroupId")
        nome = (i.get("ProductGroupName") or "").strip()
        if gid is not None and nome and nome.upper() not in GRUPOS_EXCLUIR:
            mapa[gid] = nome
    return mapa


def grupo_do_produto(iid):
    """Retorna set de ProductGroupId para um InventoryItemId (via q=InventoryItemId, confiavel)."""
    r = requests.get(f"{ORACLE_CRM_BASE}/productGroupProducts",
                     auth=(ORACLE_USER, ORACLE_PASS), verify=False,
                     params={"q": f"InventoryItemId={iid}", "limit": 20, "onlyData": "true"}, timeout=60)
    d = r.json()
    out = set()
    for i in d.get("items", []):
        if i.get("ActiveFlag") is False:
            continue
        gid = i.get("ProdGroupId")
        if gid is not None:
            out.add(gid)
    return out


def main():
    o = OracleClient()
    grupo_nome = carregar_grupos(o)
    print("grupos:", sorted(set(grupo_nome.values())))

    produtos = o.registros_todos("products")
    iids = [p.get("InventoryItemId") for p in produtos if p.get("InventoryItemId") is not None]
    print(f"{len(iids)} produtos a consultar")

    resultado = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(grupo_do_produto, iid): iid for iid in iids}
        done = 0
        for f in as_completed(fut):
            iid = fut[f]
            gids = f.result()
            nomes = {grupo_nome[g] for g in gids if g in grupo_nome}
            if nomes:
                resultado[str(iid)] = sorted(nomes)
            done += 1
            if done % 200 == 0:
                print(f"  ... {done}/{len(iids)}")

    print(f"produtos com grupo: {len(resultado)}")
    from collections import Counter
    c = Counter()
    for iid, nomes in resultado.items():
        for n in nomes:
            c[n] += 1
    print("distribuição:", dict(c))

    with open(os.path.join(DATA_DIR, "produto_grupo.json"), "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
