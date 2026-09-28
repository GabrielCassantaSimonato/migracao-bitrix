import json
import os
import re

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK
from oracle_client import OracleClient
import migrador

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WB = BITRIX_WEBHOOK.rstrip("/")


def normalizar_nome(name, grupo):
    """Padroniza o grupo no nome: remove o token do grupo (palavra, [colchete] ou (parenteses))
    e anexa [GRUPO] em maiusculo uma unica vez."""
    g = grupo.upper()
    base = name or ""
    base = re.sub(r"\[\s*" + re.escape(g) + r"\s*\]", " ", base, flags=re.IGNORECASE)
    base = re.sub(r"\(\s*" + re.escape(g) + r"\s*\)", " ", base, flags=re.IGNORECASE)
    base = re.sub(r"\b" + re.escape(g) + r"\b", " ", base, flags=re.IGNORECASE)
    base = re.sub(r"\s+", " ", base).strip()
    base = re.sub(r"\s+([,.;:])", r"\1", base)
    base = base.strip(" -–—")
    return f"{base} [{g}]" if base else f"[{g}]"


def carregar_grupos_salvos():
    """str(InventoryItemId) -> [nome do grupo] a partir do mapeamento confiavel (data/produto_grupo.json)."""
    with open(os.path.join(DATA_DIR, "produto_grupo.json"), encoding="utf-8") as f:
        raw = json.load(f)
    mapa = {}
    for iid, nomes in raw.items():
        mapa[int(iid)] = set(nomes)
    return mapa


def carregar_existentes():
    """PROPERTY_305 (valor) -> ID Bitrix dos produtos ja existentes."""
    mapa = {}
    start = 0
    while True:
        r = requests.get(f"{WB}/crm.product.list",
                         params={"select[]": "ID", "select[]": "PROPERTY_305", "start": start}, timeout=120)
        d = r.json()
        res = d.get("result", [])
        for p in res:
            prop = p.get("PROPERTY_305")
            if isinstance(prop, dict):
                val = prop.get("value")
            else:
                val = prop
            if val:
                mapa[str(val)] = int(p["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def main():
    o = OracleClient()

    print("Carregando mapeamento produto->grupo ...")
    prod_grupos = carregar_grupos_salvos()
    print(f"  {len(prod_grupos)} produtos com grupo mapeado")

    print("Carregando produtos ...")
    produtos = o.registros_todos("products")
    print(f"  {len(produtos)} produtos no total")

    print("Carregando produtos existentes no Bitrix ...")
    existentes = carregar_existentes()
    print(f"  {len(existentes)} ja existentes")

    criados = atualizados = sem_grupo = erros = 0
    comandos = []
    metas = []  # (iid, name, action)
    for rec in produtos:
        iid = rec.get("InventoryItemId")
        name = rec.get("Name") or ""
        desc = rec.get("Description") or ""
        if iid is None:
            continue

        grupos = prod_grupos.get(iid, set())
        if not grupos:
            sem_grupo += 1
            nome_final = name
        else:
            grupo = sorted(grupos)[0]
            nome_final = normalizar_nome(name, grupo)

        payload = {
            "NAME": nome_final,
            "DESCRIPTION": desc,
            "PROPERTY_305": str(iid),
        }
        existing = existentes.get(str(iid))
        if existing:
            comandos.append(migrador.command("crm.product.update", fields=payload, extra={"id": existing}))
            metas.append((iid, name, "updated"))
        else:
            comandos.append(migrador.command("crm.product.add", fields=payload))
            metas.append((iid, name, "created"))

    res, errs = migrador.batch(comandos)
    for (iid, name, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {iid} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"criados={criados} atualizados={atualizados} sem_grupo={sem_grupo} erros={erros}")


if __name__ == "__main__":
    main()
