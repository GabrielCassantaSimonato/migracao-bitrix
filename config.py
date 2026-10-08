import os


def _load_dotenv():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_load_dotenv()

ORACLE_USER = os.environ.get("ORACLE_USER", "")
ORACLE_PASS = os.environ.get("ORACLE_PASS", "")
ORACLE_CRM_BASE = "https://fa-evip-saasfaprod1.fa.ocs.oraclecloud.com/crmRestApi/resources/11.13.18.05"
ORACLE_FSCM_BASE = "https://fa-evip-saasfaprod1.fa.ocs.oraclecloud.com/fscmRestApi/resources/11.13.18.05"

BITRIX_WEBHOOK = os.environ.get("BITRIX_WEBHOOK", "")

OBJETOS = [
    ("opportunities", "deal"),
    ("accounts", "company"),
    ("contacts", "contact"),
    ("leads", "lead"),
    ("partners", "item1196"),
    ("serviceRequests", "item1216"),
    ("CampanhasSynchro_c", "item1208"),
    ("CriarCampanha_c", "item1204"),
    ("Parcelas_c", "item1212"),
    ("activities", "item1220"),
    ("products", "product"),
]

QUERY_FILTRO = {
    "opportunities": "OptyId>0",
}

ENTIDADE_ITEM = {
    "item1196": 1196,
    "item1216": 1216,
    "item1208": 1208,
    "item1204": 1204,
    "item1212": 1212,
    "item1220": 1220,
}

CAMPO_ID_ORACLE = {
    "deal": "UF_CRM_1789385750762",
    "lead": "UF_CRM_1789385673917",
    "company": "UF_CRM_1789041431158",
    "contact": "UF_CRM_1789041484569",
    "product": "PROPERTY_305",
    "item1196": "ufCrm69_1789386068257",
    "item1216": "ufCrm79_1789386308816",
    "item1208": "ufCrm75_1789386179872",
    "item1204": "ufCrm73_1789386148200",
    "item1212": "ufCrm77_1789386203592",
    "item1220": "ufCrm81_1789386385040",
}

# Campos de "contato principal" e "demais contatos" por entidade Bitrix.
CONTATO_PRINCIPAL = {
    "deal": "UF_CRM_1779800744",     # "Contato principal" (single, CONTACT)
    "company": "UF_CRM_1768849939",  # "Contato principal" (single)
}

DEMAIS_CONTATOS = {
    "deal": "UF_CRM_1789409590",     # "Demais contatos" (multiple, CONTACT)
    "company": "CONTACT_ID",         # "Demais contatos" (nativo, via COMPANY_ID do contato)
}

# Proprietário para registros cujo owner original não existe mais no Bitrix
# (usuário desligado). E-mail: crm.inativos@synchro.com.br
USUARIO_DESATIVADO = "5403"

# Propriedade (lista) de "Grupo de produto" no crm.product. Produtos usam
# crm.product.property.* (NÃO userfield). O campo real é PROPERTY_<id> (auto);
# o CODE abaixo é o identificador estável usado para localizar/criar a propriedade.
PRODUTO_GRUPO_CODE = "GRUPO_PRODUTO"
PRODUTO_GRUPOS = ["LU", "MA", "CL", "SE", "AMS", "SLA", "MONITORAMENTO", "ALL"]

# De-para do nome do grupo no Oracle para a label usada no Bitrix (campo "Grupo de
# produto"). Ex.: o grupo CLOUD do Oracle é exibido como "CL" no Bitrix.
PRODUTO_GRUPO_BITRIX = {
    "CLOUD": "CL",
}
