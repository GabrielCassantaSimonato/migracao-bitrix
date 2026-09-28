import unicodedata

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, ENTIDADE_ITEM, CAMPO_ID_ORACLE
from oracle_client import OracleClient
from de_para_valores import build_value_mapping
import mapeamento
import migrador

WB = BITRIX_WEBHOOK.rstrip("/")
EID = ENTIDADE_ITEM["item1212"]
ID_FIELD = CAMPO_ID_ORACLE["item1212"]  # ufCrm77_1789386203592

CAMPO_NUMERO_PARCELA = "ufCrm77_1779974666870"     # Número da parcela (double)
CAMPO_TIPO_OPORTUNIDADE = "ufCrm77_1779974764693"  # Tipo de oportunidade (enum)
CAMPO_PORCENTAGEM = "ufCrm77_1779974787847"        # Porcentagem da parcela (string)
CAMPO_DATA_FECHAMENTO = "ufCrm77_1779974811337"    # Data de fechamento (date)
CAMPO_DATA_FATURAMENTO = "ufCrm77_1779974822842"   # Data de faturamento (date)
CAMPO_MESES = "ufCrm77_1779974836736"              # Meses de faturamento (double)
CAMPO_DESCRICAO = "ufCrm77_1779974849348"          # Descrição da parcela (string)
CAMPO_PRORATA = "ufCrm77_1779974860433"            # Pró-rata (boolean)
CAMPO_DIVISAO = "ufCrm77_1780083021479"            # Divisão de parcelas (em vezes) (enum)

# DivisaoDasParcelas_c (Oracle) -> itens do enum "Divisão de parcelas (em vezes)"
DIVISAO_MAP = {
    "2X": "7631", "3X": "7633", "4X": "7635", "5X": "7637", "6X": "7639",
    "7X": "7641", "8X": "7643", "9X": "7645", "10X": "7647", "11X": "7649", "12X": "7651",
}

def resolver_proprietario(nome):
    return mapeamento.resolver_usuario(None, nome)


def formatar_porcentagem(v):
    try:
        val = float(v) * 100
        if val == int(val):
            return str(int(val))
        return f"{val:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


def carregar_mapa_entidade(metodo, campo_oracle):
    mapa = {}
    start = 0
    while True:
        r = requests.get(f"{WB}/{metodo}", params=[
            ("select[]", "ID"), ("select[]", campo_oracle), ("start", start)], timeout=120)
        res = r.json().get("result", [])
        for c in res:
            v = c.get(campo_oracle)
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(c["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def montar_payload(rec, o, val_map, deals):
    p = {}
    p["title"] = rec.get("RecordName") or f"Parcela {rec.get('RecordNumber')}"
    p[ID_FIELD] = str(rec.get("Id"))

    # oportunidade (Oportunidade_Id_c -> parentId2)
    if rec.get("Oportunidade_Id_c"):
        did = deals.get(str(rec.get("Oportunidade_Id_c")))
        if did:
            p["parentId2"] = did

    # proprietario (Proprietario_c -> assignedById)
    prop = rec.get("Proprietario_c")
    if prop:
        owner = resolver_proprietario(prop)
        if owner:
            p["assignedById"] = owner
        else:
            print(f"    !! proprietario nao encontrado: {prop!r}")

    # numero da parcela (obrigatorio)
    if rec.get("NumeroDaParcela_c") is not None and rec.get("NumeroDaParcela_c") != "":
        p[CAMPO_NUMERO_PARCELA] = str(rec["NumeroDaParcela_c"])

    # tipo de oportunidade (enum, via build_value_mapping)
    if rec.get("TipoDaOportunidade_c"):
        p[CAMPO_TIPO_OPORTUNIDADE] = val_map["ufCrm77_1779974764693"]["de_para"].get(
            str(rec["TipoDaOportunidade_c"]), str(rec["TipoDaOportunidade_c"]))

    # meses de faturamento (obrigatorio)
    if rec.get("Mes_c") is not None and rec.get("Mes_c") != "":
        p[CAMPO_MESES] = str(rec["Mes_c"])

    # porcentagem da parcela (string; valor bruto, ex. 1 -> "100")
    if rec.get("PorcentagemDaParcela_c") is not None and rec.get("PorcentagemDaParcela_c") != "":
        p[CAMPO_PORCENTAGEM] = formatar_porcentagem(rec["PorcentagemDaParcela_c"])

    # valor da parcela -> opportunity + currency
    if rec.get("ValorDaParcela_c") is not None:
        p["opportunity"] = str(rec["ValorDaParcela_c"])
        p["currencyId"] = rec.get("CurrencyCode") or "BRL"

    # datas
    if rec.get("DataDeFechamento_c"):
        p[CAMPO_DATA_FECHAMENTO] = mapeamento.normalizar_data(rec["DataDeFechamento_c"])
    if rec.get("DataDeVencimento_c"):
        p[CAMPO_DATA_FATURAMENTO] = mapeamento.normalizar_data(rec["DataDeVencimento_c"])

    # divisao de parcelas (enum)
    if rec.get("DivisaoDasParcelas_c"):
        p[CAMPO_DIVISAO] = DIVISAO_MAP.get(str(rec["DivisaoDasParcelas_c"]), str(rec["DivisaoDasParcelas_c"]))

    # pro-rata (boolean)
    p[CAMPO_PRORATA] = "Y" if rec.get("ProRata_c") in (True, "true", "True", "Y", "y", 1, "1") else "N"

    # descricao da parcela (CLOB via enclosure)
    if rec.get("Id"):
        desc = o.enclosure("Parcelas_c", rec["Id"], "DescricaoDaParcela_c")
        if desc:
            p[CAMPO_DESCRICAO] = desc.strip()

    return p


def main():
    o = OracleClient()
    val_map = build_value_mapping("Parcelas_c", "item1212")

    deals = carregar_mapa_entidade("crm.deal.list", CAMPO_ID_ORACLE["deal"])
    print(f"deals={len(deals)}")

    dados = o.registros_todos("Parcelas_c")
    print(f"{len(dados)} parcelas no Oracle")

    existentes = migrador.carregar_existentes_item("item1212")
    print(f"  {len(existentes)} ja existentes")

    alvos = []
    pulados = 0
    for rec in dados:
        if rec.get("Id") is None:
            continue
        alvos.append(rec)

    comandos = []
    metas = []
    for rec in alvos:
        pid = rec.get("Id")
        payload = montar_payload(rec, o, val_map, deals)
        existing = existentes.get(str(pid))
        if existing:
            comandos.append(migrador.command("crm.item.update", fields=payload, extra={"entityTypeId": EID, "id": existing}))
            metas.append((pid, rec.get("RecordName"), "updated"))
        else:
            comandos.append(migrador.command("crm.item.add", fields=payload, extra={"entityTypeId": EID}))
            metas.append((pid, rec.get("RecordName"), "created"))

    criados = atualizados = erros = 0
    res, errs = migrador.batch(comandos)
    for (pid, name, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {pid} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"criados={criados} atualizados={atualizados} erros={erros}")


if __name__ == "__main__":
    main()
