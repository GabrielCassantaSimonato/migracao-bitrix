"""Separação de contato principal vs demais contatos (Oracle -> Bitrix).

Também centraliza a construção do payload do crm.contact (usado pela migração de
contas e, sob demanda, pela migração de oportunidades quando um contato referenciado
ainda não existe no Bitrix).
"""

import json
import os
import re

import mapeamento
import migrador
from oracle_client import OracleClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Pronome de Tratamento (Oracle) -> HONORIFIC (Bitrix crm_status)
PRONOME_TRATAMENTO_MAP = {
    "SR": "1",            # Sr.
    "SRA": "2",           # Sra.
    "DR": "3",            # Dr.
    "DRA": "4",           # Dra.
    "PROF": "UC_CK82YN",  # Prof.
    "PROFA": "UC_14ZWH9",  # Profa.
}

CAMPO_CONTATO_DESCRICAO = "UF_CRM_1779911104902"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_valido(email):
    """True se o e-mail tem formato válido (evita erro do Bitrix no crm.contact.add)."""
    return bool(email) and bool(_EMAIL_RE.match(str(email).strip()))


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def separar_conta(account_rec, contatos):
    """Conta: principal = PrimaryContactPartyId; demais = o restante."""
    return _separar(contatos, account_rec.get("PrimaryContactPartyId"))


def separar_negocio(opp_rec, contatos):
    """Negócio: principal = KeyContactId; demais = o restante."""
    return _separar(contatos, opp_rec.get("KeyContactId"))


def _separar(contatos, primary_party_id):
    primary = None
    outros = []
    for ct in contatos:
        if primary_party_id is not None and str(ct.get("PartyId")) == str(primary_party_id):
            primary = ct
        else:
            outros.append(ct)
    return primary, outros


def buscar_contatos_conta(account_party_id, limit=100):
    """Baixa os contatos Oracle cujo AccountPartyId = account_party_id."""
    o = OracleClient()
    data = o.registros_todos("contacts", q=f"AccountPartyId={account_party_id}")
    return data


def buscar_contato_oracle(party_id):
    """Baixa o registro Oracle do contato pelo PartyId (ou None)."""
    if party_id is None:
        return None
    o = OracleClient()
    data = o.registros("contacts", limit=1, q=f"PartyId={party_id}")
    items = data.get("items", [])
    return items[0] if items else None


def buscar_descricao_empresa(party_number):
    """Lê o CLOB 'Descrição da empresa' via enclosure (PartyNumber, não PartyId)."""
    if not party_number:
        return None
    o = OracleClient()
    return o.enclosure("accounts", party_number, "OrganizationDEO_DescricaDaEmpresa_c")


def buscar_descricao_contato(party_number):
    """Lê o CLOB 'Descrição' do contato via enclosure (PartyNumber)."""
    if not party_number:
        return None
    o = OracleClient()
    return o.enclosure("contacts", party_number, "PersonDEO_DESCRICAO_c")


def buscar_historico_conquista(party_number):
    """Lê o CLOB 'Histórico de conquista da conta' via enclosure (PartyNumber)."""
    if not party_number:
        return None
    o = OracleClient()
    return o.enclosure("accounts", party_number, "OrganizationDEO_HistoricoDeConquistaDaConta_c")


def id_bitrix_contato(party_id):
    """ID Bitrix do contato com esse PartyId Oracle (ou None)."""
    if party_id is None:
        return None
    return migrador.find_existing("contact", party_id)


def ids_bitrix_contatos(contatos):
    out = []
    for ct in contatos:
        bid = id_bitrix_contato(ct.get("PartyId"))
        if bid:
            out.append(bid)
    return out


def build_payload_contato(ct, val_ct):
    """De-para dos campos simples do contato (mesma lógica do build_payload do migrar_conta)."""
    dp = load("de_para_completo.json")["contacts"]["campos"]
    bf = load("bitrix_contact_fields.json")
    payload = {}
    for c in dp:
        if not c["bitrix"] or c["confianca"] != "Alto":
            continue
        bfield = c["bitrix"]
        bmeta = bf.get(bfield) or {}
        btype = bmeta.get("type", "")
        if btype in ("crm", "employee", "file", "crm_multifield"):
            continue
        if c.get("transform") in ("fk", "fk_company"):
            continue
        val = mapeamento.aplicar_transform(c["oracle"], bfield, c.get("transform"), ct)
        if val in (None, ""):
            continue
        if btype == "enumeration" and bfield in val_ct:
            de_para = val_ct[bfield]["de_para"]
            if bmeta.get("isMultiple"):
                itens = []
                for code in str(val).split(","):
                    code = code.strip()
                    item = de_para.get(code)
                    if item and item not in itens:
                        itens.append(item)
                val = itens if itens else None
            else:
                val = de_para.get(str(val), val)
            if val in (None, ""):
                continue
        if btype == "boolean":
            val = "1" if val in (True, "true", "True", "Y", "y", 1, "1") else "0"
        payload[bfield] = val
    return payload


def montar_contato_payload(ct, val_ct, company_id=None, incluir_descricao=True):
    """Monta (payload, multi) de um crm.contact a partir do registro Oracle."""
    pc = build_payload_contato(ct, val_ct)
    pc["NAME"] = ct.get("FirstName")
    pc["LAST_NAME"] = ct.get("LastName")
    owner_id = mapeamento.resolver_usuario(ct.get("OwnerEmailAddress"), ct.get("OwnerName"))
    if owner_id:
        pc["ASSIGNED_BY_ID"] = owner_id
    pronome = PRONOME_TRATAMENTO_MAP.get(ct.get("PersonDEO_PRONOME_TRATAMENTO_c"))
    if pronome:
        pc["HONORIFIC"] = pronome
    if incluir_descricao:
        desc = buscar_descricao_contato(ct.get("PartyNumber"))
        if desc:
            pc[CAMPO_CONTATO_DESCRICAO] = desc
    pc["ORIGINATOR_ID"] = "oracle"
    pc["ORIGIN_ID"] = str(ct.get("PartyId"))
    if company_id:
        pc["COMPANY_ID"] = company_id
    multi = {}
    email = ct.get("EmailAddress")
    if email and email_valido(email):
        multi["EMAIL"] = [{"VALUE": email, "VALUE_TYPE": "WORK"}]
    phone = ct.get("RawPhoneNumber") or ct.get("OverallPrimaryFormattedPhoneNumber")
    if phone:
        multi["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]
    return pc, multi
