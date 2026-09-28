import os

from oracle_client import OracleClient
import migrar_conta
import migrar_oportunidade
import migrar_lead

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MONDELEZ_PARTY_ID = 300000016326873


def load(name):
    import json
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    o = OracleClient()

    # 1) Conta + contatos
    conta = load("conta_mondelez.json")
    print("=" * 70)
    print("1) CONTA + CONTATOS:", conta.get("OrganizationName"))
    print("=" * 70)
    company_id = migrar_conta.migrar_conta(conta)
    print("company_id Bitrix:", company_id)

    # 2) Oportunidades
    print()
    print("=" * 70)
    print("2) OPORTUNIDADES")
    print("=" * 70)
    val_opp = migrar_oportunidade.build_value_mapping("opportunities", "deal")
    empresas = migrar_oportunidade.carregar_empresas()
    produtos_bitrix = migrar_oportunidade.carregar_produtos_bitrix()
    opps = o.registros_todos("opportunities", q=f"TargetPartyId={MONDELEZ_PARTY_ID}")
    print(f"{len(opps)} oportunidades")
    cri = upd = err = 0
    for rec in opps:
        opty_id = rec.get("OptyId")
        if opty_id is None:
            continue
        try:
            bid, acao = migrar_oportunidade.migrar_oportunidade_rec(
                o, rec, val_opp, empresas, produtos_bitrix, company_id)
            if acao == "created":
                cri += 1
            else:
                upd += 1
            print(f"  {opty_id} -> deal {bid} ({acao}) | {rec.get('Name')}")
        except Exception as e:
            err += 1
            print(f"  ERRO {opty_id} {rec.get('Name')}: {e}", flush=True)
    print(f"oportunidades: criadas={cri} atualizadas={upd} erros={err}")

    # 3) Leads
    print()
    print("=" * 70)
    print("3) LEADS")
    print("=" * 70)
    val_lead = migrar_lead.build_value_mapping("leads", "lead")
    label_map = migrar_lead.build_label_map()
    empresas_lead = migrar_lead.carregar_empresas()
    cri, upd, err = migrar_lead.migrar_leads_por_conta(o, val_lead, empresas_lead, MONDELEZ_PARTY_ID, label_map)
    print(f"leads: criados={cri} atualizados={upd} erros={err}")


if __name__ == "__main__":
    main()
