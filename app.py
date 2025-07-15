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

def wait_for_tasks_completion(task_ids, max_wait_time=90, check_interval=5):
    """
    Espera a que las tareas se completen con polling más agresivo
    """
    start_time = time.time()
    completed_tasks = {}
    
    print(f"Iniciando polling para {len(task_ids)} tareas...")
    
    while time.time() - start_time < max_wait_time:
        try:
            # Verificar el estado de las tareas pendientes
            pending_task_ids = [task_id for task_id in task_ids if task_id not in completed_tasks]
            
            if not pending_task_ids:
                print("Todas las tareas completadas!")
                return completed_tasks
            
            task_ids_to_check = [{"id": task_id} for task_id in pending_task_ids]
            serp_results = post_to_dataforseo("serp/google/organic/task_get/advanced", task_ids_to_check)
            
            # Verificar tareas completadas
            for task in serp_results['tasks']:
                task_id = task['id']
                if task.get('status_code') == 20100 and task.get('result'):
                    # Tarea completada
                    keyword = task['data']['keyword']
                    completed_tasks[task_id] = {
                        'keyword': keyword,
                        'result': task.get('result')
                    }
                    print(f"Tarea completada para keyword: {keyword}")
                elif task.get('status_code') == 20000:
                    # Tarea en progreso
                    keyword = task['data']['keyword']
                    print(f"Tarea en progreso para keyword: {keyword}")
                else:
                    # Error en la tarea
                    print(f"Error en tarea {task_id}: {task.get('status_message', 'Unknown error')}")
            
            # Si todas las tareas están completadas, retornar
            if len(completed_tasks) == len(task_ids):
                print("Todas las tareas completadas!")
                return completed_tasks
            
            # Esperar antes del próximo check
            elapsed = time.time() - start_time
            print(f"Progreso: {len(completed_tasks)}/{len(task_ids)} completadas. Tiempo transcurrido: {elapsed:.1f}s")
            time.sleep(check_interval)
            
        except Exception as e:
            print(f"Error checking task status: {e}")
            time.sleep(check_interval)
    
    # Si llegamos aquí, retornar las tareas que sí se completaron
    print(f"Timeout alcanzado. Tareas completadas: {len(completed_tasks)}/{len(task_ids)}")
    return completed_tasks

def get_keyword_data_with_retry(keyword, max_retries=3):
    """
    Obtiene datos de keyword con reintentos
    """
    for attempt in range(max_retries):
        try:
            kw_post_data = [{
                "keywords": [keyword], 
                "language_name": "Spanish", 
                "location_code": 2724
            }]
            keyword_data_response = post_to_dataforseo("keywords_data/google/keywords_for_keywords/live", kw_post_data)
            return keyword_data_response['tasks'][0]['result'][0]
        except Exception as e:
            print(f"Intento {attempt + 1} fallido para keyword {keyword}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise e

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
        print(f"Analizando URL: {target_url} con keywords: {keywords}")

        # Análisis on-page (rápido)
        on_page_results = analyze_on_page(target_url)
        if "error" in on_page_results: 
            return jsonify(on_page_results), 500

        # === CREAR TAREAS SERP ===
        tasks_data = []
        for keyword in keywords:
            tasks_data.append({
                "keyword": keyword, 
                "language_name": "Spanish", 
                "location_code": 2724, 
                "depth": 20
            })
        
        print("Creando tareas SERP...")
        tasks_post_response = post_to_dataforseo("serp/google/organic/task_post", tasks_data)
        posted_tasks = tasks_post_response['tasks']
        
        # Obtener IDs de tareas exitosas
        task_ids = []
        task_id_to_keyword = {}
        for task in posted_tasks:
            if task.get('status_code') == 20100:
                task_id = task['id']
                keyword = task['data']['keyword']
                task_ids.append(task_id)
                task_id_to_keyword[task_id] = keyword
                print(f"Tarea creada para '{keyword}': {task_id}")
        
        if not task_ids:
            return jsonify({"error": "No se pudieron crear las tareas SERP."}), 500
        
        # === ESPERAR RESULTADOS ===
        print(f"Esperando resultados para {len(task_ids)} tareas...")
        completed_tasks = wait_for_tasks_completion(task_ids, max_wait_time=100, check_interval=5)
        
        # Procesar resultados disponibles
        keyword_analyses = []
        for keyword in keywords:
            # Buscar la tarea completada para esta keyword
            task_result = None
            for task_id, task_data in completed_tasks.items():
                if task_data['keyword'] == keyword:
                    task_result = task_data['result']
                    break
            
            if not task_result:
                # Si no hay resultado SERP, al menos obtener datos básicos de la keyword
                print(f"No hay resultado SERP para '{keyword}', obteniendo datos básicos...")
                try:
                    keyword_data = get_keyword_data_with_retry(keyword)
                    keyword_analyses.append({
                        "keyword": keyword,
                        "rendimiento_serp": {
                            "posicion": 0, 
                            "trafico_estimado": 0, 
                            "valor_trafico_usd": 0.0,
                            "nota": "Datos SERP no disponibles"
                        },
                        "metricas_keyword": {
                            "volumen_busqueda": keyword_data.get('search_volume', 0), 
                            "dificultad_keyword": keyword_data.get('keyword_difficulty', 0), 
                            "cpc_usd": keyword_data.get('cpc', 0)
                        },
                        "analisis_competencia": {
                            "top_5_competidores": []
                        }
                    })
                except Exception as e:
                    print(f"Error obteniendo datos básicos para '{keyword}': {e}")
                    keyword_analyses.append({
                        "keyword": keyword,
                        "error": "No se pudieron obtener datos para esta keyword"
                    })
                continue

            # Procesar resultado SERP completo
            try:
                keyword_data = get_keyword_data_with_retry(keyword)
                
                # Análisis de posición y competidores
                position, top_5_competitors_list = 0, []
                for item in task_result[0].get('items', []):
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
                
            except Exception as e:
                print(f"Error procesando keyword '{keyword}': {e}")
                keyword_analyses.append({
                    "keyword": keyword,
                    "error": f"Error procesando datos: {str(e)}"
                })

        final_report = {
            "url_analizada": target_url, 
            "analisis_on_page": on_page_results, 
            "analisis_keywords": keyword_analyses,
            "tareas_completadas": f"{len(completed_tasks)}/{len(task_ids)}"
        }
        
        print(f"Análisis completado. Tareas exitosas: {len(completed_tasks)}/{len(task_ids)}")
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

# Ruta para test rápido
@app.route('/test', methods=['GET'])
def test_endpoint():
    return jsonify({
        "status": "API funcionando",
        "dataforseo_login": "configurado" if DATAFORSEO_LOGIN else "no configurado",
        "dataforseo_password": "configurado" if DATAFORSEO_PASSWORD else "no configurado"
    })

if __name__ == '__main__':
    app.run(debug=True)
