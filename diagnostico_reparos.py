import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from oracle_client import OracleClient
from config import BITRIX_WEBHOOK, CAMPO_ID_ORACLE
from de_para_valores import norm, norm_loose
import mapeamento

WB = BITRIX_WEBHOOK.rstrip("/")
CAMPO_SETOR = "UF_CRM_1779455763"
ORACLE_ID_COMPANY = CAMPO_ID_ORACLE["company"]

o = OracleClient()

print("== LOV Setor (Oracle) ==", flush=True)
lov = o.get(
    "https://fa-evip-saasfaprod1.fa.ocs.oraclecloud.com/crmRestApi/resources/11.13.18.05",
    "/OrganizationProfile_LOV_For_Setor_c",
    params={"limit": 500, "onlyData": "true"},
)
lov_codes = {}
for i in lov.get("items", []):
    code = i.get("LookupCode")
    meaning = i.get("Meaning")
    lov_codes[code] = meaning
    print(f"  {code} = {meaning}", flush=True)

print("\n== Contas Oracle ==", flush=True)
contas = o.registros_todos(
    "accounts", fields=["PartyId", "OwnerName", "OwnerEmailAddress", "OrganizationDEO_Setor_c"]
)
print(f"total contas: {len(contas)}", flush=True)

# owners "marketing"
from collections import Counter
owner_mkt = []
for c in contas:
    nome = str(c.get("OwnerName") or "")
    email = str(c.get("OwnerEmailAddress") or "")
    if "marketing" in nome.lower() or "mkt" in nome.lower() or "marketing" in email.lower():
        owner_mkt.append(c)

print(f"\ncontas com owner 'marketing/mkt': {len(owner_mkt)}", flush=True)
print("OwnerName distintos:", flush=True)
for nome, n in Counter(str(c.get("OwnerName")) for c in owner_mkt).items():
    print(f"  '{nome}' x{n}", flush=True)
print("OwnerEmailAddress distintos:", flush=True)
for email, n in Counter(str(c.get("OwnerEmailAddress")) for c in owner_mkt).items():
    print(f"  '{email}' x{n}", flush=True)

# setor: contas com setor preenchido no Oracle
contas_com_setor = [c for c in contas if c.get("OrganizationDEO_Setor_c")]
print(f"\ncontas com Setor no Oracle: {len(contas_com_setor)}", flush=True)
print("Setor (codigo) distintos no Oracle:", flush=True)
for cod, n in Counter(str(c.get("OrganizationDEO_Setor_c")) for c in contas_com_setor).items():
    print(f"  {cod} ({lov_codes.get(cod, '?')}) x{n}", flush=True)

print("\n== Empresas Bitrix ==", flush=True)
empresas = {}
emp_by_id = {}
start = 0
while True:
    r = requests.get(f"{WB}/crm.company.list", params=[
        ("select[]", "ID"),
        ("select[]", "ASSIGNED_BY_ID"),
        ("select[]", ORACLE_ID_COMPANY),
        ("select[]", CAMPO_SETOR),
        ("start", start),
    ], timeout=120).json()
    res = r.get("result", [])
    for c in res:
        oid = c.get(ORACLE_ID_COMPANY)
        oid = oid.get("value") if isinstance(oid, dict) else oid
        if oid:
            empresas[str(oid)] = c
        emp_by_id[str(c["ID"])] = c
    if len(res) < 50:
        break
    start += 50
print(f"empresas com id oracle: {len(empresas)}", flush=True)

# cross: owner mkt -> bitrix owner
print("\n== Owner MKT: Bitrix ASSIGNED_BY_ID atual ==", flush=True)
owner_cur = Counter()
amostra = []
for c in owner_mkt:
    b = empresas.get(str(c.get("PartyId")))
    if b:
        owner_cur[b.get("ASSIGNED_BY_ID")] += 1
        if len(amostra) < 15:
            amostra.append((c.get("PartyId"), c.get("OwnerName"), c.get("OwnerEmailAddress"), b.get("ASSIGNED_BY_ID")))
for aid, n in owner_cur.items():
    print(f"  ASSIGNED_BY_ID={aid} x{n}", flush=True)
print("amostra (party, owner, email, assigned):", flush=True)
for a in amostra:
    print(f"  {a}", flush=True)

# setor: contas com setor no oracle mas vazio no bitrix
print("\n== Setor: contas com setor Oracle mas vazio no Bitrix ==", flush=True)
setor_vazio = 0
setor_nao_vazio = 0
setor_por_cod = Counter()
sem_empresa = 0
for c in contas_com_setor:
    b = empresas.get(str(c.get("PartyId")))
    if not b:
        sem_empresa += 1
        continue
    val = b.get(CAMPO_SETOR)
    val = val.get("value") if isinstance(val, dict) else val
    if val in (None, "", "0", 0):
        setor_vazio += 1
        setor_por_cod[c.get("OrganizationDEO_Setor_c")] += 1
    else:
        setor_nao_vazio += 1
print(f"  vazio (a corrigir): {setor_vazio}", flush=True)
print(f"  ja preenchido: {setor_nao_vazio}", flush=True)
print(f"  sem empresa bitrix: {sem_empresa}", flush=True)
for cod, n in setor_por_cod.items():
    print(f"  {cod} ({lov_codes.get(cod, '?')}) x{n}", flush=True)

# Bitrix Setor items
print("\n== Itens do enum Setor (Bitrix) ==", flush=True)
from de_para_valores import load as dload
bf = dload("bitrix_company_fields.json")
items = bf[CAMPO_SETOR]["items"]
bitrix_items = [(str(i["ID"]), i["VALUE"]) for i in items]
for iid, val in bitrix_items:
    print(f"  {iid} = {val}", flush=True)

# fuzzy match oracle meaning -> bitrix label
print("\n== Fuzzy match (Setor) ==", flush=True)
def fuzzy(m, labels):
    m = norm_loose(m)
    for iid, val in labels:
        if norm_loose(val) == m:
            return iid
    for iid, val in labels:
        if norm_loose(val) in m or m in norm_loose(val):
            return iid
    return None

for cod, meaning in lov_codes.items():
    m = fuzzy(meaning, bitrix_items)
    print(f"  {cod} ({meaning}) -> {m}", flush=True)
