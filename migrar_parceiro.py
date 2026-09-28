import json
import os

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, ENTIDADE_ITEM, CAMPO_ID_ORACLE
from oracle_client import OracleClient
from de_para_valores import build_value_mapping
import mapeamento
import migrador

WB = BITRIX_WEBHOOK.rstrip("/")
EID = ENTIDADE_ITEM["item1196"]
ID_FIELD = CAMPO_ID_ORACLE["item1196"]

# Bitrix "Status" (ufCrm69_1779815992549): 7047=Ativo, 7049=Expirado, 7051=Desligado
PARCEIRO_STATUS_MAP = {
    "ACTIVE": "7047",
    "REGISTERED": "7047",
    "PROSPECTIVE": "7047",
    "INACTIVE-TERMINATED": "7051",
}

CAMPO_CONTATO_PRINCIPAL = "ufCrm69_1779816393"
CAMPO_TELEFONE = "ufCrm69_1779816198383"
CAMPO_EMAIL = "ufCrm69_1779816210853"
CAMPO_URL = "ufCrm69_1779816227466"
CAMPO_ENDERECO = "ufCrm69_1779816395963"
CAMPO_USUARIOS = "ufCrm69_1788270441"
CAMPO_CNPJ = "ufCrm69_1789482861108"

def buscar_usuario(email, nome=None):
    return mapeamento.resolver_usuario(email, nome)


def bitrix(method, fields=None, extra=None, multi=None):
    params = {}
    if fields:
        for k, v in fields.items():
            params[f"fields[{k}]"] = v
    if multi:
        for k, vals in multi.items():
            for i, item in enumerate(vals):
                params[f"fields[{k}][{i}][VALUE]"] = item.get("VALUE", "")
                params[f"fields[{k}][{i}][VALUE_TYPE]"] = item.get("VALUE_TYPE", "WORK")
    if extra:
        params.update(extra)
    r = requests.post(f"{WB}/{method}", data=params, timeout=120)
    return r.json()


def montar_endereco(rec):
    partes = []
    for k in ("AddressLineOne", "City", "State", "PostalCode", "Country"):
        v = rec.get(k)
        if v:
            partes.append(str(v).strip())
    if not partes:
        return None
    addr = ", ".join(partes)
    addr = addr.replace("|", "-").replace(";", ",")
    addr = addr.strip(" ,-–—")
    return addr or None


def criar_contato_principal(rec):
    """Cria/atualiza o crm.contact do contato principal e retorna o ID Bitrix."""
    nome = (rec.get("PrimaryContactPartyName") or "").strip()
    if not nome:
        return None
    parts = nome.split(" ", 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""

    pnum = rec.get("PrimaryContactPartyNumber")
    email = rec.get("PrimaryContactEmailAddress")
    phone = rec.get("PrimaryContactPhone")

    payload = {
        "NAME": first,
        "LAST_NAME": last,
        "ORIGINATOR_ID": "oracle",
        "ORIGIN_ID": f"partner-{pnum}" if pnum else None,
    }
    multi = {}
    if email:
        multi["EMAIL"] = [{"VALUE": email, "VALUE_TYPE": "WORK"}]
    if phone:
        multi["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]

    existing = migrador.find_existing("contact", payload["ORIGIN_ID"])
    if existing:
        bitrix("crm.contact.update", fields=payload, extra={"id": existing}, multi=multi)
        return existing
    res = bitrix("crm.contact.add", payload, multi=multi)
    return res.get("result")


def montar_payload(rec, val_map):
    p = {}
    nome = rec.get("OrganizationName")
    if nome:
        p["title"] = nome

    owner = buscar_usuario(None, rec.get("OwnerName"))
    if owner:
        p["assignedById"] = owner

    url = rec.get("URL")
    if url:
        p[CAMPO_URL] = url

    cnpj = rec.get("PartnerProfilePEO_CNPJ_c")
    if cnpj:
        p[CAMPO_CNPJ] = cnpj

    telefone = rec.get("PrimaryContactPhone")
    if telefone:
        p[CAMPO_TELEFONE] = telefone
    email = rec.get("PrimaryContactEmailAddress")
    if email:
        p[CAMPO_EMAIL] = email

    endereco = montar_endereco(rec)
    if endereco:
        p[CAMPO_ENDERECO] = endereco

    pid = rec.get("PartyId")
    if pid is not None:
        p[ID_FIELD] = str(pid)

    for bfield, info in val_map.items():
        ofield = info.get("oracle_field")
        if not ofield:
            continue
        v = rec.get(ofield)
        if v in (None, ""):
            continue
        if ofield == "Status":
            p[bfield] = PARCEIRO_STATUS_MAP.get(str(v), str(v))
        else:
            p[bfield] = info["de_para"].get(str(v), v)

    # contato principal (crm.contact)
    contato_id = criar_contato_principal(rec)
    if contato_id:
        p[CAMPO_CONTATO_PRINCIPAL] = contato_id

    # usuarios parceiros (employee multiple) - resolve contatos que sejam usuarios Bitrix
    user_ids = []
    if email:
        uid = buscar_usuario(email, None)
        if uid:
            user_ids.append(uid)
    if user_ids:
        p[CAMPO_USUARIOS] = user_ids

    return p


def carregar_existentes():
    mapa = {}
    start = 0
    while True:
        r = requests.get(f"{WB}/crm.item.list", params=[
            ("entityTypeId", EID), ("select[]", "id"), ("select[]", ID_FIELD), ("start", start)], timeout=120)
        d = r.json()
        items = (d.get("result") or {}).get("items", [])
        for it in items:
            v = it.get(ID_FIELD)
            if v:
                mapa[str(v)] = it["id"]
        if len(items) < 50:
            break
        start += 50
    return mapa


def main():
    o = OracleClient()
    val_map = build_value_mapping("partners", "item1196")

    print("Carregando parceiros ...")
    parceiros = o.registros_todos("partners")
    print(f"  {len(parceiros)} parceiros")

    existentes = carregar_existentes()
    print(f"  {len(existentes)} ja existentes no Bitrix")

    criados = atualizados = erros = 0
    comandos = []
    metas = []
    for n, rec in enumerate(parceiros, 1):
        pid = rec.get("PartyId")
        if pid is None:
            continue
        payload = montar_payload(rec, val_map)
        existing = existentes.get(str(pid))
        if existing:
            comandos.append(migrador.command("crm.item.update", fields=payload, extra={"entityTypeId": EID, "id": existing}))
            metas.append((pid, rec.get("OrganizationName"), "updated"))
        else:
            comandos.append(migrador.command("crm.item.add", fields=payload, extra={"entityTypeId": EID}))
            metas.append((pid, rec.get("OrganizationName"), "created"))
        if n % 50 == 0:
            print(f"  ... {n}/{len(parceiros)}", flush=True)

    res, errs = migrador.batch(comandos)
    for (pid, name, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {pid} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"criados={criados} atualizados={atualizados} erros={erros}")


if __name__ == "__main__":
    main()
