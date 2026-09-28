import os
import threading
import time
from urllib.parse import urlencode

import requests

from config import BITRIX_WEBHOOK, CAMPO_ID_ORACLE, ENTIDADE_ITEM

WEBHOOK = BITRIX_WEBHOOK.rstrip("/")

BATCH_SIZE = 50

# ---------------------------------------------------------------------------
# Rate-limit / throttling (https://apidocs.bitrix24.com/limits.html)
#
# Leaky bucket por IP+Bitrix24: Enterprise = 5 req/s com burst de 250;
# "Others" = 2 req/s com burst de 50. Vários webhooks na MESMA máquina
# dividem a MESMA cota de rate-limit (o limite é por IP, não por webhook).
# O "operating" (OPERATION_TIME_LIMIT), por outro lado, é POR webhook/app.
# ---------------------------------------------------------------------------
RATE_LIMIT_RPS = float(os.environ.get("BITRIX_RPS", "5"))
RATE_LIMIT_BURST = int(os.environ.get("BITRIX_BURST", "250"))
OPERATING_LIMIT = float(os.environ.get("BITRIX_OPERATING_LIMIT", "480"))
TOTAL_SHARDS = max(1, int(os.environ.get("BITRIX_TOTAL_SHARDS", "1") or "1"))
if TOTAL_SHARDS > 1:
    RATE_LIMIT_RPS = RATE_LIMIT_RPS / TOTAL_SHARDS
    RATE_LIMIT_BURST = max(1, RATE_LIMIT_BURST // TOTAL_SHARDS)

OPERATION_MARKERS = ("operation time limit", "blocked")
QUERY_MARKERS = ("query_limit_exceeded", "too many requests", "rate limit")
RATE_LIMIT_MARKERS = OPERATION_MARKERS + QUERY_MARKERS

_ITEM_BY_EID = {str(eid): ent for ent, eid in ENTIDADE_ITEM.items()}


class TokenBucket:
    """Leaky-bucket thread-safe: permite bursts de até `capacity` e, depois,
    sustenta no máximo `rate` tokens por segundo."""

    def __init__(self, rate, capacity):
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def consume(self, n=1):
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= n:
                    self.tokens -= n
                    return
                deficit = (n - self.tokens) / self.rate
            time.sleep(min(deficit, 5.0))

    def penalize(self):
        """Zera o burst após um 503 (força reabastecimento na taxa sustentável)."""
        with self.lock:
            self.tokens = 0.0


_bucket = TokenBucket(RATE_LIMIT_RPS, RATE_LIMIT_BURST)

_operating = 0.0
_operating_reset_at = 0.0
_operating_lock = threading.Lock()


def _throttle():
    _bucket.consume(1)


def _note_time(res):
    """Registra `time.operating` / `time.operating_reset_at` de uma resposta."""
    global _operating, _operating_reset_at
    if not isinstance(res, dict):
        return
    t = res.get("time")
    if not isinstance(t, dict):
        return
    with _operating_lock:
        if "operating" in t:
            try:
                _operating = float(t["operating"])
            except (TypeError, ValueError):
                pass
        if "operating_reset_at" in t:
            try:
                _operating_reset_at = float(t["operating_reset_at"])
            except (TypeError, ValueError):
                pass


def _preflight():
    """Pausa proativa: se o `operating` já estourou o limite e sabemos o reset,
    aguarda a liberação da janela mais antiga (evita o 429)."""
    with _operating_lock:
        op, reset = _operating, _operating_reset_at
    if op >= OPERATING_LIMIT and reset > time.time():
        wait = reset - time.time()
        if wait > 0:
            time.sleep(min(wait, 600))


def _classify_error(err):
    """'operation' | 'query' | None a partir de um erro (dict/string/exceção)."""
    if not err:
        return None
    if isinstance(err, dict):
        text = " ".join(str(v) for v in err.values()).lower()
    else:
        text = str(err).lower()
    if any(m in text for m in OPERATION_MARKERS):
        return "operation"
    if any(m in text for m in QUERY_MARKERS):
        return "query"
    return None


def _status_class(status):
    """Classifica o status HTTP: 429 = operation time limit, 503 = query limit."""
    if status == 429:
        return "operation"
    if status == 503:
        return "query"
    return None


def _is_rate_limited(err):
    return _classify_error(err) is not None


def _generic_backoff(attempt):
    return 5 * (2 ** attempt)


def _query_backoff(attempt):
    return min(120, 5 * (2 ** attempt))


def _operation_sleep():
    with _operating_lock:
        reset = _operating_reset_at
    now = time.time()
    if reset > now:
        time.sleep(min(reset - now + 1, 600))
    else:
        time.sleep(120 * (2 ** 0))


def _backoff_sleep(attempt):
    """120s, 240s, 480s... espera a janela de "operation time limit" resetar (~5 min).
    Mantido para compatibilidade; o fluxo novo usa _sleep_after_limited()."""
    return 120 * (2 ** attempt)


def _sleep_after_limited(kind, attempt):
    if kind == "operation":
        _operation_sleep()
    elif kind == "query":
        _bucket.penalize()
        time.sleep(_query_backoff(attempt))
    else:
        time.sleep(_generic_backoff(attempt))


def _get_indexed(container, k):
    """Lê um resultado/erro do batch (que pode vir como dict keyed por índice ou lista)."""
    if isinstance(container, dict):
        return container.get(str(k))
    if isinstance(container, list) and k < len(container):
        return container[k]
    return None


def _retry(request_fn):
    """Executa request_fn() (retorna um requests.Response) com throttle + backoff.
    Devolve o JSON (dict) da resposta. Levanta a última exceção se esgotar."""
    last = None
    for attempt in range(6):
        _preflight()
        _throttle()
        try:
            r = request_fn()
            data = r.json()
            _note_time(data)
            if r.status_code == 200 and "error" not in data:
                return data
            if r.status_code == 200:
                kind = _classify_error(data.get("error"))
                if not kind:
                    return data  # erro de negócio (não rate-limit): devolve (compat)
                last = RuntimeError("Bitrix rate limit: " + str(data.get("error")))
            else:
                kind = _status_class(r.status_code)
                last = RuntimeError(f"HTTP {r.status_code}")
            _sleep_after_limited(kind, attempt)
        except Exception as e:
            last = e
            _sleep_after_limited(_classify_error(e), attempt)
    raise last


def _get(method, params=None):
    return _retry(lambda: requests.get(f"{WEBHOOK}/{method}", params=params, timeout=120))


def get_json(method, params=None):
    """GET com throttle/backoff, devolvendo o JSON completo (usado por mapeamento)."""
    return _get(method, params)


def _post(method, fields=None, extra=None):
    params = {}
    if fields:
        for k, v in fields.items():
            if isinstance(v, (list, tuple)):
                params[f"fields[{k}][]"] = v
            else:
                params[f"fields[{k}]"] = v
    if extra:
        params.update(extra)
    return _retry(lambda: requests.post(f"{WEBHOOK}/{method}", data=params, timeout=120))


def _post_data(method, data, timeout=120):
    """POST com form-data já montado (data=dict), com backoff de rate-limit."""
    return _retry(lambda: requests.post(f"{WEBHOOK}/{method}", data=data, timeout=timeout))


def _post_json(method, body, timeout=300):
    """POST com corpo JSON (crm.item.update), com backoff de rate-limit."""
    return _retry(lambda: requests.post(f"{WEBHOOK}/{method}", json=body, timeout=timeout))


def find_existing(entidade, oracle_id):
    """Retorna o ID Bitrix do registro com esse ID Oracle, ou None."""
    oid = str(oracle_id)
    if entidade.startswith("item"):
        eid = ENTIDADE_ITEM[entidade]
        id_field = CAMPO_ID_ORACLE[entidade]
        params = {"entityTypeId": eid, f"filter[{id_field}]": oid, "select[]": "id"}
        r = _get("crm.item.list", params)
        items = (r.get("result") or {}).get("items", [])
        return items[0]["id"] if items else None
    if entidade == "product":
        r = _get("crm.product.list", {"filter[PROPERTY_305]": oid, "select[]": "ID"})
        items = r.get("result", [])
        return items[0]["ID"] if items else None
    method = f"crm.{entidade}.list"
    r = _get(method, {"filter[ORIGINATOR_ID]": "oracle", "filter[ORIGIN_ID]": oid, "select[]": "ID"})
    items = r.get("result", [])
    if items:
        return items[0]["ID"]
    # fallback: campo custom de ID Oracle (registros antigos sem ORIGINATOR_ID)
    cf = CAMPO_ID_ORACLE.get(entidade)
    if cf:
        r2 = _get(method, {f"filter[{cf}]": oid, "select[]": "ID"})
        items2 = r2.get("result", [])
        return items2[0]["ID"] if items2 else None
    return None


def _oracle_key(method, params):
    """Extrai (entidade, oracle_id) de um comando de criação (crm.*.add / crm.item.add /
    crm.product.add). Retorna (None, None) se não for um add ou não houver chave Oracle."""
    parts = (method or "").split(".")
    if len(parts) < 3 or parts[-1] != "add":
        return None, None
    ent = parts[1]
    if ent == "item":
        ent_item = _ITEM_BY_EID.get(str(params.get("entityTypeId")))
        if not ent_item:
            return None, None
        campo = CAMPO_ID_ORACLE.get(ent_item)
        oid = params.get(f"fields[{campo}]")
        return ent_item, (str(oid) if oid is not None else None)
    if ent == "product":
        oid = params.get("fields[PROPERTY_305]")
        return "product", (str(oid) if oid is not None else None)
    oid = params.get("fields[ORIGIN_ID]")
    if oid is None:
        return None, None
    return ent, str(oid)


def _ja_existe(method, params):
    """True se o registro alvo de um comando de criação já existe no Bitrix (idempotência)."""
    ent, oid = _oracle_key(method, params)
    if not ent or oid is None:
        return False
    try:
        return find_existing(ent, oid) is not None
    except Exception:
        return False


def _create(entidade, fields):
    if entidade.startswith("item"):
        eid = ENTIDADE_ITEM[entidade]
        r = _post("crm.item.add", fields=fields, extra={"entityTypeId": eid})
        res = r.get("result") or {}
        return res.get("item", {}).get("id") if isinstance(res, dict) else res
    if entidade == "product":
        return _post("crm.product.add", fields=fields).get("result")
    return _post(f"crm.{entidade}.add", fields=fields).get("result")


def _update(entidade, bitrix_id, fields):
    if entidade.startswith("item"):
        eid = ENTIDADE_ITEM[entidade]
        return _post("crm.item.update", fields=fields, extra={"entityTypeId": eid, "id": bitrix_id}).get("result")
    if entidade == "product":
        return _post("crm.product.update", fields=fields, extra={"id": bitrix_id}).get("result")
    return _post(f"crm.{entidade}.update", fields=fields, extra={"id": bitrix_id}).get("result")


def upsert(entidade, fields, oracle_id):
    """Cria ou atualiza um registro; retorna (bitrix_id, 'created'|'updated')."""
    existing = find_existing(entidade, oracle_id)
    if existing:
        _update(entidade, existing, fields)
        return existing, "updated"
    new_id = _create(entidade, fields)
    return new_id, "created"


def command(method, fields=None, extra=None, multi=None):
    """Converte (metodo, fields/extra/multi) numa tupla (metodo, params) pronta para o batch.
    Usa o mesmo formato de _post/bitrix() dos migradores."""
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
    return method, params


def carregar_existentes_item(entidade):
    """{oracle_id -> bitrix_id} de um SPA (crm.item.list paginado)."""
    eid = ENTIDADE_ITEM[entidade]
    id_field = CAMPO_ID_ORACLE[entidade]
    mapa = {}
    start = 0
    while True:
        r = _get("crm.item.list", params=[
            ("entityTypeId", eid), ("select[]", "id"), ("select[]", id_field), ("start", start)])
        items = (r.get("result") or {}).get("items", [])
        for it in items:
            v = it.get(id_field)
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(it["id"])
        if len(items) < 50:
            break
        start += 50
    return mapa


def carregar_existentes_crm(metodo, campo_oracle):
    """{oracle_id -> bitrix_id} de uma entidade crm nativa (list paginado)."""
    mapa = {}
    start = 0
    while True:
        r = _get(metodo, params=[
            ("select[]", "ID"), ("select[]", campo_oracle), ("start", start)])
        res = r.get("result", [])
        for c in res:
            v = c.get(campo_oracle)
            val = v.get("value") if isinstance(v, dict) else v
            if val:
                mapa[str(val)] = int(c["ID"])
        if len(res) < 50:
            break
        start += 50
    return mapa


def _run_batch_chunk(chunk, halt):
    """Posta um lote e retorna (res_list, err_list) alinhados por índice do lote.

    Nunca re-lança exceção: respostas vazias/não-JSON, timeouts ou HTTP != 200 (que
    ocorrem quando o Bitrix bloqueia por "operation time limit") são re-tentadas com
    backoff exponencial e, ao esgotar, viram erro por comando (a migração segue e a
    re-execução idempotente pega os falhados).

    Idempotência: em falha ambígua (timeout/HTTP!=200/não-JSON), antes de re-postar o
    lote descarta os comandos cujo registro já existe no Bitrix — evita duplicatas
    quando o Bitrix bloqueia mas já criou o registro."""
    res_list = [None] * len(chunk)
    err_list = [None] * len(chunk)
    active = list(enumerate(chunk))  # [(orig_idx, (method, params))]
    for attempt in range(4):
        data = {}
        for j, (orig_idx, (method, params)) in enumerate(active):
            data[f"cmd[{j}]"] = f"{method}?" + urlencode(params, doseq=True, safe="[]")
        data["halt"] = str(halt)
        res = None
        status = None
        _preflight()
        _throttle()
        try:
            r = requests.post(f"{WEBHOOK}/batch", data=data, timeout=300)
            status = r.status_code
            if r.status_code != 200:
                res = {"error": f"HTTP {r.status_code}", "error_description": (r.text or "")[:300]}
            else:
                res = r.json()
                _note_time(res)
        except ValueError:
            res = {"error": "batch error", "error_description": "resposta vazia/nao-JSON"}
        except Exception as e:
            res = {"error": "batch error", "error_description": str(e)}
        if isinstance(res, dict) and "result" in res:
            rr = res["result"]
            cmd_results = rr.get("result") or {}
            cmd_errors = rr.get("result_error") or {}
            top_error = res.get("error")
            for j, (orig_idx, (method, params)) in enumerate(active):
                if top_error:
                    res_list[orig_idx] = None
                    err_list[orig_idx] = {"error": "batch error", "error_description": str(top_error)}
                else:
                    res_list[orig_idx] = _get_indexed(cmd_results, j)
                    err_list[orig_idx] = _get_indexed(cmd_errors, j)
            break
        if attempt == 3:
            for orig_idx, (method, params) in active:
                err_list[orig_idx] = {"error": "batch error",
                                      "error_description": str((res or {}).get("error_description") or (res or {}).get("error"))}
            break
        kind = _status_class(status) or _classify_error((res or {}).get("error")) or \
            _classify_error((res or {}).get("error_description"))
        _sleep_after_limited(kind, attempt)
        # guarda de idempotência: remove comandos já criados antes de re-tentar
        active = [(oi, cmd) for oi, cmd in active if not _ja_existe(cmd[0], cmd[1])]
        if not active:
            break
    return res_list, err_list


def batch(commands, halt=0, batch_size=BATCH_SIZE, chunk_sleep=0.5):
    """Executa uma lista de (metodo, params) via a API batch do Bitrix, em lotes de
    `batch_size` (default 50). Lotes menores evitam o "operation time limit" quando os
    comandos são pesados (ex.: crm.deal.update). `chunk_sleep` é a pausa entre lotes —
    aumentá-la espaça as gravações e evita estourar a cota de "operation time".
    Retorna (results, errors): results[i] = resultado do comando i (None em erro),
    errors[i] = dict de erro (None em sucesso).

    Comandos que falham por rate-limit são re-tentados com backoff antes de desistir."""
    results = []
    errors = []
    for i in range(0, len(commands), batch_size):
        chunk = commands[i:i + batch_size]
        res_list, err_list = _run_batch_chunk(chunk, halt)
        for attempt in range(3):
            retry_idx = [k for k in range(len(chunk)) if _is_rate_limited(err_list[k])]
            if not retry_idx:
                break
            # guarda de idempotência: descarta comandos cujo registro já foi criado
            sub = []
            sub_map = []
            for k in retry_idx:
                method, params = chunk[k]
                if _ja_existe(method, params):
                    res_list[k] = None
                    err_list[k] = None
                else:
                    sub.append(chunk[k])
                    sub_map.append(k)
            if not sub:
                break
            kind = _classify_error(err_list[retry_idx[0]]) if retry_idx else None
            _sleep_after_limited(kind, attempt)
            sub_res, sub_err = _run_batch_chunk(sub, halt)
            for idx, k in enumerate(sub_map):
                if not _is_rate_limited(sub_err[idx]):
                    res_list[k] = sub_res[idx]
                    err_list[k] = sub_err[idx]
        for k in range(len(chunk)):
            if err_list[k]:
                errors.append(err_list[k])
                results.append(None)
            else:
                errors.append(None)
                results.append(res_list[k])
        time.sleep(chunk_sleep)
    return results, errors
