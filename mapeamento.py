import atexit
import json
import os
import re
import threading
import unicodedata

from config import CAMPO_ID_ORACLE, USUARIO_DESATIVADO

SUFIXO_PRODUTO = {
    "SE": "[SE]",
    "LU": "[LU]",
    "MA": "[MA]",
    "CLOUD": "[CLOUD]",
    "SLA": "[SLA]",
    "MONITORAMENTO": "[MONITORAMENTO]",
    "AMS": "[AMS]",
}

PK_ORACLE = {
    "opportunities": "OptyId",
    "accounts": "PartyId",
    "contacts": "PartyId",
    "leads": "LeadId",
    "partners": "PartyId",
    "serviceRequests": "SrId",
    "CampanhasSynchro_c": "Id",
    "CriarCampanha_c": "Id",
    "Parcelas_c": "Id",
    "activities": "ActivityId",
    "products": "InventoryItemId",
}

# De-para por objeto: lista de (campo_oracle, campo_bitrix, transform)
# transform: None = direto | "date" | "datetime" | "int2str" | funcao
DE_PARA = {
    "products": [
        ("InventoryItemId", "PROPERTY_305", "id"),
        ("Name", "NAME", "produto_nome"),
        ("Description", "DESCRIPTION", None),
        ("ProductTypeCode", None, "sufixo"),
    ],
    "accounts": [
        ("PartyId", "UF_CRM_1789041431158", "id"),
        ("OrganizationName", "TITLE", None),
        ("Type", "COMPANY_TYPE", "company_type"),
        ("AddressLine1", "ADDRESS", None),
        ("City", "ADDRESS_CITY", None),
        ("State", "ADDRESS_PROVINCE", None),
        ("PostalCode", "ADDRESS_POSTAL_CODE", None),
        ("Country", "ADDRESS_COUNTRY", None),
        ("URL", "UF_CRM_1779458158591", None),
        ("Description", "COMMENTS", None),
    ],
    "contacts": [
        ("PartyId", "UF_CRM_1789041484569", "id"),
        ("FirstName", "NAME", None),
        ("LastName", "LAST_NAME", None),
        ("AccountPartyId", "COMPANY_ID", "fk_company"),
        ("EmailAddress", "EMAIL", None),
        ("PhoneNumber", "PHONE", None),
    ],
    "leads": [
        ("LeadId", "UF_CRM_1789385673917", "id"),
        ("Name", "TITLE", None),
        ("StatusCode", "STATUS_ID", "lead_status"),
        ("Description", "COMMENTS", None),
    ],
    "partners": [
        ("PartyId", "ufCrm69_1789386068257", "id"),
        ("OrganizationName", "title", None),
        ("URL", "ufCrm69_1779816227466", None),
        ("Status", "ufCrm69_1779815992549", None),
    ],
    "serviceRequests": [
        ("SrId", "ufCrm79_1789386308816", "id"),
        ("Title", "title", None),
        ("ProblemDescription", "ufCrm79_1779888785090", None),
    ],
    "CampanhasSynchro_c": [
        ("Id", "ufCrm75_1789386179872", "id"),
        ("RecordName", "title", None),
        ("Campanha_Texto_c", "title", "fallback"),
    ],
    "CriarCampanha_c": [
        ("Id", "ufCrm73_1789386148200", "id"),
        ("RecordName", "title", None),
        ("DataInicio_c", "ufCrm73_1779970143723", "date"),
        ("DataFim_c", "ufCrm73_1779970152890", "date"),
    ],
    "Parcelas_c": [
        ("Id", "ufCrm77_1789386203592", "id"),
        ("RecordName", "title", None),
        ("NumeroDaParcela_c", "ufCrm77_1779974666870", None),
        ("ValorDaParcela_c", "ufCrm77_1779974787847", None),
        ("DataDeFechamento_c", "ufCrm77_1779974811337", "date"),
        ("DataDeVencimento_c", "ufCrm77_1779974822842", "date"),
    ],
    "activities": [
        ("ActivityId", "ufCrm81_1789386385040", "id"),
        ("Subject", "title", None),
        ("ActivityPartialDescription", "ufCrm81_1779994080130", None),
        ("DueDate", "ufCrm81_1779993918286", "date"),
    ],
    "opportunities": [
        ("OptyId", "UF_CRM_1789385750762", "id"),
        ("Name", "TITLE", None),
        ("SalesStage", "STAGE_ID", "deal_stage"),
        ("ExpectAmount", "OPPORTUNITY", None),
        ("CurrencyCode", "CURRENCY_ID", None),
        ("Description", "COMMENTS", None),
        ("PrimaryOrganizationId", "COMPANY_ID", "fk_company"),
        ("CreationDate", "BEGINDATE", "date"),
    ],
}

DEAL_STAGE_MAP = {
    "Encerrado Ganho": "WON",
    "Encerrado Perdido": "LOSE",
    "Encerrado Cancelado": "ON_HOLD",
    "Diagnóstico": "NEW",
    "Validação da Solução": "1",
    "Apresentação da Proposta": "DETAILS",
    "Negociação": "2",
    "Fechamento": "NEGOTIATION",
    "Informações Inconsistentes": "UC_I2AEK1",
    "Encerramento em Aprovação Ganho": "UC_4ZLXXL",
    "Encerramento em Aprovação Cancelado": "UC_QHZUY3",
    "Encerramento em Aprovação Perdido": "UC_SDWSPI",
}


def id_transformer(pk):
    def f(v):
        return str(v)
    return f


def produto_nome(v, rec):
    code = rec.get("ProductTypeCode")
    suffix = SUFIXO_PRODUTO.get(code, "")
    if not v:
        return v
    return f"{v} {suffix}".strip() if suffix else v


def sufixo_transform(v, rec):
    return None


def normalizar_data(v):
    if not v:
        return None
    s = str(v)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s


def aplicar_transform(field_oracle, bitrix_field, transform, rec):
    v = rec.get(field_oracle)
    if transform == "id":
        return str(v) if v is not None else None
    if transform == "date":
        return normalizar_data(v)
    if transform == "datetime":
        return str(v) if v else None
    if transform == "produto_nome":
        return produto_nome(v, rec)
    if transform == "sufixo":
        return None
    if transform == "fallback":
        return v
    if transform == "fk_company":
        return str(v) if v is not None else None
    if transform == "fk":
        return str(v) if v is not None else None
    if transform == "company_type":
        return company_type(v)
    if transform == "lead_status":
        return lead_status(v)
    if transform == "deal_stage":
        return deal_stage(v)
    if transform is None:
        return v
    return v


COMPANY_TYPE_MAP = {
    "ZCA_CUSTOMER": "CUSTOMER",
    "CLIENTE": "CUSTOMER",
    "CLIENTE_BASE_HISTORICA": "RESELLER",
    "SUSPECT": "PARTNER",
    "EXCLIENTE": "COMPETITOR",
    "EXPARCEIRO": "INVESTOR",
    "FORNECEDOR": "INTEGRATOR",
    "PARCEIRO_ESTRATEGICO": "PROSPECT",
    "ZCA_PROSPECT": "PRESS",
    "PROSPECT": "PRESS",
}


def company_type(v):
    return COMPANY_TYPE_MAP.get(v, "")


def lead_status(v):
    m = {
        "UNQUALIFIED": "NEW",
        "QUALIFIED": "IN_PROCESS",
        "CONVERTED": "CONVERTED",
        "JUNK": "JUNK",
    }
    return m.get(v, v)


# "Status do Lead" (StatusLead_c) -> STATUS_ID Bitrix. Fonte mais detalhada que o
# StatusCode (MKL_LEAD_STATUS), espelha os estágios reais do lead no Bitrix.
STATUS_LEAD_MAP = {
    "NOVO": "NEW",
    "QUALIFICADO": "IN_PROCESS",
    "EM_PROSPECCAO": "UC_7YI5UA",
    "EM_APROVACAO": "ON_HOLD",
    "ACEITO": "RESTORED",
    "CONVERTIDO": "CONVERTED",
    "NAO_QUALIFICADO": "JUNK",
    "INACESSIVEL": "1",
    "REJEITADO": "UC_F09Q1R",
    "INBOUND": "UC_38814A",
    "DEMANDA_NAO_ATENDIDA": "UC_NX8QBX",
    "DEMANDA_NAO_IDENTIFICADA": "UC_03DDV4",
    "DEMANDA_RECEM_ATENDIDA": "UC_J1N3BN",
    "DEMANDA_ATENDIMENTO_FUTURO": "UC_0EM4AQ",
    "PROSPECCAO_ATENDIMENTO_OUTRO": "UC_DVUHFW",
}


def lead_status_c(v):
    return STATUS_LEAD_MAP.get(v, v)


def deal_stage(v):
    return DEAL_STAGE_MAP.get(v, v)


def is_stage_fechada(sales_stage):
    """True se a fase Oracle é 'fechada' (Encerrado Ganho/Perdido/Cancelado ou
    Encerramento em Aprovação)."""
    return bool(sales_stage) and str(sales_stage).startswith("Encerr")


# De-para de nomes de proprietário com grafia divergente no Oracle (usuários desativados
# ou com underscores no nome). Chave = nome normalizado (normalizar_nome).
NOMES_ESPECIAIS = {
    "lucilene santos": "722",          # Lucilene Santos (desativada no Bitrix, mas proprietária)
    "benedito": "712",                 # Benedito Rosa (o "Benedito Desativado" do Oracle)
    "a lemos": "672",                  # Alexane Lemos ("A Lemos" no Oracle)
    "arquiteto3": USUARIO_DESATIVADO,  # placeholder -> usuário desativado padrão
    "mkt marketing": "5405",           # "Mkt Marketing" -> CRM_Prospecção
    "ronaldo rodrigues": USUARIO_DESATIVADO,  # Ronaldo Rodrigues (desligado) -> usuário desativado
    "abreu": USUARIO_DESATIVADO,              # Abreu (desligado) -> usuário desativado
    "patricia nakamura vianna": "1026",  # "Patricia Nakamura Vianna" (Oracle) -> Patrícia Nakamura
    "rafael ferreira": "4447",           # "Rafael Ferreira" (Oracle) -> Rafael de Paulo Ferreira
}

# De-para de e-mails genéricos (ex.: mkt@) que não batem com usuário Bitrix.
# Chave = e-mail em minúsculas -> ID do usuário Bitrix.
EMAILS_ESPECIAIS = {
    "mkt@synchro.com.br": "5405",      # "Mkt Marketing" -> CRM_Prospecção
}


def normalizar_nome(nome):
    """Normaliza um nome de proprietário para matching: remove underscores, a palavra
    "Desativado", acentos e espaços duplos; retorna em minúsculas."""
    s = str(nome or "")
    s = s.replace("_", "")
    s = re.sub(r"\bdesativado\b", " ", s, flags=re.IGNORECASE)
    s = " ".join(s.split())
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "user_cache.json")
_user_cache_lock = threading.Lock()
_MISSING = object()


def _load_user_cache():
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_user_cache():
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with _user_cache_lock:
            snapshot = dict(_user_cache)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        pass


_user_cache = _load_user_cache()
atexit.register(_save_user_cache)


def _cache_get(k):
    with _user_cache_lock:
        return _user_cache.get(k, _MISSING)


def _cache_set(k, v):
    with _user_cache_lock:
        _user_cache[k] = v


def _user_search(params):
    import migrador
    try:
        data = migrador.get_json("user.search", params)
    except Exception:
        return []
    return data.get("result", [])


def _resolve_nome(nome):
    """Resolve por nome (NOMES_ESPECIAIS + user.search com scoring de tokens)."""
    norm = normalizar_nome(nome)
    if norm in NOMES_ESPECIAIS:
        return NOMES_ESPECIAIS[norm]
    cached = _cache_get(norm)
    if cached is not _MISSING:
        return cached
    tokens = [t for t in norm.split() if len(t) >= 3]
    if not tokens:
        _cache_set(norm, None)
        return None
    res = _user_search({"FILTER[NAME]": norm})
    if not res:
        res = _user_search({"FILTER[NAME]": tokens[0]})
    scored = []
    for u in res:
        full = normalizar_nome((u.get("NAME") or "") + " " + (u.get("LAST_NAME") or ""))
        score = sum(1 for t in tokens if t in full)
        active = str(u.get("ACTIVE")).lower() in ("y", "true", "1")
        scored.append((score, active, -int(u.get("ID") or 0), u))
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    val = scored[0][3].get("ID") if scored and (scored[0][0] >= 2 or len(res) == 1) else None
    _cache_set(norm, val)
    return val


def resolver_usuario(email=None, nome=None):
    """Resolve um usuário Bitrix por e-mail (prioridade) ou por nome (com matching por
    tokens + NOMES_ESPECIAIS). Regra central usada por TODOS os migradores.

    Se o e-mail não resolver (ex.: e-mails genéricos como mkt@), cai no de-para por
    nome (NOMES_ESPECIAIS) antes de desistir — evita que o registro caia no dono
    padrão do webhook."""
    if email:
        email = str(email).strip().lower()
        cached = _cache_get(email)
        if cached is not _MISSING:
            return cached
        if email in EMAILS_ESPECIAIS:
            _cache_set(email, EMAILS_ESPECIAIS[email])
            return EMAILS_ESPECIAIS[email]
        res = _user_search({"FILTER[EMAIL]": email})
        val = res[0]["ID"] if res else None
        if val:
            _cache_set(email, val)
            return val
        # e-mail não resolveu -> tenta pelo nome (fallback)
        if nome:
            val = _resolve_nome(nome)
            _cache_set(email, val)
            return val
        _cache_set(email, None)
        return None
    if nome:
        return _resolve_nome(nome)
    return None


def montar_payload(oracle_obj, bitrix_ent, rec):
    de_para = DE_PARA.get(oracle_obj, [])
    payload = {}
    extras = {}
    for field_oracle, bitrix_field, transform in de_para:
        val = aplicar_transform(field_oracle, bitrix_field, transform, rec)
        if bitrix_field and val is not None and val != "":
            payload[bitrix_field] = val
        if transform == "sufixo" and rec.get("ProductTypeCode") in SUFIXO_PRODUTO:
            extras["_sufixo_produto"] = SUFIXO_PRODUTO[rec.get("ProductTypeCode")]
    return payload, extras
