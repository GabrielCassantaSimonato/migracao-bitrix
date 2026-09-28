import requests

from config import BITRIX_WEBHOOK


class BitrixClient:
    def __init__(self):
        self.base = BITRIX_WEBHOOK.rstrip("/")

    def call(self, method, params=None):
        r = requests.get(f"{self.base}/{method}", params=params, timeout=120)
        data = r.json()
        if r.status_code != 200:
            raise RuntimeError(f"{method} -> {r.status_code}: {data}")
        if "error" in data:
            raise RuntimeError(f"{method} -> {data['error']}: {data.get('error_description', '')}")
        return data.get("result")

    def fields(self, entidade):
        if entidade.startswith("item"):
            from config import ENTIDADE_ITEM
            r = self.call("crm.item.fields", {"entityTypeId": ENTIDADE_ITEM[entidade]})
            return r.get("fields", r) if isinstance(r, dict) else r
        return self.call(f"crm.{entidade}.fields")

    def status_list(self):
        return self.call("crm.status.list")
