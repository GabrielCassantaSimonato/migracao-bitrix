import os
import subprocess
import sys


def main():
    with open("data/webhooks.txt", encoding="utf-8") as f:
        webhooks = [l.strip() for l in f if l.strip()]
    n = len(webhooks)

    procs = []
    for i, wb in enumerate(webhooks):
        env = dict(os.environ)
        env["BITRIX_WEBHOOK"] = wb
        env["PYTHONUNBUFFERED"] = "1"
        env["BITRIX_TOTAL_SHARDS"] = str(n)
        out = open(f"logs/migrar_oportunidade_shard{i}.out.log", "w", encoding="utf-8")
        err = open(f"logs/migrar_oportunidade_shard{i}.err.log", "w", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, "migrar_oportunidade.py", "--shard", str(i), str(n), "--contatos"],
            env=env, stdout=out, stderr=err,
        )
        procs.append((i, p))
        print(f"shard {i}/{n} -> PID {p.pid}", flush=True)

    for i, p in procs:
        rc = p.wait()
        print(f"shard {i} saiu com codigo {rc}", flush=True)
    print("FIM: todos os shards finalizados", flush=True)


if __name__ == "__main__":
    main()
