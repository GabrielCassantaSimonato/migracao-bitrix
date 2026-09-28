import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, ENTIDADE_ITEM, CAMPO_ID_ORACLE, ORACLE_CRM_BASE
from oracle_client import OracleClient
from de_para_valores import build_value_mapping
import mapeamento
import migrador

WB = BITRIX_WEBHOOK.rstrip("/")
EID = ENTIDADE_ITEM["item1216"]
ID_FIELD = CAMPO_ID_ORACLE["item1216"]  # ufCrm79_1789386308816

CAMPO_DESCRICAO = "ufCrm79_1779888785090"
CAMPO_DATA_DESEJADA = "ufCrm79_1779887454329"       # Data desejada do cliente
CAMPO_DATA_DISPONIVEL = "ufCrm79_1779887472962"     # Data disponível do pré-venda
CAMPO_DATA_REPLANEJADA = "ufCrm79_1779887751831"    # Data replanejada
CAMPO_DATA_RESOLUCAO = "ufCrm79_1779887798937"      # Data da resolução
CAMPO_DATA_INICIO = "ufCrm79_1779892504542"         # Data início
CAMPO_DATA_FIM = "ufCrm79_1779892512206"            # Data fim
CAMPO_DATA_INICIO_PROJETO = "ufCrm79_1779892346081"  # Data prevista início do projeto
CAMPO_DATA_GOLIVE = "ufCrm79_1779892391320"         # Data prevista para o Go live
CAMPO_DURACAO_HORAS = "ufCrm79_1779888277240"       # Duração total da atividade (horas)
CAMPO_INGLES = "ufCrm79_1779884901869"              # Necessário inglês (boolean)
CAMPO_RETRABALHO = "ufCrm79_1779884913174"          # Retrabalho (boolean)
CAMPO_INTEGRACOES = "ufCrm79_1779892243987"
CAMPO_VERSAO_SISTEMAS = "ufCrm79_1779892288656"
CAMPO_TARGET = "ufCrm79_1779892302848"
CAMPO_TEMPO_OPERACAO = "ufCrm79_1779892439268"
CAMPO_ESTABELECIMENTOS = "ufCrm79_1779892472494"
CAMPO_IMPLANTACAO_MISTA = "ufCrm79_1779892269553"
CAMPO_VALIDO_GP = "ufCrm79_1779884946378"
CAMPO_ATRIBUIDO_A = "ufCrm79_1779884865"     # employee (Atribuído A)
CAMPO_HORA_INICIO = "ufCrm79_1788870184951"  # datetime (Hora início)
CAMPO_HORA_FIM = "ufCrm79_1788870154046"     # datetime (Hora fim)
CAMPO_TIPO_PROPOSTA = "ufCrm79_1779890459"       # enum (Atualização de versão/Cliente novo/Migração cloud/Outro)
CAMPO_ATIVIDADES = "ufCrm79_1779890513"          # enum (Funcional/Funcional técnica/Técnica)
CAMPO_PROJETO_ERP = "ufCrm79_1779890578"         # enum (Sim/Não)
CAMPO_TIPO_IMPLANTACAO = "ufCrm79_1779890614"    # enum (Misto/Presencial/Remoto)
CAMPO_POLITICA_DESPESAS = "ufCrm79_1779890804"   # enum (Cobrável/Embutido na proposta)
CAMPO_HORAS_DESLOC = "ufCrm79_1779890839"        # enum (Cobrável/Embutido na proposta)
CAMPO_BASES = "ufCrm79_1779890878"               # enum multiple (Bases em que a Synchro deve atuar)
CAMPO_DETALHAMENTO_DESPESAS = "ufCrm79_1779892326431"  # string (Detalhamento das despesas)
CAMPO_ANEXOS_SPV = "ufCrm79_1790593724"  # file (MULTIPLE) - "Anexos (migracao)"

# De-para manual de enums do item1216 que não têm título e não casam com o de-para automático.
TIPO_PROPOSTA_MAP = {
    "ATUALIZACAO_VERSAO": "7107",
    "CLIENTE_NOVO": "7109",
    "MIGRACAO_CLOUD": "7111",
    "OUTRO": "7113",
}
ATIVIDADES_MAP = {
    "FUNCIONAL": "7115",
    "FUNCIONAL_TECNICA": "7117",
    "TECNICA": "7119",
}
PROJETO_ERP_MAP = {"Y": "7121", "N": "7123"}
TIPO_IMPLANTACAO_MAP = {"MISTO": "7125", "PRESENCIAL": "7127", "REMOTO": "7129"}
POLITICA_DESPESAS_MAP = {"COBRAVEL": "7131", "EMBUTIDO_PROPOSTA": "7133"}
HORAS_DESLOC_MAP = {"COBRAVEL": "7135", "EMBUTIDO_PROPOSTA": "7137"}
BASES_MAP = {
    "BASE_HOMOLOGACAO_1": "7139",
    "BASE_DE_HOMOLOGACAO_1": "7139",
    "DESENVOLVIMENTO": "7141",
    "PRODUCAO": "7143",
    "BASE_HOMOLOGACAO_2": "7145",
    "BASE_DE_HOMOLOGACAO_2": "7145",
}

# StatusCd (Oracle) -> stageId do item1216 (DYNAMIC_1216_STAGE_109)
STATUS_STAGE_MAP = {
    "ORA_SVC_NEW": "DT1216_109:NEW",
    "ORA_SVC_INPROGRESS": "DT1216_109:UC_SUMLKY",  # Em elaboração
    "ORA_SVC_RESOLVED": "DT1216_109:SUCCESS",       # Concluído
    "ORA_SVC_CLOSED": "DT1216_109:SUCCESS",
    "CANCELADO": "DT1216_109:FAIL",
    "REJEITADO": "DT1216_109:FAIL",
}

def resolver_usuario(email, nome):
    return mapeamento.resolver_usuario(email, nome)


def combinar_data_hora(data, hora):
    if not hora:
        return None
    h = str(hora).strip()
    if ":" not in h:
        h += ":00:00"
    elif h.count(":") == 1:
        h += ":00"
    if data:
        d = mapeamento.normalizar_data(data)
        if d:
            return f"{d} {h}"
    return h


def parse_numero(v):
    s = str(v).strip()
    if ":" in s:
        s = s.split(":")[0]
    s = s.replace(",", ".")
    try:
        return str(float(s))
    except ValueError:
        return None


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


def carregar_opty_para_conta(o):
    """{OptyId (str) -> TargetPartyId (str)} das oportunidades, para fallback da conta da SPV."""
    mapa = {}
    opps = o.registros_todos("opportunities", fields=["OptyId", "TargetPartyId"], q="OptyId>0")
    for opp in opps:
        if opp.get("OptyId") is not None and opp.get("TargetPartyId") is not None:
            mapa[str(int(opp["OptyId"]))] = str(int(opp["TargetPartyId"]))
    return mapa


def montar_payload(rec, val_map, empresas, deals, opty_para_conta=None):
    p = {}
    p["title"] = rec.get("Title") or f"SPV {rec.get('SrNumber')}"
    p[ID_FIELD] = str(rec.get("SrId"))

    # empresa (AccountPartyId -> companyId; fallback: conta da oportunidade)
    cid = None
    if rec.get("AccountPartyId"):
        cid = empresas.get(str(rec.get("AccountPartyId")))
    if not cid and rec.get("Oportunidade_Id_c") is not None and opty_para_conta:
        conta_opp = opty_para_conta.get(str(int(rec["Oportunidade_Id_c"])))
        if conta_opp:
            cid = empresas.get(conta_opp)
    if cid:
        p["companyId"] = cid

    # negocio (Oportunidade_Id_c -> parentId2)
    if rec.get("Oportunidade_Id_c"):
        did = deals.get(str(rec.get("Oportunidade_Id_c")))
        if did:
            p["parentId2"] = did

    # relatado por -> assignedById
    relatado = resolver_usuario(rec.get("ReportedByEmailAddress"), rec.get("ReportedByPartyName"))
    if relatado:
        p["assignedById"] = relatado
    else:
        print(f"    !! relatado por nao encontrado: {rec.get('ReportedByPartyName')!r}")

    # atribuido a -> CAMPO_ATRIBUIDO_A (employee)
    atribuido = resolver_usuario(rec.get("AssigneeEmailAddress"), rec.get("AssigneePersonName"))
    if atribuido:
        p[CAMPO_ATRIBUIDO_A] = atribuido
    else:
        print(f"    !! atribuido a nao encontrado: {rec.get('AssigneePersonName')!r}")

    # etapa (StatusCd -> stageId)
    st = rec.get("StatusCd")
    if st:
        p["stageId"] = STATUS_STAGE_MAP.get(str(st), "DT1216_109:NEW")

    # descricao
    if rec.get("ProblemDescription"):
        p[CAMPO_DESCRICAO] = rec["ProblemDescription"]

    # datas
    for oracle_f, campo in (
        ("DataDesejadaDaEntrega_c", CAMPO_DATA_DESEJADA),
        ("DataPrevistaDeEntrega_c", CAMPO_DATA_DISPONIVEL),
        ("DataDeEntregaReprogramada_c", CAMPO_DATA_REPLANEJADA),
        ("DataDaResolucao_c", CAMPO_DATA_RESOLUCAO),
        ("DataInicio_c", CAMPO_DATA_INICIO),
        ("DataFim_c", CAMPO_DATA_FIM),
        ("DataPrevistaInicioDoProjeto_c", CAMPO_DATA_INICIO_PROJETO),
        ("DataPrevistaParaOGoLive_c", CAMPO_DATA_GOLIVE),
    ):
        if rec.get(oracle_f):
            p[campo] = mapeamento.normalizar_data(rec[oracle_f])

    # horas (Hora início / Hora fim) -> datetime (data + hora)
    hi = combinar_data_hora(rec.get("DataInicio_c"), rec.get("HoraInicio_c"))
    if hi:
        p[CAMPO_HORA_INICIO] = hi
    hf = combinar_data_hora(rec.get("DataFim_c") or rec.get("DataInicio_c"), rec.get("HoraFim_c"))
    if hf:
        p[CAMPO_HORA_FIM] = hf

    # strings
    for oracle_f, campo in (
        ("IntegracoesPrevistas_c", CAMPO_INTEGRACOES),
        ("VersaoDosSistemasEnvolvidos_c", CAMPO_VERSAO_SISTEMAS),
        ("TargetValor_c", CAMPO_TARGET),
        ("TempoDesejadoOperacaoAssistida_c", CAMPO_TEMPO_OPERACAO),
        ("EstabelecimentosParaHomologacao_c", CAMPO_ESTABELECIMENTOS),
        ("ImplantacaoMista_c", CAMPO_IMPLANTACAO_MISTA),
        ("ResumoAtividadeReuniao_c", CAMPO_VALIDO_GP),
    ):
        if rec.get(oracle_f):
            p[campo] = rec[oracle_f]

    # duracao (double)
    if rec.get("DuracaoTotalAtividadeHoras_c") is not None and rec.get("DuracaoTotalAtividadeHoras_c") != "":
        dur = parse_numero(rec["DuracaoTotalAtividadeHoras_c"])
        if dur:
            p[CAMPO_DURACAO_HORAS] = dur

    # booleanos (SPA -> Y/N)
    p[CAMPO_INGLES] = "Y" if rec.get("Ingles_c") in (True, "true", "True", "Y", "y", 1, "1") else "N"
    p[CAMPO_RETRABALHO] = "Y" if rec.get("Retrabalho_c") in (True, "true", "True", "Y", "y", 1, "1") else "N"

    # enums (choice -> enum), via build_value_mapping
    for bfield, info in val_map.items():
        ofield = info.get("oracle_field")
        if not ofield:
            continue
        v = rec.get(ofield)
        if v in (None, ""):
            continue
        p[bfield] = info["de_para"].get(str(v), v)

    # enums sem título (de-para manual)
    for oracle_f, campo, mapa in (
        ("TipoDeProposta_c", CAMPO_TIPO_PROPOSTA, TIPO_PROPOSTA_MAP),
        ("AtividadesASeremConsideradas_c", CAMPO_ATIVIDADES, ATIVIDADES_MAP),
        ("ProjetoFiscalSeraImplantadoComERP_c", CAMPO_PROJETO_ERP, PROJETO_ERP_MAP),
        ("TipoDeImplantacao_c", CAMPO_TIPO_IMPLANTACAO, TIPO_IMPLANTACAO_MAP),
        ("PoliticaDespesas_c", CAMPO_POLITICA_DESPESAS, POLITICA_DESPESAS_MAP),
        ("HorasDeDeslocamento_c", CAMPO_HORAS_DESLOC, HORAS_DESLOC_MAP),
    ):
        v = rec.get(oracle_f)
        if v in (None, ""):
            continue
        p[campo] = mapa.get(str(v), v)

    # bases em que a Synchro deve atuar (enum multiple, separado por vírgula)
    bases = rec.get("BasesEmQueASynchroDeveAtuar_c")
    if bases:
        val = str(bases).replace("DESENVOLVIMENTO_PRODUCAO", "DESENVOLVIMENTO,PRODUCAO")
        ids = []
        for tok in val.split(","):
            tok = tok.strip()
            if tok in BASES_MAP:
                ids.append(BASES_MAP[tok])
        if ids:
            p[CAMPO_BASES] = ids

    return p


def anexos_spv(o, sr_number):
    """Anexos da SPV (Attachment child via SrNumber), com conteudo base64 via FileContents."""
    try:
        url = f"{ORACLE_CRM_BASE}/serviceRequests/{sr_number}/child/Attachment"
        r = requests.get(url, auth=o.auth, verify=False,
                         params={"limit": 100, "onlyData": "true", "fields": "FileName,FileContents"},
                         timeout=180)
        if r.status_code != 200:
            return []
        out = []
        for i in r.json().get("items", []):
            nome = i.get("FileName")
            conteudo = i.get("FileContents")
            if nome and conteudo:
                out.append({"name": nome, "content": conteudo})
        return out
    except Exception:
        return []


def aplicar_anexos_spv(bid, anexos, sr_number):
    """Sobe anexos (crm.item.update) de uma SPV no campo multiple CAMPO_ANEXOS_SPV."""
    if not anexos:
        return True
    try:
        body = {
            "entityTypeId": EID,
            "id": bid,
            "fields": {CAMPO_ANEXOS_SPV: [[a["name"], a["content"]] for a in anexos]},
        }
        migrador._post_json("crm.item.update", body)
        return True
    except Exception as e:
        print(f"  !! anexos {sr_number}: {e}", flush=True)
        return False


def anexos_todos(limite=None, so_faltantes=False):
    """Passada separada: sobe os anexos das SPVs ja migradas (idempotente)."""
    o = OracleClient()
    existentes = migrador.carregar_existentes_item("item1216")
    print(f"  {len(existentes)} SPVs ja migradas")

    empresas = None
    if so_faltantes:
        empresas = carregar_mapa_entidade("crm.company.list", CAMPO_ID_ORACLE["company"])

    dados = o.registros_todos("serviceRequests")
    alvo = []
    for rec in dados:
        srid = rec.get("SrId")
        if srid is None:
            continue
        bid = existentes.get(str(srid))
        if not bid:
            continue
        if so_faltantes:
            conta = rec.get("AccountPartyId")
            if conta and str(conta) in empresas:
                continue  # conta propria ja resolvia; nao e' das novas (orfas)
        alvo.append((rec, bid))
    if limite:
        alvo = alvo[:limite]
    print(f"{len(alvo)} SPVs para anexos")

    erros = 0
    com_anexo = 0
    total_anexos = 0
    BATCH = 50
    for base in range(0, len(alvo), BATCH):
        grupo = alvo[base:base + BATCH]
        anexos_map = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            fut = {}
            for rec, bid in grupo:
                srn = rec.get("SrNumber")
                fut[ex.submit(anexos_spv, o, srn)] = srn
            for f in as_completed(fut):
                srn = fut[f]
                try:
                    anexos_map[srn] = f.result()
                except Exception:
                    anexos_map[srn] = []
        for rec, bid in grupo:
            srn = rec.get("SrNumber")
            anexos = anexos_map.get(srn, [])
            if anexos:
                com_anexo += 1
                total_anexos += len(anexos)
            if not aplicar_anexos_spv(bid, anexos, srn):
                erros += 1
        print(f"  ... {base + len(grupo)}/{len(alvo)}", flush=True)

    print("=" * 60)
    print(f"processados={len(alvo)} com_anexo={com_anexo} total_anexos={total_anexos} erros={erros}")


def main():
    if "--anexos" in sys.argv:
        limite = None
        if "--limite" in sys.argv:
            i = sys.argv.index("--limite")
            if i + 1 < len(sys.argv):
                limite = int(sys.argv[i + 1])
        if CAMPO_ANEXOS_SPV == "ufCrm79_INSERIR_CODIGO":
            print("!! CAMPO_ANEXOS_SPV ainda nao configurado (campo multiple de anexos nao criado no Bitrix).")
            return
        anexos_todos(limite, so_faltantes="--faltantes" in sys.argv)
        return

    o = OracleClient()
    val_map = build_value_mapping("serviceRequests", "item1216")

    empresas = carregar_mapa_entidade("crm.company.list", CAMPO_ID_ORACLE["company"])
    deals = carregar_mapa_entidade("crm.deal.list", CAMPO_ID_ORACLE["deal"])
    opty_para_conta = carregar_opty_para_conta(o)
    print(f"empresas={len(empresas)} deals={len(deals)} opty_para_conta={len(opty_para_conta)}")

    dados = o.registros_todos("serviceRequests")
    print(f"{len(dados)} SPVs no Oracle")

    existentes = migrador.carregar_existentes_item("item1216")
    print(f"  {len(existentes)} ja existentes")

    so_faltantes = "--faltantes" in sys.argv
    alvos = []
    for rec in dados:
        if rec.get("SrId") is None:
            continue
        if so_faltantes and str(rec.get("SrId")) in existentes:
            continue
        alvos.append(rec)

    comandos = []
    metas = []
    for rec in alvos:
        sr_id = rec.get("SrId")
        payload = montar_payload(rec, val_map, empresas, deals, opty_para_conta)
        existing = existentes.get(str(sr_id))
        if existing:
            comandos.append(migrador.command("crm.item.update", fields=payload, extra={"entityTypeId": EID, "id": existing}))
            metas.append((sr_id, rec.get("AccountPartyName"), "updated"))
        else:
            comandos.append(migrador.command("crm.item.add", fields=payload, extra={"entityTypeId": EID}))
            metas.append((sr_id, rec.get("AccountPartyName"), "created"))

    criados = atualizados = erros = 0
    res, errs = migrador.batch(comandos)
    for (sr_id, name, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {sr_id} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"criados={criados} atualizados={atualizados} erros={erros}")


if __name__ == "__main__":
    main()
