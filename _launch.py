import subprocess
import sys

script = sys.argv[1] if len(sys.argv) > 1 else "migrar_oportunidade.py"
out_name = sys.argv[2] if len(sys.argv) > 2 else "logs/negocios_fase2_out.log"
err_name = sys.argv[3] if len(sys.argv) > 3 else "logs/negocios_fase2_err.log"

out = open(out_name, "w", encoding="utf-8")
err = open(err_name, "w", encoding="utf-8")
flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
p = subprocess.Popen(
    [sys.executable, script],
    stdout=out,
    stderr=err,
    creationflags=flags,
    close_fds=True,
)
print(p.pid)
