import json
import os

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

import mapeamento
from config import OBJETOS, CAMPO_ID_ORACLE

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relatorio_migracao.xlsx")

HEADER_FILL = PatternFill("solid", fgColor="4472C4")
HEADER_FONT = Font(bold=True, color="FFFFFF")
FILL_NAO = PatternFill("solid", fgColor="F2F2F2")
FILL_REV = PatternFill("solid", fgColor="FFF2CC")
FILL_OK = PatternFill("solid", fgColor="E2EFDA")


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def carregar_de_para():
    return load("de_para_completo.json")


def montar_de_para():
    dp = carregar_de_para()
    linhas = []
    for oracle_obj, info in dp.items():
        for c in info["campos"]:
            linhas.append([
                oracle_obj, info["bitrix"], c["oracle"], c["titulo_oracle"], c["tipo_oracle"],
                c["bitrix"], c.get("label_bitrix", ""), c.get("transform", ""),
                c.get("origem", ""), c["confianca"],
            ])
    return linhas


def montar_revisar():
    dp = carregar_de_para()
    linhas = []
    for oracle_obj, info in dp.items():
        for c in info["campos"]:
            if c["confianca"] == "Baixo":
                linhas.append([
                    oracle_obj, info["bitrix"], c["oracle"], c["titulo_oracle"],
                    c["bitrix"], c.get("label_bitrix", ""), c.get("score", ""),
                ])
    return linhas


def montar_amostra():
    dp = carregar_de_para()
    linhas = []
    for oracle_obj, bitrix_ent in OBJETOS:
        try:
            amostra = load(f"amostra_{oracle_obj}.json")
        except FileNotFoundError:
            continue
        campos = dp.get(oracle_obj, {}).get("campos", [])
        for rec in amostra.get("items", []):
            for c in campos:
                if not c["bitrix"] or c["confianca"] == "Nao mapeado":
                    continue
                val = mapeamento.aplicar_transform(c["oracle"], c["bitrix"], c.get("transform"), rec)
                if val is None or val == "":
                    continue
                linhas.append([oracle_obj, bitrix_ent, c["oracle"], c["bitrix"], val])
    return linhas


def montar_etapas():
    status = load("bitrix_status_list.json")
    deal = sorted([s for s in status if s["ENTITY_ID"] == "DEAL_STAGE"], key=lambda s: int(s.get("SORT", 0)))
    linhas = []
    for s in deal:
        orac = next((k for k, v in mapeamento.DEAL_STAGE_MAP.items() if v == s["STATUS_ID"]), "")
        linhas.append(["Oportunidade", orac, s["STATUS_ID"], s["NAME"]])
    return linhas


def montar_lookups():
    linhas = []
    for code, suffix in mapeamento.SUFIXO_PRODUTO.items():
        linhas.append(["Grupo de produto", "QSC_SALES_PRODUCT_TYPE", code, suffix])
    for code in ["GOODS", "COVERAGE", "INSTALLATION", "TRAINING", "SUBSCRIPTION",
                 "SOFTWARE_MAINTENANCE", "PREVENTIVE_MAINTENANCE", "ONE_TIME_SERVICE",
                 "INCLUDED_WARRANTY", "SERVICE_LEVEL_AGREEMENT"]:
        linhas.append(["Grupo de produto", "QSC_SALES_PRODUCT_TYPE", code, "sem sufixo"])
    return linhas


def estilo_sheet(ws, header, widths):
    ws.append(header)
    for c in range(1, len(header) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def main():
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "De-para Campos"
    estilo_sheet(ws1, ["Objeto Oracle", "Entidade Bitrix", "Campo Oracle", "Título Oracle",
                       "Tipo Oracle", "Campo Bitrix", "Label Bitrix", "Transformação",
                       "Origem", "Confiança"],
                 [20, 15, 34, 34, 16, 24, 34, 14, 10, 14])
    for row in montar_de_para():
        ws1.append(row)
        r = ws1.max_row
        conf = row[-1]
        fill = FILL_OK if conf == "Alto" else (FILL_REV if conf == "Baixo" else FILL_NAO)
        for c in range(1, len(row) + 1):
            ws1.cell(row=r, column=c).fill = fill

    ws2 = wb.create_sheet("A revisar")
    estilo_sheet(ws2, ["Objeto Oracle", "Entidade Bitrix", "Campo Oracle", "Título Oracle",
                       "Campo Bitrix (sugerido)", "Label Bitrix", "Score"],
                 [20, 15, 34, 34, 24, 34, 10])
    for row in montar_revisar():
        ws2.append(row)

    ws3 = wb.create_sheet("Amostra 10 registros")
    estilo_sheet(ws3, ["Objeto Oracle", "Entidade Bitrix", "Campo Oracle", "Campo Bitrix", "Valor"],
                 [20, 15, 34, 24, 80])
    for row in montar_amostra():
        ws3.append(row)

    ws4 = wb.create_sheet("Etapas")
    estilo_sheet(ws4, ["Objeto Oracle", "Fase Oracle", "STAGE_ID Bitrix", "Nome Bitrix"],
                 [18, 30, 20, 40])
    for row in montar_etapas():
        ws4.append(row)

    ws5 = wb.create_sheet("Lookups")
    estilo_sheet(ws5, ["Categoria", "Lookup", "Código", "Sufixo / Tratamento"],
                 [20, 26, 26, 24])
    for row in montar_lookups():
        ws5.append(row)

    wb.save(OUT)
    print("Excel gerado:", OUT)


if __name__ == "__main__":
    main()
