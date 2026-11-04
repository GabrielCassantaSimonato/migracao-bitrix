import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import CAMPO_ID_ORACLE, ENTIDADE_ITEM
import mapeamento
import migrador
from oracle_client import OracleClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

EID = ENTIDADE_ITEM["item1220"]
ID_FIELD = CAMPO_ID_ORACLE["item1220"]  # ufCrm81_1789386385040

CAMPO_DESCRICAO = "ufCrm81_1779994080130"      # "Descrição" (string)
CAMPO_OBJETIVO = "ufCrm81_1779993825451"       # "Objetivo" (string)
CAMPO_VENCIMENTO = "ufCrm81_1779993918286"     # "Data de vencimento" (date)
CAMPO_COMPROMISSO = "ufCrm81_1779993785877"    # "Compromisso do cliente" (boolean)
CAMPO_TIPO_OBJETIVO = "ufCrm81_1779993878245"  # "Tipo de objetivo" (enumeration)
CAMPO_PRIORIDADE = "ufCrm81_1779993997990"     # "Prioridade" (enumeration)
CAMPO_TRACAO = "ufCrm81_1779994010984"         # "Tração" (string)

# StatusCode (Oracle) -> stageId do item1220 (DYNAMIC_1220_STAGE_111)
ATIVIDADE_STAGE_MAP = {
    "NOT_STARTED": "DT1220_111:NEW",
    "CANCELED": "DT1220_111:UC_P8PT4C",
    "COMPLETE": "DT1220_111:PREPARATION",
    "IN_PROGRESS": "DT1220_111:UC_7X9ROH",
    "ON_HOLD": "DT1220_111:CLIENT",
    "WAITING": "DT1220_111:NEW",
}

# PriorityCode (1/2/3) -> itens do enum "Prioridade"
PRIORIDADE_MAP = {"1": "7581", "2": "7583", "3": "7585"}

# TipoDeObjetivo_c -> itens do enum "Tipo de objetivo"
TIPO_OBJETIVO_MAP = {
    "CONCLUSAO_FASE": "7575",
    "EVOLUCAO_DENTRO_FASE": "7577",
    "SEM_EVOLUCAO": "7579",
}

# Tracao_c (choice) -> "Tração" (string)
TRACAO_MAP = {
    "AMARELO": "Amarelo",
    "VERDE": "Verde",
    "VERMELHO": "Vermelho",
}


def buscar_usuario(email, nome=None):
    return mapeamento.resolver_usuario(email, nome)


def _carregar_mapa_paralelo(metodo, campo_oracle, workers=5):
    """{oracle_id -> bitrix_id} de uma entidade crm nativa, paginando em paralelo
    (o token bucket do migrador limita a ~5 req/s, mas esconde a latência de ~0.85s
    por página). O resultado é cacheado em disco (as entidades já estão migradas,
    então o mapa é estável entre execuções)."""
    cache_path = os.path.join(DATA_DIR, f"mapa_{metodo.replace('.', '_')}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    mapa = {}
    PAGE = 50

    def fetch(page):
        r = migrador.get_json(metodo, params=[
            ("select[]", "ID"), ("select[]", campo_oracle), ("start", page * PAGE)])
        return page, r.get("result", [])

    with ThreadPoolExecutor(max_workers=workers) as ex:
        page = 0
        while True:
            results = list(ex.map(fetch, range(page, page + workers)))
            short = False
            for _p, res in results:
                for c in res:
                    v = c.get(campo_oracle)
                    val = v.get("value") if isinstance(v, dict) else v
                    if val:
                        mapa[str(val)] = int(c["ID"])
                if len(res) < PAGE:
                    short = True
            if short:
                break
            page += workers

    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mapa, f)
    os.replace(tmp, cache_path)
    return mapa


def carregar_empresas():
    return _carregar_mapa_paralelo("crm.company.list", CAMPO_ID_ORACLE["company"])


def carregar_deals():
    return _carregar_mapa_paralelo("crm.deal.list", CAMPO_ID_ORACLE["deal"])


def carregar_contatos():
    return _carregar_mapa_paralelo("crm.contact.list", CAMPO_ID_ORACLE["contact"])


def carregar_leads():
    return _carregar_mapa_paralelo("crm.lead.list", CAMPO_ID_ORACLE["lead"])


def _activity_fields():
    return sorted([
        "ActivityId", "Subject", "AccountId", "LeadId", "OpportunityId",
        "PrimaryContactId", "OwnerEmailAddress", "OwnerName", "StatusCode",
        "ActivityPartialDescription", "Objetivo_c", "DueDate",
        "CompromissoDoCliente_c", "Tracao_c", "PriorityCode", "TipoDeObjetivo_c",
    ])


def montar_payload(rec, empresas, deals, contatos, leads):
    p = {}
    p["title"] = rec.get("Subject") or f"Atividade {rec.get('ActivityNumber')}"
    p[ID_FIELD] = str(rec.get("ActivityId"))

    # proprietario (OwnerEmailAddress vem no JSON)
    owner = buscar_usuario(rec.get("OwnerEmailAddress"), rec.get("OwnerName"))
    if owner:
        p["assignedById"] = owner
    else:
        print(f"    !! owner nao encontrado: {rec.get('OwnerName')!r}")

    # empresa (AccountId -> companyId)
    if rec.get("AccountId"):
        cid = empresas.get(str(rec.get("AccountId")))
        if cid:
            p["companyId"] = cid
        else:
            print(f"    !! empresa {rec.get('AccountId')} nao migrada")

    # oportunidade (OpportunityId -> parentId2/deal)
    if rec.get("OpportunityId"):
        did = deals.get(str(rec.get("OpportunityId")))
        if did:
            p["parentId2"] = did
        else:
            print(f"    !! oportunidade {rec.get('OpportunityId')} nao migrada")

    # lead (LeadId -> parentId1)
    if rec.get("LeadId"):
        lid = leads.get(str(rec.get("LeadId")))
        if lid:
            p["parentId1"] = lid
        else:
            print(f"    !! lead {rec.get('LeadId')} nao migrado")

    # contato (PrimaryContactId -> contactId)
    if rec.get("PrimaryContactId"):
        ctid = contatos.get(str(rec.get("PrimaryContactId")))
        if ctid:
            p["contactId"] = ctid

    # etapa (StatusCode -> stageId)
    st = rec.get("StatusCode")
    if st:
        p["stageId"] = ATIVIDADE_STAGE_MAP.get(st, "DT1220_111:NEW")

    # descricao / objetivo / vencimento
    if rec.get("ActivityPartialDescription"):
        p[CAMPO_DESCRICAO] = rec["ActivityPartialDescription"]
    if rec.get("Objetivo_c"):
        p[CAMPO_OBJETIVO] = rec["Objetivo_c"]
    if rec.get("DueDate"):
        p[CAMPO_VENCIMENTO] = mapeamento.normalizar_data(rec["DueDate"])

    # compromisso do cliente (boolean -> Y/N)
    comp = rec.get("CompromissoDoCliente_c")
    p[CAMPO_COMPROMISSO] = "Y" if comp in (True, "true", "True", "Y", "y", 1, "1") else "N"

    # tracao (choice -> string)
    if rec.get("Tracao_c"):
        p[CAMPO_TRACAO] = TRACAO_MAP.get(str(rec["Tracao_c"]), rec["Tracao_c"])

    # prioridade (enum)
    if rec.get("PriorityCode") is not None:
        p[CAMPO_PRIORIDADE] = PRIORIDADE_MAP.get(str(rec["PriorityCode"]), str(rec["PriorityCode"]))

    # tipo de objetivo (enum)
    if rec.get("TipoDeObjetivo_c"):
        p[CAMPO_TIPO_OBJETIVO] = TIPO_OBJETIVO_MAP.get(str(rec["TipoDeObjetivo_c"]), str(rec["TipoDeObjetivo_c"]))

    return p


def _parse_shard(argv):
    """Lê --shard <i> <N> da linha de comando; retorna (i, N) ou (None, None)."""
    shard = shards = None
    for k in range(len(argv) - 2):
        if argv[k] == "--shard":
            try:
                shard = int(argv[k + 1])
                shards = int(argv[k + 2])
            except ValueError:
                pass
    return shard, shards


def _ler_ids_erro(log_path):
    """Extrai os ActivityId das linhas 'ERRO <id> ...' de um log de migração."""
    ids = []
    with open(log_path, encoding="latin-1") as f:
        for line in f:
            if "ERRO" in line:
                m = re.search(r"ERRO (\d+)", line)
                if m:
                    ids.append(m.group(1))
    return ids


def sincronizar():
    limite = None
    reparar_erros = None  # None = não; False = log default; str = caminho do log
    desde = None  # YYYY-MM-DD (filtro LastUpdateDate >=)
    ate = None    # YYYY-MM-DD (filtro LastUpdateDate <=, aplicado localmente)
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
        elif a == "--reparar-erros":
            reparar_erros = False
        elif a.startswith("--reparar-erros="):
            reparar_erros = a.split("=", 1)[1]
        elif a == "--desde" and i + 1 < len(args):
            desde = args[i + 1]
        elif a.startswith("--desde="):
            desde = a.split("=", 1)[1]
        elif a == "--ate" and i + 1 < len(args):
            ate = args[i + 1]
        elif a.startswith("--ate="):
            ate = a.split("=", 1)[1]

    shard, shards = _parse_shard(sys.argv)

    o = OracleClient()
    print("carregando empresas...", flush=True)
    empresas = carregar_empresas()
    print(f"  empresas={len(empresas)}", flush=True)
    print("carregando deals...", flush=True)
    deals = carregar_deals()
    print(f"  deals={len(deals)}", flush=True)
    print("carregando contatos...", flush=True)
    contatos = carregar_contatos()
    print(f"  contatos={len(contatos)}", flush=True)
    print("carregando leads...", flush=True)
    leads = carregar_leads()
    print(f"  leads={len(leads)}", flush=True)
    existentes = migrador.carregar_existentes_item("item1220")
    print(f"existentes={len(existentes)}", flush=True)

    fields = _activity_fields()
    q = None
    if desde:
        q = f"LastUpdateDate>='{desde}'"
    if ate:
        # o Oracle não combina >= e <= no `q` (retorna tudo), então o limite
        # superior é aplicado localmente; para isso precisa buscar LastUpdateDate.
        fields = sorted(set(fields + ["LastUpdateDate"]))
    dados = o.registros_todos("activities", fields=fields, q=q)
    if ate:
        dados = [r for r in dados if (r.get("LastUpdateDate") or "")[:10] <= ate]
    if desde or ate:
        print(f"  janela: LastUpdateDate de {desde or '(inicio)'} ate {ate or '(hoje)'}", flush=True)
    print(f"{len(dados)} atividades no Oracle")

    dados = [r for r in dados if r.get("ActivityId") is not None]
    # modo reparo: só os ActivityId que falharam na carga anterior (linhas 'ERRO' do log)
    if reparar_erros is not None:
        if reparar_erros is False:
            reparar_erros = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "logs", "migrar_atividade_out.log")
        ids_erro = set(_ler_ids_erro(reparar_erros))
        dados = [r for r in dados if str(r.get("ActivityId")) in ids_erro]
        print(f"  (reparo) {len(ids_erro)} ids com ERRO no log -> {len(dados)} atividades", flush=True)
    # sharding determinístico por posição (o fetch do Oracle é estável, ordenado por
    # ActivityId), sem sobreposição entre os webhooks.
    if shard is not None and shards:
        dados = [r for idx, r in enumerate(dados) if idx % shards == shard]
        print(f"  shard {shard}/{shards}: {len(dados)} atividades", flush=True)
    if limite:
        dados = dados[:limite]
        print(f"  (teste) limitado a {limite} atividades", flush=True)

    print("Montando payloads (paralelo)...", flush=True)
    payloads = [None] * len(dados)
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(montar_payload, rec, empresas, deals, contatos, leads): i
               for i, rec in enumerate(dados)}
        done = 0
        for f in as_completed(fut):
            i = fut[f]
            try:
                payloads[i] = f.result()
            except Exception as e:
                print(f"  !! payload atividade {dados[i].get('ActivityId')}: {e}", flush=True)
            done += 1
            if done % 500 == 0:
                print(f"  ... {done}/{len(dados)} payloads", flush=True)

    comandos = []
    metas = []
    for rec, payload in zip(dados, payloads):
        if payload is None:
            continue
        act_id = rec.get("ActivityId")
        existing = existentes.get(str(act_id))
        if existing:
            comandos.append(migrador.command("crm.item.update", fields=payload,
                                             extra={"entityTypeId": EID, "id": existing}))
            metas.append((act_id, rec.get("Subject"), "updated"))
        else:
            comandos.append(migrador.command("crm.item.add", fields=payload,
                                             extra={"entityTypeId": EID}))
            metas.append((act_id, rec.get("Subject"), "created"))

    criados = atualizados = erros = 0
    res, errs = migrador.batch(comandos)
    for (act_id, name, action), r, e in zip(metas, res, errs):
        if e:
            erros += 1
            print(f"  ERRO {act_id} {name}: {e.get('error_description') or e.get('error')}", flush=True)
        elif action == "created":
            criados += 1
        else:
            atualizados += 1

    print("=" * 60)
    print(f"atividades: criados={criados} atualizados={atualizados} erros={erros}")
    return criados, atualizados, erros


def _campo_val(v):
    """Valor 'puro' de um campo (dict {'value': x} -> x)."""
    if isinstance(v, dict):
        return v.get("value")
    return v


def _data_parte(s):
    """Parte 'YYYY-MM-DD' de uma data/datetime (ou '' se vazio)."""
    if isinstance(s, str) and len(s) >= 10 and s[4] == "-" and s[:4].isdigit():
        return s[:10]
    return s or ""


def _items_do_resultado(res):
    """Extrai a lista de itens do resultado (list ou dict) de um crm.item.list em batch."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        if "items" in res:
            return res.get("items") or []
        inner = res.get("result")
        if isinstance(inner, dict):
            return inner.get("items") or []
        if isinstance(inner, list):
            return inner
    return []


def reparar_vencimento_tracao():
    """Repara 'Data de vencimento' e 'Tração' das atividades que divergem do Oracle.

    Só varre as atividades com LastUpdateDate recente (--dias N, default 120) no
    Oracle e busca no Bitrix apenas essas (filtro pelo ID Oracle), evitando ler as
    33 mil atividades. Atualiza SOMENTE os campos divergentes (limpando o campo
    quando o Oracle está vazio). Flags: --dias N, --dry-run, --limite N."""
    import argparse
    from datetime import datetime, timedelta, timezone

    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limite", type=int, default=None)
    args, _ = parser.parse_known_args()

    o = OracleClient()

    # 1. Oracle: só as atividades da janela (filtro no servidor)
    corte = (datetime.now(timezone.utc) - timedelta(days=args.dias)).strftime("%Y-%m-%dT%H:%M:%S")
    q = f"LastUpdateDate>='{corte}'"
    dados = o.registros_todos("activities", fields=["ActivityId", "Subject", "DueDate", "Tracao_c"], q=q)
    dados = [r for r in dados if r.get("ActivityId") is not None]
    print(f"{len(dados)} atividades no Oracle (ultimos {args.dias} dias)", flush=True)
    if args.limite:
        dados = dados[:args.limite]
        print(f"  (teste) limitado a {len(dados)}", flush=True)

    # 2. Bitrix: vencimento/tracao atuais dessas atividades (crm.item.list em batch)
    comandos = [
        ("crm.item.list", [
            ("entityTypeId", EID),
            (f"filter[{ID_FIELD}]", str(r["ActivityId"])),
            ("select[]", "id"),
            ("select[]", CAMPO_VENCIMENTO),
            ("select[]", CAMPO_TRACAO),
        ])
        for r in dados
    ]
    res, errs = migrador.batch(comandos, batch_size=50)

    # 3. compara e monta updates (só os campos divergentes)
    atualizacoes = []
    nao_migrado = 0
    for r, rr, e in zip(dados, res, errs):
        oid = r["ActivityId"]
        subject = r.get("Subject") or ""
        if e:
            print(f"  !! listar {oid}: {e.get('error_description') or e.get('error')}", flush=True)
            continue
        items = _items_do_resultado(rr)
        if not items or not items[0].get("id"):
            nao_migrado += 1
            continue
        it = items[0]
        bid = it["id"]
        bvenc = _campo_val(it.get(CAMPO_VENCIMENTO)) or ""
        btrac = _campo_val(it.get(CAMPO_TRACAO)) or ""

        od = mapeamento.normalizar_data(r.get("DueDate")) or ""
        ot = TRACAO_MAP.get(str(r["Tracao_c"]), r["Tracao_c"]) if r.get("Tracao_c") else ""

        campos = {}
        if _data_parte(bvenc) != od:
            campos[CAMPO_VENCIMENTO] = od
        if btrac != ot:
            campos[CAMPO_TRACAO] = ot
        if campos:
            atualizacoes.append((oid, subject, bid, campos))

    print(f"divergentes: {len(atualizacoes)} | nao_migrados: {nao_migrado}", flush=True)

    if args.dry_run:
        for oid, subject, bid, campos in atualizacoes[:50]:
            print(f"  [dry-run] {oid} {subject[:50]}: {campos}", flush=True)
        print(f"(dry-run) nada gravado; {len(atualizacoes)} atividades divergentes", flush=True)
        return len(atualizacoes)

    comandos_upd = [
        migrador.command("crm.item.update", fields=campos, extra={"entityTypeId": EID, "id": bid})
        for _oid, _subject, bid, campos in atualizacoes
    ]
    metas_upd = [(oid, subject, campos) for oid, subject, _bid, campos in atualizacoes]

    atualizados = erros = 0
    res2, errs2 = migrador.batch(comandos_upd, batch_size=50)
    for (oid, subject, campos), r2, e2 in zip(metas_upd, res2, errs2):
        if e2:
            erros += 1
            print(f"  ERRO {oid} {subject[:50]}: {e2.get('error_description') or e2.get('error')}", flush=True)
        else:
            atualizados += 1

    print("=" * 60)
    print(f"vencimento/tracao: atualizados={atualizados} erros={erros} | nao_migrados={nao_migrado}")
    return atualizados


def main():
    if "--vencimento-tracao" in sys.argv:
        reparar_vencimento_tracao()
    else:
        sincronizar()


if __name__ == "__main__":
    main()
