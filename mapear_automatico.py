import json
import os
import re
import unicodedata
from difflib import SequenceMatcher

import mapeamento
from config import OBJETOS

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def norm_compact(s):
    return norm(s).replace(" ", "")


def strip_field(s):
    s = str(s)
    s = re.sub(r"_c$", "", s)
    s = re.sub(r"_?id_?c?$", "", s, flags=re.I)
    s = re.sub(r"_?id$", "", s, flags=re.I)
    s = re.sub(r"lst$", "", s, flags=re.I)
    return norm(s)


def tokens(s):
    return set(norm(s).split())


def match_score(oracle_name, oracle_title, bitrix_label):
    best = 0.0
    for o in (oracle_name, oracle_title):
        if not o:
            continue
        oc = norm_compact(strip_field(o))
        lc = norm_compact(strip_field(bitrix_label))
        if not oc or not lc:
            continue
        if oc == lc:
            return 1.0
        if len(oc) < 4 or len(lc) < 4:
            continue
        seq = SequenceMatcher(None, oc, lc).ratio()
        t1, t2 = tokens(strip_field(o)), tokens(strip_field(bitrix_label))
        jac = (len(t1 & t2) / len(t1 | t2)) if (t1 and t2) else 0.0
        best = max(best, seq, jac)
    return best


def bitrix_candidates(ent):
    bf = load(f"bitrix_{ent}_fields.json")
    custom, standard = {}, {}
    for code, meta in bf.items():
        if code.startswith("UF_"):
            label = meta.get("listLabel") or meta.get("formLabel") or meta.get("filterLabel")
            if label:
                custom[code] = {"label": label, "type": meta.get("type")}
        elif re.match(r"^ufCrm\d+_", code):
            label = meta.get("title") or meta.get("listLabel")
            if label:
                custom[code] = {"label": label, "type": meta.get("type")}
        else:
            standard[code] = {"label": meta.get("title") or code, "type": meta.get("type")}
    return custom, standard


def oracle_fields(obj):
    d = load(f"oracle_{obj}_describe.json")
    return d["Resources"][obj]["attributes"]


def infer_transform(a):
    t = a.get("type", "")
    at = (a.get("properties") or {}).get("AttributeType") or a.get("controlType") or ""
    if t == "date":
        return "date"
    if t == "datetime":
        return "datetime"
    if at in ("Lookup", "ForeignKey"):
        return "fk"
    return None


def is_custom(name):
    return name.endswith("_c")


def build_full_de_para():
    manual = mapeamento.DE_PARA
    result = {}
    stats = {}
    for oracle_obj, bitrix_ent in OBJETOS:
        attrs = oracle_fields(oracle_obj)
        custom_cand, std_cand = bitrix_candidates(bitrix_ent)
        manual_map = {d[0]: (d[1], d[2]) for d in manual.get(oracle_obj, [])}

        rows = []
        n_map = n_rev = n_nao = 0
        for a in attrs:
            nome = a.get("name")
            title = a.get("title", "")
            if nome in manual_map:
                bcampo, transf = manual_map[nome]
                rows.append({"oracle": nome, "titulo_oracle": title, "bitrix": bcampo,
                             "tipo_oracle": a.get("type"), "transform": transf,
                             "origem": "manual", "confianca": "Alto"})
                n_map += 1
                continue

            if is_custom(nome):
                cands = custom_cand
            else:
                cands = {}  # campos padrão Oracle: só via mapeamento manual (evita falsos positivos)

            best, best_score, best_label = None, 0.0, ""
            for code, c in cands.items():
                s = match_score(nome, title, c["label"])
                if s > best_score:
                    best_score, best, best_label = s, code, c["label"]

            if best and best_score >= 0.85:
                rows.append({"oracle": nome, "titulo_oracle": title, "bitrix": best,
                             "tipo_oracle": a.get("type"), "transform": infer_transform(a),
                             "origem": "auto", "confianca": "Alto",
                             "label_bitrix": best_label})
                n_map += 1
            elif best and best_score >= 0.6:
                rows.append({"oracle": nome, "titulo_oracle": title, "bitrix": best,
                             "tipo_oracle": a.get("type"), "transform": infer_transform(a),
                             "origem": "auto", "confianca": "Baixo",
                             "score": round(best_score, 2), "label_bitrix": best_label})
                n_rev += 1
            else:
                rows.append({"oracle": nome, "titulo_oracle": title, "bitrix": "",
                             "tipo_oracle": a.get("type"), "transform": "",
                             "origem": "", "confianca": "Nao mapeado"})
                n_nao += 1

        result[oracle_obj] = {"bitrix": bitrix_ent, "campos": rows}
        stats[oracle_obj] = {"mapeado": n_map, "revisar": n_rev, "nao_mapeado": n_nao, "total": len(attrs)}

    return result, stats


def main():
    result, stats = build_full_de_para()
    with open(os.path.join(DATA_DIR, "de_para_completo.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("=" * 72)
    print("RESULTADO DO MAPEAMENTO AUTOMÁTICO (v2)")
    print("=" * 72)
    total_map = total_rev = total_nao = 0
    for obj, s in stats.items():
        total_map += s["mapeado"]; total_rev += s["revisar"]; total_nao += s["nao_mapeado"]
        print(f"{obj:20s} -> {s['mapeado']:4d} mapeados | {s['revisar']:4d} a revisar | {s['nao_mapeado']:4d} nao mapeados | {s['total']} total")
    print(f"\nTOTAL: {total_map} mapeados | {total_rev} a revisar | {total_nao} nao mapeados")
    print("Salvo em data/de_para_completo.json")


if __name__ == "__main__":
    main()
