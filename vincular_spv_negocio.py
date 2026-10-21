"""Vincula as SPVs aos negócios no campo "SPVs da oportunidade" (UF_CRM_1792538652).

Para cada negócio que tem SPV atrelada (parentId2 no item1216), grava a lista de SPVs
no campo multiple do negócio. Idempotente (re-lê o valor atual e só atualiza quem diverge).

Uso:
  python vincular_spv_negocio.py            # todos os negócios com SPV
  python vincular_spv_negocio.py --limite N # só N negócios (teste)
"""

import sys

import migrador
import migrar_spv


def main():
    limite = None
    if "--limite" in sys.argv:
        i = sys.argv.index("--limite")
        if i + 1 < len(sys.argv):
            limite = int(sys.argv[i + 1])

    deal_spvs = migrar_spv.spvs_todas_por_negocio()
    print(f"{len(deal_spvs)} negocios com SPV atrelada", flush=True)

    deals = sorted(deal_spvs)
    if limite:
        deals = deals[:limite]

    comandos, metas = [], []
    for deal_id in deals:
        alvo = deal_spvs[deal_id]
        atual = migrar_spv._campo_spv_atual(deal_id)
        if migrar_spv._listas_iguais(atual, alvo):
            continue
        comandos.append(migrar_spv.comando_vincular_spv(deal_id, alvo))
        metas.append(deal_id)

    print(f"{len(comandos)} negocios a atualizar", flush=True)

    erros = 0
    CHUNK = 100
    for base in range(0, len(comandos), CHUNK):
        chunk_cmds = comandos[base:base + CHUNK]
        chunk_metas = metas[base:base + CHUNK]
        res, errs = migrador.batch(chunk_cmds, batch_size=10)
        for deal_id, r, e in zip(chunk_metas, res, errs):
            if e:
                erros += 1
                print(f"  ERRO deal {deal_id}: {e.get('error_description') or e.get('error')}", flush=True)
        print(f"  ... {min(base + CHUNK, len(comandos))}/{len(comandos)}", flush=True)

    print("=" * 60)
    print(f"atualizados={len(comandos) - erros} erros={erros}")


if __name__ == "__main__":
    main()
