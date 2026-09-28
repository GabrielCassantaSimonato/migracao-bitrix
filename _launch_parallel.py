import os
import subprocess
import sys

WEBHOOKS_FILE = "data/webhooks.txt"


def main():
    if len(sys.argv) < 2:
        print("uso: python _launch_parallel.py <script> [args...] [--max N]", flush=True)
        sys.exit(1)

    script = sys.argv[1]
    extra = sys.argv[2:]

    # opcional: --max N limita a quantidade de webhooks/shards usados
    max_shards = None
    if "--max" in extra:
        idx = extra.index("--max")
        max_shards = int(extra[idx + 1])
        extra = extra[:idx] + extra[idx + 2:]

    with open(WEBHOOKS_FILE, encoding="utf-8") as f:
        webhooks = [l.strip() for l in f if l.strip()]

    if max_shards is not None:
        webhooks = webhooks[:max_shards]

    n = len(webhooks)
    if n == 0:
        print(f"nenhum webhook em {WEBHOOKS_FILE}", flush=True)
        sys.exit(1)

    base = os.path.splitext(os.path.basename(script))[0]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    for i, wb in enumerate(webhooks):
        env = dict(os.environ)
        env["BITRIX_WEBHOOK"] = wb
        env["BITRIX_TOTAL_SHARDS"] = str(n)
        out = open(f"logs/{base}_shard{i}.out.log", "w", encoding="utf-8")
        err = open(f"logs/{base}_shard{i}.err.log", "w", encoding="utf-8")
        args = [sys.executable, script, "--shard", str(i), str(n)] + extra
        p = subprocess.Popen(args, env=env, stdout=out, stderr=err,
                             creationflags=flags, close_fds=True)
        print(f"shard {i}/{n} -> PID {p.pid}", flush=True)


if __name__ == "__main__":
    main()
