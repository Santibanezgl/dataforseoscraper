import os
import requests
import json
import traceback
import time
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
import urllib3
from urllib.parse import urlparse

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

        # === NUEVO: OBTENER COMPETIDORES DEL DOMINIO ===
        try:
            domain = urlparse(target_url).netloc
            competitors_post_data = [{"target": domain, "location_code": 2724, "language_code": "es", "limit": 5, "order_by": ["rating,desc"]}]
            competitors_response = post_to_dataforseo("dataforseo_labs/google/competitors_domain/live", competitors_post_data)
            competitors_data = competitors_response['tasks'][0]['result'][0].get('items', [])
            top_5_domain_competitors = [item['domain'] for item in competitors_data]
        except Exception as e:
            print(f"Error obteniendo competidores de dominio: {e}")
            top_5_domain_competitors = []

        keyword_analyses = []
        for keyword in keywords:
            # Iniciamos la tarea SERP
            task_post_data = [{"keyword": keyword, "language_name": "Spanish", "location_code": 2724, "depth": 20}]
            task_post_response = post_to_dataforseo("serp/google/organic/task_post", task_post_data)
            
            # Obtenemos los datos de la keyword (esta llamada es rápida)
            kw_post_data = [{"keywords": [keyword], "language_name": "Spanish", "location_code": 2724}]
            kw_response = post_to_dataforseo("keywords_data/google/keywords_for_keywords/live", kw_post_data)
            keyword_data = kw_response['tasks'][0]['result'][0]
            
            # Añadimos un resultado parcial mientras esperamos
            keyword_analyses.append({
                "keyword": keyword,
                "rendimiento_serp": {"posicion": 0, "trafico_estimado": 0, "valor_trafico_usd": 0.0, "nota": "Datos SERP no disponibles o la tarea falló."},
                "metricas_keyword": {"volumen_busqueda": keyword_data.get('search_volume', 0), "dificultad_keyword": keyword_data.get('keyword_difficulty', 0), "cpc_usd": keyword_data.get('cpc', 0)},
            })

        final_report = {
             "url_analizada": target_url, 
             "analisis_on_page": on_page_results, 
             "analisis_keywords": keyword_analyses,
             "top_5_competidores_de_dominio": top_5_domain_competitors
        }
        return jsonify(final_report)

    except Exception as e:
        error_details = traceback.format_exc()
        return jsonify({"error": "Ha ocurrido un error interno en el servidor.", "detalle": error_details}), 500
