import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import contatos
import migrador
from config import CAMPO_ID_ORACLE
from de_para_valores import build_value_mapping
from oracle_client import OracleClient
from migrar_conta import carregar_empresas_bitrix


def _nome(ct):
    return ct.get("ContactName") or " ".join(
        filter(None, [ct.get("FirstName"), ct.get("LastName")])) or "?"


def _build(ct, val_ct, empresas, incluir_descricao):
    acct = ct.get("AccountPartyId")
    company_id = empresas.get(str(acct)) if acct is not None else None
    return contatos.montar_contato_payload(ct, val_ct, company_id=company_id,
                                            incluir_descricao=incluir_descricao)


def main():
    o = OracleClient()
    val_ct = build_value_mapping("contacts", "contact")
    empresas = carregar_empresas_bitrix()
    existentes = migrador.carregar_existentes_crm("crm.contact.list", CAMPO_ID_ORACLE["contact"])
    print(f"empresas={len(empresas)} | contatos ja existentes={len(existentes)}")

    contatos_oracle = o.registros_todos("contacts")
    print(f"{len(contatos_oracle)} contatos no Oracle")

    vistos = set()
    faltantes = []
    for ct in contatos_oracle:
        pid = ct.get("PartyId")
        if pid is None:
            continue
        key = str(pid)
        if key in existentes or key in vistos:
            continue
        vistos.add(key)
        faltantes.append(ct)
    print(f"{len(faltantes)} contatos faltantes")

    limite = None
    incluir_descricao = "--descricao" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--limite"):
            try:
                limite = int(a.split("=", 1)[1])
            except (IndexError, ValueError):
                pass
    if limite:
        faltantes = faltantes[:limite]
        print(f"  limite aplicado: {len(faltantes)} contatos")
    print(f"  incluir descricao (CLOB): {incluir_descricao}")

    print("Montando payloads (paralelo)...", flush=True)
    dados = [None] * len(faltantes)
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(_build, ct, val_ct, empresas, incluir_descricao): i
               for i, ct in enumerate(faltantes)}
        done = 0
        for f in as_completed(fut):
            i = fut[f]
            try:
                dados[i] = f.result()
            except Exception as e:
                print(f"  !! payload {faltantes[i].get('PartyId')}: {e}", flush=True)
            done += 1
            if done % 500 == 0:
                print(f"  ... {done}/{len(faltantes)} payloads", flush=True)

    comandos = []
    metas = []
    for ct, d in zip(faltantes, dados):
        if d is None:
            continue
        pc, multi = d
        comandos.append(migrador.command("crm.contact.add", fields=pc, multi=multi))
        metas.append((str(ct.get("PartyId")), _nome(ct)))

    criados = erros = 0
    res, errs = migrador.batch(comandos)
    for (pid, name), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO contato {pid} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        else:
            criados += 1

    print("=" * 60)
    print(f"contatos: criados={criados} erros={erros} | empresas={len(empresas)}")


if __name__ == "__main__":
    main()
