import json
import os
import re
import unicodedata

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import ORACLE_USER, ORACLE_PASS, ORACLE_CRM_BASE, ORACLE_FSCM_BASE

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def norm_loose(s):
    return re.sub(r"\s+", "", norm(s))


def strip_prefix(s):
    return re.sub(r"^[A-Za-z0-9]{1,6}\s*[-–—]\s*", "", str(s))


def oracle_get(path, params=None):
    r = requests.get(ORACLE_CRM_BASE + path, auth=(ORACLE_USER, ORACLE_PASS), verify=False, timeout=120, params=params)
    r.raise_for_status()
    return r.json()


# Campos cuja LOV vive em FSCM standardLookups (não em recurso VO do CRM).
LOV_FSCM = {
    "OrganizationDEO_Segmento_c": "SEGMENTO",
    "SalesProfileStatus": "ZCA_SALES_ACCOUNT_STATUS",
    "JobTitleCode": "RESPONSIBILITY",
    "PrincipalMotivoDeEncerramento_c": "PRINCIAL_MOTIVO_ENCERRAMENTO",
    "MotivoSecundario_c": "PRINCIAL_MOTIVO_ENCERRAMENTO",
}

# De-para MANUAL de valores (código Oracle -> ID do item Bitrix), para casos onde o
# dado real não bate com a LOV (variantes de código).
VALORES_MANUAIS = {
    "UF_CRM_1779909582": {  # Origem do lead
        "GERENTE_DE_NEGOCIOS": "7307",  # Gerente de contas
    },
    "UF_CRM_1779736957": {  # Motivos da rejeição (lead) - rótulos Bitrix têm sufixo
        "EMPRESA_FORA_VALOR": "6517",
        "FORA_PUBLICO_ALVO": "6519",
        "NECESSIDADE_FORA_ESCOPO": "6521",
        "RECUSOU_OUVIR": "6523",
        "SONDANDO_COMPRA_FUTURA": "6525",
    },
    "ufCrm79_1779884392": {  # Pré-venda (item1216) - Carlos Roberto saiu, não está mais na LOV
        "CARLOS_ROBERTO": "13383",
    },
}


def oracle_lov_fscm(lookup_name):
    path = f"/standardLookups/{lookup_name}/child/lookupCodes"
    r = requests.get(ORACLE_FSCM_BASE + path, auth=(ORACLE_USER, ORACLE_PASS), verify=False, timeout=120,
                     params={"limit": 500, "onlyData": "true"})
    r.raise_for_status()
    out = []
    for i in r.json().get("items", []):
        code = i.get("LookupCode")
        meaning = i.get("Meaning")
        if meaning:
            out.append({"code": code, "meaning": meaning})
    return out


def oracle_lov_values(field_attr):
    lov = field_attr.get("lov") or {}
    resources = lov.get("lovResourcePath", [])
    if not resources:
        return []
    for res in resources:
        name = res.get("resource")
        filtro = res.get("filter", "")
        path = f"/{name}"
        params = {"limit": 500, "onlyData": "true"}
        try:
            if filtro:
                d = oracle_get(path + filtro)
            else:
                d = oracle_get(path, params=params)
            items = d.get("items", [])
            if items:
                out = []
                for i in items:
                    code = i.get("LookupCode") or i.get("Meaning")
                    meaning = i.get("Meaning") or i.get("LookupCode")
                    if meaning:
                        out.append({"code": code, "meaning": meaning})
                return out
        except Exception:
            continue
    return []


def bitrix_items(bfield):
    meta = bfield
    items = meta.get("items", [])
    return [{"id": str(i.get("ID")), "value": i.get("VALUE")} for i in items]


def build_value_mapping(oracle_obj, bitrix_ent):
    describe = load(f"oracle_{oracle_obj}_describe.json")
    bfields = load(f"bitrix_{bitrix_ent}_fields.json")
    dp = load("de_para_completo.json")[oracle_obj]["campos"]

    attrs = {a["name"]: a for a in describe["Resources"][oracle_obj]["attributes"]}
    mapping = {}
    for c in dp:
        if not c["bitrix"] or c["confianca"] not in ("Alto", "Baixo"):
            continue
        bmeta = bfields.get(c["bitrix"], {})
        if bmeta.get("type") != "enumeration":
            continue
        oattr = attrs.get(c["oracle"])
        if not oattr:
            continue
        if c["oracle"] in LOV_FSCM:
            ovals = oracle_lov_fscm(LOV_FSCM[c["oracle"]])
        else:
            ovals = oracle_lov_values(oattr)
        if not ovals:
            continue
        bitems = bitrix_items(bmeta)
        if not bitems:
            continue
        pairs = {}
        by_value_norm = {norm(b["value"]): b["id"] for b in bitems}
        by_value_loose = {norm_loose(b["value"]): b["id"] for b in bitems}
        for o in ovals:
            for cand in (o["meaning"], strip_prefix(o["meaning"])):
                m = norm(cand)
                if m in by_value_norm:
                    pairs[o["code"]] = by_value_norm[m]
                    break
                m2 = norm_loose(cand)
                if m2 in by_value_loose:
                    pairs[o["code"]] = by_value_loose[m2]
                    break
        if pairs:
            mapping[c["bitrix"]] = {
                "oracle_field": c["oracle"],
                "titulo": c["titulo_oracle"],
                "label_bitrix": bmeta.get("listLabel") or bmeta.get("title"),
                "de_para": pairs,
            }
    # mescla de-para manuais (variantes de código não cobertas pela LOV)
    for bfield, manual in VALORES_MANUAIS.items():
        if bfield in mapping:
            mapping[bfield]["de_para"].update(manual)
        else:
            mapping[bfield] = {
                "oracle_field": None,
                "titulo": None,
                "label_bitrix": (bfields.get(bfield) or {}).get("listLabel") or bfield,
                "de_para": dict(manual),
            }
    return mapping


def main():
    mapping = build_value_mapping("opportunities", "deal")
    save("de_para_valores_opportunities.json", mapping)
    print("=" * 70)
    print("DE-PARA DE VALORES (opportunities -> deal)")
    print("=" * 70)
    for bfield, m in mapping.items():
        print(f"\n[{bfield}] {m['label_bitrix']}  <-  {m['oracle_field']}  ({len(m['de_para'])} valores)")
        for code, val in list(m["de_para"].items())[:6]:
            print(f"    {code} -> {val}")
        if len(m["de_para"]) > 6:
            print(f"    ... +{len(m['de_para'])-6}")
    print(f"\nTotal de campos enum com de-para: {len(mapping)}")


if __name__ == "__main__":
    main()
