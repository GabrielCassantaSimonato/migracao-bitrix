import sys

from config import USUARIO_DESATIVADO
import migrador
from oracle_client import OracleClient

VALOR_TOTAL = "UF_CRM_1786131567648"
OWNERS_ERRADOS = {"Ronaldo Rodrigues", "Abreu"}


def parse_shard(argv):
    """Lê --shard <i> <N> da linha de comando; retorna (i, N) ou (None, None)."""
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


def parse_valor(v):
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if v in (None, ""):
        return None
    s = str(v).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def batch_find_deals(o_ids):
    """{oid: {'id', 'opp', 'owner'}} via batch crm.deal.list (ORIGINATOR_ID=oracle + ORIGIN_ID)."""
    comandos = [
        ("crm.deal.list", {
            "filter[ORIGINATOR_ID]": "oracle",
            "filter[ORIGIN_ID]": oid,
            "select[]": ["ID", "OPPORTUNITY", "ASSIGNED_BY_ID"],
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
            d = items[0]
            out[oid] = {
                "id": d["ID"],
                "opp": d.get("OPPORTUNITY"),
                "owner": d.get("ASSIGNED_BY_ID"),
            }
    return out


def main():
    shard, shards = parse_shard(sys.argv)

    o = OracleClient()
    todas = o.registros_todos(
        "opportunities", q="OptyId>0",
        fields=["OptyId", "Revenue", "ExpectAmount", "PartyName1"],
    )
    print(f"oportunidades no Oracle: {len(todas)}", flush=True)

    afetados = {}
    n_owner = 0
    n_valor = 0
    for rec in todas:
        oid = rec.get("OptyId")
        if oid is None:
            continue
        d = {}
        nome = rec.get("PartyName1") or ""
        if nome in OWNERS_ERRADOS:
            d["ASSIGNED_BY_ID"] = USUARIO_DESATIVADO
            n_owner += 1
        rev = parse_valor(rec.get("Revenue"))
        exp = parse_valor(rec.get("ExpectAmount")) or 0
        if rev and rev > 0 and abs(rev - exp) > 0.01:
            d["OPPORTUNITY"] = rev
            d[VALOR_TOTAL] = rev
            n_valor += 1
        if d:
            afetados[str(oid)] = d

    print(f"proprietarios a corrigir: {n_owner} | valores a corrigir: {n_valor}", flush=True)
    print(f"opps afetadas (unicas, total): {len(afetados)}", flush=True)

    # sharding determinístico por OptyId (sem sobreposição entre webhooks)
    if shard is not None:
        oids = sorted(afetados.keys(), key=int)
        oids = migrador.shard_items(oids, shard, shards)
        afetados = {oid: afetados[oid] for oid in oids}
        print(f"shard {shard}/{shards}: {len(afetados)} opps", flush=True)

    if not afetados:
        print("Nada a atualizar.", flush=True)
        return

    deal_ids = batch_find_deals(list(afetados.keys()))
    print(f"deals resolvidos: {len(deal_ids)}", flush=True)

    comandos = []
    sem_deal = 0
    ja_ok = 0
    for oid, d in afetados.items():
        info = deal_ids.get(oid)
        if not info:
            sem_deal += 1
            continue
        # skip idempotente: já está correto?
        correto = True
        if "OPPORTUNITY" in d:
            try:
                cur = float(info["opp"] or 0)
            except (TypeError, ValueError):
                cur = 0.0
            if abs(cur - d["OPPORTUNITY"]) >= 0.01:
                correto = False
        if "ASSIGNED_BY_ID" in d and str(info["owner"]) != str(d["ASSIGNED_BY_ID"]):
            correto = False
        if correto:
            ja_ok += 1
            continue
        comandos.append(migrador.command("crm.deal.update", fields=d, extra={"id": info["id"]}))
    print(f"opps sem deal: {sem_deal} | ja corretos (skip): {ja_ok}", flush=True)
    print(f"updates a enviar: {len(comandos)}", flush=True)

    if not comandos:
        print("Nenhum update a enviar.", flush=True)
        return

    res, errs = migrador.batch(comandos, batch_size=20)
    ok = sum(1 for e in errs if not e)
    erros = len(comandos) - ok
    print(f"atualizados={ok} erros={erros}", flush=True)
    for cmd, e in zip(comandos, errs):
        if e:
            print(f"  ERRO deal {cmd[1].get('id')}: {e.get('error_description') or e.get('error')}", flush=True)


if __name__ == "__main__":
    main()
