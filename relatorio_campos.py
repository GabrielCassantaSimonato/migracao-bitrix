import json
import os
import re

from config import OBJETOS

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campos_para_revisar.txt")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    dp = load("de_para_completo.json")
    linhas = []

    for oracle_obj, bitrix_ent in OBJETOS:
        bf = load(f"bitrix_{bitrix_ent}_fields.json")
        campos = dp[oracle_obj]["campos"]

        # mapa: bitrix -> lista de oracle mapeados
        mapeados_por_bitrix = {}
        for c in campos:
            if c["bitrix"]:
                mapeados_por_bitrix.setdefault(c["bitrix"], []).append(c["oracle"])

        linhas.append("=" * 78)
        linhas.append(f"{oracle_obj.upper()}  ->  {bitrix_ent}  (campo ID: {json.load(open(os.path.join(DATA_DIR, 'de_para_completo.json'), encoding='utf-8')) and ''})")
        linhas.append("=" * 78)

        # Campos custom do Bitrix (só os que têm rótulo legível)
        custom = {}
        for k, v in bf.items():
            if k.startswith("UF_"):
                if v.get("listLabel") or v.get("formLabel") or v.get("filterLabel"):
                    custom[k] = v
            elif re.match(r"^ufCrm\d+_", k):
                custom[k] = v
        linhas.append("\n[CAMPOS CUSTOM DO BITRIX (com rótulo)]")
        for code in sorted(custom, key=lambda x: ((custom[x].get("listLabel") or custom[x].get("title") or "")).lower()):
            meta = custom[code]
            label = meta.get("listLabel") or meta.get("formLabel") or meta.get("title") or code
            oracle = ", ".join(mapeados_por_bitrix.get(code, []))
            tipo = meta.get("type", "")
            linhas.append(f"  - {label:48s} [{tipo:12s}]  <- {oracle or '(sem correspondência)'}")

        # Campos Oracle _c sem correspondência
        nao_mapeados_custom = [c for c in campos if c["oracle"].endswith("_c") and not c["bitrix"]]
        if nao_mapeados_custom:
            linhas.append("\n[ORACLE CUSTOM (_c) SEM CORRESPONDÊNCIA NO BITRIX]")
            for c in nao_mapeados_custom:
                linhas.append(f"  - {c['oracle']:48s} (título: {c['titulo_oracle']})")

        linhas.append("")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    print("Relatório salvo em:", OUT)


if __name__ == "__main__":
    main()
