import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, ENTIDADE_ITEM, CAMPO_ID_ORACLE, ORACLE_CRM_BASE, ORACLE_USER, ORACLE_PASS
from oracle_client import OracleClient
from de_para_valores import build_value_mapping, norm, norm_loose
import mapeamento
import migrador

WB = BITRIX_WEBHOOK.rstrip("/")
EID = ENTIDADE_ITEM["item1208"]
ID_FIELD = CAMPO_ID_ORACLE["item1208"]  # ufCrm75_1789386179872

CAMPO_TIPO_CONTA = "ufCrm75_1779970913049"
CAMPO_ERP = "ufCrm75_1779970926200"
CAMPO_RANGE = "ufCrm75_1779970938407"
CAMPO_VERSAO = "ufCrm75_1779970950451"
CAMPO_CARTEIRA = "ufCrm75_1779970959212"
CAMPO_PROBABILIDADE = "ufCrm75_1779971005403"
CAMPO_DATA_FECHAMENTO = "ufCrm75_1779971020841"
CAMPO_DATA_FECH_PARCEIRO = "ufCrm75_1779971035973"
CAMPO_DATA_INICIO_PROJETO = "ufCrm75_1786458496"
CAMPO_HORAS_GP = "ufCrm75_1779972442005"
CAMPO_HORAS_CT = "ufCrm75_1779972460375"
CAMPO_HORAS_CF = "ufCrm75_1779972473116"
CAMPO_HORAS_AF = "ufCrm75_1779972484971"
CAMPO_HORAS_TOTAIS = "ufCrm75_1779972499383"
CAMPO_DURACAO = "ufCrm75_1779972551406"
CAMPO_EQUIPE = "ufCrm75_1779995255448"
CAMPO_OBSERVACAO = "ufCrm75_1779971360229"
CAMPO_SOLUCAO_ATUAL = "ufCrm75_1779972229"   # enum (sem título) = "Solução atual"
CAMPO_CONTATO_PRINCIPAL = "ufCrm75_1789733976970"

# Códigos combinados de Solucao_c que não têm item próprio no campo "Solução" do Bitrix
# (a LOV tem "Synchro4Me/4DFe" e "Synchro4TDF/4DFe"; o enum do item1208 não).
SOLUCAO_EXTRA = {
    "SYNCHRO4ME_4DFE": "7413",   # Synchro4Me
    "SYNCHRO4TDF_4DFE": "7415",  # Synchro4TDF
}

# Status_c (Oracle) -> stageId do item1208 (DYNAMIC_1208_STAGE_105)
STATUS_STAGE_MAP = {
    "CLIENTE_NAO_QUER_ATUALIZAR": "DT1208_105:NEW",
    "CLIENTE_VERSAO_GARANTIA": "DT1208_105:PREPARATION",
    "CONTA_ABORDAR": "DT1208_105:CLIENT",
    "AGUARDANDO_RETORNO": "DT1208_105:UC_ZWK0M3",
    "EXCLIENTE_BASEHISTORICA": "DT1208_105:UC_U35HFZ",
    "GANHA_SYNCHRO": "DT1208_105:UC_WC0B3W",
    "GANHA_PARCEIRO": "DT1208_105:UC_CKPC9J",
    "IMPLEMENTACAO_PROPRIA": "DT1208_105:UC_VTO0TA",
    "OPORTUNIDADE_ANDAMENTO": "DT1208_105:UC_6QSEXW",
    "OPORTUNIDADE_ANDAMENTOPARCEIRO": "DT1208_105:UC_KVLVF4",
    "OPORTUNIDADE_PERDIDA": "DT1208_105:UC_IQSWYG",
    "OPTOU_CONCORRENTE": "DT1208_105:UC_LYJB6G",
    "JA_UTILIZA_SYNCHRO": "DT1208_105:UC_UOWR35",
}

def resolver_proprietario(nome):
    return mapeamento.resolver_usuario(None, nome)


def carregar_mapa_entidade(metodo, campo_oracle):
    mapa = {}
    start = 0
    while True:
        r = migrador._get(metodo, params=[
            ("select[]", "ID"), ("select[]", campo_oracle), ("start", start)])
        res = r.get("result", [])
        for c in res:
            v = c.get(campo_oracle)
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(c["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def carregar_mapa_item(eid, campo_oracle):
    mapa = {}
    start = 0
    while True:
        r = migrador._get("crm.item.list", params=[
            ("entityTypeId", eid), ("select[]", "id"), ("select[]", campo_oracle), ("start", start)])
        items = (r.get("result") or {}).get("items", [])
        for it in items:
            v = it.get(campo_oracle)
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(it["id"])
        if len(items) < 50:
            break
        start += 50
    return mapa


def build_solucao_atual_map():
    """De-para código Oracle (SolucaoAtual_c) -> ID do item Bitrix (ufCrm75_1779972229)."""
    r = migrador._get("crm.item.fields", params=[("entityTypeId", EID)])
    fields = (r.get("result") or {}).get("fields", {})
    bfield = fields.get(CAMPO_SOLUCAO_ATUAL, {})
    bitems = [{"id": str(i.get("ID")), "value": i.get("VALUE")} for i in bfield.get("items", [])]

    r2 = requests.get(f"{ORACLE_CRM_BASE}/CampanhasSynchro_LOV_For_SolucaoAtual_c",
                      auth=(ORACLE_USER, ORACLE_PASS), verify=False,
                      params={"limit": 500, "onlyData": "true"}, timeout=120)
    r2.raise_for_status()
    ovals = [{"code": i.get("LookupCode"), "meaning": i.get("Meaning")}
             for i in r2.json().get("items", [])]

    by_value_norm = {norm(b["value"]): b["id"] for b in bitems}
    by_value_loose = {norm_loose(b["value"]): b["id"] for b in bitems}
    mapa = {}
    for o in ovals:
        for cand in (o["meaning"],):
            m = norm(cand)
            if m in by_value_norm:
                mapa[o["code"]] = by_value_norm[m]
                break
            m2 = norm_loose(cand)
            if m2 in by_value_loose:
                mapa[o["code"]] = by_value_loose[m2]
                break
    return mapa


def buscar_observacoes(records, o, max_workers=8):
    """Pré-busca os CLOBs Observacao_c de uma lista de registros (paralelo)."""
    obs = {}

    def _get(rid):
        try:
            v = o.enclosure("CampanhasSynchro_c", rid, "Observacao_c")
            return rid, (v.strip() if v else None)
        except Exception:
            return rid, None

    ids = [r["Id"] for r in records if r.get("Id")]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_get, rid): rid for rid in ids}
        for f in as_completed(futs):
            rid, val = f.result()
            if val:
                obs[str(rid)] = val
    return obs


def montar_payload(rec, observacoes, val_map, solucao_atual_map, empresas, deals, parceiros, campanhas):
    p = {}
    p["title"] = rec.get("RecordName") or f"Campanha Synchro {rec.get('RecordNumber')}"
    p[ID_FIELD] = str(rec.get("Id"))

    # empresa (Conta_Id_c -> companyId)
    if rec.get("Conta_Id_c"):
        cid = empresas.get(str(rec.get("Conta_Id_c")))
        if cid:
            p["companyId"] = cid

    # negocio (Oportunidade_Id_c -> parentId2)
    if rec.get("Oportunidade_Id_c"):
        did = deals.get(str(rec.get("Oportunidade_Id_c")))
        if did:
            p["parentId2"] = did

    # criar campanha (Campanha_Id_c -> parentId1204)
    if rec.get("Campanha_Id_c"):
        cpid = campanhas.get(str(rec.get("Campanha_Id_c")))
        if cpid:
            p["parentId1204"] = cpid

    # parceiro (Parceiro_Id_c -> parentId1196)
    if rec.get("Parceiro_Id_c"):
        pid = parceiros.get(str(rec.get("Parceiro_Id_c")))
        if pid:
            p["parentId1196"] = pid

    # proprietario (Proprietario_c -> assignedById, por nome)
    prop = rec.get("Proprietario_c")
    if prop:
        owner = resolver_proprietario(prop)
        if owner:
            p["assignedById"] = owner
        else:
            print(f"    !! proprietario nao encontrado: {prop!r}")

    # etapa (Status_c -> stageId)
    st = rec.get("Status_c")
    if st:
        p["stageId"] = STATUS_STAGE_MAP.get(str(st), "DT1208_105:CLIENT")

    # valor da campanha (ValorDaCampanha_c -> opportunity + currency)
    if rec.get("ValorDaCampanha_c") is not None:
        p["opportunity"] = str(rec["ValorDaCampanha_c"])
        p["currencyId"] = rec.get("CurrencyCode") or "BRL"

    # contato principal (ContatoPrincipal_c -> string)
    if rec.get("ContatoPrincipal_c"):
        p[CAMPO_CONTATO_PRINCIPAL] = rec["ContatoPrincipal_c"]

    # datas
    if rec.get("DataDeFechamento_c"):
        p[CAMPO_DATA_FECHAMENTO] = mapeamento.normalizar_data(rec["DataDeFechamento_c"])
    if rec.get("DataFechamentoParceiro_c"):
        p[CAMPO_DATA_FECH_PARCEIRO] = mapeamento.normalizar_data(rec["DataFechamentoParceiro_c"])
    if rec.get("DataDoProjeto_c"):
        p[CAMPO_DATA_INICIO_PROJETO] = mapeamento.normalizar_data(rec["DataDoProjeto_c"])

    # strings
    if rec.get("VersaoAtualSolfis_c"):
        p[CAMPO_VERSAO] = rec["VersaoAtualSolfis_c"]
    if rec.get("Carteira_c"):
        p[CAMPO_CARTEIRA] = rec["Carteira_c"]
    if rec.get("Equipe_c"):
        p[CAMPO_EQUIPE] = rec["Equipe_c"]
    if rec.get("Probabilidade_c"):
        p[CAMPO_PROBABILIDADE] = rec["Probabilidade_c"]
    if rec.get("ERPLista_c") or rec.get("ERP_c"):
        p[CAMPO_ERP] = rec.get("ERPLista_c") or rec.get("ERP_c")
    if rec.get("RangeDeFaturamentoLista_c") or rec.get("RangeDeFaturamento_c"):
        p[CAMPO_RANGE] = rec.get("RangeDeFaturamentoLista_c") or rec.get("RangeDeFaturamento_c")
    if rec.get("TipoDeContaLista_c") or rec.get("TipoDeConta_c"):
        p[CAMPO_TIPO_CONTA] = rec.get("TipoDeContaLista_c") or rec.get("TipoDeConta_c")

    # numeros (double)
    for oracle_f, campo in (
        ("HorasGP_c", CAMPO_HORAS_GP),
        ("HorasCT_c", CAMPO_HORAS_CT),
        ("HorasCF_c", CAMPO_HORAS_CF),
        ("HorasAF_c", CAMPO_HORAS_AF),
        ("HorasTotais_c", CAMPO_HORAS_TOTAIS),
        ("DuracaoDoProjetoemMeses_c", CAMPO_DURACAO),
    ):
        v = rec.get(oracle_f)
        if v is not None and v != "":
            p[campo] = str(v)

    # enums (choice -> enum), via build_value_mapping
    for bfield, info in val_map.items():
        ofield = info.get("oracle_field")
        if not ofield:
            continue
        v = rec.get(ofield)
        if v in (None, ""):
            continue
        if ofield == "Solucao_c":
            val = info["de_para"].get(str(v)) or SOLUCAO_EXTRA.get(str(v))
            if val:
                p[bfield] = val
        else:
            p[bfield] = info["de_para"].get(str(v), v)

    # solucao atual (SolucaoAtual_c -> enum ufCrm75_1779972229)
    if rec.get("SolucaoAtual_c"):
        p[CAMPO_SOLUCAO_ATUAL] = solucao_atual_map.get(str(rec["SolucaoAtual_c"]), str(rec["SolucaoAtual_c"]))

    # observacao (CLOB via enclosure, pre-buscada em paralelo)
    obs = observacoes.get(str(rec.get("Id")))
    if obs:
        p[CAMPO_OBSERVACAO] = obs

    return p


def main():
    limite = None
    if "--limite" in sys.argv:
        i = sys.argv.index("--limite")
        if i + 1 < len(sys.argv):
            limite = int(sys.argv[i + 1])

    o = OracleClient()
    val_map = build_value_mapping("CampanhasSynchro_c", "item1208")
    solucao_atual_map = build_solucao_atual_map()

    empresas = carregar_mapa_entidade("crm.company.list", CAMPO_ID_ORACLE["company"])
    deals = carregar_mapa_entidade("crm.deal.list", CAMPO_ID_ORACLE["deal"])
    parceiros = carregar_mapa_item(ENTIDADE_ITEM["item1196"], CAMPO_ID_ORACLE["item1196"])
    campanhas = carregar_mapa_item(ENTIDADE_ITEM["item1204"], CAMPO_ID_ORACLE["item1204"])
    print(f"empresas={len(empresas)} deals={len(deals)} parceiros={len(parceiros)} campanhas={len(campanhas)} "
          f"solucao_atual={len(solucao_atual_map)}")

    dados = o.registros_todos("CampanhasSynchro_c")
    print(f"{len(dados)} campanhas synchro no Oracle")

    existentes = migrador.carregar_existentes_item("item1208")
    print(f"  {len(existentes)} ja existentes")

    alvos = []
    pulados = 0
    for rec in dados:
        if rec.get("Id") is None:
            continue
        conta = rec.get("Conta_Id_c")
        if not conta or str(conta) not in empresas:
            pulados += 1
            continue
        alvos.append(rec)

    if limite is not None:
        alvos = alvos[:limite]
        print(f"  (teste) limitado a {limite} campanhas", flush=True)

    print(f"Buscando observacoes (CLOB) de {len(alvos)} campanhas (paralelo)...", flush=True)
    observacoes = buscar_observacoes(alvos, o)

    comandos = []
    metas = []
    for rec in alvos:
        cid = rec.get("Id")
        payload = montar_payload(rec, observacoes, val_map, solucao_atual_map, empresas, deals, parceiros, campanhas)
        existing = existentes.get(str(cid))
        if existing:
            comandos.append(migrador.command("crm.item.update", fields=payload, extra={"entityTypeId": EID, "id": existing}))
            metas.append((cid, rec.get("Conta_c"), "updated"))
        else:
            comandos.append(migrador.command("crm.item.add", fields=payload, extra={"entityTypeId": EID}))
            metas.append((cid, rec.get("Conta_c"), "created"))

    criados = atualizados = erros = 0
    t0 = time.time()
    res, errs = migrador.batch(comandos)
    t_escrita = time.time() - t0
    for (cid, conta, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {cid} {conta}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"criados={criados} atualizados={atualizados} pulados={pulados} erros={erros} | escrita={t_escrita:.1f}s")


if __name__ == "__main__":
    main()
