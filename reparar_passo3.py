import sys

from oracle_client import OracleClient
import migrador
from config import CONTATO_PRINCIPAL

CAMPO_CONTA_PAI = "UF_CRM_1779458340"

ALVOS = [
    "300000021125404",
    "300000021125414",
    "300000021125429",
    "300000021125430",
    "300000021125445",
    "300000021125446",
    "300000021125459",
    "300000021125465",
    "300000021125484",
    "300000021125495",
    "300000021125502",
    "300000021125507",
    "300000021125518",
    "300000021125534",
    "300000021125540",
    "300000021125542",
    "300000021125555",
    "300000021125559",
    "300000021125569",
    "300000021125583",
    "300000021125584",
    "300000021125598",
    "300000021125599",
    "300000021125611",
]


def main():
    o = OracleClient()
    contas = o.registros_todos("accounts")
    por_party = {str(c.get("PartyId")): c for c in contas}
    print(f"{len(contas)} contas no Oracle", flush=True)

    comandos = []
    meta = []
    for pid in ALVOS:
        rec = por_party.get(pid)
        if rec is None:
            print(f"  conta {pid} nao encontrada no Oracle", flush=True)
            continue
        company_id = migrador.find_existing("company", pid)
        if not company_id:
            print(f"  conta {pid} sem empresa no Bitrix", flush=True)
            continue
        up = {}
        pai = rec.get("ParentAccountPartyId")
        if pai is not None:
            pbid = migrador.find_existing("company", pai)
            if pbid:
                up[CAMPO_CONTA_PAI] = pbid
        primary = rec.get("PrimaryContactPartyId")
        if primary is not None:
            ctbid = migrador.find_existing("contact", primary)
            if ctbid:
                up[CONTATO_PRINCIPAL["company"]] = ctbid
        if up:
            comandos.append(migrador.command("crm.company.update", fields=up, extra={"id": company_id}))
            meta.append(pid)
        else:
            print(f"  conta {pid}: nada a atualizar (sem pai/principal migrados)", flush=True)

    res, errs = migrador.batch(comandos)
    erros = 0
    for pid, r, e in zip(meta, res, errs):
        if e:
            erros += 1
            print(f"  ERRO update empresa {pid}: {e.get('error_description') or e.get('error')}", flush=True)
    print("=" * 60, flush=True)
    print(f"atualizados={len(meta) - erros} erros={erros}", flush=True)


if __name__ == "__main__":
    main()
