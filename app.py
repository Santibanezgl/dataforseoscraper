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

def post_to_dataforseo(endpoint, data):
    """Función centralizada para hacer llamadas POST a la API de DataForSEO."""
    response = requests.post(
        f"https://api.dataforseo.com/v3/{endpoint}",
        auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD),
        json=data, timeout=40
    )
    response.raise_for_status()
    return response.json()

def wait_for_tasks_completion(task_ids, max_wait_time=90, check_interval=10):
    """Espera a que las tareas se completen con polling."""
    start_time = time.time()
    completed_tasks = {}
    
    while time.time() - start_time < max_wait_time:
        try:
            pending_task_ids = [task_id for task_id in task_ids if task_id not in completed_tasks]
            if not pending_task_ids:
                return completed_tasks
            
            task_ids_to_check = [{"id": task_id} for task_id in pending_task_ids]
            serp_results = post_to_dataforseo("serp/google/organic/task_get/advanced", task_ids_to_check)
            
            for task in serp_results['tasks']:
                task_id = task['id']
                # ===== CORRECCIÓN DEFINITIVA AQUÍ =====
                # Buscamos el código 20000 (Ok) que significa que la tarea ha finalizado con éxito.
                if task.get('status_code') == 20000 and task.get('result'):
                    keyword = task['data']['keyword']
                    completed_tasks[task_id] = {'keyword': keyword, 'result': task.get('result')}

            if len(completed_tasks) == len(task_ids):
                return completed_tasks
            
            time.sleep(check_interval)
            
        except Exception as e:
            print(f"Error durante el polling: {e}")
            time.sleep(check_interval)
    
    return completed_tasks

def get_keyword_data_with_retry(keyword, max_retries=3):
    """Obtiene datos de keyword con reintentos."""
    for attempt in range(max_retries):
        try:
            kw_post_data = [{"keywords": [keyword], "language_name": "Spanish", "location_code": 2724}]
            keyword_data_response = post_to_dataforseo("keywords_data/google/keywords_for_keywords/live", kw_post_data)
            return keyword_data_response['tasks'][0]['result'][0]
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise e

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
        "puntaje_on_page": max(0, score), "conteo_palabras": len(soup.get_text(separator=' ', strip=True).split()),
        "problemas": issues if issues else ["OK"], "titulo_actual": title_tag.get_text(strip=True) if title_tag else "N/A",
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

        tasks_data = [{"keyword": keyword, "language_name": "Spanish", "location_code": 2724, "depth": 20} for keyword in keywords]
        tasks_post_response = post_to_dataforseo("serp/google/organic/task_post", tasks_data)
        posted_tasks = tasks_post_response['tasks']
        
        task_ids = [task['id'] for task in posted_tasks if task.get('status_code') == 20100]
        
        if not task_ids:
            return jsonify({"error": "No se pudieron crear las tareas SERP."}), 500
        
        # Asignamos la keyword a cada ID para usarla después
        task_id_to_keyword = {task['id']: task['data']['keyword'] for task in posted_tasks if task.get('id') in task_ids}
        
        completed_tasks = wait_for_tasks_completion(task_ids)
        
        keyword_analyses = []
        for keyword in keywords:
            task_result_data = None
            # Buscamos el resultado de la tarea por su keyword
            for task_id, task_data in completed_tasks.items():
                if task_id_to_keyword.get(task_id) == keyword:
                    task_result_data = task_data['result']
                    break
            
            if not task_result_data:
                try:
                    keyword_data = get_keyword_data_with_retry(keyword)
                    keyword_analyses.append({
                        "keyword": keyword,
                        "rendimiento_serp": {"posicion": 0, "trafico_estimado": 0, "valor_trafico_usd": 0.0, "nota": "Datos SERP no disponibles"},
                        "metricas_keyword": {"volumen_busqueda": keyword_data.get('search_volume', 0), "dificultad_keyword": keyword_data.get('keyword_difficulty', 0), "cpc_usd": keyword_data.get('cpc', 0)},
                        "analisis_competencia": {"top_5_competidores": []}
                    })
                except Exception as e:
                    keyword_analyses.append({"keyword": keyword, "error": f"No se pudieron obtener datos para esta keyword: {e}"})
                continue

            try:
                keyword_data = get_keyword_data_with_retry(keyword)
                position, top_5_competitors_list = 0, []
                for item in task_result_data[0].get('items', []):
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
            except Exception as e:
                keyword_analyses.append({"keyword": keyword, "error": f"Error procesando datos: {e}"})

        final_report = {"url_analizada": target_url, "analisis_on_page": on_page_results, "analisis_keywords": keyword_analyses, "tareas_completadas": f"{len(completed_tasks)}/{len(task_ids)}"}
        return jsonify(final_report)

    except Exception as e:
        error_details = traceback.format_exc()
        return jsonify({"error": "Ha ocurrido un error interno en el servidor.", "detalle": error_details}), 500
