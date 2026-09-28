import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import migrador
from oracle_client import OracleClient
from migrar_oportunidade import produtos_oportunidade, valores_por_grupo, VALOR_FIELDS


def parse_shard(argv):
    shard = None
    shards = None
    for k in range(len(argv) - 2):
        if argv[k] == "--shard":
            try:
                shard = int(argv[k + 1])
                shards = int(argv[k + 2])
            except ValueError:
                pass
    return shard, shards


def batch_find_deals(o_ids):
    """{oid: deal_id} via batch crm.deal.list (ORIGINATOR_ID=oracle + ORIGIN_ID)."""
    comandos = [
        ("crm.deal.list", {
            "filter[ORIGINATOR_ID]": "oracle",
            "filter[ORIGIN_ID]": oid,
            "select[]": "ID",
        })
        for oid in o_ids
    ]
    res, errs = migrador.batch(comandos)
    out = {}
    for oid, r, e in zip(o_ids, res, errs):
        if e:
            continue
        items = r if isinstance(r, list) else (r or {}).get("result", [])
        if items:
            out[oid] = items[0]["ID"]
    return out


def main():
    shard, shards = parse_shard(sys.argv)

    o = OracleClient()
    todas = o.registros_todos(
        "opportunities", q="OptyId>0",
        fields=["OptyId", "OptyNumber"],
    )
    print(f"opps no Oracle: {len(todas)}", flush=True)

    alvo = [rec for rec in todas if rec.get("OptyId") is not None]
    alvo.sort(key=lambda x: int(x["OptyId"]))
    if shard is not None:
        alvo = migrador.shard_items(alvo, shard, shards)
        print(f"shard {shard}/{shards}: {len(alvo)} opps", flush=True)
    else:
        print(f"{len(alvo)} opps", flush=True)

    # busca ChildRevenue em paralelo e computa a soma por grupo
    soma_map = {}
    BATCH = 200
    for base in range(0, len(alvo), BATCH):
        grupo = alvo[base:base + BATCH]
        with ThreadPoolExecutor(max_workers=4) as ex:
            fut = {ex.submit(produtos_oportunidade, o, rec.get("OptyNumber")): rec
                   for rec in grupo}
            for f in as_completed(fut):
                rec = fut[f]
                soma_map[str(rec["OptyId"])] = valores_por_grupo(f.result())
        print(f"  ... child {base + len(grupo)}/{len(alvo)}", flush=True)

    com_produto = [oid for oid, soma in soma_map.items() if soma]
    print(f"negocios com produto: {len(com_produto)}", flush=True)
    if not com_produto:
        print("Nada a atualizar.", flush=True)
        return

    deal_ids = batch_find_deals(com_produto)
    print(f"deals resolvidos: {len(deal_ids)}", flush=True)

    comandos = []
    for oid in com_produto:
        did = deal_ids.get(oid)
        if not did:
            continue
        soma = soma_map[oid]
        fields = {bfield: soma.get(tipo, 0.0) for tipo, bfield in VALOR_FIELDS.items()}
        comandos.append(migrador.command("crm.deal.update", fields=fields, extra={"id": did}))
    print(f"updates a enviar: {len(comandos)}", flush=True)

    if not comandos:
        print("Nenhum update.", flush=True)
        return

    res, errs = migrador.batch(comandos, batch_size=20, chunk_sleep=30)
    ok = sum(1 for e in errs if not e)
    erros = len(comandos) - ok
    print(f"atualizados={ok} erros={erros}", flush=True)
    for cmd, e in zip(comandos, errs):
        if e:
            print(f"  ERRO deal {cmd[1].get('id')}: {e.get('error_description') or e.get('error')}", flush=True)


if __name__ == "__main__":
    main()
