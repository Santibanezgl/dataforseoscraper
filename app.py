import os
import requests
import json
import traceback
import time
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
import urllib3

app = Flask(__name__)

# --- CONFIGURACIÓN DE LAS CLAVES ---
DATAFORSEO_LOGIN = os.environ.get("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD")

def analyze_on_page(url):
    """Analiza una URL para obtener métricas on-page."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    response = requests.get(url, headers={'User-Agent': 'SEO-Tool/1.0'}, timeout=15, verify=False)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    
    score = 100
    issues = []
    title_tag = soup.find('title')
    meta_desc_tag = soup.find('meta', attrs={'name': 'description'})
    h1_tags = soup.find_all('h1')

    if not meta_desc_tag: issues.append("Falta meta descripción"); score -= 15
    if not title_tag or len(title_tag.get_text(strip=True)) < 10: issues.append("Título ausente o muy corto"); score -= 15
    if not h1_tags: issues.append("Falta etiqueta H1"); score -= 20
    elif len(h1_tags) > 1: issues.append("Múltiples etiquetas H1"); score -= 10
    if soup.find_all('img', alt=lambda x: x is None or not x.strip()): issues.append("Imágenes sin atributo ALT"); score -= 10
    
    return {
        "puntaje_on_page": max(0, score),
        "conteo_palabras": len(soup.get_text(separator=' ', strip=True).split()),
        "problemas": issues if issues else ["OK"],
        "titulo_actual": title_tag.get_text(strip=True) if title_tag else "N/A",
        "metadescripcion_actual": meta_desc_tag.get('content', '').strip() if meta_desc_tag else "N/A"
    }

# --- RUTA PRINCIPAL DE LA API ---
@app.route('/analyze', methods=['GET'])
def analyze_endpoint():
    try:
        target_url = request.args.get('url')
        keywords_str = request.args.get('keywords')
        if not target_url or not keywords_str: return jsonify({"error": "Parámetros 'url' y 'keywords' son obligatorios."}), 400
        
        keywords = [kw.strip() for kw in keywords_str.split(',') if kw.strip()][:3]

        on_page_results = analyze_on_page(target_url)
        if "error" in on_page_results: return jsonify(on_page_results), 500

        # === LÓGICA OPTIMIZADA Y CORREGIDA ===
        
        # 1. Creamos todas las tareas SERP en un solo envío
        tasks_data = [{"keyword": keyword, "language_name": "Spanish", "location_code": 2724, "depth": 20} for keyword in keywords]
        
        tasks_post_response = requests.post(
            "https://api.dataforseo.com/v3/serp/google/organic/task_post",
            auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=tasks_data
        )
        tasks_post_response.raise_for_status()
        posted_tasks = tasks_post_response.json()['tasks']
        
        task_ids = [task['id'] for task in posted_tasks if task.get('status_code') == 20100]
        
        if not task_ids:
            return jsonify({"error": "DataForSEO no pudo crear ninguna tarea SERP. Revisa tu cuenta."}), 500
        
        # 2. Esperamos UNA SOLA VEZ para que todas se procesen
        time.sleep(45)

        # 3. Recogemos los resultados de todas las tareas
        task_get_data = [{"id": task_id} for task_id in task_ids]
        
        # ===== CORRECCIÓN FINAL DEL ENDPOINT =====
        # Usamos el endpoint correcto para recoger los resultados
        task_get_response = requests.post(
            "https://api.dataforseo.com/v3/serp/google/organic/tasks_ready",
            auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=task_ids
        )
        task_get_response.raise_for_status()
        ready_tasks_response = task_get_response.json()
        
        # Si la API indica que las tareas están listas, las buscamos
        if ready_tasks_response['tasks'][0]['status_code'] == 20000:
             task_get_final_response = requests.post(
                "https://api.dataforseo.com/v3/serp/google/organic/task_get",
                auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=task_get_data
             )
             task_get_final_response.raise_for_status()
             serp_results_tasks = task_get_final_response.json()['tasks']
        else:
             serp_results_tasks = []

        # Creamos un diccionario para acceder fácilmente a los resultados por keyword
        results_by_keyword = {task['data']['keyword']: task.get('result') for task in serp_results_tasks if task.get('status_code') == 20000}
        
        keyword_analyses = []
        for keyword in keywords:
            # Obtenemos los datos de la keyword (esta llamada es rápida)
            kw_post_data = [{"keywords": [keyword], "language_name": "Spanish", "location_code": 2724}]
            kw_response = requests.post("https://api.dataforseo.com/v3/keywords_data/google/keywords_for_keywords/live", auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD), json=kw_post_data)
            kw_response.raise_for_status()
            keyword_data = kw_response.json()['tasks'][0]['result'][0]

            serp_results = results_by_keyword.get(keyword)
            
            if not serp_results:
                keyword_analyses.append({"keyword": keyword, "rendimiento_serp": {"nota": "Datos SERP no disponibles o la tarea no se completó a tiempo."}})
                continue

            position, top_5_competitors_list = 0, []
            for item in serp_results[0].get('items', []):
                if item.get('type') == 'organic':
                    current_url = item.get('url', '')
                    if target_url in current_url and position == 0: position = item.get('rank_group', 0)
                    if len(top_5_competitors_list) < 5 and target_url not in current_url:
                        top_5_competitors_list.append(current_url)

            search_volume = keyword_data.get('search_volume', 0)
            cpc = keyword_data.get('cpc', 0)
            estimated_traffic = search_volume * {1: 0.28, 2: 0.16, 3: 0.11, 4: 0.08, 5: 0.06}.get(position, 0.01)

            keyword_analyses.append({
                "keyword": keyword,
                "rendimiento_serp": {"posicion": position, "trafico_estimado": round(estimated_traffic), "valor_trafico_usd": round(estimated_traffic * cpc, 2)},
                "metricas_keyword": {"volumen_busqueda": search_volume, "dificultad_keyword": keyword_data.get('keyword_difficulty', 0), "cpc_usd": cpc},
                "analisis_competencia": {"top_5_competidores": top_5_competitors_list}
            })

        final_report = {"url_analizada": target_url, "analisis_on_page": on_page_results, "analisis_keywords": keyword_analyses}
        return jsonify(final_report)

    except Exception as e:
        error_details = traceback.format_exc()
        return jsonify({"error": "Ha ocurrido un error interno en el servidor.", "detalle": error_details}), 500
