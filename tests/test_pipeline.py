import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from src.functions import (
    FINAL_OUTPUT_COLUMNS,
    asegurar_identificador_en_pdfs,
    construir_salida_final,
)
from src.gemini_processor import LicitacionGeminiProcessor
from src.analytics import guardar_metricas
from app import aplicar_filtros


class PipelineTests(unittest.TestCase):
    def test_esquema_final_tiene_exactamente_18_columnas(self):
        source = pd.DataFrame(
            [{
                "estado_licitacion": "Publicada",
                "fecha_limite_presentacion": "2026-09-01",
                "titulo": "Plataforma de datos",
                "numero_expediente": "EXP-1",
                "enlace": "https://example.test/exp-1",
                "provincia_ejecucion": "Madrid",
                "comunidad_autonoma_ejecucion": "Comunidad de Madrid",
            }]
        )
        result = construir_salida_final(source)
        self.assertEqual(result.columns.tolist(), FINAL_OUTPUT_COLUMNS)
        self.assertEqual(result.shape[1], 18)
        self.assertEqual(result.loc[0, "enlace"], "https://example.test/exp-1")
        self.assertEqual(
            result.loc[0, "lugar_ejecucion"], "Madrid / Comunidad de Madrid"
        )

    def test_fallback_no_confunde_it_con_licitacion(self):
        source = pd.DataFrame(
            [{"titulo": "Asistencia técnica para rehabilitación de un edificio"}]
        )
        processor = LicitacionGeminiProcessor(source)
        processor.client = None
        result = processor.procesar_completo()
        self.assertEqual(result.loc[0, "clasificacion"], "No tecnológica")

    def test_cache_gemini_reutiliza_solo_respuestas_validas(self):
        source = pd.DataFrame([{"titulo": "Plataforma de datos"}])
        with tempfile.TemporaryDirectory() as directory:
            processor = LicitacionGeminiProcessor(source)
            processor.cache_dir = os.path.join(directory, "cache")
            # Path también acepta una ruta reasignada durante pruebas/configuración.
            from pathlib import Path
            processor.cache_dir = Path(processor.cache_dir)
            prompt = "contenido estable"
            response = {"es_tecnologica": True, "categoria": "Software y desarrollo"}
            processor._cache_set(prompt, "web", response)
            self.assertEqual(processor._cache_get(prompt, "web"), response)
            self.assertIsNone(processor._cache_get(prompt + " modificado", "web"))
            processor.client = None
            self.assertEqual(processor._request(prompt, "web"), response)
            self.assertEqual(processor.stats["gemini_cache_reutilizadas"], 1)

    def test_pdf_se_renombra_con_identificador(self):
        with tempfile.TemporaryDirectory() as directory:
            old_name = "pliego.pdf"
            with open(os.path.join(directory, old_name), "wb") as pdf:
                pdf.write(b"%PDF-1.4\n")
            source = pd.DataFrame(
                [{
                    "fuente": "España",
                    "numero_expediente": "EXP/2026-1",
                    "enlace": "https://example.test/1",
                    "pdf": old_name,
                }]
            )
            result = asegurar_identificador_en_pdfs(source, directory)
            new_name = result.loc[0, "pdf"]
            self.assertIn("espana-exp-2026-1", new_name)
            self.assertTrue(os.path.isfile(os.path.join(directory, new_name)))
            self.assertFalse(os.path.exists(os.path.join(directory, old_name)))
            repeated = asegurar_identificador_en_pdfs(source, directory)
            self.assertEqual(repeated.loc[0, "pdf"], new_name)

    def test_filtros_fecha_y_clasificacion_dual(self):
        source = pd.DataFrame([
            {"Nº Expediente": "A-1", "Fecha Límite Presentación": "2026-09-01",
             "Clasificación": "Software y desarrollo", "Importe (€)": 100,
             "Valor estimado contrato (€)": 150},
            {"Nº Expediente": "B-2", "Fecha Límite Presentación": "2026-08-01",
             "Clasificación": "No tecnológica", "Importe (€)": 200,
             "Valor estimado contrato (€)": 250},
        ])
        result = aplicar_filtros(
            source, fecha_desde=date(2026, 9, 1), grupo_clasificacion="Tecnológicas"
        )
        self.assertEqual(result["Nº Expediente"].tolist(), ["A-1"])

    def test_metricas_se_guardan_en_json_e_historico(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = {
                "ejecucion_id": "test", "total_licitaciones": 2,
                "pdf_descargados": 1, "gemini_requeridas": 2,
                "gemini_analizadas": 1,
            }
            json_path, history_path = guardar_metricas(metrics, directory)
            self.assertTrue(json_path.exists())
            history = pd.read_csv(history_path)
            self.assertEqual(history.loc[0, "total_licitaciones"], 2)


if __name__ == "__main__":
    unittest.main()
