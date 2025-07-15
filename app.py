import os
import requests
import json
import traceback
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURACIÓN DE LAS CLAVES ---
DATAFORSEO_LOGIN = os.environ.get("DATAFORSEO_LOGIN")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD")

@app.route('/test', methods=['GET'])
def test_endpoint():
    print("Iniciando prueba de endpoint SERP...")
    try:
        # Datos para la prueba con una sola keyword
        post_data = [{
            "keyword": "software contable",
            "language_name": "Spanish",
            "location_code": 2724, # España
            "depth": 10
        }]

        print(f"Enviando datos a DataForSEO: {post_data}")

        # La llamada directa a la API que está fallando
        response = requests.post(
            "https://api.dataforseo.com/v3/serp/google/organic/task_post",
            auth=(DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD),
            json=post_data,
            timeout=40
        )
        
        # Forzamos que nos muestre un error si la respuesta no es exitosa
        response.raise_for_status()
        
        print("La llamada a DataForSEO fue exitosa.")
        # Devolvemos la respuesta exacta de DataForSEO
        return jsonify(response.json())

    except Exception as e:
        # Si algo falla, devolvemos el error exacto
        error_details = traceback.format_exc()
        print("La prueba falló. Error:")
        print(error_details)
        return jsonify({
            "error": "La prueba falló.",
            "detalle_del_error": error_details
        }), 500
