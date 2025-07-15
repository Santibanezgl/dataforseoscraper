import os
import requests
import json
import traceback
import time
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from openai import OpenAI
import urllib3

app = Flask(__name__)

# --- CONFIGURACIÓN DE LAS CLAVES (SE LEEN DEL ENTORNO DE RENDER) ---
DATAFORSEO_LOGIN = os.environ.get("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

def post_to_dataforseo(endpoint, data):
    """Función centralizada para hacer llamadas POST a la API de DataForSEO."""
    response = requests.post(
        f"https://api.dataforseo.com/v3/{endpoint}",
        auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD),
        json=data, timeout=40
    )
    response.raise_for_status()
    return response.json()['tasks'][0]['result']

def analyze_on_page(url):
    """Analiza una URL para obtener métricas on-page."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    response = requests.get(url, headers={'User-Agent': 'SEO-Tool/1.0'}, timeout=15, verify=False)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    
    score = 100
    issues = []
    
    # --- Chequeos On-Page ---
    title_tag = soup.find('title')
    meta_desc_tag = soup.find('meta', attrs={'name': 'description'})
    canonical_tag = soup.find('link', attrs={'rel': 'canonical'})
    h1_tags = soup.find_all('h1')

    if not meta_desc_tag:
        issues.append("Falta meta descripción"); score -= 15
    if not title_tag or len(title_tag.get_text(strip=True)) < 10:
        issues.append("Título ausente o muy corto"); score -= 15
    if not h1_tags:
        issues.append("Falta etiqueta H1"); score -= 20
    elif len(h1_tags) > 1:
        issues.append("Múltiples etiquetas H1"); score -= 10
    if soup.find_all('img', alt=lambda x: x is None or not x.strip()):
        issues.append("Imágenes sin atributo ALT"); score -= 10
    
    # --- Diccionario de retorno formateado correctamente ---
    return {
        "puntaje_on_page": max(0, score),
        "conteo_palabras": len(soup.get_text(separator=' ', strip=True).split()),
        "problemas": issues if issues else ["OK"],
        "titulo_actual": title_tag.get_text(strip=True) if title_tag else "N/A",
        "metadescripcion_actual": meta_desc_tag.get('content', '').strip() if meta_desc_tag else "N/A",
        "url_canonica": canonical_tag.get('href', 'N/A') if canonical_tag else "N/A",
        "tiene_schema_markup": bool(soup.find('script', type='application/ld+json')),
        "tiempo_de_respuesta_seg": round(response.elapsed.total_seconds(), 2),
        "contenido_texto": soup.get_text(separator=' ', strip=True)
    }

def enriquecer_con_ia(contenido_pagina, keyword, competidores):
    """Genera sugerencias cualitativas usando la API de OpenAI."""
    if not OPENAI_API_KEY: 
        return {"error": "La API key de OpenAI no está configurada."}
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""Actúa como un experto en SEO y copywriter de habla hispana. Analiza la siguiente información:
- Keyword principal: "{keyword}"
- Contenido de la página (primeros 2000 caracteres): "{contenido_pagina[:2000]}"
- URLs de los competidores principales: {', '.join(competidores)}

Genera una respuesta en formato JSON con claves en español y el siguiente contenido:
1. "resumen_ejecutivo": Un párrafo corto.
2. "sugerencia_titulo": Un título SEO optimizado (50-60 caracteres).
3. "sugerencia_descripcion": Una meta descripción (150-160 caracteres).
4. "brecha_de_contenido": Menciona 2-3 temas que los competidores cubren y la página no."""
    
    response = client.chat.completions.create(
        model="gpt-4o-mini", 
        response_format={"type": "json_object"}, 
        messages=[{"role": "system", "content": prompt}]
    )
    return json.loads(response.choices[0].message.content)

# --- RUTA PRINCIPAL DE LA API ---
@app.route('/analyze', methods=['GET'])
def analyze_endpoint():
    try:
        target_url = request.args.get('url')
        keywords_str = request.args.get('keywords')
        if not target_url or not keywords_str: 
            return jsonify({"error": "Parámetros 'url' y 'keywords' son obligatorios."}), 400
        
        keywords = [kw.strip() for kw in keywords_str.split(',') if kw.strip()][:3]

        on_page_results = analyze_on_page(target_url)
        if "error" in on_page_results: 
            return jsonify(on_page_results), 500

        keyword_analyses = []
        all_competitors = set()

        for keyword in keywords:
            keyword_results = post_to_dataforseo(
                "keywords_data/google/keywords_for_keywords/live", 
                [{"keywords": [keyword], "language_name": "Spanish", "location_code": 2724}]
            )
            
            serp_post_data = [{"keyword": keyword, "language_name": "Spanish", "location_code": 2724, "depth": 20}]
            task_post_result = post_to_dataforseo("serp/google/organic/task_post", serp_post_data)
            if not task_post_result or 'id' not in task_post_result[0]:
                keyword_analyses.append({"keyword": keyword, "error": "Fallo al crear la tarea SERP."})
                continue
            
            task_id = task_post_result[0]['id']
            time.sleep(35)

            serp_results = post_to_dataforseo("serp/google/organic/task_get/advanced", [{"id": task_id}])

            if not keyword_results or not serp_results:
                keyword_analyses.append({"keyword": keyword, "error": "No se pudieron obtener datos para esta keyword."})
                continue
            
            keyword_data = keyword_results[0]
            position, top_5_competitors_list = 0, []
            features_en_serp = [item['feature'] for item in serp_results[0].get('serp_extra', []) if item.get('feature')] if serp_results else []

            for item in serp_results[0].get('items', []):
                if item.get('type') == 'organic':
                    current_url = item.get('url', '')
                    if target_url in current_url and position == 0: 
                        position = item.get('rank_group', 0)
                    if len(top_5_competitors_list) < 5 and target_url not in current_url:
                        top_5_competitors_list.append(current_url)
                        all_competitors.add(current_url)

            search_volume = keyword_data.get('search_volume', 0)
            cpc = keyword_data.get('cpc', 0)
            estimated_traffic = search_volume * {1: 0.28, 2: 0.16, 3: 0.11, 4: 0.08, 5: 0.06}.get(position, 0.01)

            keyword_analyses.append({
                "keyword": keyword,
                "rendimiento_serp": {
                    "posicion": position, 
                    "trafico_estimado": round(estimated_traffic), 
                    "valor_trafico_usd": round(estimated_traffic * cpc, 2), 
                    "features_en_serp": features_en_serp
                },
                "metricas_keyword": {
                    "volumen_busqueda": search_volume, 
                    "dificultad_keyword": keyword_data.get('keyword_difficulty', 0), 
                    "cpc_usd": cpc
                },
                "analisis_competencia": {
                    "top_5_competidores": top_5_competitors_list
                }
            })

        sugerencias_ia = enriquecer_con_ia(on_page_results.get('contenido_texto', ''), keywords[0], list(all_competitors))
        del on_page_results['contenido_texto']

        final_report = {
            "url_analizada": target_url, 
            "analisis_on_page": on_page_results, 
            "analisis_keywords": keyword_analyses, 
            "sugerencias_ia": sugerencias_ia
        }
        return jsonify(final_report)

    except Exception as e:
        error_details = traceback.format_exc()
        print("Ha ocurrido un error:")
        print(error_details)
        return jsonify({"error": "Ha ocurrido un error interno en el servidor.", "detalle": error_details}), 500
