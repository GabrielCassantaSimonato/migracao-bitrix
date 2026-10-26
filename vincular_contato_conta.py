import sys

import migrador
from config import CAMPO_ID_ORACLE, CONTATO_PRINCIPAL
from oracle_client import OracleClient


def _parse_desde(argv):
    """Data mínima (YYYY-MM-DD) de criação da empresa no Bitrix, ou None."""
    for i, a in enumerate(argv):
        if a == "--desde":
            return argv[i + 1] if i + 1 < len(argv) else None
        if a.startswith("--desde="):
            return a.split("=", 1)[1]
    return None


def main():
    desde = _parse_desde(sys.argv[1:])
    o = OracleClient()

    contatos = migrador.carregar_existentes_crm("crm.contact.list", CAMPO_ID_ORACLE["contact"])
    print(f"contatos no Bitrix: {len(contatos)}")

    filtros = [
        ("select[]", "ID"), ("select[]", CAMPO_ID_ORACLE["company"]),
        ("select[]", CONTATO_PRINCIPAL["company"]),
    ]
    if desde:
        filtros.append(("filter[>=DATE_CREATE]", f"{desde}T00:00:00"))
        print(f"filtro: empresas criadas desde {desde}")

    empresas = {}
    start = 0
    while True:
        r = migrador._get("crm.company.list", params=filtros + [("start", start)])
        res = r.get("result", [])
        for c in res:
            v = c.get(CAMPO_ID_ORACLE["company"])
            oid = v.get("value") if isinstance(v, dict) else v
            p = c.get(CONTATO_PRINCIPAL["company"])
            pv = p.get("value") if isinstance(p, dict) else p
            if oid:
                empresas[str(oid)] = (int(c["ID"]), pv)
        if len(res) < 50:
            break
        start += 50
    print(f"empresas no Bitrix: {len(empresas)}")

    contas = o.registros_todos("accounts")
    print(f"{len(contas)} contas no Oracle")

    comandos = []
    metas = []
    for rec in contas:
        pid = rec.get("PartyId")
        if pid is None:
            continue
        info = empresas.get(str(pid))
        if not info:
            continue
        company_id, primary_atual = info
        primary_party = rec.get("PrimaryContactPartyId")
        if primary_party is None:
            continue
        novo = contatos.get(str(primary_party))
        if not novo:
            continue
        if primary_atual is not None and str(primary_atual) == str(novo):
            continue
        comandos.append(migrador.command("crm.company.update",
                                         fields={CONTATO_PRINCIPAL["company"]: novo},
                                         extra={"id": company_id}))
        metas.append((str(pid), rec.get("OrganizationName")))

    print(f"{len(comandos)} contas com contato principal a atualizar")

    erros = 0
    res, errs = migrador.batch(comandos)
    for (pid, name), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO empresa {pid} {name}: {e.get('error_description') or e.get('error')}", flush=True)

    print("=" * 60)
    print(f"contato principal atualizado em {len(comandos) - erros} contas | erros={erros}")


if __name__ == "__main__":
    main()
