import sys

import difflib
from collections import defaultdict

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from oracle_client import OracleClient
from config import BITRIX_WEBHOOK, CAMPO_ID_ORACLE
from de_para_valores import norm, norm_loose, load as dload
import migrador

WB = BITRIX_WEBHOOK.rstrip("/")
CAMPO_SETOR = "UF_CRM_1779455763"
ORACLE_ID_COMPANY = CAMPO_ID_ORACLE["company"]
CRM_PROSPECCAO = "5405"
WEBHOOK_OWNER = "3894"


def norm_code(s):
    return str(s or "").strip().upper()


def main():
    o = OracleClient()

    # --- LOV Setor (codigo -> significado) ---
    lov = o.get(
        "https://fa-evip-saasfaprod1.fa.ocs.oraclecloud.com/crmRestApi/resources/11.13.18.05",
        "/OrganizationProfile_LOV_For_Setor_c",
        params={"limit": 500, "onlyData": "true"},
    )
    lov_norm = {}
    for i in lov.get("items", []):
        code = i.get("LookupCode")
        meaning = i.get("Meaning")
        if meaning:
            lov_norm[norm_code(code)] = meaning

    # --- Itens do enum Setor no Bitrix (id -> label) ---
    bf = dload("bitrix_company_fields.json")
    bitrix_items = [(str(i["ID"]), i["VALUE"]) for i in bf[CAMPO_SETOR]["items"]]

    def match_setor(meaning):
        m = norm(meaning)
        if not m:
            return None
        # 1) exato (sem acentos)
        for iid, val in bitrix_items:
            if norm(val) == m:
                return iid
        # 2) loose (sem espacos)
        ml = norm_loose(meaning)
        for iid, val in bitrix_items:
            if norm_loose(val) == ml:
                return iid
        # 3) proximidade (difflib), pega o melhor acima de 0.85
        best = (0.0, None)
        for iid, val in bitrix_items:
            r = difflib.SequenceMatcher(None, ml, norm_loose(val)).ratio()
            if r > best[0]:
                best = (r, iid)
        return best[1] if best[0] >= 0.85 else None

    setor_map = {}
    for code, meaning in lov_norm.items():
        iid = match_setor(meaning)
        setor_map[code] = iid
        if iid is None:
            print(f"  !! sem match Setor: {code} ({meaning})", flush=True)

    # --- Contas Oracle ---
    contas = o.registros_todos(
        "accounts", fields=["PartyId", "OwnerName", "OwnerEmailAddress", "OrganizationDEO_Setor_c"]
    )
    print(f"{len(contas)} contas no Oracle", flush=True)

    # --- Empresas Bitrix ---
    empresas = {}  # oracle_party_id -> {id, assigned, setor}
    start = 0
    while True:
        r = requests.get(f"{WB}/crm.company.list", params=[
            ("select[]", "ID"),
            ("select[]", "ASSIGNED_BY_ID"),
            ("select[]", ORACLE_ID_COMPANY),
            ("select[]", CAMPO_SETOR),
            ("start", start),
        ], timeout=120).json()
        res = r.get("result", [])
        for c in res:
            oid = c.get(ORACLE_ID_COMPANY)
            oid = oid.get("value") if isinstance(oid, dict) else oid
            if oid:
                empresas[str(oid)] = c
        if len(res) < 50:
            break
        start += 50
    print(f"{len(empresas)} empresas no Bitrix", flush=True)

    updates = defaultdict(dict)  # bitrix_id -> campos a atualizar

    n_owner = 0
    n_setor = 0
    for c in contas:
        party = str(c.get("PartyId"))
        b = empresas.get(party)
        if not b:
            continue
        bid = str(b.get("ID"))

        # --- task 1: owner Mkt Marketing -> CRM_Prospeccao ---
        email = str(c.get("OwnerEmailAddress") or "")
        nome = str(c.get("OwnerName") or "")
        if email.lower() == "mkt@synchro.com.br" or "marketing" in nome.lower():
            if str(b.get("ASSIGNED_BY_ID")) == WEBHOOK_OWNER:
                updates[bid]["ASSIGNED_BY_ID"] = CRM_PROSPECCAO
                n_owner += 1

        # --- task 2: setor vazio ---
        setor = b.get(CAMPO_SETOR)
        setor = setor.get("value") if isinstance(setor, dict) else setor
        if setor in (None, "", "0", 0):
            cod = c.get("OrganizationDEO_Setor_c")
            if cod:
                target = setor_map.get(norm_code(cod))
                if target:
                    updates[bid][CAMPO_SETOR] = target
                    n_setor += 1
                else:
                    print(f"  !! setor sem target: party={party} cod={cod!r}", flush=True)

    print(f"owner a corrigir: {n_owner} | setor a corrigir: {n_setor} | empresas afetadas: {len(updates)}", flush=True)

    # detalhe do setor: codigo -> target -> qtde (para conferencia)
    setor_detalhe = defaultdict(lambda: defaultdict(int))
    for c in contas:
        b = empresas.get(str(c.get("PartyId")))
        if not b:
            continue
        setor = b.get(CAMPO_SETOR)
        setor = setor.get("value") if isinstance(setor, dict) else setor
        if setor in (None, "", "0", 0):
            cod = c.get("OrganizationDEO_Setor_c")
            if cod:
                target = setor_map.get(norm_code(cod))
                if target:
                    setor_detalhe[norm_code(cod)][target] += 1
    for cod in sorted(setor_detalhe):
        for target, n in setor_detalhe[cod].items():
            print(f"  setor {cod} -> {target} x{n}", flush=True)

    if "--dry-run" in sys.argv:
        print("DRY-RUN: nada foi alterado.", flush=True)
        return

    comandos = [
        migrador.command("crm.company.update", fields=fields, extra={"id": bid})
        for bid, fields in updates.items()
    ]
    res, errs = migrador.batch(comandos)
    erros = 0
    for bid, r, e in zip(updates.keys(), res, errs):
        if e:
            erros += 1
            print(f"  ERRO update empresa {bid}: {e.get('error_description') or e.get('error')}", flush=True)
    print("=" * 60, flush=True)
    print(f"atualizados={len(comandos) - erros} erros={erros}", flush=True)


if __name__ == "__main__":
    main()
