import requests
import json
import urllib3

# Desativa os alertas no terminal sobre requisições sem verificação de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configurações do seu ambiente Oracle
DOMAIN = "https://fa-evip-saasfaprod1.fa.ocs.oraclecloud.com"
USERNAME = "gabriel.simonato@synchro.com.br"
PASSWORD = "Oracle26"
BASE_URL = f"{DOMAIN}/fscmRestApi/resources/11.13.18.05"

# Mapeamento dos 3 endpoints
ENDPOINTS_LOOKUPS = {
    "standard": "/standardLookups",
    "common": "/commonLookups",
    "set_enabled": "/setEnabledLookups"
}

def extrair_todas_lookups():
    resultado_geral = {}

    for tipo, path in ENDPOINTS_LOOKUPS.items():
        print(f"\n--- Iniciando extração das Lookups do tipo: {tipo.upper()} ---")
        endpoint_url = BASE_URL + path
        
        lookups_deste_tipo = []
        limit = 499
        offset = 0
        has_more = True

        while has_more:
            # Substituímos o nome específico por expand=all
            url = f"{endpoint_url}?expand=all&limit={limit}&offset={offset}"
            
            try:
                # verify=False adicionado para ignorar o bloqueio de SSL
                response = requests.get(url, auth=(USERNAME, PASSWORD), verify=False)
                
                if response.status_code != 200:
                    print(f"Erro no tipo {tipo} (Status {response.status_code}): {response.text}")
                    break
                    
                data = response.json()
                itens_pagina = data.get('items', [])
                lookups_deste_tipo.extend(itens_pagina)
                
                print(f"[{tipo.upper()}] Extraídos {len(itens_pagina)} registros (Offset atual: {offset})...")
                
                has_more = data.get('hasMore', False)
                if has_more:
                    offset += limit
                    
            except Exception as e:
                print(f"Erro ao conectar com a API: {e}")
                break
        
        resultado_geral[tipo] = lookups_deste_tipo
        print(f"Total de {tipo.upper()} extraídas: {len(lookups_deste_tipo)}")

    nome_arquivo = 'todas_lookups_oracle_completo.json'
    with open(nome_arquivo, 'w', encoding='utf-8') as arquivo:
        json.dump(resultado_geral, arquivo, ensure_ascii=False, indent=4)
        
    print(f"\nExtração 100% concluída! Arquivo '{nome_arquivo}' gerado com sucesso.")

if __name__ == "__main__":
    extrair_todas_lookups()