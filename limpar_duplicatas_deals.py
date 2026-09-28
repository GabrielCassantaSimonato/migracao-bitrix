import json
import os
from collections import defaultdict

import migrador

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CAMPO = "ORIGIN_ID"


def coletar_ids():
    """{oracle_id: [bitrix_id, ...]} de todos os deals ORIGINATOR_ID=oracle."""
    mapa = defaultdict(list)
    start = 0
    while True:
        r = migrador._get("crm.deal.list", params=[
            ("filter[ORIGINATOR_ID]", "oracle"),
            ("select[]", "ID"),
            ("select[]", CAMPO),
            ("start", start),
        ])
        res = r.get("result", [])
        for c in res:
            oid = c.get(CAMPO)
            if oid:
                mapa[str(oid)].append(int(c["ID"]))
        if len(res) < 50:
            break
        start += 50
    return mapa


def main():
    print("Coletando deals (paginacao)...", flush=True)
    mapa = coletar_ids()
    total = sum(len(v) for v in mapa.values())
    unicos = len(mapa)
    print(f"deals={total} opps_unicas={unicos}", flush=True)

    dups = {k: sorted(v) for k, v in mapa.items() if len(v) > 1}
    a_apagar = [i for v in dups.values() for i in v[1:]]
    print(f"opps com duplicata={len(dups)} | deals a apagar={len(a_apagar)}", flush=True)

    if not a_apagar:
        print("Nada a apagar.", flush=True)
        return

    backup = {
        "mantidos": {k: v[0] for k, v in dups.items()},
        "a_apagar": a_apagar,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "duplicatas_deals_deletadas.json"), "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    print(f"backup salvo em data/duplicatas_deals_deletadas.json", flush=True)

    comandos = [migrador.command("crm.deal.delete", extra={"id": i}) for i in a_apagar]
    res, errs = migrador.batch(comandos)
    ok = sum(1 for e in errs if not e)
    print(f"deletados={ok} erros={len(a_apagar) - ok}", flush=True)
    for i, e in zip(a_apagar, errs):
        if e:
            print(f"  ERRO delete deal {i}: {e.get('error_description') or e.get('error')}", flush=True)


if __name__ == "__main__":
    main()
