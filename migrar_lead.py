import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, CAMPO_ID_ORACLE, ORACLE_CRM_BASE
import mapeamento
import migrador
import contatos as contatos_mod
from de_para_valores import build_value_mapping, norm, norm_loose, oracle_lov_values, load as _load_data
from oracle_client import OracleClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WB = BITRIX_WEBHOOK.rstrip("/")

# Campos "crm"/"employee" do lead que exigem tratamento especial.
CAMPO_CONTATO_PRINCIPAL = "UF_CRM_1779736432"   # "Nome do contato principal" (crm CONTACT)
CAMPO_PARCEIRO_PRINCIPAL = "UF_CRM_1779734915"  # "Parceiro Principal" (item1196)
CAMPO_AGENTE_PROSPECCAO = "UF_CRM_1779733909"   # "Agente de prospecção" (employee)
SEGMENTO_FIELD = "UF_CRM_1779737419145"         # "Segmento" (string) - LOV em cascata (depende de Setor_c)
CAMPO_DESCRICAO_OPPORTUNIDADE = "UF_CRM_1779737212811"  # "Descrição da oportunidade" (CLOB)
CAMPO_DESCRICAO_LEAD = "UF_CRM_1779737221467"          # "Descrição do lead" (CLOB)

_user_cache = {}
_contato_cache = {}
_parceiro_cache = {}
_owner_email_cache = {}
_contato_lock = threading.Lock()


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def bitrix(method, fields=None, extra=None, multi=None):
    params = {}
    if fields:
        for k, v in fields.items():
            if isinstance(v, (list, tuple)):
                params[f"fields[{k}][]"] = v
            else:
                params[f"fields[{k}]"] = v
    if multi:
        for k, vals in multi.items():
            for i, item in enumerate(vals):
                params[f"fields[{k}][{i}][VALUE]"] = item.get("VALUE", "")
                params[f"fields[{k}][{i}][VALUE_TYPE]"] = item.get("VALUE_TYPE", "WORK")
    if extra:
        params.update(extra)
    r = requests.post(f"{WB}/{method}", data=params, timeout=120)
    return r.json()


_label_map_cache = None
_segmento_map_cache = {}


def segmento_map(o, lead_id, setor):
    """Mapa de rótulos do Segmento (LOV cascata, depende de Setor_c). Cache por Setor_c."""
    key = setor or ""
    if key in _segmento_map_cache:
        return _segmento_map_cache[key]
    m = {}
    if setor:
        try:
            url = f"{ORACLE_CRM_BASE}/leads/{lead_id}/lov/LOVVA_For_Segmento_c"
            resp = requests.get(url, auth=o.auth, verify=False, params={"onlyData": "true", "limit": 300}, timeout=120)
            if resp.status_code == 200:
                for i in resp.json().get("items", []):
                    c = i.get("LookupCode")
                    if c:
                        m[c] = i.get("Meaning")
        except Exception as e:
            print(f"    !! segmento_map {lead_id}: {e}")
    _segmento_map_cache[key] = m
    return m


def buscar_clob_lead(o, lead_id, attr):
    """Lê CLOB/attachment do lead via enclosure (LeadId)."""
    url = f"{ORACLE_CRM_BASE}/leads/{lead_id}/enclosure/{attr}"
    resp = requests.get(url, auth=o.auth, verify=False, timeout=120,
                        headers={"Accept": "text/plain, application/octet-stream, */*"})
    if resp.status_code != 200:
        return None
    return resp.text


def resolver_owner_email(o, owner_id):
    """Resolve o e-mail do owner (recurso Oracle) pelo OwnerId."""
    if not owner_id:
        return None
    if owner_id in _owner_email_cache:
        return _owner_email_cache[owner_id]
    try:
        data = o.get(ORACLE_CRM_BASE, "/resources", params={"q": f"PartyId={owner_id}", "limit": 5, "onlyData": "true"})
        items = data.get("items", [])
        val = items[0].get("EmailAddress") or items[0].get("PrimaryEmailAddress") if items else None
    except Exception as e:
        print(f"    !! resolver_owner_email {owner_id}: {e}")
        val = None
    _owner_email_cache[owner_id] = val
    return val


def build_label_map():
    """De-para de RÓTULOS (código Oracle -> Meaning) para campos 'choice' do Oracle
    que no Bitrix são 'string' (o de-para automático só cobre enum)."""
    global _label_map_cache
    if _label_map_cache is not None:
        return _label_map_cache
    describe = load("oracle_leads_describe.json")
    bf = load("bitrix_lead_fields.json")
    dp = load("de_para_completo.json")["leads"]["campos"]
    attrs = {a["name"]: a for a in describe["Resources"]["leads"]["attributes"]}
    mapping = {}
    for c in dp:
        if not c["bitrix"] or c["confianca"] != "Alto":
            continue
        bfield = c["bitrix"]
        if (bf.get(bfield) or {}).get("type") != "string":
            continue
        if c["oracle"] == "Segmento_c":  # LOV cascata (depende de Setor_c) - tratar à parte
            continue
        oattr = attrs.get(c["oracle"])
        if not oattr:
            continue
        ovals = oracle_lov_values(oattr)
        if not ovals:
            continue
        mapping[bfield] = {o["code"]: o["meaning"] for o in ovals}
    _label_map_cache = mapping
    return mapping


def buscar_usuario(email, nome=None):
    return mapeamento.resolver_usuario(email, nome)


def build_payload(rec, val_map, label_map=None):
    """Mapeia os campos simples (string/enum/date/boolean) do lead."""
    dp = load("de_para_completo.json")["leads"]["campos"]
    bf = load("bitrix_lead_fields.json")
    label_map = label_map or {}
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
        val = mapeamento.aplicar_transform(c["oracle"], bfield, c.get("transform"), rec)
        if val in (None, ""):
            continue
        if btype == "enumeration" and bfield in val_map:
            de_para = val_map[bfield]["de_para"]
            if bmeta.get("isMultiple"):
                itens = []
                for code in str(val).split(","):
                    code = code.strip()
                    item = de_para.get(code)
                    if item and item not in itens:
                        itens.append(item)
                val = itens if itens else None
            else:
                if str(val) not in de_para:
                    print(f"    !! enum sem de-para: {bfield} <- {val!r}")
                val = de_para.get(str(val), val)
            if val in (None, ""):
                continue
        elif btype == "string" and bfield in label_map:
            val = label_map[bfield].get(str(val), val)
        if btype == "boolean":
            val = "1" if val in (True, "true", "True", "Y", "y", 1, "1") else "0"
        payload[bfield] = val
    return payload


def id_contato_cached(party_id):
    if party_id is None:
        return None
    if party_id in _contato_cache:
        return _contato_cache[party_id]
    bid = migrador.find_existing("contact", party_id)
    _contato_cache[party_id] = bid
    return bid


def criar_contato(o, party_id, company_id):
    """Cria o crm.contact do contato principal do lead, se ainda não existir."""
    if party_id is None:
        return None
    existente = id_contato_cached(party_id)
    if existente:
        return existente

    with _contato_lock:
        existente = id_contato_cached(party_id)
        if existente:
            return existente

        data = o.registros("contacts", limit=1, q=f"PartyId={party_id}")
        items = data.get("items", [])
        ct = items[0] if items else None
        if not ct:
            return None

        first, last = contatos_mod.nome_contato(ct)
        if not first and not last:
            return None

        pc = {
            "NAME": first,
            "LAST_NAME": last,
            "ORIGINATOR_ID": "oracle",
            "ORIGIN_ID": str(ct.get("PartyId")),
            "COMPANY_ID": company_id,
        }
        owner_id = buscar_usuario(ct.get("OwnerEmailAddress"), ct.get("OwnerName"))
        if owner_id:
            pc["ASSIGNED_BY_ID"] = owner_id
        multi = {}
        if ct.get("EmailAddress"):
            multi["EMAIL"] = [{"VALUE": ct["EmailAddress"], "VALUE_TYPE": "WORK"}]
        phone = ct.get("RawPhoneNumber") or ct.get("OverallPrimaryFormattedPhoneNumber")
        if phone:
            multi["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]
        r = bitrix("crm.contact.add", fields=pc, multi=multi)
        new_id = r.get("result")
        if new_id:
            _contato_cache[party_id] = new_id
            print(f"    contato criado {new_id} | {ct.get('ContactName')}")
        else:
            print(f"    !! erro ao criar contato {party_id}: {r}")
        return new_id


def buscar_parceiro(nome):
    """Busca o item1196 (parceiro) por nome/título."""
    if not nome:
        return None
    key = norm(nome)
    if key in _parceiro_cache:
        return _parceiro_cache[key]
    val = None
    start = 0
    while True:
        r = requests.get(f"{WB}/crm.item.list", params=[
            ("entityTypeId", 1196), ("select[]", "id"), ("select[]", "title"), ("start", start)], timeout=120)
        items = (r.json().get("result") or {}).get("items", [])
        for it in items:
            if norm(it.get("title", "")) == key:
                val = it["id"]
                break
        if val is not None or len(items) < 50:
            break
        start += 50
    _parceiro_cache[key] = val
    return val


def migrar_lead_rec(o, rec, val_map, empresas, label_map=None, incluir_descricao=True):
    """Monta o payload (dict) de um único lead."""
    lead_id = rec.get("LeadId")
    label_map = dict(label_map or {})
    seg = segmento_map(o, lead_id, rec.get("Setor_c"))
    if seg:
        label_map[SEGMENTO_FIELD] = seg
    payload = build_payload(rec, val_map, label_map)

    payload["TITLE"] = rec.get("Name") or ""
    status_lead = rec.get("StatusLead_c")
    if status_lead:
        payload["STATUS_ID"] = mapeamento.lead_status_c(status_lead)
    else:
        payload["STATUS_ID"] = mapeamento.lead_status(rec.get("StatusCode"))
    payload["ORIGINATOR_ID"] = "oracle"
    payload["ORIGIN_ID"] = str(lead_id)
    payload[CAMPO_ID_ORACLE["lead"]] = str(lead_id)

    # ---- empresa (CustomerId -> company) ----
    customer_id = rec.get("CustomerId")
    company_id = None
    if customer_id:
        company_id = (empresas or {}).get(str(customer_id))
        if not company_id:
            company_id = migrador.find_existing("company", customer_id)
        if company_id:
            payload["COMPANY_ID"] = company_id
        else:
            print(f"    !! empresa {rec.get('CustomerPartyName')} ({customer_id}) ainda nao migrada")

    # ---- proprietario (OwnerId -> resources -> email -> user) ----
    owner = buscar_usuario(resolver_owner_email(o, rec.get("OwnerId")), rec.get("OwnerPartyName"))
    if owner:
        payload["ASSIGNED_BY_ID"] = owner
    else:
        print(f"    !! owner nao encontrado: {rec.get('OwnerPartyName')!r}")

    # ---- agente de prospeccao (employee) ----
    agente = rec.get("AgenteProspeccao_c")
    if agente:
        uid = buscar_usuario(None, agente)
        if uid:
            payload[CAMPO_AGENTE_PROSPECCAO] = uid
        else:
            print(f"    !! agente de prospeccao nao encontrado: {agente!r}")

    # ---- contato principal (PrimaryContactId -> crm.contact) ----
    primary_contact = rec.get("PrimaryContactId")
    if primary_contact:
        cid = criar_contato(o, primary_contact, company_id)
        if cid:
            payload[CAMPO_CONTATO_PRINCIPAL] = cid

    # ---- parceiro principal (PartnerId -> item1196; fallback por nome) ----
    parceiro_nome = rec.get("NomeDoParceiroLead_c") or rec.get("PartnerPartyName")
    pid = None
    if rec.get("PartnerId"):
        pid = migrador.find_existing("item1196", rec.get("PartnerId"))
    if not pid and parceiro_nome:
        pid = buscar_parceiro(parceiro_nome)
    if pid:
        payload[CAMPO_PARCEIRO_PRINCIPAL] = pid
    elif parceiro_nome:
        print(f"    !! parceiro nao encontrado: {parceiro_nome!r}")

    # ---- descricoes (CLOB via enclosure) ----
    if incluir_descricao:
        for attr, campo in (("DescricaoOportunidade_c", CAMPO_DESCRICAO_OPPORTUNIDADE),
                            ("DescricaoLead_c", CAMPO_DESCRICAO_LEAD)):
            clob = buscar_clob_lead(o, lead_id, attr)
            if clob and clob.strip():
                payload[campo] = clob

    return payload


def carregar_empresas():
    mapa = {}
    start = 0
    while True:
        r = requests.get(f"{WB}/crm.company.list", params=[
            ("select[]", "ID"), ("select[]", CAMPO_ID_ORACLE["company"]), ("start", start)], timeout=120)
        res = r.json().get("result", [])
        for c in res:
            v = c.get(CAMPO_ID_ORACLE["company"])
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(c["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def migrar_leads_por_conta(o, val_map, empresas, account_id, label_map=None):
    data = o.registros_todos("leads", q=f"CustomerId={account_id}")
    print(f"Conta {account_id}: {len(data)} leads")
    criados = atualizados = erros = 0
    for rec in data:
        lead_id = rec.get("LeadId")
        if lead_id is None:
            continue
        try:
            bid, acao = migrar_lead_rec(o, rec, val_map, empresas, label_map)
            if acao == "created":
                criados += 1
            else:
                atualizados += 1
            print(f"  {lead_id} -> lead {bid} ({acao}) | {rec.get('Name')}")
        except Exception as e:
            erros += 1
            print(f"  ERRO {lead_id} {rec.get('Name')}: {e}", flush=True)
    return criados, atualizados, erros


def _lead_fields():
    dp = load("de_para_completo.json")["leads"]["campos"]
    campos = {c["oracle"] for c in dp if c.get("oracle") and c.get("confianca") == "Alto"}
    campos.update([
        "CustomerId", "CustomerPartyName", "OwnerId", "OwnerPartyName",
        "PrimaryContactId", "PartnerId", "PartnerPartyName",
        "NomeDoParceiroLead_c", "StatusLead_c",
    ])
    return sorted(campos)


def sincronizar():
    o = OracleClient()
    val_map = build_value_mapping("leads", "lead")
    label_map = build_label_map()
    empresas = carregar_empresas()
    print(f"Empresas migradas: {len(empresas)}")

    incluir_descricao = "--descricao" in sys.argv
    limite = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--limite" and i + 1 < len(args):
            try:
                limite = int(args[i + 1])
            except ValueError:
                pass
        elif a.startswith("--limite="):
            try:
                limite = int(a.split("=", 1)[1])
            except ValueError:
                pass

    leads = o.registros_todos("leads", fields=_lead_fields())
    print(f"{len(leads)} leads no Oracle")

    existentes = migrador.carregar_existentes_crm("crm.lead.list", CAMPO_ID_ORACLE["lead"])
    print(f"  {len(existentes)} ja existentes")

    leads = [r for r in leads if r.get("LeadId") is not None]
    leads.sort(key=lambda r: 0 if str(r["LeadId"]) in existentes else 1)
    if limite:
        leads = leads[:limite]
        print(f"  limite aplicado: {len(leads)} leads")
    print(f"  incluir descricao (CLOB): {incluir_descricao}")

    print("Montando payloads (paralelo)...", flush=True)
    dados = [None] * len(leads)
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(migrar_lead_rec, o, rec, val_map, empresas, label_map, incluir_descricao): i
               for i, rec in enumerate(leads)}
        done = 0
        for f in as_completed(fut):
            i = fut[f]
            try:
                dados[i] = f.result()
            except Exception as e:
                print(f"  !! payload lead {leads[i].get('LeadId')}: {e}", flush=True)
            done += 1
            if done % 500 == 0:
                print(f"  ... {done}/{len(leads)} payloads", flush=True)

    comandos = []
    metas = []
    for rec, payload in zip(leads, dados):
        if payload is None:
            continue
        lead_id = rec.get("LeadId")
        existing = existentes.get(str(lead_id))
        if existing:
            comandos.append(migrador.command("crm.lead.update", fields=payload, extra={"id": existing}))
            metas.append((lead_id, rec.get("Name"), "updated"))
        else:
            comandos.append(migrador.command("crm.lead.add", fields=payload))
            metas.append((lead_id, rec.get("Name"), "created"))

    criados = atualizados = erros = 0
    res, errs = migrador.batch(comandos)
    for (lead_id, name, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {lead_id} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"leads: criados={criados} atualizados={atualizados} erros={erros}")
    return criados, atualizados, erros


def main():
    sincronizar()


if __name__ == "__main__":
    main()
