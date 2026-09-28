import json
import os

from oracle_client import OracleClient
from bitrix_client import BitrixClient
from config import OBJETOS, ENTIDADE_ITEM, QUERY_FILTRO

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def coletar_describes():
    o = OracleClient()
    b = BitrixClient()
    ensure_dir()

    oracle_describes = {}
    bitrix_fields = {}

    for oracle_obj, bitrix_ent in OBJETOS:
        print(f"[ORACLE] describe {oracle_obj} ...")
        d = o.describe(oracle_obj)
        oracle_describes[oracle_obj] = d
        with open(os.path.join(DATA_DIR, f"oracle_{oracle_obj}_describe.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

        print(f"[BITRIX] fields {bitrix_ent} ...")
        bf = b.fields(bitrix_ent)
        bitrix_fields[bitrix_ent] = bf
        with open(os.path.join(DATA_DIR, f"bitrix_{bitrix_ent}_fields.json"), "w", encoding="utf-8") as f:
            json.dump(bf, f, ensure_ascii=False, indent=2)

    sl = b.status_list()
    bitrix_fields["__status_list__"] = sl
    with open(os.path.join(DATA_DIR, "bitrix_status_list.json"), "w", encoding="utf-8") as f:
        json.dump(sl, f, ensure_ascii=False, indent=2)

    print("describes OK")


def coletar_amostras(n=10):
    o = OracleClient()
    ensure_dir()
    for oracle_obj, bitrix_ent in OBJETOS:
        print(f"[ORACLE] amostra {oracle_obj} ...")
        try:
            data = o.registros(oracle_obj, limit=n, q=QUERY_FILTRO.get(oracle_obj))
        except Exception as e:
            print(f"  !! erro {oracle_obj}: {e}")
            continue
        with open(os.path.join(DATA_DIR, f"amostra_{oracle_obj}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        items = data.get("items", [])
        print(f"  -> {len(items)} registros")
    print("amostras OK")


if __name__ == "__main__":
    coletar_describes()
    coletar_amostras(10)
