import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import BITRIX_WEBHOOK, ENTIDADE_ITEM, CAMPO_ID_ORACLE, ORACLE_CRM_BASE
from oracle_client import OracleClient
import mapeamento
import migrador

WB = BITRIX_WEBHOOK.rstrip("/")
EID = ENTIDADE_ITEM["item1204"]
ID_FIELD = CAMPO_ID_ORACLE["item1204"]  # ufCrm73_1789386148200

CAMPO_DATA_INICIO = "ufCrm73_1779970143723"
CAMPO_DATA_FIM = "ufCrm73_1779970152890"

_user_cache = {}


def buscar_usuario(email, nome=None):
    return mapeamento.resolver_usuario(email, nome)


def resolver_owner_email(o, resource_profile_id):
    """Resolve o e-mail do owner (recurso Oracle) pelo OraZcxOwner_Id_c (ResourceProfileId)."""
    if not resource_profile_id:
        return None
    try:
        data = o.get(ORACLE_CRM_BASE, "/resources",
                     params={"q": f"ResourceProfileId={resource_profile_id}", "limit": 5, "onlyData": "true"})
        items = data.get("items", [])
        if items:
            return items[0].get("EmailAddress") or items[0].get("PrimaryEmailAddress")
    except Exception as e:
        print(f"    !! resolver_owner_email {resource_profile_id}: {e}")
    return None


def montar_payload(rec, o):
    p = {}
    p["title"] = rec.get("RecordName") or f"Campanha {rec.get('RecordNumber')}"
    p[ID_FIELD] = str(rec.get("Id"))

    if rec.get("DataInicio_c"):
        p[CAMPO_DATA_INICIO] = mapeamento.normalizar_data(rec["DataInicio_c"])
    if rec.get("DataFim_c"):
        p[CAMPO_DATA_FIM] = mapeamento.normalizar_data(rec["DataFim_c"])

    email = resolver_owner_email(o, rec.get("OraZcxOwner_Id_c"))
    owner = buscar_usuario(email, rec.get("OraZcxOwner_c"))
    if owner:
        p["assignedById"] = owner
    else:
        print(f"    !! owner nao encontrado: {rec.get('OraZcxOwner_c')!r}")

    return p


def main():
    o = OracleClient()
    campanhas = o.registros_todos("CriarCampanha_c")
    print(f"{len(campanhas)} campanhas")

    existentes = migrador.carregar_existentes_item("item1204")
    print(f"  {len(existentes)} ja existentes")

    comandos = []
    metas = []
    for rec in campanhas:
        cid = rec.get("Id")
        if cid is None:
            continue
        payload = montar_payload(rec, o)
        existing = existentes.get(str(cid))
        if existing:
            comandos.append(migrador.command("crm.item.update", fields=payload, extra={"entityTypeId": EID, "id": existing}))
            metas.append((cid, rec.get("RecordName"), "updated"))
        else:
            comandos.append(migrador.command("crm.item.add", fields=payload, extra={"entityTypeId": EID}))
            metas.append((cid, rec.get("RecordName"), "created"))

    criados = atualizados = erros = 0
    res, errs = migrador.batch(comandos)
    for (cid, name, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {cid} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"criados={criados} atualizados={atualizados} erros={erros}")


if __name__ == "__main__":
    main()
