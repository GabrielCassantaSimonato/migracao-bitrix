import sys
from collections import defaultdict

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from oracle_client import OracleClient
from config import BITRIX_WEBHOOK, CAMPO_ID_ORACLE, USUARIO_DESATIVADO
import mapeamento
import migrador

WB = BITRIX_WEBHOOK.rstrip("/")
CAMPO_GERENTE_CS = "UF_CRM_1779458529"
ORACLE_ID_COMPANY = CAMPO_ID_ORACLE["company"]


def main():
    o = OracleClient()

    contas = o.registros_todos(
        "accounts", fields=["PartyId", "OrganizationDEO_GerenteDeCustomerService_c"]
    )
    print(f"{len(contas)} contas no Oracle", flush=True)

    empresas = {}
    start = 0
    while True:
        r = requests.get(f"{WB}/crm.company.list", params=[
            ("select[]", "ID"),
            ("select[]", ORACLE_ID_COMPANY),
            ("select[]", CAMPO_GERENTE_CS),
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

    updates = defaultdict(dict)
    por_nome = defaultdict(int)
    for c in contas:
        nome = c.get("OrganizationDEO_GerenteDeCustomerService_c")
        if not nome:
            continue
        b = empresas.get(str(c.get("PartyId")))
        if not b:
            continue
        ger = b.get(CAMPO_GERENTE_CS)
        ger = ger.get("value") if isinstance(ger, dict) else ger
        if ger not in (None, "", "0", 0):
            continue
        gid = mapeamento.resolver_usuario(None, nome)
        target = gid or USUARIO_DESATIVADO
        updates[str(b.get("ID"))][CAMPO_GERENTE_CS] = target
        por_nome[str(nome)] += 1

    print(f"gerente CS vazio a corrigir: {len(updates)}", flush=True)
    for nome, n in sorted(por_nome.items(), key=lambda x: -x[1]):
        print(f"  {nome!r} x{n}", flush=True)

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
