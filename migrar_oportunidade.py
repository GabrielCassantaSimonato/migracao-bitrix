import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, CONTATO_PRINCIPAL, DEMAIS_CONTATOS, ORACLE_CRM_BASE, USUARIO_DESATIVADO, CAMPO_ID_ORACLE
import contatos
import mapeamento
import migrador
from de_para_valores import build_value_mapping, norm, norm_loose
from oracle_client import OracleClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WB = BITRIX_WEBHOOK.rstrip("/")

VALOR_FIELDS = {
    "SE": "UF_CRM_1785759782270",
    "LU": "UF_CRM_1785503561603",
    "MA": "UF_CRM_1785507451643",
    "CLOUD": "UF_CRM_1785507463090",
    "SLA": "UF_CRM_1785507492227",
    "AMS": "UF_CRM_1785853113897",
    "MONITORAMENTO": "UF_CRM_1785853129238",
}
VALOR_TOTAL = "UF_CRM_1786131567648"
CAMPO_REVENUE = {
    "SE": "RevenueSE_c",
    "LU": "RevenueLU_c",
    "MA": "RevenueMA_c",
    "CLOUD": "RevenueCloud_c",
    "SLA": "RevenueSLA_c",
    "AMS": "RevenueAMS_c",
    "MONITORAMENTO": "RevenueMonitoramento_c",
}

# Campos adicionais (nao cobertos pelo de-para automatico)
CAMPO_AGENTE_PROSPECCAO = "UF_CRM_1779800373"       # employee
CAMPO_CONTATO_NOTA = "UF_CRM_1784227706"            # crm contact
CAMPO_CONTATO_ATIVACAO = "UF_CRM_1784227685"        # crm contact
CAMPO_PARCEIRO_PRINCIPAL = "UF_CRM_1769105112"      # crm (item1196)
CAMPO_PREVENDA = "UF_CRM_1789490557"                # enumeration multiple
CAMPO_DESCRICAO_LONGA = "UF_CRM_1779802374875"      # string (CLOB)
CAMPO_MOTIVO_DETALHADO = "UF_CRM_1779805261322"     # string (CLOB)
CAMPO_INSTRUCOES = "UF_CRM_1779814966720"           # string (CLOB)
CAMPO_DATA_FECHAMENTO = "UF_CRM_1779805396315"      # date
CAMPO_DURACAO_MESES = "UF_CRM_1768932433"           # double
CAMPO_ANEXOS_ITEM = "ufCrmAnexos"                    # file (multiple) - nome camelCase no crm.item
CAMPO_HORAS = {
    "AF": "UF_CRM_1768932445",
    "CF": "UF_CRM_1768932456",
    "GP": "UF_CRM_1768932475",
    "CT": "UF_CRM_1768932465",
}

CAMPO_FAMILIA = "UF_CRM_1779799488"                  # "Família de produto" (enumeration)
CAMPO_FAMILIA_LEAD = "UF_CRM_6AB51E4E5474B"          # "Família de produto - lead" (enumeration)
CAMPO_FAMILIA_ERRADO = "UF_CRM_1768850832"           # campo antigo (nao usar)
CAMPO_CONCORRENTES = "UF_CRM_1789560499"             # "Concorrentes" (enumeration, multiple)
CAMPO_CONTA_REFERENCIA = "UF_CRM_1779805434"         # "Conta informada para referência" (crm company)

PREVENDA_MAP = {
    "ALEXANE_LEMOS": "13237",
    "ANDERSON_MARCILIO": "13239",
    "ANDRE_AFONSO": "13241",
    "ASSLAN_COHEN": "13243",
    "CARLOS_ROBERTO": "13245",
    "GC": "13247",
    "GUILHERME_SALES": "13249",
    "PARCEIRO": "13251",
    "SHIRLEY": "13253",
    "RICHARD_DONZEL": "13255",
    "LUIS_GARCIA": "13257",
    "FLAVIO_PITTA": "13259",
}

FAMILIA_MAP = {
    "AMS": "6619",
    "CIAP": "6621",
    "CNPJ_ADICIONAL": "6623",
    "CONTAB": "6625",
    "CREDPIS": "6627",
    "DFE_MANAGER": "6629",
    "ECD": "6631",
    "ECD_ECF": "6633",
    "ECF": "6635",
    "EFD_PIS_COFINS_SYNCHRO4ME": "6637",
    "EMISSOR_FISCAL": "6639",
    "FIS_CON_DFE_EXP": "6641",
    "FIS": "6643",
    "FIS_CON": "6645",
    "FIS_DFE": "6647",
    "FIS_GOV": "6649",
    "GOVERNANCA": "6651",
    "INBOUND": "6653",
    "INT_ORA": "6655",
    "INT_SAP": "6657",
    "MDEST": "6659",
    "MONITORAMENTO": "6661",
    "OBRIGACOES": "6663",
    "OPECON": "6665",
    " OPECON": "6665",
    "REINF": "6667",
    "RECUP_ST": "6669",
    "SYNCHRO4DFE": "6671",
    "SYNCHRO4DRC": "6673",
    "SYNCHRO4ME": "6675",
    "SYNCHRO_4ME": "6675",
    "SYNCHRO4TDF": "6677",
    "TAX_ENGINE": "6679",
    # códigos legados presentes nos dados (fora da LOV atual, mas com item no Bitrix)
    "AE": "13801",
    "AE_GOA": "13945",
    "CTE": "13967",
    "EFD_CONTRIBUICOES": "13969",
    "EFD_ICMS_IPI": "13971",
    "GDT": "13947",
    "GOA": "6651",
    "INSIGHT": "13949",
    "NFE": "13975",
    "NFSE": "13973",
}

# De-para do MESMO campo (Família de produto) para o campo "Família de produto - lead"
# (UF_CRM_6AB51E4E5474B) do negócio. Ambos os campos recebem o valor da oportunidade.
FAMILIA_LEAD_MAP = {
    "AMS": "13983",
    "CIAP": "13409",
    "CNPJ_ADICIONAL": "13411",
    "CONTAB": "13415",
    "CREDPIS": "13417",
    "DFE_MANAGER": "13963",
    "ECD": "13419",
    "ECD_ECF": "13421",
    "ECF": "13423",
    "EFD_PIS_COFINS_SYNCHRO4ME": "13965",
    "EMISSOR_FISCAL": "13429",
    "FIS_CON_DFE_EXP": "13435",
    "FIS": "13431",
    "FIS_CON": "13433",
    "FIS_DFE": "13437",
    "FIS_GOV": "13439",
    "GOVERNANCA": "13443",
    "INBOUND": "13445",
    "INT_ORA": "13449",
    "INT_SAP": "13451",
    "MDEST": "13453",
    "MONITORAMENTO": "13455",
    "OBRIGACOES": "13463",
    "OPECON": "13461",
    " OPECON": "13461",
    "REINF": "13465",
    "RECUP_ST": "13469",
    "SYNCHRO4DFE": "13985",
    "SYNCHRO4DRC": "13987",
    "SYNCHRO4ME": "13475",
    "SYNCHRO_4ME": "13475",
    "SYNCHRO4TDF": "13473",
    "TAX_ENGINE": "13477",
    # códigos legados presentes nos dados (fora da LOV atual, mas com item no Bitrix)
    "AE": "13405",
    "AE_GOA": "13407",
    "CTE": "13413",
    "EFD_CONTRIBUICOES": "13425",
    "EFD_ICMS_IPI": "13427",
    "GDT": "13441",
    "GOA": "13443",
    "INSIGHT": "13447",
    "NFE": "13459",
    "NFSE": "13457",
}

_user_cache = {}
_contact_cache = {}
_partner_cache = {}


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def buscar_usuario(email, nome=None):
    return mapeamento.resolver_usuario(email, nome)


def build_payload(rec, val_map):
    dp = load("de_para_completo.json")["opportunities"]["campos"]
    bf = load("bitrix_deal_fields.json")
    payload = {}
    for c in dp:
        if not c["bitrix"] or c["confianca"] != "Alto":
            continue
        bfield = c["bitrix"]
        btype = (bf.get(bfield) or {}).get("type", "")
        if btype in ("crm", "employee", "file", "crm_multifield"):
            continue
        if c.get("transform") in ("fk", "fk_company"):
            continue
        val = mapeamento.aplicar_transform(c["oracle"], bfield, c.get("transform"), rec)
        if val in (None, ""):
            continue
        if btype == "enumeration" and bfield in val_map:
            val = val_map[bfield]["de_para"].get(str(val), val)
        if btype == "boolean":
            val = "1" if val in (True, "true", "True", "Y", "y", 1, "1") else "0"
        payload[bfield] = val
    return payload


def carregar_empresas():
    mapa = {}
    start = 0
    while True:
        r = migrador._get("crm.company.list", params=[
            ("select[]", "ID"), ("select[]", "UF_CRM_1789041431158"), ("start", start)])
        res = r.get("result", [])
        for c in res:
            v = c.get("UF_CRM_1789041431158")
            if v:
                mapa[str(v)] = int(c["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def carregar_produtos_bitrix():
    """PROPERTY_305 -> ID do produto no Bitrix."""
    mapa = {}
    start = 0
    while True:
        r = migrador._get("crm.product.list", params=[
            ("select[]", "ID"), ("select[]", "PROPERTY_305"), ("start", start)])
        res = r.get("result", [])
        for p in res:
            prop = p.get("PROPERTY_305")
            val = prop.get("value") if isinstance(prop, dict) else prop
            if val:
                mapa[str(val)] = int(p["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def id_contato_cached(party_id):
    if party_id is None:
        return None
    key = str(party_id)
    if key in _contact_cache:
        return _contact_cache[key]
    bid = migrador.find_existing("contact", key)
    _contact_cache[key] = bid
    return bid


def garantir_contato(o, party_id, val_ct, empresas=None, company_bitrix_id=None):
    """Resolve o crm.contact do PartyId Oracle, criando-o no Bitrix se ainda não existir.

    Contatos da oportunidade podem pertencer a outra conta (ou nunca terem sido
    migrados) — por isso, ao invés de apenas procurar, cria o contato sob demanda
    usando o payload completo (mesma lógica da migração de contas)."""
    if party_id is None:
        return None
    key = str(party_id)
    if key in _contact_cache:
        return _contact_cache[key]
    bid = migrador.find_existing("contact", key)
    if bid:
        _contact_cache[key] = bid
        return bid
    ct = contatos.buscar_contato_oracle(party_id)
    if not ct:
        _contact_cache[key] = None
        return None
    company_id = company_bitrix_id
    acct = ct.get("AccountPartyId")
    if empresas and acct is not None:
        company_id = empresas.get(str(acct)) or company_id
    res = contatos.montar_contato_payload(ct, val_ct, company_id=company_id)
    if res is None:
        _contact_cache[key] = None
        return None
    pc, multi = res
    method, params = migrador.command("crm.contact.add", fields=pc, multi=multi)
    r = migrador._post_data(method, params)
    new_id = r.get("result")
    if new_id:
        _contact_cache[key] = int(new_id)
        print(f"    contato criado {new_id} | {ct.get('ContactName')} (party {key})", flush=True)
    else:
        _contact_cache[key] = None
        print(f"    !! erro ao criar contato {party_id}: {r.get('error_description') or r.get('error')}", flush=True)
    return _contact_cache[key]


def id_parceiro_cached(partner_id):
    if partner_id is None:
        return None
    if partner_id in _partner_cache:
        return _partner_cache[partner_id]
    pid = migrador.find_existing("item1196", partner_id)
    _partner_cache[partner_id] = pid
    return pid


_contatos_opp_cache = {}


def contatos_oportunidade(o, opty_number):
    """Lista de contatos atrelados a oportunidade (OpportunityContact via OptyNumber).
    Retorna None se o acesso falhar."""
    if opty_number in _contatos_opp_cache:
        return _contatos_opp_cache[opty_number]
    try:
        data = _oracle_get_child(
            o, f"/opportunities/{opty_number}/child/OpportunityContact",
            {"limit": 100, "onlyData": "true"})
        items = data.get("items", [])
    except Exception:
        items = None
    _contatos_opp_cache[opty_number] = items
    return items


_FALHAS_ORACLE = 0


def _oracle_get_child(o, path, params, timeout=120, tentativas=4):
    """GET com retry/backoff para child resources do Oracle.

    Evita falha silenciosa por throttling/timeout: tenta `tentativas` vezes com
    backoff (5s/10s/20s...). Levanta exceção se esgotar todas as tentativas."""
    global _FALHAS_ORACLE
    url = f"{ORACLE_CRM_BASE}{path}"
    last = None
    for attempt in range(tentativas):
        try:
            r = requests.get(url, auth=o.auth, verify=False, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last = e
        if attempt == tentativas - 1:
            break
        time.sleep(5 * (2 ** attempt))
    _FALHAS_ORACLE += 1
    raise last


def produtos_oportunidade(o, opty_number):
    """Linhas de receita (ChildRevenue) da oportunidade."""
    try:
        data = _oracle_get_child(
            o, f"/opportunities/{opty_number}/child/ChildRevenue",
            {"limit": 200, "onlyData": "true"}, timeout=120)
        return data.get("items", [])
    except Exception:
        return []


def concorrentes_oportunidade(o, opty_number):
    """Competidores da oportunidade (OpportunityCompetitor2 via OptyNumber).
    Retorna lista de {'nome': str, 'primary': bool}."""
    url = f"{ORACLE_CRM_BASE}/opportunities/{opty_number}/child/OpportunityCompetitor2"
    resp = requests.get(url, auth=o.auth, verify=False, params={"limit": 100, "onlyData": "true"}, timeout=120)
    if resp.status_code != 200:
        return []
    out = []
    for i in resp.json().get("items", []):
        nome = i.get("PartyName") or i.get("CompetitorPartyName")
        if not nome:
            continue
        primary = str(i.get("PrimaryFlg", "")).upper() in ("Y", "TRUE", "1")
        out.append({"nome": nome, "primary": primary})
    return out


_concorrentes_cache = None


def concorrentes_items():
    global _concorrentes_cache
    if _concorrentes_cache is None:
        r = requests.get(f"{WB}/crm.deal.fields", timeout=60).json()
        meta = (r.get("result") or {}).get(CAMPO_CONCORRENTES) or {}
        items = meta.get("items", [])
        _concorrentes_cache = [(str(i.get("VALUE", "")).strip(), str(i["ID"]))
                               for i in items if i.get("VALUE")]
    return _concorrentes_cache


def id_concorrente(nome):
    if not nome:
        return None
    for val, iid in concorrentes_items():
        if norm(val) == norm(nome):
            return iid
    for val, iid in concorrentes_items():
        if norm_loose(val) == norm_loose(nome):
            return iid
    return None


def valores_por_grupo(linhas):
    """Soma RevnAmount por grupo (GrupoProduto_c) das linhas de receita."""
    soma = {}
    for ln in linhas:
        grupo = (ln.get("GrupoProduto_c") or ln.get("ProdGroupName") or "").strip().upper()
        if not grupo:
            continue
        valor = parse_valor(ln.get("RevnAmount")) or 0
        soma[grupo] = soma.get(grupo, 0) + valor
    return soma


def anexos_oportunidade(o, opty_number):
    """Anexos da oportunidade (Attachment child), com conteudo base64 via FileContents."""
    try:
        data = _oracle_get_child(
            o, f"/opportunities/{opty_number}/child/Attachment",
            {"limit": 100, "onlyData": "true", "fields": "FileName,FileContents"}, timeout=180)
        out = []
        for i in data.get("items", []):
            nome = i.get("FileName")
            conteudo = i.get("FileContents")
            if nome and conteudo:
                out.append({"name": nome, "content": conteudo})
        return out
    except Exception:
        return []


def buscar_clob(o, opty_number, attr):
    url = f"{ORACLE_CRM_BASE}/opportunities/{opty_number}/enclosure/{attr}"
    resp = requests.get(url, auth=o.auth, verify=False, timeout=120,
                        headers={"Accept": "text/plain, application/octet-stream, */*"})
    if resp.status_code != 200:
        return None
    return resp.text


def parse_valor(v):
    """Converte para float com 2 casas. Aceita numero ou string BR ('308.905,75' -> 308905.75)."""
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


def montar_extra(o, rec, payload, empresas=None):
    """Preenche os campos adicionais (fks, usuarios, contatos, CLOBs)."""
    opty_number = rec.get("OptyNumber")
    empresas = empresas or {}

    # agente de prospeccao (employee)
    agente_nome = rec.get("AgenteDeProspeccaoLst_c")
    if agente_nome:
        uid = buscar_usuario(None, agente_nome)
        if uid:
            payload[CAMPO_AGENTE_PROSPECCAO] = uid

    # contato envio de nota / ativacao (crm contact)
    for fk_id, campo in ((rec.get("ContatoEnvioNota_Id_c"), CAMPO_CONTATO_NOTA),
                         (rec.get("ContatoAtivacaoSistema_Id_c"), CAMPO_CONTATO_ATIVACAO)):
        if fk_id:
            cid = id_contato_cached(fk_id)
            if cid:
                payload[campo] = cid

    # parceiro principal (item1196)
    partner_id = rec.get("PrimaryPartnerId")
    if partner_id:
        pid = id_parceiro_cached(partner_id)
        if pid:
            payload[CAMPO_PARCEIRO_PRINCIPAL] = pid

    # conta informada para referencia (crm company) - ContaReferencia_Id_c = PartyId da conta
    ref_id = rec.get("ContaReferencia_Id_c")
    if ref_id:
        ref_bx = empresas.get(str(ref_id))
        if ref_bx:
            payload[CAMPO_CONTA_REFERENCIA] = ref_bx

    # concorrentes (enumeration multiple) - OpportunityCompetitor2 via OptyNumber
    if opty_number:
        itens = []
        for c in concorrentes_oportunidade(o, opty_number):
            cid = id_concorrente(c["nome"])
            if cid and cid not in itens:
                itens.append(cid)
        if itens:
            payload[CAMPO_CONCORRENTES] = itens

    # pre-venda (enum multiple, aceita valores separados por virgula)
    prevenda = rec.get("PreVenda_c")
    if prevenda:
        itens = []
        for code in str(prevenda).split(","):
            code = code.strip()
            item = PREVENDA_MAP.get(code)
            if item and item not in itens:
                itens.append(item)
        if itens:
            payload[CAMPO_PREVENDA] = itens

    # familia de produto -> campos UF_CRM_1779799488 ("Família de produto") e
    # UF_CRM_6AB51E4E5474B ("Família de produto - lead"), ambos com o mesmo valor.
    payload.pop(CAMPO_FAMILIA_ERRADO, None)
    fam = rec.get("FamiliaDeProdutos_c")
    if fam:
        item = FAMILIA_MAP.get(str(fam))
        if item:
            payload[CAMPO_FAMILIA] = item
        item_lead = FAMILIA_LEAD_MAP.get(str(fam))
        if item_lead:
            payload[CAMPO_FAMILIA_LEAD] = item_lead

    # data de fechamento
    fech = rec.get("EffectiveDate")
    if fech:
        payload[CAMPO_DATA_FECHAMENTO] = mapeamento.normalizar_data(fech)

    # duracao do projeto (meses)
    dur = rec.get("DuracaoDoProjetoEmMeses_c")
    if dur not in (None, ""):
        payload[CAMPO_DURACAO_MESES] = dur

    # horas AF / CF / GP / CT
    for tipo, campo in CAMPO_HORAS.items():
        v = rec.get(f"Horas{tipo}_c")
        if v not in (None, ""):
            payload[campo] = v

    # CLOBs via enclosure (OptyNumber)
    if opty_number:
        for attr, campo in (("DescriptionTextLong_c", CAMPO_DESCRICAO_LONGA),
                            ("MotivoDoEncerramentoDetalhadoLong_c", CAMPO_MOTIVO_DETALHADO),
                            ("InfoAdicionalFaturamento_c", CAMPO_INSTRUCOES)):
            clob = buscar_clob(o, opty_number, attr)
            if clob:
                payload[campo] = clob

    # demais contatos (OpportunityContact, atrelados a oportunidade)
    if opty_number:
        contas_opp = contatos_oportunidade(o, opty_number)
        if contas_opp is not None:
            key_contact = rec.get("KeyContactId")
            demais = []
            for c in contas_opp:
                cpid = c.get("PERPartyId")
                if cpid is None:
                    continue
                if key_contact is not None and str(cpid) == str(key_contact):
                    continue
                bid = id_contato_cached(cpid)
                if bid and bid not in demais:
                    demais.append(bid)
            payload[DEMAIS_CONTATOS["deal"]] = demais if demais else [""]

    return payload


def montar_payload_opp(o, rec, val_map, empresas, company_bitrix_id):
    """Monta o payload do deal. Retorna (payload, opty_id, opty_number, linhas)."""
    opty_id = rec.get("OptyId")
    payload = build_payload(rec, val_map)
    payload["TITLE"] = rec.get("Name") or ""
    payload["STAGE_ID"] = mapeamento.deal_stage(rec.get("SalesStage"))
    payload["ORIGINATOR_ID"] = "oracle"
    payload["ORIGIN_ID"] = str(opty_id)
    payload["COMPANY_ID"] = company_bitrix_id

    owner_id = buscar_usuario(rec.get("EmailAddress"), rec.get("PartyName1"))
    if owner_id:
        payload["ASSIGNED_BY_ID"] = owner_id
    elif mapeamento.is_stage_fechada(rec.get("SalesStage")):
        # Opp fechada cujo owner original não existe mais no Bitrix -> "Usuário Desativado".
        payload["ASSIGNED_BY_ID"] = USUARIO_DESATIVADO

    # contato principal (KeyContactId)
    if rec.get("KeyContactId"):
        cid = id_contato_cached(rec.get("KeyContactId"))
        if cid:
            payload[CONTATO_PRINCIPAL["deal"]] = cid

    # linhas de receita (ChildRevenue) - fonte confiavel dos valores por grupo e dos produtos
    linhas = produtos_oportunidade(o, rec.get("OptyNumber"))
    soma_grupo = valores_por_grupo(linhas)
    for tipo, bfield in VALOR_FIELDS.items():
        v = soma_grupo.get(tipo)
        if v is None:
            v = parse_valor(rec.get(CAMPO_REVENUE[tipo]))
        if v is not None:
            payload[bfield] = v
    vtotal = parse_valor(rec.get("Revenue"))
    if vtotal is None:
        vtotal = parse_valor(rec.get("EmailValorTotal_c"))
    if vtotal is not None:
        payload[VALOR_TOTAL] = vtotal
        # OPPORTUNITY (valor nativo) usa Revenue; ExpectAmount tem escala errada (10x).
        payload["OPPORTUNITY"] = vtotal

    montar_extra(o, rec, payload, empresas)

    return payload, opty_id, rec.get("OptyNumber"), linhas


def aplicar_produtos(bid, linhas, produtos_bitrix, opty_id):
    """Sobe productrows.set de um deal (ChildRevenue). Retorna True em sucesso."""
    try:
        rows = []
        for ln in linhas:
            iid = ln.get("InventoryItemId")
            if iid is None:
                continue
            pid_b = produtos_bitrix.get(str(iid))
            if not pid_b:
                continue
            unit_price = ln.get("UnitPrice")
            if unit_price not in (None, ""):
                price = parse_valor(unit_price) or 0
                qty = ln.get("Quantity") or 1
            else:
                price = parse_valor(ln.get("RevnAmount")) or parse_valor(ln.get("ExpectAmount")) or 0
                qty = 1
            rows.append({"PRODUCT_ID": pid_b, "PRICE": price, "QUANTITY": qty})
        if rows:
            params = {"id": bid}
            for i, row in enumerate(rows):
                for k, v in row.items():
                    params[f"rows[{i}][{k}]"] = v
            migrador._post_data("crm.deal.productrows.set", params)
        return True
    except Exception as e:
        print(f"  !! produtos {opty_id}: {e}", flush=True)
        return False


def aplicar_anexos(bid, anexos, opty_id):
    """Sobe anexos (crm.item.update) de um deal. Retorna True em sucesso."""
    try:
        if anexos:
            body = {
                "entityTypeId": 2,
                "id": bid,
                "fields": {CAMPO_ANEXOS_ITEM: [[a["name"], a["content"]] for a in anexos]},
            }
            migrador._post_json("crm.item.update", body)
        return True
    except Exception as e:
        print(f"  !! anexos {opty_id}: {e}", flush=True)
        return False


def criar_faltantes():
    o = OracleClient()
    val_map = build_value_mapping("opportunities", "deal")
    val_ct = build_value_mapping("contacts", "contact")
    empresas = carregar_empresas()
    produtos_bitrix = carregar_produtos_bitrix()
    deals_map = migrador.carregar_existentes_crm("crm.deal.list", CAMPO_ID_ORACLE["deal"])
    print(f"Empresas migradas: {len(empresas)} | produtos no Bitrix: {len(produtos_bitrix)} | deals existentes: {len(deals_map)}")

    # coleta TODAS as oportunidades (q=OptyId>0) e filtra pelas contas migradas,
    # pulando as que já têm deal (resume idempotente).
    todas = o.registros_todos("opportunities", q="OptyId>0")
    print(f"{len(todas)} oportunidades no Oracle")

    opps = []
    for rec in todas:
        if rec.get("OptyId") is None:
            continue
        if str(rec.get("OptyId")) in deals_map:
            continue
        cid = empresas.get(str(rec.get("TargetPartyId")))
        if not cid:
            continue
        opps.append((rec, cid))
    print(f"{len(opps)} oportunidades faltantes (de contas migradas)")

    # Pré-passo: garante que todos os contatos referenciados (principal, demais,
    # envio de nota, ativação) existam no Bitrix ANTES de montar os payloads —
    # evita POSTs de criação dentro das threads de montagem de payload.
    print("Garantindo contatos das oportunidades (pré-passo)...", flush=True)
    for rec, cid in opps:
        opty_number = rec.get("OptyNumber")
        garantir_contato(o, rec.get("KeyContactId"), val_ct, empresas, cid)
        garantir_contato(o, rec.get("ContatoEnvioNota_Id_c"), val_ct, empresas, cid)
        garantir_contato(o, rec.get("ContatoAtivacaoSistema_Id_c"), val_ct, empresas, cid)
        contas_opp = contatos_oportunidade(o, opty_number)
        for c in contas_opp or []:
            garantir_contato(o, c.get("PERPartyId"), val_ct, empresas, cid)
    print("  pré-passo de contatos concluído", flush=True)

    comandos = []
    metas = []  # (opty_id, name)
    falhas = 0
    print("Montando payloads dos negócios (paralelo)...", flush=True)
    dados = [None] * len(opps)
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(montar_payload_opp, o, rec, val_map, empresas, cid): i
               for i, (rec, cid) in enumerate(opps)}
        done = 0
        for f in as_completed(fut):
            i = fut[f]
            try:
                dados[i] = f.result()
            except Exception as e:
                print(f"  !! payload {opps[i][0].get('OptyId')}: {e}", flush=True)
            done += 1
            if done % 200 == 0:
                print(f"  ... {done}/{len(opps)} payloads montados", flush=True)

    for (rec, cid), d in zip(opps, dados):
        if d is None:
            falhas += 1
            continue
        payload, opty_id, opty_number, linhas = d
        comandos.append(migrador.command("crm.deal.add", fields=payload))
        metas.append((opty_id, rec.get("Name"), opty_number, linhas))
    if falhas:
        print(f"  {falhas} oportunidades puladas por falha ao montar payload", flush=True)

    criados = erros = 0
    novos = []  # (deal_id, opty_id, opty_number, linhas) dos deals recem-criados
    res, errs = migrador.batch(comandos)
    for meta, r, e in zip(metas, res, errs):
        opty_id, name, opty_number, linhas = meta
        if e:
            erros += 1
            print(f"  ERRO deal {opty_id} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        else:
            criados += 1
            if r:
                novos.append((r, opty_id, opty_number, linhas))

    # ---- produtos + anexos dos deals recem-criados ----
    if novos:
        print(f"Aplicando produtos + anexos em {len(novos)} deals novos...", flush=True)
        anexos_map = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            fut = {ex.submit(anexos_oportunidade, o, on): oid
                   for _, oid, on, _ in novos}
            for f in as_completed(fut):
                oid = fut[f]
                try:
                    anexos_map[oid] = f.result()
                except Exception:
                    anexos_map[oid] = []
        for deal_id, oid, on, linhas in novos:
            aplicar_produtos(deal_id, linhas, produtos_bitrix, oid)
            aplicar_anexos(deal_id, anexos_map.get(oid, []), oid)

    print("=" * 60)
    print(f"criados={criados} erros={erros}")


def _parse_shard(argv):
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


def produtos_anexos_todos():
    shard, shards = _parse_shard(sys.argv)

    o = OracleClient()
    empresas = carregar_empresas()
    produtos_bitrix = carregar_produtos_bitrix()
    deals_map = migrador.carregar_existentes_crm("crm.deal.list", CAMPO_ID_ORACLE["deal"])
    print(f"Empresas: {len(empresas)} | produtos: {len(produtos_bitrix)} | deals: {len(deals_map)}")

    todas = o.registros_todos("opportunities", q="OptyId>0")

    alvo = []
    for rec in todas:
        if rec.get("OptyId") is None:
            continue
        deal_id = deals_map.get(str(rec.get("OptyId")))
        if not deal_id:
            continue
        if not empresas.get(str(rec.get("TargetPartyId"))):
            continue
        alvo.append((rec, deal_id))

    # sharding determinístico por OptyId (sem sobreposição entre webhooks)
    alvo.sort(key=lambda x: int(x[0].get("OptyId") or 0))
    if shard is not None:
        alvo = migrador.shard_items(alvo, shard, shards)

    if shard is not None:
        print(f"{len(alvo)} oportunidades para produtos/anexos (shard {shard}/{shards})")
    else:
        print(f"{len(alvo)} oportunidades para produtos/anexos")

    erros = 0
    BATCH = 200
    for base in range(0, len(alvo), BATCH):
        grupo = alvo[base:base + BATCH]
        linhas_map = {}
        anexos_map = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            fut = {}
            for rec, deal_id in grupo:
                on = rec.get("OptyNumber")
                fut[ex.submit(produtos_oportunidade, o, on)] = ("linhas", on)
                fut[ex.submit(anexos_oportunidade, o, on)] = ("anexos", on)
            for f in as_completed(fut):
                kind, on = fut[f]
                try:
                    val = f.result()
                except Exception:
                    val = []
                if kind == "linhas":
                    linhas_map[on] = val
                else:
                    anexos_map[on] = val
        for rec, deal_id in grupo:
            on = rec.get("OptyNumber")
            ok_p = aplicar_produtos(deal_id, linhas_map.get(on, []), produtos_bitrix, rec.get("OptyId"))
            ok_a = aplicar_anexos(deal_id, anexos_map.get(on, []), rec.get("OptyId"))
            if not (ok_p and ok_a):
                erros += 1
        print(f"  ... {base + len(grupo)}/{len(alvo)}", flush=True)

    print("=" * 60)
    print(f"processados={len(alvo)} erros={erros} | falhas_busca_oracle={_FALHAS_ORACLE}")


def carregar_contatos():
    """{PartyId Oracle -> ID do contato no Bitrix} em uma única passada paginada.

    Substitui milhares de find_existing por item (que estourava o rate-limit)."""
    return migrador.carregar_existentes_crm("crm.contact.list", CAMPO_ID_ORACLE["contact"])


def reparar_contatos():
    """Preenche o contato principal (KeyContactId) nos negócios já migrados onde o
    Oracle tem o contato mas o deal está com o campo vazio. Cria o contato no Bitrix
    se ainda não existir. Idempotente: re-rodar é seguro.

    Suporta --shard <i> <N> para paralelizar entre vários processos/webhooks."""
    shard, shards = _parse_shard(sys.argv)
    so_abertas = "--abertas" in sys.argv
    o = OracleClient()
    val_ct = build_value_mapping("contacts", "contact")
    empresas = carregar_empresas()

    deal_principal = {}  # opty_id -> (deal_id, principal_atual)
    start = 0
    while True:
        r = migrador._get("crm.deal.list", params=[
            ("select[]", "ID"), ("select[]", CAMPO_ID_ORACLE["deal"]),
            ("select[]", CONTATO_PRINCIPAL["deal"]), ("start", start)])
        res = r.get("result", [])
        for c in res:
            v = c.get(CAMPO_ID_ORACLE["deal"])
            oid = v.get("value") if isinstance(v, dict) else v
            p = c.get(CONTATO_PRINCIPAL["deal"])
            pv = p.get("value") if isinstance(p, dict) else p
            if oid:
                deal_principal[str(oid)] = (int(c["ID"]), pv)
        if len(res) < 50:
            break
        start += 50
    print(f"Empresas: {len(empresas)} | deals existentes: {len(deal_principal)}", flush=True)

    todas = o.registros_todos("opportunities", q="OptyId>0",
                              fields=["OptyId", "KeyContactId", "SalesStage", "Name", "TargetPartyId"])

    alvo = []
    for rec in todas:
        if rec.get("OptyId") is None or rec.get("KeyContactId") is None:
            continue
        if so_abertas and mapeamento.is_stage_fechada(rec.get("SalesStage")):
            continue
        d = deal_principal.get(str(rec.get("OptyId")))
        if not d:
            continue
        deal_id, pv = d
        if pv not in (None, "", "0", 0):
            continue
        alvo.append((rec, deal_id))
    filtro = " (somente abertos)" if so_abertas else ""
    print(f"{len(alvo)} oportunidades com contato principal pendente{filtro}", flush=True)

    # sharding determinístico por OptyId (sem sobreposição entre webhooks)
    alvo.sort(key=lambda x: int(x[0].get("OptyId") or 0))
    if shard is not None:
        alvo = migrador.shard_items(alvo, shard, shards)
        print(f"  shard {shard}/{shards}: {len(alvo)} oportunidades", flush=True)

    comandos = []
    metas = []
    contatos_map = carregar_contatos()
    print(f"Contatos no Bitrix carregados: {len(contatos_map)}", flush=True)

    criados = 0
    for i, (rec, deal_id) in enumerate(alvo, 1):
        party_id = rec.get("KeyContactId")
        if party_id is None:
            continue
        cid = contatos_map.get(str(party_id))
        if cid is None:
            ct = contatos.buscar_contato_oracle(party_id)
            if ct:
                company_id = empresas.get(str(rec.get("TargetPartyId")))
                res = contatos.montar_contato_payload(ct, val_ct, company_id=company_id)
                if res is not None:
                    pc, multi = res
                    method, params = migrador.command("crm.contact.add", fields=pc, multi=multi)
                    r = migrador._post_data(method, params)
                    new_id = r.get("result")
                    if new_id:
                        contatos_map[str(party_id)] = int(new_id)
                        cid = int(new_id)
                        criados += 1
                    else:
                        print(f"  !! erro ao criar contato {party_id}: {r.get('error_description') or r.get('error')}", flush=True)
        if cid is None:
            continue
        comandos.append(migrador.command(
            "crm.deal.update", fields={CONTATO_PRINCIPAL["deal"]: cid},
            extra={"id": deal_id}))
        metas.append((rec.get("OptyId"), rec.get("Name")))
        if i % 200 == 0:
            print(f"  ... {i}/{len(alvo)} montados", flush=True)

    atualizados = erros = 0
    res, errs = migrador.batch(comandos, batch_size=10)
    for meta, r, e in zip(metas, res, errs):
        opty_id, name = meta
        if e:
            erros += 1
            print(f"  ERRO deal {opty_id} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        else:
            atualizados += 1

    print("=" * 60)
    print(f"contato principal preenchido; deals atualizados={atualizados} erros={erros} contatos_criados={criados}")


def _valor_item(v):
    """Extrai o ID do item de um campo enum (single) que vem como dict {value: ID}."""
    if isinstance(v, dict):
        return v.get("value")
    return v


def _parse_limite(argv):
    for i, a in enumerate(argv):
        if a == "--limite" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return None
        elif a.startswith("--limite="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return None
    return None


def backfill_familia_lead():
    """Preenche o campo "Família de produto - lead" (UF_CRM_6AB51E4E5474B) nos negócios
    já migrados, a partir da família de produto da oportunidade (FamiliaDeProdutos_c).
    Também garante o campo "Família de produto" (UF_CRM_1779799488) — ambos recebem o
    mesmo valor. Idempotente: só atualiza quando algum dos dois diverge.

    Filtra as oportunidades criadas/atualizadas nos últimos `--anos` (default 10) anos,
    via LastUpdateDate no Oracle. Flags: --limite N (teste), --shard <i> <N>."""
    shard, shards = _parse_shard(sys.argv)
    limite = _parse_limite(sys.argv)
    anos = 10
    for i, a in enumerate(sys.argv):
        if a == "--anos" and i + 1 < len(sys.argv):
            try:
                anos = int(sys.argv[i + 1])
            except ValueError:
                pass
        elif a.startswith("--anos="):
            try:
                anos = int(a.split("=", 1)[1])
            except ValueError:
                pass

    from datetime import datetime, timedelta, timezone
    corte = (datetime.now(timezone.utc) - timedelta(days=365 * anos)).strftime("%Y-%m-%dT%H:%M:%S")

    o = OracleClient()

    deals_map = {}  # opty_id -> (deal_id, familia_atual, familia_lead_atual)
    start = 0
    while True:
        r = migrador._get("crm.deal.list", params=[
            ("select[]", "ID"), ("select[]", CAMPO_ID_ORACLE["deal"]),
            ("select[]", CAMPO_FAMILIA), ("select[]", CAMPO_FAMILIA_LEAD), ("start", start)])
        res = r.get("result", [])
        for c in res:
            v = c.get(CAMPO_ID_ORACLE["deal"])
            oid = v.get("value") if isinstance(v, dict) else v
            if oid:
                deals_map[str(oid)] = (int(c["ID"]),
                                       _valor_item(c.get(CAMPO_FAMILIA)),
                                       _valor_item(c.get(CAMPO_FAMILIA_LEAD)))
        if len(res) < 50:
            break
        start += 50
    print(f"Deals existentes: {len(deals_map)} | janela: ultimos {anos} anos (desde {corte})", flush=True)

    todas = o.registros_todos(
        "opportunities",
        q=f"OptyId>0;LastUpdateDate>='{corte}'",
        fields=["OptyId", "FamiliaDeProdutos_c", "Name"])

    alvo = []
    for rec in todas:
        fam = rec.get("FamiliaDeProdutos_c")
        if not fam:
            continue
        item = FAMILIA_MAP.get(str(fam))
        item_lead = FAMILIA_LEAD_MAP.get(str(fam))
        if not item and not item_lead:
            continue
        d = deals_map.get(str(rec.get("OptyId")))
        if not d:
            continue
        deal_id, atual, atual_lead = d
        campos = {}
        if item and str(atual) != str(item):
            campos[CAMPO_FAMILIA] = item
        if item_lead and str(atual_lead) != str(item_lead):
            campos[CAMPO_FAMILIA_LEAD] = item_lead
        if campos:
            alvo.append((rec, deal_id, campos))

    alvo.sort(key=lambda x: int(x[0].get("OptyId") or 0))
    if shard is not None and shards:
        alvo = [x for idx, x in enumerate(alvo) if idx % shards == shard]
        print(f"  shard {shard}/{shards}: {len(alvo)} negocios", flush=True)
    if limite:
        alvo = alvo[:limite]
        print(f"  limite aplicado: {len(alvo)} negocios", flush=True)

    print(f"{len(alvo)} negocios para atualizar familia", flush=True)

    comandos = []
    metas = []
    for rec, deal_id, campos in alvo:
        comandos.append(migrador.command("crm.deal.update", fields=campos, extra={"id": deal_id}))
        metas.append((rec.get("OptyId"), rec.get("Name")))

    atualizados = erros = 0
    GRUPO = 500
    for base in range(0, len(comandos), GRUPO):
        sub_c = comandos[base:base + GRUPO]
        sub_m = metas[base:base + GRUPO]
        res, errs = migrador.batch(sub_c, batch_size=10)
        for meta, r, e in zip(sub_m, res, errs):
            opty_id, name = meta
            if e:
                erros += 1
                print(f"  ERRO deal {opty_id} {name}: {e.get('error_description') or e.get('error')}", flush=True)
            else:
                atualizados += 1
        print(f"  ... {min(base + GRUPO, len(comandos))}/{len(comandos)} | atualizados={atualizados} erros={erros}", flush=True)

    print("=" * 60)
    print(f"familia de produto (lead) preenchida; deals atualizados={atualizados} erros={erros}")


if __name__ == "__main__":
    import sys
    if "--produtos-anexos" in sys.argv:
        produtos_anexos_todos()
    elif "--contatos" in sys.argv:
        reparar_contatos()
    elif "--familia-lead" in sys.argv:
        backfill_familia_lead()
    else:
        criar_faltantes()
