import os
import requests
import json
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from openai import OpenAI

# --- Aplicación Flask ---
app = Flask(__name__)

# --- CONFIGURACIÓN ---
# Las credenciales se leen de las variables de entorno de Render
DATAFORSEO_LOGIN = os.environ.get("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# --- FUNCIONES AUXILIARES ---

def post_to_dataforseo(endpoint, data):
    """Función centralizada para hacer llamadas POST a la API de DataForSEO."""
    try:
        response = requests.post(
            f"https://api.dataforseo.com/v3/{endpoint}",
            auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD),
            json=data,
            timeout=40
        )
        response.raise_for_status()
        return response.json()['tasks'][0]['result']
    except Exception as e:
        print(f"Error en DataForSEO: {e}")
        return None

def analyze_on_page(url):
    """Analiza la URL y extrae las métricas on-page."""
    try:
        response = requests.get(url, headers={'User-Agent': 'SEO-Tool/1.0'}, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    except requests.RequestException as e:
        return {"error": f"Error al acceder a la URL: {e}"}

    score = 100
    issues = []
    if not soup.find('meta', attrs={'name': 'description'}):
        issues.append("Falta meta descripción"); score -= 15
    title_tag = soup.find('title')
    if not title_tag or len(title_tag.get_text(strip=True)) < 10:
        issues.append("Título ausente o muy corto"); score -= 15
    h1_tags = soup.find_all('h1')
    if not h1_tags:
        issues.append("Falta etiqueta H1"); score -= 20
    elif len(h1_tags) > 1:
        issues.append("Múltiples etiquetas H1"); score -= 10
    if soup.find_all('img', alt=lambda x: x is None or not x.strip()):
        issues.append("Imágenes sin atributo ALT"); score -= 10

    return {
        "puntaje_on_page": max(0, score),
        "conteo_palabras": len(soup.get_text(separator=' ', strip=True).split()),
        "problemas": issues if issues else ["OK"],
        "titulo_actual": title_tag.get_text(strip=True) if title_tag else "N/A",
        "metadescripcion_actual": soup.find('meta', attrs={'name': 'description'}).get('content', '').strip() if soup.find('meta', attrs={'name': 'description'}) else "N/A",
        "url_canonica": soup.find('link', attrs={'rel': 'canonical'}).get('href', 'N/A') if soup.find('link', attrs={'rel': 'canonical'}) else "N/A",
        "tiene_schema_markup": bool(soup.find('script', type='application/ld+json')),
        "tiempo_de_respuesta_seg": round(response.elapsed.total_seconds(), 2),
        "contenido_texto": soup.get_text(separator=' ', strip=True)
    }

def enriquecer_con_ia(contenido_pagina, keyword, competidores):
    """
    Función ACTIVADA para la llamada a la API de OpenAI.
    """
    if not OPENAI_API_KEY:
        return {"error": "La API key de OpenAI no está configurada."}

    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""
    Actúa como un experto en SEO y copywriter de habla hispana. Analiza la siguiente información:
    - Keyword principal: "{keyword}"
    - Contenido de la página (primeros 2000 caracteres): "{contenido_pagina[:2000]}"
    - URLs de los competidores principales: {', '.join(competidores)}

    Genera una respuesta en formato JSON con claves en español y el siguiente contenido:
    1. "resumen_ejecutivo": Un párrafo corto (2-3 frases) con el diagnóstico principal y la recomendación más importante.
    2. "sugerencia_titulo": Un título SEO optimizado (50-60 caracteres) que sea atractivo.
    3. "sugerencia_descripcion": Una meta descripción (150-160 caracteres) que incentive el clic.
    4. "brecha_de_contenido": Menciona 2-3 temas o preguntas específicas que los competidores probablemente cubren y que deberían añadirse a la página.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error en la llamada a OpenAI: {e}")
        return {"error": "No se pudo generar la sugerencia de la IA."}

# --- RUTA PRINCIPAL DE LA API ---

@app.route('/analyze', methods=['GET'])
def analyze_endpoint():
    target_url = request.args.get('url')
    keywords_str = request.args.get('keywords')

    # ... Validación de entradas ...
    if not target_url or not keywords_str: return jsonify({"error": "Parámetros 'url' y 'keywords' son obligatorios."}), 400
    keywords = [kw.strip() for kw in keywords_str.split(',') if kw.strip()]
    if not 1 <= len(keywords) <= 3: return jsonify({"error": "Debe proporcionar de 1 a 3 palabras clave."}), 400
    if not all([DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD]): return jsonify({"error": "Credenciales de DataForSEO no configuradas."}), 500

    on_page_results = analyze_on_page(target_url)
    if "error" in on_page_results: return jsonify(on_page_results), 500

    keyword_analyses = []
    all_competitors = set()

    for keyword in keywords:
        # ... (lógica de llamadas y procesamiento de DataForSEO) ...
        keyword_results = post_to_dataforseo("keywords_data/google/keywords_for_keywords/live", [{"keywords": [keyword], "language_name": "Spanish", "location_code": 2724}])
        serp_results = post_to_dataforseo("serp/google/organic/live", [{"keyword": keyword, "language_name": "Spanish", "location_code": 2724, "depth": 10}])

        if not keyword_results or not serp_results:
            keyword_analyses.append({"keyword": keyword, "error": "No se pudieron obtener datos para esta keyword."})
            continue

        keyword_data = keyword_results[0]
        position, top_5_competitors = 0, []
        features_en_serp = [item['feature'] for item in serp_results[0].get('serp_extra', []) if item.get('feature')] if serp_results else []

        for item in serp_results[0].get('items', []):
            if item['type'] == 'organic':
                current_url = item.get('url', '')
                if target_url in current_url and position == 0: position = item.get('rank_group', 0)
                if len(top_5_competitors) < 5 and target_url not in current_url:
                    top_5_competitors.append(current_url)
                    all_competitors.add(current_url)
        
        # ... (cálculos de tráfico y valor) ...
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
            "metricas_keyword": { "volumen_busqueda": search_volume, "dificultad_keyword": keyword_data.get('keyword_difficulty', 0), "cpc_usd": cpc },
            "analisis_competencia": { "top_5_competidores": top_5_competitors }
        })

    sugerencias_ia = enriquecer_con_ia(
        on_page_results.get('contenido_texto', ''),
        keywords[0],
        list(all_competitors)
    )
    del on_page_results['contenido_texto']

    final_report = {
        "url_analizada": target_url,
        "analisis_on_page": on_page_results,
        "analisis_keywords": keyword_analyses,
        "sugerencias_ia": sugerencias_ia
    }
    
    return jsonify(final_report)
