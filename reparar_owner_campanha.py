import migrador
import mapeamento
from config import ENTIDADE_ITEM, CAMPO_ID_ORACLE
from oracle_client import OracleClient

EID = ENTIDADE_ITEM["item1208"]

# Nomes Oracle cujo proprietário deve ser re-atrelado (de-para via NOMES_ESPECIAIS).
ALVOS_NOMES = ("Patricia Nakamura Vianna", "Rafael Ferreira")


def main():
    o = OracleClient()
    dados = o.registros_todos("CampanhasSynchro_c", fields=["Id", "Proprietario_c"])
    existentes = migrador.carregar_existentes_item("item1208")
    print(f"item1208 existentes={len(existentes)} | campanhas Oracle={len(dados)}", flush=True)

    comandos = []
    metas = []
    for rec in dados:
        prop = rec.get("Proprietario_c")
        if prop not in ALVOS_NOMES:
            continue
        bid = existentes.get(str(rec.get("Id")))
        if not bid:
            continue
        owner = mapeamento.resolver_usuario(None, prop)
        if not owner:
            print(f"  !! proprietario ainda nao resolvido: {prop!r}", flush=True)
            continue
        comandos.append(migrador.command(
            "crm.item.update", fields={"assignedById": owner},
            extra={"entityTypeId": EID, "id": bid}))
        metas.append((rec.get("Id"), prop, owner))

    print(f"{len(comandos)} campanhas para re-atrelar owner", flush=True)

    res, errs = migrador.batch(comandos)
    erros = 0
    for (cid, prop, owner), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {cid} {prop}: {e.get('error_description') or e.get('error')}", flush=True)

    print("=" * 60)
    print(f"atualizados={len(comandos) - erros} erros={erros}")


if __name__ == "__main__":
    main()
