import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from src.functions import (
    FINAL_OUTPUT_COLUMNS,
    asegurar_identificador_en_pdfs,
    construir_salida_final,
    normalizar_columnas_multilingues,
    parsear_fechas_inteligente,
)
from src.gemini_processor import LicitacionGeminiProcessor
from src.analytics import guardar_metricas
from src.document_keywords import matches_document_text
from app import aplicar_filtros, formatear_importe_compacto, normalizar_estado


class PipelineTests(unittest.TestCase):
    def test_esquema_final_tiene_exactamente_20_columnas(self):
        source = pd.DataFrame(
            [{
                "estado_licitacion": "Publicada",
                "fecha_limite_presentacion": "2026-09-01",
                "titulo": "Plataforma de datos",
                "numero_expediente": "EXP-1",
                "codigo_cpv": "45216111",
                "importe_con_iva": 1210,
                "enlace": "https://example.test/exp-1",
                "provincia_ejecucion": "Madrid",
                "comunidad_autonoma_ejecucion": "Comunidad de Madrid",
            }]
        )
        result = construir_salida_final(source)
        self.assertEqual(result.columns.tolist(), FINAL_OUTPUT_COLUMNS)
        self.assertEqual(result.shape[1], 20)
        self.assertEqual(result.columns[:2].tolist(), ["numero_expediente", "codigo_cpv"])
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

    def test_estado_prioriza_fase_administrativa_y_despues_fecha(self):
        self.assertEqual(normalizar_estado("Publicada", "2026-09-01", date(2026, 8, 19)), "En plazo")
        self.assertEqual(normalizar_estado("Publicada", "2026-08-01", date(2026, 8, 19)), "Fuera de plazo")
        self.assertEqual(normalizar_estado("Adjudicada", "2026-09-01", date(2026, 8, 19)), "Adjudicada")
        self.assertEqual(normalizar_estado("Abierta", None, date(2026, 8, 19)), "Abierta (plazo sin confirmar)")

    def test_filtro_por_estado_permite_seleccion_multiple(self):
        source = pd.DataFrame([
            {"Nº Expediente": "A", "Estado": "En plazo"},
            {"Nº Expediente": "B", "Estado": "Adjudicada"},
            {"Nº Expediente": "C", "Estado": "Fuera de plazo"},
        ])
        result = aplicar_filtros(source, estados=["En plazo", "Adjudicada"])
        self.assertEqual(result["Nº Expediente"].tolist(), ["A", "B"])

    def test_aliases_multilingues_recuperan_cpv_iva_y_expediente(self):
        source = pd.DataFrame([{
            "File": "CON/45/25", "CPV code": "45216111",
            "Total amount tax included": "2157129.84",
            "Contracting Party": "Ayuntamiento",
        }])
        result = normalizar_columnas_multilingues(source)
        self.assertEqual(result.loc[0, "numero_expediente"], "CON/45/25")
        self.assertEqual(result.loc[0, "codigo_cpv"], "45216111")
        self.assertEqual(result.loc[0, "importe_con_iva"], "2157129.84")

    def test_salida_cpv_elimina_descripcion(self):
        source = pd.DataFrame([{
            "numero_expediente": "EXP", "codigo_cpv": "45216111-Trabajos de construcción",
        }])
        result = construir_salida_final(source)
        self.assertEqual(result.loc[0, "codigo_cpv"], "45216111")

    def test_fechas_inglesas_y_catalanas(self):
        values = pd.Series(["08-Sep-2026", "September 8, 2026", "8 de setembre del 2026 23:59"])
        result = parsear_fechas_inteligente(values)
        self.assertTrue(all(value == date(2026, 9, 8) for value in result))

    def test_formato_compacto_de_importes(self):
        self.assertEqual(formatear_importe_compacto(1782751.93), "1,78 M €")
        self.assertEqual(formatear_importe_compacto(45000), "45,0 K €")
        self.assertEqual(formatear_importe_compacto(-1), "-")

    def test_documentos_se_detectan_en_cuatro_idiomas(self):
        for label in (
            "Pliego de prescripciones técnicas", "Tender specifications",
            "Veure documents - plec tècnic", "Baldintza teknikoak",
        ):
            self.assertTrue(matches_document_text(label), label)

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
