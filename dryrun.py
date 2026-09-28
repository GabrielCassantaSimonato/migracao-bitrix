import json
import os

from config import OBJETOS, CAMPO_ID_ORACLE
import mapeamento

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def gerar_relatorio():
    relatorio = {}
    for oracle_obj, bitrix_ent in OBJETOS:
        try:
            amostra = load(f"amostra_{oracle_obj}.json")
        except FileNotFoundError:
            relatorio[oracle_obj] = {"erro": "sem amostra"}
            continue

        items = amostra.get("items", [])
        pk = mapeamento.PK_ORACLE.get(oracle_obj, "?")
        regs = []
        for rec in items:
            payload, extras = mapeamento.montar_payload(oracle_obj, bitrix_ent, rec)
            regs.append({
                "pk_oracle": rec.get(pk),
                "payload": payload,
                "extras": extras,
            })
        relatorio[oracle_obj] = {
            "bitrix_entidade": bitrix_ent,
            "campo_id_bitrix": CAMPO_ID_ORACLE.get(bitrix_ent),
            "total_registros": len(items),
            "registros": regs,
        }

    with open(os.path.join(DATA_DIR, "dryrun_relatorio.json"), "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=2)

    # resumo legivel
    print("=" * 70)
    print("DRY-RUN — resumo")
    print("=" * 70)
    for oracle_obj, info in relatorio.items():
        if "erro" in info:
            print(f"\n[{oracle_obj}] ERRO: {info['erro']}")
            continue
        print(f"\n[{oracle_obj}] -> {info['bitrix_entidade']} | id bitrix={info['campo_id_bitrix']} | {info['total_registros']} registros")
        for r in info["registros"][:2]:
            print(f"  pk={r['pk_oracle']} payload={json.dumps(r['payload'], ensure_ascii=False)}")
        if info["total_registros"] > 2:
            print(f"  ... +{info['total_registros'] - 2} registros")

    print("\nRelatório completo salvo em data/dryrun_relatorio.json")


if __name__ == "__main__":
    gerar_relatorio()
