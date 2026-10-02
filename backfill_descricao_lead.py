import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import migrar_lead as ml
import migrador
from config import CAMPO_ID_ORACLE
from oracle_client import OracleClient


def main():
    limite = None
    dias = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--limite" and i + 1 < len(args):
            try:
                limite = int(args[i + 1])
            except ValueError:
                pass
        elif a.startswith("--limite="):
            try:
                limite = int(a.split("=", 1)[1])
            except ValueError:
                pass
        elif a == "--dias" and i + 1 < len(args):
            try:
                dias = int(args[i + 1])
            except ValueError:
                pass
        elif a.startswith("--dias="):
            try:
                dias = int(a.split("=", 1)[1])
            except ValueError:
                pass

    o = OracleClient()
    existentes = migrador.carregar_existentes_crm("crm.lead.list", CAMPO_ID_ORACLE["lead"])
    print(f"leads existentes: {len(existentes)}", flush=True)

    items = list(existentes.items())

    if dias is not None:
        agora = datetime.now(timezone.utc)
        corte = (agora - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")
        recentes = {str(r.get("LeadId")) for r in o.registros_todos(
            "leads", fields=["LeadId"], q=f"LastUpdateDate>='{corte}'")}
        items = [it for it in items if it[0] in recentes]
        print(f"  janela {dias} dia(s): {len(items)} leads a considerar", flush=True)

    if limite:
        items = items[:limite]
        print(f"  limite aplicado: {len(items)} leads", flush=True)

    def _clobs(oracle_id):
        d1 = ml.buscar_clob_lead(o, oracle_id, "DescricaoOportunidade_c")
        d2 = ml.buscar_clob_lead(o, oracle_id, "DescricaoLead_c")
        return oracle_id, d1, d2

    resultados = {}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(_clobs, oid): oid for oid, _ in items}
        for f in as_completed(fut):
            oid = fut[f]
            try:
                resultados[oid] = f.result()
            except Exception as e:
                print(f"  !! {oid}: {e}", flush=True)
            done += 1
            if done % 5000 == 0:
                print(f"  ... {done}/{len(items)} clobs", flush=True)

    comandos = []
    metas = []
    for oid, bitrix_id in items:
        _, d1, d2 = resultados.get(oid, (oid, None, None))
        fields = {}
        if d1 and d1.strip():
            fields[ml.CAMPO_DESCRICAO_OPPORTUNIDADE] = d1
        if d2 and d2.strip():
            fields[ml.CAMPO_DESCRICAO_LEAD] = d2
        if not fields:
            continue
        comandos.append(migrador.command("crm.lead.update", fields=fields, extra={"id": bitrix_id}))
        metas.append((oid, bitrix_id))

    print(f"leads com descricao a atualizar: {len(comandos)}", flush=True)

    atualizados = erros = 0
    res, errs = migrador.batch(comandos, batch_size=10)
    for (oid, bid), e in zip(metas, errs):
        if e:
            erros += 1
            print(f"  ERRO {oid}: {e.get('error_description') or e.get('error')}", flush=True)
        else:
            atualizados += 1

    print("=" * 60)
    print(f"backfill descricao: atualizados={atualizados} erros={erros}")


if __name__ == "__main__":
    main()
