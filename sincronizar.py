import os
import sys
import datetime
import traceback

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class Tee:
    """Espelha a saída para o console e para um arquivo de log."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"sync_{ts}.log")
    logf = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, logf)
    sys.stderr = Tee(sys.__stderr__, logf)

    import migrar_produto
    import migrar_conta
    import migrar_parceiro
    import migrar_oportunidade
    import migrar_campanha
    import migrar_campanha_synchro
    import migrar_spv
    import migrar_parcela
    import migrar_atividade
    import migrar_lead

    etapas = [
        ("PRODUTOS", migrar_produto.main),
        ("EMPRESAS + CONTATOS", migrar_conta.sincronizar),
        ("PARCEIROS", migrar_parceiro.main),
        ("NEGÓCIOS", migrar_oportunidade.main),
        ("CRIAR CAMPANHA", migrar_campanha.main),
        ("CAMPANHAS SYNCHRO", migrar_campanha_synchro.main),
        ("SPV", migrar_spv.main),
        ("PARCELAS", migrar_parcela.main),
        ("ATIVIDADES", migrar_atividade.sincronizar),
        ("LEADS", migrar_lead.sincronizar),
    ]

    inicio = datetime.datetime.now()
    print("=" * 70)
    print(f"SINCRONIZAÇÃO INICIADA {inicio.isoformat()}")
    print("=" * 70)

    for nome, fn in etapas:
        print(f"\n{'#' * 70}\n# {nome}\n{'#' * 70}", flush=True)
        t0 = datetime.datetime.now()
        try:
            fn()
            status = "OK"
        except Exception as e:
            status = f"ERRO: {e}"
            traceback.print_exc()
        t1 = datetime.datetime.now()
        print(f"\n[{nome}] {status} em {(t1 - t0).total_seconds():.1f}s", flush=True)

    fim = datetime.datetime.now()
    print("\n" + "=" * 70)
    print(f"SINCRONIZAÇÃO FINALIZADA {fim.isoformat()} (duração {(fim - inicio).total_seconds()/60:.1f} min)")
    print(f"Log: {log_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
