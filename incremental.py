"""Helpers da atualização diária incremental.

- Detecção do delta no Oracle (criados/atualizados desde N dias, filtro no servidor).
- Diff campo a campo (valor atual do Bitrix x novo do Oracle), com rótulos legíveis.
- Log (xlsx) e envio de e-mail (Zimbra SMTP).
"""

import json
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import migrador
from config import BITRIX_WEBHOOK, ENTIDADE_ITEM

WB = BITRIX_WEBHOOK.rstrip("/")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

SKIP_DIFF = {"ORIGINATOR_ID", "ORIGIN_ID"}


def corte_iso(dias):
    """Cutoff ISO (UTC) de `dias` atrás."""
    agora = datetime.now(timezone.utc)
    corte = agora - timedelta(days=dias)
    return corte.strftime("%Y-%m-%dT%H:%M:%S")


def buscar_delta(o, obj, dias, fields=None, extra_q=None):
    """(criados, atualizados): registros criados/atualizados nos últimos `dias`.
    Filtro no servidor via q=LastUpdateDate>=..., depois classifica por CreationDate."""
    corte = corte_iso(dias)
    q = f"LastUpdateDate>='{corte}'"
    if extra_q:
        q = f"{extra_q};{q}"
    tocados = o.registros_todos(obj, fields=fields, q=q)
    criados, atualizados = [], []
    for r in tocados:
        cd = r.get("CreationDate")
        if cd and str(cd) >= corte:
            criados.append(r)
        else:
            atualizados.append(r)
    return criados, atualizados


class LazyMap:
    """Mapa dict-like que resolve chave Oracle -> ID Bitrix sob demanda (find_existing)."""

    def __init__(self, entidade):
        self.entidade = entidade
        self._cache = {}

    def get(self, key, default=None):
        if key is None or key == "":
            return default
        k = str(key)
        if k not in self._cache:
            try:
                self._cache[k] = migrador.find_existing(self.entidade, k)
            except Exception:
                self._cache[k] = None
        return self._cache[k] if self._cache[k] is not None else default

    def __contains__(self, key):
        return key is not None and self.get(key) is not None

    def __len__(self):
        return len(self._cache)


def _load_fields(entidade_bitrix):
    fname = f"bitrix_{entidade_bitrix}_fields.json"
    path = os.path.join(DATA_DIR, fname)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict) and "fields" in d and isinstance(d["fields"], dict):
        d = d["fields"]
    return d


def carregar_labels(entidade_bitrix):
    """{codigo_campo -> rotulo} a partir do bitrix_*_fields.json."""
    d = _load_fields(entidade_bitrix)
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = v.get("listLabel") or v.get("title") or v.get("formLabel") or k
    return out


def carregar_enum_items(entidade_bitrix):
    """{codigo_campo -> {id_item -> VALUE}} para campos enum/crm_status."""
    d = _load_fields(entidade_bitrix)
    out = {}
    for k, v in d.items():
        if isinstance(v, dict) and v.get("type") in ("enumeration", "crm_status"):
            itens = v.get("items") or []
            out[k] = {str(i.get("ID")): i.get("VALUE", i.get("ID")) for i in itens}
    return out


_status_cache = None


def carregar_status_labels():
    """{ENTITY_ID: {STATUS_ID: NAME}} a partir do crm.status.list (escopado por entidade)."""
    global _status_cache
    if _status_cache is not None:
        return _status_cache
    out = {}
    try:
        r = requests.get(f"{WB}/crm.status.list", timeout=120)
        for s in r.json().get("result", []):
            eid, sid, name = s.get("ENTITY_ID"), s.get("STATUS_ID"), s.get("NAME")
            if eid and sid and name:
                out.setdefault(eid, {})[sid] = name
    except Exception:
        pass
    _status_cache = out
    return out


def status_labels_para(entidade):
    """Mapa {STATUS_ID -> NAME} escopado para a entidade (deal/lead/item)."""
    por_ent = carregar_status_labels()
    if entidade == "deal":
        return por_ent.get("DEAL_STAGE", {})
    if entidade == "lead":
        return por_ent.get("STATUS", {})
    if entidade.startswith("item"):
        eid = ENTIDADE_ITEM[entidade]
        for k, v in por_ent.items():
            if k.startswith(f"DYNAMIC_{eid}_STAGE_"):
                return v
    return {}


def _norm(val):
    if isinstance(val, dict):
        for k in ("ID", "value", "VALUE"):
            if k in val:
                return val[k]
        return str(val)
    if isinstance(val, (list, tuple)):
        return tuple(_norm(x) for x in val)
    if val is None:
        return ""
    return val


def _is_num(s):
    if isinstance(s, bool):
        return False
    if isinstance(s, (int, float)):
        return True
    if isinstance(s, str):
        t = s.strip().replace(",", ".")
        if t == "":
            return False
        try:
            float(t)
            return True
        except ValueError:
            return False
    return False


def _data_only(s):
    """'YYYY-MM-DD...' -> 'YYYY-MM-DD' (ou None se não for data)."""
    if isinstance(s, str):
        s = s.strip()
        if len(s) >= 10 and s[:4].isdigit() and s[4] == "-" and s[5:7].isdigit() and s[7] == "-":
            return s[:10]
    return None


def _eq(a, b):
    na, nb = _norm(a), _norm(b)
    if isinstance(na, tuple) or isinstance(nb, tuple):
        try:
            return sorted(str(x) for x in na) == sorted(str(x) for x in nb)
        except Exception:
            return False
    if _is_num(na) and _is_num(nb):
        try:
            return round(float(str(na).replace(",", ".")), 2) == round(float(str(nb).replace(",", ".")), 2)
        except Exception:
            pass
    da, db = _data_only(na), _data_only(nb)
    if da is not None and db is not None:
        return da == db
    return str(na) == str(nb)


def _fmt(val, campo, enum_items, status_labels):
    v = _norm(val)
    if isinstance(v, tuple):
        return ", ".join(_fmt(x, campo, enum_items, status_labels) for x in v)
    s = "" if v == "" else str(v)
    if s == "":
        return "(vazio)"
    if campo in ("STAGE_ID", "STATUS_ID", "stageId", "statusId") and s in status_labels:
        return status_labels[s]
    itens = enum_items.get(campo)
    if itens and s in itens:
        return itens[s]
    return s


def diferenciar(existente, payload, labels, enum_items, status_labels):
    """Lista de (rotulo, antigo, novo) dos campos que mudaram."""
    mudancas = []
    for k, novo in payload.items():
        if k in SKIP_DIFF:
            continue
        antigo = existente.get(k)
        if _eq(antigo, novo):
            continue
        mudancas.append((
            labels.get(k, k),
            _fmt(antigo, k, enum_items, status_labels),
            _fmt(novo, k, enum_items, status_labels),
        ))
    return mudancas


def obter_atual(entidade, bid):
    """Registro atual no Bitrix (dict de campos) para o diff."""
    try:
        if entidade.startswith("item"):
            eid = ENTIDADE_ITEM[entidade]
            r = migrador._get("crm.item.get", params=[("entityTypeId", eid), ("id", bid)])
            return (r.get("result") or {}).get("item", {})
        if entidade == "product":
            r = migrador._get("crm.product.get", params=[("id", bid)])
            return r.get("result", {})
        r = migrador._get(f"crm.{entidade}.get", params=[("id", bid)])
        return r.get("result", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Log (acumulador) + xlsx
# ---------------------------------------------------------------------------

class Log:
    def __init__(self):
        self.entidades = {}  # entidade -> {"criados": [(nome, data)], "atualizados": [(nome, mudancas)], "sem_alteracao": N, "erros": N}
        self.exclusoes = []  # [(entidade, nome, oracle_id, bitrix_id, aplicado)]

    def _slot(self, entidade):
        return self.entidades.setdefault(entidade, {"criados": [], "atualizados": [], "sem_alteracao": 0, "erros": 0})

    def criado(self, entidade, nome):
        self._slot(entidade)["criados"].append((nome,))

    def atualizado(self, entidade, nome, mudancas):
        self._slot(entidade)["atualizados"].append((nome, mudancas))

    def sem_alteracao(self, entidade):
        self._slot(entidade)["sem_alteracao"] += 1

    def erro(self, entidade):
        self._slot(entidade)["erros"] += 1

    def excluido(self, entidade, nome, oracle_id, bitrix_id, aplicado):
        self.exclusoes.append((entidade, nome, str(oracle_id), str(bitrix_id), aplicado))

    def resumo(self):
        linhas = []
        for entidade, d in sorted(self.entidades.items()):
            linhas.append((entidade, len(d["criados"]), len(d["atualizados"]), d["sem_alteracao"], d["erros"]))
        return linhas

    def gerar_xlsx(self, caminho):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        wb = Workbook()
        wb.remove(wb.active)
        hdr_fill = PatternFill("solid", fgColor="4472C4")
        hdr_font = Font(bold=True, color="FFFFFF")

        for entidade, d in sorted(self.entidades.items()):
            ws = wb.create_sheet(title=entidade[:31])
            ws.append(["Entidade", "Nome", "Ação", "Campo", "Antes", "Depois"])
            for c in ws[1]:
                c.fill, c.font = hdr_fill, hdr_font
            for nome in d["criados"]:
                ws.append([entidade, nome[0], "Criado", "", "", ""])
            for nome, mudancas in d["atualizados"]:
                if not mudancas:
                    ws.append([entidade, nome, "Atualizado (sem alteração)", "", "", ""])
                else:
                    for i, (campo, antigo, novo) in enumerate(mudancas):
                        ws.append([entidade, nome if i == 0 else "", "Atualizado" if i == 0 else "", campo, antigo, novo])
            # largura das colunas
            for col, w in zip("ABCDEF", (16, 40, 16, 30, 30, 30)):
                ws.column_dimensions[col].width = w

        if not self.entidades and not self.exclusoes:
            ws = wb.create_sheet(title="vazio")
            ws.append(["Nenhuma alteração no período"])

        if self.exclusoes:
            ws = wb.create_sheet(title="Exclusões")
            ws.append(["Entidade", "Nome", "Oracle ID", "Bitrix ID", "Status"])
            for c in ws[1]:
                c.fill, c.font = hdr_fill, hdr_font
            for entidade, nome, oracle_id, bitrix_id, aplicado in self.exclusoes:
                ws.append([entidade, nome, oracle_id, bitrix_id,
                           "Excluído" if aplicado else "Candidato (dry-run)"])
            for col, w in zip("ABCDE", (20, 44, 22, 14, 24)):
                ws.column_dimensions[col].width = w

        wb.save(caminho)
        return caminho


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------

def smtp_config():
    return {
        "host": os.environ.get("SMTP_HOST", "webmail.synchro.com.br"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", "asv.integracao@synchro.com.br"),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "to": os.environ.get("EMAIL_TO", "crm@synchro.com.br"),
    }


def enviar_email(assunto, corpo_html, caminho_anexo):
    cfg = smtp_config()
    msg = MIMEMultipart()
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg["Subject"] = assunto
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    with open(caminho_anexo, "rb") as f:
        part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=os.path.basename(caminho_anexo))
    msg.attach(part)

    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=120) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(cfg["user"], cfg["password"])
        s.sendmail(cfg["user"], [cfg["to"]], msg.as_string())


def periodo(dias):
    """Formata a janela para exibição: 7 -> 'última semana', 14 -> 'últimas 2 semanas', senão 'últimos N dia(s)'."""
    if dias % 7 == 0:
        semanas = dias // 7
        return "última semana" if semanas == 1 else f"últimas {semanas} semanas"
    return "último dia" if dias == 1 else f"últimos {dias} dias"


def montar_corpo(log, dias):
    """HTML curto com o resumo (criados/atualizados por entidade)."""
    resumo = log.resumo()
    tot_criados = sum(r[1] for r in resumo)
    tot_atualizados = sum(r[2] for r in resumo)
    tot_sem = sum(r[3] for r in resumo)
    tot_erros = sum(r[4] for r in resumo)
    tot_exclusoes = len(log.exclusoes)

    linhas = []
    for entidade, criados, atualizados, sem, erros in resumo:
        linhas.append(
            f"<tr><td>{entidade}</td><td align='right'>{criados}</td>"
            f"<td align='right'>{atualizados}</td><td align='right'>{sem}</td>"
            f"<td align='right'>{erros}</td></tr>"
        )

    exc_linha = ""
    if tot_exclusoes:
        exc_linha = f"<p>Exclusões detectadas: <b>{tot_exclusoes}</b> (ver aba 'Exclusões' no anexo).</p>"

    html = f"""<html><body style="font-family: Arial, sans-serif; font-size: 14px;">
<h3>Atualização diária Bitrix — {periodo(dias)}</h3>
<p>Total: <b>{tot_criados} criados</b>, <b>{tot_atualizados} atualizados</b>,
{tot_sem} sem alteração, {tot_erros} erros.</p>
{exc_linha}
<table border="1" cellpadding="4" cellspacing="0" style="border-collapse: collapse;">
<tr style="background:#4472C4;color:#fff;">
<th>Entidade</th><th>Criados</th><th>Atualizados</th><th>Sem alteração</th><th>Erros</th>
</tr>
{''.join(linhas)}
</table>
<p>Detalhes (nome de cada registro e, nas edições, os campos alterados) no arquivo anexo.</p>
</body></html>"""
    return html
