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

# --- FUNCIONES AUXILIARES ---
def post_to_dataforseo(endpoint, data):
    response = requests.post(
        f"https://api.dataforseo.com/v3/{endpoint}",
        auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD),
        json=data, timeout=40
    )
    response.raise_for_status()
    return response.json()

def wait_for_tasks_completion(task_ids, max_wait_time=70, check_interval=10):
    """
    Espera a que las tareas se completen con polling inteligente
    """
    start_time = time.time()
    
    while time.time() - start_time < max_wait_time:
        try:
            # Verificar el estado de las tareas
            task_ids_to_check = [{"id": task_id} for task_id in task_ids]
            serp_results = post_to_dataforseo("serp/google/organic/task_get/advanced", task_ids_to_check)
            
            # Verificar si todas las tareas están completadas
            all_completed = True
            results_by_keyword = {}
            
            for task in serp_results['tasks']:
                if task.get('status_code') == 20100 and task.get('result'):
                    # Tarea completada
                    keyword = task['data']['keyword']
                    results_by_keyword[keyword] = task.get('result')
                else:
                    # Tarea aún no completada
                    all_completed = False
                    break
            
            if all_completed:
                return results_by_keyword
            
            # Esperar antes del próximo check
            time.sleep(check_interval)
            
        except Exception as e:
            print(f"Error checking task status: {e}")
            time.sleep(check_interval)
    
    # Si llegamos aquí, algunas tareas no se completaron en el tiempo límite
    return None

def analyze_on_page(url):
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
        if not target_url or not keywords_str: 
            return jsonify({"error": "Parámetros 'url' y 'keywords' son obligatorios."}), 400
        
        keywords = [kw.strip() for kw in keywords_str.split(',') if kw.strip()][:3]

        # Análisis on-page (rápido)
        on_page_results = analyze_on_page(target_url)
        if "error" in on_page_results: 
            return jsonify(on_page_results), 500

        # === LÓGICA OPTIMIZADA CON POLLING INTELIGENTE ===
        # 1. Creamos todas las tareas SERP
        tasks_data = []
        for keyword in keywords:
            tasks_data.append({
                "keyword": keyword, 
                "language_name": "Spanish", 
                "location_code": 2724, 
                "depth": 20
            })
        
        tasks_post_response = post_to_dataforseo("serp/google/organic/task_post", tasks_data)
        posted_tasks = tasks_post_response['tasks']
        
        # 2. Obtener IDs de tareas exitosas
        task_ids = []
        for task in posted_tasks:
            if task.get('status_code') == 20100:
                task_ids.append(task['id'])
        
        if not task_ids:
            return jsonify({"error": "No se pudieron crear las tareas SERP."}), 500
        
        # 3. Esperar con polling inteligente
        print(f"Esperando resultados para {len(task_ids)} tareas...")
        results_by_keyword = wait_for_tasks_completion(task_ids, max_wait_time=70, check_interval=8)
        
        if not results_by_keyword:
            return jsonify({"error": "Timeout: Las tareas SERP no se completaron en el tiempo esperado."}), 408
        
        print(f"Resultados obtenidos para {len(results_by_keyword)} keywords")
        
        # === FIN DE LA LÓGICA OPTIMIZADA ===

        keyword_analyses = []
        for keyword in keywords:
            serp_results = results_by_keyword.get(keyword)
            if not serp_results:
                keyword_analyses.append({
                    "keyword": keyword, 
                    "error": "No se pudo obtener el resultado de la tarea SERP."
                })
                continue

            # Obtener datos de la keyword (llamada rápida)
            try:
                kw_post_data = [{
                    "keywords": [keyword], 
                    "language_name": "Spanish", 
                    "location_code": 2724
                }]
                keyword_data_response = post_to_dataforseo("keywords_data/google/keywords_for_keywords/live", kw_post_data)
                keyword_data = keyword_data_response['tasks'][0]['result'][0]
            except Exception as e:
                print(f"Error getting keyword data for {keyword}: {e}")
                keyword_analyses.append({
                    "keyword": keyword, 
                    "error": "Error obteniendo datos de la keyword."
                })
                continue

            # Análisis de posición y competidores
            position, top_5_competitors_list = 0, []
            for item in serp_results[0].get('items', []):
                if item.get('type') == 'organic':
                    current_url = item.get('url', '')
                    if target_url in current_url and position == 0: 
                        position = item.get('rank_group', 0)
                    if len(top_5_competitors_list) < 5 and target_url not in current_url:
                        top_5_competitors_list.append(current_url)

            # Cálculos
            search_volume = keyword_data.get('search_volume', 0)
            cpc = keyword_data.get('cpc', 0)
            ctr_by_position = {1: 0.28, 2: 0.16, 3: 0.11, 4: 0.08, 5: 0.06}
            estimated_traffic = search_volume * ctr_by_position.get(position, 0.01)

            keyword_analyses.append({
                "keyword": keyword,
                "rendimiento_serp": {
                    "posicion": position, 
                    "trafico_estimado": round(estimated_traffic), 
                    "valor_trafico_usd": round(estimated_traffic * cpc, 2)
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

        final_report = {
            "url_analizada": target_url, 
            "analisis_on_page": on_page_results, 
            "analisis_keywords": keyword_analyses, 
        }
        return jsonify(final_report)

    except Exception as e:
        error_details = traceback.format_exc()
        print("Ha ocurrido un error:")
        print(error_details)
        return jsonify({
            "error": "Ha ocurrido un error interno en el servidor.", 
            "detalle": error_details
        }), 500

# Ruta de health check
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "timestamp": time.time()})

if __name__ == '__main__':
    app.run(debug=True)
