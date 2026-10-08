import json
import os
import re

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, PRODUTO_GRUPO_CODE, PRODUTO_GRUPOS, PRODUTO_GRUPO_BITRIX
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


def traduzir_grupo(grupo):
    """Nome do grupo no Oracle -> label do grupo no Bitrix (ex.: CLOUD -> CL)."""
    return PRODUTO_GRUPO_BITRIX.get(grupo, grupo)


def obter_propriedade_grupo():
    """Retorna (campo, {grupo -> id_item}) da propriedade de lista "Grupo de produto",
    criando-a no Bitrix se ainda não existir (idempotente)."""
    r = requests.get(f"{WB}/crm.product.property.list", timeout=120)
    r.raise_for_status()
    prop = None
    for p in r.json().get("result", []):
        if (p.get("CODE") or "") == PRODUTO_GRUPO_CODE:
            prop = p
            break
    if prop is None:
        params = {
            "fields[NAME]": "Grupo de produto",
            "fields[PROPERTY_TYPE]": "L",
            "fields[CODE]": PRODUTO_GRUPO_CODE,
            "fields[ACTIVE]": "Y",
            "fields[SORT]": "600",
        }
        for i, g in enumerate(PRODUTO_GRUPOS):
            params[f"fields[VALUES][{i}][VALUE]"] = g
            params[f"fields[VALUES][{i}][SORT]"] = str((i + 1) * 100)
        r = requests.post(f"{WB}/crm.product.property.add", data=params, timeout=120)
        r.raise_for_status()
        prop = {"ID": str(r.json().get("result"))}
    r = requests.get(f"{WB}/crm.product.property.get", params={"id": prop["ID"]}, timeout=120)
    r.raise_for_status()
    values = r.json().get("result", {}).get("VALUES") or {}
    if isinstance(values, dict):
        values = list(values.values())
    mapa = {}
    for v in values:
        if isinstance(v, dict) and v.get("VALUE") is not None:
            mapa[str(v["VALUE"])] = v["ID"]
    return f"PROPERTY_{prop['ID']}", mapa


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

    print("Garantindo propriedade 'Grupo de produto' ...")
    grupo_campo, grupo_id_map = obter_propriedade_grupo()
    print(f"  {grupo_campo} ({len(grupo_id_map)} opcoes)")

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
        if grupos:
            grupo = sorted(grupos)[0]
            gid = grupo_id_map.get(traduzir_grupo(grupo))
            if gid is not None:
                payload[grupo_campo] = gid
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
