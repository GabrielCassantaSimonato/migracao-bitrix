import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import ORACLE_USER, ORACLE_PASS, ORACLE_CRM_BASE


class OracleClient:
    def __init__(self):
        self.auth = (ORACLE_USER, ORACLE_PASS)

    def get(self, base, path, params=None):
        url = base + path
        last = None
        for attempt in range(3):
            try:
                r = requests.get(url, auth=self.auth, verify=False, timeout=120, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                if attempt == 2:
                    break
                time.sleep(1 * (attempt + 1))
        raise last

    def describe(self, obj):
        return self.get(ORACLE_CRM_BASE, f"/{obj}/describe")

    def registros(self, obj, limit=10, offset=0, fields=None, q=None):
        params = {"limit": limit, "offset": offset, "onlyData": "true"}
        if fields:
            params["fields"] = ",".join(fields)
        if q:
            params["q"] = q
        return self.get(ORACLE_CRM_BASE, f"/{obj}", params=params)

    def registros_todos(self, obj, limit=500, fields=None, q=None):
        """Página todos os registros (usa hasMore) e retorna lista de items."""
        out = []
        offset = 0
        while True:
            data = self.registros(obj, limit=limit, offset=offset, fields=fields, q=q)
            items = data.get("items", [])
            out.extend(items)
            if not data.get("hasMore"):
                break
            offset += limit
        return out

    def enclosure(self, obj, record_id, attribute):
        """Lê conteúdo de atributo CLOB/attachment via enclosure (record_id = PartyNumber)."""
        url = f"{ORACLE_CRM_BASE}/{obj}/{record_id}/enclosure/{attribute}"
        for attempt in range(3):
            try:
                r = requests.get(url, auth=self.auth, verify=False, timeout=120,
                                 headers={"Accept": "text/plain, application/octet-stream, */*"})
                if r.status_code == 200:
                    return r.text
                return None
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(1 * (attempt + 1))
        return None
